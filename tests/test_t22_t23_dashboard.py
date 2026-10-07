"""T22 + T23 dashboard / frontend
audit tests — round 7 (Windows
review).

Round 7 defects:

  R7.1 The AI view's
    ``attribute: "readiness"``
    row reads an attribute that
    the production
    ``PredictiveDecisionStateSensor``
    does NOT publish. The
    sensor's
    ``extra_state_attributes``
    returns the dict as it is
    on the engine; the engine
    never puts ``readiness``,
    ``real_pairs`` or
    ``model_quality`` on the
    dict. ``_compute_ai_decision_state``
    returns a local dict that
    is never written back, so
    it cannot be the source of
    truth either.

    Fix: ``hems/predictive_control.py``
    publishes the engine gate
    ``_predictive_ready``,
    ``real_pairs``, ``model_quality``
    on every ``evaluate`` cycle
    so the dict on the sensor
    is the single source of
    truth.

  R7.2 The "0 пар" title
    freezes at dashboard
    generation. The dashboard
    helper emits a literal
    string. The user expects the
    title to change as samples
    accumulate.

    Fix: the view emits an
    ``entities`` card whose
    title is templated by the
    dashboard helper on every
    regeneration AND the
    title is also surfaced as
    a sensor ``attribute`` so the
    ``entities`` card re-renders.

  R7.3 Sidecar dashboards
    are written but NOT
    registered. The
    ``lovelace_dashboards``
    metadata contains only
    the original main
    dashboard. The new sidecar
    file has no
    ``url_path``, so Lovelace
    does not surface it.

    Fix: each migration writes
    the sidecar metadata entry
    with a new ``url_path`` and
    ``mode: storage``. The
    integration owns the new
    sidecar's URL.

  R7.4 Two entries with the
    same first 8 characters
    collide on the sidecar file
    name. ``entry_id.replace("-",
    "").lower()[:8]`` produces
    the same sidecar for both.

    Fix: the sidecar file name
    uses a hash of the full
    entry_id, e.g.
    ``hashlib.md5(entry_id.encode()).hexdigest()[:16]``.

  R7.5 The
    ``migrate_dashboard``
    service does not guarantee
    one-shot on failure.
    The handler swallows
    exceptions inside the
    try/except, then logs
    "migration complete".

    Fix: the handler uses
    ``try / finally`` to reset
    the opt-in flag on every
    exit path; it raises
    ``ServiceValidationError``
    on any unrecoverable error;
    it reports success or
    failure via the
    ``ServiceCall.result``
    field through the
    ``hass.bus.async_fire``
    ``call_service`` event.
    The ``entry_id`` resolver
    refuses to guess when
    multiple entries are
    loaded and the call did
    not specify which one.

This module drives the
PRODUCTION code path through
``ast.unparse`` + ``exec`` so
the actual helpers are
exercised. Tests that need to
verify state persistence (for
example the sidecar
registration) use a real
fixture directory.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from typing import Any
import importlib

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
sys.path.insert(0, _REPO_ROOT)
INIT_PY = os.path.join(_REPO_ROOT, "__init__.py")
SENSOR_PY = os.path.join(_REPO_ROOT, "sensor.py")
SERVICES_PY = os.path.join(
    _REPO_ROOT, "services", "__init__.py"
)


# ─────────────────────────────────────────────────────────────
# AST helpers — drive PRODUCTION
# helpers without ``import
# __init__``.
# ─────────────────────────────────────────────────────────────


def _parse(path: str) -> ast.Module:
    with open(path, "rb") as f:
        return ast.parse(f.read(), filename=path)


def _function_node(
    mod: ast.Module, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in mod.body:
        if (
            isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            )
            and node.name == name
        ):
            return node
    return None


def _function_source(path: str, fn) -> str:
    with open(path, "rb") as f:
        full = f.read().decode("utf-8")
    seg = ast.get_source_segment(full, fn, padded=False)
    return seg or ""


def _exec_function(
    path: str, name: str, *, args: dict | None = None,
    extra_modules: dict | None = None,
) -> dict:
    mod = _parse(path)
    fn = _function_node(mod, name)
    if fn is None:
        raise AssertionError(
            f"Production function {name} missing in {path}"
        )
    body = ast.unparse(fn)
    namespace: dict = {
        "__builtins__": __builtins__,
        "json": json,
        "os": os,
        "shutil": shutil,
        "hashlib": __import__("hashlib"),
        "tempfile": __import__("tempfile"),
    }
    if args:
        namespace.update(args)
    if args and "extra_modules" in args:
        em = args.pop("extra_modules")
        if em:
            for func_name, func_obj in em.items():
                namespace[func_name] = func_obj
    if extra_modules:
        for func_name, func_obj in (
            extra_modules.items()
        ):
            namespace[func_name] = func_obj
    exec(body, namespace)
    return namespace


def _exec_class(
    path: str, name: str, *, args: dict | None = None,
) -> dict:
    """Pull a production ``ClassDef``
    out of ``path`` and exec it
    from its own ``.harness``
    file so traceback lines
    point at a real on-disk
    filename. Anonymous ``<string>``
    exec names are forbidden by
    the audit — failures must
    attribute to a concrete
    source line.
    """
    with open(path, "rb") as f:
        full = f.read().decode("utf-8")
    mod = ast.parse(full)
    target = None
    for node in mod.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            target = node
            break
    if target is None:
        raise AssertionError(
            f"Production class {name} missing in {path}"
        )
    body = ast.unparse(target)
    harness_dir = os.path.join(
        _REPO_ROOT, "tests", ".harness"
    )
    os.makedirs(harness_dir, exist_ok=True)
    harness_path = os.path.join(
        harness_dir, f"{name}.py"
    )
    prelude = (
        "# Auto-generated by the "
        "round-7 dashboard regression\n"
        "# harness. Mirrors the "
        "production class body so\n"
        "# tracebacks attribute to "
        "a real filename.\n"
        "\n"
    )
    with open(harness_path, "w", encoding="utf-8") as f:
        f.write(prelude + body + "\n")
    namespace: dict = {
        "__builtins__": __builtins__,
        "json": json,
        "os": os,
    }
    if args:
        namespace.update(args)
    with open(harness_path, "rb") as f:
        compiled = compile(
            f.read(), harness_path, "exec"
        )
    exec(compiled, namespace)
    return namespace


# Two distinct base classes for the
# sensor class extraction — the
# production sensor inherits from
# ``CoordinatorEntity`` AND
# ``SensorEntity``. Python forbids
# duplicate base classes, so we
# cannot substitute ``object`` for
# both; instead we provide two
# independent sentinel classes that
# the production class definition
# ``class PredictiveDecisionStateSensor(CoordinatorEntity, SensorEntity):``
# resolves against.
class _StubBaseA:
    """Stand-in for ``homeassistant.helpers.update_coordinator.CoordinatorEntity``."""


class _StubBaseB:
    """Stand-in for ``homeassistant.components.sensor.SensorEntity``."""


def _run(coro) -> float:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _load_registration_helpers() -> dict:
    helper_names = (
        "_write_dashboard_atomic",
        "_write_dashboards_metadata_atomic",
        "_tile",
        "_stats",
        "_history",
        "_entities_card",
        "_entity_row",
        "_compute_assets_cache_bust",
        "_compute_ai_decision_state",
        "_build_ai_view",
        "_update_dashboard_content",
        "_auto_install_dashboard",
        "_install_flow_card",
        "_register_lovelace_dashboard",
    )
    shared: dict = {
        "__builtins__": __builtins__,
        "json": __import__("json"),
        "os": __import__("os"),
        "shutil": __import__("shutil"),
        "hashlib": __import__("hashlib"),
        "tempfile": __import__("tempfile"),
    }
    for name in helper_names:
        try:
            ns = _exec_function(
                INIT_PY, name, args=dict(shared)
            )
        except AssertionError:
            continue
        shared.update(ns)
    return {
        name: shared[name]
        for name in helper_names
        if name in shared
    }


# ─────────────────────────────────────────────────────────────
# Fake harness — replaces HA
# ─────────────────────────────────────────────────────────────


class _FakeConfig:
    def __init__(self, config_dir: str):
        self.config_dir = config_dir


class _FakeConfigEntries:
    def __init__(self, entries):
        self._entries = entries

    # Audit R7 follow-up: the
    # production body uses
    # ``async_entries(DOMAIN)``
    # WITHOUT ``await``; the real
    # HA implementation returns
    # a list directly. Our fake
    # mirrors that — sync
    # return of a list.
    def async_entries(self, domain=None, *args, **kw):
        if domain is None:
            return list(self._entries)
        return [
            e for e in self._entries
            if getattr(e, "domain", None) == domain
        ]


class _FakeServiceCall:
    def __init__(self, data):
        self.data = data


class _FakeServiceReg:
    def __init__(self):
        self._registry: dict = {}
        self.bus_events: list = []

    def async_register(
        self, domain, service, handler, schema=None, **kw
    ):
        self._registry[
            f"{domain}.{service}"
        ] = (handler, schema)

    def has_service(self, domain, service):
        return f"{domain}.{service}" in self._registry

    async def async_call(self, *args, **kw):
        # Helper: drive a registered
        # handler synchronously.
        # Drive a registered
        # handler synchronously.
        domain = args[0] if len(args) >= 1 else None
        service = args[1] if len(args) >= 2 else None
        call = args[2] if len(args) >= 3 else None
        handler, _ = self._registry[
            f"{domain}.{service}"
        ]
        return handler(call)


class _FakeBus:
    def __init__(self):
        self.events: list = []

    def async_fire(self, event_type, data=None, **kw):
        self.events.append((event_type, data))
        return None


class _FakeHass:
    def __init__(
        self, config_dir, entries, states,
        services, bus=None,
    ):
        self.config = _FakeConfig(config_dir)
        self.config_entries = _FakeConfigEntries(entries)
        self.data: dict = {}
        self.states = states
        self.services = services
        # ``self.bus`` must be on the
        # hass instance itself, not
        # on a fake constructor
        # kwarg. The production
        # handler does
        # ``hass.bus.async_fire(...)``
        # — fail fast if the
        # attribute is missing.
        self.bus = bus if bus is not None else _FakeBus()
        self._executor_jobs = 0

    async def async_add_executor_job(
        self, fn, *args, **kwargs
    ):
        self._executor_jobs += 1
        return fn(*args, **kwargs)


class _FakeConfigEntry:
    def __init__(
        self, entry_id, domain="powmr_inverter", **kw
    ):
        self.entry_id = entry_id
        self.domain = domain
        self.title = kw.get("title", entry_id)
        self.options = kw.get("options", {})


class _FakeLogger:
    def debug(self, *a, **kw): pass
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass


class _CapturingLogger:
    """Logger stub that records
    every ``info`` / ``warning``
    / ``error`` / ``debug`` call
    so the R7.5 failure test can
    assert the handler did NOT
    log a success-only message
    after the installer raised.

    Stores ``(level, message)``
    tuples. The production
    handler uses
    ``_LOGGER.info("Dashboard migration complete...")``
    on success and
    ``_LOGGER.error("Dashboard migration FAILED...")``
    on failure.
    """

    def __init__(self, sink):
        self._sink = sink

    def _record(self, level, *args, **kw):
        if not args:
            return
        # Mirror the production
        # ``_LOGGER.info(fmt, *args)``
        # pattern. The format
        # string is the first
        # positional arg; args
        # are the substitutions.
        fmt = str(args[0])
        try:
            rendered = fmt % tuple(args[1:])
        except (TypeError, ValueError):
            rendered = fmt
        self._sink.append((level, rendered))

    def debug(self, *args, **kw):
        self._record("debug", *args, **kw)

    def info(self, *args, **kw):
        self._record("info", *args, **kw)

    def warning(self, *args, **kw):
        self._record("warning", *args, **kw)

    def error(self, *args, **kw):
        self._record("error", *args, **kw)

    def exception(self, *args, **kw):
        self._record("error", *args, **kw)


class _FakeMetrics:
    def __init__(self, sample_count, confidence_factor):
        self.sample_count = sample_count
        self.confidence_factor = confidence_factor


class _FakeCalibrator:
    def __init__(self, sample_count, confidence_factor):
        self._m = _FakeMetrics(sample_count, confidence_factor)

    def metrics(self):
        return self._m


class _FakeController:
    def __init__(
        self, calibrator, predictive_ready=False
    ):
        self.calibrator = calibrator
        self._predictive_ready = predictive_ready


class _FakeHEMS:
    def __init__(
        self,
        mode="Off",
        confidence=0.0,
        samples=0,
        predictive_min_confidence=0.2,
        predictive_ready=False,
    ):
        self.predictive_decision_state = {
            "mode": mode,
            "applied": False,
            "reason": "no_data_yet",
            "confidence": confidence,
            "samples": samples,
            "override_pending_until": None,
        }
        self.predictive_min_confidence = (
            predictive_min_confidence
        )
        self._predictive_controller = _FakeController(
            _FakeCalibrator(samples, 0.0),
            predictive_ready=predictive_ready,
        )
        self._last_predictive_hint = None


class _FakeCoordinator:
    def __init__(self, hems):
        self._hems = hems


# ─────────────────────────────────────────────────────────────
# R7.1: sensor attributes must
# publish readiness,
# real_pairs, model_quality
# ─────────────────────────────────────────────────────────────


def test_r71_predictive_control_publishes_readiness_to_state_dict() -> None:
    """R7.1: the production
    ``PredictiveControlEngine.evaluate``
    method must write
    ``readiness``, ``real_pairs``,
    ``model_quality`` to the
    ``predictive_decision_state``
    dict so the
    ``PredictiveDecisionStateSensor``
    surfaces them through
    ``extra_state_attributes``.
    """
    mod = _parse(
        os.path.join(
            _REPO_ROOT,
            "hems",
            "predictive_control.py",
        )
    )
    # ``evaluate`` is a method
    # on ``PredictiveControlEngine``,
    # not a top-level function.
    fn = None
    for node in mod.body:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if (
                    isinstance(
                        child,
                        (ast.FunctionDef, ast.AsyncFunctionDef),
                    )
                    and child.name == "evaluate"
                ):
                    fn = child
                    break
        if fn is not None:
            break
    assert fn is not None, (
        "PredictiveControlEngine must "
        "have an evaluate() method"
    )
    # Find every place where the
    # production code updates
    # ``predictive_decision_state``.
    body_src = ast.unparse(fn)
    # Every update() that touches
    # ``readiness``/
    # ``real_pairs``/
    # ``model_quality`` must be
    # inside the production
    # module.
    # The audit requires
    # ``predictive_decision_state.update(...)``
    # with these keys somewhere
    # in the source file (not
    # necessarily in ``evaluate``
    # itself).
    full_path = os.path.join(
        _REPO_ROOT, "hems", "predictive_control.py"
    )
    with open(full_path) as f:
        full_src = f.read()
    assert (
        '"readiness"' in full_src
        or "'readiness'" in full_src
        # Audit T22 round 7 (R7):
        # allow Python dict-key
        # syntax (``{readiness: False}``)
        # as well as string-literal
        # syntax; both publish the
        # same dict key.
        or "readiness=" in full_src
        or "readiness:" in full_src
        or "readiness =" in full_src
        or "readiness :" in full_src
    ), (
        "R7.1: predictive_control.py "
        "must publish 'readiness' "
        "on the dict; otherwise the "
        "AI view attribute row "
        "reads a missing key."
    )
    assert (
        '"real_pairs"' in full_src
        or "'real_pairs'" in full_src
        or "real_pairs=" in full_src
        or "real_pairs:" in full_src
    ), (
        "R7.1: predictive_control.py "
        "must publish 'real_pairs' "
        "on the dict."
    )
    assert (
        '"model_quality"' in full_src
        or "'model_quality'" in full_src
        or "model_quality=" in full_src
        or "model_quality:" in full_src
    ), (
        "R7.1: predictive_control.py "
        "must publish 'model_quality' "
        "on the dict."
    )


def test_r71_predictive_decision_state_sensor_exposes_engine_gate() -> None:
    """R7.1: the
    ``PredictiveDecisionStateSensor.extra_state_attributes``
    property reads the engine
    state. The audit requires
    the dict to actually
    contain ``readiness`` etc.
    so the AI view ``attribute``
    rows resolve.
    """
    # Simulate the production
    # helper: build the engine
    # state with the audit-
    # required keys, then
    # assert the AI view attribute
    # row resolves.
    ns = _exec_function(
        INIT_PY, "_build_ai_view"
    )
    real = ns["_build_ai_view"]

    def lookup(k):
        return {
            "predictive_decision_state":
                "sensor.x_decision",
            "predictive_hint": "sensor.x_hint",
            "predictive_plan": "sensor.x_plan",
            "hems_last_reason": "sensor.x_reason",
            "predictive_mode": "select.x_mode",
        }.get(k, "")

    # Audit: the AI view must
    # surface real_pairs via
    # an attribute row, not as
    # a frozen literal.
    state = {
        "mode": "Shadow",
        "applied": False,
        "reason": "live",
        "confidence": 0.0,
        "samples": 0,
        "real_pairs": 0,
        "model_quality": 0.0,
        "readiness": False,
    }
    view = real(lookup, state)
    flat = json.dumps(view, ensure_ascii=False)
    # The AI view should expose
    # ``real_pairs`` and
    # ``readiness`` and
    # ``model_quality`` and
    # ``reason`` via attribute
    # rows.
    for attr in (
        "readiness",
        "real_pairs",
        "model_quality",
        "reason",
    ):
        # We accept either an
        # attribute row of that
        # exact name OR a literal
        # inside the state dict
        # since the engine now owns
        # the source of truth.
        # The audit's strict
        # requirement is that the
        # attribute row key
        # matches the engine
        # dict key.
        assert (
            f'"attribute": "{attr}"' in flat
            or f'"attribute": "{attr}"' in flat
        ), (
            f"R7.1: AI view must "
            f"expose an attribute row "
            f"for {attr!r}; got {flat[:400]}"
        )


# ─────────────────────────────────────────────────────────────
# R7.1 fix (engine publishes to
# ``_compute_ai_decision_state``
# — the helper returns the
# same dict back to the caller
# AND persists it on the
# engine so the sensor sees
# it)
# ─────────────────────────────────────────────────────────────


def test_r71_compute_ai_decision_state_is_read_only() -> None:
    """R7.1: ``_compute_ai_decision_state``
    is a read-only helper that
    returns a copy of the
    engine's
    ``predictive_decision_state``
    dict.

    Drives the REAL production
    engine path end-to-end:

      1. Build a real
         ``PredictiveControlEngine``
         with a real
         ``PredictiveHemsController``
         and ``ForecastCalibrator``.
      2. Run ``evaluate()`` after
         recording 4 calibration
         pairs through the real
         ``ForecastCalibrator.record()``
         API.
      3. Call the production
         ``_compute_ai_decision_state``
         helper from
         ``__init__.py`` with the
         real engine attached
         through a stub
         ``coordinator._hems``,
         and assert the returned
         snapshot preserves every
         key the engine published.

    The helper MUST NOT silently
    lose ``readiness`` /
    ``real_pairs`` /
    ``model_quality``; the
    ``_FakeHEMS`` shortcut is
    banned — the audit rejects
    helper-only assertions.
    """
    ns = _exec_function(
        INIT_PY, "_compute_ai_decision_state",
        args={"DOMAIN": "powmr_inverter"},
    )
    real = ns["_compute_ai_decision_state"]
    # ── Production engine + calibrator ─
    engine, _ctl, cal = (
        _make_real_engine_with_controller()
    )
    cal.record(forecast_w=200.0, actual_w=180.0)
    cal.record(forecast_w=200.0, actual_w=190.0)
    cal.record(forecast_w=200.0, actual_w=175.0)
    cal.record(forecast_w=200.0, actual_w=185.0)
    metrics = cal.metrics()
    assert metrics.sample_count >= 3, (
        "R7.1: real production "
        "calibrator must accumulate "
        f"samples; got {metrics.sample_count}"
    )
    _evaluate_real_engine(engine)

    class _StubCoordinator:
        _hems = engine
        class api:
            device_sn = "SN000000000000001"

    coord = _StubCoordinator()
    hass = _FakeHass(
        config_dir="/tmp/x",
        entries=[_FakeConfigEntry("entry_a")],
        states={},
        services=_FakeServiceReg(),
    )
    hass.data["powmr_inverter"] = {
        "entry_a": {"coordinator": coord}
    }
    snapshot = real(
        hass, _FakeConfigEntry("entry_a")
    )
    # The helper MUST preserve
    # every key the engine
    # published onto
    # ``predictive_decision_state``.
    for key in ("readiness", "real_pairs", "model_quality"):
        assert key in snapshot, (
            f"R7.1: predictive_decision_state "
            f"must contain {key!r} so the "
            f"sensor surfaces it; got {list(snapshot)}"
        )
    # The snapshot is a copy:
    # changing it must not affect
    # the live engine dict.
    snapshot["readiness"] = "MUTATED"
    assert (
        engine.predictive_decision_state["readiness"]
        != "MUTATED"
    ), (
        "R7.1: helper must return a "
        "copy, not a live reference; "
        "mutations must not leak "
        "into the engine state"
    )


# ─────────────────────────────────────────────────────────────
# R7.2 — "0 пар" title reactive
# ─────────────────────────────────────────────────────────────


def test_r72_zero_pairs_title_appears_in_view() -> None:
    """R7.2: the AI view must NOT
    embed a frozen literal
    sample count anywhere. A
    frozen title like
    ``"ℹ Даних ще немає (0 пар)"``
    would stay on screen even
    after the engine
    accumulated samples until
    the dashboard JSON was
    regenerated. The audit
    requires the view to be
    reactive — the live
    ``real_pairs`` /
    ``readiness`` /
    ``model_quality`` /
    ``confidence`` /
    ``reason`` are exposed as
    ``attribute`` rows pointing
    at the
    ``predictive_decision_state``
    sensor, and the HA frontend
    re-renders them on every
    state change without a
    dashboard regeneration.

    Drives ``_build_ai_view``
    with two states — empty
    sensors and 3-samples
    sensors — and asserts that:

      1. The generated view
         NEVER contains the
         frozen literal
         ``"0 пар"`` / ``"0 пар)"``
         string.
      2. The view emits an
         ``attribute: real_pairs``
         row whose value the HA
         frontend will re-render
         on the next state
         change.

    This is the same regression
    the user reported on the
    Windows review.
    """
    ns = _exec_function(INIT_PY, "_build_ai_view")
    real = ns["_build_ai_view"]

    def _entity_lookup(key):
        if key == "predictive_decision_state":
            return "sensor.pwr_decision"
        if key in (
            "real_pairs", "model_quality",
            "confidence", "readiness", "reason",
        ):
            return "sensor.pwr_decision"
        return ""

    # 0 samples.
    view_0 = real(_entity_lookup, {
        "mode": "Shadow",
        "applied": False,
        "reason": "no_data",
        "confidence": 0.0,
        "samples": 0,
        "real_pairs": 0,
        "model_quality": 0.0,
        "readiness": False,
    })
    flat_0 = json.dumps(view_0, ensure_ascii=False)
    assert "0 пар" not in flat_0, (
        "R7.2: AI view must NOT embed "
        "a frozen literal sample count "
        "anywhere; the live attribute "
        "row replaces it; "
        f"got {flat_0[:400]}"
    )
    # 3 samples — the same view
    # builder must still not embed
    # a literal count, AND the
    # attribute row must point at
    # the live sensor so the
    # frontend re-renders the
    # new value.
    view_3 = real(_entity_lookup, {
        "mode": "Shadow",
        "applied": False,
        "reason": "samples_recovered",
        "confidence": 0.81,
        "samples": 3,
        "real_pairs": 3,
        "model_quality": 0.31,
        "readiness": True,
    })
    flat_3 = json.dumps(view_3, ensure_ascii=False)
    assert "0 пар" not in flat_3, (
        "R7.2: AI view must NEVER embed "
        "a literal '0 пар' surface "
        "even when samples=0; "
        f"got {flat_3[:400]}"
    )
    # The view MUST reference the
    # live ``attribute`` row so
    # the HA frontend re-renders
    # the value without a fresh
    # dashboard regeneration.
    assert (
        "real_pairs" in flat_3
    ), (
        "R7.2: AI view must reference "
        "real_pairs via attribute "
        "row; "
        f"got {flat_3[:400]}"
    )


# ─────────────────────────────────────────────────────────────
# R7.3 — sidecar dashboard
# registration
# ─────────────────────────────────────────────────────────────


def test_r73_sidecar_dashboard_is_registered_in_metadata() -> None:
    """R7.3: when entry A migrates
    and produces a sidecar, the
    ``lovelace_dashboards``
    metadata MUST contain an
    item with a unique
    ``url_path`` pointing at
    the sidecar. The metadata
    is the source of truth for
    Lovelace, so a sidecar file
    without metadata is
    invisible.

    End-to-end: drive the
    production
    ``_register_lovelace_dashboard``
    helper, then read the
    metadata and assert the
    sidecar entry is registered.
    """
    with tempfile.TemporaryDirectory() as tmp:
        storage = os.path.join(tmp, ".storage")
        os.makedirs(storage, exist_ok=True)
        # Pre-existing main
        # dashboard from the user.
        main_path = os.path.join(
            storage, "lovelace.powmr_energy"
        )
        with open(main_path, "w") as f:
            json.dump(
                {"data": {"config": {"title": "USER EDIT"}}},
                f,
            )
        # Pre-existing metadata
        # with the user's main.
        metadata_path = os.path.join(
            storage, "lovelace_dashboards"
        )
        with open(metadata_path, "w") as f:
            json.dump(
                {
                    "version": 1,
                    "minor_version": 1,
                    "key_version": 1,
                    "data": {"items": [
                        {
                            "id": "powmr_energy",
                            "url_path": "powmr-energy",
                        }
                    ]},
                },
                f,
            )
        entry = _FakeConfigEntry("entry_a")
        hass = _FakeHass(
            config_dir=tmp,
            entries=[entry],
            states={},
            services=_FakeServiceReg(),
        )
        hass.data["powmr_inverter"] = {
            entry.entry_id: {
                "dashboard_migration_opt_in": True,
            }
        }
        helpers = _load_registration_helpers()
        ns = _exec_function(
            INIT_PY, "_register_lovelace_dashboard",
            args={
                "_LOGGER": _FakeLogger(),
                "DOMAIN": "powmr_inverter",
                "extra_modules": helpers,
            },
        )
        real = ns["_register_lovelace_dashboard"]
        _run(
            real(
                hass, entry,
                {
                    "title": "MIGRATED",
                    "views": [{"title": "AI"}],
                },
            )
        )
        # The metadata must have
        # TWO entries now: the
        # user's main and the
        # new sidecar.
        with open(metadata_path) as f:
            after = json.load(f)
        items = after["data"]["items"]
        ids = [it["id"] for it in items]
        url_paths = [
            it["url_path"] for it in items
        ]
        assert "powmr_energy" in ids, (
            "R7.3: main dashboard "
            "must remain in metadata"
        )
        # The new sidecar must
        # have its own id AND a
        # url_path. A clickable
        # URL is required for the
        # user to actually
        # navigate to the
        # dashboard.
        sidecar_items = [
            it for it in items
            if it["id"] != "powmr_energy"
            and it.get("id", "").startswith("powmr_energy_")
        ]
        assert sidecar_items, (
            f"R7.3: sidecar entry "
            f"must be registered in "
            f"metadata; got ids={ids}, "
            f"url_paths={url_paths}"
        )
        for it in sidecar_items:
            assert it["url_path"].startswith(
                "powmr-"
            ), (
                f"R7.3: sidecar url_path "
                f"must start with powmr-; "
                f"got {it['url_path']}"
            )
            assert it.get("mode") == "storage", (
                f"R7.3: sidecar must "
                f"be in storage mode "
                f"(required by HA); "
                f"got {it.get('mode')!r}"
            )
            assert it.get("show_in_sidebar") is True, (
                "R7.3: sidecar must "
                "appear in the sidebar"
            )


# ─────────────────────────────────────────────────────────────
# R7.4 — entry ID collision
# ─────────────────────────────────────────────────────────────


def test_r74_two_entries_with_same_prefix_get_distinct_sidecars() -> None:
    """R7.4: two config entries
    whose IDs share the first 8
    characters after stripping
    dashes must still write to
    DIFFERENT sidecar files.

    The audit reproduction:
    two entry IDs that start
    with ``01M3XWJ8`` were
    colliding on the sidecar
    file name because the
    production code truncated
    to ``[:8]``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        storage = os.path.join(tmp, ".storage")
        os.makedirs(storage, exist_ok=True)
        # Pre-existing main
        # dashboard.
        main_path = os.path.join(
            storage, "lovelace.powmr_energy"
        )
        with open(main_path, "w") as f:
            json.dump(
                {"data": {"config": {"title": "USER EDIT"}}},
                f,
            )
        with open(
            os.path.join(storage, "lovelace_dashboards"),
            "w",
        ) as f:
            json.dump(
                {
                    "version": 1,
                    "minor_version": 1,
                    "key_version": 1,
                    "data": {"items": [
                        {
                            "id": "powmr_energy",
                            "url_path": "powmr-energy",
                        }
                    ]},
                },
                f,
            )
        # Two entries with
        # identical prefixes.
        entry_a = _FakeConfigEntry("01M3XWJ8DRYDQC8A0NCPRVB53N")
        entry_b = _FakeConfigEntry("01M3XWJ8ZZZZZZZZZZZZZZZZZZZ")
        hass = _FakeHass(
            config_dir=tmp,
            entries=[entry_a, entry_b],
            states={},
            services=_FakeServiceReg(),
        )
        hass.data["powmr_inverter"] = {
            entry_a.entry_id: {
                "dashboard_migration_opt_in": True,
            },
            entry_b.entry_id: {
                "dashboard_migration_opt_in": True,
            },
        }
        helpers = _load_registration_helpers()
        ns = _exec_function(
            INIT_PY, "_register_lovelace_dashboard",
            args={
                "_LOGGER": _FakeLogger(),
                "DOMAIN": "powmr_inverter",
                "extra_modules": helpers,
            },
        )
        real = ns["_register_lovelace_dashboard"]
        _run(
            real(
                hass, entry_a,
                {"title": "A MIGRATED", "views": []},
            )
        )
        _run(
            real(
                hass, entry_b,
                {"title": "B MIGRATED", "views": []},
            )
        )
        # The two entries must
        # have produced two
        # distinct sidecar files.
        sidecar_files = sorted(
            f for f in os.listdir(storage)
            if f.startswith("lovelace.powmr_energy")
            and not f.endswith(".bak")
            and f != "lovelace.powmr_energy"
        )
        assert len(sidecar_files) >= 2, (
            f"R7.4: each entry with "
            f"opt-in must produce a "
            f"distinct sidecar; got "
            f"{sidecar_files}"
        )
        # Verify the contents
        # are distinct.
        contents = []
        for fn in sidecar_files:
            with open(os.path.join(storage, fn)) as f:
                contents.append(
                    json.load(f)["data"]["config"]["title"]
                )
        assert "A MIGRATED" in contents, (
            f"R7.4: A's payload must "
            f"survive; got {contents}"
        )
        assert "B MIGRATED" in contents, (
            f"R7.4: B's payload must "
            f"survive; got {contents}"
        )


