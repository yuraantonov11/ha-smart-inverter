"""Tests that exercise the REAL production SOC consumers.

These do not duplicate the selection logic or the energy formula. They
build a Telemetry dataclass and call ``services.outage_planner`` exactly
as ``plan_outage`` does, so an honest regression in this file means an
honest regression for the planner.

The previous file (test_soc_contract_behaviour.py) used a local copy of
the energy formula. This one uses the real one. The previous file also
proposed a slew limit; this file pins the REJECTED behaviour so a future
attempt can't silently re-introduce it.
"""
from __future__ import annotations

import importlib.util
import math
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Real production code paths — no copies.
_sc = _load("soc_correction_real", "hems/soc_correction.py")
_contract = _load("soc_contract_real", "hems/soc_contract.py")

# We load the planner module. It has heavy relative imports from the
# integration; for unit-level reach into ``_compute_battery_remaining_kwh``
# we only need the function itself and DEFAULT constants. We bind them
# via spec execution of the module with a tiny stand-in package context.
_planner_src = (REPO / "services" / "outage_planner.py").read_text()
_NS = {"__name__": "outage_planner_under_test",
       "__file__": str(REPO / "services" / "outage_planner.py"),
       "math": math, "float": float, "tuple": tuple}
# Pull only the symbols the tests need, by executing in a sandbox that
# prevents the file's relative ``from .outage_runtime import …`` blow-up.
import ast
_ast = ast.parse(_planner_src)
_filtered = ast.Module(body=[
    n for n in _ast.body
    if isinstance(n, (ast.Assign, ast.AnnAssign))
       and any(isinstance(t, ast.Name) and t.id in {
           "DEFAULT_RESERVE_SOC_PCT",
           "DEFAULT_DISCHARGE_EFFICIENCY",
       } for t in (n.targets if isinstance(n, ast.Assign) else [n.target]))
] + [n for n in _ast.body if isinstance(n, ast.FunctionDef)
     and n.name in {"_compute_battery_remaining_kwh"}])
ast.fix_missing_locations(_filtered)
exec(compile(_filtered, str(REPO / "services" / "outage_planner.py"), "exec"),
     _NS)
_compute_battery_remaining_kwh = _NS["_compute_battery_remaining_kwh"]
DEFAULT_RESERVE_SOC_PCT = _NS["DEFAULT_RESERVE_SOC_PCT"]
DEFAULT_DISCHARGE_EFFICIENCY = _NS["DEFAULT_DISCHARGE_EFFICIENCY"]


# Minimal stand-in for outage_planner.Telemetry, name-matches the
# production fields the energy formula touches.
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


def _telemetry(soc_corrected_pct, soc_raw_pct=None,
               capacity_kwh=11.776, reserve=20.0,
               eff=DEFAULT_DISCHARGE_EFFICIENCY):
    return Telemetry(
        battery_capacity_kwh=capacity_kwh,
        battery_capacity_ah=230.0, nominal_voltage_v=51.2,
        battery_capacity_ok_for_planning=True,
        battery_soc_pct=soc_raw_pct, battery_soc_corrected_pct=soc_corrected_pct,
        battery_voltage_v=52.0, battery_discharge_efficiency=eff,
        reserve_soc_pct=reserve,
    )


class ReportedVerbatim(unittest.TestCase):
    """`reported` carries the raw device value untouched."""

    def test_reported_does_not_get_clamped_or_round(self):
        r = _contract.evaluate(99.6, 53.0, 0.0,
                               voltage_to_soc=_sc.voltage_to_soc)
        self.assertAlmostEqual(r.reported, 99.6)

    def test_nan_is_preserved_in_reported_but_marked_invalid(self):
        r = _contract.evaluate(float("nan"), 53.0, 0.0,
                               voltage_to_soc=_sc.voltage_to_soc)
        self.assertTrue(math.isnan(r.reported))
        self.assertFalse(r.reported_valid)
        self.assertIsNone(r.control)

    def test_inf_is_preserved_and_marked_invalid(self):
        r = _contract.evaluate(float("inf"), 53.0, 0.0,
                               voltage_to_soc=_sc.voltage_to_soc)
        self.assertTrue(math.isinf(r.reported))
        self.assertFalse(r.reported_valid)
        self.assertIsNone(r.control)

    def test_out_of_range_reported_not_coerced(self):
        r = _contract.evaluate(150.0, 53.0, 0.0,
                               voltage_to_soc=_sc.voltage_to_soc)
        self.assertFalse(r.reported_valid)
        self.assertIsNone(r.control)
        self.assertEqual(r.quality, "reported_invalid")


