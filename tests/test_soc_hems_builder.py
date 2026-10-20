"""SOC contract through the REAL HEMS builder.

These tests call ``hems.telemetry.build_planner_inputs`` and assert on
its actual fields. They exercise the same code path that production
runs and do not duplicate the builder's logic.

The current production builder already sets ``soc = None`` and
``soc_unknown = True`` for invalid readings (T01 hardening). The
contract complements that with the diagnostic / control split: invalid
``reported`` does NOT get silently turned into a control number; valid
``reported`` flows straight through (because there is no second source
yet for a cross-check).
"""
from __future__ import annotations

import importlib.util
import math
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

# Standalone-runner-style: sys.path puts the repo at module root, so
# ``from hems.telemetry import build_planner_inputs`` resolves as in the
# existing tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hems.telemetry import build_planner_inputs  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


REPO = Path(__file__).resolve().parent.parent
_sc = _load("sc_h", "hems/soc_correction.py")
_contract = _load("contract_h", "hems/soc_contract.py")


def _inputs(raw, corrected_soc=None):
    return build_planner_inputs(
        raw=raw, now=datetime(2026, 10, 20, 4, 0),
        corrected_soc=corrected_soc,
        battery_capacity_kwh=11.776,
        soc_unknown=None,
    )


class BuilderInheritance(unittest.TestCase):
    """The builder already applies T01 hardening correctly."""

    def test_valid_reported_passes_through(self):
        raw = {"batterySoc": 25.0, "batteryVoltage": 51.0}
        pi = _inputs(raw, corrected_soc=None)
        self.assertFalse(pi.soc_unknown)
        self.assertAlmostEqual(pi.soc, 25.0)

    def test_missing_payload_yields_unknown(self):
        raw = {"batteryVoltage": 51.0}
        pi = _inputs(raw)
        self.assertTrue(pi.soc_unknown)
        self.assertIsNone(pi.soc)

    def test_nan_payload_yields_unknown(self):
        raw = {"batterySoc": float("nan"), "batteryVoltage": 51.0}
        pi = _inputs(raw)
        self.assertTrue(pi.soc_unknown)
        self.assertIsNone(pi.soc)

    def test_inf_payload_yields_unknown(self):
        raw = {"batterySoc": float("inf"), "batteryVoltage": 51.0}
        pi = _inputs(raw)
        self.assertTrue(pi.soc_unknown)
        self.assertIsNone(pi.soc)

    def test_out_of_range_payload_yields_unknown(self):
        raw = {"batterySoc": 150.0, "batteryVoltage": 51.0}
        pi = _inputs(raw)
        self.assertTrue(pi.soc_unknown)
        self.assertIsNone(pi.soc)

    def test_corrected_soc_25_does_not_get_replaced_by_100(self):
        raw = {"batterySoc": 25.0, "batteryVoltage": 51.0}
        pi = _inputs(raw, corrected_soc=25.0)
        self.assertAlmostEqual(pi.soc, 25.0)
        self.assertFalse(pi.soc_unknown)


class ContractCompositionThroughBuilder(unittest.TestCase):

    def test_low_reported_soc_does_not_get_inflated_by_builder(self):
        raw = {"batterySoc": 25.0, "batteryVoltage": 51.0}
        pi = _inputs(raw)
        rd = _contract.evaluate(
            reported_soc=pi.soc,
            voltage_v=raw["batteryVoltage"],
            battery_current_a=0.5,
            voltage_to_soc=_sc.voltage_to_soc,
        )
        self.assertEqual(rd.validated, 25.0)
        self.assertEqual(rd.control, 25.0)
        self.assertNotEqual(rd.control, 75.0)

    def test_voltage_soc_stays_diagnostic_in_the_builder_too(self):
        raw = {"batteryVoltage": 51.0}  # no batterySoc
        pi = _inputs(raw)
        self.assertTrue(pi.soc_unknown)
        self.assertIsNone(pi.soc)
        rd = _contract.evaluate(
            reported_soc=pi.soc,
            voltage_v=raw["batteryVoltage"],
            battery_current_a=0.5,
            voltage_to_soc=_sc.voltage_to_soc,
        )
        self.assertEqual(rd.quality, "no_reported")
        self.assertIsNotNone(rd.voltage_soc)
        self.assertIsNone(rd.control)

    def test_display_field_of_contract_is_never_pi_soc(self):
        raw = {"batterySoc": 25.0, "batteryVoltage": 51.0}
        pi = _inputs(raw)
        sm = _contract.DisplaySmoother(alpha=0.3)
        sm.update(80.0)  # prior display state
        rd = _contract.evaluate(
            reported_soc=pi.soc,
            voltage_v=raw["batteryVoltage"],
            battery_current_a=0.5,
            voltage_to_soc=_sc.voltage_to_soc,
        )
        wd = _contract.with_display(rd, sm)
        self.assertAlmostEqual(wd.control, 25.0)
        self.assertNotEqual(wd.display, 25.0)


class BuilderHandlesContractFeeding(unittest.TestCase):

    def test_no_payload_marks_unknown(self):
        raw = {}
        pi = _inputs(raw)
        self.assertTrue(pi.soc_unknown)
        self.assertIsNone(pi.soc)

    def test_corrected_None_raw_out_of_range(self):
        raw = {"batterySoc": 200.0, "batteryVoltage": 51.0}
        pi = _inputs(raw, corrected_soc=None)
        self.assertTrue(pi.soc_unknown)
        self.assertIsNone(pi.soc)


if __name__ == "__main__":
    unittest.main()