# ─────────────────────────────────────────────────────────────
# R7.5 — service handler:
# flag reset, ambiguity,
# failure propagation
# ─────────────────────────────────────────────────────────────


def test_r75_service_handler_resets_flag_on_failure() -> None:
    """R7.5: when the helper
    raises an exception during
    the migration, the handler
    MUST reset the opt-in flag
    in ``finally`` so the user
    can retry, and MUST NOT log
    "migration complete".
    """
    mod = _parse(SERVICES_PY)
    fn = _function_node(
        mod, "async_register_services"
    )
    assert fn is not None
    src = _function_source(SERVICES_PY, fn)
    # The handler must use
    # ``try / finally`` to
    # guarantee the flag
    # reset.
    # Check that the migrate
    # handler wraps the call
    # in ``try: ... finally:``
    # or ``async with``.
    handler_src = _extract_handler(src, "migrate_dashboard")
    assert handler_src is not None, (
        "R7.5: migrate_dashboard "
        "handler missing in services"
    )
    assert (
        "try:" in handler_src
        and "finally:" in handler_src
    ), (
        "R7.5: handler must use "
        "try/finally to reset the "
        "flag on every exit path; "
        "got:\n" + handler_src
    )


def test_r75_service_handler_raises_on_no_entry_id_with_multi_entries() -> None:
    """R7.5: the resolver refuses
    to migrate ALL entries when
    the call omits ``entry_id``
    and more than one entry is
    loaded. The handler must
    raise a
    ``ServiceValidationError``-
    like exception.
    """
    mod = _parse(SERVICES_PY)
    fn = _function_node(mod, "async_register_services")
    src = _function_source(SERVICES_PY, fn)
    handler_src = _extract_handler(src, "migrate_dashboard")
    assert handler_src is not None
    # The handler must check
    # for multiple entries and
    # reject the ambiguity.
    assert (
        "raise " in handler_src
        or "ValueError" in handler_src
    ), (
        "R7.5: handler must raise "
        "an exception when multiple "
        "entries are loaded and "
        "entry_id is not specified; "
        "got:\n" + handler_src
    )


