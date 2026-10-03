"""Exercise predictive proposals through the real HEMS guard pipeline."""
import sys
import unittest
from pathlib import Path
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hems.predictive_control import PredictiveControlEngine
from hems.engine import HemsEngine, HemsDecision, SmartMode
from hems.forecast_calibration import ForecastCalibrator
from hems.predictive import PlannerInputs, PredictiveHemsController
from test_engine_refactor import evaluate, NOW


def prime(engine, mode="assist", confidence=0.5, samples=5):
    engine.predictive_tuning.predictive_mode = mode
    engine._last_forecast_today_kwh = 2.
    engine._hourly_pv_forecast = [100.] * 24
    engine._hourly_radiation = [100.] * 24
    engine._hourly_weather_codes = [0] * 24
    engine._consumption_history = [[500.] * 24] * 7
    engine._tariff_schedule = [4.32] * 24
    cal = ForecastCalibrator(unit="kWh")
    for _ in range(samples):
        cal.record(5., 5.)
    hint = SimpleNamespace(target_soc_morning=60., target_soc_evening=70.,
                           confidence=confidence, storm_preemption=False)
    plan = SimpleNamespace(generated_at=NOW)
    proposal = HemsDecision("2", "2", "planner_test")
    controller = SimpleNamespace(calibrator=cal, last_decision=None, last_plan=None,
                                 suggest=Mock(return_value=hint))
    def decide(inputs):
        controller.last_decision = proposal
        return proposal, plan
    controller.decide = Mock(side_effect=decide)
    engine._predictive_controller = controller
    return controller, hint


class TestPredictiveImpact(unittest.TestCase):
    def setUp(self):
        patch("hems.engine.debug_logging.log_evaluation").start()
        self.addCleanup(patch.stopall)

    def test_shadow_keeps_baseline_and_records_proposal(self):
        e = PredictiveControlEngine()
        prime(e, "shadow")
        self.assertEqual(evaluate(e), evaluate(HemsEngine()))
        self.assertEqual(e._last_predictive_decision.reason, "planner_test")
        self.assertFalse(e.predictive_decision_state["applied"])

    def test_assist_proposal_and_transport_ack(self):
        for mode in (SmartMode.ADAPTIVE, SmartMode.ARBITRAGE):
            e = PredictiveControlEngine()
            prime(e)
            decision = evaluate(e, smart_mode=mode)
            self.assertEqual((decision.output_priority, decision.charger_priority), ("2", "2"))
            self.assertFalse(e.predictive_decision_state["applied"])
            e.confirm_predictive_delivery(True)
            self.assertTrue(e.predictive_decision_state["applied"])

    def test_low_confidence_or_samples_keeps_baseline(self):
        for confidence, samples in ((.1, 5), (.5, 2), (float("nan"), 5), (.5, 0)):
            e = PredictiveControlEngine()
            prime(e, confidence=confidence, samples=samples)
            self.assertEqual(evaluate(e), evaluate(HemsEngine()))

    def test_storm_cannot_be_weakened(self):
        e = PredictiveControlEngine()
        prime(e, confidence=.9)
        d = evaluate(e, smart_mode=SmartMode.STORM, current_output="2", current_charger="2")
        self.assertEqual((d.output_priority, d.charger_priority), ("0", "1"))
        self.assertFalse(e._predictive_selected)

    def test_manual_override(self):
        e = PredictiveControlEngine()
        c, _ = prime(e)
        e._manual_override_until = NOW + timedelta(hours=1)
        self.assertTrue(evaluate(e).skip)
        c.decide.assert_not_called()
        self.assertIsNone(e._last_predictive_decision)

    def test_reserve_and_hysteresis(self):
        for soc in (20., 22., 40.):
            e = PredictiveControlEngine()
            prime(e)
            args = dict(soc=soc, pv_power=0., current_charger="2")
            self.assertEqual(evaluate(e, **args), evaluate(HemsEngine(), **args))
            self.assertFalse(e._predictive_selected)

    def test_transport_failure_is_not_applied(self):
        e = PredictiveControlEngine()
        prime(e)
        evaluate(e)
        e.confirm_predictive_delivery(False)
        self.assertFalse(e.predictive_decision_state["applied"])

    def test_dwell_blocks_proposal(self):
        e = PredictiveControlEngine()
        prime(e)
        e._last_output_switch_at = NOW - timedelta(seconds=1)
        d = evaluate(e)
        self.assertIsNone(d.output_priority)
        e.confirm_predictive_delivery(True)
        self.assertFalse(e.predictive_decision_state["applied"])

    def test_circuit_offline_auto_off_and_keepalive(self):
        for hold in ("circuit", "offline", "auto", "keepalive"):
            e = PredictiveControlEngine()
            prime(e)
            args = {}
            if hold == "circuit": e._blocked_until = NOW + timedelta(minutes=1)
            if hold == "offline": args["is_online"] = False
            if hold == "auto": args["hems_auto"] = False
            if hold == "keepalive": e.keepalive.in_progress = True
            evaluate(e, **args)
            self.assertFalse(e._predictive_selected)

    def test_config_cannot_lower_hard_confidence_gate(self):
        e = PredictiveControlEngine()
        prime(e, confidence=.15)
        e.predictive_min_confidence = .0
        self.assertEqual(evaluate(e), evaluate(HemsEngine()))

    def test_unapproved_storm_advice_has_no_effect(self):
        e = PredictiveControlEngine()
        _, hint = prime(e)
        hint.storm_preemption = True
        self.assertEqual(evaluate(e), evaluate(HemsEngine()))

    def test_real_plan_starts_now_and_skips_sunny_night_charge(self):
        for hour in (0, 6, 12, 23):
            inputs = PlannerInputs(now=NOW.replace(hour=hour), soc=60., soc_corrected=60.,
                pv_w=100., load_w=500., grid_w=400., batt_w=0., battery_capacity_kwh=10.,
                grid_v=230., grid_ok=True, smart_mode=0, forecast_today_kwh=5., forecast_tomorrow_kwh=5.,
                hourly_pv=[100.] * 24, hourly_weather=[{}] * 24, hourly_radiation=[100.] * 24,
                tariff_schedule=[4.32] * 24, consumption_history=[[500.] * 24] * 7)
            controller = PredictiveHemsController()
            decision, plan = controller.decide(inputs)
            self.assertEqual(plan.hourly[0].hour, hour)
            self.assertEqual(plan.hourly[0].timestamp, inputs.now)
            self.assertEqual([p.timestamp for p in plan.hourly], sorted(p.timestamp for p in plan.hourly))
            expected = "2" if plan.hourly[0].output == "SBU" else "0"
            self.assertEqual(decision.output_priority, expected)
            if hour in (0, 6, 23):
                self.assertEqual(decision.charger_priority, "2")

    def test_coordinator_hold_clears_applied_and_recommendation(self):
        e = PredictiveControlEngine()
        prime(e)
        evaluate(e)
        e.confirm_predictive_delivery(True)
        e.invalidate_predictive("keepalive_in_progress")
        self.assertFalse(e.predictive_decision_state["applied"])
        self.assertIsNone(e._last_predictive_decision)
        self.assertIsNone(e._last_predictive_hint)


if __name__ == "__main__":
    unittest.main()
