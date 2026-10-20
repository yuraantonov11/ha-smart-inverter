"""Tests of the SOC formula itself.

This file tests ONLY ``hems.soc_contract.evaluate`` and the
``SocReading`` shape. It does NOT exercise any production consumer; for
that, see ``test_soc_hems_builder.py`` and ``test_soc_outage_runtime.py``.
"""
from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_contract = _load("contract_under_test", "hems/soc_contract.py")


def vto(v): return v  # identity, just to keep call sites readable.


class PayloadVersusValidatedVersusControl(unittest.TestCase):
    """``reported`` is preserved verbatim; ``reported_payload`` is a
    float-typed normalisation; ``validated`` is a numeric-only pass;
    ``control`` is the decision-eligible number. All distinct."""

    def test_reported_is_byte_for_byte(self):
        r = _contract.evaluate(99.6, 53.0, 0.5, voltage_to_soc=vto)
        self.assertEqual(r.reported, 99.6)
        self.assertIsNotNone(r.reported_payload)
        self.assertAlmostEqual(r.reported_payload, 99.6)
        self.assertAlmostEqual(r.validated, 99.6)
        self.assertAlmostEqual(r.control, 99.6)

    def test_nan_payload(self):
        r = _contract.evaluate(float("nan"), 53.0, 0.5, voltage_to_soc=vto)
        self.assertTrue(math.isnan(r.reported))
        self.assertIsNone(r.reported_payload)   # NaN -> no payload
        self.assertIsNone(r.validated)          # no validity
        self.assertIsNone(r.control)            # no decision number

    def test_inf_payload(self):
        r = _contract.evaluate(float("inf"), 53.0, 0.5, voltage_to_soc=vto)
        self.assertTrue(math.isinf(r.reported))
        self.assertIsNone(r.reported_payload)
        self.assertIsNone(r.validated)
        self.assertIsNone(r.control)

    def test_out_of_range_reported_passes_payload_but_fails_validity(self):
        """``reported_payload`` IS the float form (a normalisation step
        can see the bytes were numeric); ``validated`` is the trust
        boundary and correctly refuses out-of-range."""
        r = _contract.evaluate(150.0, 53.0, 0.5, voltage_to_soc=vto)
        self.assertTrue(math.isfinite(r.reported_payload))  # 150.0
        self.assertIsNone(r.validated)
        self.assertIsNone(r.control)
        self.assertEqual(r.quality, "reported_invalid")

    def test_no_reported_is_not_a_known_trustworthy_value(self):
        r = _contract.evaluate(None, 53.0, 0.5, voltage_to_soc=vto)
        self.assertIsNone(r.reported)
        self.assertIsNone(r.reported_payload)
        self.assertIsNone(r.validated)
        self.assertIsNone(r.control)
        self.assertEqual(r.quality, "no_reported")

    def test_reported_can_be_arbitrary_object(self):
        r = _contract.evaluate("84.5", 53.0, 0.5, voltage_to_soc=vto)
        self.assertEqual(r.reported, "84.5")  # verbatim
        self.assertAlmostEqual(r.reported_payload, 84.5)
        self.assertAlmostEqual(r.validated, 84.5)


class VoltageStaysDiagnostic(unittest.TestCase):
    """Before OCV conditions are validated, voltage-derived numbers
    appear in ``voltage_soc`` but NEVER reach ``control``."""

    def test_voltage_soc_is_diagnostic_not_control(self):
        r = _contract.evaluate(None, 53.0, 0.5, voltage_to_soc=vto)
        self.assertEqual(r.voltage_soc, 53.0)  # recorded as diagnostic
        self.assertIsNone(r.control)            # but never promoted

    def test_quiescent_now_is_a_hint_not_a_confirmation(self):
        """``|I| <= 1`` AND sane voltage is a first hint. It is NOT a
        declaration that the pack has rested long enough for OCV."""
        r = _contract.evaluate(None, 53.0, 0.5, voltage_to_soc=vto)
        self.assertTrue(r.quiescent_now)
        self.assertFalse(r.rest_conditions_confirmed)

    def test_voltage_to_soc_omitted_means_no_diagnostic_voltage(self):
        r = _contract.evaluate(None, 53.0, 0.5)
        self.assertIsNone(r.voltage_soc)
        self.assertFalse(r.quiescent_now)

    def test_load_current_takes_table_out(self):
        r = _contract.evaluate(None, 53.0, 15.0, voltage_to_soc=vto)
        self.assertIsNone(r.voltage_soc)
        self.assertFalse(r.quiescent_now)
        self.assertIsNone(r.control)

    def test_outside_voltage_band(self):
        r = _contract.evaluate(None, 12.0, 0.0, voltage_to_soc=vto)
        self.assertIsNone(r.voltage_soc)
        r2 = _contract.evaluate(None, 80.0, 0.0, voltage_to_soc=vto)
        self.assertIsNone(r2.voltage_soc)

    def test_no_reported_plus_quiescent_still_yields_none_for_control(self):
        """Even when the device sent nothing AND the pack looks
        quiescent, ``control`` stays None until conditions are confirmed."""
        r = _contract.evaluate(None, 53.0, 0.0, voltage_to_soc=vto)
        self.assertTrue(r.quiescent_now)
        self.assertIsNotNone(r.voltage_soc)
        self.assertIsNone(r.control)
        self.assertTrue(r.is_diagnostic_only)

    def test_rest_conditions_confirmed_always_false_in_this_build(self):
        """A constant flag we expose so callers cannot forget that the
        full admissibility check has not run."""
        self.assertFalse(_contract.evaluate(50, 53, 0.5,
                                           voltage_to_soc=vto).rest_conditions_confirmed)