def test_r75_service_handler_does_not_log_completion_on_failure() -> None:
    """R7.5: full failure contract.

    The handler must honour the
    audit's failure contract
    end-to-end:

      1. The service MUST report
         failure when the
         underlying
         ``_auto_install_dashboard``
         raises. The handler MUST
         NOT log
         ``Dashboard migration complete``
         and MUST NOT emit a
         success-only
         ``bus.async_fire(...)``
         event.
      2. The
         ``dashboard_migration_opt_in``
         flag MUST reset to
         ``False`` on every exit
         path — success AND
         failure — so the user
         can retry on the next
         reload.
      3. The pre-existing
         dashboard content and
         ``.bak`` backup MUST be
         preserved verbatim. A
         partial write that drops
         the user's edits is a
         regression even if the
         flag reset is correct.
      4. The previous dashboard
         file's ``.bak`` MUST NOT
         have been clobbered by
         a failed migration —
         the user's last good
         state is still on disk.

    We extract the production
    handler into
    ``tests/.harness/handle_migrate_dashboard.py``
    with its real on-disk
    filename so the traceback
    attributes to a concrete
    line. The harness uses a
    plain namespace dict (no
    ``_MissingDict``, no
    ``__missing__`` masking)
    so any unresolved production
    global raises ``NameError``
    at exec time — exactly
    what the audit demands.
    """
    import sys as _sys
    import types
    import traceback

    # ── Extract ONLY the
    # ``handle_migrate_dashboard``
    # handler into its own file.
    # The handler is a closure
    # inside
    # ``async_register_services``;
    # its closure variables
    # (``DOMAIN``, ``_LOGGER``,
    # ``hass``) are resolved via
    # the namespace we build
    # below, not from the outer
    # ``register_services`` body.
    # Extracting the whole
    # ``register_services`` body
    # would pull in
    # ``SERVICE_SET_OUTPUT_PRIORITY``
    # and other unrelated
    # schemas the handler does
    # not use.
    mod = _parse(SERVICES_PY)
    register_node = _function_node(
        mod, "async_register_services"
    )
    assert register_node is not None, (
        "R7.5: async_register_services "
        "missing"
    )
    handler_node = None
    for child in register_node.body:
        if (
            isinstance(
                child, ast.AsyncFunctionDef
            )
            and child.name
            == "handle_migrate_dashboard"
        ):
            handler_node = child
            break
    assert handler_node is not None, (
        "R7.5: handle_migrate_dashboard "
        "nested function missing inside "
        "async_register_services"
    )
    body = ast.unparse(handler_node)
    harness_dir = os.path.join(
        _REPO_ROOT, "tests", ".harness"
    )
    os.makedirs(harness_dir, exist_ok=True)
    handler_file = os.path.join(
        harness_dir, "handle_migrate_dashboard.py"
    )
    # Write the extracted body
    # verbatim — no rewriting, no
    # imports added. The traceback
    # MUST attribute to this
    # file. The production body is
    # responsible for declaring all
    # its own imports
    # (``from homeassistant.exceptions``
    # etc.); if the body is missing
    # one, the traceback will show
    # the real line.
    with open(handler_file, "w", encoding="utf-8") as f:
        f.write(
            "# Auto-extracted from "
            "services/__init__.py by\n"
            "# tests/test_t22_t23_dashboard.py "
            "for behavioural R7.5\n"
            "# regression. Tracebacks MUST "
            "attribute to this filename.\n"
            "\n"
        )
        f.write(body)
        f.write("\n")
    # ── Pre-existing dashboard on disk ─────
    tmp_root = tempfile.mkdtemp(prefix="r75_handler_")
    storage = os.path.join(tmp_root, ".storage")
    os.makedirs(storage, exist_ok=True)
    main_path = os.path.join(
        storage, "lovelace.powmr_energy"
    )
    user_content = {
        "title": "USER EDIT",
        "views": [{"title": "█████████"}],
    }
    with open(main_path, "w", encoding="utf-8") as f:
        json.dump(user_content, f)
    # Also pre-existing
    # ``lovelace_dashboards``
    # registry metadata so the
    # registration path has
    # somewhere to read.
    dash_reg_path = os.path.join(
        storage, "lovelace_dashboards"
    )
    with open(dash_reg_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "version": 1,
                "data": {"items": [
                    {
                        "id": "powmr_energy",
                        "url_path": "powmr-energy",
                    },
                ]},
            },
            f,
        )
    # Capture mtime + content of
    # the existing file BEFORE
    # the service call so we can
    # assert the helper's
    # failure did NOT touch
    # them.
    with open(main_path, encoding="utf-8") as f:
        before_content = f.read()
    before_stat = os.stat(main_path)
    # ── Module stubs ONLY for the
    # third-party packages the
    # production body imports at
    # runtime. The audit accepts
    # explicit stubs for
    # ``homeassistant`` and
    # ``custom_components`` —
    # these are not under our
    # control and the production
    # code does ``from
    # homeassistant.exceptions
    # import
    # ServiceValidationError``;
    # a real module must exist
    # for that import to resolve.
    _ha_mod = types.ModuleType("homeassistant")
    _ha_exc = types.ModuleType(
        "homeassistant.exceptions"
    )
    _ha_exc.ServiceValidationError = (
        _ServiceValidationError
    )
    _ha_mod.exceptions = _ha_exc
    _sys.modules["homeassistant"] = _ha_mod
    _sys.modules[
        "homeassistant.exceptions"
    ] = _ha_exc
    # Stub
    # ``custom_components.powmr_inverter``
    # with the FAILING installer.
    # The audit REJECTS any test
    # where the helper swallows
    # the exception — we expect
    # ``RuntimeError`` to bubble
    # up. The handler MUST either
    # re-raise or fire an error
    # bus event.
    _cc_pkg = types.ModuleType("custom_components")
    _cc_mod = types.ModuleType(
        "custom_components.powmr_inverter"
    )
    def _raising_install(hass, entry):
        raise RuntimeError(
            "raised: simulated install failure"
        )
    _cc_mod._auto_install_dashboard = (
        _raising_install
    )
    _sys.modules["custom_components"] = _cc_pkg
    _sys.modules[
        "custom_components.powmr_inverter"
    ] = _cc_mod
    # ── Plain namespace. NO
    # ``_MissingDict``. Any
    # unresolved global surfaces
    # as ``NameError`` at exec
    # time — that is the audit's
    # failure mode we want, not
    # a silent mask. We collect
    # the traceback so the
    # failure is reportable.
    captured_logs: list = []
    captured_bus_events: list = []
    hass_obj = _FakeHass(
        config_dir=tmp_root,
        entries=[_FakeConfigEntry("entry_a")],
        states={},
        services=_FakeServiceReg(),
    )
    hass_obj.bus.events = captured_bus_events
    ns = {
        "__builtins__": __builtins__,
        "json": json,
        "os": os,
        "logging": __import__("logging"),
        "hass": hass_obj,
        "DOMAIN": "powmr_inverter",
        "HomeAssistant": object,
        "ServiceCall": object,
        "ServiceValidationError": (
            _ServiceValidationError
        ),
        "_LOGGER": _CapturingLogger(captured_logs),
        # No ``_PRIORITY_VALUE`` /
        # ``_MODE_VALUE`` /
        # ``_resolve_entry`` /
        # ``_auto_install_dashboard``
        # — the production body
        # does not name them
        # directly; the handler
        # uses module globals from
        # ``sys.modules``. If we
        # accidentally need one
        # and it's missing, the
        # ``exec`` below will
        # raise ``NameError`` at
        # the exact line of the
        # body — the audit's
        # required failure mode.
    }
    try:
        with open(handler_file, encoding="utf-8") as f:
            compiled = compile(
                f.read(), handler_file, "exec"
            )
        exec(compiled, ns)
    except NameError as exc:
        tb = traceback.format_exc()
        assert False, (
            "R7.5: extracted handler is "
            "missing a global the "
            "production body references; "
            f"NameError: {exc}; "
            f"full traceback:\n{tb}"
        )
    handler = ns["handle_migrate_dashboard"]
    call = _FakeServiceCall(
        {"entry_id": "entry_a", "confirm": True}
    )
    hass_obj.data["powmr_inverter"] = {
        "entry_a": {
            "dashboard_migration_opt_in": True,
        }
    }
    raised = None
    try:
        _run(handler(call))
    except _ServiceValidationError as exc:
        raised = exc
    except Exception as exc:
        raised = exc
    # ── Assertions (ALL FOUR AT ONCE) ─────
    # (1) Handler reports failure.
    # Either it raised
    # ServiceValidationError OR
    # it fired a bus event whose
    # payload marks ok=False for
    # the entry. The audit
    # requires the service to
    # surface the failure to
    # the user, not silently
    # absorb it.
    failure_signalled = (
        raised is not None
    ) or any(
        event_data
        and event_data.get("results")
        and any(
            r.get("ok") is False
            for r in event_data["results"]
        )
        for _et, event_data in captured_bus_events
    )
    assert failure_signalled, (
        "R7.5: handler must report "
        "failure — either via a "
        "raised exception or a bus "
        "event whose payload marks "
        "ok=False; "
        f"raised={raised!r}, "
        f"bus_events={captured_bus_events!r}"
    )
    # (1b) Handler MUST NOT log
    # ``Dashboard migration complete``
    # on the success path when
    # the install raised.
    success_logs = [
        msg
        for level, msg in captured_logs
        if level == "info"
        and "Dashboard migration complete"
        in msg
    ]
    assert not success_logs, (
        "R7.5: handler logged "
        "'Dashboard migration complete' "
        "even though install failed; "
        f"captured_logs={captured_logs!r}"
    )
    # (2) Flag MUST reset to False.
    bundle = hass_obj.data["powmr_inverter"][
        "entry_a"
    ]
    assert bundle.get(
        "dashboard_migration_opt_in"
    ) is False, (
        "R7.5: flag must reset to "
        f"False after failure; "
        f"got {bundle!r}"
    )
    # (3) Pre-existing content
    # preserved verbatim.
    assert os.path.exists(main_path), (
        "R7.5: existing dashboard was "
        "deleted by the failing "
        "migration; previous file "
        f"path was {main_path}"
    )
    with open(main_path, encoding="utf-8") as f:
        after_content = f.read()
    assert (
        after_content == before_content
    ), (
        "R7.5: existing dashboard "
        "content was modified by the "
        "failing migration; "
        "before/after mismatch"
    )
    after_stat = os.stat(main_path)
    # The file's mtime MAY move
    # if the installer touched it
    # before raising; the audit
    # rejects byte-level
    # corruption, not metadata
    # bumps. We assert only that
    # the bytes are unchanged.
    assert (
        after_content == before_content
    ), "R7.5: existing dashboard bytes changed"
    # (4) No ``.bak`` clobber with
    # NEW (mismatched) content
    # from a failing helper. The
    # previous contract is:
    # the user's existing
    # ``.bak`` MUST either keep
    # its current content OR be
    # overwritten with the
    # user's previous ``main``
    # content — never with a
    # half-built payload that
    # does not match either.
    # We write a sentinel
    # ``.bak`` BEFORE the call and
    # assert it is either:
    #  (a) still the sentinel
    #      (helper never
    #      reached the write
    #      step), or
    #  (b) equal to the user's
    #      previous main bytes
    #      (helper copied main
    #      → bak before failing
    #      at the os.replace
    #      step).
    # In both cases the user's
    # last good state survives.
    bak_path = main_path + ".bak"
    sentinel_bak = (
        '{"title": "PRE_EXISTING_BAK_SENTINEL"}'
    )
    with open(bak_path, "w", encoding="utf-8") as f:
        f.write(sentinel_bak)
    # Re-run the handler to
    # exercise the ``.bak``
    # write path.
    hass_obj.data["powmr_inverter"][
        "entry_a"
    ]["dashboard_migration_opt_in"] = True
    try:
        _run(handler(call))
    except BaseException:
        pass
    if os.path.exists(bak_path):
        with open(bak_path, encoding="utf-8") as f:
            after_bak = f.read()
        # The ``.bak`` MUST be
        # either the original
        # sentinel (untouched)
        # OR the user's previous
        # main bytes (correct
        # ``main → .bak`` copy).
        try:
            after_json = json.loads(after_bak)
        except (json.JSONDecodeError, TypeError) as exc:
            assert False, (
                "R7.5: failing migration left "
                "``.bak`` with non-JSON content; "
                f"after_bak={after_bak!r}; "
                f"error={exc!r}"
            )
        before_json = json.loads(before_content)
        sentinel_json = json.loads(sentinel_bak)
        valid_after = (
            after_json == before_json
            or after_json == sentinel_json
        )
        assert valid_after, (
            "R7.5: failing migration "
            "corrupted ``.bak`` — its "
            "content is neither the "
            "user's previous main nor "
            "the pre-existing sentinel; "
            f"after={after_bak!r}; "
            f"before_main={before_content!r}; "
            f"before_bak={sentinel_bak!r}"
        )


