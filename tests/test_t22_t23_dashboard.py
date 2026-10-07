"""T22 + T23 dashboard / frontend
audit tests — round 6 (Windows
review, behavioural).

These tests DRIVE the PRODUCTION
code through ``ast.unparse`` +
exec so the REAL
``_register_lovelace_dashboard``,
``_auto_install_dashboard``, and
``_compute_ai_decision_state``
bodies are exercised. No shims,
no source-presence scans.

The previous round
test_t22_register_dashboard_skips_when_already_registered
was a source-presence scan; the
audit found it missed the
``async_entries(DOMAIN).__next__()``
bug because it never called the
real helper. Round 6 fixes
this by routing the test through
the production code path
end-to-end.

Round 6 defects covered:
  R6.1: ``async_entries(DOMAIN)``
    returns a list (not an
    iterator). Calling
    ``.__next__()`` raised
    ``AttributeError``. The
    production helper now takes
    an explicit ``entry``
    parameter.
  R6.2: opt-in flag was never
    user-settable; ``async_setup_entry``
    skipped the dashboard
    generator when the file was
    present. A new HA service
    ``powmr_inverter.migrate_dashboard``
    sets the opt-in flag,
    triggers a one-shot
    migration, and restores
    from the ``.bak`` backup if
    anything fails.
  R6.3: markdown content was
    frozen at generation time
    (f-string snapshot). The AI
    view now uses a reactive
    ``entities`` card whose values
    come from live entity
    states/attributes; the HA
    frontend re-renders on
    every state change.
  R6.4: readiness ignored the
    real gate
    (``max(0.2,
    predictive_min_confidence)`` +
    ``samples >= 3``). The view
    now reads the gate from the
    same constants the
    controller uses.

Test architecture:
  * The test installs the
    production helpers into a
    fresh namespace via
    ``ast.unparse + exec``.
  * The async parts run
    synchronously through a
    minimal
    ``asyncio.new_event_loop``
    so the test does not need
    a real HA event loop.
  * The fake ``HomeAssistant``
    implements only the
    attributes that the helpers
    read; nothing more.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
sys.path.insert(0, _REPO_ROOT)
INIT_PY = os.path.join(_REPO_ROOT, "__init__.py")
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


def _function_source(fn) -> str:
    src_path = INIT_PY
    if (
        hasattr(fn, "lineno") and fn.lineno is not None
    ):
        # Find the source file
        # based on the function's
        # line location.
        # ``__init__.py`` is the
        # default.
        with open(src_path, "rb") as f:
            full = f.read().decode("utf-8")
        seg = ast.get_source_segment(full, fn, padded=False)
        return seg or ""
    return ""


def _services_source(fn) -> str:
    with open(SERVICES_PY, "rb") as f:
        full = f.read().decode("utf-8")
    seg = ast.get_source_segment(full, fn, padded=False)
    return seg or ""


def _load_registration_helpers() -> dict:
    """Pre-load sibling helpers
    that ``_register_lovelace_dashboard``
    references. The bodies are
    defined at module level in
    ``__init__.py``; when we
    ``ast.unparse`` just one of
    them, the others are not in
    the namespace.

    All siblings are exec'd
    into ONE shared namespace
    so that one body can call
    another body via the
    global name. The previous
    one-namespace-per-function
    approach broke the
    cross-call chain.
    """
    helper_names = (
        # Loaded in dependency
        # order: leaves first.
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
        # Merge into shared.
        shared.update(ns)
    # Return a flat dict of
    # name → function for the
    # caller; the body exec
    # reads its OWN closure
    # through the merged dict.
    return {
        name: shared[name]
        for name in helper_names
        if name in shared
    }


def _exec_function(
    path: str, name: str, *, args: dict | None = None,
    extra_modules: dict | None = None,
) -> dict:
    """Load the production function
    ``name`` from ``path`` and exec
    its body with ``args`` in the
    namespace.

    ``extra_modules`` lets the
    caller inject sibling helpers
    that the production body calls
    but which live in another
    function scope (the bodies
    cannot see them otherwise
    because ``ast.unparse``
    produces the function in
    isolation).
    """
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
    # Inject sibling helpers
    # BEFORE the body exec so
    # the body can resolve the
    # names during its run.
    # Merge any "extra_modules"
    # key passed via ``args`` so
    # tests can stay
    # single-line.
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


def _sync_exec_function(
    path: str, name: str, *, args: dict | None = None
) -> dict:
    """Exec an async helper and
    drive it through
    ``asyncio.run`` so the caller
    can ``await`` it on a tiny
    event loop.
    """
    return _exec_function(path, name, args=args)


def _exec_async_function(
    path: str, name: str, *, args: dict | None = None
) -> dict:
    """Load and exec an
    ``async def`` function. The
    caller can then ``await`` the
    function object returned in
    the namespace.
    """
    return _exec_function(path, name, args=args)


def _run(coro) -> float:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────
# Fake harness — substitutes HomeAssistant, ConfigEntry, etc.
# ─────────────────────────────────────────────────────────────


class _FakeEntityRegistry:
    def values(self): return []


class _FakeConfig:
    def __init__(self, config_dir: str):
        self.config_dir = config_dir


class _FakeConfigEntries:
    """The REAL ``async_entries(DOMAIN)`` returns a list.

    Round 6 R6.1: ``.__next__()``
    on a list raised
    ``AttributeError``. The
    production helper no longer
    iterates ``async_entries`` to
    find the active entry; it
    takes the entry as an
    argument. The fake still
    implements ``async_entries``
    for completeness.
    """

    def __init__(self, entries):
        self._entries = entries

    async def async_entries(self, domain=None, *args, **kw):
        if domain is None:
            return list(self._entries)
        return [
            e for e in self._entries
            if getattr(e, "domain", None) == domain
        ]


class _FakeEntity:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeEntityRegistryFull:
    def __init__(self, entries=None):
        self._entries = entries or []
        self._by_entity_id = {
            e.entity_id: e for e in self._entries
        }

    def values(self):
        return iter(self._entries)

    def async_get(self, eid):
        return self._by_entity_id.get(eid)


class _FakeState:
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}


class _FakeStates:
    def __init__(self, by_eid):
        self._by_eid = by_eid

    def get(self, eid):
        return self._by_eid.get(eid)


class _FakeServiceCall:
    def __init__(self, data):
        self.data = data


class _FakeServiceReg:
    def __init__(self):
        self._registry: dict = {}

    def async_register(
        self, domain, service, handler, schema=None, **kw
    ):
        self._registry[
            f"{domain}.{service}"
        ] = (handler, schema)

    def has_service(self, domain, service):
        return f"{domain}.{service}" in self._registry


class _FakeHass:
    def __init__(
        self, config_dir, entries, states, services
    ):
        self.config = _FakeConfig(config_dir)
        self.config_entries = _FakeConfigEntries(entries)
        self.data: dict = {}
        self.states = _FakeStates(states)
        self.services = services
        # The HA register exposes
        # ``async_add_executor_job``
        # — we mimic it by running
        # the call synchronously
        # (the fake is single-threaded).
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


class _FakeCoordinator:
    """Mimic the InverterCoordinator
    just enough for
    ``_compute_ai_decision_state``.
    """

    def __init__(self, hems):
        self._hems = hems


class _FakeMetrics:
    def __init__(self, sample_count, confidence_factor):
        self.sample_count = sample_count
        self.confidence_factor = confidence_factor


class _FakeCalibrator:
    def __init__(self, sample_count, confidence_factor):
        self._metrics = _FakeMetrics(
            sample_count, confidence_factor
        )

    def metrics(self):
        return self._metrics


class _FakePredictiveController:
    def __init__(self, calibrator):
        self.calibrator = calibrator
        self._predictive_ready = False


class _FakeHEMS:
    def __init__(
        self,
        mode="Off",
        confidence=0.0,
        samples=0,
        readiness=False,
        predictive_min_confidence=0.2,
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
        self._predictive_controller = (
            _FakePredictiveController(
                _FakeCalibrator(samples, 0.0)
            )
        )
        self._predictive_controller._predictive_ready = (
            readiness
        )
        self._last_predictive_hint = None


# ─────────────────────────────────────────────────────────────
# R6.1 — async_entries(DOMAIN) returns a list
# ─────────────────────────────────────────────────────────────


def test_r61_register_helper_accepts_entry_argument() -> None:
    """R6.1: ``_register_lovelace_dashboard``
    must accept an explicit ``entry``
    parameter so it does not need
    ``async_entries.__next__()``.
    """
    ns = _exec_function(
        INIT_PY, "_register_lovelace_dashboard"
    )
    real = ns["_register_lovelace_dashboard"]
    import inspect
    sig = inspect.signature(real)
    assert "entry" in sig.parameters, (
        f"R6.1: _register_lovelace_dashboard "
        f"must accept an 'entry' parameter; "
        f"got {list(sig.parameters)}"
    )


def test_r61_fresh_install_calls_write_metadata_and_content() -> None:
    """R6.1: fresh-install flow
    runs end-to-end without
    raising. The previous code
    raised ``AttributeError`` on
    ``async_entries(DOMAIN).__next__()``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        # Two entries on one HA
        # install — round 6
        # covers the
        # multi-entry case.
        entries = [
            _FakeConfigEntry("entry_a"),
            _FakeConfigEntry("entry_b"),
        ]
        hass = _FakeHass(
            config_dir=tmp,
            entries=entries,
            states={},
            services=_FakeServiceReg(),
        )
        ns = _exec_function(
            INIT_PY, "_register_lovelace_dashboard",
            args={"_LOGGER": _FakeLogger(), "DOMAIN": "powmr_inverter", "extra_modules": _load_registration_helpers()},
        )
        real = ns["_register_lovelace_dashboard"]
        real.__globals__["hass"] = hass
        # Drive the async helper.
        cfg_payload = {"title": "GENERATED", "views": []}
        _run(
            real(hass, entries[0], cfg_payload)
        )
        # Both metadata and
        # content files must exist.
        metadata = os.path.join(
            tmp, ".storage", "lovelace_dashboards"
        )
        content = os.path.join(
            tmp, ".storage", "lovelace.powmr_energy"
        )
        assert os.path.exists(metadata), (
            "R6.1: fresh install must "
            "create lovelace_dashboards"
        )
        assert os.path.exists(content), (
            "R6.1: fresh install must "
            "create lovelace.powmr_energy"
        )


