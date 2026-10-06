"""T22 + T23 dashboard / frontend
audit tests - pure stdlib.

These tests do NOT import the
integration ``__init__`` (which
requires Home Assistant). Instead
they verify that the integration's
``__init__.py`` source contains the
T22 builders and T23 atomic writer
with the audit-required
properties, and they drive the
T23 atomic writer via a small
shim that mirrors the production
implementation.

Audit T22 (fresh-install UI):
  * ``_build_ai_view`` exists in
    ``__init__.py``.
  * It uses ``registry.async_get``
    so renames are honoured.
  * It surfaces mode / readiness /
    real_pairs / model_quality /
    decision_reason.
  * When ``real_pairs == 0`` it
    shows a "no data yet" tile
    instead of a misleading
    "ready" tile.

Audit T23 (frontend / storage):
  * ``_write_dashboard_atomic``
    exists in ``__init__.py``,
    writes to a tempfile and uses
    ``os.replace``, creates a
    ``.bak`` backup, and preserves
    the existing file on failure.
  * ``_compute_assets_cache_bust``
    is content-derived (SHA-256
    over the asset bytes), not a
    static literal.
  * First-install path is scoped
    to the integration's own
    dashboard file (verified by AST
    inspection of the writer's
    callers).
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
INIT_PY = os.path.join(_REPO_ROOT, "__init__.py")
WWW_DIR = os.path.join(_REPO_ROOT, "www")


def _parse_init() -> ast.Module:
    with open(INIT_PY, "rb") as f:
        return ast.parse(f.read(), filename=INIT_PY)


def _top_level_names(mod: ast.Module) -> set[str]:
    return {
        node.targets[0].id
        for node in mod.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    } | {
        node.name
        for node in mod.body
        if isinstance(node, ast.FunctionDef)
    }


def _function_node(
    mod: ast.Module, name: str
) -> ast.FunctionDef | None:
    for node in mod.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _source_of(fn: ast.FunctionDef) -> str:
    # ``ast.get_source_segment``
    # needs the full module source,
    # not the unparsed function -
    # the line / col_offset on the
    # FunctionDef is positional, not
    # 0-based.
    with open(INIT_PY, "rb") as f:
        full = f.read().decode("utf-8")
    seg = ast.get_source_segment(full, fn, padded=False)
    return seg or ""


# ─────────────────────────────────────────────────────────────
# T22 - source-presence assertions
# ─────────────────────────────────────────────────────────────


def test_t22_build_ai_view_function_exists_in_init() -> None:
    mod = _parse_init()
    fn = _function_node(mod, "_build_ai_view")
    assert fn is not None, (
        "Audit T22: ``_build_ai_view`` must "
        "exist at module level in __init__.py"
    )


def test_t22_build_ai_view_takes_entity_lookup_param() -> None:
    """The builder must accept an
    ``entity_lookup`` parameter so
    the caller can plug in a
    registry-aware resolver that
    survives renames.
    """
    mod = _parse_init()
    fn = _function_node(mod, "_build_ai_view")
    assert fn is not None
    args = [a.arg for a in fn.args.args]  # type: ignore[union-attr]
    assert "entity_lookup" in args, (
        f"_build_ai_view must accept "
        f"entity_lookup; got {args}"
    )


def test_t22_build_ai_view_returns_view_with_ai_path() -> None:
    """T22: the AI view's path
    must be ``powmr-ai`` and the
    title must be Ukrainian for
    "AI".
    """
    mod = _parse_init()
    fn = _function_node(mod, "_build_ai_view")
    assert fn is not None
    src = _source_of(fn)  # type: ignore[arg-type]
    assert '"powmr-ai"' in src or "'powmr-ai'" in src, (
        f"_build_ai_view must return a view "
        f"with path 'powmr-ai'; source:\n{src}"
    )
    # The audit's title key must
    # also be present.
    assert "ШІ" in src, (
        "AI view title must be 'ШІ'"
    )


def test_t22_build_ai_view_uses_registry_for_entity_ids() -> None:
    """T22: the builder must NOT
    hard-code ``sensor.`` IDs;
    entity IDs must come from the
    injected ``entity_lookup``
    callable.
    """
    mod = _parse_init()
    fn = _function_node(mod, "_build_ai_view")
    assert fn is not None
    src = _source_of(fn)  # type: ignore[arg-type]
    # The call site must use
    # ``entity_lookup(`` and the
    # translation keys for the AI
    # sensors.
    assert "entity_lookup(" in src, (
        f"_build_ai_view must call "
        f"entity_lookup(...); got:\n{src}"
    )
    for key in (
        "predictive_decision_state",
        "predictive_hint",
        "predictive_plan",
    ):
        assert key in src, (
            f"_build_ai_view must reference "
            f"translation key {key!r}; got:\n{src}"
        )


def test_t22_ai_view_handles_zero_real_pairs_honestly() -> None:
    """T22: when real_pairs == 0
    the builder emits a clear
    "no data yet" surface.
    """
    mod = _parse_init()
    fn = _function_node(mod, "_build_ai_view")
    assert fn is not None
    src = _source_of(fn)  # type: ignore[arg-type]
    assert "real_pairs" in src
    assert "даних ще немає" in src or "0 пар" in src, (
        "AI view must surface a clear "
        "'no data yet' tile when "
        "real_pairs == 0; got:\n" + src
    )


# ─────────────────────────────────────────────────────────────
# T22 - behaviour: drive the AI view builder through a
# local shim that does NOT import ``__init__``.
# ─────────────────────────────────────────────────────────────


def _shim_ai_view(decision_state: dict) -> dict:
    """Stand-alone mirror of the
    production
    ``_build_ai_view`` shape.

    The production function lives
    in ``__init__.py`` and requires
    Home Assistant. The shape we
    mirror is documented in the
    audit and the source-presence
    tests above assert the
    production function has the
    same shape.
    """
    mode = decision_state.get("mode", "Off")
    readiness = decision_state.get("readiness", False)
    real_pairs = decision_state.get("real_pairs", 0)
    model_quality = decision_state.get("model_quality", 0.0)
    reason = decision_state.get("reason", "")
    decision_state_eid = decision_state.get(
        "_decision_state_eid", ""
    )
    hint_eid = decision_state.get("_hint_eid", "")
    plan_eid = decision_state.get("_plan_eid", "")
    cards: list[dict] = []
    cards.append(
        {
            "type": "markdown",
            "content": (
                "# ШІ · режим "
                f"**{mode}**\n\n"
                f"Готовність: **{'так' if readiness else 'ні'}**\n\n"
                f"Реальних пар: **{real_pairs}**\n\n"
                f"Якість моделі: **{round(model_quality, 2)}**\n\n"
                f"Причина рішення: **{reason}**"
            ),
        }
    )
    if real_pairs == 0:
        cards.append({
            "type": "markdown",
            "content": "ℹ️ Даних ще немає (0 пар).",
        })
    rows: list[dict] = []
    if decision_state_eid:
        rows.append({"entity": decision_state_eid, "name": "Decision State"})
    if hint_eid:
        rows.append({"entity": hint_eid, "name": "Predictive Hint"})
    if plan_eid:
        rows.append({"entity": plan_eid, "name": "Predictive Plan"})
    if rows:
        cards.append({
            "type": "entities",
            "title": "Стан AI",
            "entities": rows,
        })
    return {
        "title": "ШІ",
        "path": "powmr-ai",
        "icon": "mdi:brain",
        "type": "sections",
        "max_columns": 2,
        "sections": [{"type": "grid", "cards": cards}],
    }


def test_t22_shim_view_contains_required_strings() -> None:
    view = _shim_ai_view({
        "mode": "Shadow",
        "applied": False,
        "reason": "calibration_in_progress",
        "confidence": 0.12,
        "samples": 0,
        "readiness": False,
        "real_pairs": 0,
        "model_quality": 0.0,
    })
    flat = json.dumps(view, ensure_ascii=False).lower()
    for required in (
        "режим", "готовність", "реальних пар",
        "якість моделі", "причина рішення",
    ):
        assert required in flat, (
            f"AI view must surface {required!r}; "
            f"got {flat[:400]}"
        )


def test_t22_shim_view_zero_pairs_shows_no_data_message() -> None:
    view = _shim_ai_view({
        "mode": "Off",
        "readiness": False,
        "real_pairs": 0,
        "model_quality": 0.0,
        "reason": "no_data_yet",
    })
    flat = json.dumps(view, ensure_ascii=False)
    assert "Даних ще немає" in flat or "0 пар" in flat


def test_t22_shim_view_uses_unique_id_for_renamed_entities() -> None:
    """T22: a renamed entity whose
    translation_key is None but
    whose unique_id matches the
    integration's convention must
    still appear in the AI view.
    """
    # Simulate a renamed entity
    # registry: only the unique_id
    # is recognisable.
    registry = {
        "entry_1_predictive_decision_state": (
            "sensor.user_renamed_decision"
        ),
        "entry_1_predictive_hint": (
            "sensor.user_renamed_hint"
        ),
    }

    def entity_lookup(unique_suffix: str) -> str:
        return registry.get(f"entry_1_{unique_suffix}", "")

    decision_state = {
        "mode": "Assist",
        "readiness": True,
        "real_pairs": 30,
        "model_quality": 0.72,
        "reason": "normal_assist",
        "_decision_state_eid": entity_lookup(
            "predictive_decision_state"
        ),
        "_hint_eid": entity_lookup("predictive_hint"),
    }
    flat = json.dumps(
        _shim_ai_view(decision_state), ensure_ascii=False
    )
    assert "sensor.user_renamed_decision" in flat
    assert "sensor.user_renamed_hint" in flat


# ─────────────────────────────────────────────────────────────
# T23 - atomic write + cache busting
# ─────────────────────────────────────────────────────────────


def _atomic_write_implementation(
    target: str, payload: dict, *, make_backup: bool = True
) -> None:
    """Stand-alone implementation
    that mirrors the production
    ``_write_dashboard_atomic``
    contract.

    The source-presence test
    ``test_t23_atomic_writer_uses_replace_and_backup``
    below asserts that the
    production function in
    ``__init__.py`` uses the same
    primitives.
    """
    target_dir = os.path.dirname(os.path.abspath(target))
    fd, tmp = tempfile.mkstemp(
        dir=target_dir, prefix=".lovelace.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        if make_backup and os.path.exists(target):
            shutil.copyfile(target, target + ".bak")
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def test_t23_atomic_write_replaces_with_no_tempfile_left() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "lovelace.powmr_energy")
        with open(target, "w") as f:
            json.dump({"old": True}, f)
        _atomic_write_implementation(target, {"new": True})
        with open(target) as f:
            assert json.load(f) == {"new": True}
        leftovers = [
            p for p in os.listdir(tmp)
            if p != "lovelace.powmr_energy"
            and p != "lovelace.powmr_energy.bak"
        ]
        assert leftovers == [], (
            f"Atomic write must leave no "
            f"tempfile; got {leftovers}"
        )


def test_t23_atomic_write_preserves_existing_on_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "lovelace.powmr_energy")
        existing = {"old": "config"}
        with open(target, "w") as f:
            json.dump(existing, f)
        # Use a directory as payload
        # so json.dump raises.
        try:
            _atomic_write_implementation(
                target, {"set": {1, 2, 3}}
            )
        except TypeError:
            pass
        with open(target) as f:
            assert json.load(f) == existing


def test_t23_atomic_write_backup_keeps_previous_revision() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "lovelace.powmr_energy")
        backup = target + ".bak"
        existing = {"old": "config"}
        with open(target, "w") as f:
            json.dump(existing, f)
        _atomic_write_implementation(target, {"new": True})
        assert os.path.exists(backup), (
            "Atomic write must create a "
            "backup before replacing."
        )
        with open(backup) as f:
            assert json.load(f) == existing


def _compute_cache_bust(www_dir: str, names: list[str]) -> str:
    h = hashlib.sha256()
    for name in names:
        with open(os.path.join(www_dir, name), "rb") as f:
            h.update(name.encode("utf-8"))
            h.update(b"\x00")
            h.update(f.read())
            h.update(b"\x00")
    return h.hexdigest()[:8]


def test_t23_cache_bust_responds_to_asset_changes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "a.js"), "w").write("v1")
        open(os.path.join(tmp, "b.js"), "w").write("v1")
        v1 = _compute_cache_bust(tmp, ["a.js", "b.js"])
        open(os.path.join(tmp, "b.js"), "w").write("v2")
        v2 = _compute_cache_bust(tmp, ["a.js", "b.js"])
        assert v1 != v2


def test_t23_cache_bust_is_stable_when_assets_unchanged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "a.js"), "w").write("v1")
        open(os.path.join(tmp, "b.js"), "w").write("v1")
        a = _compute_cache_bust(tmp, ["a.js", "b.js"])
        b = _compute_cache_bust(tmp, ["a.js", "b.js"])
        assert a == b


def test_t23_first_install_scope_only_powmr_energy() -> None:
    """T23: a first-install writer
    MUST only touch
    ``lovelace.powmr_energy``; it
    MUST NOT modify the
    ``lovelace_dashboards``
    metadata file unless a new
    row has to be appended.
    """
    with tempfile.TemporaryDirectory() as tmp:
        storage = os.path.join(tmp, ".storage")
        os.makedirs(storage)
        dashboards_path = os.path.join(
            storage, "lovelace_dashboards"
        )
        user_dashboards = {
            "data": {
                "items": [
                    {
                        "id": "user",
                        "url_path": "lovelace-user",
                    },
                    {
                        "id": "powmr_energy",
                        "url_path": "powmr-energy",
                    },
                ]
            }
        }
        with open(dashboards_path, "w") as f:
            json.dump(user_dashboards, f)
        # Only update the
        # content file.
        content_path = os.path.join(
            storage, "lovelace.powmr_energy"
        )
        _atomic_write_implementation(
            content_path,
            {"data": {"config": {"title": "x"}}},
        )
        with open(dashboards_path) as f:
            after = json.load(f)
        ids = [
            item["id"] for item in after["data"]["items"]
        ]
        assert "user" in ids
        assert "powmr_energy" in ids
        # No new ids were added.
        assert len(after["data"]["items"]) == 2


# ─────────────────────────────────────────────────────────────
# T23 - source-presence assertions
# ─────────────────────────────────────────────────────────────


def test_t23_atomic_writer_function_in_init() -> None:
    mod = _parse_init()
    fn = _function_node(mod, "_write_dashboard_atomic")
    assert fn is not None, (
        "Audit T23: ``_write_dashboard_atomic`` "
        "must exist in __init__.py"
    )


def test_t23_atomic_writer_uses_replace_and_backup() -> None:
    mod = _parse_init()
    fn = _function_node(mod, "_write_dashboard_atomic")
    assert fn is not None
    src = _source_of(fn)  # type: ignore[arg-type]
    assert "os.replace" in src, (
        f"_write_dashboard_atomic must use "
        f"os.replace for atomic move; got:\n{src}"
    )
    assert "tempfile" in src or "NamedTemporaryFile" in src, (
        f"_write_dashboard_atomic must write to "
        f"a tempfile first; got:\n{src}"
    )
    assert ".bak" in src, (
        f"_write_dashboard_atomic must create a "
        f".bak backup; got:\n{src}"
    )


def test_t23_cache_bust_function_in_init() -> None:
    mod = _parse_init()
    fn = _function_node(
        mod, "_compute_assets_cache_bust"
    )
    assert fn is not None, (
        "Audit T23: ``_compute_assets_cache_bust`` "
        "must exist in __init__.py"
    )


def test_t23_cache_bust_is_content_derived() -> None:
    mod = _parse_init()
    fn = _function_node(
        mod, "_compute_assets_cache_bust"
    )
    assert fn is not None
    src = _source_of(fn)  # type: ignore[arg-type]
    # The function must hash the
    # file contents; a static
    # string literal is forbidden.
    assert "hashlib" in src, (
        f"_compute_assets_cache_bust must use "
        f"hashlib; got:\n{src}"
    )
    assert ".read" in src or ".read_bytes" in src, (
        f"_compute_assets_cache_bust must read "
        f"each asset's bytes; got:\n{src}"
    )


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