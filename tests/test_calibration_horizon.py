"""Only locally issued day-ahead forecasts may train the calibrator."""
from datetime import datetime, timezone
from hems.pv_learning import PvLearningState


def test_horizon_scope_preserves_history_and_survives_restart(tmp_path):
    state = PvLearningState("Europe/Kyiv", 50.0, 30.0)
    state.set_calibration_model("model")
    # Both issued at 23:00 UTC on Oct 1 = Oct 2 in Kyiv.
    issued = datetime(2026, 10, 1, 23, tzinfo=timezone.utc)
    assert state.snapshot("2026-10-03", 5, issued, forecast_model="model")
    assert state.snapshot("2026-10-04", 9, issued, forecast_model="model")
    assert state.match({"2026-10-03": 4, "2026-10-04": 1}, datetime(2026, 10, 5, tzinfo=timezone.utc)) == 2
    assert list(state.calibration_pairs()) == ["2026-10-03"]
    assert state.calibrator.to_list() == [[5.0, 4.0]]
    status = state.calibration_status("2026-10-05")
    assert status["samples"] == 1 and status["excluded_horizon_pairs"] == 1
    assert status["evaluation"]["by_lead_days"]["2"]["samples"] == 1
    path = tmp_path / "history.json"
    state.save(path)
    restored = PvLearningState("Europe/Kyiv", 50.0, 30.0)
    restored.load(path)
    assert len(restored.pairs) == 2
    assert restored.calibrator.to_list() == [[5.0, 4.0]]


def test_missing_naive_or_invalid_issue_time_does_not_train():
    state = PvLearningState("Europe/Kyiv", 50.0, 30.0)
    state.pairs = {"2026-10-03": {"forecast_kwh": 2, "actual_kwh": 1}}
    for stamp in (None, "2026-10-02T12:00:00", "invalid"):
        state.snapshots = {"2026-10-03": {"issued_at": stamp}}
        state.set_calibration_model(None)
        assert len(state.calibrator) == 0