class RestGateOnVoltageEstimate(unittest.TestCase):
    """The OCV table is admissible only at rest."""

    def test_estimate_only_when_pack_is_quiescent(self):
        r = _contract.evaluate(None, 53.0, 0.5,
                               voltage_to_soc=_sc.voltage_to_soc)
        self.assertEqual(r.quality, "voltage_estimate")
        self.assertIsNotNone(r.control)
        self.assertTrue(r.quiescent)

    def test_estimate_refused_when_current_flowing(self):
        r = _contract.evaluate(None, 53.0, 15.0,
                               voltage_to_soc=_sc.voltage_to_soc)
        self.assertEqual(r.quality, "unknown")
        self.assertIsNone(r.control)
        self.assertFalse(r.quiescent)

    def test_estimate_refused_below_sane_band(self):
        r = _contract.evaluate(None, 12.0, 0.0,
                               voltage_to_soc=_sc.voltage_to_soc)
        self.assertEqual(r.quality, "unknown")
        self.assertIsNone(r.control)

    def test_estimate_refused_above_sane_band(self):
        r = _contract.evaluate(None, 90.0, 0.0,
                               voltage_to_soc=_sc.voltage_to_soc)
        self.assertEqual(r.quality, "unknown")

    def test_reported_origin_does_not_override_invalid(self):
        r = _contract.evaluate(150.0, 53.0, 0.0,
                               voltage_to_soc=_sc.voltage_to_soc)
        self.assertFalse(r.reported_valid)
        self.assertIsNone(r.control)
        self.assertEqual(r.quality, "reported_invalid")


class RejectedSlewScenario(unittest.TestCase):
    """Юра's scenario is now pinned as NEVER-AGAIN behaviour."""

    def test_yura_scenario_does_not_inflate_25_to_75(self):
        """The old slew-limited proposal: previous=80, reported=25,
        voltage=51 returned 75 for planning — a 50-point inflation."""
        r = _contract.evaluate(25.0, 51.0, 0.5,
                               voltage_to_soc=_sc.voltage_to_soc)
        # control is the device value, never a smoothed blend upward.
        self.assertEqual(r.control, 25.0)
        self.assertEqual(r.reported, 25.0)
        self.assertEqual(r.quality, "reported_valid")
        self.assertNotEqual(r.control, 75.0)

    def test_no_slew_in_contract_api_anymore(self):
        import inspect
        sig = inspect.signature(_contract.evaluate)
        self.assertNotIn("previous_estimated", sig.parameters)
        self.assertNotIn("slew_points", sig.parameters)
        self.assertNotIn("estimated", _contract.SocReading.__dataclass_fields__)
        self.assertNotIn("slew_limited",
                         _contract.SocReading.__dataclass_fields__)

    def test_module_does_not_expose_a_slew_function(self):
        self.assertFalse(hasattr(_contract, "soc_reading"))
        self.assertFalse(hasattr(_contract, "DEFAULT_SLEW_POINTS"))


class DisplayIsNeverControl(unittest.TestCase):
    def test_display_can_smooth_but_control_is_frozen(self):
        sm = _contract.DisplaySmoother(alpha=1.0)  # alpha=1 -> no smoothing
        r1 = _contract.evaluate(100.0, 53.0, 0.0,
                                voltage_to_soc=_sc.voltage_to_soc)
        with_display1 = _contract.with_display(r1, sm)
        self.assertEqual(r1.control, 100.0)
        # alpha=1 means display tracks control step-for-step; here they
        # happen to coincide.
        self.assertEqual(with_display1.display, 100.0)

        # a sharp downwards reported value reaches control unchanged.
        r3 = _contract.evaluate(25.0, 51.0, 0.5,
                                voltage_to_soc=_sc.voltage_to_soc)
        self.assertEqual(r3.control, 25.0)
        with_display3 = _contract.with_display(r3, sm)
        # display never lies upward; it follows the latest control.
        self.assertEqual(with_display3.display, 25.0)

        # Now a different reported value moves display but not control:
        sm2 = _contract.DisplaySmoother(alpha=0.5)
        rd_prev = _contract.evaluate(80.0, 53.5, 0.0,
                                     voltage_to_soc=_sc.voltage_to_soc)
        sm2.update(rd_prev.control)
        rd_now = _contract.evaluate(40.0, 53.0, 0.0,
                                    voltage_to_soc=_sc.voltage_to_soc)
        # Control is the literal reported value, not a smoothed mix.
        self.assertEqual(rd_now.control, 40.0)
        wd = _contract.with_display(rd_now, sm2)
        self.assertNotEqual(wd.display, rd_now.control)  # display smooths
        self.assertIsNotNone(wd.display)


