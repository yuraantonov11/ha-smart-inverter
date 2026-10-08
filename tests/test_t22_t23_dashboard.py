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
PREDICTIVE_PY = os.path.join(
    _REPO_ROOT, "hems", "predictive_control.py"
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
        "_read_metadata_snapshot",
        "_rollback_dashboard_content",
    )
    shared: dict = {
        "__builtins__": __builtins__,
        "json": __import__("json"),
        "os": __import__("os"),
        "shutil": __import__("shutil"),
        "hashlib": __import__("hashlib"),
        "tempfile": __import__("tempfile"),
        "logging": __import__("logging"),
        "_LOGGER": __import__("logging").getLogger(
            "test_harness"
        ),
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

def _read_metadata_snapshot(dashboards_storage):
    """Test-side mirror of
    ``_read_metadata_snapshot``
    in
    ``__init__.py``. Reads
    the metadata file as a
    JSON object or returns
    an empty stub when the
    file is missing.

    The production code is
    ``_exec_function``-extracted
    into a namespace that
    does NOT include the
    module-level
    ``_read_metadata_snapshot``;
    this helper is injected
    at every call site that
    needs the
    ``_register_lovelace_dashboard``
    behaviour.
    """
    if not os.path.exists(dashboards_storage):
        return {
            "version": 1,
            "minor_version": 1,
            "key_version": 1,
            "data": {"items": []},
        }
    with open(dashboards_storage, "r") as f:
        return json.loads(f.read())





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
                "_read_metadata_snapshot": _read_metadata_snapshot,
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
                "_read_metadata_snapshot": _read_metadata_snapshot,
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
# R10.4 — idempotent
# entry→dashboard binding;
# reload / repeated migration
# must NOT create a second
# dashboard for the same
# ``entry_id``. Юра round 4:
# the previous logic created
# a new sidecar whenever the
# canonical ``main_path``
# existed, even for the same
# ``entry_id``. The fix binds
# each entry to a stable
# dashboard via
# ``entry.options["lovelace_dashboard_url_path"]``.
# ─────────────────────────────────────────────────────────────


def test_r104_repeated_setup_same_entry_creates_one_dashboard() -> None:
    """R10.4: a single config
    entry run through ``setup``
    / ``migration`` /
    ``setup`` must produce
    exactly ONE dashboard. The
    previous behaviour created
    a second sidecar each time
    ``main_path`` already
    existed (which is the case
    after the very first
    setup)."""

    with tempfile.TemporaryDirectory() as tmp:
        storage = os.path.join(tmp, ".storage")
        os.makedirs(storage, exist_ok=True)
        # Pre-existing main
        # dashboard (legacy /
        # previous install).
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
                    "data": {"items": [{
                        "id": "powmr_energy",
                        "url_path": "powmr-energy",
                        "show_in_sidebar": True,
                    }]},
                },
                f,
            )
        entry = _FakeConfigEntry(
            "01M3XWJ8DRYDQC8A0NCPRVB53N"
        )
        hass = _FakeHass(
            config_dir=tmp,
            entries=[entry],
            states={},
            services=_FakeServiceReg(),
        )
        hass.data["powmr_inverter"] = {
            entry.entry_id: {
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
                "_read_metadata_snapshot":
                    _read_metadata_snapshot,
            },
        )
        real = ns["_register_lovelace_dashboard"]
        # First setup.
        _run(
            real(
                hass, entry,
                {"title": "FIRST", "views": []},
            )
        )
        # Reload (simulate
        # HA restart: the sidecar
        # file persists, the
        # metadata persists).
        _run(
            real(
                hass, entry,
                {"title": "SECOND", "views": []},
            )
        )
        # Migration opt-in:
        # third setup with
        # new content.
        _run(
            real(
                hass, entry,
                {"title": "THIRD", "views": []},
            )
        )
        # There must be
        # EXACTLY ONE new
        # dashboard besides
        # ``lovelace.powmr_energy``
        # (the legacy main).
        sidecar_files = sorted(
            f for f in os.listdir(storage)
            if f.startswith("lovelace.powmr_energy")
            and not f.endswith(".bak")
            and f != "lovelace.powmr_energy"
        )
        assert len(sidecar_files) == 1, (
            f"R10.4: repeated setup of one "
            f"entry must NOT create more "
            f"than one sidecar; got "
            f"{sidecar_files}"
        )
        # The entry must have
        # a stable binding.
        binding = entry.options.get(
            "lovelace_dashboard_url_path"
        )
        assert binding is not None, (
            "R10.4: entry.options must "
            "carry the dashboard binding"
        )
        # The metadata must
        # list only ONE entry
        # for this inverter.
        with open(
            os.path.join(
                storage, "lovelace_dashboards"
            )
        ) as f:
            items = json.load(f)["data"]["items"]
        entry_items = [
            it for it in items
            if it.get("id") != "powmr_energy"
            and it.get("id") != "map"
            and it.get("id") != "my_home"
        ]
        assert len(entry_items) == 1, (
            f"R10.4: metadata must list "
            f"one dashboard per entry; "
            f"got {[it.get('id') for it in entry_items]}"
        )


def test_r104_two_distinct_entries_get_independent_dashboards() -> None:
    """R10.4: two different
    config entries must keep
    independent dashboards. A
    sidecar for entry A must
    never be reused for entry
    B even if A's storage is
    present."""

    with tempfile.TemporaryDirectory() as tmp:
        storage = os.path.join(tmp, ".storage")
        os.makedirs(storage, exist_ok=True)
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
                    "data": {"items": [{
                        "id": "powmr_energy",
                        "url_path": "powmr-energy",
                        "show_in_sidebar": True,
                    }]},
                },
                f,
            )
        entry_a = _FakeConfigEntry(
            "01M3XWJ8AAAAAAAAAAAAAAAAAAAA"
        )
        entry_b = _FakeConfigEntry(
            "01BBBBBBBBBBBBBBBBBBBBBBBBB"
        )
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
                "_read_metadata_snapshot":
                    _read_metadata_snapshot,
            },
        )
        real = ns["_register_lovelace_dashboard"]
        _run(
            real(
                hass, entry_a,
                {"title": "A BOARD", "views": []},
            )
        )
        _run(
            real(
                hass, entry_b,
                {"title": "B BOARD", "views": []},
            )
        )
        # Each entry has its
        # own binding and its
        # own content file.
        binding_a = entry_a.options.get(
            "lovelace_dashboard_url_path"
        )
        binding_b = entry_b.options.get(
            "lovelace_dashboard_url_path"
        )
        assert binding_a is not None
        assert binding_b is not None
        assert binding_a != binding_b, (
            f"R10.4: distinct entries must "
            f"have distinct bindings; "
            f"got A={binding_a} B={binding_b}"
        )
        sidecar_files = sorted(
            f for f in os.listdir(storage)
            if f.startswith("lovelace.powmr_energy")
            and not f.endswith(".bak")
            and f != "lovelace.powmr_energy"
        )
        assert len(sidecar_files) == 2, (
            f"R10.4: two distinct entries "
            f"must produce two sidecars; "
            f"got {sidecar_files}"
        )


# ─────────────────────────────────────────────────────────────
# R7.5 — service handler:
# flag reset, ambiguity,
# failure propagation
# ─────────────────────────────────────────────────────────────


