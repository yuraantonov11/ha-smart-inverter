from datetime import datetime, timedelta, timezone
import unittest
from hems.autonomy_evaluation import AutonomyMonitor

class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,11,5,tzinfo=timezone.utc)
        self.args = dict(capacity=5.12, soc=70, reserve=20, load=600, pv=100, max_gap_seconds=120)

    def test_restoration_records_grid_interval_without_runtime_claim(self):
        m = AutonomyMonitor()
        m.observe(self.now, True, **self.args)
        m.observe(self.now+timedelta(seconds=60), False, **self.args)
        m.observe(self.now+timedelta(seconds=120), False, **self.args)
        m.observe(self.now+timedelta(seconds=180), True, **self.args)
        event = m.status()['recent'][0]
        self.assertEqual(event['observation'], 'grid_outage_observed')
        self.assertIsNone(event['absolute_error_minutes'])
        self.assertIsNone(event['prediction_below_observed_lower_bound'])

    def test_gap_censors_and_does_not_start_false_outage(self):
        m = AutonomyMonitor()
        m.observe(self.now, True, **self.args)
        m.observe(self.now+timedelta(seconds=300), False, **self.args)
        self.assertIsNone(m.status()['active'])
        self.assertEqual(m.status()['recent'], [])

    def test_restart_keeps_active_but_lost_telemetry_is_incomplete(self):
        m = AutonomyMonitor()
        m.observe(self.now, True, **self.args)
        m.observe(self.now+timedelta(seconds=60), False, **self.args)
        m.observe(self.now+timedelta(seconds=120), False, **self.args)
        m = AutonomyMonitor(m.snapshot())
        m.observe(self.now+timedelta(seconds=600), True, **self.args)
        self.assertEqual(m.status()['recent'][0]['observation'], 'incomplete')

    def test_invalid_values_remove_old_forecast(self):
        m = AutonomyMonitor()
        m.observe(self.now, True, **self.args)
        bad = {**self.args, 'soc': None}
        m.observe(self.now+timedelta(seconds=60), True, **bad)
        m.observe(self.now+timedelta(seconds=120), False, **self.args)
        self.assertIsNone(m.status()['active'])

if __name__ == '__main__':
    unittest.main()
