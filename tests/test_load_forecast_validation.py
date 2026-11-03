"""Chronological demand comparison: no future facts or filled rows."""
import asyncio
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hems.load_forecast import forecast_load, measured_days, _evaluate
from hems.forecast_evaluation import day_ahead_error_range
from hems.predictive import ConsumptionPredictor


def history(n=30):
    start = date(2026, 1, 1)
    return [(start + timedelta(days=i),
             [200 + i * 9 + (800 if (start + timedelta(days=i)).weekday() >= 5 else 0)] * 24,
             False) for i in range(n)]


class LoadForecastTests(unittest.TestCase):
    def test_never_trains_on_issue_day_or_future(self):
        rows = history()
        issue = rows[20][0]
        a = forecast_load(rows[:20], issue, issue)
        b = forecast_load(rows[:20] + [(d, [19999] * 24, False) for d, _, _ in rows[20:]], issue, issue)
        self.assertEqual(a, b)

    def test_candidate_responds_to_recent_history(self):
        rows = history()
        issue = rows[-1][0] + timedelta(days=1)
        result = forecast_load(rows, issue, issue)
        legacy = ConsumptionPredictor(rows).predict(12, issue.weekday())[0]
        self.assertGreater(result["candidate_hourly_w"][12], legacy)
        self.assertGreaterEqual(result["validation_days"], 7)

    def test_constant_history_does_not_claim_superiority(self):
        rows = [(d, [300] * 24, False) for d, _, _ in history()]
        issue = rows[-1][0] + timedelta(days=1)
        result = forecast_load(rows, issue, issue)
        self.assertFalse(result["candidate_selected"])
        self.assertEqual(result["selection_reason"], "baseline_not_beaten")
        self.assertEqual(result["hourly_w"], (300.,) * 24)

    def test_warmup_does_not_invent_ranges_or_promote(self):
        result = forecast_load(history(8), date(2026, 1, 9), date(2026, 1, 9))
        self.assertEqual(result["selection_reason"], "insufficient_test_days")
        self.assertIsNone(result["hourly_error_range_w"])

    def test_invalid_or_filled_days_are_not_evidence(self):
        rows = history(3)
        for i, value in enumerate((True, float("nan"), float("inf"), -1, 20001)):
            rows.append((date(2026, 1, 5) + timedelta(days=i), [value] * 24, False))
        rows.append((date(2026, 1, 12), [300] * 24, True))
        self.assertEqual(len(measured_days(rows, date(2026, 2, 1))), 3)

    def test_future_only_history_does_not_leak_via_fallback(self):
        p = ConsumptionPredictor(history(), issued_on=date(2025, 12, 1))
        self.assertEqual(p.predict_for_date(12, date(2025, 12, 1)), ConsumptionPredictor().predict(12, 0))

    def test_promotion_requires_both_baselines(self):
        issue = date(2026, 1, 31)
        result = forecast_load(history(), issue, issue)
        self.assertTrue(result["candidate_selected"])
        self.assertLessEqual(result["candidate_mae_w"], .95 * result["legacy_mae_w"] + .1)
        self.assertLessEqual(result["candidate_mae_w"], result["yesterday_mae_w"] + .1)
        self.assertFalse(result["range_is_probability_guarantee"])

    def test_input_order_is_irrelevant(self):
        self.assertEqual(forecast_load(history(), date(2026, 2, 1), date(2026, 2, 1)),
                         forecast_load(list(reversed(history())), date(2026, 2, 1), date(2026, 2, 1)))

    def test_day_ahead_validation_cannot_see_issuance_days_actual(self):
        rows = measured_days(history(), date(2026, 2, 1))
        target = date(2026, 1, 21)
        before = next(r for r in _evaluate(rows, 1) if r[0] == target)
        changed = tuple((d, (19999.,) * 24 if d == target - timedelta(days=1) else values)
                        for d, values in rows)
        after = next(r for r in _evaluate(changed, 1) if r[0] == target)
        self.assertEqual(before, after)

    def test_daily_range_uses_daily_errors_not_sum_of_hourly_ranges(self):
        start = date(2026, 1, 1)
        rows = [(start + timedelta(days=i),
                 [300 + (100 if (h + i) % 2 else -100) for h in range(24)], False)
                for i in range(30)]
        result = forecast_load(rows, date(2026, 1, 31), date(2026, 2, 1))
        lower, upper = result["daily_error_range_kwh"]
        self.assertAlmostEqual(lower, 7.2)
        self.assertAlmostEqual(upper, 7.2)
        self.assertGreater(sum(r[1] for r in result["hourly_error_range_w"]) / 1000, upper)


class PvErrorRangeTests(unittest.TestCase):
    def make_pairs(self):
        pairs, snapshots = {}, {}
        for i in range(12):
            day = date(2026, 1, 2) + timedelta(days=i)
            pairs[day.isoformat()] = {"forecast_kwh": 1., "actual_kwh": 1. + i / 10}
            snapshots[day.isoformat()] = {"issued_at": (day - timedelta(days=1)).isoformat() + "T12:00:00+02:00"}
        return pairs, snapshots

    def test_interval_only_uses_facts_available_before_issuance(self):
        pairs, snapshots = self.make_pairs()
        kwargs = dict(issued_at="2026-01-10T10:00:00+02:00", forecast_kwh=2., timezone_name="Europe/Kyiv")
        before = day_ahead_error_range(pairs, snapshots, **kwargs)
        for day, pair in pairs.items():
            if day >= "2026-01-10":
                pair["actual_kwh"] = 9999.
        self.assertEqual(before, day_ahead_error_range(pairs, snapshots, **kwargs))
        self.assertEqual(before["samples"], 8)
        self.assertFalse(before["probability_guarantee"])

    def test_range_warmup_and_unverifiable_issuance(self):
        pairs, snapshots = self.make_pairs()
        kwargs = dict(forecast_kwh=2., timezone_name="Europe/Kyiv")
        result = day_ahead_error_range(pairs, snapshots, issued_at="2026-01-05T10:00:00+02:00", **kwargs)
        self.assertEqual(result["status"], "insufficient_samples")
        result = day_ahead_error_range(pairs, snapshots, issued_at="2026-01-10T10:00:00", **kwargs)
        self.assertEqual(result["status"], "invalid_issuance")


if __name__ == "__main__":
    unittest.main()