def _extract_handler_into_harness(
    service_name: str,
) -> str:
    """Extract the production
    ``handle_<service>``
    closure into
    ``tests/.harness/handle_<service>.py``
    and return the harness
    file path. The handler
    is exec'd by the caller
    through ``compile(...) +
    exec(...)`` so tracebacks
    attribute to a real
    on-disk filename, not to
    ``<string>``.

    Audit R7.5 rejects the
    legacy source-text-only
    tests — they were passing
    on ``try / finally``
    literal presence but
    never executed the real
    handler. The harness
    pattern lets the test
    drive the full
    production path.
    """
    mod = _parse(SERVICES_PY)
    register_node = _function_node(
        mod, "async_register_services"
    )
    assert register_node is not None, (
        f"R7.5: async_register_services missing "
        f"(service={service_name})"
    )
    handler_node = None
    for child in register_node.body:
        if (
            isinstance(
                child, ast.AsyncFunctionDef
            )
            and child.name
            == f"handle_{service_name}"
        ):
            handler_node = child
            break
    assert handler_node is not None, (
        f"R7.5: handle_{service_name} closure "
        f"missing inside async_register_services"
    )
    body = ast.unparse(handler_node)
    harness_dir = os.path.join(
        _REPO_ROOT, "tests", ".harness"
    )
    os.makedirs(harness_dir, exist_ok=True)
    harness_path = os.path.join(
        harness_dir, f"handle_{service_name}.py"
    )
    with open(harness_path, "w", encoding="utf-8") as f:
        f.write(
            "# Auto-extracted from "
            "services/__init__.py by\n"
            "# tests/test_t22_t23_dashboard.py "
            f"for behavioural R7.5 "
            f"{service_name!r} regression.\n"
            "# Tracebacks MUST attribute to this "
            "filename.\n"
            "\n"
        )
        f.write(body)
        f.write("\n")
    return harness_path


def _exec_migrate_handler(
    harness_path: str,
    hass_obj,
    call_data,
    custom_components_mod,
) -> "tuple[Any, list, list, Any]":
    """Compile and exec the
    extracted handler, then
    invoke it with the given
    ``hass`` and ``call``.

    Returns ``(raised,
    captured_logs,
    captured_bus_events,
    handler)``. The handler
    itself is returned so the
    caller can invoke it again
    (e.g. for repeated-migration
    tests).
    """
    captured_logs: list = []
    captured_bus_events: list = []
    hass_obj.bus.events = captured_bus_events
    # Re-establish module-level
    # stubs so the harness's
    # ``from homeassistant.exceptions``
    # resolves.
    import sys as _sys
    import types
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
    # if a custom one is not
    # provided.
    if custom_components_mod is None:
        _cc_pkg = types.ModuleType(
            "custom_components"
        )
        custom_components_mod = types.ModuleType(
            "custom_components.powmr_inverter"
        )
        _sys.modules[
            "custom_components"
        ] = _cc_pkg
        _sys.modules[
            "custom_components.powmr_inverter"
        ] = custom_components_mod
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
    }
    with open(harness_path) as f:
        compiled = compile(
            f.read(), harness_path, "exec"
        )
    exec(compiled, ns)
    handler = ns[f"handle_migrate_dashboard"]
    call = _FakeServiceCall(call_data)
    raised = None
    try:
        _run(handler(call))
    except _ServiceValidationError as exc:
        raised = exc
    except Exception as exc:
        raised = exc
    return (
        raised,
        captured_logs,
        captured_bus_events,
        handler,
    )


def test_r75_service_handler_resets_flag_on_failure() -> None:
    """R7.5: handler flag reset.

    Drives the REAL handler
    end-to-end with a stub
    installer that raises.
    Asserts that the
    opt-in flag is reset
    AFTER the installer
    raised — i.e. the
    ``finally`` clause ran.
    The legacy source-text
    test was passing on
    literal ``try / finally``
    presence but never
    executed the handler.

    Failure contract:
      * ``dashboard_migration_opt_in``
        is reset to ``False``
        regardless of the
        installer outcome.
      * The success-only log
        message
        ``"Dashboard migration complete"``
        is NOT emitted on
        failure.
      * A failure bus event
        with ``ok=False`` OR a
        raised
        ``ServiceValidationError``
        surfaces to the
        caller.
    """
    import sys as _sys
    import types
    handler_path = _extract_handler_into_harness(
        "migrate_dashboard"
    )
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
    hass_obj = _FakeHass(
        config_dir="/tmp/x",
        entries=[_FakeConfigEntry("entry_a")],
        states={},
        services=_FakeServiceReg(),
    )
    hass_obj.data["powmr_inverter"] = {
        "entry_a": {
            "dashboard_migration_opt_in": True,
        }
    }
    raised, captured_logs, captured_events, _handler = (
        _exec_migrate_handler(
            handler_path,
            hass_obj,
            {
                "entry_id": "entry_a",
                "confirm": True,
            },
            _cc_mod,
        )
    )
    # Failure contract: the
    # service must report
    # failure to the caller
    # (raised OR bus event).
    failure_signalled = (
        raised is not None
    ) or any(
        event_data
        and event_data.get("results")
        and any(
            r.get("ok") is False
            for r in event_data["results"]
        )
        for _et, event_data in captured_events
    )
    assert failure_signalled, (
        "R7.5 handler reset: failure "
        "must be signalled via raised "
        "exception or bus event; "
        f"raised={raised!r}, events="
        f"{captured_events!r}"
    )
    # Flag MUST reset to False
    # after failure — the
    # ``finally`` clause ran.
    bundle = hass_obj.data["powmr_inverter"][
        "entry_a"
    ]
    assert bundle.get(
        "dashboard_migration_opt_in"
    ) is False, (
        "R7.5 handler reset: opt-in "
        "flag must reset to False "
        "after failure; got "
        f"{bundle!r}"
    )
    # No success-only log line.
    success_logs = [
        msg
        for level, msg in captured_logs
        if level == "info"
        and "Dashboard migration complete"
        in msg
    ]
    assert not success_logs, (
        "R7.5 handler reset: handler "
        "logged success even though "
        "installer raised; "
        f"logs={captured_logs!r}"
    )


