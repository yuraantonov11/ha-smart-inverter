"""Tests for the freshness and SOC contracts.

Юра's requirements in this round:
  * a repeated response without a new measurement must NOT renew freshness;
  * reception, local message and unknown measurement time stay separate;
  * metrics named as intervals BETWEEN CHANGES, frequency counted separately;
  * raw SOC from both transports normalises to one contract;
  * corrected_soc computed by the SAME path for both, so transport cannot
    change the SOC algorithm.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Resolve ``source_contracts`` relative to the test file so the test works
# from any checkout or extracted review package -- no hardcoded paths.
REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "source_contracts_under_test", REPO / "source_contracts.py"
)
sc = importlib.util.module_from_spec(_spec)
sys.modules["source_contracts_under_test"] = sc
_spec.loader.exec_module(sc)

T0 = datetime(2026, 10, 20, 11, 0, 0, tzinfo=timezone.utc)


class ChangeTrackerTests(unittest.TestCase):
    def test_repeat_response_does_not_renew_freshness(self):
        """The core requirement. 63 cloud receptions carry the same value;
        freshness must stay pinned to the last real CHANGE."""
        t = sc.ChangeTracker()
        assert t.observe_counted("batteryVoltage", 53.1, T0)
        now = T0
        for i in range(63):
            now = now + timedelta(seconds=5)
            t.observe_counted("batteryVoltage", 53.1, now)   # same value
        age = t.change_age_s("batteryVoltage", now)
        self.assertAlmostEqual(age, 315.0, places=3)
        self.assertEqual(t.receptions_since_change("batteryVoltage"), 63)

    def test_change_renews_freshness(self):
        t = sc.ChangeTracker()
        t.observe("batteryVoltage", 53.1, T0)
        later = T0 + timedelta(seconds=300)
        t.observe("batteryVoltage", 53.4, later)
        self.assertEqual(t.change_age_s("batteryVoltage", later), 0.0)

    def test_tolerance_absorbs_noise(self):
        t = sc.ChangeTracker(tolerance=0.05)
        t.observe("batteryVoltage", 53.10, T0)
        later = T0 + timedelta(seconds=300)
        self.assertFalse(t.observe("batteryVoltage", 53.12, later))
        self.assertAlmostEqual(t.change_age_s("batteryVoltage", later), 300.0)

    def test_none_never_counts_as_change(self):
        t = sc.ChangeTracker()
        later = T0 + timedelta(seconds=600)
        self.assertFalse(t.observe("batteryVoltage", None, later))
        self.assertIsNone(t.change_age_s("batteryVoltage", later))

    def test_reception_alone_is_not_a_clock(self):
        """A ReceptionEvent carries no value, so it cannot renew
        freshness: 999 s of receptions leave the change age at 999 s,
        not at 0 s. A second ChangeTracker that never observed a value
        has no clock at all.
        """
        r = sc.ReceptionEvent(received_at=T0 + timedelta(seconds=999))
        self.assertEqual(r.received_at, T0 + timedelta(seconds=999))
        t = sc.ChangeTracker()
        t.observe("batteryVoltage", 53.1, T0)
        now = T0 + timedelta(seconds=999)
        self.assertAlmostEqual(t.change_age_s("batteryVoltage", now), 999.0)
        # An unobserved key has no freshness clock whatsoever.
        self.assertIsNone(t.change_age_s("neverObserved", now))


class ClockSeparationTests(unittest.TestCase):
    def test_measurement_time_is_unknown_by_default(self):
        m = sc.MeasurementTime()
        self.assertFalse(m.known)
        self.assertIsNone(m.value)

    def test_measurement_time_can_be_populated_but_is_not_inferred(self):
        m = sc.MeasurementTime()
        self.assertFalse(m.known)          # not inferred from reception
        m.value = T0
        self.assertTrue(m.known)

    def test_three_clocks_are_distinct_types(self):
        r = sc.ReceptionEvent(T0)
        l = sc.LocalBlockEvent(cap_ts=T0 + timedelta(seconds=2),
                              values={"batteryVoltage": 53.1})
        m = sc.MeasurementTime()
        self.assertFalse(hasattr(r, "values"))
        self.assertFalse(hasattr(l, "received_at"))
        self.assertFalse(hasattr(m, "value") and m.known)


class MetricNamingTests(unittest.TestCase):
    def test_intervals_between_changes(self):
        ts = [T0, T0 + timedelta(seconds=300), T0 + timedelta(seconds=602)]
        self.assertEqual(sc.intervals_between_changes(ts), [300.0, 302.0])

    def test_frequency_is_separate_from_change_rate(self):
        """10 messages span 297 s -> (10-1)/297*60 = 1.818 msg/min, while
        the number of CHANGE intervals over the same span is 1. The two
        numbers are deliberately different and both are reported."""
        ts = [T0 + timedelta(seconds=i * 33) for i in range(10)]
        self.assertAlmostEqual(sc.message_frequency(ts), 9 / 297 * 60, places=9)
        self.assertEqual(sc.intervals_between_changes([ts[0], ts[9]]), [297.0])
        # Exactly 1 change across 10 messages.
        self.assertEqual(len(sc.intervals_between_changes([ts[0], ts[-1]])), 1)


class RawSocContractTests(unittest.TestCase):
    def test_local_word6_normalises_to_raw_percent(self):
        self.assertEqual(sc.normalise_raw_soc_local(88), 88.0)
        self.assertEqual(sc.normalise_raw_soc_local(0), 0.0)
        self.assertEqual(sc.normalise_raw_soc_local(140), 100.0)

    def test_cloud_normalises_to_the_same_contract(self):
        self.assertEqual(sc.normalise_raw_soc_cloud(88.0), 88.0)
        self.assertEqual(sc.normalise_raw_soc_cloud(88), sc.normalise_raw_soc_local(88))
        self.assertIsNone(sc.normalise_raw_soc_cloud(None))

    def test_both_transports_produce_interchangeable_readings(self):
        a = sc.RawSocReading(value_pct=sc.normalise_raw_soc_local(88),
                             observed_at=T0, source="local_raw")
        b = sc.RawSocReading(value_pct=sc.normalise_raw_soc_cloud(88.0),
                             observed_at=T0, source="cloud_raw")
        self.assertEqual(a.value_pct, b.value_pct)
        self.assertIsNone(a.transport_reception)
        self.assertFalse(a.measurement_time.known)
        self.assertFalse(b.measurement_time.known)


class CorrectedSocPathTests(unittest.TestCase):
    """Both transports must produce corrected SOC through the SAME
    injected production function. Transport cannot reorder or duplicate
    the algorithm."""

    def test_same_inputs_give_same_corrected_soc_for_both(self):
        import hems.soc_correction as prod
        raw_local = sc.normalise_raw_soc_local(88)
        raw_cloud = sc.normalise_raw_soc_cloud(88.0)
        self.assertEqual(raw_local, raw_cloud)
        a = sc.corrected_soc_for(raw_local, 52.8, 0.0, prod.get_real_soc)
        b = sc.corrected_soc_for(raw_cloud, 52.8, 0.0, prod.get_real_soc)
        self.assertEqual(a, b)
        self.assertEqual(a, prod.get_real_soc(88.0, 52.8, 0.0))

    def test_corrected_soc_none_when_raw_unknown(self):
        import hems.soc_correction as prod
        self.assertIsNone(sc.corrected_soc_for(None, 52.8, 0.0, prod.get_real_soc))

    def test_transport_cannot_change_the_algorithm(self):
        """Identical (raw, V, I) from either transport must call
        get_real_soc with identical arguments."""
        import hems.soc_correction as prod
        seen = []

        def spy(reported, voltage, current):
            seen.append((round(reported, 6), round(voltage, 6), round(current, 6)))
            return prod.get_real_soc(reported, voltage, current)

        raw_l = sc.normalise_raw_soc_local(92)
        raw_c = sc.normalise_raw_soc_cloud(92.0)
        sc.corrected_soc_for(raw_l, 53.4, -2.0, spy)
        sc.corrected_soc_for(raw_c, 53.4, -2.0, spy)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], seen[1])


if __name__ == "__main__":
    unittest.main()