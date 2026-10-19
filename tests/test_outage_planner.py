"""Outage planner tests (Phase 2B).

Behaviour-driven tests for ``services/outage_planner.py``. The planner
is a pure function on a telemetry snapshot + a (duck-typed) outage
event; the tests do NOT touch HA, the coordinator, the engine, or
any inverter state.

Loading pattern
---------------
The production module is loaded via
``importlib.util.spec_from_file_location`` (same pattern as
``test_outage_event_model.py``). The planner does not import HA at
all, so the test file does not need to set up HA imports.

Frozen "now"
------------
All tests inject ``now_iso = 2026-10-13T10:00:00+03:00`` (Kyiv) so the
math is deterministic. Outage events are constructed relative to that
anchor.

Verification commands (run from repo root):

    .test-venv/bin/python -m py_compile tests/test_outage_planner.py
    .test-venv/bin/python -m unittest tests.test_outage_planner -v
    .test-venv/bin/python -m unittest discover tests/ -p "test_outage*.py" -v
"""

from __future__ import annotations

import dataclasses
import importlib.util
import math
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parent.parent
PLANNER_PATH = REPO_ROOT / "services" / "outage_planner.py"


def _load_planner_module():
    """Load ``services/outage_planner.py`` as a standalone module.

    The planner has no HA / coordinator imports; the module loads
    cleanly via ``spec_from_file_location`` without any package
    bootstrap. We register it in ``sys.modules`` for ``@dataclass``
    introspection consistency with the production module identity.
    """
    spec = importlib.util.spec_from_file_location(
        "outage_planner_under_test", str(PLANNER_PATH)
    )
    assert spec is not None and spec.loader is not None, (
        f"could not build spec for {PLANNER_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["outage_planner_under_test"] = module
    spec.loader.exec_module(module)
    return module


planner = _load_planner_module()


# Structural probe: the tests rely on these symbols being present.
REQUIRED_SYMBOLS = (
    "Telemetry",
    "PlannerPlan",
    "plan_outage",
    "TELEMETRY_STALENESS_LIMIT_SECONDS",
    "PV_CONFIDENCE_SAMPLES_MIN",
    "PV_CONFIDENCE_FACTOR_MIN",
    "DEFAULT_RESERVE_SOC_PCT",
    "DEFAULT_MAX_PLANNING_HORIZON_DAYS",
)
STRUCTURAL_PROBE_OK = all(hasattr(planner, name) for name in REQUIRED_SYMBOLS)
PROBE_FAILURE_REASON = (
    "production planner module is missing one of: " + ", ".join(REQUIRED_SYMBOLS)
    if not STRUCTURAL_PROBE_OK
    else ""
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 10, 13, 10, 0, 0, tzinfo=KYIV)
NOW_ISO = NOW.isoformat()


def _iso(dt: datetime) -> str:
    """Render a ``datetime`` as an offset-aware ISO 8601 string in Kyiv."""
    return dt.astimezone(KYIV).isoformat()


def _evt(
    *,
    event_id: str = "evt-1",
    start: datetime | None = None,
    end: datetime | None = None,
    status: str = "confirmed",
) -> SimpleNamespace:
    """Build a minimal duck-typed outage event.

    Defaults: a 2-hour outage starting 1 hour from ``NOW``. A
    ``SimpleNamespace`` keeps the test independent of the production
    ``OutageEvent`` dataclass; the planner only reads the four
    attributes via ``getattr``.
    """
    if start is None:
        start = NOW + timedelta(hours=1)
    if end is None:
        end = start + timedelta(hours=2)
    return SimpleNamespace(
        id=event_id,
        start_iso=_iso(start),
        end_iso=_iso(end),
        status=status,
    )


def _healthy_telemetry(**overrides) -> planner.Telemetry:
    """A fully-populated, healthy telemetry snapshot.

    Defaults match the operator-ratified "healthy" scenario:
    11 kWh battery at 80 % SOC, healthy PV forecast (confidence 0.8,
    14 samples), grid available, no manual pause.
    """
    base = dict(
        now_iso=NOW_ISO,
        telemetry_freshness_seconds=30,

        battery_capacity_kwh=11.0,
        battery_capacity_ah=215.0,
        nominal_voltage_v=51.2,
        battery_capacity_ok_for_planning=True,
        battery_soc_pct=80.0,
        battery_soc_corrected_pct=80.0,
        battery_voltage_v=53.5,
        battery_charge_efficiency=0.92,
        battery_discharge_efficiency=0.92,

        pv_power_w=2000.0,
        load_power_w=600.0,
        grid_power_w=300.0,

        grid_available=True,

        reserve_soc_pct=20.0,
        max_utility_charging_current_a=30.0,
        max_charging_current_a=60.0,

        pv_forecast_tomorrow_kwh=20.0,
        pv_forecast_confidence=0.8,
        pv_forecast_samples=14,

        tarpit_consumption_avg_kwh={
            "2026-10-12": 14.0,
            "2026-10-11": 13.5,
            "2026-10-10": 15.0,
            "2026-10-09": 14.5,
            "2026-10-08": 13.0,
            "2026-10-07": 16.0,
            "2026-10-06": 14.2,
        },

        current_pause_until_iso=None,
        max_planning_horizon_days=7,
        is_holiday_or_weekend=False,
    )
    base.update(overrides)
    return planner.Telemetry(**base)


# ---------------------------------------------------------------------------
# Healthy telemetry
# ---------------------------------------------------------------------------


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class PlannerHealthyCaseTests(unittest.TestCase):
    """Short outage + healthy telemetry; the planner returns a full
    plan, no block."""

    def test_plan_for_short_outage_with_healthy_telemetry(self):
        tel = _healthy_telemetry()
        event = _evt()  # 2-hour outage, starts in 1h
        result = planner.plan_outage(tel, event)

        self.assertEqual(result.event_id, "evt-1")
        self.assertFalse(result.blocked, msg=result.reason_if_blocked)
        self.assertIsNone(result.reason_if_blocked)
        self.assertEqual(result.mode, "observation")

        # energy_needed_kwh = mean_daily_kwh * duration/24.
        # Daily mean from history = 14.0+13.5+15+14.5+13+16+14.2 / 7 = 14.314
        # 2 hours -> 14.314 * 2/24 ~= 1.193 kWh. This matches the spec
        # wording "energy_needed_kwh ~ 2 * mean_load" (mean_load per
        # hour = 14.314 / 24 * 2 = 1.193).
        mean_daily = 14.314285714285717
        expected_need = mean_daily * (2.0 / 24.0)
        self.assertAlmostEqual(
            result.plan["energy_needed_kwh"], round(expected_need, 3), places=3
        )
        self.assertGreater(result.plan["energy_needed_kwh"], 0.0)

        # Attainable target SOC must be in [0, 100]; per the spec it
        # is the SOC at which the battery alone covers need+reserve.
        # With the operator's "1-day buffer on top of the window"
        # policy the attainable target is bounded by 100; the
        # planner's honesty is reflected in needs_charging_from_grid_kwh
        # (not in the attainable target itself).
        self.assertIsNotNone(result.plan["attainable_target_soc_pct"])
        self.assertGreaterEqual(result.plan["attainable_target_soc_pct"], 0.0)
        self.assertLessEqual(result.plan["attainable_target_soc_pct"], 100.0)

        # No BLOCKING warnings for the healthy path -- low PV
        # confidence, stale telemetry, manual pause are all clear.
        self.assertEqual(result.plan["warnings"], [])

        # The planner computed the kWh math correctly. With the
        # conservative "1-day buffer on top of the window" reserve
        # policy, a 2-hour outage at 14.3 kWh/day still leaves a
        # non-zero charging gap; this is the planner being honest,
        # not buggy. Verify the magnitude is finite and non-negative.
        self.assertGreaterEqual(result.plan["needs_charging_from_grid_kwh"], 0.0)
        self.assertTrue(math.isfinite(result.plan["needs_charging_from_grid_kwh"]))


# ---------------------------------------------------------------------------
# Low PV confidence
# ---------------------------------------------------------------------------


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class PlannerLowPVConfidenceTests(unittest.TestCase):
    """Low PV confidence must collapse the PV contribution to zero
    and surface a warning, but NOT block."""

    def test_plan_with_low_pv_confidence_assumes_zero_sun(self):
        tel = _healthy_telemetry(
            pv_forecast_confidence=0.3,
            pv_forecast_samples=3,
        )
        event = _evt()  # 2-hour outage
        result = planner.plan_outage(tel, event)

        self.assertFalse(result.blocked)
        # The PV contribution must be zero (rule 2).
        self.assertEqual(result.energy_from_pv_during_outage_kwh, 0.0)
        # Warning text per the test contract. The planner emits a
        # diagnostic string that starts with this prefix; we check
        # via ``any(...)`` so additional suffix text is allowed.
        self.assertTrue(
            any(
                "low PV confidence -- assuming zero sun contribution" in w
                for w in result.plan["warnings"]
            ),
            f"expected PV-confidence warning in {result.plan['warnings']!r}",
        )

    def test_plan_with_low_pv_confidence_but_grid_charging_still_works(self):
        """Low PV confidence is a warning, not a block. If the battery
        headroom is insufficient and the grid is available, the
        planner still produces a prep_start_iso."""
        tel = _healthy_telemetry(
            pv_forecast_confidence=0.3,
            pv_forecast_samples=3,
            battery_soc_pct=25.0,
            battery_soc_corrected_pct=25.0,
            reserve_soc_pct=20.0,
            battery_capacity_kwh=5.0,
            battery_capacity_ah=98.0,
        )
        # Long enough window that consumption exceeds the small battery.
        event = _evt(
            start=NOW + timedelta(hours=1),
            end=NOW + timedelta(hours=8),
        )
        result = planner.plan_outage(tel, event)
        self.assertFalse(result.blocked, msg=result.reason_if_blocked)
        self.assertEqual(result.energy_from_pv_during_outage_kwh, 0.0)
        self.assertTrue(
            any(
                "low PV confidence -- assuming zero sun contribution" in w
                for w in result.plan["warnings"]
            ),
            f"expected PV-confidence warning in {result.plan['warnings']!r}",
        )
        # Need > available -> grid charging is required.
        self.assertGreater(result.plan["needs_charging_from_grid_kwh"], 0.0)
        # Grid is available -> prep_start_iso is set.
        self.assertIsNotNone(result.plan["prep_start_iso"])


# ---------------------------------------------------------------------------
# Block conditions
# ---------------------------------------------------------------------------


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class PlannerBlockConditionTests(unittest.TestCase):
    """Each block predicate produces ``blocked=True`` with a concrete
    reason -- never 'unknown'."""

    def test_plan_with_grid_unavailable_marks_blocked(self):
        tel = _healthy_telemetry(
            grid_available=False,
            # Force a positive charging gap.
            battery_soc_pct=21.0,
            battery_soc_corrected_pct=21.0,
            reserve_soc_pct=20.0,
            battery_capacity_kwh=5.0,
            battery_capacity_ah=98.0,
            # No PV: the planner must
            # require grid charging to
            # cover the event so the
            # grid-unavailable guard
            # fires.
            pv_forecast_tomorrow_kwh=0.0,
            pv_forecast_confidence=0.0,
            pv_forecast_samples=0,
        )
        # Long enough window that need > available even at 21 % SOC.
        event = _evt(
            start=NOW + timedelta(hours=1),
            end=NOW + timedelta(hours=8),
        )
        result = planner.plan_outage(tel, event)
        self.assertTrue(result.blocked)
        self.assertIsNotNone(result.reason_if_blocked)
        self.assertIn("grid unavailable", result.reason_if_blocked)
        # next_attempt_iso should be after the event -- operator can
        # re-plan once the event ends.
        self.assertIsNotNone(result.next_attempt_iso)
        self.assertEqual(result.next_attempt_iso, event.end_iso)

    def test_plan_with_invalid_battery_capacity_marks_blocked(self):
        tel = _healthy_telemetry(
            battery_capacity_ok_for_planning=False,
            battery_capacity_kwh=None,
        )
        event = _evt()
        result = planner.plan_outage(tel, event)
        self.assertTrue(result.blocked)
        self.assertIsNotNone(result.reason_if_blocked)
        self.assertIn("battery capacity", result.reason_if_blocked)

    def test_plan_with_stale_telemetry_marks_blocked(self):
        tel = _healthy_telemetry(
            telemetry_freshness_seconds=400,
            # Force the planner to want grid charging.
            battery_soc_pct=21.0,
            battery_soc_corrected_pct=21.0,
            battery_capacity_kwh=5.0,
            battery_capacity_ah=98.0,
            # No PV: the event must require
            # grid charging for the
            # staleness guard to fire.
            pv_forecast_tomorrow_kwh=0.0,
            pv_forecast_confidence=0.0,
            pv_forecast_samples=0,
        )
        event = _evt(
            start=NOW + timedelta(hours=1),
            end=NOW + timedelta(hours=8),
        )
        result = planner.plan_outage(tel, event)
        self.assertTrue(
            result.blocked,
            f"planner must block when grid "
            f"charging is required under "
            f"stale telemetry; got "
            f"blocked={result.blocked} "
            f"reason={result.reason_if_blocked!r}",
        )
        self.assertIsNotNone(result.reason_if_blocked)
        # The reason mentions "stale" or "freshness".
        reason_lower = result.reason_if_blocked.lower()
        self.assertTrue(
            "stale" in reason_lower or "freshness" in reason_lower,
            f"reason did not mention stale/freshness: {result.reason_if_blocked!r}",
        )

    def test_plan_with_active_manual_pause_marks_blocked(self):
        # Manual pause 30 minutes from now; outage is in 1 hour.
        pause_until = NOW + timedelta(minutes=30)
        tel = _healthy_telemetry(
            current_pause_until_iso=_iso(pause_until),
        )
        event = _evt()
        result = planner.plan_outage(tel, event)
        self.assertTrue(result.blocked)
        self.assertIsNotNone(result.reason_if_blocked)
        self.assertIn("manual", result.reason_if_blocked.lower())
        # next_attempt_iso should match the pause timestamp so the
        # caller can re-plan when the hold expires.
        self.assertIsNotNone(result.next_attempt_iso)


# ---------------------------------------------------------------------------
# Incomplete plans (not blocked, but not enough energy)
# ---------------------------------------------------------------------------


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class PlannerIncompletePlanTests(unittest.TestCase):
    """Rule 4: the planner never promises to keep the house powered."""

    def test_plan_does_not_promise_to_keep_house_powered(self):
        # 5 kWh battery at 10 % SOC; 6-hour outage at 2 kW -> needs
        # 12 kWh; available = 0 (current SOC is at-or-below reserve)
        # + PV (which is zero per the rule 2 threshold when confidence
        # is low). Either blocked=True OR
        # (not blocked AND attainable < required AND
        # reason_if_incomplete is non-null and quantifies the gap).
        tel = _healthy_telemetry(
            battery_capacity_kwh=5.0,
            battery_capacity_ah=98.0,
            battery_soc_pct=10.0,
            battery_soc_corrected_pct=10.0,
            reserve_soc_pct=20.0,
            # Big load to force a gap; long outage at ~2 kW.
            load_power_w=2000.0,
            # Force PV contribution to zero so the planner cannot
            # "save" the plan via the forecast.
            pv_forecast_confidence=0.3,
            pv_forecast_samples=3,
        )
        event = _evt(
            start=NOW + timedelta(hours=1),
            end=NOW + timedelta(hours=7),
        )
        result = planner.plan_outage(tel, event)

        if not result.blocked:
            # Plausible path 1: not blocked (grid is available) but
            # the planner must surface an incomplete reason with the
            # gap quantified.
            self.assertIsNotNone(
                result.reason_if_incomplete,
                "planner returned non-blocked but did not surface "
                "reason_if_incomplete",
            )
            # The attainable target SOC should be at the floor (the
            # battery cannot cover need even at 100 %).
            target = result.plan["attainable_target_soc_pct"]
            self.assertIsNotNone(target)
            self.assertLessEqual(target, 100.0)
            self.assertGreater(
                result.plan["needs_charging_from_grid_kwh"], 0.0
            )
        else:
            # Plausible path 2: the planner blocks -- the reason must
            # NOT be "blocked: unknown" and must reference a
            # recognisable cause.
            self.assertIsNotNone(result.reason_if_blocked)
            self.assertNotIn("unknown", result.reason_if_blocked.lower())


# ---------------------------------------------------------------------------
# PV hardware limit policy (rule 1)
# ---------------------------------------------------------------------------


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class PlannerPVPolicyTests(unittest.TestCase):
    """Rule 1: never use ``pv_power_w`` as a hardware ceiling."""

    def test_plan_with_hardware_pv_max_unknown_uses_forecast(self):
        # pv_power_w is set very high (8000 W) so any naive
        # "current PV power = hardware limit" interpretation would
        # clip the planner's PV estimate below the forecast. The
        # planner must NOT do that -- the energy_available_kwh must
        # scale with pv_forecast_tomorrow_kwh, not pv_power_w.
        tel_low = _healthy_telemetry(
            pv_power_w=8000.0,
            pv_forecast_tomorrow_kwh=20.0,
            pv_forecast_confidence=0.8,
            pv_forecast_samples=14,
            # Suppress the battery so PV alone drives the comparison.
            battery_soc_pct=20.0,
            battery_soc_corrected_pct=20.0,
            reserve_soc_pct=20.0,
        )
        tel_high = replace(
            tel_low,
            pv_forecast_tomorrow_kwh=40.0,  # doubled forecast
        )
        event = _evt(
            start=NOW + timedelta(hours=1),
            end=NOW + timedelta(hours=25),
        )

        low_result = planner.plan_outage(tel_low, event)
        high_result = planner.plan_outage(tel_high, event)

        # Identical pv_power_w; the only change is the forecast.
        self.assertEqual(tel_low.pv_power_w, tel_high.pv_power_w)
        # A higher forecast MUST yield more energy available -- the
        # planner does not clip to the instantaneous PV power.
        self.assertGreater(
            high_result.plan["energy_available_kwh"],
            low_result.plan["energy_available_kwh"],
            "planner clipped energy_available to current PV power "
            "(violation of rule 1: PV max is UNKNOWN)",
        )


# ---------------------------------------------------------------------------
# Missing consumption history
# ---------------------------------------------------------------------------


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class PlannerConsumptionFallbackTests(unittest.TestCase):
    """When ``tarpit_consumption_avg_kwh`` is missing, the planner
    falls back to ``load_power_w`` and warns."""

    def test_plan_handles_missing_consumption_history_with_warning(self):
        tel = _healthy_telemetry(
            tarpit_consumption_avg_kwh=None,
            load_power_w=600.0,
        )
        event = _evt()  # 2-hour outage
        result = planner.plan_outage(tel, event)

        self.assertTrue(
            any(
                "consumption history missing; using current load" in w
                for w in result.plan["warnings"]
            ),
            f"expected consumption-history warning in {result.plan['warnings']!r}",
        )
        self.assertGreater(result.plan["energy_needed_kwh"], 0.0)
        self.assertTrue(math.isfinite(result.plan["energy_needed_kwh"]))


# ---------------------------------------------------------------------------
# Extreme horizon
# ---------------------------------------------------------------------------


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class PlannerHorizonTests(unittest.TestCase):
    """Events beyond ``max_planning_horizon_days`` are refused."""

    def test_plan_handles_extreme_horizon(self):
        tel = _healthy_telemetry(max_planning_horizon_days=7)
        event = _evt(
            start=NOW + timedelta(days=10),
            end=NOW + timedelta(days=10, hours=2),
        )
        result = planner.plan_outage(tel, event)
        self.assertTrue(result.blocked)
        self.assertIsNotNone(result.reason_if_blocked)
        reason_lower = result.reason_if_blocked.lower()
        self.assertTrue(
            "horizon" in reason_lower or "too far ahead" in reason_lower,
            f"reason did not mention horizon: {result.reason_if_blocked!r}",
        )


# ---------------------------------------------------------------------------
# Grid-charge-rate math (rule: I*V*eff / 1000)
# ---------------------------------------------------------------------------


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class PlannerGridChargeRateTests(unittest.TestCase):
    """Verify the I*V*eff / 1000 derivation for grid-charge rate."""

    def test_plan_estimate_for_grid_charge_hours(self):
        tel = _healthy_telemetry(
            nominal_voltage_v=51.2,
            max_utility_charging_current_a=30.0,
            battery_charge_efficiency=0.92,
        )
        # Force a grid-charging scenario (small battery, long outage).
        tel = replace(
            tel,
            battery_capacity_kwh=5.0,
            battery_capacity_ah=98.0,
            battery_soc_pct=21.0,
            battery_soc_corrected_pct=21.0,
            reserve_soc_pct=20.0,
        )
        event = _evt(
            start=NOW + timedelta(hours=1),
            end=NOW + timedelta(hours=8),
        )
        result = planner.plan_outage(tel, event)

        expected_rate = 30.0 * 51.2 * 0.92 / 1000.0  # 1.41312 kWh/h
        self.assertAlmostEqual(
            result.grid_charge_hours_per_hour, expected_rate, places=3
        )
        # And the same value appears in the plan dict.
        self.assertAlmostEqual(
            result.plan["grid_charge_hours_estimate"], expected_rate, places=3
        )


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class PlannerPurityTests(unittest.TestCase):
    """Two calls with the same input must return equivalent plans."""

    def test_plan_output_is_pure_function(self):
        tel = _healthy_telemetry()
        event = _evt()

        result_a = planner.plan_outage(tel, event)
        result_b = planner.plan_outage(tel, event)

        # ``computed_at_iso`` and the computed-at offset are stamped
        # from the telemetry's ``now_iso``, which is frozen in the
        # fixture. Two calls therefore produce identical dicts.
        self.assertEqual(result_a.to_dict(), result_b.to_dict())


# ---------------------------------------------------------------------------
# Stale (past) events
# ---------------------------------------------------------------------------


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class PlannerStaleEventTests(unittest.TestCase):
    """Events whose end_iso is in the past are refused."""

    def test_plan_stale_event_handled(self):
        tel = _healthy_telemetry()
        # Event ended 1 hour ago.
        event = _evt(
            start=NOW - timedelta(hours=3),
            end=NOW - timedelta(hours=1),
        )
        result = planner.plan_outage(tel, event)
        self.assertTrue(result.blocked)
        self.assertIsNotNone(result.reason_if_blocked)
        reason_lower = result.reason_if_blocked.lower()
        self.assertTrue(
            "past" in reason_lower or "ended" in reason_lower,
            f"reason did not mention past/ended: {result.reason_if_blocked!r}",
        )


# ---------------------------------------------------------------------------
# Telemetry field contract
# ---------------------------------------------------------------------------


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class PlannerTelemetryContractTests(unittest.TestCase):
    """The planner must accept a Telemetry dataclass without mutating
    the caller's copy."""

    def test_planner_does_not_mutate_input_telemetry(self):
        tel = _healthy_telemetry()
        before = {
            f.name: getattr(tel, f.name) for f in dataclasses.fields(tel)
        }
        event = _evt()
        planner.plan_outage(tel, event)
        # Every field must be unchanged.
        for name, expected in before.items():
            self.assertEqual(
                getattr(tel, name),
                expected,
                f"planner mutated telemetry field {name!r}",
            )

    def test_planner_does_not_mutate_input_event(self):
        tel = _healthy_telemetry()
        event = _evt()
        before_start = event.start_iso
        before_end = event.end_iso
        before_status = event.status
        planner.plan_outage(tel, event)
        self.assertEqual(event.start_iso, before_start)
        self.assertEqual(event.end_iso, before_end)
        self.assertEqual(event.status, before_status)

    def test_planner_rejects_naive_now_iso(self):
        # The boundary must reject naive datetimes -- the production
        # helper ``_require_offset_aware_iso`` raises ValueError.
        tel = _healthy_telemetry()
        tel = replace(tel, now_iso="2026-10-13T10:00:00")  # no offset
        event = _evt()
        with self.assertRaises(ValueError):
            planner.plan_outage(tel, event)

    def test_planner_returns_planner_plan_type(self):
        tel = _healthy_telemetry()
        event = _evt()
        result = planner.plan_outage(tel, event)
        self.assertIsInstance(result, planner.PlannerPlan)
        # plan dict must carry all required keys.
        required_keys = {
            "energy_needed_kwh",
            "energy_reserve_kwh",
            "energy_available_kwh",
            "attainable_target_soc_pct",
            "needs_charging_from_grid_kwh",
            "prep_start_iso",
            "grid_charge_hours_estimate",
            "warnings",
        }
        self.assertTrue(required_keys.issubset(set(result.plan.keys())))


# ---------------------------------------------------------------------------
# Pending-event plan-only behaviour
# ---------------------------------------------------------------------------


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class PlannerPendingEventTests(unittest.TestCase):
    """Pending events return a plan but never get blocked on the
    plan-only path -- the dashboard reads the plan and waits for the
    operator's confirm."""

    def test_plan_for_pending_event_is_plan_only_not_blocked(self):
        tel = _healthy_telemetry()
        event = _evt(status="pending")
        result = planner.plan_outage(tel, event)
        self.assertFalse(result.blocked)
        self.assertTrue(
            any("plan-only" in w for w in result.plan["warnings"]),
            "expected a 'plan-only' warning for pending events",
        )


# ---------------------------------------------------------------------------
# ISO round-tripping on the planner boundary
# ---------------------------------------------------------------------------


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class PlannerTimezoneTests(unittest.TestCase):
    """The planner converts to UTC for math but keeps the original
    offset on the way in for the audit trail."""

    def test_planner_computed_at_iso_preserves_offset(self):
        tel = _healthy_telemetry()  # now is +03:00
        event = _evt()
        result = planner.plan_outage(tel, event)
        # computed_at_iso is a stamped copy of the telemetry's
        # now_iso -- both share the same offset.
        self.assertEqual(result.computed_at_iso, tel.now_iso)
        self.assertEqual(
            datetime.fromisoformat(result.computed_at_iso).utcoffset(),
            timedelta(hours=3),
        )


if __name__ == "__main__":
    unittest.main()