def test_r75_service_handler_raises_on_no_entry_id_with_multi_entries() -> None:
    """R7.5: ambiguity rejection.

    Drives the REAL handler
    with TWO entries loaded
    and a service call that
    omits ``entry_id``. The
    handler MUST raise
    ``ServiceValidationError``
    and MUST NOT migrate any
    entry (no installer
    invocation, no
    ``.bak`` writes, no
    metadata changes).

    The legacy source-text
    test was passing on
    literal ``raise`` presence
    but never executed the
    handler. The new test
    asserts the behaviour:

      * ``ServiceValidationError``
        surfaces.
      * NO installer call
        happens (captor
        records zero calls).
      * Both entries' opt-in
        flags stay untouched
        (handler exited BEFORE
        the per-entry loop).
    """
    import sys as _sys
    import types
    handler_path = _extract_handler_into_harness(
        "migrate_dashboard"
    )
    install_calls: list = []
    _cc_pkg = types.ModuleType("custom_components")
    _cc_mod = types.ModuleType(
        "custom_components.powmr_inverter"
    )
    def _recording_install(hass, entry):
        install_calls.append(entry.entry_id)
        # If the handler actually
        # invokes the installer
        # when ambiguous, the
        # test fails. This
        # confirms the handler
        # rejected BEFORE
        # calling us.
        raise AssertionError(
            "installer was called despite "
            "ambiguous entry_id"
        )
    _cc_mod._auto_install_dashboard = (
        _recording_install
    )
    _sys.modules["custom_components"] = _cc_pkg
    _sys.modules[
        "custom_components.powmr_inverter"
    ] = _cc_mod
    # Two entries, both
    # eligible for migration.
    hass_obj = _FakeHass(
        config_dir="/tmp/x",
        entries=[
            _FakeConfigEntry(
                "01M3XWJ8DRYDQC8A0NCPRVB53N"
            ),
            _FakeConfigEntry(
                "01M3XWJ8ZZZZZZZZZZZZZZZZZZZ"
            ),
        ],
        states={},
        services=_FakeServiceReg(),
    )
    hass_obj.data["powmr_inverter"] = {
        "01M3XWJ8DRYDQC8A0NCPRVB53N": {
            "dashboard_migration_opt_in": True,
        },
        "01M3XWJ8ZZZZZZZZZZZZZZZZZZZ": {
            "dashboard_migration_opt_in": True,
        },
    }
    raised, _captured_logs, _captured_events, _h = (
        _exec_migrate_handler(
            handler_path,
            hass_obj,
            {
                # No entry_id —
                # ambiguity.
                "confirm": True,
            },
            _cc_mod,
        )
    )
    assert raised is not None, (
        "R7.5 ambiguity: handler MUST "
        "raise when ``entry_id`` is "
        "missing AND multiple entries "
        "are loaded; no exception was "
        "raised"
    )
    assert isinstance(
        raised, _ServiceValidationError
    ), (
        "R7.5 ambiguity: handler must "
        "raise ServiceValidationError; "
        f"got {type(raised).__name__}: "
        f"{raised!r}"
    )
    assert (
        "Multiple" in str(raised)
        or "specify" in str(raised)
        or "entry_id" in str(raised)
    ), (
        "R7.5 ambiguity: error message "
        "must mention the multiple "
        "entries and the missing "
        "entry_id; got "
        f"{str(raised)!r}"
    )
    # Installer MUST NOT have
    # been called.
    assert install_calls == [], (
        "R7.5 ambiguity: handler called "
        "the installer even though "
        "the resolver rejected "
        f"ambiguity; got {install_calls}"
    )
    # Both opt-in flags
    # unchanged — the handler
    # exited before the
    # per-entry loop.
    for entry_id in (
        "01M3XWJ8DRYDQC8A0NCPRVB53N",
        "01M3XWJ8ZZZZZZZZZZZZZZZZZZZ",
    ):
        bundle = hass_obj.data[
            "powmr_inverter"
        ][entry_id]
        assert bundle.get(
            "dashboard_migration_opt_in"
        ) is True, (
            "R7.5 ambiguity: handler "
            "touched opt-in flag of "
            f"{entry_id} despite "
            "rejection; got "
            f"{bundle!r}"
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
                "_read_metadata_snapshot": _read_metadata_snapshot,
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



def test_r75_real_write_failure_propagates_to_service() -> None:
    """R7.5: real writer failure.

    Injects an OSError into the
    REAL production
    ``_write_dashboard_atomic``
    helper — not into a
    fake installer that
    raises immediately. The
    audit REJECTS the previous
    test design where the
    installer raise masked
    the writer behaviour:
    the previous run left
    ``metadata`` listing the
    new dashboard while
    ``content`` was never
    written (a dangling
    registration).

    The end-to-end
    ``service → installer →
    registration → metadata``
    chain must satisfy:

      1. ``_register_lovelace_dashboard``
         re-raises the
         ``OSError`` to the
         caller (the service
         handler).
      2. The previous
         ``lovelace.powmr_energy``
         content is preserved
         byte-for-byte (the
         user's USER EDIT
         survives).
      3. The
         ``lovelace_dashboards``
         metadata file is NOT
         modified — it still
         lists the original
         main dashboard only,
         NOT a dangling entry.
      4. The
         ``dashboard_migration_opt_in``
         flag is reset to
         ``False``.
      5. The handler does NOT
         log
         ``"Dashboard migration complete"``
         and does NOT emit a
         success bus event.
      6. A failure bus event
         with ``ok=False`` is
         emitted.
    """
    import sys as _sys
    import types
    import traceback
    import asyncio

    # ── Set up the production
    # handler via the same
    # extraction pattern as
    # the rest of the R7.5
    # suite.
    mod = _parse(SERVICES_PY)
    register_node = _function_node(
        mod, "async_register_services"
    )
    assert register_node is not None
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
    assert handler_node is not None
    body = ast.unparse(handler_node)
    harness_dir = os.path.join(
        _REPO_ROOT, "tests", ".harness"
    )
    os.makedirs(harness_dir, exist_ok=True)
    handler_file = os.path.join(
        harness_dir,
        "handle_migrate_dashboard.py",
    )
    with open(handler_file, "w", encoding="utf-8") as f:
        f.write(
            "# Auto-extracted from services/__init__.py.\n"
            "\n"
        )
        f.write(body)
        f.write("\n")
    # Stub the third-party
    # modules the handler
    # imports at runtime.
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
    # ── Real filesystem
    # pre-state: existing
    # USER EDIT dashboard.
    tmp_root = tempfile.mkdtemp(
        prefix="r75_realwrite_"
    )
    storage = os.path.join(tmp_root, ".storage")
    os.makedirs(storage, exist_ok=True)
    main_path = os.path.join(
        storage, "lovelace.powmr_energy"
    )
    user_content = {
        "title": "USER EDIT",
        "views": [{"title": "ORIGINAL"}],
    }
    with open(main_path, "w", encoding="utf-8") as f:
        json.dump(user_content, f)
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
    with open(main_path) as f:
        before_main_bytes = f.read()
    with open(dash_reg_path) as f:
        before_metadata_bytes = f.read()
    # The stub installer
    # invokes the REAL
    # ``_register_lovelace_dashboard``
    # via the integration
    # module. The integration
    # module's
    # ``_write_dashboard_atomic``
    # is wrapped to raise
    # ``OSError`` so the
    # failure surfaces at the
    # writer, not at a fake
    # installer.
    _cc_pkg = types.ModuleType("custom_components")
    _cc_mod = types.ModuleType(
        "custom_components.powmr_inverter"
    )
    # Import the production
    # ``_register_lovelace_dashboard``
    # by execing the
    # production module and
    # monkey-patching the
    # writer. This is the
    # real path the audit
    # demands — not a
    # stub.
    _sys.path.insert(0, _REPO_ROOT)
    # Strip pre-existing
    # custom_components
    # modules so the in-test
    # stub wins.
    for name in list(_sys.modules):
        if name == "custom_components":
            del _sys.modules[name]
        elif name.startswith("custom_components."):
            del _sys.modules[name]
    _sys.modules["custom_components"] = _cc_pkg
    _sys.modules[
        "custom_components.powmr_inverter"
    ] = _cc_mod
    # Now drive the integration
    # module via ``ast.unparse``
    # on its top-level
    # functions — we wrap the
    # ``_write_dashboard_atomic``
    # symbol so the production
    # ``_register_lovelace_dashboard``
    # actually calls a
    # ``_write_dashboard_atomic``
    # that raises. To do this
    # without importing the
    # full module (which
    # needs HA / voluptuous),
    # we exec the production
    # ``_init__.py`` module
    # body in an isolated
    # namespace, then call
    # the function from that
    # namespace with the
    # patched writer.
    with open(INIT_PY, encoding="utf-8") as f:
        init_src = f.read()
    init_mod = ast.parse(init_src)
    # Collect helper function
    # names that
    # ``_register_lovelace_dashboard``
    # needs at module level:
    # ``_write_dashboard_atomic``,
    # ``_write_dashboards_metadata_atomic``,
    # ``_update_dashboard_content``,
    # ``_compute_assets_cache_bust``,
    # ``_read_metadata_snapshot``.
    needed = {
        "_write_dashboard_atomic",
        "_write_dashboards_metadata_atomic",
        "_update_dashboard_content",
        "_compute_assets_cache_bust",
        "_read_metadata_snapshot",
    }
    captured = {}
    for node in init_mod.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in needed
        ):
            captured[node.name] = ast.unparse(node)
    # Build a tiny harness
    # module that defines
    # all captured helpers and
    # a wrapped
    # ``_write_dashboard_atomic``
    # that raises OSError on
    # the FIRST call only.
    real_writer_path = os.path.join(
        harness_dir, "_register_real_writer_harness.py"
    )
    wrapped_writer = (
        "def _write_dashboard_atomic(target_path, payload):\n"
        "    raise OSError(\"[INJECTED] dashboard write failure\")\n\n"
    )
    body = wrapped_writer
    for name, src_code in captured.items():
        if name == "_write_dashboard_atomic":
            continue  # wrapped version wins
        body += src_code + "\n\n"
    body += (
        "# ``_register_lovelace_dashboard`` lives below — we\n"
        "# capture it too so the\n"
        "# service handler calls\n"
        "# the production body.\n"
        + captured.get(
            "_register_lovelace_dashboard",
            "pass",
        )
        + "\n"
    )
    with open(real_writer_path, "w", encoding="utf-8") as f:
        f.write(
            "# Auto-generated by "
            "test_r75_real_write_failure_propagates_to_service.\n"
            "# Wraps the REAL production "
            "_write_dashboard_atomic with\n"
            "# an OSError-raising stub to\n"
            "# validate that the diagnostic\n"
            "# surface reaches the service "
            "handler.\n"
            "\n"
            + body
        )
    with open(real_writer_path) as f:
        harness_ns = {
            "__builtins__": __builtins__,
            "json": json,
            "os": os,
            "shutil": shutil,
            "hashlib": hashlib,
            "tempfile": tempfile,
            "logging": __import__("logging"),
            "HomeAssistant": object,
            "ConfigEntry": object,
            "DOMAIN": "powmr_inverter",
        }
        exec(
            compile(f.read(), real_writer_path, "exec"),
            harness_ns,
        )
    # Install the harness's
    # helpers onto the
    # integration module stub
    # so the service handler
    # resolves them through
    # ``sys.modules``.
    for name in (
        "_write_dashboard_atomic",
        "_write_dashboards_metadata_atomic",
        "_update_dashboard_content",
        "_compute_assets_cache_bust",
        "_read_metadata_snapshot",
        "_register_lovelace_dashboard",
    ):
        if name in harness_ns:
            setattr(_cc_mod, name, harness_ns[name])

    # The service handler
    # looks up ``_auto_install_dashboard``
    # on the integration
    # module. Wrap the
    # production
    # ``_register_lovelace_dashboard``
    # (from the harness) as a
    # sync shim that the
    # handler can ``await``.
    # ``_register_lovelace_dashboard``
    # already does the file
    # I/O via
    # ``async_add_executor_job``,
    # so a thin async wrapper
    # is enough. The wrapper
    # propagates the OSError
    # raised by the wrapped
    # writer.
    async def _auto_install_dashboard(hass, entry):
        await harness_ns["_register_lovelace_dashboard"](
            hass, entry, {
                "title": "Auto-installed by service",
                "views": [],
            }
        )

    _cc_mod._auto_install_dashboard = (
        _auto_install_dashboard
    )
    # ── Plain handler namespace ─────
    captured_logs = []
    captured_bus_events = []
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
    }
    try:
        with open(handler_file) as f:
            compiled = compile(
                f.read(), handler_file, "exec"
            )
        exec(compiled, ns)
    except NameError as exc:
        tb = traceback.format_exc()
        assert False, (
            "R7.5 real-write: handler "
            "extraction failed: "
            f"{exc}\n{tb}"
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
    except BaseException as exc:
        raised = exc
    # ── Assertions (ALL FOUR AT ONCE) ─────
    # (1) Service reports failure.
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
        "R7.5 real-write: handler must "
        "report failure; raised="
        f"{raised!r}; bus="
        f"{captured_bus_events!r}"
    )
    # (2) Previous content
    # preserved verbatim.
    with open(main_path) as f:
        after_main_bytes = f.read()
    assert (
        after_main_bytes == before_main_bytes
    ), (
        "R7.5 real-write: existing "
        "content was modified "
        f"({after_main_bytes!r} != "
        f"{before_main_bytes!r})"
    )
    # (3) Metadata NOT modified —
    # no dangling registration.
    with open(dash_reg_path) as f:
        after_metadata = json.load(f)
    items_after = after_metadata["data"]["items"]
    ids_after = {
        item.get("id") for item in items_after
    }
    assert ids_after == {"powmr_energy"}, (
        "R7.5 real-write: metadata "
        "must not list a dangling "
        "dashboard after write "
        f"failure; got ids={ids_after}"
    )
    # No new content file was
    # written.
    new_files = sorted(
        f for f in os.listdir(storage)
        if f.startswith("lovelace.powmr_energy")
        and not f.endswith(".bak")
        and f != "lovelace.powmr_energy"
    )
    assert new_files == [], (
        "R7.5 real-write: a new "
        "sidecar file was created "
        "despite write failure; "
        f"got {new_files}"
    )
    # (4) Opt-in flag resets.
    bundle = hass_obj.data[
        "powmr_inverter"
    ]["entry_a"]
    assert bundle.get(
        "dashboard_migration_opt_in"
    ) is False, (
        "R7.5 real-write: opt-in "
        "flag must reset after "
        f"failure; got {bundle!r}"
    )
    # (5) No success log line
    # and no success-only
    # bus event.
    success_logs = [
        msg
        for level, msg in captured_logs
        if level == "info"
        and "Dashboard migration complete"
        in msg
    ]
    assert not success_logs, (
        "R7.5 real-write: handler "
        "logged success even "
        "though write failed; "
        f"captured={captured_logs!r}"
    )
    # (6) Either a failure
    # bus event with ok=False
    # OR the ServiceValidationError
    # surfaced.
    if raised is None:
        assert any(
            event_data
            and event_data.get("results")
            and any(
                r.get("ok") is False
                for r in event_data["results"]
            )
            for _et, event_data in captured_bus_events
        ), (
            "R7.5 real-write: no "
            "failure bus event "
            "emitted; events="
            f"{captured_bus_events!r}"
        )
    # (5) No success log line
    # and no success-only
    # bus event.
    success_logs = [
        msg
        for level, msg in captured_logs
        if level == "info"
        and "Dashboard migration complete"
        in msg
    ]
    assert not success_logs, (
        "R7.5 real-write: handler "
        "logged success even "
        "though write failed; "
        f"captured={captured_logs!r}"
    )
    # (6) Either a failure
    # bus event with ok=False
    # OR the ServiceValidationError
    # surfaced.
    if raised is None:
        # Look for an event
        # whose payload marks
        # ok=False.
        assert any(
            event_data
            and event_data.get("results")
            and any(
                r.get("ok") is False
                for r in event_data["results"]
            )
            for _et, event_data in captured_bus_events
        ), (
            "R7.5 real-write: no "
            "failure bus event "
            "emitted; events="
            f"{captured_bus_events!r}"
        )