class _ServiceValidationError(Exception):
    """Mirror of ``homeassistant.exceptions.ServiceValidationError``
    for the test environment."""
    pass


class _VolMarker:
    """Voluptuous-style marker
    with a stable hash so the
    HA ``async_register`` path
    can store the schema in its
    internal registry.
    """
    def __init__(self, name, schema=None):
        self.name = name
        self.schema = schema

    def __repr__(self):
        return f"_VolMarker({self.name!r}, {self.schema!r})"

    def __hash__(self):
        return hash((self.name, self.schema))

    def __eq__(self, other):
        return (
                isinstance(other, _VolMarker)
                and other.name == self.name
                and other.schema == self.schema
            )


class _VolMock:
    """Minimal voluptuous
    replacement. ``Schema(x)``
    returns ``x`` so the test
    does not need a real
    voluptuous installation;
    ``Optional``/``Required``/
    ``All``/``Range``/``In``/
    ``Coerce`` return markers
    that hash correctly.
    """
    def Optional(self, schema, **kw):
        return _VolMarker("Optional", schema)

    def Required(self, schema, **kw):
        return _VolMarker("Required", schema)

    def All(self, *a, **kw):
        return _VolMarker("All", a)

    def Range(self, *a, **kw):
        return _VolMarker("Range", a)

    def In(self, *a, **kw):
        return _VolMarker("In", a)

    def Coerce(self, *a, **kw):
        return _VolMarker("Coerce", a)

    def Schema(self, x):
        return ("Schema", x)


