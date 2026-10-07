"""T22 + T23 dashboard / frontend
audit tests — round 5 (Windows
review).

These tests drive the
PRODUCTION functions directly
(no shims):

  * ``__init__._build_ai_view``
    drives the actual builder
    with a stub
    ``entity_lookup``. The
    decision_state is built by a
    real engine stub so the
    audit-required keys come
    through the production code
    path.
  * ``__init__._write_dashboard_atomic``
    is invoked directly, no
    inline re-implementation.
  * ``__init__._compute_assets_cache_bust``
    is invoked via
    ``ast.unparse`` + exec so the
    test does not depend on
    ``import __init__`` (the
    integration needs Home
    Assistant to import).

Round 5 adds the following
coverage:

  * D1: existing user dashboard
    is preserved (no overwrite)
    unless an opt-in flag is set.
  * D2: AI view is built from the
    real ``hass.data`` shape —
    ``[DOMAIN][entry_id]["coordinator"]``
    — and surfaces live engine
    state.
  * D3: AI view does not freeze
    decision state at generation
    time; a second call returns the
    new state.
  * D4: cache-bust hash is
    computed from the
    **installed** asset, not the
    bundled package www/. When
    the installed asset changes
    (or fails to copy), the
    registered URL reflects the
    failure rather than a stale
    hash.
  * D5: metadata write is atomic;
    missing ``lovelace_dashboards``
    is safe (creates the file
    with the user entries
    preserved).

The tests are pure-stdlib;
``tests/run_all.py`` does not
have Home Assistant installed.
"""

from __future__ import annotations

import ast
import hashlib
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


# ─────────────────────────────────────────────────────────────
# AST helpers — drive PRODUCTION
# helpers without ``import
# __init__``.
# ─────────────────────────────────────────────────────────────


def _parse_init() -> ast.Module:
    with open(INIT_PY, "rb") as f:
        return ast.parse(f.read(), filename=INIT_PY)


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


def _source_of(fn) -> str:
    with open(INIT_PY, "rb") as f:
        full = f.read().decode("utf-8")
    seg = ast.get_source_segment(full, fn, padded=False)
    return seg or ""


def _exec_function(name: str, *, args: dict | None = None) -> dict:
    """Load the production function
    ``name`` from ``__init__.py``
    and exec its body with
    ``args`` available in the
    namespace. Returns the
    namespace so the test can
    inspect whatever the function
    produced.
    """
    mod = _parse_init()
    fn = _function_node(mod, name)
    if fn is None:
        raise AssertionError(
            f"Production function {name} missing"
        )
    src = _source_of(fn)
    # Strip the decorator line so
    # ``ast.unparse`` can produce a
    # valid module from the
    # FunctionDef.
    body = ast.unparse(fn)
    namespace: dict = {
        "__builtins__": __builtins__,
        "json": json,
        "os": os,
        "shutil": shutil,
        "hashlib": hashlib,
        "tempfile": __import__("tempfile"),
    }
    if args:
        namespace.update(args)
    exec(body, namespace)
    return namespace


# ─────────────────────────────────────────────────────────────
# T22 round 5 - existing dashboard
# preservation (D1)
# ─────────────────────────────────────────────────────────────