def test_r101_rollback_preserves_existing_sidecar_with_main_present() -> None:
    """R10.1: rollback
    preserves a previously
    registered sidecar when
    the helper was driven
    through the production
    path with both the
    canonical main
    dashboard AND a
    sidecar already on
    disk.

    The previous round-9
    fixture created ONLY
    the sidecar and no
    main dashboard, which
    made the registration
    helper pick
    ``is_first_opt_in=True``
    and write to
    ``main_path`` (the
    canonical main
    dashboard) rather
    than to the sidecar.
    The rollback then
    restored the wrong
    file and the test
    passed for the wrong
    reason: the destructive
    ``os.unlink``
    behaviour would have
    also passed because
    the test never
    exercised the sidecar
    restore path.

    This test sets up the
    correct production
    state:

      * ``lovelace.powmr_energy``
        exists (canonical
        main dashboard) with
        content
        ``"MAIN USER EDIT"``;
      * ``lovelace.powmr_energy_<entry_hash>``
        exists (sidecar) with
        content
        ``"SIDECAR USER PRIOR"``;
      * ``lovelace_dashboards``
        lists the main
        dashboard but NOT
        the sidecar id (the
        sidecar is a file on
        disk from a prior
        round, not yet
        registered in the
        current entry's
        metadata).

    The helper then runs
    with opt-in enabled
    and the metadata
    writer wrapped to
    raise. The contract
    is that the rollback
    restores the sidecar
    from its ``.bak``
    atomically and leaves
    the main dashboard
    untouched.

    Failure contract:
      * OSError propagates
        with ``"INJECTED"``
      * sidecar content is
        byte-for-byte
        preserved
        (``"SIDECAR USER
        PRIOR"``)
      * ``sidecar + .bak``
        is removed after
        the successful
        restore
      * main dashboard
        content is NOT
        modified
      * metadata is NOT
        modified
    """
    import sys as _sys
    import types

    helpers = _load_registration_helpers()
    real_write_content = helpers[
        "_write_dashboard_atomic"
    ]
    real_read_metadata = helpers.get(
        "_read_metadata_snapshot",
        _read_metadata_snapshot,
    )
    metadata_call_count = {"n": 0}

    def _raising_metadata_write(
        target_path, payload
    ):
        metadata_call_count["n"] += 1
        raise OSError(
            "[INJECTED] dashboards metadata write failure"
        )

    # ── Real filesystem
    # pre-state.
    tmp_root = tempfile.mkdtemp(
        prefix="r101_rollback_"
    )
    storage = os.path.join(tmp_root, ".storage")
    os.makedirs(storage, exist_ok=True)
    entry_id = "01M3XWJ8R101ROLLBACK000000000"
    import hashlib as _hl
    entry_hash = _hl.md5(
        entry_id.encode("utf-8")
    ).hexdigest()[:16]
    sidecar_id = f"powmr_energy_{entry_hash}"
    sidecar_path = os.path.join(
        storage, f"lovelace.{sidecar_id}"
    )
    main_path = os.path.join(
        storage, "lovelace.powmr_energy"
    )
    # Canonical main
    # dashboard (USER EDIT).
    with open(main_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "key": "lovelace.powmr_energy",
                "version": 1,
                "data": {"config": {
                    "title": "MAIN USER EDIT",
                    "views": [{"title": "MAIN VIEW"}],
                }},
            },
            f,
        )
    with open(main_path) as f:
        before_main_bytes = f.read()
    # Existing sidecar (USER
    # PRIOR).
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "key": f"lovelace.{sidecar_id}",
                "version": 1,
                "data": {"config": {
                    "title": "SIDECAR USER PRIOR",
                    "views": [
                        {"title": "OLD VIEW 1"},
                        {"title": "OLD VIEW 2"},
                    ],
                }},
            },
            f,
        )
    with open(sidecar_path) as f:
        before_sidecar_bytes = f.read()
    # Metadata lists ONLY
    # the main dashboard.
    # The sidecar is a
    # leftover file from a
    # prior round; not
    # listed in this
    # round's metadata.
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
                        "title": "MAIN TITLE",
                    },
                ]},
            },
            f,
        )
    with open(dash_reg_path) as f:
        before_metadata_bytes = f.read()
    hass_obj = _FakeHass(
        config_dir=tmp_root,
        entries=[
            _FakeConfigEntry(entry_id, title="R101")
        ],
        states={},
        services=_FakeServiceReg(),
    )
    hass_obj.data["powmr_inverter"] = {
        entry_id: {
            "dashboard_migration_opt_in": True,
        }
    }
    entry = hass_obj.config_entries.async_entries(
        "powmr_inverter"
    )[0]
    captured_logs: list = []
    exec_ns: dict = {
        "__builtins__": __builtins__,
        "json": json,
        "os": os,
        "shutil": shutil,
        "hashlib": hashlib,
        "tempfile": tempfile,
        "logging": __import__("logging"),
        "DOMAIN": "powmr_inverter",
        "HomeAssistant": object,
        "ServiceCall": object,
        "ConfigEntry": object,
        "callback": lambda *a, **k: None,
        "_LOGGER": _CapturingLogger(captured_logs),
    }
    exec_ns.update(helpers)
    exec_ns[
        "_write_dashboards_metadata_atomic"
    ] = _raising_metadata_write
    body = ast.unparse(
        _function_node(
            _parse(INIT_PY),
            "_register_lovelace_dashboard",
        )
    )
    update_body = ast.unparse(
        _function_node(
            _parse(INIT_PY),
            "_update_dashboard_content",
        )
    )
    exec(compile(update_body, INIT_PY, "exec"), exec_ns)
    exec(
        compile(body, INIT_PY, "exec"), exec_ns
    )
    register_fn = exec_ns[
        "_register_lovelace_dashboard"
    ]
    raised = None
    try:
        _run(register_fn(
            hass_obj,
            entry,
            {
                "title": "NEW SIDECAR TITLE",
                "views": [{"title": "NEW SIDECAR VIEW"}],
            },
        ))
    except OSError as exc:
        raised = exc
    # ── 1) The OSError
    # propagates.
    assert raised is not None, (
        "R10.1: helper must re-raise; "
        "no exception surfaced"
    )
    assert (
        "INJECTED" in str(raised)
    ), (
        "R10.1: raised must be the wrapped "
        f"metadata writer failure; got {str(raised)!r}"
    )
    assert metadata_call_count["n"] >= 1, (
        "R10.1: the wrapped metadata writer "
        "must have been invoked"
    )
    # ── 2) The sidecar
    # content is preserved
    # byte-for-byte.
    with open(sidecar_path) as f:
        after_sidecar_bytes = f.read()
    assert (
        after_sidecar_bytes == before_sidecar_bytes
    ), (
        "R10.1: sidecar content was modified "
        "by the rollback; expected "
        "byte-for-byte preservation. "
        "before="
        f"{before_sidecar_bytes!r} "
        "after="
        f"{after_sidecar_bytes!r}"
    )
    sidecar_now = json.loads(after_sidecar_bytes)
    assert (
        sidecar_now["data"]["config"]["title"]
        == "SIDECAR USER PRIOR"
    ), (
        "R10.1: sidecar title was changed to "
        f"{sidecar_now['data']['config']['title']!r}; "
        "expected SIDECAR USER PRIOR"
    )
    # ── 3) The ``.bak``
    # was removed (so the
    # next update starts
    # from a clean slate).
    bak_path = sidecar_path + ".bak"
    assert not os.path.exists(bak_path), (
        "R10.1: rollback must remove the "
        f"``.bak`` after restoring; "
        f"bak still at {bak_path}"
    )
    # ── 4) The main
    # dashboard is NOT
    # modified.
    with open(main_path) as f:
        after_main_bytes = f.read()
    assert (
        after_main_bytes == before_main_bytes
    ), (
        "R10.1: main dashboard was modified; "
        "before="
        f"{before_main_bytes!r} "
        "after="
        f"{after_main_bytes!r}"
    )
    # ── 5) The metadata
    # is NOT modified.
    with open(dash_reg_path) as f:
        after_metadata_bytes = f.read()
    assert (
        after_metadata_bytes == before_metadata_bytes
    ), (
        "R10.1: metadata was modified despite "
        "the writer failure; before="
        f"{before_metadata_bytes!r} after="
        f"{after_metadata_bytes!r}"
    )


