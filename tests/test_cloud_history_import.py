"""Cloud energy provenance, bounded transport and honest calibration."""
import ast
import asyncio
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hems.cloud_history import measured_pv_days
from hems.pv_coordinator import PvLearningCoordinatorMixin
from hems.forecast_calibration import ForecastCalibrator


def properties(points, key="pvGeneratedEnergy", unit="kWh"):
    return [{"property": {"key": key, "unit": unit}, "timePoints": points}]


class TestCloudFacts(unittest.TestCase):
    start, end = date(2026, 9, 1), date(2026, 9, 30)

    def test_measured_days_only(self):
        rows = [{"time": "2026-09-01", "value": .571, "isRealValue": True},
                {"time": "2026-09-02", "value": 0, "isRealValue": False},
                {"time": "2026-09-03", "value": 0, "isRealValue": True},
                {"time": "2026-10-01", "value": 1, "isRealValue": True}]
        self.assertEqual(measured_pv_days(properties(rows), self.start, self.end), {"2026-09-01": .571, "2026-09-03": 0.})

    def test_wrong_property_and_unit(self):
        rows = [{"time": "2026-09-01", "value": 1., "isRealValue": True}]
        for key, unit in (("consumeElectricityQuantity", "kWh"), ("pvGeneratedEnergy", "Wh")):
            self.assertEqual(measured_pv_days(properties(rows, key, unit), self.start, self.end), {})

    def test_invalid_values_and_ambiguous_duplicates(self):
        for value in (True, None, -1, 501, float("nan"), float("inf")):
            rows = [{"time": "2026-09-01", "value": value, "isRealValue": True}]
            self.assertEqual(measured_pv_days(properties(rows), self.start, self.end), {})
        rows = [{"time": "2026-09-01", "value": v, "isRealValue": True} for v in (1., 2.)]
        self.assertEqual(measured_pv_days(properties(rows), self.start, self.end), {})

    def test_api_range_months_and_account_scope(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "api.py").read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "InverterApiClient")
        fn = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "fetch_daily_pv_history")
        for n in ast.walk(fn):
            if isinstance(n, ast.ImportFrom) and n.module == "hems.cloud_history": n.level = 0
        ns = {"timedelta": timedelta, "SUMMARY_KEY_ENERGY": "energy", "_LOGGER": Mock()}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "api-method", "exec"), ns)
        async def month(category, key, **kwargs):
            return properties([{"time": kwargs["month"].isoformat(), "value": 1., "isRealValue": True}])
        fake = SimpleNamespace(_account_device_count=1, _fetch_overview=AsyncMock(side_effect=month))
        result = asyncio.run(ns[fn.name](fake, date(2026, 6, 5), date(2026, 10, 2)))
        self.assertEqual(fake._fetch_overview.await_count, 5)
        self.assertEqual(len(result), 4)  # June 1 is outside the requested range
        self.assertTrue(all(c.kwargs["raw_properties"] for c in fake._fetch_overview.await_args_list))
        fake._account_device_count = 2
        fake._fetch_overview.reset_mock()
        self.assertEqual(asyncio.run(ns[fn.name](fake, self.start, self.end)), {})
        fake._fetch_overview.assert_not_called()
        fake._account_device_count = 1
        with self.assertRaises(ValueError): asyncio.run(ns[fn.name](fake, date(2026, 1, 1), self.end))

    def test_import_throttles_and_never_records_fake_pairs(self):
        c = PvLearningCoordinatorMixin.__new__(PvLearningCoordinatorMixin)
        now = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
        c._pv_local_now = lambda: now
        c.api = SimpleNamespace(fetch_daily_pv_history=AsyncMock(return_value={"2026-09-01": .571}))
        c._pv_calibrator = ForecastCalibrator(unit="kWh")
        asyncio.run(c._maybe_refresh_cloud_pv_history(now))
        asyncio.run(c._maybe_refresh_cloud_pv_history(now + timedelta(hours=1)))
        c.api.fetch_daily_pv_history.assert_awaited_once()
        self.assertEqual(c._cloud_pv_actual, {"2026-09-01": .571})
        self.assertEqual(c._pv_calibrator.metrics().sample_count, 0)

    def test_error_preserves_cache_and_is_throttled(self):
        c = PvLearningCoordinatorMixin.__new__(PvLearningCoordinatorMixin)
        now = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
        c._pv_local_now = lambda: now
        c._cloud_pv_actual = {"2026-09-01": .571}
        c.api = SimpleNamespace(fetch_daily_pv_history=AsyncMock(side_effect=RuntimeError("offline")))
        asyncio.run(c._maybe_refresh_cloud_pv_history(now))
        asyncio.run(c._maybe_refresh_cloud_pv_history(now + timedelta(minutes=5)))
        self.assertEqual(c._cloud_pv_actual, {"2026-09-01": .571})
        c.api.fetch_daily_pv_history.assert_awaited_once()


if __name__ == "__main__": unittest.main()