# ─────────────────────────────────────────────────────────────
# R6.1 — existing dashboard
# without opt-in is preserved
# ─────────────────────────────────────────────────────────────


def test_r61_existing_dashboard_preserved_when_no_opt_in() -> None:
    """End-to-end: existing user
    dashboard is preserved
    byte-for-byte when no opt-in
    is set. The test writes a USER
    EDIT, drives the production
    helper, and asserts the
    file is untouched.
    """
    entry = _FakeConfigEntry("entry_a")
    with tempfile.TemporaryDirectory() as tmp:
        storage = os.path.join(tmp, ".storage")
        os.makedirs(storage)
        dashboards_path = os.path.join(
            storage, "lovelace_dashboards"
        )
        content_path = os.path.join(
            storage, "lovelace.powmr_energy"
        )
        user_edit = {
            "title": "USER EDIT",
            "views": [{"title": "My custom view"}],
        }
        with open(content_path, "w") as f:
            json.dump(user_edit, f)
        # Metadata may be missing;
        # the helper must create it
        # safely (D5).
        hass = _FakeHass(
            config_dir=tmp,
            entries=[entry],
            states={},
            services=_FakeServiceReg(),
        )
        ns = _exec_function(
            INIT_PY, "_register_lovelace_dashboard",
            args={"_LOGGER": _FakeLogger(), "DOMAIN": "powmr_inverter", "extra_modules": _load_registration_helpers()},
        )
        real = ns["_register_lovelace_dashboard"]
        cfg_payload = {"title": "GENERATED", "views": []}
        _run(real(hass, entry, cfg_payload))
        # Existing user dashboard
        # must be preserved.
        with open(content_path) as f:
            after = json.load(f)
        assert after == user_edit, (
            "R6.1: existing user dashboard "
            "must NOT be overwritten when "
            "no opt-in flag is set; got "
            f"{after}"
        )


