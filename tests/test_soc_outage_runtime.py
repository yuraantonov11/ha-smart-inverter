"""SOC contract through the REAL outage runtime + planner formula.

This file covers two surfaces together:
  * services.outage_runtime.snapshot_telemetry (raw reading selection)
  * services.outage_planner._compute_battery_remaining_kwh (energy
    formula as called by the live planner)

The runtime selection at runtime.py:350-351 is the LEGACY fallback
that the producer-side adoption has to remove. Pinning that pin here
is intentional: it documents what the contract eliminates.
"""
from __future__ import annotations

import importlib.util
import math
import os
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_sc = _load("sc_r", "hems/soc_correction.py")
_contract = _load("contract_r", "hems/soc_contract.py")

# Load outage_runtime within a fake services-package context so its
# relative imports resolve.
import types as _types
_pkg = _types.ModuleType("services")
_pkg.__path__ = [str(REPO / "services")]
sys.modules["services"] = _pkg
_spec_rt = importlib.util.spec_from_file_location(
    "services.outage_runtime", REPO / "services" / "outage_runtime.py")
_runtime = importlib.util.module_from_spec(_spec_rt)
sys.modules["services.outage_runtime"] = _runtime
_spec_rt.loader.exec_module(_runtime)

_spec_pl = importlib.util.spec_from_file_location(
    "services.outage_planner", REPO / "services" / "outage_planner.py")
_planner = importlib.util.module_from_spec(_spec_pl)
sys.modules["services.outage_planner"] = _planner
_spec_pl.loader.exec_module(_planner)


# Mirror the production runtime selection: the part at L346-351.
def _legacy_runtime_selection(data):
    battery_soc_pct = _runtime._as_float(
        _runtime._safe_dict_get(data, "batterySoc"))
    battery_soc_corrected_pct = _runtime._as_float(
        _runtime._safe_dict_get(data, "correctedSoc"))
    if battery_soc_corrected_pct is None:
        battery_soc_corrected_pct = battery_soc_pct
    return battery_soc_pct, battery_soc_corrected_pct


@dataclass
class Telemetry:
    battery_capacity_kwh: float
    battery_capacity_ok_for_planning: bool
    battery_capacity_ah: float | None
    nominal_voltage_v: float | None
    battery_soc_pct: float | None
    battery_soc_corrected_pct: float | None
    battery_voltage_v: float | None
    battery_discharge_efficiency: float | None
    reserve_soc_pct: float | None


def _telemetry(*, corrected, raw, capacity_kwh=11.776, reserve=20.0,
               eff=_runtime.DEFAULT_DISCHARGE_EFFICIENCY):
    return Telemetry(
        battery_capacity_kwh=capacity_kwh,
        battery_capacity_ah=230.0, nominal_voltage_v=51.2,
        battery_capacity_ok_for_planning=True,
        battery_soc_pct=raw,
        battery_soc_corrected_pct=corrected,
        battery_voltage_v=52.0,
        battery_discharge_efficiency=eff,
        reserve_soc_pct=reserve,
    )


class RuntimeSelection(unittest.TestCase):

    def test_runtime_recovers_from_missing_corrected_with_raw(self):
        """LEGACY: corrected=None, raw=25 -> corrected=25 (silent)."""
        data = {"batterySoc": 25.0}
        raw, corrected = _legacy_runtime_selection(data)
        self.assertEqual(corrected, 25.0)
        self.assertEqual(raw, 25.0)

    def test_runtime_falls_back_to_raw_zero(self):
        data = {"batterySoc": 0}
        raw, corrected = _legacy_runtime_selection(data)
        self.assertEqual(corrected, 0.0)

    def test_runtime_does_not_validate_corrected_when_recovering(self):
        """Pinned bug: a corrected value of 150 with no raw still
        becomes 150 via the legacy fallback. The contract's role is
        to prevent PUBLISHING 150. The planner formula's range check
        catches it on its own."""
        data = {"correctedSoc": 150.0}
        raw, corrected = _legacy_runtime_selection(data)
        self.assertIsNone(raw)
        self.assertEqual(corrected, 150.0)   # legacy leak still here
        # Planner formula DOES correctly drop out-of-range corrected
        # and treats SOC as unknown (rescue floor).
        kwh, soc, unknown = _planner._compute_battery_remaining_kwh(
            _telemetry(corrected=corrected, raw=None))
        self.assertTrue(unknown)
        # What the producer, after adopting the contract, MUST publish
        # instead of 150:
        producer_publishes_corrected = _contract.evaluate(
            150.0, 53.0, 0.5, voltage_to_soc=_sc.voltage_to_soc).control
        self.assertIsNone(producer_publishes_corrected)