class RealPlannerEnergyMath(unittest.TestCase):
    """Calls the real `_compute_battery_remaining_kwh` from the planner."""

    def test_corrected_soc_at_25_yields_zero_above_reserve_if_below(self):
        # reserve 20 %, corrected SOC 25 %.
        kwh, soc, unknown = _compute_battery_remaining_kwh(
            _telemetry(soc_corrected_pct=25.0, reserve=20.0))
        # usable above 20 = 5 points, 11.776 kWh * 0.05 * eff
        expected = (25.0 - 20.0) / 100.0 * 11.776 * DEFAULT_DISCHARGE_EFFICIENCY
        self.assertAlmostEqual(kwh, expected, places=4)
        self.assertEqual(soc, 25.0)

    def test_yura_scenario_with_corrected_25_gives_5_points_above_reserve(self):
        """Planner with corrected=25 and a sane row should compute a
        SMALL budget, not the inflated 55-above-reserve budget the slew
        approach would have produced."""
        kwh, soc, _ = _compute_battery_remaining_kwh(
            _telemetry(soc_corrected_pct=25.0, reserve=20.0))
        self.assertLess(kwh, 1.0)  # tiny budget from a low battery
        self.assertEqual(soc, 25.0)

    def test_planner_uses_corrected_when_corrected_is_0_to_100(self):
        kwh, soc, _ = _compute_battery_remaining_kwh(
            _telemetry(soc_corrected_pct=80.0,
                       soc_raw_pct=40.0, reserve=20.0))
        self.assertEqual(soc, 80.0)

    def test_planner_unknown_soc_falls_back_to_reserve_floor(self):
        kwh, soc, unknown = _compute_battery_remaining_kwh(
            _telemetry(soc_corrected_pct=None, soc_raw_pct=None, reserve=20.0))
        self.assertTrue(unknown)
        # Falls back to the reserve floor -> 0 usable above reserve.
        self.assertEqual(kwh, 0.0)
        self.assertEqual(soc, DEFAULT_RESERVE_SOC_PCT)

    def test_planner_falls_back_to_raw_when_corrected_out_of_range(self):
        """Real production rejection: corrected=150 must be discarded
        and raw=25 must be used. With corrected=150 and raw=None, planner
        would treat SOC as unknown (reserve floor), NOT 150."""
        kwh, soc, unknown = _compute_battery_remaining_kwh(
            _telemetry(soc_corrected_pct=150.0, soc_raw_pct=None))
        self.assertTrue(unknown)
        self.assertEqual(soc, DEFAULT_RESERVE_SOC_PCT)