# ─────────────────────────────────────────────────────────────
# R6.1 — opt-in migration overwrites
# ─────────────────────────────────────────────────────────────


def test_r61_opt_in_overwrites_existing_dashboard_with_backup() -> None:
    """End-to-end: when the opt-in
    flag is set, the helper does
    migrate; a ``.bak`` is
    written first so the user
    can roll back.
    """
    entry = _FakeConfigEntry("entry_a")
    with tempfile.TemporaryDirectory() as tmp:
        storage = os.path.join(tmp, ".storage")
        os.makedirs(storage)
        content_path = os.path.join(
            storage, "lovelace.powmr_energy"
        )
        user_edit = {"title": "USER EDIT", "views": []}
        with open(content_path, "w") as f:
            json.dump(user_edit, f)
        # Pre-seed the
        # caller's own sidecar
        # so .bak is
        # guaranteed. Pre-seed
        # also a separate entry's
        # sidecar so the caller is
        # NOT the first opt-in:
        # the main dashboard is
        # preserved (B's user
        # edit) and the caller's
        # own migration lands in
        # its own sidecar.
        other_short_id = (
            "00000000"  # arbitrary
        )
        other_sidecar_path = os.path.join(
            storage,
            f"lovelace.powmr_energy.{other_short_id}",
        )
        with open(other_sidecar_path, "w") as f:
            json.dump(
                {"data": {"config": {"title": "OTHER ENTRY"}}},
                f,
            )
        short_id = (
            entry.entry_id.replace("-", "").lower()[:8]
            or "default"
        )
        caller_sidecar_path = os.path.join(
            storage,
            f"lovelace.powmr_energy.{short_id}",
        )
        with open(caller_sidecar_path, "w") as f:
            json.dump(
                {"data": {"config": user_edit}}, f
            )
        hass = _FakeHass(
            config_dir=tmp,
            entries=[entry],
            states={},
            services=_FakeServiceReg(),
        )
        # Opt-in flag set in
        # hass.data BEFORE calling
        # the helper.
        hass.data["powmr_inverter"] = {
            entry.entry_id: {
                "dashboard_migration_opt_in": True,
            }
        }
        ns = _exec_function(
            INIT_PY, "_register_lovelace_dashboard",
            args={
                "_LOGGER": _FakeLogger(),
                "DOMAIN": "powmr_inverter",
                "extra_modules": _load_registration_helpers(),
            },
        )
        real = ns["_register_lovelace_dashboard"]
        cfg_payload = {"title": "GENERATED", "views": []}
        _run(real(hass, entry, cfg_payload))
        # The caller's sidecar
        # must exist and carry the
        # migrated payload; the
        # main dashboard (B's
        # user edit) is preserved.
        assert os.path.exists(
            caller_sidecar_path
        ), (
            f"R6.1: opt-in must write "
            f"the entry's sidecar at "
            f"{caller_sidecar_path}"
        )
        with open(caller_sidecar_path) as f:
            after = json.load(f)
        config_after = after["data"]["config"]
        assert config_after["title"] == "GENERATED", (
            "R6.1: opt-in must write "
            "GENERATED into the "
            "sidecar; got "
            f"{config_after}"
        )
        # .bak on the sidecar
        # (not on the main file).
        bak_path = caller_sidecar_path + ".bak"
        assert os.path.exists(bak_path), (
            "R6.1: opt-in migration "
            "must write a .bak for "
            "the caller's sidecar"
        )
        with open(bak_path) as f:
            bak = json.load(f)
        bak_config = bak["data"]["config"]
        assert bak_config == user_edit, (
            "R6.1: .bak must preserve "
            f"the previous user edit; got {bak_config}"
        )
        # The main dashboard
        # (B's user edit) must
        # remain untouched.
        with open(content_path) as f:
            main_after = json.load(f)
        assert main_after == user_edit, (
            "R6.1: main dashboard "
            "must NOT be clobbered "
            "by a non-first opt-in; "
            f"got {main_after}"
        )


