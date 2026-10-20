"""Behavioural tests: SOC contract, HEMS impact, outage planner impact.

The scenario is the real one from the evidence journal: on 2026-10-20 at
04:04 UTC the charging mode changed (current_charger_mode 1 -> 2),
batteryVoltage moved 58.0 -> 52.8 V, and corrected_soc stepped
100.0 -> 80.0 in one cycle while batterySoc stayed 100.0.

These tests pin:
  * the contract distinguishes raw from estimated and labels quality;
  * the slew limiter removes the 20-point step WITHOUT changing the
    reported value;
  * HEMS receives a usable SOC in both readings;
  * the outage planner's energy budget changes consistently with the
    reading it is given, and a quality downgrade is visible to it.
"""
from __future__ import annotations

import unittest

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, _REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sc = _load("soc_correction_under_test", "hems/soc_correction.py")
_soc_contract = _load("soc_contract_under_test", "hems/soc_contract.py")

from soc_contract_under_test import (
    DEFAULT_SLEW_POINTS,
    SocReading,
    series,
    soc_reading,
)


def read(reported, voltage, current=0.0, prev=None, slew=DEFAULT_SLEW_POINTS):
    return soc_reading(
        reported, voltage, current,
        get_real_soc=sc.get_real_soc,
        voltage_to_soc=sc.voltage_to_soc,
        previous_estimated=prev,
        slew_points=slew,
    )


class RawVersusEstimated(unittest.TestCase):
    def test_the_jump_is_visible_as_a_kind_change(self):
        before = read(100.0, 58.0)
        after = read(100.0, 52.8, prev=before.estimated)
        # The RAW device value never moved.
        self.assertEqual(before.raw, 100.0)
        self.assertEqual(after.raw, 100.0)
        # The KIND changed: reported -> estimated.
        self.assertEqual(before.quality, "reported")
        self.assertEqual(after.quality, "estimated")
        self.assertTrue(after.is_estimated)

    def test_unlimited_series_reproduces_the_20_point_step(self):
        """Without the limiter the contract still exposes the step — it
        does not hide the problem, it labels it."""
        a = read(100.0, 58.0, slew=None)
        b = read(100.0, 52.8, prev=a.estimated, slew=None)
        self.assertAlmostEqual(a.estimated, 100.0)
        self.assertAlmostEqual(b.estimated, 80.0)
        self.assertAlmostEqual(b.estimated - a.estimated, -20.0)
        self.assertFalse(b.slew_limited)

    def test_slew_limited_series_removes_the_step(self):
        a = read(100.0, 58.0)
        b = read(100.0, 52.8, prev=a.estimated)
        self.assertTrue(b.slew_limited)
        self.assertAlmostEqual(b.estimated - a.estimated, -DEFAULT_SLEW_POINTS)
        # The step is gone, but the KIND change is still reported.
        # Quality stays "estimated": the value is a lagged voltage
        # estimate, not a mix of report and estimate. "blended" is
        # reserved for a REPORTED value that was slew-limited.
        self.assertEqual(b.quality, "estimated")
        self.assertIn("step limited", b.reason)

    def test_blended_is_used_when_a_reported_value_is_limited(self):
        """If a reader's estimate happened to equal the device report
        and then jumped, the result is a mix over time -> blended."""
        a = read(100.0, 58.0)
        self.assertEqual(a.quality, "reported")
        b = read(100.0, 58.0, prev=a.estimated + 30.0)
        self.assertTrue(b.slew_limited)
        self.assertEqual(b.quality, "blended")

    def test_slew_never_moves_the_reported_value(self):
        a = read(100.0, 58.0)
        b = read(100.0, 52.8, prev=a.estimated)
        self.assertEqual(b.raw, 100.0)
        self.assertNotEqual(b.estimated, b.raw)

    def test_missing_soc_is_unknown_not_guessed(self):
        r = read(None, 52.8)
        self.assertIsNone(r.raw)
        self.assertIsNone(r.estimated)
        self.assertEqual(r.quality, "unknown")

    def test_low_voltage_keeps_the_report(self):
        r = read(64.0, 5.0)
        self.assertEqual(r.quality, "reported")
        self.assertAlmostEqual(r.estimated, 64.0)
        self.assertIsNone(r.voltage_soc)


class SlewBehaviour(unittest.TestCase):
    def test_normal_drift_is_not_limited(self):
        a = read(88.0, 53.2)
        b = read(88.0, 53.15, prev=a.estimated)
        self.assertFalse(b.slew_limited)

    def test_slew_is_bidirectional(self):
        a = read(40.0, 50.0)
        b = read(100.0, 55.0, prev=a.estimated)
        self.assertTrue(b.slew_limited)
        self.assertAlmostEqual(b.estimated - a.estimated, DEFAULT_SLEW_POINTS)

    def test_slew_survives_repeated_large_steps(self):
        """A sustained disagreement walks the estimate down rather than
        jumping, and every step stays within the bound."""
        est = 100.0
        steps = []
        for _ in range(6):
            r = read(100.0, 52.8, prev=est)
            steps.append(r.estimated - est)
            est = r.estimated
        for s in steps:
            self.assertLessEqual(abs(s), DEFAULT_SLEW_POINTS + 1e-9)
        self.assertLess(est, 100.0)

    def test_series_reports_max_step_per_channel(self):
        a = read(100.0, 58.0)
        b = read(100.0, 52.8, prev=a.estimated)
        s = series([a, b])
        self.assertEqual(s["n"], 2)
        self.assertEqual(s["raw_max_step"], 0.0)          # raw never moved
        self.assertLessEqual(s["estimated_max_step"], DEFAULT_SLEW_POINTS)
        self.assertTrue(s["quality_changes"])