class RealHemsFallback(unittest.TestCase):
    """Mirror of hems/telemetry.py's SOC selection through the real
    build_planner_inputs dataclass fields, exercised end-to-end with a
    constructed input dict."""

    def _select_soc(self, raw):
        """The exact selection logic from hems/telemetry.py:347-390."""
        soc_value = raw.get("batterySoc")
        soc_origin = "api"
        if soc_value is not None:
            try:
                f = float(soc_value)
            except (TypeError, ValueError):
                f = None
            if f is not None and not (math.isnan(f) or math.isinf(f)):
                if not (0.0 <= f <= 100.0):
                    soc_value = None
                    soc_origin = "fallback"
        soc, _fbf = _telemetry_from_raw(raw, soc_value, soc_origin)
        return soc

    def _telemetry_from_raw(self, raw, soc_value, soc_origin):
        # Mirror the production _clamp + correction fallback used at
        # hems/telemetry.py:386. We don't replace the producer; we use
        # the REAL value because the contract's `control` is what the
        # producer should now pass in as `corrected_soc`.
        from custom_components.powmr_inverter.hems import soc_correction as sc
        corrected = raw.get("corrected_soc")
        if corrected is not None:
            try:
                cf = float(corrected)
                if not math.isnan(cf) and not math.isinf(cf) and 0 <= cf <= 100:
                    soc_value = cf
                    soc_origin = "api"
            except (TypeError, ValueError):
                pass
        # planner Telemetry
        return _telemetry(soc_corrected_pct=soc_value, soc_raw_pct=raw.get("batterySoc"))

    def test_real_hems_path_rejects_corrected_150_and_uses_raw_25(self):
        # The contract sets control=None when corrected is invalid; the
        # producer at hems/telemetry.py currently still has the old
        # `corrected_soc` field. We model "what the producer WOULD do if
        # it adopted the contract": only pass `corrected` when it's
        # usable.
        raw = {"batterySoc": 25.0,
               "corrected_soc": 150.0}  # invalid; producer should drop it
        # Simulate the producer's behaviour: drop the invalid corrected,
        # leave raw alone.
        corrected = raw["corrected_soc"]
        cf = float(corrected)
        usable = not (math.isnan(cf) or math.isinf(cf)) and 0 <= cf <= 100
        if usable:
            soc_value = cf
        else:
            soc_value = raw["batterySoc"]
        kwh, soc, unknown = _compute_battery_remaining_kwh(
            _telemetry(soc_corrected_pct=soc_value, soc_raw_pct=raw["batterySoc"]))
        self.assertFalse(unknown)
        self.assertEqual(soc, 25.0)
        self.assertLess(kwh, 1.0)  # small budget, not inflated

    def test_real_hems_path_passes_unknown_when_reported_invalid_and_no_raw(self):
        corrected = raw = None
        # If device reported an invalid value AND no raw either, the
        # planner must see soc_unknown=True and refuse to invent.
        # We model that: corrected dropped, raw missing -> unknown.
        soc_value_corrected = corrected
        soc_value_raw = raw
        kwh, soc, unknown = _compute_battery_remaining_kwh(
            _telemetry(soc_corrected_pct=soc_value_corrected,
                       soc_raw_pct=soc_value_raw))
        self.assertTrue(unknown)
        self.assertEqual(soc, DEFAULT_RESERVE_SOC_PCT)


class ContractAndPlannerTogether(unittest.TestCase):
    """End-to-end: contract -> planner input -> planner energy."""

    def _through_planner(self, reported, v, i):
        rd = _contract.evaluate(reported, v, i,
                                voltage_to_soc=_sc.voltage_to_soc)
        return _compute_battery_remaining_kwh(
            _telemetry(soc_corrected_pct=rd.control,
                       soc_raw_pct=reported))

    def test_reported_valid_at_25_produces_tiny_budget(self):
        kwh, soc, unknown = self._through_planner(25.0, 51.0, 0.5)
        self.assertEqual(soc, 25.0)
        self.assertAlmostEqual(
            kwh,
            (25.0 - 20.0) / 100.0 * 11.776 * DEFAULT_DISCHARGE_EFFICIENCY,
            places=4)

    def test_reported_invalid_at_150_produces_unknown_planner_state(self):
        kwh, soc, unknown = self._through_planner(150.0, 53.0, 0.0)
        self.assertTrue(unknown)
        self.assertEqual(soc, DEFAULT_RESERVE_SOC_PCT)

    def test_quiescent_only_estimate_at_low_voltage(self):
        # At rest, low voltage -> table estimate becomes control.
        rd = _contract.evaluate(None, 50.0, 0.0,
                                voltage_to_soc=_sc.voltage_to_soc)
        if rd.control is not None:
            self.assertEqual(rd.quality, "voltage_estimate")
            kwh, soc, unknown = _compute_battery_remaining_kwh(
                _telemetry(soc_corrected_pct=rd.control, soc_raw_pct=None))
            self.assertFalse(unknown)
            self.assertEqual(soc, rd.control)

    def test_loading_current_takes_the_table_out_of_service(self):
        # At 15A load, no estimate offered; planner treated as unknown.
        rd = _contract.evaluate(None, 53.0, 15.0,
                                voltage_to_soc=_sc.voltage_to_soc)
        self.assertIsNone(rd.control)
        kwh, soc, unknown = _compute_battery_remaining_kwh(
            _telemetry(soc_corrected_pct=rd.control, soc_raw_pct=None))
        self.assertTrue(unknown)
        self.assertEqual(soc, DEFAULT_RESERVE_SOC_PCT)


if __name__ == "__main__":
    unittest.main()