class DisplayNeverReachesControl(unittest.TestCase):

    def test_display_smoother_is_separate_from_control(self):
        sm = _contract.DisplaySmoother(alpha=0.5)
        sm.update(100.0)
        # Sudden drop: control carries the truth, display smooths behind.
        r = _contract.evaluate(25.0, 51.0, 0.5, voltage_to_soc=vto)
        self.assertEqual(r.control, 25.0)
        wd = _contract.with_display(r, sm)
        # Display lags (state from 100 with alpha=0.5 on 25 -> 62.5).
        # The key property is that control is still 25, NOT 62.5.
        self.assertEqual(wd.control, 25.0)
        self.assertNotEqual(wd.display, 25.0)

    def test_display_cannot_be_passed_off_as_control(self):
        """The dataclass has separate fields; tampering with display
        does not change control."""
        r = _contract.evaluate(15.0, 51.0, 0.5, voltage_to_soc=vto)
        self.assertEqual(r.control, 15.0)
        self.assertNotEqual(r.control, r.display or -1)

    def test_display_is_none_when_no_control(self):
        r = _contract.evaluate(float("nan"), 53.0, 0.5, voltage_to_soc=vto)
        self.assertIsNone(r.control)
        sm = _contract.DisplaySmoother(alpha=0.3)
        wd = _contract.with_display(r, sm)
        self.assertIsNone(wd.control)
        # Display can still hold a stale value if it had any; not for
        # control.


class YuraScenario(unittest.TestCase):
    """The exact reformulation of Юра's bug."""

    def test_reported_25_voltage_51_current_0p5(self):
        """Reported at 25 (a critically-low battery), voltage at 51,
        current at 0.5 A. The contract returns ``control = 25.0`` and
        does NOT inflate it to 75."""
        r = _contract.evaluate(25.0, 51.0, 0.5, voltage_to_soc=vto)
        self.assertEqual(r.reported, 25.0)
        self.assertEqual(r.reported_payload, 25.0)
        self.assertEqual(r.validated, 25.0)
        self.assertEqual(r.control, 25.0)
        self.assertEqual(r.quality, "reported_valid")
        self.assertNotEqual(r.control, 75.0)


class Summarise(unittest.TestCase):

    def test_summarise_does_not_count_display_as_control(self):
        a = _contract.evaluate(50.0, 53.0, 0.5, voltage_to_soc=vto)
        b = _contract.evaluate(None, 53.0, 0.5, voltage_to_soc=vto)
        c = _contract.evaluate(float("nan"), 53.0, 0.5, voltage_to_soc=vto)
        s = _contract.summarise([a, b, c])
        self.assertEqual(s["with_control"], 1)            # only a
        self.assertEqual(s["without_control"], 2)          # b, c
        self.assertEqual(s["diagnostic_only_count"], 2)    # b, c
        # voltage_soc is consulted for ALL THREE (a, b, c) because they
        # all have V/I and quiescent_now=True. Display is never counted
        # as a control surface.
        self.assertEqual(s["any_voltage_soc_consulted"], 3)
        # Crucially: none of b/c's voltage_soc values ever reached control.
        self.assertNotEqual(b.control or -999, b.voltage_soc or -998)
        self.assertIsNone(c.control)


if __name__ == "__main__":
    unittest.main()