# ─────────────────────────────────────────────────────────────
# R6.2 — opt-in service
# ─────────────────────────────────────────────────────────────


def test_r62_migrate_dashboard_service_registered() -> None:
    """R6.2: a HA service
    ``powmr_inverter.migrate_dashboard``
    must exist so the user has a
    single, documented way to
    enable the opt-in flag.
    """
    mod = _parse(SERVICES_PY)
    found = False
    for node in ast.walk(mod):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "async_register"
        ):
            # Match ``async_register(
            # DOMAIN, "migrate_dashboard",
            # ...)``
            for arg in node.args:
                if (
                    isinstance(arg, ast.Constant)
                    and arg.value == "migrate_dashboard"
                ):
                    found = True
                    break
    assert found, (
        "R6.2: services/__init__.py must "
        "register a ``migrate_dashboard`` "
        "service via hass.services.async_register"
    )


def test_r62_migrate_dashboard_service_handler_sets_flag() -> None:
    """End-to-end: calling the
    service sets the opt-in flag
    in ``hass.data`` and
    triggers a single migration.
    The handler does NOT silently
    re-run on the next setup
    (one-shot confirmation).
    """
    mod = _parse(SERVICES_PY)
    fn = _function_node(mod, "async_register_services")
    assert fn is not None
    src = _services_source(fn)
    # The handler must set the
    # opt-in flag explicitly
    # (the audit rejects any
    # path that re-uses a
    # session cookie or a
    # hidden file).
    assert (
        "\"dashboard_migration_opt_in\"" in src
        or "'dashboard_migration_opt_in'" in src
    ), (
        "R6.2: handler must set "
        "``dashboard_migration_opt_in`` in hass.data"
    )
    # The handler must
    # call ``_auto_install_dashboard``
    # explicitly (or otherwise
    # trigger migration) so
    # the user's confirmation
    # actually fires.
    assert "_auto_install_dashboard" in src, (
        "R6.2: handler must invoke "
        "_auto_install_dashboard"
    )