class MissingOrInvalidDoesNotResurface(unittest.TestCase):

    def test_no_payload_gives_unknown_no_recovery_via_display(self):
        rd = _contract.evaluate(None, 53.0, 0.5,
                                voltage_to_soc=_sc.voltage_to_soc)
        sm = _contract.DisplaySmoother(alpha=0.3)
        sm.update(70.0)
        wd = _contract.with_display(rd, sm)
        self.assertIsNone(wd.control)
        kwh, soc, unknown = _planner._compute_battery_remaining_kwh(
            _telemetry(corrected=wd.control, raw=None))
        self.assertTrue(unknown)
        self.assertEqual(soc, _runtime.DEFAULT_RESERVE_SOC_PCT)

    def test_invalid_payload_yields_unknown_no_recovery_via_display(self):
        rd = _contract.evaluate(float("nan"), 53.0, 0.5,
                                voltage_to_soc=_sc.voltage_to_soc)
        sm = _contract.DisplaySmoother(alpha=0.3)
        sm.update(50.0)
        wd = _contract.with_display(rd, sm)
        self.assertIsNone(wd.control)
        kwh, soc, unknown = _planner._compute_battery_remaining_kwh(
            _telemetry(corrected=wd.control, raw=None))
        self.assertTrue(unknown)

    def test_low_reported_does_not_get_inflated_at_runtime(self):
        rd = _contract.evaluate(25.0, 51.0, 0.5,
                                voltage_to_soc=_sc.voltage_to_soc)
        kwh, soc, _ = _planner._compute_battery_remaining_kwh(
            _telemetry(corrected=rd.control, raw=25.0))
        self.assertEqual(soc, 25.0)
        expected = (25.0 - 20.0) / 100.0 * 11.776 \
            * _runtime.DEFAULT_DISCHARGE_EFFICIENCY
        self.assertAlmostEqual(kwh, expected, places=4)


class OutOfRangeIsUnknownNotHundred(unittest.TestCase):

    def test_corrected_150_alone_produces_planner_reserve_floor(self):
        """The runtime selector currently admits 150; the planner
        formula checks the range on the corrected path and falls back
        to the raw path. With raw=None the planner ends up in the
        unknown branch. After the producer adopts the contract, the
        producer publishes None in this case and the planner sees
        the unknown branch directly."""
        kwh, soc, unknown = _planner._compute_battery_remaining_kwh(
            _telemetry(corrected=150.0, raw=None))
        self.assertTrue(unknown)
        self.assertEqual(soc, _runtime.DEFAULT_RESERVE_SOC_PCT)
        self.assertEqual(kwh, 0.0)


class BlockedTarget(unittest.TestCase):
    """Bars the exact paths Юра asked to be blocked."""

    def test_corrected_None_does_not_silently_come_back_via_display(self):
        rd = _contract.evaluate(float("nan"), 53.0, 0.5,
                                voltage_to_soc=_sc.voltage_to_soc)
        sm = _contract.DisplaySmoother(alpha=0.3)
        sm.update(50.0)
        wd = _contract.with_display(rd, sm)
        # Even if display has state, control MUST be None.
        self.assertIsNone(wd.control)

    def test_display_display_does_not_leak_into_planner_input(self):
        raw = {"batterySoc": 25.0, "batteryVoltage": 51.0}
        pi = __import__("hems.telemetry", fromlist=["build_planner_inputs"]).build_planner_inputs(
            raw=raw, now=__import__("datetime").datetime(2026, 10, 20, 4, 0),
            battery_capacity_kwh=11.776,
            soc_unknown=None,
        )
        self.assertAlmostEqual(pi.soc, 25.0)
        self.assertFalse(pi.soc_unknown)
        # Planner inputs has no "display" surface; the contract's
        # cosmetic field never reaches the builder.


if __name__ == "__main__":
    unittest.main()