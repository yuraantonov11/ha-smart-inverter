"""R09 — already-registered lovelace resources are NOT re-registered.

Юра reproduced an "Already used" custom-element warning in
the browser. The root cause: the integration calls
``add_extra_js_url`` for each card JS, but HA does NOT
deduplicate, so when the operator has already pinned the
same path in ``.storage/lovelace_resources`` (often with a
custom cache-bust version), the integration adds a second
registration. The browser loads the script twice and
``customElements.define`` throws.

R09 fix: query the operator's resource list BEFORE adding
new registrations; skip the URL when the path is already
present. The custom-element JS now also has its own
``if (!customElements.get(...))`` guard as defence in depth.

These tests drive the PRODUCTION ``_install_flow_card``
helper against synthetic ``lovelace_resources`` stores,
plus a static contract test that each bundled frontend JS
file still guards ``customElements.define``.

REPO is resolved from ``__file__`` so the test runs from
any checkout location. Temporary directories are cleaned up
via ``TemporaryDirectory`` to avoid file pollution.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


# REPO is computed from this test file's location so the
# test runs from any checkout. parents[1] = the directory
# that contains the tests/ folder.
REPO = Path(__file__).resolve().parents[1]


def _load_integration_module():
    """Load the integration's __init__.py by file path so
    the test does not depend on the package layout.
    """
    spec = importlib.util.spec_from_file_location(
        "_powmr_inverter_under_test", REPO / "__init__.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"could not load integration __init__.py from {REPO}"
        )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_store(tmp: Path, items: list) -> Path:
    """Write a synthetic lovelace_resources store inside
    ``tmp`` and return its path.
    """
    store_dir = tmp / ".storage"
    store_dir.mkdir(parents=True, exist_ok=True)
    p = store_dir / "lovelace_resources"
    p.write_text(
        json.dumps({"data": {"items": items}}),
        encoding="utf-8",
    )
    return p


def _build_hass(tmp: Path):
    """Stub hass with the minimum surface used by
    ``_install_flow_card``.
    """

    class _Hass:
        class _Cfg:
            config_dir = str(tmp)

        config = _Cfg()

        async def async_add_executor_job(self, fn, *a, **kw):
            return fn(*a, **kw)

    return _Hass()


def _capture_add_extra_js_url():
    """Patch ``homeassistant.components.frontend.add_extra_js_url``
    with a capture function. Returns ``(captured, restore)``.
    """
    import homeassistant.components.frontend as fe  # type: ignore

    captured: list[str] = []
    orig = fe.add_extra_js_url

    def _capture(hass_arg, url):
        captured.append(url)

    fe.add_extra_js_url = _capture

    def _restore():
        fe.add_extra_js_url = orig

    return captured, _restore


class TestR09ResourceExtraction(unittest.IsolatedAsyncioTestCase):
    """Drive the real ``_install_flow_card`` through the
    real ``_existing_resource_paths`` helper. Each test
    uses a fresh TemporaryDirectory so the filesystem is
    clean on entry and clean on exit.
    """

    async def test_missing_storage_file_registers_full_set(self):
        # Fresh install: no .storage file at all.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mod = _load_integration_module()
            captured, restore = _capture_add_extra_js_url()
            try:
                await mod._install_flow_card(_build_hass(tmp))
            finally:
                restore()
            # The dedup helper must return an empty set
            # for a missing storage file, so every URL
            # the integration knows about is registered.
            self.assertTrue(
                len(captured) >= 1,
                f"fresh install should register at least one URL, "
                f"got {captured}",
            )

    async def test_empty_store_registers_full_set(self):
        # Store exists with an empty items list. Same
        # semantics as a fresh install.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write_store(tmp, [])
            mod = _load_integration_module()
            captured, restore = _capture_add_extra_js_url()
            try:
                await mod._install_flow_card(_build_hass(tmp))
            finally:
                restore()
            self.assertTrue(
                len(captured) >= 1,
                f"empty store should yield full registration set, "
                f"got {captured}",
            )

    async def test_malformed_storage_does_not_crash(self):
        # Store file with invalid JSON: the dedup helper
        # MUST swallow the parse error and fall through to
        # a full registration. The setup must not raise.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            store = _write_store(tmp, [])
            store.write_text(
                "{ this is not valid JSON", encoding="utf-8"
            )
            mod = _load_integration_module()
            captured, restore = _capture_add_extra_js_url()
            try:
                # Should NOT raise even though the store
                # is malformed.
                await mod._install_flow_card(_build_hass(tmp))
            finally:
                restore()
            self.assertTrue(
                len(captured) >= 1,
                f"malformed store should fall through to "
                f"full registration, got {captured}",
            )

    async def test_pinned_power_history_not_reregistered(self):
        # Operator has pinned power-history-card.js with
        # a custom cache-bust version. The integration
        # MUST NOT add a second registration.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write_store(
                tmp,
                [
                    {
                        "id": "operator-ph",
                        "url": (
                            "/local/community/powmr-inverter/"
                            "power-history-card.js?v=2.0.0-92c25bc9"
                        ),
                        "type": "module",
                    },
                ],
            )
            mod = _load_integration_module()
            captured, restore = _capture_add_extra_js_url()
            try:
                await mod._install_flow_card(_build_hass(tmp))
            finally:
                restore()
            ph_calls = [
                u for u in captured
                if "power-history-card" in u
            ]
            self.assertEqual(
                ph_calls, [],
                f"power-history-card was re-registered despite "
                f"being in lovelace_resources: {ph_calls}",
            )

    async def test_pinned_pv_comparison_with_static_v2_skipped(self):
        # Operator has pinned
        # pv-comparison-card.js?v=2 (the legacy static
        # version). The integration MUST NOT add a
        # second registration.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write_store(
                tmp,
                [
                    {
                        "id": "operator-pc",
                        "url": (
                            "/local/community/powmr-inverter/"
                            "pv-comparison-card.js?v=2"
                        ),
                        "type": "module",
                    },
                ],
            )
            mod = _load_integration_module()
            captured, restore = _capture_add_extra_js_url()
            try:
                await mod._install_flow_card(_build_hass(tmp))
            finally:
                restore()
            pc_calls = [
                u for u in captured
                if "pv-comparison-card" in u
            ]
            self.assertEqual(
                pc_calls, [],
                f"pv-comparison-card was re-registered despite "
                f"being in lovelace_resources: {pc_calls}",
            )

    async def test_all_five_pinned_skipped(self):
        # Operator has pinned all 5 cards. The
        # integration MUST NOT register any of them.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write_store(
                tmp,
                [
                    {
                        "id": f"op-{n}",
                        "url": (
                            f"/local/community/powmr-inverter/{path}"
                            f"?v=1.9.1-610b4520"
                        ),
                        "type": "module",
                    }
                    for n, path in enumerate(
                        [
                            "k-flow-card.js",
                            "forecast-card.js",
                            "pv-comparison-card.js",
                            "power-history-card.js",
                            "total-energy-card.js",
                        ]
                    )
                ],
            )
            mod = _load_integration_module()
            captured, restore = _capture_add_extra_js_url()
            try:
                await mod._install_flow_card(_build_hass(tmp))
            finally:
                restore()
            self.assertEqual(
                captured, [],
                f"no URL should be re-registered when operator "
                f"pinned all 5: {captured}",
            )


class TestR09CustomElementGuard(unittest.TestCase):
    """Static contract: each bundled frontend JS file MUST
    wrap ``customElements.define`` in
    ``if (!customElements.get(...))`` so a double-load is
    safe. Regression pin — if a future edit removes the
    guard, this test fails immediately.
    """

    CASES = [
        ("frontend/power-history-card.js", "power-history-card"),
        ("frontend/forecast-card.js",       "forecast-card"),
        ("frontend/total-energy-card.js",   "total-energy-card"),
        ("frontend/energy-flow-card.js",    "smart-solar-energy-flow"),
        ("frontend/k-flow-card.js",         "k-flow-card"),
    ]

    def test_each_file_has_guard(self):
        for path, name in self.CASES:
            text = (REPO / path).read_text()
            needle = f"if (!customElements.get('{name}'))"
            self.assertIn(
                needle, text,
                f"{path} missing guard "
                f"`if (!customElements.get('{name}'))`",
            )

    def test_k_flow_card_editor_is_guarded(self):
        # k-flow-card.js registers TWO custom elements:
        # ``k-flow-card`` (main) and ``k-flow-card-editor``
        # (the editor panel). Both must be guarded so a
        # double-load is safe.
        text = (REPO / "frontend/k-flow-card.js").read_text()
        self.assertIn(
            "if (!customElements.get('k-flow-card-editor'))",
            text,
            "k-flow-card.js missing guard for k-flow-card-editor",
        )

    def test_each_file_still_calls_define(self):
        for path, name in self.CASES:
            text = (REPO / path).read_text()
            self.assertIn(
                f"customElements.define('{name}'", text,
                f"{path} missing customElements.define for {name}",
            )

    def test_pv_comparison_card_already_guarded(self):
        text = (REPO / "frontend/pv-comparison-card.js").read_text()
        self.assertIn(
            "if (!customElements.get('pv-comparison-card'))",
            text,
        )


if __name__ == "__main__":
    unittest.main()