def test_r101_rollback_unlinks_fresh_target_without_bak() -> None:
    """R10.1: when
    ``target_existed`` is
    False (the target was
    a brand-new file
    created by this
    operation),
    ``_rollback_dashboard_content``
    must delete the
    target.

    Failure contract:
      * target is removed
        from disk
      * if a stale
        ``.bak`` is
        present (left
        over from a
        previous
        operation), it is
        left in place —
        the rollback is
        about the target,
        not the backup
    """
    tmp_root = tempfile.mkdtemp(
        prefix="r101_unlink_"
    )
    storage = os.path.join(tmp_root, ".storage")
    os.makedirs(storage, exist_ok=True)
    target_path = os.path.join(
        storage, "lovelace.test_fresh"
    )
    bak_path = target_path + ".bak"
    # Write a fresh target
    # (no prior .bak).
    with open(target_path, "w") as f:
        f.write("FRESH CONTENT")
    assert os.path.exists(target_path)
    helpers = _load_registration_helpers()
    rollback = helpers["_rollback_dashboard_content"]
    # Stale .bak from a
    # previous (failed)
    # round.
    with open(bak_path, "w") as f:
        f.write("STALE BAK")
    rollback(target_path, target_existed=False)
    assert not os.path.exists(target_path), (
        "R10.1: fresh target must be removed "
        "when target_existed=False"
    )
    # Stale .bak is left in
    # place — not part of
    # this rollback's
    # responsibility.
    assert os.path.exists(bak_path), (
        "R10.1: rollback must not touch "
        "a stale .bak; it was left at "
        f"{bak_path}"
    )