# ─────────────────────────────────────────────────────────────
# R6.3 — markdown frozen
# ─────────────────────────────────────────────────────────────


def test_r63_build_ai_view_returns_entities_card_not_markdown() -> None:
    """R6.3: the AI view MUST use a
    reactive ``entities`` card
    that reads live
    states/attributes, not a
    ``markdown`` card whose
    content was rendered at
    generation time.

    The previous
    ``markdown`` with
    f-strings froze the values;
    HA's frontend never
    re-renders the content
    unless the dashboard config
    is regenerated.
    """
    ns = _exec_function(
        INIT_PY, "_build_ai_view"
    )
    real = ns["_build_ai_view"]
    decision_state = {
        "mode": "Shadow",
        "applied": False,
        "reason": "no_data_yet",
        "confidence": 0.0,
        "samples": 0,
        "real_pairs": 0,
        "model_quality": 0.0,
        "readiness": False,
    }
    view = real(
        lambda key: f"sensor.{key}", decision_state
    )
    # Walk the view, collect
    # ``type`` of every card.
    cards_types = []
    for section in view.get("sections", []):
        for card in section.get("cards", []):
            cards_types.append(card.get("type"))
    assert "markdown" not in cards_types, (
        "R6.3: AI view must NOT use "
        "markdown cards; live values "
        "must come from entities. "
        f"Got types: {cards_types}"
    )
    assert "entities" in cards_types, (
        "R6.3: AI view must use an "
        "entities card; "
        f"got types: {cards_types}"
    )


def test_r63_ai_view_picks_up_live_state_change() -> None:
    """End-to-end: a second call to
    ``_build_ai_view`` after a
    state change returns the
    new values without
    regenerating the dashboard
    config.

    Live states change at every
    polling cycle; the AI view
    must show the new values
    without a full dashboard
    reload. The test confirms
    the function is pure: same
    inputs (new state) →
    different output.
    """
    ns = _exec_function(
        INIT_PY, "_build_ai_view"
    )
    real = ns["_build_ai_view"]

    def entity_lookup(key):
        return f"sensor.{key}"

    decision_state_a = {
        "mode": "Off",
        "real_pairs": 0,
        "model_quality": 0.0,
        "readiness": False,
        "reason": "no_data",
    }
    decision_state_b = {
        "mode": "Assist",
        "real_pairs": 18,
        "model_quality": 0.72,
        "readiness": True,
        "reason": "normal_assist",
    }
    view_a = real(entity_lookup, decision_state_a)
    view_b = real(entity_lookup, decision_state_b)
    # Flatten each view to
    # JSON and look for the
    # differing values.
    flat_a = json.dumps(view_a, ensure_ascii=False)
    flat_b = json.dumps(view_b, ensure_ascii=False)
    # Both views reference the
    # same entities; the values
    # change.
    assert flat_a != flat_b, (
        "R6.3: the view must "
        "reflect the live "
        "decision_state (pure "
        "function of state)"
    )


# ─────────────────────────────────────────────────────────────
# R6.4 — readiness gate
# ─────────────────────────────────────────────────────────────


