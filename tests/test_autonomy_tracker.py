from datetime import datetime, timedelta, timezone
import unittest
from hems.autonomy_evaluation import AutonomyObservationTracker

class TrackerTests(unittest.TestCase):
    def test_forecast_freezes_and_survives_restore(self):
        now = datetime(2026,11,5,tzinfo=timezone.utc)
        t = AutonomyObservationTracker()
        t.offer_forecast(120, now)
        self.assertTrue(t.start(now + timedelta(seconds=30), 60))
        self.assertFalse(t.offer_forecast(1, now))
        restored = AutonomyObservationTracker(t.snapshot())
        result = restored.finish(now + timedelta(minutes=91), 'grid_restored')
        self.assertEqual(result['predicted_minutes'], 120)
        self.assertIsNone(result['absolute_error_minutes'])
        self.assertEqual(len(restored.snapshot()['completed']), 1)

    def test_stale_or_future_forecast_not_used(self):
        now = datetime(2026,11,5,tzinfo=timezone.utc)
        t = AutonomyObservationTracker()
        t.offer_forecast(120, now)
        self.assertFalse(t.start(now + timedelta(seconds=61), 60))
        self.assertFalse(t.start(now - timedelta(seconds=1), 60))
        self.assertIsNone(t.snapshot()['active'])

if __name__ == '__main__':
    unittest.main()