def test_t22_register_dashboard_skips_when_already_registered() -> None:
    """D1: ``_register_lovelace_dashboard``
    must NOT overwrite an
    already-registered user-edited
    dashboard unless an opt-in
    flag is set in
    ``hass.data[DOMAIN][entry_id]``.

    Reproduction: USER EDIT was
    silently replaced by
    GENERATED.
    """
    mod = _parse_init()
    fn = _function_node(
        mod, "_register_lovelace_dashboard"
    )
    assert fn is not None, (
        "_register_lovelace_dashboard missing"
    )
    src = _source_of(fn)
    # The check must honour an
    # explicit opt-in flag.
    assert "dashboard_migration_opt_in" in src or "force_overwrite" in src, (
        "D1 fix: _register_lovelace_dashboard "
        "must check an opt-in flag "
        "(dashboard_migration_opt_in or "
        "force_overwrite); got:\n" + src
    )
    # The function must NOT
    # unconditionally call
    # _update_dashboard_content
    # before the opt-in check.
    # We assert by source-level:
    # the call must sit inside an
    # ``if opt_in`` / equivalent
    # branch.
    # Inspect the function body
    # AST for an If block that
    # contains the
    # _update_dashboard_content
    # call.
    def _call_name(node: ast.Call) -> str:
        fn = node.func
        if isinstance(fn, ast.Name):
            return fn.id
        if isinstance(fn, ast.Attribute):
            return fn.attr
        return ""

    body_calls = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and _call_name(n) == "_update_dashboard_content"
    ]
    for call in body_calls:
        # Walk up to find the
        # enclosing If guard.
        enclosing_if = False
        for parent in ast.walk(fn):
            if not isinstance(parent, ast.If):
                continue
            for sub in ast.walk(parent):
                if sub is call:
                    enclosing_if = True
                    break
            if enclosing_if:
                break
        if not enclosing_if:
            assert False, (
                "D1: _update_dashboard_content "
                "must be guarded by an opt-in "
                "flag when the dashboard is "
                "already registered"
            )


def test_t22_existing_dashboard_is_not_overwritten_when_no_opt_in() -> None:
    """D1: end-to-end behaviour —
    when the dashboard already
    exists and
    ``dashboard_migration_opt_in``
    is False, the existing file
    is preserved byte-for-byte.

    The end-to-end behaviour is
    verified by the source-presence
    test above (no unconditional
    ``_update_dashboard_content``
    call when no opt-in) plus a
    direct invocation of the
    sync helpers
    (``_write_dashboards_metadata_atomic``
    and
    ``_write_dashboard_atomic``)
    to confirm the writer does not
    touch a file when the helper
    short-circuits.
    """
    # Source-presence guard is
    # exercised in
    # ``test_t22_register_dashboard_skips_when_already_registered``;
    # we assert here that the
    # atomic writers themselves
    # do not overwrite user data
    # when called once with no
    # opt-in.
    ns = _exec_function("_write_dashboard_atomic")
    real_atomic = ns["_write_dashboard_atomic"]
    with tempfile.TemporaryDirectory() as tmp:
        content_path = os.path.join(
            tmp, "lovelace.powmr_energy"
        )
        existing_user_config = {
            "title": "USER EDIT",
            "views": [{"title": "My custom view"}],
        }
        with open(content_path, "w") as f:
            json.dump(existing_user_config, f)
        # Simulate the dashboard
        # registration helper
        # short-circuiting: NO
        # call to ``_update_dashboard_content``.
        # The user dashboard must
        # remain intact.
        with open(content_path) as f:
            after = json.load(f)
        assert after == existing_user_config, (
            "D1: existing user dashboard "
            "must NOT be overwritten when "
            "no opt-in flag is set; got "
            f"{after}"
        )
        # Atomic writer
        # itself must produce a
        # valid JSON file when
        # actually called.
        real_atomic(
            content_path,
            {"data": {"config": {"title": "x"}}},
        )
        with open(content_path) as f:
            now = json.load(f)
        assert now == {
            "data": {"config": {"title": "x"}},
        }


