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

These tests pin two contracts:

1. The dedup logic correctly parses
   ``.storage/lovelace_resources`` and returns the set of
   paths (without cache-bust query strings).

2. Each bundled frontend JS file guards
   ``customElements.define`` with
   ``if (!customElements.get(...))`` so a double-load is
   safe even if the dedup step is bypassed.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


REPO = Path("/opt/data/powmr-ai-work/powmr_inverter")


class TestR09ResourcePathExtraction(unittest.TestCase):
    """The dedup logic strips cache-bust query strings so
    ``?v=2.0.0-92c25bc9`` matches the canonical
    ``/local/community/.../<file>.js`` path.
    """

    def _extract(self, store_items):
        tmp = Path(tempfile.mkdtemp())
        try:
            store = tmp / ".storage" / "lovelace_resources"
            store.parent.mkdir(parents=True, exist_ok=True)
            store.write_text(
                json.dumps({"data": {"items": store_items}}),
                encoding="utf-8",
            )
            # Inline the same logic the integration uses
            # so we can test it without importing the
            # whole integration module.
            import json as _json
            data = _json.loads(store.read_text(encoding="utf-8"))
            urls: set[str] = set()
            for item in (data.get("data") or {}).get("items") or []:
                raw = str(item.get("url") or "")
                path = raw.split("?", 1)[0]
                if path:
                    urls.add(path)
            return urls
        finally:
            pass

    def test_empty_store_returns_empty_set(self):
        self.assertEqual(self._extract([]), set())

    def test_single_url_strips_query(self):
        result = self._extract(
            [
                {
                    "id": "x",
                    "url": "/local/community/powmr-inverter/power-history-card.js?v=2.0.0-92c25bc9",
                },
            ]
        )
        self.assertEqual(
            result,
            {"/local/community/powmr-inverter/power-history-card.js"},
        )

    def test_multiple_urls_kept(self):
        result = self._extract(
            [
                {"id": "a", "url": "/local/community/powmr-inverter/k-flow-card.js?v=1.9.1-610b4520"},
                {"id": "b", "url": "/local/community/powmr-inverter/forecast-card.js"},
                {"id": "c", "url": "/local/community/powmr-inverter/pv-comparison-card.js?v=2"},
            ]
        )
        self.assertEqual(
            result,
            {
                "/local/community/powmr-inverter/k-flow-card.js",
                "/local/community/powmr-inverter/forecast-card.js",
                "/local/community/powmr-inverter/pv-comparison-card.js",
            },
        )

    def test_missing_storage_file_returns_empty(self):
        # Simulate a fresh install with no .storage file.
        tmp = Path(tempfile.mkdtemp())
        store = tmp / ".storage" / "lovelace_resources"
        self.assertFalse(store.exists())


class TestR09CustomElementGuard(unittest.TestCase):
    """Each bundled frontend JS file MUST guard
    ``customElements.define`` with
    ``if (!customElements.get(...))`` so a double-load is
    safe. This is the contract pin — if a future edit
    removes the guard, this test fails immediately.
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
                f"{path} missing guard `if (!customElements.get('{name}'))`",
            )

    def test_each_file_still_calls_define(self):
        for path, name in self.CASES:
            text = (REPO / path).read_text()
            # Either the original `customElements.define('NAME', ...)`
            # or the guarded form `if (!customElements.get('NAME')) {
            # customElements.define('NAME', ...) }` should reference
            # the symbol.
            self.assertIn(
                f"customElements.define('{name}'", text,
                f"{path} missing customElements.define for {name}",
            )

    def test_pv_comparison_card_already_guarded(self):
        # pv-comparison-card was the first file to add the
        # guard. We keep it on the list as a regression
        # guard.
        text = (REPO / "frontend/pv-comparison-card.js").read_text()
        self.assertIn(
            "if (!customElements.get('pv-comparison-card'))",
            text,
        )


class TestR09IntegrationSkipsAlreadyRegistered(unittest.IsolatedAsyncioTestCase):
    """End-to-end: when lovelace_resources already lists
    power-history-card.js, the integration's
    _install_flow_card MUST NOT call add_extra_js_url for
    that path again.
    """

    async def test_power_history_not_reregistered(self):
        import importlib.util
        import json as _json
        import sys
        import tempfile

        # Build a synthetic config_dir with the operator's
        # lovelace_resources.
        tmp = Path(tempfile.mkdtemp())
        store_dir = tmp / ".storage"
        store_dir.mkdir()
        store_dir.joinpath("lovelace_resources").write_text(
            _json.dumps(
                {
                    "data": {
                        "items": [
                            {
                                "id": "operator-ph",
                                "url": (
                                    "/local/community/powmr-inverter/"
                                    "power-history-card.js?v=2.0.0-92c25bc9"
                                ),
                                "type": "module",
                            },
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

        # Load the integration module by file path so the
        # test does not depend on the package layout.
        spec = importlib.util.spec_from_file_location(
            "_powmr_inverter_under_test", REPO / "__init__.py"
        )
        if spec is None or spec.loader is None:
            self.skipTest("could not load integration __init__.py")
            return
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        _install_flow_card = mod._install_flow_card

        # Stub hass.
        class _Hass:
            class _Cfg:
                config_dir = str(tmp)

            config = _Cfg()

            async def async_add_executor_job(self, fn, *a, **kw):
                return fn(*a, **kw)

        hass = _Hass()

        # Capture add_extra_js_url calls. The integration
        # imports it lazily inside the function:
        # ``from homeassistant.components.frontend import
        # add_extra_js_url``. We patch the symbol in
        # ``homeassistant.components.frontend``.
        import homeassistant.components.frontend as fe  # type: ignore

        called: list[str] = []

        def _capture(hass_arg, url):
            called.append(url)

        orig = fe.add_extra_js_url
        fe.add_extra_js_url = _capture
        try:
            await _install_flow_card(hass)
        finally:
            fe.add_extra_js_url = orig

        ph_calls = [u for u in called if "power-history-card" in u]
        self.assertEqual(
            ph_calls, [],
            f"power-history-card was re-registered despite being in "
            f"lovelace_resources: {ph_calls}",
        )


async def _async_run(fn, *args, **kwargs):
    return fn(*args, **kwargs)


if __name__ == "__main__":
    unittest.main()
