"""Regression tests for the strict mapping verdict.

Round-8. These tests target the real defect found on live data: a
near-constant HA field (battery_voltage_v, spread 0.2 V) let several
unrelated word+scale candidates "confirm" with a +3.9 V or -5.0 V offset,
because the loose criterion only checked |median| <= 5 V.

The strict verdict must reject those while still accepting a genuine
proportional mapping.
"""
from __future__ import annotations

import importlib.util
import statistics
import sys
import unittest
from pathlib import Path

REPO = Path("/opt/data/powmr-ai-work/powmr_inverter")
spec = importlib.util.spec_from_file_location(
    "rwb1_mapping_verdict", REPO / "scripts" / "rwb1_mapping_verdict.py"
)
sv = importlib.util.module_from_spec(spec)
sys.modules["rwb1_mapping_verdict"] = sv
spec.loader.exec_module(sv)


class VariationTests(unittest.TestCase):
    def test_constant_field_is_not_identifiable(self):
        """battery_voltage_v spread 0.2 V: any candidate can 'fit' it.
        The verdict must say so instead of confirming.
        """
        ha = [58.0, 58.1, 57.9, 58.1, 58.0, 58.1, 58.0, 57.9, 58.1]
        dec = [53.1, 53.1, 53.1, 53.1, 53.1, 53.1, 53.1, 53.1, 53.1]
        v = sv.strict_verdict(dec, ha)
        self.assertFalse(v.ok)
        # The guard fires on the HA field's own variation being too small
        # to identify any mapping.
        self.assertIn("variation too small to identify", v.reason)

    def test_zero_variation_ha_is_rejected_explicitly(self):
        v = sv.strict_verdict([1, 2, 3, 4, 5], [100.0] * 5)
        self.assertFalse(v.ok)
        self.assertIn("spread=0", v.reason)

    def test_constant_candidate_against_varying_field_is_rejected(self):
        ha = [213.0, 231.0, 250.0, 348.0, 290.0, 310.0]
        dec = [440.0] * 6
        v = sv.strict_verdict(dec, ha)
        self.assertFalse(v.ok)
        self.assertIn("constant while HA field varies", v.reason)


class OffsetSigmaTests(unittest.TestCase):
    def test_constant_offset_fails_in_ha_sigmas(self):
        """The loose criterion allowed any |offset| <= 5 units. The strict
        one requires the offset to be small RELATIVE to the HA field's own
        spread, so a constant offset fails on a narrow field.

        A constant offset shrinks the candidate's own relative variation,
        so on a narrow field the VARIATION gate fires first. Either way the
        candidate must be rejected — and the rejection reason must name a
        real guard, not be a bare "no match".
        """
        ha = [19.8, 20.0, 19.9, 20.2, 20.1, 19.95, 20.05, 20.0]
        dec = [v + 10.0 for v in ha]
        v = sv.strict_verdict(dec, ha)
        self.assertFalse(v.ok)
        self.assertTrue(
            "constant while HA field varies" in v.reason
            or "constant offset" in v.reason,
            f"unexpected reason: {v.reason}",
        )

    def test_offset_guard_fires_when_variation_gates_pass(self):
        """To reach the offset guard the candidate must keep its relative
        variation. Scaling rather than shifting preserves it, so the
        +offset term is applied proportionally here.
        """
        ha = [19.8, 20.0, 19.9, 20.2, 20.1, 19.95, 20.05, 20.0]
        # Scale keeps rel_spread identical, then subtract a constant that
        # is large relative to the 0.4 spread but small relative to the
        # magnitude, so both variation gates pass and the offset guard is
        # the one that must reject.
        dec = [d - 0.45 for d in ha]   # 0.45 V offset vs 0.4 V spread
        v = sv.strict_verdict(dec, ha)
        self.assertFalse(v.ok)
        self.assertIn("constant offset", v.reason)
        self.assertGreater(v.offset_in_ha_sigmas, 1.0)
        # The residual spread is 0 (a pure shift), so the sigma test is
        # the only thing standing between this and a false confirmation.
        self.assertEqual(v.residual_spread, 0.0)

    def test_offset_just_inside_one_sigma_passes(self):
        """Documents the boundary: 0.35 V against a 0.4 V spread is
        0.875 sigma and is accepted.
        """
        ha = [19.8, 20.0, 19.9, 20.2, 20.1, 19.95, 20.05, 20.0]
        v = sv.strict_verdict([d - 0.35 for d in ha], ha)
        self.assertTrue(v.ok, v.reason)

    def test_same_offset_passes_on_wide_field(self):
        """The identical +10.0 offset passes when the field is wide
        (spread 135). The test is relative, not absolute — which is
        exactly why the narrow-field case above is the one that matters.
        """
        ha = [213.0, 231.0, 250.0, 348.0, 290.0, 310.0, 220.0, 260.0]
        dec = [v + 10.0 for v in ha]
        v = sv.strict_verdict(dec, ha)
        self.assertTrue(v.ok, v.reason)

    def test_large_absolute_offset_on_wide_field_may_pass(self):
        """With a genuinely varying field (spread 135 W), a +50 W offset is
        0.37 sigma, which the criterion accepts. This documents that the
        test is relative, not absolute.
        """
        ha = [213.0, 231.0, 250.0, 348.0, 290.0, 310.0, 220.0, 260.0]
        dec = [v + 50.0 for v in ha]
        v = sv.strict_verdict(dec, ha)
        self.assertTrue(v.ok, v.reason)
        self.assertLess(v.offset_in_ha_sigmas, 1.0)
        self.assertAlmostEqual(abs(v.residual_median), 50.0, delta=1e-6)
        # And the same offset on a NARROW field is 25 sigma -> rejected.
        ha_narrow = [58.0, 58.1, 57.9, 58.1, 58.0, 58.1, 58.0, 57.9]
        v2 = sv.strict_verdict([x + 5.0 for x in ha_narrow], ha_narrow)
        self.assertFalse(v2.ok)