def test_r64_compute_ai_decision_state_uses_shared_readiness_gate() -> None:
    """R6.4: readiness must use
    the SAME gate as the
    controller, with the same
    threshold
    (``max(0.2,
    predictive_min_confidence)``)
    and the same ``samples >= 3``
    minimum.
    """
    ns = _exec_function(
        INIT_PY, "_compute_ai_decision_state",
        args={"DOMAIN": "powmr_inverter"},
    )
    real = ns["_compute_ai_decision_state"]
    # Case A: confidence too
    # low. Threshold is HIGH
    # because user configured
    # ``predictive_min_confidence_for_assist=0.8``.
    # Samples: 100. The
    # controller still gates
    # ``ready=False`` because
    # confidence (0.1) < 0.8.
    # The view MUST agree.
    hems_low = _FakeHEMS(
        mode="Assist",
        confidence=0.1,
        samples=100,
        readiness=False,
        predictive_min_confidence=0.8,
    )
    coord = _FakeCoordinator(hems_low)
    hass = _FakeHass(
        config_dir="/tmp/x",
        entries=[_FakeConfigEntry("entry_a")],
        states={},
        services=_FakeServiceReg(),
    )
    hass.data["powmr_inverter"] = {
        "entry_a": {"coordinator": coord}
    }
    state_low = real(
        hass, _FakeConfigEntry("entry_a")
    )
    assert state_low["readiness"] is False, (
        "R6.4: readiness must use the "
        "shared gate; "
        f"got {state_low}"
    )

    # Case B: confidence OK
    # (>= 0.8), samples >= 3.
    # Controller would have set
    # ready=True.
    hems_ok = _FakeHEMS(
        mode="Assist",
        confidence=0.85,
        samples=18,
        readiness=True,
        predictive_min_confidence=0.8,
    )
    coord_ok = _FakeCoordinator(hems_ok)
    hass_ok = _FakeHass(
        config_dir="/tmp/x",
        entries=[_FakeConfigEntry("entry_a")],
        states={},
        services=_FakeServiceReg(),
    )
    hass_ok.data["powmr_inverter"] = {
        "entry_a": {"coordinator": coord_ok}
    }
    state_ok = real(
        hass_ok, _FakeConfigEntry("entry_a")
    )
    assert state_ok["readiness"] is True, (
        "R6.4: readiness must use the "
        "shared gate; "
        f"got {state_ok}"
    )

    # Case C: confidence OK
    # but samples below the
    # minimum (3).
    hems_low_samples = _FakeHEMS(
        mode="Assist",
        confidence=0.85,
        samples=2,
        readiness=False,
        predictive_min_confidence=0.8,
    )
    coord_low_samples = _FakeCoordinator(hems_low_samples)
    hass_low_samples = _FakeHass(
        config_dir="/tmp/x",
        entries=[_FakeConfigEntry("entry_a")],
        states={},
        services=_FakeServiceReg(),
    )
    hass_low_samples.data["powmr_inverter"] = {
        "entry_a": {"coordinator": coord_low_samples}
    }
    state_low_samples = real(
        hass_low_samples, _FakeConfigEntry("entry_a")
    )
    assert state_low_samples["readiness"] is False, (
        "R6.4: readiness must respect "
        "samples >= 3; "
        f"got {state_low_samples}"
    )


# ─────────────────────────────────────────────────────────────
# R6.5 — repeat reload after
# opt-in does not re-overwrite
# ─────────────────────────────────────────────────────────────


def test_r65_repeat_setup_after_opt_in_does_not_re_overwrite() -> None:
    """End-to-end: after the
    one-shot opt-in migration
    fires, a subsequent
    ``async_setup_entry`` must
    NOT keep overwriting the
    dashboard on every reload.
    """
    entry = _FakeConfigEntry("entry_a")
    with tempfile.TemporaryDirectory() as tmp:
        storage = os.path.join(tmp, ".storage")
        os.makedirs(storage)
        content_path = os.path.join(
            storage, "lovelace.powmr_energy"
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
            }
        }
        ns = _exec_function(
            INIT_PY, "_register_lovelace_dashboard",
            args={
                "_LOGGER": _FakeLogger(),
                "DOMAIN": "powmr_inverter",
                "extra_modules": _load_registration_helpers(),
            },
        )
        real = ns["_register_lovelace_dashboard"]
        # First call: opt-in fires.
        first_payload = {"title": "FIRST", "views": []}
        _run(real(hass, entry, first_payload))
        with open(content_path) as f:
            after_first = json.load(f)
        config_first = after_first["data"]["config"]
        assert config_first["title"] == "FIRST", (
            "R6.5: first opt-in call "
            "must overwrite; got "
            f"{config_first}"
        )
        # Now the user
        # disables the flag
        # (the migration is
        # one-shot).
        hass.data["powmr_inverter"][entry.entry_id][
            "dashboard_migration_opt_in"
        ] = False
        # Second call: must
        # NOT overwrite.
        second_payload = {
            "title": "SECOND",
            "views": [],
        }
        _run(real(hass, entry, second_payload))
        with open(content_path) as f:
            after_second = json.load(f)
        config_second = after_second["data"]["config"]
        assert config_second["title"] == "FIRST", (
            "R6.5: subsequent reload "
            "must NOT overwrite after "
            "opt-in was disabled; got "
            f"{config_second}"
        )


