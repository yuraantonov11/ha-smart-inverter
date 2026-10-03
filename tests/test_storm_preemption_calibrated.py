import sys
import unittest
from unittest.mock import Mock
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hems.storm_risk import calibrated_storm_risk
from hems.forecast_calibration import ForecastCalibrator
from hems.predictive import PredictiveHemsController, PlannerInputs
from hems.predictive_control import PredictiveControlEngine
from test_predictive_decision_impact import prime
from test_engine_refactor import evaluate, NOW


class TestStorm(unittest.TestCase):
    def test_signed_bias_and_depth(self):
        for actual, samples, expected in ((3., 5, True), (7., 5, False), (3., 4, False), (3.5, 5, False)):
            cal = ForecastCalibrator(unit="kWh")
            for _ in range(samples): cal.record(5., actual)
            self.assertEqual(calibrated_storm_risk(cal), expected)

    def test_hint_and_safe_proposal(self):
        c = PredictiveHemsController()
        c.calibrated_storm_alert = True
        inputs = PlannerInputs(now=NOW, soc=60., soc_corrected=60., pv_w=0., load_w=500., grid_w=500., batt_w=0.,
            battery_capacity_kwh=10., grid_v=230., grid_ok=True, smart_mode=0,
            forecast_today_kwh=2., forecast_tomorrow_kwh=2., hourly_pv=[0.] * 24,
            hourly_weather=[{}] * 24, hourly_radiation=[0.] * 24, tariff_schedule=[4.32] * 24,
            consumption_history=[[500.] * 24] * 7)
        self.assertTrue(c.suggest(inputs).storm_preemption)
        self.assertTrue(inputs.storm_alert)
        self.assertIsNone(inputs.storm_hours_away)
        decision, _ = c.decide(inputs)
        self.assertEqual((decision.output_priority, decision.charger_priority), ("0", "1"))

    def test_without_storm_permission_no_activation(self):
        e = PredictiveControlEngine()
        _, hint = prime(e)
        hint.storm_preemption = True
        evaluate(e)
        self.assertFalse(e._predictive_selected)


if __name__ == "__main__": unittest.main()