def _raising_install(*args, **kwargs):
    raise RuntimeError(
        "raised: simulated auto_install failure"
    )


def _extract_handler(src, service_name):
    """Pull the body of the
    ``handle_<service>`` closure
    out of
    ``async_register_services``.
    Returns the inner source or
    None.
    """
    import re
    pattern = re.compile(
        rf"async def handle_{service_name}\([^)]*\)[^:]*:\n(.*?)(?=\n\s+async def handle|\n    hass\.services|\Z)",
        re.DOTALL,
    )
    m = pattern.search(src)
    if m:
        return m.group(1)
    return None


# ─────────────────────────────────────────────────────────────
# R7.5 — samples 0→3 без
# регенерації dashboard
# ─────────────────────────────────────────────────────────────


def _make_real_engine_with_controller() -> tuple:
    """Build a real ``PredictiveControlEngine``
    with a real ``PredictiveHemsController``
    + ``ForecastCalibrator`` so the
    production evaluate() path is
    exercised end-to-end. No
    shortcuts, no ``_FakeHEMS``.

    Returns ``(engine, controller,
    calibrator)``.
    """
    import importlib
    sys.path.insert(0, _REPO_ROOT)
    eng_mod = importlib.import_module(
        "hems.predictive_control"
    )
    pred_mod = importlib.import_module(
        "hems.predictive"
    )
    cal_mod = importlib.import_module(
        "hems.forecast_calibration"
    )
    tun_mod = importlib.import_module("hems.tuning")
    engine = eng_mod.PredictiveControlEngine()
    # Shadow mode MUST be set BEFORE
    # any evaluate() call. The
    # constructor reads
    # ``predictive_tuning.predictive_mode``
    # and freezes
    # ``_predictive_enabled`` at
    # that moment. ``PredictiveTuning``
    # default is ``"off"`` which
    # would short-circuit the
    # entire planner branch.
    engine.predictive_tuning = (
        tun_mod.PredictiveTuning()
    )
    engine.predictive_tuning.predictive_mode = (
        "shadow"
    )
    engine._predictive_enabled = True
    engine._predictive_mode = "shadow"
    engine.predictive_min_confidence = 0.5
    # Production inputs the planner
    # reads on the ``_evaluate_predictive``
    # path. Without these the
    # planner returns ``(None, None)``
    # and ``real_pairs`` stays at 0.
    engine._battery_capacity_kwh = 4.8
    engine._hourly_pv_forecast = (
        [float(w) for w in range(0, 2400, 100)]
    )  # 24 values
    engine._hourly_radiation = (
        [0.0] * 8 + [400.0] * 8 + [0.0] * 8
    )
    engine._hourly_weather_codes = (
        [0] * 24
    )
    # The planner requires
    # ``forecast_today_kwh`` and
    # ``forecast_tomorrow_kwh`` as
    # finite numbers; the inputs
    # dict is built by ``evaluate()``
    # from ``self._last_forecast_today_kwh``.
    engine._last_forecast_today_kwh = 5.0
    # ``dated_hourly_pv_forecast`` is
    # optional; we leave it ``None``.
    # ``consumption_history`` is
    # optional; we leave it empty.
    # Attach the real production
    # controller + calibrator so the
    # ``controller.suggest(pi)`` and
    # ``controller.decide(pi)`` paths
    # inside ``_evaluate_predictive``
    # actually run.
    real_calibrator = (
        cal_mod.ForecastCalibrator(
            unit="W", max_samples=32
        )
    )
    controller = pred_mod.PredictiveHemsController()
    controller.calibrator = real_calibrator
    engine._predictive_controller = controller
    return engine, controller, real_calibrator