class HemsBehaviour(unittest.TestCase):
    """hems/telemetry.py:345 prefers corrected_soc, else batterySoc."""

    @staticmethod
    def _soc_value(reading: SocReading, fallback_raw_key=None):
        """Mirror of hems/telemetry.py soc selection, fed by the contract."""
        if reading.estimated is not None:
            return float(reading.estimated), "api"
        if fallback_raw_key is not None:
            return float(fallback_raw_key), "api"
        return None, "fallback"

    def test_hems_sees_a_usable_soc_in_both_states(self):
        a = read(100.0, 58.0)
        b = read(100.0, 52.8, prev=a.estimated)
        va, _ = self._soc_value(a)
        vb, _ = self._soc_value(b)
        self.assertIsNotNone(va)
        self.assertIsNotNone(vb)
        # Without the limiter HEMS would see a 20-point drop in one cycle.
        self.assertGreaterEqual(vb, va - DEFAULT_SLEW_POINTS)

    def test_reserve_gate_is_not_crossed_by_the_step(self):
        """HEMS blocks on soc <= reserve. The step must not push a
        battery that was above reserve below it in one cycle."""
        reserve = 20.0
        a = read(25.0, 52.0)
        b = read(25.0, 51.5, prev=a.estimated)
        _, _ = self._soc_value(a), self._soc_value(b)
        self.assertGreater(a.estimated, reserve)
        self.assertGreater(b.estimated, reserve)

    def test_hems_cannot_tell_raw_from_estimated_without_the_contract(self):
        """Documents the gap the contract closes: a bare float gives a
        consumer no way to know the number changed KIND."""
        bare_before = sc.get_real_soc(100.0, 58.0, 0.0)
        bare_after = sc.get_real_soc(100.0, 52.8, 0.0)
        self.assertIsInstance(bare_before, float)
        self.assertIsInstance(bare_after, float)
        r = read(100.0, 52.8)
        self.assertIsInstance(r, SocReading)


class OutagePlannerBehaviour(unittest.TestCase):
    """services/outage_runtime.py:346-351 prefers correctedSoc, else
    batterySoc. outage_planner.py:996-1003 reads
    telemetry.battery_soc_pct."""

    CAPACITY_AH = 230.0
    NOMINAL_V = 51.2
    DISCHARGE_EFF = 0.95

    def _battery_remaining_kwh(self, soc_pct: float) -> float:
        usable = max(0.0, (soc_pct - 20.0) / 100.0) * self.CAPACITY_AH * self.NOMINAL_V
        return usable * self.DISCHARGE_EFF

    def test_planner_sees_usable_energy_in_both_states(self):
        a = read(100.0, 58.0)
        b = read(100.0, 52.8, prev=a.estimated)
        ea = self._battery_remaining_kwh(a.estimated)
        eb = self._battery_remaining_kwh(b.estimated)
        self.assertGreater(ea, 0.0)
        self.assertGreater(eb, 0.0)

    def test_no_20_point_energy_loss_in_one_cycle(self):
        """The unbounded step costs the planner real budget:
        100 % -> 80 % over reserve 20 % is a 20-point drop."""
        a = read(100.0, 58.0)
        unbounded = read(100.0, 52.8, prev=a.estimated, slew=None)
        bounded = read(100.0, 52.8, prev=a.estimated)
        cost_unbounded = self._battery_remaining_kwh(a.estimated) \
            - self._battery_remaining_kwh(unbounded.estimated)
        cost_bounded = self._battery_remaining_kwh(a.estimated) \
            - self._battery_remaining_kwh(bounded.estimated)
        per_point = self.CAPACITY_AH * self.NOMINAL_V * self.DISCHARGE_EFF / 100.0
        self.assertAlmostEqual(cost_unbounded, 20.0 * per_point, places=3)
        self.assertLess(cost_bounded, cost_unbounded / 3)

    def test_planner_tariff_path_is_unaffected_by_kind(self):
        """Tariff planning consumes telemetry.battery_soc_pct. The
        contract does not change which field the planner reads; it only
        tells the planner whether the number it received is reported."""
        a = read(100.0, 58.0)
        self.assertEqual(a.estimated, a.raw)     # reported == raw
        b = read(100.0, 52.8, prev=a.estimated, slew=None)
        self.assertNotEqual(b.estimated, b.raw)  # estimated != raw

    def test_quality_is_carried_for_the_planner_to_choose(self):
        a = read(100.0, 58.0)
        b = read(100.0, 52.8, prev=a.estimated, slew=None)
        self.assertEqual(a.quality, "reported")
        self.assertEqual(b.quality, "estimated")
        # A consumer preferring reported data can stay on 100 % in both.
        self.assertEqual(a.raw, b.raw)


if __name__ == "__main__":
    unittest.main()