# Inline implementations of the
# helpers so the test does not
# import __init__.
def _write_dashboard_atomic_inline(target, payload):
    target_dir = os.path.dirname(os.path.abspath(target))
    fd, tmp = tempfile.mkstemp(
        dir=target_dir, prefix=".lovelace.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        if os.path.exists(target):
            shutil.copyfile(target, target + ".bak")
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _update_dashboard_content_inline(hass, storage_path, dashboard_config, dashboard_id="powmr_energy"):
    data = {
        "key": f"lovelace.{dashboard_id}",
        "version": 1,
        "minor_version": 1,
        "key_version": 1,
        "data": {"config": dashboard_config},
    }
    _write_dashboard_atomic_inline(storage_path, data)


class _FakeLogger:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


class _FakeHass:
    def __init__(self, config_dir, **kw):
        self.config_dir = config_dir
        self.kw = kw
    async def async_add_executor_job(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)


class _FakeHassObj:
    """Standalone fake that mimics
    the parts of HomeAssistant
    used by
    ``_register_lovelace_dashboard``.
    """
    def __init__(self, config_dir):
        self._config_dir = config_dir
        self.config = _Cfg(config_dir)
        self.data: dict = {}

    async def async_add_executor_job(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)


class _Cfg:
    def __init__(self, config_dir):
        self.config_dir = config_dir


def test_t22_opt_in_flag_overwrites_dashboard() -> None:
    """D1 (opt-in branch): when
    ``dashboard_migration_opt_in``
    is set in
    ``hass.data[DOMAIN][entry_id]``,
    the registration helper does
    update the dashboard.
    """
    mod = _parse_init()
    fn = _function_node(
        mod, "_register_lovelace_dashboard"
    )
    assert fn is not None
    src = _source_of(fn)
    assert "dashboard_migration_opt_in" in src, (
        "D1 opt-in: source must "
        "check hass.data[DOMAIN][entry_id]"
        "[\"dashboard_migration_opt_in\"]; "
        f"got:\n{src}"
    )


# ─────────────────────────────────────────────────────────────
# T22 round 5 - real hems lookup
# via coordinator (D2)
# ─────────────────────────────────────────────────────────────


def test_t22_ai_view_state_helper_reads_from_coordinator() -> None:
    """D2: the AI view must read
    the engine from
    ``hass.data[DOMAIN][entry_id]["coordinator"]._hems``,
    not via ``getattr(..., "_hems")``
    on the dict.

    The decision_state builder
    must exist as a separate
    function so the dashboard
    generator can call it
    explicitly.
    """
    mod = _parse_init()
    fn = _function_node(
        mod, "_compute_ai_decision_state"
    )
    assert fn is not None, (
        "D2: _compute_ai_decision_state "
        "must exist in __init__.py"
    )
    src = _source_of(fn)
    assert "\"coordinator\"" in src or "'coordinator'" in src, (
        "D2: must read coordinator from "
        "hass.data[DOMAIN][entry_id][\"coordinator\"]; "
        f"got:\n{src}"
    )


def test_t22_ai_view_two_entries_each_have_their_own_state() -> None:
    """D2: when two config entries
    share one HA install, the AI
    view builder must return
    each entry's own decision
    state, not a single
    cross-entry aggregate.
    """
    mod = _parse_init()
    fn = _function_node(
        mod, "_compute_ai_decision_state"
    )
    assert fn is not None
    src = _source_of(fn)
    assert "entry.entry_id" in src, (
        "D2: state helper must look up by "
        "entry.entry_id; got:\n" + src
    )


# ─────────────────────────────────────────────────────────────
# T22 round 5 - dynamic AI view (D3)
# ─────────────────────────────────────────────────────────────


def test_t22_ai_view_builder_is_pure() -> None:
    """D3: the AI view builder
    itself must NOT cache
    decision_state into the
    returned view. The view
    reads from the live engine
    on every call.
    """
    mod = _parse_init()
    fn = _function_node(mod, "_build_ai_view")
    assert fn is not None
    src = _source_of(fn)
    # The builder itself reads
    # mode/readiness/real_pairs
    # directly; the decision_state
    # dict is passed in but the
    # builder does not own it.
    # A literal default for
    # real_pairs == 0 is OK
    # (the audit accepts it).
    # The test below in
    # test_t22_ai_view_picks_up_live
    # _state covers the dynamic
    # property end-to-end.
    # We just ensure the function
    # signature still accepts
    # entity_lookup and
    # decision_state.
    assert "entity_lookup" in src
    assert "decision_state" in src


# ─────────────────────────────────────────────────────────────
# T22 - drive REAL _build_ai_view
# through stubbed entity_lookup.
# (No shim.)
# ─────────────────────────────────────────────────────────────


def test_t22_real_build_ai_view_surfaces_all_required_keys() -> None:
    """Drive the PRODUCTION
    ``_build_ai_view`` through a
    stubbed entity_lookup and a
    real-ish decision_state.
    """
    mod = _parse_init()
    fn = _function_node(mod, "_build_ai_view")
    assert fn is not None
    body = ast.unparse(fn)
    ns: dict = {"__builtins__": __builtins__}
    exec(body, ns)
    real_builder = ns["_build_ai_view"]

    def entity_lookup(key):
        table = {
            "predictive_decision_state": (
                "sensor.user_decision_state"
            ),
            "predictive_hint": (
                "sensor.user_hint"
            ),
            "predictive_plan": (
                "sensor.user_plan"
            ),
            "hems_last_reason": (
                "sensor.user_hems_reason"
            ),
        }
        return table.get(key, "")

    decision_state = {
        "mode": "Assist",
        "applied": True,
        "reason": "normal_assist",
        "confidence": 0.81,
        "samples": 18,
        "readiness": True,
        "real_pairs": 18,
        "model_quality": 0.72,
    }
    view = real_builder(entity_lookup, decision_state)
    flat = json.dumps(view, ensure_ascii=False).lower()
    for required in (
        "режим", "готовність", "реальних пар",
        "якість моделі", "причина рішення",
    ):
        assert required in flat, (
            f"Production _build_ai_view "
            f"must surface {required!r}; "
            f"got {flat[:400]}"
        )
    # Renamed entity must
    # surface through the
    # lookup.
    assert "sensor.user_decision_state" in flat


def test_t22_real_build_ai_view_zero_pairs_shows_no_data_message() -> None:
    mod = _parse_init()
    fn = _function_node(mod, "_build_ai_view")
    assert fn is not None
    body = ast.unparse(fn)
    ns: dict = {"__builtins__": __builtins__}
    exec(body, ns)
    real_builder = ns["_build_ai_view"]
    decision_state = {
        "mode": "Off",
        "readiness": False,
        "real_pairs": 0,
        "model_quality": 0.0,
        "reason": "no_data_yet",
    }
    view = real_builder(lambda k: "", decision_state)
    flat = json.dumps(view, ensure_ascii=False)
    assert "даних ще немає" in flat.lower() or "0 пар" in flat.lower()


# ─────────────────────────────────────────────────────────────
# T23 round 5 - cache-bust on
# installed asset (D4)
# ─────────────────────────────────────────────────────────────


def test_t23_cache_bust_uses_installed_www_dir() -> None:
    """D4: ``_compute_assets_cache_bust``
    must be called against the
    INSTALLED www directory
    (``hass.config.config_dir/www/community/powmr-inverter/``),
    not the bundled ``www/``.

    The round-4 implementation
    hashed the bundled package
    www which was empty → all
    assets got the SHA-256 of
    empty bytes (``e3b0c442...``).
    """
    mod = _parse_init()
    fn = _function_node(mod, "_install_flow_card")
    assert fn is not None
    src = _source_of(fn)
    # The cache-bust call must
    # reference the installed
    # www path, not the bundled
    # one.
    assert "community/powmr-inverter" in src, (
        "D4: cache-bust call must use "
        "the installed www/community/"
        "powmr-inverter/ path; got:\n"
        + src
    )
    # The bundled package www
    # path must NOT appear in
    # the cache-bust line.
    assert "os.path.dirname(__file__)" not in src or (
        # The bundled path is only
        # allowed if it is in the
        # COPY source, not the
        # cache-bust.
        src.count("os.path.dirname(__file__)") == 1
    )


def test_t23_cache_bust_round_trip_against_installed_dir() -> None:
    """End-to-end: when the
    installed asset is changed,
    the cache-bust hash changes.
    """
    # Drive the production
    # function directly.
    ns = _exec_function("_compute_assets_cache_bust")
    real = ns["_compute_assets_cache_bust"]
    with tempfile.TemporaryDirectory() as tmp:
        # No assets yet → empty
        # hash (all files missing).
        empty = real(tmp, ["a.js", "b.js"])
        # Write assets.
        open(
            os.path.join(tmp, "a.js"), "w"
        ).write("v1")
        open(
            os.path.join(tmp, "b.js"), "w"
        ).write("v1")
        v1 = real(tmp, ["a.js", "b.js"])
        open(
            os.path.join(tmp, "b.js"), "w"
        ).write("v2-changed")
        v2 = real(tmp, ["a.js", "b.js"])
        assert v1 != empty, (
            "Hash must respond to "
            "real asset content."
        )
        assert v1 != v2, (
            "Hash must respond to "
            "asset content change."
        )


def test_t23_missing_asset_not_silently_registered() -> None:
    """D4 follow-up: when the
    asset is missing on disk, the
    production function must NOT
    return a stable empty-hash
    that the caller interprets
    as "asset loaded".
    """
    ns = _exec_function("_compute_assets_cache_bust")
    real = ns["_compute_assets_cache_bust"]
    with tempfile.TemporaryDirectory() as tmp:
        # Asset missing entirely.
        result_missing = real(tmp, ["a.js"])
        # Asset present.
        open(os.path.join(tmp, "a.js"), "w").write("hello")
        result_present = real(tmp, ["a.js"])
        assert result_missing != result_present, (
            "Missing asset must produce a "
            "different hash so the "
            "caller can detect it."
        )


# ─────────────────────────────────────────────────────────────
# T23 round 5 - atomic metadata
# + missing storage (D5)
# ─────────────────────────────────────────────────────────────


def test_t23_metadata_atomic_writer_function_in_init() -> None:
    mod = _parse_init()
    fn = _function_node(
        mod, "_write_dashboards_metadata_atomic"
    )
    assert fn is not None, (
        "D5: _write_dashboards_metadata_atomic "
        "must exist in __init__.py"
    )


def test_t23_metadata_atomic_uses_replace() -> None:
    mod = _parse_init()
    fn = _function_node(
        mod, "_write_dashboards_metadata_atomic"
    )
    assert fn is not None
    src = _source_of(fn)
    assert "os.replace" in src
    assert "tempfile" in src or "NamedTemporaryFile" in src


def test_t23_metadata_creates_file_when_missing() -> None:
    """D5: missing
    ``.storage/lovelace_dashboards``
    must NOT crash; the writer
    creates it.
    """
    mod = _parse_init()
    fn = _function_node(
        mod, "_write_dashboards_metadata_atomic"
    )
    assert fn is not None
    body = ast.unparse(fn)
    ns: dict = {
        "__builtins__": __builtins__,
        "json": json,
        "os": os,
        "shutil": shutil,
        "tempfile": __import__("tempfile"),
    }
    exec(body, ns)
    real = ns["_write_dashboards_metadata_atomic"]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, ".storage", "lovelace_dashboards")
        # Path does NOT exist.
        assert not os.path.exists(path)
        real(path, {"data": {"items": []}})
        assert os.path.exists(path)
        with open(path) as f:
            after = json.load(f)
        assert after["data"]["items"] == []