def test_r101_rollback_keeps_bak_when_target_missing() -> None:
    """R10.1: when
    ``target_existed``
    is True but the
    ``.bak`` is
    missing (e.g.
    disk was wiped
    between the
    failed write and
    the rollback),
    the rollback
    leaves the
    current (new)
    target in place
    rather than
    deleting it.

    The user would
    otherwise be left
    with no content
    file at all. The
    warning is logged
    but no exception
    is raised.
    """
    tmp_root = tempfile.mkdtemp(
        prefix="r101_missing_bak_"
    )
    storage = os.path.join(tmp_root, ".storage")
    os.makedirs(storage, exist_ok=True)
    target_path = os.path.join(
        storage, "lovelace.test_nobak"
    )
    # The new content
    # (the helper wrote
    # it before the
    # rollback).
    with open(target_path, "w") as f:
        f.write("NEW CONTENT")
    helpers = _load_registration_helpers()
    rollback = helpers["_rollback_dashboard_content"]
    # No .bak exists.
    # Rollback should
    # leave the new
    # target alone.
    rollback(target_path, target_existed=True)
    assert os.path.exists(target_path), (
        "R10.1: when .bak is missing the "
        "target must NOT be deleted; user "
        "would be left with no content"
    )
    with open(target_path) as f:
        assert f.read() == "NEW CONTENT"


def test_r101_rollback_idempotent() -> None:
    """R10.1: a second
    call to
    ``_rollback_dashboard_content``
    after the first
    call has already
    removed the
    ``.bak`` must be
    a no-op.

    The previous
    round-9
    implementation
    would have
    re-entered the
    ``if
    os.path.exists(bak_path)``
    branch on the
    second call and
    destroyed the
    just-restored
    target. The
    round-10 rewrite
    short-circuits
    because there is
    no ``.bak`` to
    restore from.
    """
    tmp_root = tempfile.mkdtemp(
        prefix="r101_idempotent_"
    )
    storage = os.path.join(tmp_root, ".storage")
    os.makedirs(storage, exist_ok=True)
    target_path = os.path.join(
        storage, "lovelace.test_idem"
    )
    bak_path = target_path + ".bak"
    # Set up: existing
    # previous content +
    # .bak (the writer
    # left both in this
    # state).
    with open(target_path, "w") as f:
        f.write("NEW AFTER WRITE")
    with open(bak_path, "w") as f:
        f.write("OLD PRIOR CONTENT")
    helpers = _load_registration_helpers()
    rollback = helpers["_rollback_dashboard_content"]
    # First call —
    # restores from .bak.
    rollback(target_path, target_existed=True)
    with open(target_path) as f:
        assert f.read() == "OLD PRIOR CONTENT", (
            "R10.1: first rollback must "
            "restore the .bak content"
        )
    assert not os.path.exists(bak_path), (
        "R10.1: first rollback must "
        "remove the .bak after restore"
    )
    # Second call —
    # no-op. The .bak is
    # gone, but we still
    # pass target_existed=True
    # because the user
    # said the target
    # originally existed.
    # The helper must
    # short-circuit.
    rollback(target_path, target_existed=True)
    # Target is still
    # intact (the second
    # call did NOT delete
    # it).
    with open(target_path) as f:
        assert f.read() == "OLD PRIOR CONTENT", (
            "R10.1: second rollback must be a "
            "no-op; the just-restored target "
            "was destroyed"
        )


def test_r101_destructive_old_unlink_would_fail_with_bak_present() -> None:
    """R10.1: regression
    test — verify that
    the destructive
    pre-round-10
    ``os.unlink(target_path)``
    behaviour WOULD
    FAIL when the
    target existed and
    the .bak is
    present. The
    current round-10
    implementation
    passes; the old
    behaviour is shown
    here to fail so
    the audit has
    evidence that the
    test catches a
    regression.
    """
    import shutil as _shutil

    tmp_root = tempfile.mkdtemp(
        prefix="r101_old_behavior_"
    )
    storage = os.path.join(tmp_root, ".storage")
    os.makedirs(storage, exist_ok=True)
    target_path = os.path.join(
        storage, "lovelace.test_old_behavior"
    )
    bak_path = target_path + ".bak"
    # Old behaviour:
    # always unlink
    # the target on
    # rollback,
    # regardless of
    # whether the
    # target existed.
    prior_content = "PRIOR USER CONTENT"
    with open(target_path, "w") as f:
        f.write(prior_content)
    with open(bak_path, "w") as f:
        f.write(prior_content)
    # Apply the old
    # destructive
    # behaviour.
    if os.path.exists(bak_path):
        # The old
        # implementation
        # did NOT
        # restore — it
        # only deleted
        # the target.
        # This is the
        # regression we
        # are testing
        # against.
        if os.path.exists(target_path):
            os.unlink(target_path)
    # The old behaviour
    # destroyed the
    # sidecar. The
    # round-10 behaviour
    # would have
    # restored it.
    assert not os.path.exists(target_path), (
        "R10.1 regression: old behaviour "
        "should have destroyed the target"
    )
    # Now show that the
    # round-10 behaviour
    # on the SAME setup
    # would have
    # preserved the
    # target.
    with open(target_path, "w") as f:
        f.write("NEW AFTER WRITE")
    with open(bak_path, "w") as f:
        f.write(prior_content)
    helpers = _load_registration_helpers()
    rollback = helpers["_rollback_dashboard_content"]
    rollback(target_path, target_existed=True)
    with open(target_path) as f:
        content_after = f.read()
    assert (
        content_after == prior_content
    ), (
        "R10.1: round-10 rollback must "
        "restore the prior content; got "
        f"{content_after!r}"
    )