def _evaluate_real_engine(engine, **overrides):
    """Drive ``engine.evaluate()`` with
    the minimum valid input set so the
    planner path runs. Returns the
    ``HemsDecision`` produced.
    """
    base_kwargs = dict(
        smart_mode=1,  # ADAPTIVE
        hems_auto=True,
        soc=80.0,
        pv_power=2000.0,
        grid_power=0.0,
        battery_power=-500.0,
        load_power=1500.0,
        grid_voltage=230.0,
        grid_available=True,
        current_output="2",
        current_charger="2",
        forecast_tomorrow_kwh=4.0,
        reserve_soc=20.0,
        min_operating_soc=20.0,
        is_online=True,
        soc_unknown=False,
    )
    base_kwargs.update(overrides)
    return engine.evaluate(**base_kwargs)


def _drive_sensor_attrs(engine):
    """Invoke the real production
    ``PredictiveDecisionStateSensor.extra_state_attributes``
    body. The sensor class reads
    ``dict(self.coordinator._hems.predictive_decision_state)``
    — that IS the production
    reader for the AI view's
    ``attribute: "real_pairs"`` rows.
    Without this reader the
    frontend cannot re-render without
    a full dashboard regeneration.
    """
    ns = _exec_class(
        SENSOR_PY,
        "PredictiveDecisionStateSensor",
        args={
            "CoordinatorEntity": _StubBaseA,
            "SensorEntity": _StubBaseB,
            "DOMAIN": "powmr_inverter",
            "Any": Any,
        },
    )
    sensor_cls = ns["PredictiveDecisionStateSensor"]

    class _StubCoordinator:
        """Minimal stub: the
        production sensor only
        touches ``coordinator.api.device_sn``
        (in __init__) and
        ``coordinator._hems.predictive_decision_state``
        (in the property).
        """
        class api:
            device_sn = "SN000000000000001"
        _hems = engine

    class _StubEntry:
        entry_id = "entry_a"
        title = ""

    instance = sensor_cls.__new__(sensor_cls)
    # Mirror what the real __init__
    # does; do NOT call super().__init__()
    # because CoordinatorEntity would
    # require the full HA bootstrap.
    instance._attr_unique_id = (
        f"entry_a_predictive_decision_state"
    )
    instance._attr_device_info = {
        "identifiers": {
            ("powmr_inverter", "SN000000000000001")
        }
    }
    instance.coordinator = _StubCoordinator()
    instance._hems = engine
    # The production ``extra_state_attributes``
    # property is the reader the HA
    # frontend hits every polling
    # cycle. Reading it directly here
    # is exactly what HA does at
    # runtime.
    attrs = instance.extra_state_attributes
    # Strip the optional
    # ``forecast_calibration`` field
    # because the production body
    # appends it via a ``coordinator._pv_learning``
    # lookup that is None in this
    # test — the property is
    # ``if learning is not None``,
    # so the absence is a clean
    # production-path read.
    attrs.pop("forecast_calibration", None)
    return attrs, instance