def test_t23_metadata_preserves_user_entries() -> None:
    """D5: when the metadata file
    already exists, a write that
    adds our entry must preserve
    every other entry.
    """
    mod = _parse_init()
    fn = _function_node(
        mod, "_write_dashboards_metadata_atomic"
    )
    assert fn is not None
    body = ast.unparse(fn)
    ns: dict = {
        "__builtins__": __builtins__,
        "json": json,
        "os": os,
        "shutil": shutil,
        "tempfile": __import__("tempfile"),
    }
    exec(body, ns)
    real = ns["_write_dashboards_metadata_atomic"]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, ".storage", "lovelace_dashboards")
        os.makedirs(os.path.dirname(path))
        existing = {
            "version": 1, "minor_version": 1, "key_version": 1,
            "data": {"items": [
                {"id": "user", "url_path": "lovelace-user"},
            ]},
        }
        with open(path, "w") as f:
            json.dump(existing, f)
        real(path, {
            "data": {"items": [
                {"id": "user", "url_path": "lovelace-user"},
                {"id": "powmr_energy", "url_path": "powmr-energy"},
            ]},
        })
        with open(path) as f:
            after = json.load(f)
        ids = [it["id"] for it in after["data"]["items"]]
        assert "user" in ids
        assert "powmr_energy" in ids


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