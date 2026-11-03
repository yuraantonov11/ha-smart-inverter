"""Exercise the real update listener and forecast request guards."""
import asyncio
import ast
import __future__
import logging
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hems.forecast import ForecastService


def listener():
    tree = ast.parse((ROOT / "__init__.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_async_options_updated")
    scope = {"DOMAIN": "powmr_inverter", "_LOGGER": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "real_options_listener", "exec",
                 flags=__future__.annotations.compiler_flag), scope)
    return scope[fn.name]


class SiteOptionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_location_change_reloads_once(self):
        previous = {"site_latitude": 12., "site_longitude": 45.}
        bundle = {"reload_options": previous.copy()}
        reload = AsyncMock(return_value=True)
        hass = SimpleNamespace(data={"powmr_inverter": {"e": bundle}},
                               config_entries=SimpleNamespace(async_reload=reload))
        entry = SimpleNamespace(entry_id="e", options={"site_latitude": 13., "site_longitude": 46.})
        await listener()(hass, entry)
        await listener()(hass, entry)
        reload.assert_awaited_once_with("e")

    async def test_internal_persistence_does_not_reload(self):
        reload = AsyncMock()
        bundle = {"reload_options": {"site_latitude": 12., "site_longitude": 45.}}
        hass = SimpleNamespace(data={"powmr_inverter": {"e": bundle}},
                               config_entries=SimpleNamespace(async_reload=reload))
        entry = SimpleNamespace(entry_id="e", options={**bundle["reload_options"], "_energy_state": {"changed": 1}})
        await listener()(hass, entry)
        reload.assert_not_awaited()

    async def test_failed_reload_remains_retryable(self):
        previous = {"site_latitude": 12., "site_longitude": 45.}
        bundle = {"reload_options": previous.copy()}
        hass = SimpleNamespace(data={"powmr_inverter": {"e": bundle}},
                               config_entries=SimpleNamespace(async_reload=AsyncMock(return_value=False)))
        await listener()(hass, SimpleNamespace(entry_id="e", options={"site_latitude": 13., "site_longitude": 46.}))
        self.assertEqual(bundle["reload_options"], previous)

    async def test_no_forecast_or_archive_http_without_location(self):
        service = ForecastService()
        service._ensure_session = AsyncMock(side_effect=AssertionError("must not request weather for an invented city"))
        self.assertEqual(await service.get_hourly_forecast(), [])
        self.assertEqual(await service.get_daily_forecasts(), {})
        self.assertEqual(await service.get_archive_radiation(date(2026, 1, 1), date(2026, 1, 2)), {})
        self.assertEqual(await service.get_archive_hourly_radiation(date(2026, 1, 1), date(2026, 1, 2)), [])
        service._ensure_session.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
