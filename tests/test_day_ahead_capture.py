"""Keep day-ahead forecasts separate from existing longer-horizon evidence."""
import asyncio
from datetime import datetime, timedelta, timezone
from test_real_pair_capture import fixture


def test_real_journal_captures_each_date_when_it_becomes_tomorrow(tmp_path):
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    coordinator = fixture(tmp_path, now)
    coordinator.forecast_tomorrow_kwh = 5
    coordinator.forecast_day_after_kwh = 8
    asyncio.run(coordinator._save_real_forecast_pair(now))
    rows = coordinator._real_pairs_store.pairs
    assert set(rows) == {"2026-10-02"}
    assert rows["2026-10-02"]["forecast_kwh"] == 5
    later = now + timedelta(days=1)
    coordinator._pv_local_now = lambda: later
    coordinator._pv_matrix_at = later
    coordinator.forecast_tomorrow_kwh = 9
    coordinator.forecast_day_after_kwh = 10
    asyncio.run(coordinator._save_real_forecast_pair(later))
    assert set(rows) == {"2026-10-02", "2026-10-03"}
    assert rows["2026-10-03"]["forecast_kwh"] == 9
    assert rows["2026-10-03"]["captured_at"] == later.isoformat()


def test_existing_longer_horizon_snapshot_stays_immutable(tmp_path):
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    coordinator = fixture(tmp_path, now)
    coordinator._pv_learning.snapshot("2026-10-03", 8, now)
    asyncio.run(coordinator._save_real_forecast_pair(now))
    later = now + timedelta(days=1)
    coordinator._pv_local_now = lambda: later
    coordinator._pv_matrix_at = later
    coordinator.forecast_tomorrow_kwh = 9
    asyncio.run(coordinator._save_real_forecast_pair(later))
    row = coordinator._real_pairs_store.pairs["2026-10-03"]
    assert row["forecast_kwh"] == 8
    assert row["captured_at"] == now.isoformat()
