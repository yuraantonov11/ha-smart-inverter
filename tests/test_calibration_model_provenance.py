"""Changing forecast pipelines must not inherit bias or fake live evidence."""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from hems.pv_learning import PvLearningState, RealForecastPairs
from hems.pv_coordinator import PvLearningCoordinatorMixin
from pv_test_support import Checks

check = Checks()
now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
state = PvLearningState('UTC', 50., 30.)
store = RealForecastPairs(state.identity)
for i in range(4):
    issued = now+timedelta(days=i)
    store.snapshot((issued.date()+timedelta(days=1)).isoformat(), 5., issued)
store.match({d: 2. for d in store.pairs}, now+timedelta(days=6))
store.publish(state)
check(state.calibrator.metrics().bias_w == -3., 'legacy evidence retained before model selection')
original = dict(store.pairs['2026-10-02'])
state.set_calibration_model('hourly_response_v1')
store.publish(state)
check(len(state.calibrator) == 0, 'legacy bias excluded from new hourly pipeline')
check(store.pairs['2026-10-02'] == original, 'original forecast and fact unchanged')
for i in range(4):
    issued = now+timedelta(days=i+7)
    day = (issued.date()+timedelta(days=1)).isoformat()
    store.snapshot(day, 6., issued, forecast_model='hourly_response_v1')
store.match({d: 5. for d in store.pairs}, now+timedelta(days=13))
store.publish(state)
check(len(state.calibrator) == 4 and state.calibrator.adjust(6.) == 5., 'same pipeline learns real bias')
store.publish(state)
check(len(state.calibrator) == 4, 'repeated publication cannot duplicate samples')
store.snapshot('2026-10-16', 7., now+timedelta(days=14), forecast_model='hourly_response_v1')
store.publish(state)
status = state.calibration_status('2026-10-15')
check(status['excluded_model_pairs'] == 4 and status['samples'] == 4, 'diagnostics distinguish retained and active evidence')
check(status['pending_count'] == 1 and status['pending'][0]['awaiting'] == 'completed_day', 'pending forecast visible without fabricated fact')
check(status['bias_kwh'] == -1. and status['unit'] == 'kWh', 'diagnostics label daily units')
check(state.calibration_status('2026-10-17')['pending'][0]['awaiting'] == 'daily_fact', 'delayed daily facts visible')
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory)/'state.json'
    state.save(path)
    restored = PvLearningState('UTC', 50., 30.)
    restored.load(path)
    check(restored.calibration_model == 'hourly_response_v1' and len(restored.calibrator) == 4, 'restart restores model scope')
    journal = Path(directory)/'journal.json'
    store.save(journal)
    fresh = RealForecastPairs(state.identity)
    fresh.load(journal)
    fresh.publish(restored)
    check(restored.calibration_status('2026-10-15') == status, 'journal roundtrip preserves provenance and diagnostics')
    migrated = RealForecastPairs(state.identity)
    migrated.migrate(restored)
    check(migrated.pairs == fresh.pairs, 'migration retains model tags')
state.set_calibration_model('station_gain_v1')
check(len(state.calibrator) == 0, 'daily fallback cannot use hourly bias')
c = PvLearningCoordinatorMixin.__new__(PvLearningCoordinatorMixin)
c._forecast = SimpleNamespace(hourly_response={'last_day': '2026-10-02'})
check(c._forecast_model_for_day(now.date()+timedelta(days=2)) == 'hourly_response_v1', 'fresh hourly model identified')
check(c._forecast_model_for_day(now.date()+timedelta(days=17)) == 'station_gain_v1', 'expired hourly model identified as fallback')
state.set_calibration_model('hourly_response_v1')
c._pv_learning, c._pv_calibrator = state, state.calibrator
c._pv_local_now = lambda: datetime(2026, 10, 15, tzinfo=timezone.utc)
c._raw_forecast_kwh = {'2026-10-16': 6., '2026-10-17': 6.}
c.forecast_tomorrow_kwh = c.forecast_day_after_kwh = 6.
c._adjust_daily_forecasts()
check(c.forecast_tomorrow_kwh == 5. and c.forecast_day_after_kwh == 6., 'mixed horizons apply bias only to matching pipeline')
check(not store.snapshot('2026-10-02', 99., now, forecast_model='hourly_response_v1'), 'changing model cannot overwrite issued legacy forecast')
check.finish()