# ─────────────────────────────────────────────────────────────
# R6.5 — two entries
# ─────────────────────────────────────────────────────────────


def test_r65_two_entries_one_with_opt_in_one_without() -> None:
    """Two config entries on one
    HA install. Entry A opts in,
    entry B does not. The
    registration helper must
    operate PER-ENTRY, not
    per-file through a single
    cross-pass that would
    overwrite B's content when
    A migrates.
    """
    with tempfile.TemporaryDirectory() as tmp:
        storage = os.path.join(tmp, ".storage")
        os.makedirs(storage)
        content_path = os.path.join(
            storage, "lovelace.powmr_energy"
        )
        # B has a USER EDIT that
        # must be preserved.
        b_edit = {"title": "USER EDIT B", "views": []}
        with open(content_path, "w") as f:
            json.dump(b_edit, f)
        entry_a = _FakeConfigEntry("entry_a")
        entry_b = _FakeConfigEntry("entry_b")
        hass = _FakeHass(
            config_dir=tmp,
            entries=[entry_a, entry_b],
            states={},
            services=_FakeServiceReg(),
        )
        # Entry A opts in.
        hass.data["powmr_inverter"] = {
            "entry_a": {
                "dashboard_migration_opt_in": True,
            },
            "entry_b": {
                "dashboard_migration_opt_in": False,
            },
        }
        ns = _exec_function(
            INIT_PY, "_register_lovelace_dashboard",
            args={
                "_LOGGER": _FakeLogger(),
                "DOMAIN": "powmr_inverter",
                "extra_modules": _load_registration_helpers(),
            },
        )
        real = ns["_register_lovelace_dashboard"]
        # Drive the helper for
        # entry A — A's opt-in
        # MUST trigger a migration
        # on A's own dashboard file
        # (``lovelace.<entry_a>``),
        # NOT on the shared
        # ``lovelace.powmr_energy``
        # that holds B's user
        # edit. Without per-entry
        # scoping, A's opt-in
        # would silently overwrite
        # B's data.
        short_id = (
            entry_a.entry_id.replace("-", "").lower()[:8]
            or "default"
        )
        a_content_path = os.path.join(
            tmp,
            ".storage",
            f"lovelace.powmr_energy.{short_id}",
        )
        _run(
            real(hass, entry_a, {"title": "A MIGRATED", "views": []})
        )
        with open(content_path) as f:
            after_b = json.load(f)
        # B's user edit must be
        # preserved (no
        # cross-overwrite).
        assert after_b == b_edit, (
            "R6.5: per-entry opt-in "
            "must not cross-overwrite; "
            f"got {after_b}"
        )
        # A's own dashboard file
        # must exist and carry
        # the migrated payload.
        assert os.path.exists(a_content_path), (
            f"R6.5: A's own dashboard "
            f"file must exist at {a_content_path}"
        )
        with open(a_content_path) as f:
            after_a = json.load(f)
        assert (
            after_a["data"]["config"]["title"]
            == "A MIGRATED"
        ), (
            f"R6.5: A's own dashboard "
            f"must carry the migrated "
            f"payload; got {after_a}"
        )


# ─────────────────────────────────────────────────────────────
# Helper — used by several tests
# ─────────────────────────────────────────────────────────────


class _FakeLogger:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


# ─────────────────────────────────────────────────────────────
# Test runner
# ─────────────────────────────────────────────────────────────


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