import unittest
from datetime import datetime, timedelta, timezone
from hems.autonomy_evaluation import evaluate_observation

class AutonomyEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 11, 5, tzinfo=timezone.utc)
        self.end = self.start + timedelta(minutes=90)

    def test_grid_return_is_lower_bound_not_prediction_error(self):
        result = evaluate_observation(60, self.start, self.end, 'grid_restored')
        self.assertEqual(result['observation'], 'grid_outage_observed')
        self.assertIsNone(result['absolute_error_minutes'])
        self.assertIsNone(result['prediction_below_observed_lower_bound'])

    def test_exhaustion_is_exact(self):
        result = evaluate_observation(60, self.start, self.end, 'battery_exhausted')
        self.assertEqual(result['absolute_error_minutes'], 30)

    def test_disconnect_is_incomplete(self):
        self.assertEqual(evaluate_observation(60, self.start, self.end, 'telemetry_lost')['observation'], 'incomplete')

    def test_rejects_invalid_data(self):
        for value in (True, float('nan'), -1):
            with self.assertRaises(ValueError):
                evaluate_observation(value, self.start, self.end, 'grid_restored')
        with self.assertRaises(ValueError):
            evaluate_observation(60, self.start.replace(tzinfo=None), self.end, 'grid_restored')



class ScenarioEstimateTests(unittest.TestCase):
    def test_estimate_uses_capacity_reserve_and_net_load(self):
        from hems.autonomy_evaluation import estimate_constant_load_minutes
        r = estimate_constant_load_minutes(5.12, 70, 20, 600, 100)
        self.assertAlmostEqual(r['minutes'], 307.2)
        self.assertIn('not_validated', r['warning'])

    def test_missing_or_unusable_inputs_are_not_guessed(self):
        from hems.autonomy_evaluation import estimate_constant_load_minutes
        for args in [(5.12, None, 20, 600, 100), (5.12, 10, 20, 600, 100),
                     (5.12, 70, 20, 600, 590), (5.12, 70, 20, True, 0)]:
            self.assertIsNone(estimate_constant_load_minutes(*args))

if __name__ == '__main__':
    unittest.main()