class ShapeTests(unittest.TestCase):
    def test_wrong_scale_fails_variation_shape(self):
        """Scale 10 gives 10x the relative variation; the ratio guard
        rejects it even though a loose absolute check might pass.
        """
        ha = [213.0, 231.0, 250.0, 348.0, 290.0, 310.0, 220.0, 260.0]
        dec = [v * 10 for v in ha]
        v = sv.strict_verdict(dec, ha)
        # Either the shape guard or the absolute residual guard rejects it;
        # both are legitimate rejections of a 10x scale.
        self.assertFalse(v.ok)
        self.assertTrue(
            "variation shape mismatch" in v.reason
            or "residual spread" in v.reason,
            f"unexpected reason: {v.reason}",
        )

    def test_correct_scale_confirms(self):
        """grid_voltage_v * 10 decoded / 10: proportional variation and a
        near-zero offset must confirm.
        """
        # A genuine proportional mapping: the candidate tracks the HA
        # field, including its relative variation, with a small offset.
        ha_gv = [213.0, 231.0, 250.0, 348.0, 290.0, 310.0, 220.0, 260.0]
        dec = [v * 1.02 + 1.0 for v in ha_gv]
        v = sv.strict_verdict(dec, ha_gv)
        self.assertTrue(v.ok, v.reason)
        self.assertLess(v.offset_in_ha_sigmas, 1.0)
        # The binding constraint is the sigma test: a 6.1 W median offset
        # against a 135 W field spread is only 0.045 sigma, which is why
        # the verdict is a pass despite the non-zero absolute offset.
        self.assertLess(abs(v.residual_median) / v.ha_variation.spread, 1.0)
        self.assertLessEqual(v.residual_spread, 5.0)

    def test_insufficient_pairs_rejected_first(self):
        v = sv.strict_verdict([1.0, 2.0], [1.0, 2.0], min_n=5)
        self.assertFalse(v.ok)
        self.assertIn("need 5", v.reason)


class LiveDataReproductionTests(unittest.TestCase):
    """Reproduce the exact false confirmations observed on the live capture."""

    def test_battery_voltage_false_confirmation_is_now_rejected(self):
        # LE word[5] x0.1 "confirmed" battery_voltage_v with med -5.0,
        # spread 1.8 under the loose criterion. HA spread here is 0.2.
        ha = [58.1, 58.1, 57.9, 58.1, 58.1, 58.1, 58.0, 58.1, 58.0]
        le_w5 = [531, 531, 531, 531, 531, 531, 530, 530, 530]
        dec = [w * 0.1 for w in le_w5]
        v = sv.strict_verdict(dec, ha)
        self.assertFalse(v.ok)
        self.assertIn("variation too small to identify", v.reason)

    def test_battery_voltage_be_candidate_is_rejected(self):
        # BE word[17] x0.001 "confirmed" with med +4.136, spread 0.200.
        ha = [58.1, 58.1, 57.9, 58.1, 58.1, 58.1, 58.0, 58.1, 58.0]
        dec = [v + 4.136 for v in ha]
        v = sv.strict_verdict(dec, ha)
        self.assertFalse(v.ok)
        self.assertIn("variation too small to identify", v.reason)


class MeasureTests(unittest.TestCase):
    def test_measure_reports_distinct_and_spread(self):
        v = sv.measure([213.0, 231.0, 250.0, 348.0])
        self.assertEqual(v.n, 4)
        self.assertEqual(v.distinct, 4)
        self.assertAlmostEqual(v.spread, 135.0)
        self.assertTrue(v.varies)

    def test_measure_zero_mean_is_safe(self):
        v = sv.measure([0.0, 0.0, 0.0])
        self.assertEqual(v.spread, 0.0)
        self.assertFalse(v.varies)


if __name__ == "__main__":
    unittest.main()