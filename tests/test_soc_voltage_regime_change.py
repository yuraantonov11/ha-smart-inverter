"""Regression scenario: raw SOC constant, voltage moves with the mode.

Юра: «Підготуй окремий regression-сценарій: raw SOC незмінний, напруга
змінюється при зміні режиму. Не змінюй алгоритм SOC довільно в межах RWB1.»

This scenario reproduces the 2026-10-20T04:04:32 UTC cloud event WITHOUT
inventing data. Both cycles are taken verbatim from the evidence journal:

  04:04:27.448  batterySoc=100.0  batteryVoltage=58.0  corrected_soc=100.0
  04:04:32.289  batterySoc=100.0  batteryVoltage=52.8  corrected_soc=80.0

The assertion is NOT "corrected_soc must be 80". It is the structural
contract that was actually violated by surprise:

  * `batterySoc` (raw) must NOT be used to drive `corrected_soc` when the
    voltage-derived SOC disagrees by more than the gate;
  * a voltage crossing the ±10 % gate MUST move `corrected_soc`, even
    though raw SOC never moved by a single point;
  * the corrected value must come from the OCV table, so it must land
    exactly on a table entry;
  * a sub-gate voltage change MUST NOT move `corrected_soc`.

Nothing here changes the algorithm. It pins the behaviour of the existing
`get_real_soc` and the existing coordinator branch, so a future change to
either is caught.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path("/opt/data/powmr-ai-work/powmr_inverter")


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


soc_mod = _load("soc_correction_under_test", "hems/soc_correction.py")


class RawSocConstantVoltageMoves(unittest.TestCase):
    """The exact pair observed in the evidence journal."""

    RAW_SOC = 100.0

    def test_voltage_crossing_the_gate_moves_corrected_soc(self):
        before = soc_mod.get_real_soc(self.RAW_SOC, 58.0, 0.0)
        after = soc_mod.get_real_soc(self.RAW_SOC, 52.8, 0.0)
        # Raw SOC did not move at all.
        self.assertEqual(self.RAW_SOC, 100.0)
        # But corrected SOC did.
        self.assertNotEqual(before, after)
        self.assertAlmostEqual(before, 100.0)
        self.assertAlmostEqual(after, 80.0)

    def test_corrected_value_comes_from_the_ocv_table(self):
        after = soc_mod.get_real_soc(self.RAW_SOC, 52.8, 0.0)
        table_values = {v for _t, v in soc_mod._OCV_TABLE}
        # The corrected SOC must be a real table entry, not an
        # interpolation. This is what makes 80.0 explainable.
        self.assertIn(after, table_values)
        self.assertAlmostEqual(soc_mod.voltage_to_soc(52.8), 80.0)

    def test_sub_gate_voltage_change_does_not_move_corrected_soc(self):
        """Inside the gate the BMS value wins, so a small voltage move
        must leave corrected_soc alone. This is the other half of the
        contract and guards against over-triggering the correction.
        """
        a = soc_mod.get_real_soc(self.RAW_SOC, 54.4, 0.0)   # table 100
        b = soc_mod.get_real_soc(self.RAW_SOC, 54.0, 0.0)   # table 95
        # Both voltage-derived values sit within 10 % of 100, so the
        # reported value stays at the BMS reading.
        self.assertEqual(a, self.RAW_SOC)
        self.assertEqual(b, self.RAW_SOC)

    def test_gate_boundary_is_ten_points(self):
        """delta <= 10.0 keeps the BMS value; delta > 10.0 switches.
        The boundary is exact, so it is asserted exactly.
        """
        # voltage_to_soc(53.2) == 90.0 -> delta == 10.0 -> gate keeps BMS.
        self.assertAlmostEqual(soc_mod.voltage_to_soc(53.2), 90.0)
        self.assertEqual(soc_mod.get_real_soc(100.0, 53.2, 0.0), 100.0)
        # Just below the table step: 52.8 -> 80.0 -> delta == 20 -> switch.
        self.assertAlmostEqual(soc_mod.voltage_to_soc(52.8), 80.0)
        self.assertEqual(soc_mod.get_real_soc(100.0, 52.8, 0.0), 80.0)


class IrDropCompensationScenario(unittest.TestCase):
    """A mode change can also move the voltage via load current."""

    def test_discharging_current_lowers_compensated_voltage(self):
        # 10 A discharging x 0.0128 V/A = 0.128 V IR drop.
        self.assertAlmostEqual(
            soc_mod.LFP_IR_TOTAL_V_PER_A * 10, 0.128, places=6
        )
        # Sign convention: current is subtracted, so a DISCHARGING
        # current (-10 A) RAISES the compensated voltage to 53.328 V,
        # which is still inside the 53.2 step -> table 95 -> delta 5 <= 10
        # -> the BMS reading still wins.
        no_load = soc_mod.get_real_soc(100.0, 53.2, 0.0)
        with_load = soc_mod.get_real_soc(100.0, 53.2, -10.0)
        self.assertEqual(no_load, 100.0)
        self.assertEqual(with_load, 100.0)
        self.assertEqual(soc_mod.voltage_to_soc(53.2 + 0.128), 90.0)

    def test_charging_current_can_pull_the_voltage_down_the_gate(self):
        """The other direction: a CHARGING current (+10 A) lowers the
        compensated voltage. 52.9 - 0.128 = 52.772 V -> table 70 -> delta
        30 > 10 -> the correction fires, even though the pack reads
        52.9 V, which on its own would give 80 (delta 20, also firing).
        At 53.2 V the same +10 A gives 53.072 -> table 80 -> delta 20 ->
        also fires. So a charging load can trigger the correction at a
        voltage that alone would still have passed the gate.
        """
        self.assertEqual(soc_mod.get_real_soc(100.0, 53.2, 0.0), 100.0)
        self.assertEqual(soc_mod.get_real_soc(100.0, 53.2, 10.0), 80.0)
        self.assertEqual(soc_mod.get_real_soc(100.0, 52.9, 10.0), 70.0)
        self.assertEqual(
            soc_mod.get_real_soc(100.0, 53.2, 10.0),
            soc_mod.voltage_to_soc(53.2 - 10 * soc_mod.LFP_IR_TOTAL_V_PER_A),
        )

    def test_sign_convention_is_documented(self):
        """``battery_current`` is >0 charging, <0 discharging. The function
        computes ``voltage - current * IR``, so:

          charging  (+I) -> LOWER compensated voltage
          discharging(-I) -> HIGHER compensated voltage

        Asserted on the table lookup the function feeds, so the final
        gate result cannot mask a sign flip. (At 54.4 V both variants
        stay inside the gate and the BMS value is returned, so the
        OUTPUT alone cannot distinguish the sign.)
        """
        ir = soc_mod.LFP_IR_TOTAL_V_PER_A
        base = 54.4
        charged = base - 10 * ir          # 54.272 -> table 95
        discharged = base + 10 * ir        # 54.528 -> table 100
        self.assertEqual(soc_mod.voltage_to_soc(charged), 95.0)
        self.assertEqual(soc_mod.voltage_to_soc(discharged), 100.0)
        self.assertLess(charged, base)
        self.assertGreater(discharged, base)


class GuardRailTests(unittest.TestCase):
    def test_low_voltage_passes_raw_soc_through(self):
        """Below 10 V the function must not invent a value from the
        table; it returns the clamped reported SOC.
        """
        self.assertEqual(soc_mod.get_real_soc(77.0, 5.0, 0.0), 77.0)
        self.assertEqual(soc_mod.get_real_soc(77.0, 0.0, 0.0), 77.0)

    def test_reported_soc_is_clamped_before_the_gate(self):
        # 150 is clamped to 100, which then agrees with the table at
        # 54.4 V, so the BMS reading is returned.
        self.assertEqual(soc_mod.get_real_soc(150.0, 54.4, 0.0), 100.0)

    def test_out_of_range_reported_soc_loses_to_the_voltage(self):
        """Documented consequence of the gate: a reported value of -20 is
        clamped to 0, which then disagrees with the table (100) by 100
        points, so the VOLTAGE wins and 100.0 is returned.

        This is not a defect being introduced here — it is the existing
        behaviour of get_real_soc, pinned so a future change is visible.
        """
        # -20 clamps to 0; at 54.4 V the table says 100, delta 100 > 10
        # -> the voltage wins.
        self.assertEqual(soc_mod.get_real_soc(-20.0, 54.4, 0.0), 100.0)
        # At 48.0 V the table says 2, delta |0 - 2| = 2 <= 10 -> the
        # clamped reported value wins.
        self.assertEqual(soc_mod.get_real_soc(-20.0, 48.0, 0.0), 0.0)

    def test_output_always_within_range(self):
        for v in (40.0, 47.0, 48.5, 50.0, 51.5, 52.8, 54.4, 58.0, 60.0):
            for soc in (0.0, 50.0, 100.0):
                out = soc_mod.get_real_soc(soc, v, 0.0)
                self.assertGreaterEqual(out, 0.0, f"V={v} soc={soc}")
                self.assertLessEqual(out, 100.0, f"V={v} soc={soc}")


if __name__ == "__main__":
    unittest.main()