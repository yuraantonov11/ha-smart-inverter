"""Comprehensive tests for HEMS modules.

Run with: python -m pytest tests/ -v
Or standalone: python tests/test_hems_all.py
"""

from __future__ import annotations

import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hems.engine import HemsEngine, SmartMode, OutputPriority, ChargerPriority
from hems.tuning import HemsTunables, HemsTuningService
from hems.storm_risk import evaluate_storm_risk
from hems.schedule_rules import ScheduleRule, ScheduleRulesService
from hems.demand_forecast import DemandForecastService
from hems.battery_soh import BatterySoH
from hems.soc_correction import get_real_soc


def test_soc_correction():
    assert get_real_soc(100.0, 54.4, 0.0) == 100.0
    soc = get_real_soc(100.0, 48.0, 0.0)
    assert soc < 10.0
    soc_no_load = get_real_soc(100.0, 52.0, 0.0)
    soc_with_load = get_real_soc(100.0, 52.0, 50.0)
    assert soc_with_load <= soc_no_load
    print("✅ SOC correction OK")


def test_storm_risk():
    r = evaluate_storm_risk(weather_code=95)
    assert r.score == 1.0 and r.is_high_risk
    r = evaluate_storm_risk(weather_code=0)
    assert r.score == 0.0 and r.is_clear
    r = evaluate_storm_risk(wind_speed_ms=30)
    assert r.score == 0.8 and r.reason == "strong wind"
    print("✅ Storm risk OK")


def test_tuning():
    t = HemsTuningService()
    assert 70 <= t.compute_adaptive_pv_surplus(3000.0) <= 600
    assert 8 <= t.compute_adaptive_dwell() <= 25
    assert isinstance(t.should_reduce_buzzer(), bool)
    r1 = t.compute_adaptive_reserve_soc(100.0)
    r2 = t.compute_adaptive_reserve_soc(70.0)
    assert r2 > r1
    print("✅ Tuning OK")


def test_schedule_rules():
    svc = ScheduleRulesService()
    rule = ScheduleRule(name="Night", days_of_week=[1,2,3,4,5,6,7], start_hour=23, end_hour=7, mode=1, priority=8)
    svc.add_rule(rule)
    assert svc.get_active_rule_now(datetime(2026, 6, 22, 23, 30)) is not None
    assert svc.get_active_rule_now(datetime(2026, 6, 23, 5, 0)) is not None
    assert svc.get_active_rule_now(datetime(2026, 6, 22, 12, 0)) is None
    rule2 = ScheduleRule(name="Storm", days_of_week=[1,2,3,4,5,6,7], start_hour=22, end_hour=8, mode=2, priority=10)
    svc.add_rule(rule2)
    assert svc.get_active_rule_now(datetime(2026, 6, 22, 23, 30)).name == "Storm"
    data = svc.save_to_dict()
    svc2 = ScheduleRulesService()
    svc2.load_from_dict(data)
    assert len(svc2.rules) == 2
    print("✅ Schedule rules OK")


def test_demand_forecast():
    df = DemandForecastService()
    for _ in range(10):
        df.update_ewma(datetime(2026, 6, 22, 19, 0), 3000.0)
    f = df.to_demand_forecast()
    m = f.get_metrics_for_hour(19)
    assert m.p50 > 2000
    assert m.p25 < m.p50 < m.p75 < m.p90
    print("✅ Demand forecast OK")


def test_battery_soh():
    soh = BatterySoH(cycle_count=0, install_date=datetime(2026, 1, 1))
    assert soh.estimated_soh_percent() > 95
    soh2 = BatterySoH(cycle_count=200, install_date=datetime(2024, 1, 1))
    assert 70 < soh2.estimated_soh_percent() < 90
    soh3 = BatterySoH()
    assert soh3.track_soc(25.0) is False and soh3.in_low_state
    assert soh3.track_soc(85.0) is True and soh3.cycle_count == 1
    print("✅ Battery SoH OK")


def test_hems_engine():
    e = HemsEngine()
    d = e.evaluate(smart_mode=0, hems_auto=True, soc=60.0, pv_power=2000.0, grid_power=0.0, battery_power=0.0, load_power=500.0, grid_voltage=230.0, grid_available=True, current_output="0", current_charger="1")
    assert d.output_priority == "2" and d.reason == "surplus_enter_sbu"

    e2 = HemsEngine()
    d = e2.evaluate(smart_mode=0, hems_auto=True, soc=15.0, pv_power=0.0, grid_power=0.0, battery_power=0.0, load_power=500.0, grid_voltage=230.0, grid_available=True, current_output="0", current_charger="1")
    assert d.output_priority == "0" and d.charger_priority == "1"

    e3 = HemsEngine()
    d = e3.evaluate(smart_mode=2, hems_auto=True, soc=60.0, pv_power=2000.0, grid_power=0.0, battery_power=0.0, load_power=500.0, grid_voltage=230.0, grid_available=True)
    assert d.output_priority == "0" and d.charger_priority == "1"

    e4 = HemsEngine()
    d = e4.evaluate(smart_mode=0, hems_auto=False, soc=60.0, pv_power=2000.0, grid_power=0.0, battery_power=0.0, load_power=500.0, grid_voltage=230.0, grid_available=True)
    assert d.skip is True

    e5 = HemsEngine()
    e5._last_cmd_output = "2"
    e5._last_cmd_output_at = datetime.now() - timedelta(seconds=60)
    assert e5.detect_manual_override(actual_output="0", actual_charger="1")
    assert e5._manual_override_until is not None

    e6 = HemsEngine()
    e6.report_control_failure()
    assert e6._blocked_until is not None

    e7 = HemsEngine()
    e7.keepalive.last_activity_at = datetime.now() - timedelta(hours=3)
    d = e7.check_keepalive(battery_power=10.0, soc=50.0)
    assert d is not None and d.output_priority == "2"
    assert e7.keepalive.in_progress

    print("✅ HEMS engine OK")


if __name__ == "__main__":
    test_soc_correction()
    test_storm_risk()
    test_tuning()
    test_schedule_rules()
    test_demand_forecast()
    test_battery_soh()
    test_hems_engine()
    print("\n🎉 ALL TESTS PASSED")
