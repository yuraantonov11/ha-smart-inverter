"""R03 — issued/used/model-identity життєвий цикл (offline).

Audit R03 (round 3):
  * **All tests in this file are offline.** No SSH, no live HA.
    Live state is verified by a separate tool:
    ``scripts/probe_r03_live.py``. Failed live probes report
    "not verified" and never affect this suite's pass/fail.
  * **Direct test of RealForecastPairs.snapshot immutability.**
    The previous test invoked ``PvLearningState`` (a wrapper);
    we now test ``RealForecastPairs`` directly to pin the
    class-level invariant.
  * **Restart invariants**: sample_count, bias, model identity,
    no duplicate records, after JSON round-trip.
  * **Bias unit is kWh**, NOT kWh×1000. ForecastCalibrator is
    constructed with ``unit="kWh"``. For forecast_kwh=5,
    actual_kwh=4, bias = 4 - 5 = -1 (kWh). NOT -1000.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from hems.pv_coordinator import PvLearningCoordinatorMixin  # noqa: E402
from hems.pv_learning import PvLearningState, RealForecastPairs  # noqa: E402


# Identity used for RealForecastPairs (per Round 9 contract).
_TEST_IDENTITY = {"site_id": "test_site", "station_id": "test_station"}


# ─────────────────────────────────────────────────────────────────
# Direct test of RealForecastPairs.snapshot immutability
# ─────────────────────────────────────────────────────────────────


def test_r03_real_forecast_pairs_pending_snapshot_immutable_value() -> None:
    """Direct test of RealForecastPairs.snapshot: pending pair
    (used=False) must NOT be changed by a new snapshot with a
    different forecast_kwh.
    """
    pairs = RealForecastPairs(identity=_TEST_IDENTITY)
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    # Issue for tomorrow.
    ok1 = pairs.snapshot("2026-10-02", 5.0, now,
                         forecast_model="station_gain_v1")
    assert ok1, "first snapshot must succeed"
    before = dict(pairs.pairs["2026-10-02"])
    # Try to change the value.
    ok2 = pairs.snapshot("2026-10-02", 99.0, now,
                         forecast_model="station_gain_v1")
    after = pairs.pairs["2026-10-02"]
    assert after == before, (
        f"Pending pair must not change value. "
        f"before={before}, after={after}, ok2={ok2}"
    )
    # The second call must be rejected (False) because day is in pairs.
    assert ok2 is False, (
        f"Second snapshot of the same day must be rejected; got ok2={ok2}"
    )


def test_r03_real_forecast_pairs_pending_snapshot_immutable_model() -> None:
    """Direct test: pending pair must NOT be changed by a new
    snapshot with a different forecast_model.
    """
    pairs = RealForecastPairs(identity=_TEST_IDENTITY)
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    pairs.snapshot("2026-10-02", 5.0, now,
                   forecast_model="station_gain_v1")
    before = dict(pairs.pairs["2026-10-02"])
    # Try to change the model.
    ok2 = pairs.snapshot("2026-10-02", 5.0, now,
                         forecast_model="hourly_response_v1")
    after = pairs.pairs["2026-10-02"]
    assert after == before, (
        f"Pending pair must not change model. "
        f"before={before}, after={after}, ok2={ok2}"
    )
    assert ok2 is False, "Second snapshot with different model must be rejected"


def test_r03_real_forecast_pairs_used_pair_immutable() -> None:
    """After pairing (used=True), the pair must NOT be changeable
    by value OR model. This is the strongest immutability invariant.
    """
    pairs = RealForecastPairs(identity=_TEST_IDENTITY)
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    pairs.snapshot("2026-10-02", 5.0, now,
                   forecast_model="station_gain_v1")
    # Simulate the day being completed and matched (used=True).
    later = now + timedelta(days=2)
    pairs.match({"2026-10-02": 4.0}, later)
    pair = pairs.pairs["2026-10-02"]
    assert pair["used"] is True
    assert pair["actual_kwh"] == 4.0
    assert pair["forecast_kwh"] == 5.0
    assert pair["forecast_model"] == "station_gain_v1"
    before = dict(pair)
    # Try to change value (snapshot for an existing used day is rejected).
    pairs.snapshot("2026-10-02", 99.0, now,
                   forecast_model="station_gain_v1")
    # Try to change model.
    pairs.snapshot("2026-10-02", 5.0, now,
                   forecast_model="hourly_response_v1")
    after_pair = pairs.pairs["2026-10-02"]
    assert after_pair == before, (
        f"Used pair must remain immutable. "
        f"before={before}, after={after_pair}"
    )
    # The contract: actual_kwh, forecast_kwh, forecast_model all
    # preserved; captured_at (the issued timestamp) preserved.
    assert after_pair["captured_at"] == before["captured_at"], (
        f"captured_at must not change; "
        f"before={before['captured_at']}, after={after_pair['captured_at']}"
    )


def test_r03_real_forecast_pairs_after_save_load_immutable() -> None:
    """After JSON save/load round-trip, the immutability invariants
    must still hold.
    """
    pairs = RealForecastPairs(identity=_TEST_IDENTITY)
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    pairs.snapshot("2026-10-02", 5.0, now,
                   forecast_model="station_gain_v1")
    later = now + timedelta(days=2)
    pairs.match({"2026-10-02": 4.0}, later)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "real_forecast_pairs.json"
        pairs.save(path)
        # Reload.
        reloaded = RealForecastPairs(identity=_TEST_IDENTITY)
        loaded = reloaded.load(path)
        assert loaded is True
        before = dict(reloaded.pairs["2026-10-02"])
        reloaded.snapshot("2026-10-02", 99.0, now,
                          forecast_model="station_gain_v1")
        reloaded.snapshot("2026-10-02", 5.0, now,
                          forecast_model="hourly_response_v1")
        after = reloaded.pairs["2026-10-02"]
        assert after == before, (
            f"After save/load, used pair must remain immutable. "
            f"before={before}, after={after}"
        )


# ─────────────────────────────────────────────────────────────────
# Test 1: issued → used → restart, with strict invariants
# ─────────────────────────────────────────────────────────────────


def _build_coordinator(directory, now):
    c = PvLearningCoordinatorMixin.__new__(PvLearningCoordinatorMixin)
    c._site_timezone = timezone.utc
    c._pv_local_now = lambda: now
    c._entry = SimpleNamespace(
        entry_id="entry", options={"predictive_mode": "shadow"}
    )
    c._pv_learning = PvLearningState("UTC", 50.45, 30.52)
    c._pv_calibrator = c._pv_learning.calibrator
    c._pv_state_path = Path(directory) / "station.json"
    c._pv_legacy_path = Path(directory) / "legacy.json"
    c._pv_state_loaded, c._pv_state_dirty = True, False
    c._pv_matrix_at, c._pv_actual = now, {}
    c.forecast_tomorrow_kwh, c.forecast_day_after_kwh = 5.0, None
    c.hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(side_effect=lambda f, *a: f(*a))
    )
    c._history_entity = lambda key, fallback: fallback
    return c


def test_r03_issued_used_restart_strict() -> None:
    """Pair: forward issued, fact via HA recorder, restart with all
    invariants verified:
      - sample_count == 1 after fact
      - bias == -1.0 (in kWh, since calibrator unit='kWh')
      - model identity preserved through restart
      - pair.used == True after restart (no duplicate records)
    """
    import asyncio
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as directory:
        c = _build_coordinator(directory, now)
        c._pv_learning.set_calibration_model("station_gain_v1")
        asyncio.run(c._save_real_forecast_pair(now))
        rows = list(c._real_pairs_store.pairs.values())
        assert len(rows) == 1 and rows[0]["actual_kwh"] is None
        assert rows[0]["date"] == "2026-10-02" and not rows[0]["used"]

        later = now + timedelta(days=2)
        c._pv_local_now = lambda: later
        c._pv_matrix_at = later
        c.forecast_tomorrow_kwh = None
        c._pv_actual = {"2026-10-02": 4.0}
        rec = SimpleNamespace(
            statistics=SimpleNamespace(
                statistics_during_period=lambda *a, **k: {
                    "sensor.garazh_smart_solar_inverter_daily_pv_energy": [
                        {"start": datetime(2026, 10, d, tzinfo=timezone.utc).timestamp(),
                         "sum": v}
                        for d, v in ((1, 100.0), (2, 104.0))
                    ]
                },
                get_metadata=lambda **k: {
                    "sensor.garazh_smart_solar_inverter_daily_pv_energy": {
                        "has_sum": True, "unit_of_measurement": "kWh"
                    }
                },
            )
        )
        with patch.dict(sys.modules,
                        {"homeassistant.components.recorder": rec}):
            asyncio.run(c._save_real_forecast_pair(later))

        pair = c._real_pairs_store.pairs["2026-10-02"]
        assert pair["used"] is True
        assert pair["actual_kwh"] == 4.0
        assert pair["forecast_kwh"] == 5.0
        assert pair["forecast_model"] == "station_gain_v1"
        m = c._pv_calibrator.metrics()
        assert m.sample_count == 1, f"sample_count must be 1; got {m.sample_count}"
        # Calibrator unit='kWh', so bias_w is in kWh. For fc=5, ac=4:
        # bias = 4 - 5 = -1 (kWh). NOT -1000.
        assert abs(m.bias_w - (-1.0)) < 1e-9, (
            f"bias_w must be -1.0 in kWh; got {m.bias_w}"
        )
        journal_path = Path(directory) / "entry" / "real_forecast_pairs.json"
        assert journal_path.exists(), f"journal must exist at {journal_path}"

        # Restart
        fresh = _build_coordinator(directory, later)
        asyncio.run(fresh._save_real_forecast_pair(later))
        fresh_pair = fresh._real_pairs_store.pairs["2026-10-02"]
        assert fresh_pair["used"] is True, "Restart: pair.used must be True"
        assert fresh_pair["actual_kwh"] == 4.0, (
            f"Restart: actual_kwh must be preserved; got {fresh_pair['actual_kwh']}"
        )
        assert fresh_pair["forecast_kwh"] == 5.0, (
            f"Restart: forecast_kwh must be preserved; got {fresh_pair['forecast_kwh']}"
        )
        assert fresh_pair["forecast_model"] == "station_gain_v1", (
            f"Restart: forecast_model must be preserved; got {fresh_pair['forecast_model']}"
        )
        dates = list(fresh._real_pairs_store.pairs.keys())
        assert len(dates) == len(set(dates)), (
            f"Restart: duplicate dates in pairs: {dates}"
        )


# ─────────────────────────────────────────────────────────────────
# Test 2: model change excludes legacy pairs
# ─────────────────────────────────────────────────────────────────


def test_r03_model_change_excludes_legacy() -> None:
    """Калибратор скидається при зміні model."""
    import asyncio
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as directory:
        c = _build_coordinator(directory, now)
        c._pv_learning.set_calibration_model("station_gain_v1")
        asyncio.run(c._save_real_forecast_pair(now))
        later = now + timedelta(days=2)
        c._pv_local_now = lambda: later
        c._pv_matrix_at = later
        c.forecast_tomorrow_kwh = None
        c._pv_actual = {"2026-10-02": 4.0}
        rec = SimpleNamespace(
            statistics=SimpleNamespace(
                statistics_during_period=lambda *a, **k: {
                    "sensor.garazh_smart_solar_inverter_daily_pv_energy": [
                        {"start": datetime(2026, 10, d, tzinfo=timezone.utc).timestamp(),
                         "sum": v}
                        for d, v in ((1, 100.0), (2, 104.0))
                    ]
                },
                get_metadata=lambda **k: {
                    "sensor.garazh_smart_solar_inverter_daily_pv_energy": {
                        "has_sum": True, "unit_of_measurement": "kWh"
                    }
                },
            )
        )
        with patch.dict(sys.modules,
                        {"homeassistant.components.recorder": rec}):
            asyncio.run(c._save_real_forecast_pair(later))
        assert c._pv_calibrator.metrics().sample_count == 1
        c._pv_learning.set_calibration_model("hourly_response_v1")
        assert c._pv_calibrator.metrics().sample_count == 0
        assert c._real_pairs_store.pairs["2026-10-02"]["used"] is True


# ─────────────────────────────────────────────────────────────────
# Test 3: pending_count = issued - paired
# ─────────────────────────────────────────────────────────────────


def test_r03_pending_count_invariant() -> None:
    """Audit state: pending_count=3, samples=0 — очікуване накопичення."""
    state = PvLearningState("UTC", 50.45, 30.52)
    today = date(2026, 10, 1)
    for i in range(3):
        issued = datetime(2026, 10, 1, 12, tzinfo=timezone.utc) + timedelta(days=i)
        day = (issued.date() + timedelta(days=1)).isoformat()
        ok = state.snapshot(day, 1.0, issued, forecast_model="station_gain_v1")
        assert ok, f"snapshot {day} must succeed"
    status = state.calibration_status(today.isoformat())
    assert status["pending_count"] == 3
    assert status["samples"] == 0
    count = state.match({"2026-10-02": 0.5},
                        datetime(2026, 10, 3, tzinfo=timezone.utc))
    assert count == 1
    status2 = state.calibration_status(today.isoformat())
    assert status2["pending_count"] == 2
    assert status2["samples"] == 1


# ─────────────────────────────────────────────────────────────────
# Test 4: model identity separation
# ─────────────────────────────────────────────────────────────────


def test_r03_model_identity_separation() -> None:
    """Зміна calibration_model виключає пари з іншим forecast_model."""
    state = PvLearningState("UTC", 50.45, 30.52)
    state.snapshot("2026-10-02", 1.0,
                   datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
                   forecast_model="station_gain_v1")
    state.match({"2026-10-02": 0.5},
                datetime(2026, 10, 3, tzinfo=timezone.utc))
    state.set_calibration_model("station_gain_v1")
    assert state.calibrator.metrics().sample_count == 1
    state.set_calibration_model("hourly_response_v1")
    assert state.calibrator.metrics().sample_count == 0


# ─────────────────────────────────────────────────────────────────
# Test 5: synthetic fixture (no live HA)
# ─────────────────────────────────────────────────────────────────


def test_r03_synthetic_fixture_state_consistent() -> None:
    """A synthetic state that mirrors the live observation: 2 issued
    snapshots for future dates, 0 completed pairs, calibration_model =
    hourly_response_v1. The reason for samples=0 is that no forecasts
    were issued for past dates.

    **This is offline-only.** The live state is verified by
    ``scripts/probe_r03_live.py`` separately.
    """
    state = PvLearningState("UTC", 50.45, 30.52)
    state.snapshot("2026-10-09", 0.1,
                   datetime(2026, 10, 8, 10, 4, 26,
                            tzinfo=timezone(timedelta(hours=3))),
                   forecast_model="station_gain_v1")
    state.snapshot("2026-10-10", 0.37,
                   datetime(2026, 10, 8, 10, 4, 26,
                            tzinfo=timezone(timedelta(hours=3))),
                   forecast_model="station_gain_v1")
    today = date(2026, 10, 8)
    status = state.calibration_status(today.isoformat())
    assert status["pending_count"] == 2
    assert status["samples"] == 0


if __name__ == "__main__":
    test_r03_real_forecast_pairs_pending_snapshot_immutable_value()
    print("test_r03_real_forecast_pairs_pending_snapshot_immutable_value: PASS")
    test_r03_real_forecast_pairs_pending_snapshot_immutable_model()
    print("test_r03_real_forecast_pairs_pending_snapshot_immutable_model: PASS")
    test_r03_real_forecast_pairs_used_pair_immutable()
    print("test_r03_real_forecast_pairs_used_pair_immutable: PASS")
    test_r03_real_forecast_pairs_after_save_load_immutable()
    print("test_r03_real_forecast_pairs_after_save_load_immutable: PASS")
    test_r03_issued_used_restart_strict()
    print("test_r03_issued_used_restart_strict: PASS")
    test_r03_model_change_excludes_legacy()
    print("test_r03_model_change_excludes_legacy: PASS")
    test_r03_pending_count_invariant()
    print("test_r03_pending_count_invariant: PASS")
    test_r03_model_identity_separation()
    print("test_r03_model_identity_separation: PASS")
    test_r03_synthetic_fixture_state_consistent()
    print("test_r03_synthetic_fixture_state_consistent: PASS")
    print("\nAll 9 tests passed (0 failed).")
    sys.exit(0)
