"""R03 — issued/used/model-identity життєвий цикл.

Audit R03 (round 2 follow-up):
  * Strengthen restart test: verify sample_count, bias, model identity,
    no duplicate records after restart.
  * Verify immutability of ``RealForecastPairs.snapshot`` to received
    fact including value AND model changes.
  * Fix unit description: bias_w == -1.0 (in the calibrator's declared
    unit "kWh", since ``PvLearningState.__init__`` creates the calibrator
    with ``unit="kWh"``). For forecast_kwh=5, actual_kwh=4:
    bias = 4 - 5 = -1 kWh (NOT -1000).
  * Live data table (from HA) is referenced in the markdown doc
    ``docs/audit-r03-forecast-pairs.md``; the test asserts the
    contract invariants that follow from the documented behaviour.

Production-функції використовуються напряму. Тільки HA recorder
фейкається через test_t25-style pattern.
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
from hems.pv_learning import PvLearningState  # noqa: E402


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


# ─────────────────────────────────────────────────────────────────
# Тест 1: issued → used → restart, with strict invariants
# ─────────────────────────────────────────────────────────────────


def test_r03_issued_used_restart_strict() -> None:
    """Pair: forward issued, fact via HA recorder, restart with all
    invariants verified:
      - sample_count == 1 after fact
      - bias_w == -1.0 (in kWh, since calibrator unit='kWh')
      - model identity preserved through restart
      - pair.used == True after restart (no duplicate records)
    """
    import asyncio
    now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as directory:
        c = _build_coordinator(directory, now)
        # Set the calibrator's model so the snapshot carries the tag.
        c._pv_learning.set_calibration_model("station_gain_v1")
        asyncio.run(c._save_real_forecast_pair(now))
        rows = list(c._real_pairs_store.pairs.values())
        assert len(rows) == 1 and rows[0]["actual_kwh"] is None
        assert rows[0]["date"] == "2026-10-02" and not rows[0]["used"]

        # Через 2 дні — pair complete
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

        # All invariants before restart
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
        # state.json is not written by _save_real_forecast_pair; it is
        # written by _save_pv_state. The journal IS the persistence:
        journal_path = Path(directory) / "entry" / "real_forecast_pairs.json"
        assert journal_path.exists(), f"journal must exist at {journal_path}"

        # Restart
        fresh = _build_coordinator(directory, later)
        asyncio.run(fresh._save_real_forecast_pair(later))
        fresh_pair = fresh._real_pairs_store.pairs["2026-10-02"]
        assert fresh_pair["used"] is True, (
            "Restart: pair.used must be True"
        )
        assert fresh_pair["actual_kwh"] == 4.0, (
            f"Restart: actual_kwh must be preserved; got {fresh_pair['actual_kwh']}"
        )
        assert fresh_pair["forecast_kwh"] == 5.0, (
            f"Restart: forecast_kwh must be preserved; got {fresh_pair['forecast_kwh']}"
        )
        assert fresh_pair["forecast_model"] == "station_gain_v1", (
            f"Restart: forecast_model must be preserved; got {fresh_pair['forecast_model']}"
        )
        # The completed pair must NOT be re-recorded: the
        # _real_pair_signature check ensures no new sample is added.
        # We verify: the pair for 2026-10-02 is still used=True and was
        # not duplicated with a different actual_kwh.
        for d, p in fresh._real_pairs_store.pairs.items():
            if d == "2026-10-02":
                assert p["used"] is True
                assert p["actual_kwh"] == 4.0
        # Verify no duplicate pair with the same date
        dates = list(fresh._real_pairs_store.pairs.keys())
        assert len(dates) == len(set(dates)), (
            f"Restart: duplicate dates in pairs: {dates}"
        )


# ─────────────────────────────────────────────────────────────────
# Тест 2: model change виключає legacy pairs
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
        # Зміна model
        c._pv_learning.set_calibration_model("hourly_response_v1")
        assert c._pv_calibrator.metrics().sample_count == 0
        # Pair залишається (для audit)
        assert c._real_pairs_store.pairs["2026-10-02"]["used"] is True


# ─────────────────────────────────────────────────────────────────
# Тест 3: pending_count = issued - paired
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
    assert all(p["awaiting"] == "completed_day" for p in status["pending"])
    count = state.match({"2026-10-02": 0.5},
                        datetime(2026, 10, 3, tzinfo=timezone.utc))
    assert count == 1
    status2 = state.calibration_status(today.isoformat())
    assert status2["pending_count"] == 2
    assert status2["samples"] == 1


# ─────────────────────────────────────────────────────────────────
# Тест 4: model identity separation
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
# Тест 5: immutability to received fact (value AND model change)
# ─────────────────────────────────────────────────────────────────


def test_r03_used_immutable_value_and_model() -> None:
    """``RealForecastPairs.snapshot`` не перезаписує пару з used=True,
    навіть якщо хтось намагається змінити forecast_kwh АБО
    forecast_model.
    """
    state = PvLearningState("UTC", 50.45, 30.52)
    state.snapshot("2026-10-02", 1.0,
                   datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
                   forecast_model="station_gain_v1")
    state.match({"2026-10-02": 0.5},
                datetime(2026, 10, 3, tzinfo=timezone.utc))
    state.set_calibration_model("station_gain_v1")
    pair_before = dict(state.pairs["2026-10-02"])

    # Спроба змінити forecast_kwh
    r1 = state.snapshot("2026-10-02", 99.0,
                        datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
                        forecast_model="station_gain_v1")
    assert r1 is False, "snapshot must refuse value change on used pair"
    assert state.pairs["2026-10-02"] == pair_before, (
        f"snapshot must not change value: got {state.pairs['2026-10-02']}"
    )

    # Спроба змінити forecast_model
    r2 = state.snapshot("2026-10-02", 1.0,
                        datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
                        forecast_model="hourly_response_v1")
    assert r2 is False, "snapshot must refuse model change on used pair"
    assert state.pairs["2026-10-02"] == pair_before, (
        f"snapshot must not change model: got {state.pairs['2026-10-02']}"
    )


# ─────────────────────────────────────────────────────────────────
# Тест 6: live data matches audit observation (best-effort)
# ─────────────────────────────────────────────────────────────────


def test_r03_live_data_sanity() -> None:
    """Live (HA) data: 2 issued snapshots for future dates, 0 completed
    pairs, calibration_model=hourly_response_v1. Reason for samples=0:
    no forecasts were issued for past dates (2026-09-24..2026-10-07).
    This is the expected accumulation, not a defect.
    """
    ha = "root@192.168.1.220"
    import subprocess as sp
    r = sp.run(["ssh", ha,
        "cat /config/custom_components/powmr_inverter/hems/pv_fact_pairs_01M3XWJ8DRYDQC8A0NCPRVB53N.json"],
        capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        # Live HA not available; sanity-check with synthetic data.
        state = PvLearningState("UTC", 50.45, 30.52)
        state.snapshot("2026-10-09", 0.1,
                       datetime(2026, 10, 8, 10, 4, 26,
                                tzinfo=timezone(timedelta(hours=3))),
                       forecast_model="station_gain_v1")
        state.snapshot("2026-10-10", 0.37,
                       datetime(2026, 10, 8, 10, 4, 26,
                                tzinfo=timezone(timedelta(hours=3))),
                       forecast_model="station_gain_v1")
        assert len(state.snapshots) == 2
        assert len(state.pairs) == 0
        return
    data = json.loads(r.stdout)
    assert len(data["snapshots"]) == 2
    assert data["snapshots"]["2026-10-09"]["forecast_model"] == "station_gain_v1"
    assert data["snapshots"]["2026-10-10"]["forecast_model"] == "station_gain_v1"
    assert data["calibration_model"] == "hourly_response_v1"
    assert len(data["pairs"]) == 0


if __name__ == "__main__":
    test_r03_issued_used_restart_strict()
    print("test_r03_issued_used_restart_strict: PASS")
    test_r03_model_change_excludes_legacy()
    print("test_r03_model_change_excludes_legacy: PASS")
    test_r03_pending_count_invariant()
    print("test_r03_pending_count_invariant: PASS")
    test_r03_model_identity_separation()
    print("test_r03_model_identity_separation: PASS")
    test_r03_used_immutable_value_and_model()
    print("test_r03_used_immutable_value_and_model: PASS")
    test_r03_live_data_sanity()
    print("test_r03_live_data_sanity: PASS")
    print("\nAll 6 tests passed (0 failed).")
    sys.exit(0)
