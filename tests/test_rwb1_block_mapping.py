"""Regression tests for the independent 21-word mapping derivation.

Юра: «21-word mapping залиш кандидатом до повторних незалежних зіставлень.»

These tests use SYNTHETIC data with known ground truth, so the analyzer's
verdict logic is proved correct independently of any real capture. They
pin down:

  * a candidate with too few independent pairs never confirms, even with a
    perfect residual — this is what keeps a single lucky match from
    promoting a field;
  * a constant-offset candidate does NOT confirm (the constant-value trap:
    stable values fit many scales and produce zero spread while proving
    nothing about which HA field it is);
  * a genuine varying mapping confirms only with the right scale;
  * scale is never applied implicitly.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path("/opt/data/powmr-ai-work/powmr_inverter")
spec = importlib.util.spec_from_file_location(
    "rwb1_block_mapping", REPO / "scripts" / "rwb1_block_mapping.py"
)
bm = importlib.util.module_from_spec(spec)
sys.modules["rwb1_block_mapping"] = bm
spec.loader.exec_module(bm)


class SyntheticGroundTruth(unittest.TestCase):
    """Known mapping: word 1 = grid_voltage_v * 10, varying values."""

    def setUp(self):
        # 10 independent observations, grid voltage varies 230..239.
        self.grid = [230.0, 231.0, 232.0, 233.0, 234.0,
                     235.0, 236.0, 237.0, 238.0, 239.0]
        self.observations = []
        self.ha = {}
        for i, gv in enumerate(self.grid):
            words = [0] * 21
            words[1] = int(gv * 10)
            # a decoy field that is CONSTANT — the trap.
            words[5] = 440
            self.observations.append((1000 + i, words))
            self.ha[1000 + i] = (
                200.0,          # load_power_w
                gv,             # grid_voltage_v
                58.0,           # battery_voltage_v
                100.0,          # corrected_soc
                f"2026-10-20T00:00:{i:02d}+00:00",
            )

    def test_correct_scale_confirms(self):
        cands = bm.build_candidates(self.observations, self.ha)
        rows = bm.report(cands, min_n=5)
        # Find any confirmed candidate for grid_voltage_v.
        confirmed = [r for r in rows if r["ha_field"] == "grid_voltage_v"
                     and r["confirmed"]]
        self.assertTrue(confirmed, "the true mapping must be found")
        best = confirmed[0]
        self.assertEqual(best["word_index"], 1)
        self.assertAlmostEqual(best["scale"], 0.1)
        self.assertAlmostEqual(best["median_residual"], 0.0, places=6)

    def test_wrong_scale_does_not_confirm(self):
        """scale=1.0 would give 2300 vs 230 — a huge residual. It must fail."""
        cands = bm.build_candidates(
            self.observations, self.ha,
            scales=(1.0,),
        )
        rows = bm.report(cands, min_n=5)
        conf = [r for r in rows if r["ha_field"] == "grid_voltage_v" and r["confirmed"]]
        self.assertEqual(conf, [], "scale=1.0 must not confirm")

    def test_constant_field_cannot_confirm_by_spread_alone(self):
        """word 5 is constant 440. It fits corrected_soc*? or load*? with
        zero spread for some scale. Zero spread is NOT sufficient: it must
        also be tested against a field that actually varies, and the
        candidate must name the right field.

        The key protection: a constant candidate may have tiny spread, but
        it is only confirmed if its median residual is also tiny. Here
        440 can never equal a HA value in these units, so it fails.
        """
        c = bm.Candidate(word_index=5, scale=1.0, ha_field="grid_voltage_v")
        for i, sec in enumerate(sorted(self.ha)):
            c.pairs.append((440.0, self.ha[sec][1]))
        ok, reason = c.verdict(min_n=5, tol=5.0)
        self.assertFalse(ok)
        self.assertIn("exceed", reason)


class InsufficientEvidenceTests(unittest.TestCase):
    def test_few_pairs_never_confirm(self):
        """3 perfect pairs must not promote a field — this is what stops a
        single coincidental match from becoming 'confirmed'.
        """
        ha = {1000 + i: (200.0, 230.0 + i, 58.0, 100.0, f"t{i}") for i in range(3)}
        obs = []
        for i in range(3):
            words = [0] * 21
            words[1] = int((230.0 + i) * 10)
            obs.append((1000 + i, words))
        cands = bm.build_candidates(obs, ha)
        rows = bm.report(cands, min_n=5)
        for r in rows:
            self.assertFalse(r["confirmed"], f"{r} must not confirm on 3 pairs")
            if r["n"] == 3:
                self.assertIn("need 5", r["reason"])

    def test_min_n_is_honoured_exactly(self):
        ha = {1000 + i: (200.0, 230.0, 58.0, 100.0, f"t{i}") for i in range(5)}
        obs = [(1000 + i, [0] * 21) for i in range(5)]
        for i in range(5):
            obs[i][1][1] = 2300
        c = bm.Candidate(word_index=1, scale=0.1, ha_field="grid_voltage_v")
        for i in range(5):
            c.pairs.append((2300 * 0.1, 230.0))
        ok, _ = c.verdict(min_n=5)
        self.assertTrue(ok, "exactly 5 pairs with a perfect fit must confirm")
        ok2, reason2 = c.verdict(min_n=6)
        self.assertFalse(ok2, "raising min_n to 6 must block the same candidate")


class ResidualSpreadTests(unittest.TestCase):
    def test_constant_offset_fails_median(self):
        """A candidate that is consistently 10 units high fails even though
        its spread is 0. This is the 'stable values' trap from the RPC
        analysis, applied here: zero spread plus a wrong offset must not
        confirm.
        """
        c = bm.Candidate(word_index=3, scale=1.0, ha_field="grid_voltage_v")
        for i in range(10):
            c.pairs.append((240.0, 230.0))   # always +10
        ok, reason = c.verdict(min_n=5, tol=5.0)
        self.assertFalse(ok)
        self.assertIn("median residual", reason)

    def test_tolerance_is_in_ha_units(self):
        """tol is the HA field's unit, so a 0.5 V claim and a 0.5 W claim
        mean the same number. The test asserts the unit semantics.
        """
        c = bm.Candidate(word_index=3, scale=1.0, ha_field="grid_voltage_v")
        for i in range(10):
            c.pairs.append((230.0 + (i % 3) * 0.2, 230.0))
        ok_tight, _ = c.verdict(min_n=5, tol=0.5)
        ok_loose, _ = c.verdict(min_n=5, tol=5.0)
        self.assertTrue(ok_tight)
        self.assertTrue(ok_loose)
        # Now a 2.0 spread: passes a 5.0 tol, fails a 0.5 tol.
        c2 = bm.Candidate(word_index=3, scale=1.0, ha_field="grid_voltage_v")
        for i in range(10):
            c2.pairs.append((230.0 + i * 0.5, 230.0))   # spread 4.5
        ok2, _ = c2.verdict(min_n=5, tol=0.5)
        self.assertFalse(ok2, "spread 4.5 must fail a 0.5 tol")
        ok3, _ = c2.verdict(min_n=5, tol=5.0)
        self.assertTrue(ok3, "spread 4.5 must pass a 5.0 tol")


class NoImplicitScaleTests(unittest.TestCase):
    def test_decode_does_not_apply_scale(self):
        """decode_21_word_le returns raw word values. A caller must choose
        the scale; the decoder must not bake one in.
        """
        words = [0, 2340, 0, 0, 0, 440, 0, 0, 0, 0, 0,
                 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        out = bm.decode_21_word_le(words)
        self.assertEqual(out[1], 2340.0)
        self.assertEqual(out[5], 440.0)


if __name__ == "__main__":
    unittest.main()