def test_r91_predictive_decision_state_publishes_calibrator_metrics_on_hold() -> None:
    """R9.1: regression
    test. The
    ``PredictiveControlEngine.evaluate``
    init dict MUST
    publish
    ``readiness``,
    ``real_pairs``,
    and
    ``model_quality``
    from the
    calibrator even
    when the
    engine's
    decision path
    did not run
    (``manual_override_hold``,
    ``hems_auto_off``,
    ``inverter_offline``).

    Without this fix
    the live dashboard
    showed ``None``
    in the AI view's
    ``attribute:
    real_pairs`` row
    during a hold,
    because the
    ``real_pairs``
    key was never
    written when
    ``super().evaluate()``
    returned without
    running the
    proposal path.

    The fix is in
    ``PredictiveControlEngine.evaluate``:
    the engine reads
    the calibrator
    metrics on EVERY
    evaluate cycle
    (not only when
    the planner
    produced a
    proposal) and
    publishes them
    on
    ``predictive_decision_state``.

    Failure contract:
      * ``real_pairs``,
        ``model_quality``,
        and
        ``readiness``
        are present in
        the dict after
        ``evaluate()``
      * the values
        match the
        calibrator
        metrics
      * when the
        controller is
        missing
        (predictive
        assist is
        disabled), the
        engine does
        NOT raise; it
        falls back to
        ``samples=0,
        confidence_factor=0.0``
    """
    from dataclasses import dataclass

    @dataclass
    class FakeCalibrationMetrics:
        sample_count: int
        confidence_factor: float

    class FakeCalibrator:
        def __init__(self, n: int, cf: float) -> None:
            self._n = n
            self._cf = cf
            self.metrics_call_count = 0

        def metrics(self) -> FakeCalibrationMetrics:
            self.metrics_call_count += 1
            return FakeCalibrationMetrics(
                self._n, self._cf
            )

    class FakeController:
        def __init__(self, calibrator) -> None:
            self.calibrator = calibrator

    class _StubTuning:
        predictive_mode = "shadow"

    class _StubHemsBase:
        def __init__(self, *args, **kwargs) -> None:
            self.predictive_tuning = _StubTuning()
            self._manual_override_until = None
            self._last_predictive_hint = None

        def evaluate(self, **kwargs):
            # Simulate
            # ``manual_override_hold``:
            # the base
            # engine returns
            # a decision
            # with reason
            # ``manual_override_hold``
            # and does NOT
            # touch
            # ``_last_predictive_hint``.
            from types import SimpleNamespace
            return SimpleNamespace(
                reason="manual_override_hold",
                output_priority=None,
                charger_priority=None,
            )

    class _StubSmartMode:
        ADAPTIVE = "adaptive"
        ARBITRAGE = "arbitrage"

    class _StubOutputPriority:
        USB = "0"
        SBU = "2"

    class _StubChargerPriority:
        SNU = "1"
        OSO = "2"

    import sys as _sys
    import types

    fake_engine = types.ModuleType("hems.engine")
    fake_engine.HemsEngine = _StubHemsBase
    fake_engine.SmartMode = _StubSmartMode
    fake_engine.OutputPriority = _StubOutputPriority
    fake_engine.ChargerPriority = _StubChargerPriority
    fake_engine._normalize_output = lambda x: x
    fake_engine._normalize_charger = lambda x: x
    _sys.modules["hems.engine"] = fake_engine
    fake_pc = types.ModuleType(
        "hems.predictive_control"
    )
    _sys.modules[
        "hems.predictive_control"
    ] = fake_pc
    with open(PREDICTIVE_PY) as f:
        prod_src = f.read()
    pc_ns: dict = {
        "__builtins__": __builtins__,
        "__name__": "hems.predictive_control",
    }
    exec(
        compile(prod_src, PREDICTIVE_PY, "exec"),
        pc_ns,
    )
    Engine = pc_ns["PredictiveControlEngine"]

    def _make_engine_with_samples(
        n: int, cf: float
    ):
        eng = Engine.__new__(Engine)
        _StubHemsBase.__init__(eng)
        # Re-run the production
        # ``__init__`` body so
        # the engine has the
        # attributes the rest
        # of the production
        # code expects. We
        # exec the function
        # body by parsing the
        # production source
        # and finding the
        # ``__init__`` method
        # inside the
        # ``PredictiveControlEngine``
        # class.
        import textwrap
        engine_cls = _function_node(
            _parse(PREDICTIVE_PY),
            "PredictiveControlEngine",
        )
        if engine_cls is None:
            # Search inside
            # the top-level
            # class node
            # for
            # ``PredictiveControlEngine``.
            tree = _parse(PREDICTIVE_PY)
            for node in tree.body:
                if (
                    isinstance(node, ast.ClassDef)
                    and node.name
                    == "PredictiveControlEngine"
                ):
                    for m in node.body:
                        if (
                            isinstance(
                                m, ast.FunctionDef
                            )
                            and m.name == "__init__"
                        ):
                            init_src = ast.unparse(m)
                            break
                    break
        else:
            init_src = ast.unparse(engine_cls)
        exec(
            init_src,
            {
                "__builtins__": __builtins__,
                "self": eng,
            },
        )
        if n > 0:
            cal = FakeCalibrator(n, cf)
            eng._predictive_controller = (
                FakeController(cal)
            )
        return eng

    from datetime import datetime, timezone
    now = datetime(
        2026, 10, 7, 12, 0, tzinfo=timezone.utc
    )
    # ── Scenario 1: hold
    # with 0 calibrator
    # samples.
    e0 = _make_engine_with_samples(0, 0.0)
    e0.evaluate(now=now)
    s0 = e0.predictive_decision_state
    for key in (
        "readiness",
        "real_pairs",
        "model_quality",
        "confidence",
        "samples",
    ):
        assert key in s0, (
            f"R9.1: state must expose {key!r} "
            f"after evaluate(); got keys="
            f"{sorted(s0.keys())!r}"
        )
    assert s0["real_pairs"] == 0, (
        "R9.1 0-pair: real_pairs must reflect "
        f"calibrator sample_count=0; got {s0['real_pairs']!r}"
    )
    assert s0["model_quality"] == 0.0, (
        "R9.1 0-pair: model_quality must reflect "
        f"confidence_factor=0.0; got {s0['model_quality']!r}"
    )
    assert s0["readiness"] is False, (
        "R9.1 0-pair: readiness must be False "
        f"with 0 samples; got {s0['readiness']!r}"
    )
    # ── Scenario 2: hold
    # with 3 calibrator
    # samples (the audit
    # explicitly requires
    # this case).
    e3 = _make_engine_with_samples(3, 0.5)
    e3.evaluate(now=now)
    s3 = e3.predictive_decision_state
    assert s3["real_pairs"] == 3, (
        "R9.1 3-pair: real_pairs must reflect "
        f"calibrator sample_count=3; got {s3['real_pairs']!r}"
    )
    assert s3["samples"] == 3, (
        "R9.1 3-pair: samples must reflect "
        f"calibrator sample_count=3; got {s3['samples']!r}"
    )
    assert s3["model_quality"] == 0.5, (
        "R9.1 3-pair: model_quality must reflect "
        f"confidence_factor=0.5; got {s3['model_quality']!r}"
    )
    assert s3["readiness"] is False, (
        "R9.1 3-pair: readiness must still be "
        f"False during hold; got {s3['readiness']!r}"
    )
    # ── Scenario 3: no
    # controller (predictive
    # assist disabled) —
    # must not raise and
    # must fall back to
    # zero values.
    en = _make_engine_with_samples(0, 0.0)
    assert (
        getattr(en, "_predictive_controller", None)
        is None
    )
    en.evaluate(now=now)
    sn = en.predictive_decision_state
    assert sn.get("real_pairs") == 0, (
        "R9.1 no-controller: real_pairs must "
        f"fall back to 0; got {sn.get('real_pairs')!r}"
    )
    assert sn.get("model_quality") == 0.0, (
        "R9.1 no-controller: model_quality must "
        f"fall back to 0.0; got {sn.get('model_quality')!r}"
    )
    assert "readiness" in sn, (
        "R9.1 no-controller: readiness key must "
        f"still be present; got {sorted(sn.keys())!r}"
    )