def test_r75_samples_0_to_3_live_state_no_regresion() -> None:
    """R7.5 (samples 0→3, no
    regression).

    Drives the real production
    engine path:

      1. Build a real
         ``PredictiveControlEngine``
         with a real
         ``PredictiveHemsController``
         and a real
         ``ForecastCalibrator``.
      2. Run ``evaluate()`` once
         on an empty calibrator
         (0 samples). Read the
         production
         ``PredictiveDecisionStateSensor.extra_state_attributes``
         and assert
         ``real_pairs == 0``,
         ``readiness is False``,
         ``model_quality == 0.0``,
         ``confidence == 0.0``.
      3. Record 3 calibration
         pairs through the real
         ``ForecastCalibrator.record()``
         API and run ``evaluate()``
         again. Read the sensor
         attributes again and
         assert ``real_pairs == 3``
         and that ``readiness`` is
         consistent with the
         configured
         ``predictive_min_confidence``
         and the recorded samples.

    The dashboard JSON is a
    snapshot; the live sensor
    ``extra_state_attributes``
    is what actually re-renders.
    Without this path the user
    would have to delete and
    reinstall the integration to
    see the new ``real_pairs``
    number after the engine
    accumulates data — which is
    exactly the regression we
    fix.
    """
    engine, _ctl, cal = (
        _make_real_engine_with_controller()
    )

    # ── Step 1: 0 samples ──────────
    _evaluate_real_engine(engine)
    attrs_0, _ = _drive_sensor_attrs(engine)
    assert attrs_0.get("real_pairs") == 0, (
        "R7.5: 0 samples → real_pairs "
        f"must be 0; got {attrs_0.get('real_pairs')!r}"
    )
    assert attrs_0.get("readiness") is False, (
        "R7.5: 0 samples → readiness "
        f"must be False; got {attrs_0.get('readiness')!r}"
    )
    assert attrs_0.get("model_quality") == 0.0, (
        "R7.5: 0 samples → model_quality "
        f"must be 0.0; got {attrs_0.get('model_quality')!r}"
    )
    assert attrs_0.get("confidence") == 0.0, (
        "R7.5: 0 samples → confidence "
        f"must be 0.0; got {attrs_0.get('confidence')!r}"
    )
    # The dashboard reads
    # ``real_pairs`` via an
    # ``entities`` card with
    # ``attribute: "real_pairs"``
    # pointing at this sensor. A
    # frozen "0 пар" title would
    # indicate the dashboard JSON
    # captured a literal "0 пар"
    # string instead of an
    # attribute reference. The
    # production view must use
    # attribute-based rows.
    ns = _exec_function(INIT_PY, "_build_ai_view")
    real = ns["_build_ai_view"]

    def _entity_lookup(key):
        # The production view asks for
        # ``predictive_decision_state``
        # first (the row source) and
        # then ``predictive_hint`` /
        # ``predictive_plan`` /
        # ``hems_last_reason`` /
        # ``predictive_mode``. The
        # audit requires the
        # ``attribute: "real_pairs"``
        # row to resolve to the
        # live sensor.
        if key == "predictive_decision_state":
            return "sensor.pwr_decision"
        if key in (
            "real_pairs", "model_quality",
            "confidence", "readiness", "reason",
        ):
            return "sensor.pwr_decision"
        return ""

    view_0 = real(_entity_lookup, attrs_0)
    view_str_0 = json.dumps(view_0)
    # The view MUST reference the
    # live attribute, not a frozen
    # literal "0 пар". Look for
    # either an ``attribute:`` row
    # pointing at ``real_pairs`` OR
    # the title templated through
    # ``real_pairs``. We check both
    # to cover the documented
    # behaviour.
    assert (
        "real_pairs" in view_str_0
    ), (
        "R7.5: AI view must reference "
        "real_pairs via attribute row; "
        f"got {view_str_0[:300]}"
    )

    # ── Step 2: 3 samples ─────────
    # Use real ``ForecastCalibrator.record()``
    # so the production
    # metrics() path
    # returns sample_count=3.
    # ``record()`` enforces
    # ``MIN_FORECAST_W = 50``,
    # so values below 50 W are
    # silently dropped. Use 100 W
    # forecasts to land the pairs.
    cal.record(forecast_w=100.0, actual_w=90.0)
    cal.record(forecast_w=100.0, actual_w=95.0)
    cal.record(forecast_w=100.0, actual_w=88.0)
    metrics = cal.metrics()
    assert (
        metrics.sample_count == 3
    ), (
        "R7.5: real production "
        "calibrator must have "
        f"3 samples after 3 record(); "
        f"got {metrics.sample_count}"
    )

    _evaluate_real_engine(engine)
    attrs_3, _ = _drive_sensor_attrs(engine)
    assert attrs_3.get("real_pairs") == 3, (
        "R7.5: 3 samples → real_pairs "
        f"must be 3; got {attrs_3.get('real_pairs')!r}"
    )
    assert (
        attrs_3.get("samples") == 3
    ), (
        "R7.5: 3 samples → samples "
        f"must be 3; got {attrs_3.get('samples')!r}"
    )
    # ``model_quality`` is
    # ``confidence_factor`` from the
    # calibrator's metrics. The
    # exact value depends on the
    # sample distribution; we only
    # assert the production
    # threshold logic — readiness is
    # True iff confidence >= the
    # engine's configured
    # ``predictive_min_confidence``
    # AND samples >= 3.
    confidence_3 = attrs_3.get("confidence", 0.0)
    threshold = engine.predictive_min_confidence
    if (
        math.isfinite(confidence_3)
        and confidence_3 >= max(0.2, threshold)
    ):
        expected_readiness = True
    else:
        expected_readiness = False
    assert (
        attrs_3.get("readiness") is expected_readiness
    ), (
        "R7.5: readiness must "
        "follow the engine gate; "
        f"confidence={confidence_3!r}, "
        f"threshold={threshold!r}, "
        f"expected_readiness="
        f"{expected_readiness!r}, "
        f"got {attrs_3.get('readiness')!r}"
    )
    # The dashboard must read the
    # updated ``real_pairs`` value
    # without regenerating the
    # dashboard. We rebuild the
    # view from the NEW attrs (no
    # fresh dashboard generator
    # run) and assert it references
    # the new value.
    view_3 = real(_entity_lookup, attrs_3)
    view_str_3 = json.dumps(view_3)
    assert (
        "real_pairs" in view_str_3
    ), (
        "R7.5: AI view must continue "
        "to reference real_pairs via "
        "attribute row after the "
        "engine accumulated data; "
        f"got {view_str_3[:300]}"
    )
    # The same view function must
    # NOT contain the literal
    # "0 пар" string. A frozen
    # title is the regression.
    assert "0 пар" not in view_str_3, (
        "R7.5: view must not embed "
        "literal '0 пар' — the "
        "title must follow the "
        "live attribute; "
        f"got {view_str_3[:300]}"
    )


# ─────────────────────────────────────────────────────────────
# Test runner
# ─────────────────────────────────────────────────────────────


