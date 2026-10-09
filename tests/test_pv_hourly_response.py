"""Measured shading, bad telemetry and actual forecast source regression."""
import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import aiohttp
except ImportError:
    sys.modules['aiohttp'] = ModuleType('aiohttp')
from hems.forecast import ForecastService
from hems.pv_hourly import train_hourly_response, validate_hourly_response
from hems.pv_coordinator import PvLearningCoordinatorMixin


class HourlyResponseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.today = datetime(2026, 10, 3, tzinfo=timezone.utc)
        self.power, self.radiation = [], []
        for day in range(2):
            for hour in range(24):
                ts = (self.today - timedelta(days=2-day) + timedelta(hours=hour)).timestamp()
                rad = 500 if 8 <= hour <= 17 else 0
                power = 50 if 8 <= hour < 14 else 450 if 14 <= hour <= 17 else 0
                self.power.append({'start': ts, 'mean': power})
                self.radiation.append({'start': ts, 'mean': rad})

    def train(self, power=None, radiation=None):
        return train_hourly_response(self.power if power is None else power,
                                     self.radiation if radiation is None else radiation,
                                     timezone.utc, self.today.date())

    def test_shading_and_provisional_evidence(self):
        model = self.train()
        self.assertEqual(model['gains'][12], .1)
        self.assertEqual(model['gains'][16], .9)
        self.assertTrue(model['provisional'])
        self.assertEqual(model['sample_days'], 2)

    def test_single_day_and_duplicate_not_evidence(self):
        self.assertIsNone(self.train(self.power[:24] * 2))

    def test_missing_power_or_radiation_not_filled(self):
        self.assertIsNone(self.train(self.power[:-1]))
        self.assertIsNone(self.train(radiation=self.radiation[:-1]))

    def test_stuck_night_rejects_day(self):
        rows = [dict(r) for r in self.power]
        rows[22]['mean'] = 461
        self.assertIsNone(self.train(rows))

    def test_nonfinite_and_stale_not_evidence(self):
        rows = [dict(r) for r in self.power]
        rows[12]['mean'] = float('nan')
        self.assertIsNone(self.train(rows))
        old = [{**r, 'start': r['start'] - 30*86400} for r in self.power]
        self.assertIsNone(self.train(old))

    def test_sustained_change_uses_recent_regime_without_fixed_dates(self):
        power, radiation = [], []
        for d in range(10):
            for h in range(24):
                ts = (self.today - timedelta(days=10-d) + timedelta(hours=h)).timestamp()
                rad = 500 if 8 <= h <= 17 else 0
                p = rad * (.8 if d >= 7 else .1)
                power.append({'start': ts, 'mean': p})
                radiation.append({'start': ts, 'mean': rad})
        model = self.train(power, radiation)
        self.assertEqual(model['available_days'], 10)
        self.assertEqual(model['sample_days'], 3)
        self.assertEqual(model['gains'][16], .8)
        self.assertEqual(model['training_reason'], 'sustained_recent_gain_increase')
        # A single unusually productive day does not redefine the station.
        for row in power[:-24]:
            row['mean'] = 50 if row['mean'] > 0 else 0
        model = self.train(power, radiation)
        self.assertEqual(model['sample_days'], 10)
        self.assertEqual(model['gains'][16], .1)

    def test_walk_forward_validates_only_unseen_days_and_no_live_confidence(self):
        power, radiation = [], []
        for d in range(8):
            for h in range(24):
                ts=(self.today-timedelta(days=8-d)+timedelta(hours=h)).timestamp()
                rad=500 if 8 <= h <= 17 else 0
                p=rad*(.1 if h<14 else .9)
                power.append({'start':ts,'mean':p})
                radiation.append({'start':ts,'mean':rad})
        metrics=validate_hourly_response(power,radiation,timezone.utc,self.today.date())
        self.assertEqual(metrics['test_days'],6)
        self.assertEqual(metrics['daylight_mae_w'],0)
        self.assertGreater(metrics['baseline_daylight_mae_w'],0)
        self.assertFalse(metrics['live_forecast_accuracy'])
        # The final day changes, but cannot teach its own prediction.
        for r in power[-24:]:r['mean']*=2
        metrics=validate_hourly_response(power,radiation,timezone.utc,self.today.date())
        self.assertGreater(metrics['daylight_mae_w'],0)

    async def test_common_source_and_cache_invalidation(self):
        f = ForecastService(timezone_name='UTC')
        f._hourly_cache, f._daily_cache = (1, []), (1, {})
        self.assertTrue(f.set_hourly_response(self.train()))
        self.assertIsNone(f._hourly_cache)
        self.assertIsNone(f._daily_cache)
        data = {'hourly': {'time': [int((self.today + timedelta(hours=h)).timestamp()) for h in (12, 16)],
                           'shortwave_radiation': [500, 500]}}
        class Response:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def raise_for_status(self): pass
            async def json(self): return data
        f._ensure_session = AsyncMock(return_value=SimpleNamespace(get=Mock(return_value=Response())))
        f._rate_limit = AsyncMock()
        # Freeze datetime.now() to today so the trim keeps rows
        # whose local date is today. Without this, the trim
        # uses real wall-clock now and drops the rows.
        import hems.forecast as forecast_mod
        real_datetime = forecast_mod.datetime

        class _FrozenDateTime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                base = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
                return base if tz is None else base.astimezone(tz)

        forecast_mod.datetime = _FrozenDateTime
        try:
            with patch('hems.forecast.ZoneInfo', return_value=timezone.utc):
                rows = await f._fetch_hourly()
        finally:
            forecast_mod.datetime = real_datetime
        self.assertEqual([r['power_w'] for r in rows], [50, 450])
        f.hourly_response['last_day'] = '2026-09-01'
        # Re-freeze for the second call; the finally above has
        # already restored ``datetime``.
        forecast_mod.datetime = _FrozenDateTime
        try:
            with patch('hems.forecast.ZoneInfo', return_value=timezone.utc):
                rows = await f._fetch_hourly()
        finally:
            forecast_mod.datetime = real_datetime
        self.assertEqual([r['power_w'] for r in rows], [60, 60])

    async def test_coordinator_throttle_and_no_calibration_seed(self):
        c = PvLearningCoordinatorMixin.__new__(PvLearningCoordinatorMixin)
        c._hourly_pv_rows = self.power
        c._site_timezone = timezone.utc
        c._pv_local_now = lambda: self.today
        c._forecast = ForecastService(timezone_name='UTC')
        c._forecast.get_archive_hourly_radiation = AsyncMock(return_value=self.radiation)
        c._maybe_refresh_forecast = AsyncMock()
        await c._maybe_train_hourly_pv(self.today)
        await c._maybe_train_hourly_pv(self.today + timedelta(hours=1))
        self.assertEqual(c._forecast.get_archive_hourly_radiation.call_count, 1)
        self.assertEqual(c._maybe_refresh_forecast.call_count, 1)
        self.assertEqual(c._forecast.hourly_response['sample_days'], 2)
        # No access to or mutation of live ForecastCalibrator was needed.
        self.assertFalse(hasattr(c, '_pv_calibrator'))

    async def test_archive_failure_preserves_model(self):
        c = PvLearningCoordinatorMixin.__new__(PvLearningCoordinatorMixin)
        c._hourly_pv_rows = self.power
        c._pv_local_now = lambda: self.today
        c._forecast = ForecastService(timezone_name='UTC')
        c._forecast.set_hourly_response(self.train())
        c._forecast.get_archive_hourly_radiation = AsyncMock(side_effect=RuntimeError('offline'))
        await c._maybe_train_hourly_pv(self.today)
        self.assertEqual(c._forecast.hourly_response['sample_days'], 2)


if __name__ == '__main__':
    unittest.main()
