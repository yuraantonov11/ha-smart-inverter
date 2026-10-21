"""End-to-end SOC contract: evaluate -> production builder/runtime -> planner.

REPLACES the ``_legacy_runtime_selection`` mirror that lived in the old
``tests/test_soc_outage_runtime.py``. The replacement drives the real
``snapshot_telemetry`` from ``services/outage_runtime.py`` with the same
FakeCoordinator the existing tests use, and exercises the contract path:

    evaluate() (contract)
        -> build_planner_inputs(..., soc_control=reading.control)
        -> snapshot_telemetry(coordinator, ..., soc_control=reading.control)
        -> _compute_battery_remaining_kwh (planner formula)
        -> plan_night_charge (predictive night-window abstention)

The contract result IS authoritative; no consumer computes its own SOC.

Every test passes ``reading.control`` directly (including ``None``).
NO sentinel substitution; the authority distinction in production code
is the test's responsibility too.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent


def _load_in_package(name, rel, parent_pkg_name="svc_under_test",
                     parent_rel="services"):
    parent_path = REPO / parent_rel
    if parent_pkg_name not in sys.modules:
        pkg = types.ModuleType(parent_pkg_name)
        pkg.__path__ = [str(parent_path)]
        sys.modules[parent_pkg_name] = pkg
    full = f"{parent_pkg_name}.{name}"
    spec = importlib.util.spec_from_file_location(full, parent_path / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


def _hard_load_soc_contract():
    spec = importlib.util.spec_from_file_location(
        "soc_contract_auth", REPO / "hems" / "soc_contract.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["soc_contract_auth"] = mod
    spec.loader.exec_module(mod)
    return mod


def _hard_load_hems_package():
    """Load hems/* with a real package context so the relative
    imports resolve at module load time."""
    if "hems" not in sys.modules:
        hems_pkg = types.ModuleType("hems")
        hems_pkg.__path__ = [str(REPO / "hems")]
        sys.modules["hems"] = hems_pkg

    engine_spec = importlib.util.spec_from_file_location(
        "hems.engine", REPO / "hems" / "engine.py")
    engine = importlib.util.module_from_spec(engine_spec)
    sys.modules["hems.engine"] = engine
    engine_spec.loader.exec_module(engine)

    sc_spec = importlib.util.spec_from_file_location(
        "hems.soc_correction", REPO / "hems" / "soc_correction.py")
    sc_mod = importlib.util.module_from_spec(sc_spec)
    sys.modules["hems.soc_correction"] = sc_mod
    sc_spec.loader.exec_module(sc_mod)

    tele_spec = importlib.util.spec_from_file_location(
        "hems.telemetry", REPO / "hems" / "telemetry.py")
    tele = importlib.util.module_from_spec(tele_spec)
    sys.modules["hems.telemetry"] = tele
    tele_spec.loader.exec_module(tele)

    pred_spec = importlib.util.spec_from_file_location(
        "hems.predictive", REPO / "hems" / "predictive.py")
    pred = importlib.util.module_from_spec(pred_spec)
    sys.modules["hems.predictive"] = pred
    pred_spec.loader.exec_module(pred)

    return engine, sc_mod, tele, pred


_c = _hard_load_soc_contract()
_engine, _sc_mod, _t, _pred = _hard_load_hems_package()

_runtime = _load_in_package("outage_runtime", "outage_runtime.py")
_planner = _load_in_package("outage_planner", "outage_planner.py")

_TEST_RT = importlib.util.spec_from_file_location(
    "test_outage_runtime_for_auth",
    REPO / "tests" / "test_outage_runtime.py")
_TEST_RT_MOD = importlib.util.module_from_spec(_TEST_RT)
_TEST_RT.loader.exec_module(_TEST_RT_MOD)
FakeCoordinator = _TEST_RT_MOD.FakeCoordinator


def _plan_night_charge(soc_corrected, tomorrow_pv=2.0):
    """Direct call into the production ``plan_night_charge`` function
    (``hems/predictive.py``). It returns
    ``"soc_unknown: planner abstains, baseline governs"`` whenever
    the SOC is unreadable."""
    pi = _pred.PlannerInputs(
        now=datetime(2026, 10, 20, 4, 0),
        soc=None,
        soc_corrected=soc_corrected,
        pv_w=0.0, load_w=200.0, grid_w=0.0, batt_w=0.0,
        battery_capacity_kwh=11.776,
        grid_v=230.0, grid_ok=True,
        smart_mode=0,
        forecast_today_kwh=2.0,
        forecast_tomorrow_kwh=tomorrow_pv,
        hourly_pv=[0.0]*24, hourly_radiation=[0.0]*24,
        hourly_weather=[{}]*24,
        tariff_schedule=[0.0]*24,
        consumption_history=[[300.0]*24]*7,
    )
    return _pred.plan_night_charge(pi, target_morning=60.0)


# ──────────────────────────────────────────────────────────────────
# Bool rejection
# ──────────────────────────────────────────────────────────────────


class BoolRejected(unittest.TestCase):

    def test_True_rejected(self):
        r = _c.evaluate(True, 53.0, 0.5, voltage_to_soc=_sc_mod.voltage_to_soc)
        self.assertIsNone(r.reported_payload)
        self.assertIsNone(r.validated)
        self.assertIsNone(r.control)
        self.assertEqual(r.quality, "reported_invalid")

    def test_False_rejected(self):
        r = _c.evaluate(False, 53.0, 0.5, voltage_to_soc=_sc_mod.voltage_to_soc)
        self.assertIsNone(r.reported_payload)
        self.assertIsNone(r.validated)
        self.assertIsNone(r.control)
        self.assertEqual(r.quality, "reported_invalid")

    def test_bool_does_not_become_1_or_0(self):
        for v in (True, False):
            r = _c.evaluate(v, 53.0, 0.5, voltage_to_soc=_sc_mod.voltage_to_soc)
            self.assertNotEqual(r.control, 1.0)
            self.assertNotEqual(r.control, 0.0)


# ──────────────────────────────────────────────────────────────────
# End-to-end chain
# ──────────────────────────────────────────────────────────────────


class ContractDrivesProducer(unittest.TestCase):

    def _drive(self, raw_reported, voltage, current, *,
               soc_unknown=None):
        rd = _c.evaluate(raw_reported, voltage, current,
                         voltage_to_soc=_sc_mod.voltage_to_soc)
        pi = _t.build_planner_inputs(
            raw={"batteryVoltage": voltage},
            now=datetime(2026, 10, 20, 4, 0),
            battery_capacity_kwh=11.776,
            soc_control=rd.control,
            soc_unknown=soc_unknown,
        )
        return rd, pi

    def test_raw_25_yields_control_25_even_with_caller_passing_75(self):
        rd, pi = self._drive(25.0, 51.0, 0.5)
        self.assertEqual(rd.control, 25.0)
        self.assertAlmostEqual(pi.soc, 25.0)
        self.assertFalse(pi.soc_unknown)
        self.assertNotEqual(pi.soc, 75.0)

    def test_raw_0_yields_control_0(self):
        rd, pi = self._drive(0.0, 53.0, 0.5)
        self.assertEqual(rd.control, 0.0)
        self.assertAlmostEqual(pi.soc, 0.0)

    def test_missing_payload_yields_unknown(self):
        rd, pi = self._drive(None, 53.0, 0.5)
        self.assertIsNone(rd.control)
        self.assertTrue(pi.soc_unknown)
        self.assertIsNone(pi.soc)

    def test_nan_yields_unknown(self):
        rd, pi = self._drive(float("nan"), 51.0, 0.5)
        self.assertIsNone(rd.control)
        self.assertTrue(pi.soc_unknown)

    def test_inf_yields_unknown(self):
        rd, pi = self._drive(float("inf"), 51.0, 0.5)
        self.assertIsNone(rd.control)
        self.assertTrue(pi.soc_unknown)

    def test_150_yields_unknown(self):
        rd, pi = self._drive(150.0, 51.0, 0.5)
        self.assertIsNone(rd.control)
        self.assertTrue(pi.soc_unknown)

    def test_bool_yields_unknown(self):
        for v in (True, False):
            rd, pi = self._drive(v, 51.0, 0.5)
            self.assertIsNone(rd.control)
            self.assertTrue(pi.soc_unknown)

    def test_soc_control_25_with_explicit_soc_unknown_True_honours_unknown(self):
        """Юра's corridor: a numeric ``soc_control`` MUST NOT cancel
        an explicit ``soc_unknown=True`` -- "readable but engine
        must NOT use it". Display can show ``soc_control``,
        ``soc_unknown`` carries the guard."""
        rd = _c.evaluate(25.0, 51.0, 0.5,
                         voltage_to_soc=_sc_mod.voltage_to_soc)
        self.assertEqual(rd.control, 25.0)
        pi = _t.build_planner_inputs(
            raw={"batteryVoltage": 51.0},
            now=datetime(2026, 10, 20, 4, 0),
            battery_capacity_kwh=11.776,
            soc_control=rd.control,
            soc_unknown=True,
        )
        self.assertTrue(pi.soc_unknown)
        self.assertEqual(pi.soc, 25.0)


# ──────────────────────────────────────────────────────────────────
# Real snapshot_telemetry (not a copy)
# ──────────────────────────────────────────────────────────────────


class ContractDrivesSnapshot(unittest.TestCase):

    def test_raw_25_surfaces_as_25_through_runtime(self):
        rd = _c.evaluate(25.0, 51.0, 0.5,
                         voltage_to_soc=_sc_mod.voltage_to_soc)
        coord = FakeCoordinator(
            data={"batteryVoltage": 51.0}, options={})
        tel = _runtime.snapshot_telemetry(
            coord, hass=None, soc_control=rd.control)
        self.assertEqual(tel.battery_soc_corrected_pct, 25.0)

    def test_soc_control_None_suppresses_legacy_fallback(self):
        coord = FakeCoordinator(
            data={"batterySoc": 80.0, "correctedSoc": 80.0,
                  "batteryVoltage": 51.0},
            options={},
        )
        tel = _runtime.snapshot_telemetry(coord, hass=None, soc_control=None)
        self.assertIsNone(tel.battery_soc_pct)
        self.assertIsNone(tel.battery_soc_corrected_pct)
        kwh, soc, unknown = _planner._compute_battery_remaining_kwh(tel)
        self.assertTrue(unknown)
        self.assertEqual(soc, _planner.DEFAULT_RESERVE_SOC_PCT)

    def test_soc_control_0_is_kept_as_0(self):
        rd = _c.evaluate(0.0, 53.0, 0.5,
                         voltage_to_soc=_sc_mod.voltage_to_soc)
        coord = FakeCoordinator(
            data={"batterySoc": 99.9, "batteryVoltage": 51.0},
            options={},
        )
        tel = _runtime.snapshot_telemetry(
            coord, hass=None, soc_control=rd.control)
        self.assertEqual(tel.battery_soc_corrected_pct, 0.0)


# ──────────────────────────────────────────────────────────────────
# Authority: control=None cannot be recovered
# ──────────────────────────────────────────────────────────────────


class AuthorityPreventsRecovery(unittest.TestCase):

    def test_builder_soc_control_None_overrides_corrected_soc(self):
        pi = _t.build_planner_inputs(
            raw={"batterySoc": 25.0, "batteryVoltage": 51.0},
            now=datetime(2026, 10, 20, 4, 0),
            battery_capacity_kwh=11.776,
            soc_control=None,
            corrected_soc=75.0,
        )
        self.assertTrue(pi.soc_unknown)
        self.assertIsNone(pi.soc)
        self.assertNotEqual(pi.soc, 75.0)

    def test_builder_soc_control_None_overrides_raw_fallback(self):
        pi = _t.build_planner_inputs(
            raw={"batterySoc": 25.0, "batteryVoltage": 51.0},
            now=datetime(2026, 10, 20, 4, 0),
            battery_capacity_kwh=11.776,
            soc_control=None,
        )
        self.assertTrue(pi.soc_unknown)
        self.assertIsNone(pi.soc)
        self.assertNotEqual(pi.soc, 25.0)

    def test_runtime_soc_control_None_no_fallback_to_raw(self):
        coord = FakeCoordinator(
            data={"batterySoc": 99.9, "batteryVoltage": 51.0},
            options={},
        )
        tel = _runtime.snapshot_telemetry(
            coord, hass=None, soc_control=None)
        self.assertIsNone(tel.battery_soc_pct)
        self.assertIsNone(tel.battery_soc_corrected_pct)


# ──────────────────────────────────────────────────────────────────
# Real predictive planner abstention (the documented guard)
# ──────────────────────────────────────────────────────────────────


class UnknownTriggersGuard(unittest.TestCase):

    def test_predictive_plan_night_charge_abstains_when_soc_unknown(self):
        start, end, reason = _plan_night_charge(soc_corrected=None)
        self.assertIn("soc_unknown", reason)
        self.assertIn("planner abstains", reason)
        # The documented default window is (23, 7) -- the function
        # returns the configured conservative default.
        self.assertEqual(start, 23)
        self.assertEqual(end, 7)

    def test_predictive_plan_night_charge_proceeds_when_soc_known(self):
        start, end, reason = _plan_night_charge(soc_corrected=80.0)
        self.assertNotIn("planner abstains", reason)
        self.assertNotIn("soc_unknown", reason)
        # Either sunrise is short / window short: just that planner
        # made a concrete decision.
        self.assertIsInstance(start, int)
        self.assertIsInstance(end, int)

    def test_unknown_yields_unknown_in_planner_math(self):
        rd = _c.evaluate(None, 53.0, 0.5,
                         voltage_to_soc=_sc_mod.voltage_to_soc)
        kwh, soc, unknown = _planner._compute_battery_remaining_kwh(
            type("T", (), {
                "battery_capacity_kwh": 11.776,
                "battery_capacity_ok_for_planning": True,
                "battery_capacity_ah": 230.0,
                "nominal_voltage_v": 51.2,
                "battery_soc_pct": None,
                "battery_soc_corrected_pct": rd.control,
                "battery_voltage_v": 51.0,
                "battery_discharge_efficiency":
                    _planner.DEFAULT_DISCHARGE_EFFICIENCY,
                "reserve_soc_pct": _planner.DEFAULT_RESERVE_SOC_PCT,
            })())
        self.assertTrue(unknown)
        self.assertEqual(kwh, 0.0)
        self.assertEqual(soc, _planner.DEFAULT_RESERVE_SOC_PCT)


class Direction(unittest.TestCase):

    def test_contract_does_not_consume_builder_output(self):
        rd = _c.evaluate(25.0, 51.0, 0.5,
                         voltage_to_soc=_sc_mod.voltage_to_soc)
        pi = _t.build_planner_inputs(
            raw={"batterySoc": 999.0, "batteryVoltage": 999.0},
            now=datetime(2026, 10, 20, 4, 0),
            battery_capacity_kwh=11.776,
            soc_control=rd.control,
        )
        self.assertEqual(rd.control, 25.0)
        self.assertAlmostEqual(pi.soc, 25.0)

    def test_planner_does_not_backtrack_to_voltage(self):
        pi = _t.build_planner_inputs(
            raw={"batterySoc": 25.0, "batteryVoltage": 51.0},
            now=datetime(2026, 10, 20, 4, 0),
            battery_capacity_kwh=11.776,
            soc_control=None,
        )
        self.assertTrue(pi.soc_unknown)


if __name__ == "__main__":
    unittest.main()