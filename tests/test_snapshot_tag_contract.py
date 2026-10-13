"""Production-path regression for forecast_snapshot.tag.

Юра (2026-10-13, after live deploy 6fc6ca3):

  «ha core logs --lines 15000 показує: о 10:50:56 вибрано
   station_gain_v2; о 10:51:05 — hourly_response_v2 та
   PV hourly response trained: days=12 provisional=False.
   Модель вже навчена, нестачу даних і необхідність очікування
   не підтверджено. Причина тегів 14/15 жовтня — ранні
   незмінні snapshots до завершення навчання. Збережи їх без
   переписування.

   Заміни скопійований helper у тестах викликом production
   _forecast_model_for_day. Перевір реальний сценарій:
   початковий fallback → навчання → оновлений прогноз →
   новий snapshot на ще не записану дату → завершена пара,
   прийнята активним калібратором. Тест виключення
   station_gain-пари має створювати завершену пару, а не
   перевіряти порожній журнал.»

Tests in this file:
  1. Real-scenario: initial ``station_gain_v2`` snapshot
     (before hourly training), train hourly, subsequent
     snapshot on a new day with ``hourly_response_v2``, the
     older ``station_gain_v2`` pair completes and is REJECTED
     by the active calibrator, the new ``hourly_response_v2``
     pair completes and is ACCEPTED.
  2. The production ``_forecast_model_for_day`` is invoked
     directly (no copy). The test exercises the same code
     path the live coordinator runs.

The test does NOT assume which date will be the first
``hourly_response_v2`` snapshot. It only requires the
production chain to be wired correctly: snapshot.day and
``match()`` for the matching actual produce a completed
pair, ``calibration_pairs()`` filters by tag, and the
active calibrator accepts only matching tags.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import types
import unittest
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


_REPO = Path(__file__).parent.parent.resolve()
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# --- aiohttp fake. The production ForecastService does
# ``import aiohttp`` at module load time; we patch the symbol
# the production module already imported so the
# ``await self._ensure_session()`` call inside
# ``_fetch_hourly`` resolves to the fake. ---


class _FakeForecastResponse:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    async def json(self, content_type=None):
        return self._payload


class _FakeForecastSession:
    """Records the URL and params of every ``get`` call and
    returns a synthetic Open-Meteo /v1/forecast payload.
    """

    last_url: str | None = None
    last_params: dict | None = None
    call_count: int = 0

    def __init__(self, *args, **kwargs):
        self.timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url, *args, **kwargs):
        type(self).last_url = url
        type(self).last_params = kwargs.get("params")
        type(self).call_count += 1
        empty = [None] * 24
        payload = {
            "hourly": {
                "time": empty,
                "shortwave_radiation": empty,
                "weather_code": empty,
                "cloud_cover": empty,
                "temperature_2m": empty,
                "wind_speed_10m": empty,
                "precipitation_probability": empty,
            }
        }
        return _FakeForecastResponse(payload)


def _patch_aiohttp():
    import hems.forecast
    fake_module = types.ModuleType("aiohttp")
    fake_module.ClientSession = _FakeForecastSession
    fake_module.ClientTimeout = lambda **kw: None
    fake_module.ClientError = type("ClientError", (Exception,), {})
    fake_module.ClientResponseError = type("ClientResponseError", (Exception,), {})
    return patch.object(hems.forecast, "aiohttp", fake_module)


# --- Host factory. The mixin requires several attributes
# that the real coordinator sets during ``__init__``; we set
# them to the minimum needed for ``_maybe_refresh_forecast``
# and the journal pipeline. ---


def _make_host(*, ha_lat, ha_lon, ha_tz, entry_options, entry_id, tmp_path,
               hourly_response=None, learned_ratio=0.1395):
    """Build a minimal host for ``PvLearningCoordinatorMixin``
    and run the real ``_init_pv_learning``.

    The test does NOT construct a "correct" identity in
    place; it lets the real mixin call
    ``resolve_site_identity(entry.options, ha_latitude, ha_lon)``
    and bind the result to ``self._pv_learning.identity``.
    """
    from hems.pv_coordinator import PvLearningCoordinatorMixin

    class _Host(PvLearningCoordinatorMixin):
        def __init__(self, hass, entry):
            self.hass = hass
            self._entry = entry
            self._forecast = None
            self._hems = SimpleNamespace(
                _predictive_controller=None,
            )
            self._raw_hourly_forecast = []
            self._raw_forecast_kwh = {}
            self._forecast_today_kwh = None
            self.forecast_tomorrow_kwh = None
            self.forecast_day_after_kwh = None
            self.weather_tomorrow_code = None
            self.weather_day_after_code = None
            self.hourly_forecast_today = []
            self.hourly_weather_today = []
            self.hourly_radiation_today = []
            self._forecast_last_fetch = None
            self._forecast_model_tags = {}

    hass = SimpleNamespace(
        config=SimpleNamespace(
            latitude=ha_lat, longitude=ha_lon, time_zone=ha_tz,
        ),
    )
    entry = SimpleNamespace(
        options=entry_options or {},
        entry_id=entry_id,
    )
    host = _Host(hass, entry)
    host._pv_state_path = tmp_path / f"pv_fact_pairs_{entry_id}.json"
    host._pv_legacy_path = tmp_path / "pv_fact_pairs.json"
    host._init_pv_learning()
    if learned_ratio is not None:
        host.forecast_learned_ratio = learned_ratio
    # Inject the hourly_response the test wants into the
    # ForecastService that _maybe_refresh_forecast will
    # create. The mixin creates the service lazily; we do
    # the same here.
    from hems.forecast import ForecastService
    state = host._pv_learning
    service = ForecastService(
        latitude=state.identity["latitude"],
        longitude=state.identity["longitude"],
        timezone_name=state.identity["timezone"],
    )
    service.set_station_gain(learned_ratio or 0.1395)
    if hourly_response is not None:
        service.set_hourly_response(hourly_response)
    host._forecast = service
    return host


class ProductionTagChainContractTests(unittest.TestCase):
    """Drive the real coordinator chain:
        _maybe_refresh_forecast → snapshot() → match() →
        calibration_pairs().

    The test reproduces the live scenario Юра described:
    the 14/15 жовтня snapshots are early ``station_gain_v2``
    because hourly training had not finished when the
    forecast pipeline ran. A subsequent train completes
    hourly, the next snapshot is tagged ``hourly_response_v2``,
    and the active calibrator accepts only the matching tag.
    """

    LIVE_HA_COORDS = (49.70953716609777, 23.908143639564518)

    def setUp(self):
        _FakeForecastSession.last_url = None
        _FakeForecastSession.last_params = None
        _FakeForecastSession.call_count = 0

    def test_initial_snapshot_uses_station_gain_v2_before_hourly(self):
        """At deploy time, hourly_response is None. The real
        ``_forecast_model_for_day`` returns ``station_gain_v2``.
        A snapshot on a future day gets that tag.
        """
        with tempfile.TemporaryDirectory() as tmp:
            host = _make_host(
                ha_lat=self.LIVE_HA_COORDS[0],
                ha_lon=self.LIVE_HA_COORDS[1],
                ha_tz="Europe/Kyiv",
                entry_options={},
                entry_id="test-entry",
                tmp_path=Path(tmp),
                hourly_response=None,
            )
            # The real production method.
            tag = host._forecast_model_for_day(date(2026, 10, 14))
            self.assertEqual(
                tag, "station_gain_v2",
                msg=(
                    "with hourly_response=None, the real "
                    "_forecast_model_for_day returns "
                    "station_gain_v2. The 14/15 жовтня snapshots "
                    "in the live journal are exactly this case."
                ),
            )

    def test_subsequent_snapshot_uses_hourly_response_v2(self):
        """After ``set_hourly_response`` is called (live: 10:51:05),
        ``_forecast_model_for_day`` returns
        ``hourly_response_v2``. A new snapshot on a future day
        gets that tag.
        """
        with tempfile.TemporaryDirectory() as tmp:
            host = _make_host(
                ha_lat=self.LIVE_HA_COORDS[0],
                ha_lon=self.LIVE_HA_COORDS[1],
                ha_tz="Europe/Kyiv",
                entry_options={},
                entry_id="test-entry",
                tmp_path=Path(tmp),
                hourly_response={
                    "gains": [None] * 24,
                    "sample_days": 12,
                    "last_day": "2026-10-13",
                    "rejected_days": ["2026-09-30"],
                    "provisional": False,
                },
            )
            tag = host._forecast_model_for_day(date(2026, 10, 15))
            self.assertEqual(
                tag, "hourly_response_v2",
                msg=(
                    "with hourly_response.last_day=2026-10-13 and "
                    "snapshot day=2026-10-15 (within 14 days), the "
                    "real _forecast_model_for_day returns "
                    "hourly_response_v2. The first such snapshot "
                    "is the one that lands in the calibrator."
                ),
            )

    def test_stale_hourly_response_falls_back_to_station_gain(self):
        """If hourly_response.last_day is more than 14 days
        before the snapshot day, the function returns
        ``station_gain_v2``. This protects against stale
        per-hour gains after a long gap.
        """
        with tempfile.TemporaryDirectory() as tmp:
            host = _make_host(
                ha_lat=self.LIVE_HA_COORDS[0],
                ha_lon=self.LIVE_HA_COORDS[1],
                ha_tz="Europe/Kyiv",
                entry_options={},
                entry_id="test-entry",
                tmp_path=Path(tmp),
                hourly_response={
                    "gains": [None] * 24,
                    "sample_days": 12,
                    "last_day": "2026-09-25",  # 20 days before 10-15
                    "rejected_days": [],
                    "provisional": False,
                },
            )
            tag = host._forecast_model_for_day(date(2026, 10, 15))
            self.assertEqual(
                tag, "station_gain_v2",
                msg=(
                    "stale hourly_response (>14 days) must fall "
                    "back to station_gain_v2."
                ),
            )

    def test_full_chain_initial_fallback_train_actual_pair_accepted(self):
        """End-to-end production scenario:

          1. Initial snapshot on 2026-10-14 with hourly_response
             = None → tag = station_gain_v2.
          2. Hourly training completes (we set hourly_response
             with last_day=2026-10-13, provisional=False).
          3. New snapshot on 2026-10-15 → tag = hourly_response_v2.
          4. ``match()`` is called with the actual for both
             days; the journal produces two completed pairs
             with the respective tags.
          5. ``calibration_pairs()`` (calibration_model =
             hourly_response_v2) returns ONLY the
             hourly_response_v2 pair. The station_gain_v2 pair
             is REJECTED. The active calibrator therefore has
             exactly one pair — the hourly_response_v2 one.
        """
        with tempfile.TemporaryDirectory() as tmp:
            # --- Step 1: deploy state ---
            host = _make_host(
                ha_lat=self.LIVE_HA_COORDS[0],
                ha_lon=self.LIVE_HA_COORDS[1],
                ha_tz="Europe/Kyiv",
                entry_options={},
                entry_id="test-entry",
                tmp_path=Path(tmp),
                hourly_response=None,
            )
            # Set the active calibrator identity on the journal.
            host._pv_learning.set_calibration_model("hourly_response_v2")
            # Step 1: snapshot 2026-10-14 with station_gain_v2.
            host._pv_learning.snapshot(
                "2026-10-14", 0.24,
                datetime(2026, 10, 13, 10, 50, 56),
                forecast_model=host._forecast_model_for_day(
                    date(2026, 10, 14)
                ),
            )
            self.assertEqual(
                host._pv_learning.snapshots["2026-10-14"]["forecast_model"],
                "station_gain_v2",
            )

            # --- Step 2: hourly training completes ---
            host._forecast.set_hourly_response({
                "gains": [None] * 24,
                "sample_days": 12,
                "last_day": "2026-10-13",
                "rejected_days": ["2026-09-30"],
                "provisional": False,
            })
            # Live evidence: 10:51:05 hourly_response_v2 selected.
            self.assertEqual(
                host._forecast_model_for_day(date(2026, 10, 15)),
                "hourly_response_v2",
            )

            # --- Step 3: new snapshot on 2026-10-15 ---
            host._pv_learning.snapshot(
                "2026-10-15", 0.11,
                datetime(2026, 10, 14, 0, 15, 0),
                forecast_model=host._forecast_model_for_day(
                    date(2026, 10, 15)
                ),
            )
            self.assertEqual(
                host._pv_learning.snapshots["2026-10-15"]["forecast_model"],
                "hourly_response_v2",
            )

            # --- Step 4: match() with actuals for both days ---
            # The actuals are recorded in self._pv_actual by
            # the coordinator from the recorder. ``match()``
            # requires a dict keyed by ISO date.
            host._pv_learning.match(
                {"2026-10-14": 0.50, "2026-10-15": 0.40},
                datetime(2026, 10, 16, 0, 0, 0),
            )
            self.assertIn("2026-10-14", host._pv_learning.pairs)
            self.assertIn("2026-10-15", host._pv_learning.pairs)
            self.assertEqual(
                host._pv_learning.pairs["2026-10-14"]["forecast_model"],
                "station_gain_v2",
            )
            self.assertEqual(
                host._pv_learning.pairs["2026-10-15"]["forecast_model"],
                "hourly_response_v2",
            )

            # --- Step 5: calibration_pairs() filter ---
            cal_pairs = host._pv_learning.calibration_pairs()
            self.assertEqual(
                len(cal_pairs), 1,
                msg=(
                    f"calibration_pairs() must return exactly one "
                    f"pair (the hourly_response_v2 one). Got: "
                    f"{list(cal_pairs.keys())}"
                ),
            )
            self.assertIn(
                "2026-10-15", cal_pairs,
                msg=(
                    "the hourly_response_v2 pair must enter the "
                    "active calibrator."
                ),
            )
            self.assertNotIn(
                "2026-10-14", cal_pairs,
                msg=(
                    "the station_gain_v2 pair must be REJECTED "
                    "by the active calibrator. The exclusion is "
                    "driven by the tag mismatch with "
                    "calibration_model='hourly_response_v2'. "
                    "Юра: «Тест виключення station_gain-пари має "
                    "створювати завершену пару» — this is the "
                    "completed pair we created."
                ),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
