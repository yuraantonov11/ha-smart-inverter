"""Exercise the real journal/executor/matcher with dated, complete evidence."""
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from hems.pv_coordinator import PvLearningCoordinatorMixin
from hems.pv_learning import PvLearningState, RealForecastPairs, complete_hourly_days, day_bounds
from pv_test_support import Checks, kyiv_2026


def fixture(directory, now):
    c = PvLearningCoordinatorMixin.__new__(PvLearningCoordinatorMixin)
    c._site_timezone = timezone.utc
    c._pv_local_now = lambda: now
    c._entry = SimpleNamespace(entry_id='entry', options={'predictive_mode': 'shadow'})
    c._pv_learning = PvLearningState('UTC', 50.45, 30.52)
    c._pv_calibrator = c._pv_learning.calibrator
    c._pv_state_path = Path(directory) / 'station.json'
    c._pv_legacy_path = Path(directory) / 'legacy.json'
    c._pv_state_loaded, c._pv_state_dirty = True, False
    c._pv_matrix_at, c._pv_actual = now, {}
    c.forecast_tomorrow_kwh, c.forecast_day_after_kwh = 5., None
    c.hass = SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=lambda f, *a: f(*a)))
    c._history_entity = lambda key, fallback: fallback
    return c


async def exercise(check):
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as directory:
        c = fixture(directory, now)
        await c._save_real_forecast_pair(now)
        rows = list(c._real_pairs_store.pairs.values())
        check(len(rows) == 1 and rows[0]['actual_kwh'] is None, 'forecast saved before fact exists')
        check(rows[0]['date'] == '2026-10-02' and not rows[0]['used'], 'target date and unused marker')
        check(c._real_pairs_path == Path(directory)/'entry'/'real_forecast_pairs.json', 'per-entry path')
        check(c.hass.async_add_executor_job.await_count == 2, 'load and atomic save run in executor')
        persisted = json.loads(c._real_pairs_path.read_text())
        check(persisted['version'] == 1 and len(persisted['pairs']) == 1, 'versioned JSON persisted')
        c.forecast_tomorrow_kwh = 9.
        await c._save_real_forecast_pair(now)
        check(len(c._real_pairs_store.pairs) == 1 and rows[0]['forecast_kwh'] == 5., 'same date cannot overwrite first forecast')
        await c._save_real_forecast_pair(now)
        check(c.hass.async_add_executor_job.await_count == 2, 'duplicate cycle does not rewrite journal')

        later = now + timedelta(days=2)
        c._pv_local_now = lambda: later
        c._pv_matrix_at = later
        c.forecast_tomorrow_kwh = None
        c._pv_actual = {'2026-10-02': 4.}
        rec = ModuleType('homeassistant.components.recorder')
        ent = 'sensor.garazh_smart_solar_inverter_daily_pv_energy'
        daily_rows = [{'start': datetime(2026, 10, day, tzinfo=timezone.utc).timestamp(), 'sum': total}
                      for day, total in ((1, 100.), (2, 104.))]
        rec.statistics = SimpleNamespace(statistics_during_period=Mock(return_value={ent: daily_rows}),
                                        get_metadata=Mock(return_value={ent: {'has_sum': True, 'unit_of_measurement': 'kWh'}}))
        with patch.dict(sys.modules, {'homeassistant.components.recorder': rec}), \
                patch.object(c._pv_calibrator, 'record', wraps=c._pv_calibrator.record) as record:
            await c._save_real_forecast_pair(later)
            check(record.call_count == 1 and record.call_args.kwargs == {'forecast_w': 5., 'actual_w': 4.}, 'one record call in kWh')
            await c._save_real_forecast_pair(later)
            check(record.call_count == 1, 'completed date never recorded twice')
        check(rec.statistics.statistics_during_period.call_args.args[4] == 'day', 'recorder daily sum query')
        row = c._real_pairs_store.pairs['2026-10-02']
        check(row['used'] and row['actual_kwh'] == 4., 'fact and used marker persisted')
        check(c._pv_calibrator.metrics().bias_w == -1., 'cumulative sum becomes a delta, not 104 kWh')
        fresh = fixture(directory, later)
        fresh.forecast_tomorrow_kwh = None
        await fresh._save_real_forecast_pair(later)
        check(len(fresh._pv_calibrator) == 1 and fresh._pv_calibrator.metrics().bias_w == -1., 'restart restores evidence')
        fresh._pv_actual = c._pv_actual
        fresh._pv_matrix_at = later + timedelta(hours=1)
        await fresh._save_real_forecast_pair(later)
        check(len(fresh._pv_calibrator) == 1, 'restart deduplicates completed pair')

    with tempfile.TemporaryDirectory() as directory:
        c = fixture(directory, now)
        c.hass.async_add_executor_job.side_effect = lambda f, *a: (_ for _ in ()).throw(OSError('disk full')) if f.__name__ == 'save' else f(*a)
        await c._save_real_forecast_pair(now)
        check(not c._real_pairs_path.exists() and len(c._pv_calibrator) == 0, 'failed write cannot publish evidence')
        check(getattr(c, '_last_real_pair_date', None) is None, 'failed write does not consume day guard')
        c.hass.async_add_executor_job.side_effect = lambda f, *a: f(*a)
        await c._save_real_forecast_pair(now)
        check(c._real_pairs_path.exists(), 'executor failure retried same day')
        later = now + timedelta(days=2)
        c._pv_local_now = lambda: later
        c.forecast_tomorrow_kwh = None
        c._pv_matrix_at = later
        await c._save_real_forecast_pair(later)
        check(len(c._pv_calibrator) == 0, 'missing recorder facts never become zero')
        c._pv_matrix_at = later + timedelta(hours=1)
        c._pv_actual = {'2026-10-02': 0.}
        c.hass.async_add_executor_job.side_effect = lambda f, *a: (_ for _ in ()).throw(OSError('disk full')) if f.__name__ == 'save' else f(*a)
        await c._save_real_forecast_pair(later)
        check(len(c._pv_calibrator) == 0 and not c._real_pairs_store.pairs['2026-10-02']['used'], 'failed completed-pair save rolls back')
        c.hass.async_add_executor_job.side_effect = lambda f, *a: f(*a)
        await c._save_real_forecast_pair(later)
        check(len(c._pv_calibrator) == 1 and c._real_pairs_store.pairs['2026-10-02']['actual_kwh'] == 0., 'delayed measured zero retried honestly')

    with tempfile.TemporaryDirectory() as directory:
        c = fixture(directory, now)
        c._pv_learning.snapshot('2026-10-02', 3., now)
        c._pv_learning.match({'2026-10-02': 2.}, now + timedelta(days=2))
        c._pv_local_now = lambda: now + timedelta(days=2)
        c.forecast_tomorrow_kwh = None
        await c._save_real_forecast_pair(now)
        check(len(c._pv_calibrator) == 1 and c._real_pairs_store.pairs['2026-10-02']['used'], 'migration keeps completed samples exactly once')
        check(c._real_pairs_store.pairs['2026-10-02']['captured_at'] == now.isoformat(), 'migration preserves issued timestamp')
        saved = c._real_pairs_path.read_text()
        c._real_pairs_path.write_text('{"version":999}')
        fresh = fixture(directory, now)
        await fresh._save_real_forecast_pair(now)
        check(c._real_pairs_path.read_text() == '{"version":999}' and not hasattr(fresh, '_real_pairs_store'), 'corrupt journal never overwritten')
        c._real_pairs_path.write_text(saved)

    store = RealForecastPairs({'timezone': 'UTC'})
    for age in range(100):
        day = now.date()-timedelta(days=age)
        store.snapshot(day.isoformat(), 1., now-timedelta(days=age+1))
    store.prune(now.date())
    check(len(store.pairs) <= 90 and (now.date()-timedelta(days=91)).isoformat() not in store.pairs, '91-day record pruned and cap enforced')
    check(not store.snapshot('2026-10-02', float('nan'), now), 'nonfinite forecast rejected')
    state = PvLearningState('UTC', 50.45, 30.52)
    check(state.snapshot('2026-10-02', 5., now) and not state.snapshot('2026-10-02', 99., now), 'station snapshots also keep first forecast')
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)/'pairs.json'
        store.save(path)
        original = path.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('rename failed')):
            try:
                store.save(path)
                check(False, 'rename failure raised')
            except OSError:
                check(True, 'rename failure raised')
        check(path.read_bytes() == original, 'failed rename preserves original JSON')
        store.save(path)
        check(not path.with_suffix('.json.tmp').exists(), 'atomic retry replaces temporary file')
    tz = kyiv_2026()
    day = '2026-10-25'
    first, last = day_bounds(day, tz)
    rows = [{'start': (first+timedelta(hours=h)).timestamp(), 'mean': 1000.}
            for h in range(int((last-first).total_seconds()/3600))]
    actual = complete_hourly_days(rows, tz, datetime(2026, 10, 26).date())
    check(actual[day] == 25., 'DST fact integrates all 25 elapsed hours')
    check(not complete_hourly_days(rows[:-1], tz, datetime(2026, 10, 26).date()), 'incomplete DST fact rejected')


if __name__ == '__main__':
    check = Checks()
    asyncio.run(exercise(check))
    check.finish()