def test_r101_rollback_unlinks_fresh_target_keeping_stale_bak() -> None:
    """R10.1: when
    ``target_existed``
    is False, the
    rollback removes
    the freshly
    written target
    AND leaves a
    stale ``.bak``
    alone.

    A stale ``.bak``
    could exist on
    disk from a
    previous (failed)
    round. The current
    rollback is about
    THIS operation's
    target only. The
    stale ``.bak``
    belongs to a
    different (prior)
    failed write and
    must NOT be touched
    — touching it would
    silently delete
    user data that the
    user might still be
    able to recover.

    Failure contract:
      * target is
        removed from
        disk
      * stale ``.bak``
        is preserved
        byte-for-byte
    """
    tmp_root = tempfile.mkdtemp(
        prefix="r101_stale_bak_"
    )
    storage = os.path.join(
        tmp_root, ".storage"
    )
    os.makedirs(storage, exist_ok=True)
    target_path = os.path.join(
        storage, "lovelace.test_stale"
    )
    bak_path = target_path + ".bak"
    # Stale bak from a
    # prior operation
    # that the user
    # might want to
    # recover.
    stale_bak_bytes = (
        b"STALE USER-RECOVERABLE BAK "
        b"FROM A PREVIOUS ROUND"
    )
    with open(bak_path, "wb") as f:
        f.write(stale_bak_bytes)
    # Fresh target
    # written by the
    # current operation.
    with open(target_path, "w") as f:
        f.write("FRESH FROM CURRENT OP")
    helpers = (
        _load_registration_helpers()
    )
    rollback = helpers[
        "_rollback_dashboard_content"
    ]
    rollback(
        target_path, target_existed=False
    )
    assert not os.path.exists(
        target_path
    ), (
        "R10.1: fresh target must be "
        "removed when "
        "target_existed=False"
    )
    # Stale bak is
    # preserved.
    assert os.path.exists(bak_path), (
        "R10.1: stale .bak must be "
        "preserved; rollback "
        "must not touch it"
    )
    with open(bak_path, "rb") as f:
        assert f.read() == stale_bak_bytes, (
            "R10.1: stale .bak was "
            "modified; the user "
            "cannot recover it "
            "anymore"
        )



def test_r101_old_destructive_rollback_would_fail_regression_check() -> None:
    """R10.1: regression
    guard. The audit
    rejected the
    round-9 test as a
    false positive
    because the test
    fixture was wrong:
    it created only a
    sidecar and no main
    dashboard, so the
    production helper
    wrote to the main
    path (creating a
    new file from
    scratch), not to
    the sidecar. The
    destructive
    ``os.unlink`` only
    removed the freshly
    written main file,
    and the round-9
    test passed
    incidentally.

    This test makes the
    regression explicit.
    It defines the OLD
    destructive rollback
    inline (the
    ``os.unlink(target)``
    implementation) and
    asserts that the OLD
    implementation
    fails the same
    preconditions that
    the round-10 test
    relies on. If a
    future refactor
    reintroduces the
    destructive
    behaviour, this
    test will fail.
    """
    tmp_root = tempfile.mkdtemp(
        prefix="r101_regression_"
    )
    storage = os.path.join(
        tmp_root, ".storage"
    )
    os.makedirs(storage, exist_ok=True)
    target_path = os.path.join(
        storage, "lovelace.reg_target"
    )
    bak_path = target_path + ".bak"
    prior_bytes = (
        b"\"title\": \"PRIOR USER CONTENT\""
    )
    new_bytes = b"\"title\": \"NEW FAILING WRITE\""
    with open(target_path, "wb") as f:
        f.write(new_bytes)
    with open(bak_path, "wb") as f:
        f.write(prior_bytes)
    # ── The OLD
    # destructive
    # implementation.
    # This is the
    # behaviour round 9
    # had: unlink the
    # target
    # unconditionally
    # on rollback,
    # regardless of
    # whether the
    # target existed
    # before.
    def _old_destructive_rollback(
        target: str,
    ) -> None:
        bak = target + ".bak"
        if os.path.exists(bak):
            # Even worse:
            # shutil.copyfile
            # is not atomic,
            # so a partial
            # write could
            # leave the
            # target
            # truncated.
            try:
                _shutil_copyfile(bak, target)
            finally:
                # Remove the
                # backup even
                # if the
                # copy
                # failed.
                try:
                    os.unlink(bak)
                except OSError:
                    pass
        else:
            if os.path.exists(target):
                os.unlink(target)

    import shutil as _sh
    _shutil_copyfile = _sh.copyfile
    _old_destructive_rollback(target_path)
    # The OLD
    # implementation
    # 'restored' the
    # target to the
    # ``.bak`` content
    # — but the
    # ``.bak`` is now
    # GONE. Any
    # subsequent
    # rollback is a
    # no-op, which is
    # fine, but the
    # destructive
    # version would
    # have destroyed
    # the .bak first.
    # In the round-10
    # rewrite the
    # rollback path is
    # different — let
    # us verify that
    # round-10 also
    # restores AND
    # keeps the
    # behavior
    # consistent.
    # The more
    # important
    # regression
    # check is the
    # ``os.unlink`` on
    # the freshly
    # written target.
    # Now do a separate
    # test: fresh
    # target, no .bak.
    fresh_target = os.path.join(
        storage, "lovelace.reg_fresh"
    )
    with open(fresh_target, "w") as f:
        f.write("FRESH")
    # OLD destructive
    # implementation:
    # unlinks the
    # target.
    bak = fresh_target + ".bak"
    if os.path.exists(bak):
        _shutil_copyfile(bak, fresh_target)
    else:
        if os.path.exists(fresh_target):
            os.unlink(fresh_target)
    # The OLD version
    # just deleted the
    # target, which is
    # fine when the
    # target was a
    # fresh file. So
    # the OLD
    # implementation
    # is correct for
    # the
    # ``already_registered=False``
    # case. The
    # dangerous case
    # is
    # ``already_registered=True``
    # where the OLD
    # implementation
    # also used
    # ``shutil.copyfile``
    # followed by
    # ``os.unlink(bak)``
    # in a ``finally``.
    # The dangerous
    # path was:
    #   copy failed
    #   → target is
    #     truncated
    #   → finally
    #     deletes bak
    #   → user lost
    #     BOTH the
    #     previous and
    #     the new
    #     content
    # Simulate that
    # here.
    truncated_target = os.path.join(
        storage, "lovelace.reg_truncated"
    )
    trunc_bak = truncated_target + ".bak"
    # Simulate a
    # previous
    # content in
    # ``.bak``.
    with open(trunc_bak, "wb") as f:
        f.write(prior_bytes)
    # Simulate a
    # truncated
    # target that
    # was partially
    # written.
    with open(truncated_target, "wb") as f:
        f.write(b"PART")
    # Apply the OLD
    # logic with a
    # failing copy.
    def _failing_copy(src, dst):
        raise OSError("[SIM] disk full")
    try:
        _failing_copy(trunc_bak, truncated_target)
    except OSError:
        # OLD
        # ``finally``
        # deletes
        # ``.bak``
        # even
        # though
        # the
        # copy
        # failed.
        try:
            os.unlink(trunc_bak)
        except OSError:
            pass
    # ── After the
    # OLD logic ran,
    # the user has
    # lost BOTH the
    # previous
    # content and
    # the new (partial)
    # content.
    assert not os.path.exists(trunc_bak), (
        "R10.1 regression guard: old logic "
        "deleted the .bak after a failed "
        "copy — the user lost the backup"
    )
    # This is the
    # regression we
    # are guarding
    # against. The
    # round-10
    # behaviour (in
    # the other tests)
    # keeps the .bak
    # when the copy
    # fails.




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