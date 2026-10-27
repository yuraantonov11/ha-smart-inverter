"""Physical planner regressions; use production functions, never copied math."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace
import math

import pytest

from hems.battery_limits import resolve_battery_power_limits
from hems.forecast_evaluation import evaluate_daily_forecasts
from hems.predictive import (
    _balance_hour, plan_night_charge, plan_soc_targets, ConsumptionPredictor,
    PredictiveHemsController,
)
from hems.telemetry import build_planner_inputs
from hems.predictive_control import PredictiveControlEngine
from hems.forecast_calibration import ForecastCalibrator


def inputs(**overrides):
    args = dict(raw={"batterySoc": 40, "loadPower": 1000, "gridVoltage": 230},
                now=datetime(2026, 10, 27, 23), battery_capacity_kwh=11.776,
                forecast_today_kwh=1, forecast_tomorrow_kwh=4,
                consumption_history=[[1000.0]*24]*7,
                hourly_pv=[0.0]*24, hourly_radiation=[0.0]*24,
                hourly_weather_codes=[0]*24, tariff_schedule=[4.32]*24,
                max_charge_power_w=2560, max_grid_charge_power_w=2048,
                max_discharge_power_w=4000)
    args.update(overrides)
    return build_planner_inputs(**args)


def balance(**overrides):
    args = dict(output="0", charger="1", pv_w=0, load_w=500, grid_ok=True,
                soc=20, reserve_soc=20, battery_capacity_kwh=11.776,
                charge_efficiency=.95, discharge_efficiency=.9,
                max_charge_power_w=2560, max_grid_charge_power_w=2048,
                max_discharge_power_w=4000)
    args.update(overrides)
    return _balance_hour(**args)


def test_utility_charge_is_limited_instead_of_filling_battery_in_one_hour():
    stored, grid, unserved = balance()
    assert stored == pytest.approx(2048)
    assert grid == pytest.approx(500 + 2048/.95)
    assert unserved == 0
    assert 20 + stored/1000/11.776*100 < 40


def test_combined_pv_and_utility_obey_total_dc_limit():
    stored, grid, _ = balance(pv_w=2000, max_charge_power_w=1500)
    assert stored == pytest.approx(1500)
    assert grid == pytest.approx(1500/.95 - 1500)


def test_discharge_limit_uses_grid_for_remaining_load():
    battery, grid, unserved = balance(output="2", charger="2", soc=90,
                                     load_w=5000, max_discharge_power_w=1000)
    assert battery == pytest.approx(-1000)
    assert grid == pytest.approx(4100)
    assert unserved == 0


def test_off_grid_discharge_limit_surfaces_unserved_energy():
    battery, grid, unserved = balance(output="2", soc=90, load_w=5000,
                                     grid_ok=False, max_discharge_power_w=1000)
    assert battery == pytest.approx(-1000)
    assert grid == 0
    assert unserved == pytest.approx(4100)


def test_hourly_charge_stops_at_requested_target():
    stored, _, _ = balance(soc=49, charge_target_soc=50)
    assert stored == pytest.approx(117.76)


@pytest.mark.parametrize("value", [True, -1, math.nan, math.inf, "1000"])
def test_balance_rejects_invalid_explicit_limits(value):
    with pytest.raises(ValueError):
        balance(max_grid_charge_power_w=value)


def test_sunny_forecast_does_not_cancel_required_night_charge():
    start, end, reason = plan_night_charge(inputs(), 95)
    assert (start, end) == (3, 7)  # 6.4768 kWh / 2.048 kW -> four hours.
    assert "skip" not in reason


def test_night_window_reports_unreachable_target():
    start, end, reason = plan_night_charge(inputs(max_grid_charge_power_w=100), 95)
    assert (start, end) == (23, 7)
    assert "unreachable" in reason


def test_night_window_uses_remaining_hours_not_elapsed_hours():
    _, _, reason = plan_night_charge(inputs(now=datetime(2026,10,27,6)), 95)
    assert "unreachable" in reason


def test_night_charge_duration_honours_dst_elapsed_hours():
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Kyiv")
    # Spring cheap window has seven elapsed hours, not eight.
    pi = inputs(now=datetime(2026,3,28,23,tzinfo=tz), battery_capacity_kwh=10,
                raw={"batterySoc":20}, max_grid_charge_power_w=1000)
    assert "unreachable" in plan_night_charge(pi, 95)[2]
    # Fall cheap window has nine elapsed hours, so the same 7.5 kWh fits.
    pi.now = datetime(2026,10,24,23,tzinfo=tz)
    assert "unreachable" not in plan_night_charge(pi, 95)[2]


def test_unknown_charge_limit_preserves_full_window():
    start, end, reason = plan_night_charge(inputs(max_grid_charge_power_w=None), 95)
    assert (start, end) == (23, 7)
    assert "unknown" in reason


def test_explicit_zero_disables_utility_charge():
    assert plan_night_charge(inputs(max_grid_charge_power_w=0), 95)[:2] == (-1, -1)
    assert balance(max_grid_charge_power_w=0)[0] == 0


def test_daytime_pv_cannot_erase_evening_energy_requirement():
    morning, evening = plan_soc_targets(inputs(forecast_tomorrow_kwh=6))
    assert morning > 90
    assert evening > 60  # Five post-sunset hours, reserve, inverter losses.


def test_after_midnight_targets_use_today_not_tomorrow_forecast():
    args = dict(now=datetime(2026, 10, 27, 1), forecast_today_kwh=0,
                forecast_tomorrow_kwh=30)
    assert plan_soc_targets(inputs(**args))[0] == 95
    args["now"] = datetime(2026, 10, 27, 12)
    assert plan_soc_targets(inputs(**args))[0] == 20


def test_rollout_recomputes_target_for_next_night_instead_of_reusing_today():
    from hems.predictive import simulate_24h
    pi = inputs(now=datetime(2026, 10, 27, 1), forecast_today_kwh=30,
                forecast_tomorrow_kwh=0, max_grid_charge_power_w=100)
    morning, evening = plan_soc_targets(pi)
    assert morning == 20
    plan = simulate_24h(pi, morning, evening, ConsumptionPredictor(pi.consumption_history))
    assert next(hour for hour in plan if hour.hour == 1).charger == "OSO"
    assert next(hour for hour in plan if hour.hour == 23).charger == "SNU"


def test_actual_plan_propagates_power_limits_and_never_instantly_fills():
    _, plan = PredictiveHemsController().decide(inputs())
    assert len(plan.hourly) == 24
    for hour in plan.hourly:
        assert -4000 <= hour.batt_w <= 2560
    assert max(p.grid_w for p in plan.hourly) <= 1000 + 2048/.85 + .01


def test_readback_and_operator_use_tightest_limit_and_correct_units():
    result = resolve_battery_power_limits(
        {"max_charge_power_w": 2000, "max_discharge_power_w": 4000},
        {"setMaxChargingCurrent": {"value": "50", "unit": "A"},
         "setUtilityMaxChargingCurrent": {"value": "40", "unit": "A"}}, 51.2)
    assert result == {"max_charge_power_w": 2000, "max_grid_charge_power_w": 2000,
                      "max_discharge_power_w": 4000}


def test_bad_units_missing_settings_and_boolean_options_are_not_guessed():
    limits = resolve_battery_power_limits(
        {"max_discharge_power_w": True},
        {"setMaxChargingCurrent": {"value": 50, "unit": "V"}}, 51.2)
    assert all(value is None for value in limits.values())


def test_invalidation_preserves_learning_but_clears_application():
    engine = PredictiveControlEngine()
    cal = ForecastCalibrator(unit="kWh")
    for _ in range(5):
        cal.record(1, 1)
    engine._predictive_controller = SimpleNamespace(calibrator=cal)
    engine._predictive_can_apply = True
    engine.invalidate_predictive("inverter_offline")
    state = engine.predictive_decision_state
    assert state["samples"] == state["real_pairs"] == 5
    assert state["model_quality"] == cal.metrics().confidence_factor
    assert state["confidence"] == 0
    assert state["readiness"] is False
    assert engine._predictive_can_apply is False


def test_assist_abstains_when_battery_limits_are_unknown():
    from test_predictive_decision_impact import prime
    from test_engine_refactor import evaluate
    from hems.engine import HemsEngine
    engine = PredictiveControlEngine()
    prime(engine)
    engine._battery_power_limits = {}
    assert evaluate(engine) == evaluate(HemsEngine())
    assert engine.predictive_decision_state["battery_limits_known"] is False
    assert engine.predictive_decision_state["readiness"] is False
    assert engine.predictive_decision_state["planning_limitation"] == "battery_power_limits_unknown"


def test_successful_evaluation_does_not_keep_an_old_offline_reason():
    from test_predictive_decision_impact import prime
    from test_engine_refactor import evaluate
    engine = PredictiveControlEngine()
    prime(engine)
    engine.invalidate_predictive("inverter_offline")
    evaluate(engine)
    state = engine.predictive_decision_state
    assert state["reason"] == "planner_test"
    assert state["execution_reason"] != "inverter_offline"
    assert state["telemetry_online"] is True
    assert state["samples"] == 5


def test_dated_consumption_distinguishes_weekdays_without_inventing_dates():
    days = [(date(2026, 10, 5) + timedelta(days=i), [100 if i % 7 < 5 else 900]*24, False)
            for i in range(14)]
    predictor = ConsumptionPredictor(days)
    assert predictor.predict(12, 0)[0] == 100
    assert predictor.predict(12, 6)[0] == 900


def test_forecast_metrics_and_baseline_use_same_dates():
    # Synthetic values: public regression fixtures contain no household history.
    forecasts = [1, 1, 1]
    actuals = [2, 3, 4]
    pairs = {(date(2026,10,22)+timedelta(days=i)).isoformat():
             {"forecast_kwh": fc, "actual_kwh": ac}
             for i, (fc, ac) in enumerate(zip(forecasts, actuals))}
    history = {**pairs, "2026-10-21": {"actual_kwh": 1}}
    score = evaluate_daily_forecasts(pairs, actual_history=history)
    assert score["mae_kwh"] == 2
    assert score["bias_kwh"] == 2
    assert score["wape_percent"] == 66.67
    assert score["baseline_samples"] == 3
    assert score["baseline_mae_kwh"] == 1
    assert score["skill_vs_yesterday"] == -1


def test_forecast_horizons_are_separate_and_zero_energy_has_no_fake_percentage():
    pairs = {"2026-10-22": {"forecast_kwh": 1, "actual_kwh": 0},
             "2026-10-23": {"forecast_kwh": 2, "actual_kwh": 0}}
    snapshots = {"2026-10-22": {"issued_at": "2026-10-21T18:00:00+03:00"},
                 "2026-10-23": {"issued_at": "2026-10-21T18:00:00+03:00"}}
    score = evaluate_daily_forecasts(pairs, snapshots)
    assert score["wape_percent"] is None
    assert score["by_lead_days"]["1"]["samples"] == 1
    assert score["by_lead_days"]["2"]["samples"] == 1
    assert score["skill_vs_yesterday"] is None


def test_forecast_lead_uses_site_calendar_even_when_issued_timestamp_is_utc():
    pairs = {"2026-10-23": {"forecast_kwh": 1, "actual_kwh": 1}}
    snapshots = {"2026-10-23": {"issued_at": "2026-10-21T22:30:00+00:00"}}
    score = evaluate_daily_forecasts(pairs, snapshots, timezone_name="Europe/Kyiv")
    assert score["by_lead_days"]["1"]["samples"] == 1


def test_learning_status_publishes_scores_without_changing_issued_forecasts():
    from copy import deepcopy
    from hems.pv_learning import PvLearningState
    state = PvLearningState("Europe/Kyiv", 0, 0)
    state.snapshots = {"2026-10-22": {"forecast_kwh": 1, "issued_at": "2026-10-21T18:00:00+03:00"}}
    state.pairs = {"2026-10-22": {"forecast_kwh": 1, "actual_kwh": 2, "coverage": 1}}
    original = deepcopy((state.snapshots, state.pairs))
    state.set_calibration_model(None)
    status = state.calibration_status("2026-10-23")
    assert status["evaluation"]["mae_kwh"] == 1
    assert status["evaluation"]["by_lead_days"]["1"]["samples"] == 1
    assert (state.snapshots, state.pairs) == original


@pytest.mark.parametrize("forecast", [None, float("nan"), float("inf"), -1])
def test_unknown_forecast_abstains_without_inventing_targets(forecast):
    pi = inputs(forecast_tomorrow_kwh=forecast)
    controller = PredictiveHemsController()
    hint = controller.suggest(pi)
    assert hint.confidence == 0
    assert hint.target_soc_morning is None and hint.target_soc_evening is None
    assert hint.night_charge_start_hour == hint.night_charge_end_hour == -1
    decision, plan = controller.decide(pi)
    assert decision.skip and "forecast_unknown" in decision.reason
    assert plan.hourly == [] and plan.confidence == 0


def test_missing_next_day_forecast_abstains_during_rollout():
    pi = inputs(now=datetime(2026, 10, 27, 1), forecast_tomorrow_kwh=None)
    decision, plan = PredictiveHemsController().decide(pi)
    assert decision.skip and "forecast_unknown" in decision.reason
    assert plan.hourly == [] and plan.confidence == 0


def test_charge_readbacks_distinguish_missing_invalid_and_recorded_values():
    from hems.battery_limits import read_charge_current
    assert read_charge_current({}, "setMaxChargingCurrent") is None
    assert read_charge_current({"setMaxChargingCurrent": {"value": "50", "unit": "A"}}, "setMaxChargingCurrent") == 50
    assert read_charge_current({"setMaxChargingCurrent": {"value": "50", "unit": "V"}}, "setMaxChargingCurrent") is None
    assert read_charge_current({"setMaxChargingCurrent": True}, "setMaxChargingCurrent") is None
    assert read_charge_current({"setMaxChargingCurrent": "nan"}, "setMaxChargingCurrent") is None
    assert read_charge_current({"maxChargingCurrent": 0}, "setMaxChargingCurrent", "maxChargingCurrent") == 0


def test_expired_or_undated_config_readback_cannot_supply_a_charge_limit():
    from datetime import timezone
    from hems.battery_limits import settings_readback_fresh, resolve_battery_power_limits
    now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    assert settings_readback_fresh(now - timedelta(seconds=179), now)
    assert not settings_readback_fresh(now - timedelta(seconds=181), now)
    assert not settings_readback_fresh(now + timedelta(seconds=1), now)
    assert not settings_readback_fresh(None, now)
    assert not settings_readback_fresh(now.replace(tzinfo=None), now)
    assert resolve_battery_power_limits({}, {}, 51.2)["max_charge_power_w"] is None


def test_real_coordinator_accepts_a_snapshot_and_preserves_it_on_empty_response():
    import ast
    import logging
    from datetime import timezone
    from pathlib import Path
    from hems.battery_limits import record_settings_snapshot, read_charge_current
    source = (Path(__file__).resolve().parents[1] / "coordinator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "InverterCoordinator")
    fn = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_async_update_data")
    branch = next(n for n in ast.walk(fn) if isinstance(n, ast.If)
                  and ast.unparse(n.test) == "isinstance(fetched, dict) and fetched")
    # Execute the actual branch body. Resolve its relative import from this test's package.
    branch.body = [n for n in branch.body if not isinstance(n, ast.ImportFrom)]
    code = compile(ast.fix_missing_locations(ast.Module(body=[branch], type_ignores=[])), source[:0] or "coordinator.py", "exec")
    now = datetime.now(timezone.utc)
    owner = SimpleNamespace(api=SimpleNamespace(last_device_configs_received_at=now),
                            _device_settings_snapshot={}, _device_settings_received_at=None)
    settings = {"setMaxChargingCurrent": {"value": 50, "unit": "A"}}
    namespace = {"self": owner, "fetched": settings, "_LOGGER": logging.getLogger(__name__),
                 "record_settings_snapshot": record_settings_snapshot}
    exec(code, namespace)
    assert read_charge_current(owner._device_settings_snapshot, "setMaxChargingCurrent") == 50
    assert owner._device_settings_received_at == now
    namespace["fetched"] = {}
    owner.api.last_device_configs_received_at = now + timedelta(minutes=1)
    exec(code, namespace)
    assert owner._device_settings_received_at == now
    assert read_charge_current(owner._device_settings_snapshot, "setMaxChargingCurrent") == 50


if __name__ == "__main__":
    import sys
    from pathlib import Path
    tests = Path(__file__).resolve().parent
    raise SystemExit(pytest.main([str(Path(__file__).resolve()),
        "--rootdir=" + str(tests), "--confcutdir=" + str(tests),
        "--noconftest", "--import-mode=importlib", "-q"]))