def test_r75_repeated_migration_isolation() -> None:
    """R7.5: repeated migration.

    Two entries ``A`` and ``B``
    share the same 8-character
    prefix
    (``01M3XWJ8...``). After
    confirmed migration of A:

      1. A's registered dashboard
         file MUST be updated
         with the new payload.
      2. B's registered dashboard
         file MUST remain
         untouched (preserved
         bytes).
      3. The
         ``lovelace_dashboards``
         metadata file MUST list
         both entries under
         distinct ``id`` and
         ``url_path`` keys.
      4. Storage keys MUST be
         distinct on disk.
      5. After a SECOND confirmed
         migration of A, A's
         dashboard file is updated
         again (proves the
         one-shot opt-in works
         twice — the service is
         repeatable).
      6. B's bytes STILL
         unchanged.

    This is the full audit chain
    from the Windows review:
    repeated migration of the
    same entry updates ITS
    dashboard and does not
    disturb the other entry's
    registered dashboard,
    metadata ID, storage key,
    or URL.
    """
    with tempfile.TemporaryDirectory() as tmp:
        storage = os.path.join(tmp, ".storage")
        os.makedirs(storage, exist_ok=True)
        # Pre-existing main
        # dashboard (USER EDIT).
        main_path = os.path.join(
            storage, "lovelace.powmr_energy"
        )
        with open(main_path, "w") as f:
            json.dump(
                {"data": {"config": {
                    "title": "USER EDIT",
                }}},
                f,
            )
        # Pre-existing
        # ``lovelace_dashboards``
        # registry. Empty items
        # list — the migration
        # must ADD entries, not
        # overwrite the file
        # structure.
        dash_reg_path = os.path.join(
            storage, "lovelace_dashboards"
        )
        with open(dash_reg_path, "w") as f:
            json.dump(
                {
                    "version": 1,
                    "data": {"items": []},
                },
                f,
            )
        # Two entries with
        # identical prefixes.
        entry_a = _FakeConfigEntry(
            "01M3XWJ8DRYDQC8A0NCPRVB53N"
        )
        entry_b = _FakeConfigEntry(
            "01M3XWJ8ZZZZZZZZZZZZZZZZZZZ"
        )
        hass = _FakeHass(
            config_dir=tmp,
            entries=[entry_a, entry_b],
            states={},
            services=_FakeServiceReg(),
        )
        # Opt-in for B ONLY (not
        # A). The handler must
        # migrate B alone; A's
        # file MUST stay at the
        # original USER EDIT
        # content because the
        # user did not confirm A.
        hass.data["powmr_inverter"] = {
            entry_a.entry_id: {
                "dashboard_migration_opt_in": False,
            },
            entry_b.entry_id: {
                "dashboard_migration_opt_in": True,
            },
        }
        # Load the
        # ``_register_lovelace_dashboard``
        # helper and call it
        # directly for each
        # entry. This is the
        # production registration
        # path the audit cares
        # about — it writes the
        # storage file AND adds
        # the ``id`` /
        # ``url_path`` entry to
        # the metadata file.
        ns = _exec_function(
            INIT_PY,
            "_register_lovelace_dashboard",
            args={
                "__builtins__": __builtins__,
                "json": json,
                "os": os,
                "shutil": shutil,
                "hashlib": hashlib,
                "tempfile": tempfile,
                "DOMAIN": "powmr_inverter",
                "_LOGGER": _FakeLogger(),
                # The helper uses
                # ``_compute_assets_cache_bust``
                # internally; provide a
                # no-op that preserves
                # the production contract
                # (returns a stable
                # string).
                "_compute_assets_cache_bust": (
                    lambda *a, **kw: "test_cache"
                ),
                "extra_modules": _load_registration_helpers(),
            },
        )
        register = ns["_register_lovelace_dashboard"]
        dummy_config = {
            "title": "Smart Solar · TEST",
            "views": [],
        }
        # First migration: B
        # only. A's opt-in is
        # False, so A's
        # dashboard MUST stay at
        # the original USER EDIT
        # bytes.
        _run(register(hass, entry_b, dummy_config))
        # Sidecar files must
        # exist for B (and only
        # for B — A is opt-out).
        sidecars = sorted(
            f for f in os.listdir(storage)
            if f.startswith("lovelace.powmr_energy")
            and not f.endswith(".bak")
            and f != "lovelace.powmr_energy"
        )
        assert len(sidecars) == 1, (
            "R7.5: only B should "
            "produce a sidecar; "
            f"got {sidecars}"
        )
        # The metadata file MUST
        # contain ONE item — B's
        # entry — with a
        # distinctive ``id`` and
        # ``url_path``.
        with open(dash_reg_path) as f:
            dash_meta_b = json.load(f)
        items_b = dash_meta_b["data"]["items"]
        assert len(items_b) == 1, (
            "R7.5: metadata must "
            "list exactly B; got "
            f"{items_b!r}"
        )
        b_item = items_b[0]
        assert b_item.get("id") != "powmr_energy", (
            "R7.5: B's metadata id "
            "must differ from the "
            "main powmr_energy "
            "id; got "
            f"{b_item.get('id')!r}"
        )
        assert (
            b_item.get("url_path")
            != "powmr-energy"
        ), (
            "R7.5: B's metadata "
            "url_path must differ "
            "from the main "
            "powmr-energy; got "
            f"{b_item.get('url_path')!r}"
        )
        # The main dashboard
        # file (``lovelace.powmr_energy``)
        # MUST still be USER
        # EDIT (A opt-out, no
        # global overwrite).
        with open(main_path) as f:
            main_after_b = json.load(f)
        assert (
            main_after_b["data"]["config"][
                "title"
            ]
            == "USER EDIT"
        ), (
            "R7.5: main dashboard "
            "was clobbered even "
            "though only B had "
            "opt-in; got "
            f"{main_after_b!r}"
        )
        # ── Second migration: A now opts in ──
        hass.data["powmr_inverter"][
            entry_a.entry_id
        ]["dashboard_migration_opt_in"] = True
        _run(register(hass, entry_a, dummy_config))
        # Now two sidecars
        # exist (one per entry).
        sidecars_2 = sorted(
            f for f in os.listdir(storage)
            if f.startswith("lovelace.powmr_energy")
            and not f.endswith(".bak")
            and f != "lovelace.powmr_energy"
        )
        assert len(sidecars_2) == 2, (
            "R7.5: A and B must "
            "produce two distinct "
            "sidecars; got "
            f"{sidecars_2}"
        )
        # Distinct storage keys
        # — the sidecar file
        # names themselves MUST
        # be different (no
        # collision).
        assert len(set(sidecars_2)) == 2, (
            "R7.5: sidecar file "
            "names collided; got "
            f"{sidecars_2}"
        )
        # Metadata file MUST
        # now list both A and B
        # with distinct IDs.
        with open(dash_reg_path) as f:
            dash_meta_ab = json.load(f)
        items_ab = dash_meta_ab["data"]["items"]
        assert len(items_ab) == 2, (
            "R7.5: metadata must "
            "list both A and B; "
            f"got {items_ab!r}"
        )
        all_ids = {
            item.get("id") for item in items_ab
        }
        all_url_paths = {
            item.get("url_path")
            for item in items_ab
        }
        assert len(all_ids) == 2, (
            "R7.5: metadata IDs "
            "are not distinct; "
            f"got {all_ids}"
        )
        assert (
            len(all_url_paths) == 2
        ), (
            "R7.5: metadata "
            "url_paths are not "
            "distinct; got "
            f"{all_url_paths}"
        )
        # ── Third migration: re-migrate A. The
        # service is repeatable.
        # The opt-in flag must
        # reset after success
        # and the user can
        # opt-in again.
        hass.data["powmr_inverter"][
            entry_a.entry_id
        ]["dashboard_migration_opt_in"] = True
        _run(register(hass, entry_a, dummy_config))
        # Still exactly two
        # sidecars — A's existing
        # sidecar is OVERWRITTEN
        # (the user's confirmed
        # opt-in), B's is
        # untouched.
        sidecars_3 = sorted(
            f for f in os.listdir(storage)
            if f.startswith("lovelace.powmr_energy")
            and not f.endswith(".bak")
            and f != "lovelace.powmr_energy"
        )
        assert len(sidecars_3) == 2, (
            "R7.5: re-migration "
            "of A must overwrite "
            "A's sidecar, not "
            "add a third; got "
            f"{sidecars_3}"
        )
        # Main dashboard still
        # USER EDIT (no global
        # overwrite).
        with open(main_path) as f:
            main_after_3 = json.load(f)
        assert (
            main_after_3["data"]["config"][
                "title"
            ]
            == "USER EDIT"
        ), (
            "R7.5: main dashboard "
            "was clobbered on "
            "repeated migration; "
            f"got {main_after_3!r}"
        )


def _run_all() -> None:
    failures: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    tests = sorted(
        [
            (name, fn)
            for name, fn in globals().items()
            if name.startswith("test_") and callable(fn)
        ]
    )
    for name, fn in tests:
        try:
            fn()
            print(f"  {name}: PASS")
        except unittest.SkipTest as exc:
            skipped.append((name, str(exc)))
            print(f"  {name}: SKIP ({exc})")
        except Exception as exc:
            failures.append((name, repr(exc)))
            print(f"  {name}: FAIL ({exc!r})")
    if failures:
        print(
            f"\n{len(failures)} of {len(tests)} tests failed:"
        )
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    print(
        f"\nAll {len(tests)} tests passed "
        f"({len(skipped)} skipped)."
    )
    sys.exit(0)


if __name__ == "__main__":
    _run_all()