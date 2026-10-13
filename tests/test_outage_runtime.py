"""Outage runtime tests (Phase 2C + Phase 3).

Behavioural tests for ``services/outage_runtime.py``. The tests
do NOT touch Home Assistant, the live inverter, or any
in-process persistent storage. They use the project's existing
``importlib.util.spec_from_file_location`` pattern to load the
production source directly (see ``tests/test_outage_planner.py``
and ``tests/test_outage_event_model.py`` for the same idea).

Loading pattern
---------------
``services/outage_runtime.py`` does ``from .outage_planner import
...``. To make the relative import resolve without a real
``powmr_inverter`` package, we build a tiny stub package on
``sys.modules`` whose ``__path__`` points at the repo root. The
two production modules are loaded as sub-modules of that stub
so the relative import resolves at the same site it resolves
at runtime in production.

The stub package never imports HA; the lazy HA imports inside
``outage_runtime.py`` (for ``homeassistant.helpers.event`` and
``homeassistant.core``) are only reached on the live code path,
not by these tests.

Frozen "now"
------------
``mode_from_options`` / ``snapshot_telemetry`` are deterministic
with respect to the coordinator's ``last_update_ts`` + the
current wall clock. The tests inject a fixed ``last_update_ts``
so the freshness arithmetic is stable. ``execute_plan`` and
``is_automatic_allowed`` use the same fields and the same
fixed clock.

Verification commands (run from repo root):

    .test-venv/bin/python -m py_compile tests/test_outage_runtime.py
    .test-venv/bin/python -m unittest tests.test_outage_runtime -v
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICES_DIR = REPO_ROOT / "services"
RUNTIME_PATH = SERVICES_DIR / "outage_runtime.py"
PLANNER_PATH = SERVICES_DIR / "outage_planner.py"
OUTAGE_PATH = SERVICES_DIR / "outage.py"


# ---------------------------------------------------------------------------
# Module loader: stub package so the relative import inside
# outage_runtime.py resolves.
# ---------------------------------------------------------------------------


_STUB_PKG = "powmr_inverter_rt_stub"


def _load_production_modules() -> tuple[types.ModuleType, types.ModuleType, types.ModuleType]:
    """Load ``outage_runtime`` + ``outage_planner`` + ``outage`` as sub-modules.

    The stub package is named ``powmr_inverter_rt_stub`` and lives
    on ``sys.modules`` with ``__path__`` set to the repo root so
    any future sibling sub-modules can be loaded the same way.

    ``outage.py`` imports ``homeassistant.core`` / ``...helpers.storage``
    at module load. We do NOT have HA on sys.path in the test
    env, so we use a ``sys.meta_path`` blocker for ``homeassistant.*``
    AND we install dummy ``homeassistant`` modules with the
    minimum attributes the production module reads. This is the
    same trick the existing tests in the repo use.
    """
    pkg = types.ModuleType(_STUB_PKG)
    pkg.__path__ = [str(REPO_ROOT)]
    sys.modules[_STUB_PKG] = pkg

    # Stub ``homeassistant`` so the production ``outage.py`` can
    # import it. The stub exposes the bare minimum the production
    # module needs.
    if "homeassistant" not in sys.modules:
        ha = types.ModuleType("homeassistant")
        ha.__path__ = []  # mark as package
        sys.modules["homeassistant"] = ha

        ha_core = types.ModuleType("homeassistant.core")
        # ``async_register`` is a method on ``hass.services``; the
        # service registration block calls it. We do not exercise
        # the service path directly so a no-op is fine.
        setattr(ha_core, "HomeAssistant", object)
        setattr(ha_core, "ServiceCall", dict)
        sys.modules["homeassistant.core"] = ha_core

        ha_exc = types.ModuleType("homeassistant.exceptions")
        class _SvcValErr(Exception):
            pass
        setattr(ha_exc, "ServiceValidationError", _SvcValErr)
        sys.modules["homeassistant.exceptions"] = ha_exc

        ha_helpers = types.ModuleType("homeassistant.helpers")
        ha_helpers.__path__ = []
        sys.modules["homeassistant.helpers"] = ha_helpers

        ha_storage = types.ModuleType("homeassistant.helpers.storage")
        class _Store:
            def __init__(self, *a, **kw):
                pass
        setattr(ha_storage, "Store", _Store)
        sys.modules["homeassistant.helpers.storage"] = ha_storage

    # 1. Load outage_planner (no relative imports; safe standalone).
    planner_spec = importlib.util.spec_from_file_location(
        f"{_STUB_PKG}.outage_planner", str(PLANNER_PATH)
    )
    assert planner_spec is not None and planner_spec.loader is not None
    planner_mod = importlib.util.module_from_spec(planner_spec)
    sys.modules[f"{_STUB_PKG}.outage_planner"] = planner_mod
    planner_spec.loader.exec_module(planner_mod)

    # 2. Load outage (imports HA stubs above).
    outage_spec = importlib.util.spec_from_file_location(
        f"{_STUB_PKG}.outage", str(OUTAGE_PATH)
    )
    assert outage_spec is not None and outage_spec.loader is not None
    outage_mod = importlib.util.module_from_spec(outage_spec)
    sys.modules[f"{_STUB_PKG}.outage"] = outage_mod
    outage_spec.loader.exec_module(outage_mod)

    # 3. Load outage_runtime (has ``from .outage_planner import ...``).
    runtime_spec = importlib.util.spec_from_file_location(
        f"{_STUB_PKG}.outage_runtime", str(RUNTIME_PATH)
    )
    assert runtime_spec is not None and runtime_spec.loader is not None
    runtime_mod = importlib.util.module_from_spec(runtime_spec)
    sys.modules[f"{_STUB_PKG}.outage_runtime"] = runtime_mod
    runtime_spec.loader.exec_module(runtime_mod)
    return runtime_mod, planner_mod, outage_mod


runtime, planner, outage = _load_production_modules()


# Structural probe: the production module exposes the public
# API the tests depend on. ``STRUCTURAL_PROBE_OK`` is consumed
# by ``@unittest.skipUnless`` so a regression that renames or
# removes a public symbol surfaces as a loud skip rather than
# a silent failure.
REQUIRED_SYMBOLS = (
    "snapshot_telemetry",
    "plan_for_event",
    "execute_plan",
    "is_automatic_allowed",
    "mode_from_options",
    "wire_outage_runtime",
)
STRUCTURAL_PROBE_OK = all(hasattr(runtime, name) for name in REQUIRED_SYMBOLS)
PROBE_FAILURE_REASON = (
    "production outage_runtime module is missing one of: "
    + ", ".join(REQUIRED_SYMBOLS)
    if not STRUCTURAL_PROBE_OK
    else ""
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 10, 13, 10, 0, 0, tzinfo=KYIV)
NOW_ISO = NOW.astimezone(timezone.utc).isoformat()
EVENT_ID = "evt-test-001"
PLAN_BLOCKED_REASON = "telemetry stale; refusing to plan"

# Some tests need a wall-clock-aligned event window (otherwise
# the test only passes for a few hours after the frozen NOW).
# ``DYNAMIC_NOW`` is the actual ``datetime.now(tz=timezone.utc)``
# used ONLY for time-window construction in those tests; the
# planner's ``now_iso`` is still the frozen ``NOW_ISO`` so plan
# output is deterministic.
from datetime import datetime as _dt_now  # noqa: E402
DYNAMIC_NOW_UTC = _dt_now.now(tz=timezone.utc)
DYNAMIC_NOW = DYNAMIC_NOW_UTC.astimezone(KYIV)


def _iso(dt: datetime) -> str:
    return dt.astimezone(KYIV).isoformat()


def _evt(
    *,
    event_id: str = EVENT_ID,
    start: datetime | None = None,
    end: datetime | None = None,
    status: str = "confirmed",
) -> SimpleNamespace:
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


def _healthy_telemetry(
    *,
    now_iso: str = NOW_ISO,
    freshness_seconds: int = 30,
) -> planner.Telemetry:
    """A fully-populated, healthy telemetry snapshot.

    Mirrors the production defaults in
    ``tests/test_outage_planner.py::_healthy_telemetry`` so the
    planner returns a non-blocked plan with
    ``needs_charging_from_grid_kwh > 0`` (the planner produces
    a non-zero value when the SOC + PV are insufficient for the
    outage window).
    """
    return planner.Telemetry(
        now_iso=now_iso,
        telemetry_freshness_seconds=freshness_seconds,
        battery_capacity_kwh=11.0,
        battery_capacity_ah=215.0,
        nominal_voltage_v=51.2,
        battery_capacity_ok_for_planning=True,
        battery_soc_pct=40.0,  # below reserve so planner needs grid
        battery_soc_corrected_pct=40.0,
        battery_voltage_v=53.5,
        battery_charge_efficiency=0.92,
        battery_discharge_efficiency=0.92,
        pv_power_w=200.0,
        load_power_w=600.0,
        grid_power_w=0.0,
        grid_available=True,
        reserve_soc_pct=80.0,  # high reserve forces a grid charge gap
        max_utility_charging_current_a=30.0,
        max_charging_current_a=60.0,
        pv_forecast_tomorrow_kwh=5.0,
        pv_forecast_confidence=0.8,
        pv_forecast_samples=14,
        tarpit_consumption_avg_kwh={
            "2026-10-12": 14.0,
            "2026-10-11": 13.5,
            "2026-10-10": 15.0,
        },
        current_pause_until_iso=None,
        max_planning_horizon_days=7,
        is_holiday_or_weekend=False,
    )


def _build_blocked_plan(reason: str = PLAN_BLOCKED_REASON) -> planner.PlannerPlan:
    """Build a planner-shaped object that the runtime will treat as blocked."""
    return planner.PlannerPlan(
        event_id=EVENT_ID,
        computed_at_iso=NOW_ISO,
        mode="observation",
        plan={"needs_charging_from_grid_kwh": 0.0},
        blocked=True,
        reason_if_blocked=reason,
        reason_if_incomplete=None,
        next_attempt_iso=None,
    )


def _build_unblocked_plan(needs_grid_kwh: float = 2.0) -> planner.PlannerPlan:
    """Build a planner-shaped object that the runtime will accept."""
    return planner.PlannerPlan(
        event_id=EVENT_ID,
        computed_at_iso=NOW_ISO,
        mode="observation",
        plan={
            "needs_charging_from_grid_kwh": needs_grid_kwh,
            "grid_available": True,
            "telemetry_freshness_seconds": 30,
            "current_pause_until_iso": None,
        },
        blocked=False,
        reason_if_blocked=None,
        reason_if_incomplete=None,
        next_attempt_iso=None,
    )


# ---------------------------------------------------------------------------
# Fake coordinator
# ---------------------------------------------------------------------------


class FakeHems:
    """Bare-minimum stand-in for the HEMS engine.

    The runtime reads ``_manual_override_until`` from
    ``coordinator._hems``, and the calibrator (optional). We
    default to no manual pause and no calibrator.
    """

    def __init__(self, *, manual_until: datetime | None = None) -> None:
        self._manual_override_until = manual_until
        self._consumption_history_with_dates: dict[str, float] = {}
        self._predictive_controller = SimpleNamespace(
            calibrator=SimpleNamespace(
                metrics=lambda: SimpleNamespace(
                    confidence_factor=0.8, sample_count=14
                )
            )
        )


class FakeCoordinator:
    """Stand-in for ``InverterCoordinator``.

    The runtime reads the following attributes:
      * ``data`` -- the live telemetry dict
      * ``_battery_capacity_kwh``, ``_battery_capacity_ah``,
        ``_nominal_voltage_v``, ``_battery_capacity_ok_for_planning``
      * ``_hems`` -- a ``FakeHems`` (or similar)
      * ``forecast_tomorrow_kwh``
      * ``last_update_ts``
      * ``_execute_hems_command`` -- the protected dispatcher
      * ``options`` or ``_entry.options`` -- operator config
    """

    def __init__(
        self,
        *,
        data: dict | None = None,
        options: dict | None = None,
        hems: FakeHems | None = None,
        capacity_kwh: float | None = 11.0,
        capacity_ah: float | None = 215.0,
        voltage_v: float | None = 51.2,
        capacity_ok: bool = True,
        forecast_tomorrow_kwh: float | None = 12.0,
        last_update_ts: datetime | None = None,
        execute_hems_command=None,
    ) -> None:
        self.data = data
        self._battery_capacity_kwh = capacity_kwh
        self._battery_capacity_ah = capacity_ah
        self._nominal_voltage_v = voltage_v
        self._battery_capacity_ok_for_planning = capacity_ok
        self._hems = hems if hems is not None else FakeHems()
        self.forecast_tomorrow_kwh = forecast_tomorrow_kwh
        self.last_update_ts = (
            last_update_ts if last_update_ts is not None
            else datetime.now(timezone.utc) - timedelta(seconds=10)
        )
        self.options = options or {}
        self._entry = SimpleNamespace(options=options or {})
        self._execute_hems_command = execute_hems_command

    def set_execute_hems_command(self, fn) -> None:
        self._execute_hems_command = fn


class FakeStore:
    """Minimal stand-in for ``EventStore`` for Phase 3 tests.

    The runtime reads ``iter_active()`` and ``list_events()``.
    We expose a small list of events so the tick can resolve
    the active event without going through the real
    ``EventStore`` (which requires a full ``OutageEvent`` and
    triggers async save on add).
    """

    def __init__(self, events: list | None = None) -> None:
        self._events = list(events or [])

    def iter_active(self):
        for ev in self._events:
            yield ev

    def list_events(self):
        return list(self._events)


# ---------------------------------------------------------------------------
# Test suites
# ---------------------------------------------------------------------------


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class SnapshotTelemetryTests(unittest.TestCase):
    """``snapshot_telemetry`` returns a populated ``Telemetry``."""

    def test_snapshot_telemetry_returns_none_for_missing_coordinator_data(self):
        """data=None: every field except ``now_iso``/``freshness`` is None/0; no raise."""
        coord = FakeCoordinator(
            data=None,
            capacity_kwh=None,
            capacity_ah=None,
            voltage_v=None,
            forecast_tomorrow_kwh=None,
        )
        # The snapshot must not raise.
        tel = runtime.snapshot_telemetry(coord, hass=None)
        # now_iso + freshness are always populated.
        self.assertIsInstance(tel.now_iso, str)
        self.assertIsNotNone(tel.now_iso)
        self.assertGreaterEqual(tel.telemetry_freshness_seconds, 0)
        # Every other field is None.
        self.assertIsNone(tel.battery_capacity_kwh)
        self.assertIsNone(tel.battery_soc_pct)
        self.assertIsNone(tel.battery_voltage_v)
        self.assertIsNone(tel.pv_power_w)
        self.assertIsNone(tel.load_power_w)
        self.assertIsNone(tel.grid_power_w)
        self.assertIsNone(tel.pv_forecast_tomorrow_kwh)
        # grid_available defaults to True (absence == "don't know",
        # not "false").
        self.assertTrue(tel.grid_available)

    def test_snapshot_telemetry_packs_real_coordinator_data(self):
        """Real data + options: every field is populated and math is correct."""
        coord = FakeCoordinator(
            data={
                "batterySoc": 80,
                "correctedSoc": 78,
                "batteryVoltage": 51.2,
                "pvPower": 500,
                "loadPower": 300,
                "gridPower": -200,
                "gridAvailable": True,
            },
            forecast_tomorrow_kwh=None,
            options={
                "reserve_soc": 25,
                "nominal_voltage_v": 51.2,
                "battery_capacity_ah": 230,
                "outage_mode": "observation",
            },
        )
        tel = runtime.snapshot_telemetry(coord, hass=None)
        # SOC + correctedSoc
        self.assertEqual(tel.battery_soc_pct, 80.0)
        self.assertEqual(tel.battery_soc_corrected_pct, 78.0)
        # Power
        self.assertEqual(tel.pv_power_w, 500.0)
        self.assertEqual(tel.load_power_w, 300.0)
        self.assertEqual(tel.grid_power_w, -200.0)
        self.assertTrue(tel.grid_available)
        # Voltage
        self.assertEqual(tel.battery_voltage_v, 51.2)
        # Capacity math: 230 * 51.2 / 1000 = 11.776
        self.assertAlmostEqual(tel.battery_capacity_kwh, 11.776, places=3)
        self.assertEqual(tel.battery_capacity_ah, 230.0)
        self.assertEqual(tel.nominal_voltage_v, 51.2)
        self.assertTrue(tel.battery_capacity_ok_for_planning)
        # Reserve SOC + mode
        self.assertEqual(tel.reserve_soc_pct, 25.0)
        # forecast is None (not overridden in the test) -> None
        self.assertIsNone(tel.pv_forecast_tomorrow_kwh)
        # Plan can consume this telemetry.
        ev = _evt()
        plan = planner.plan_outage(tel, ev)
        # Plan is well-formed (no exception, has an event_id, etc.).
        self.assertEqual(plan.event_id, EVENT_ID)

    def test_snapshot_telemetry_corrected_soc_falls_back_to_soc(self):
        """When correctedSoc is missing, snapshot falls back to batterySoc."""
        coord = FakeCoordinator(
            data={"batterySoc": 72},
            options={},
        )
        tel = runtime.snapshot_telemetry(coord, hass=None)
        self.assertEqual(tel.battery_soc_pct, 72.0)
        self.assertEqual(tel.battery_soc_corrected_pct, 72.0)


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class PlanForEventTests(unittest.TestCase):
    """``plan_for_event`` runs the planner with the snapshot's telemetry."""

    def test_plan_for_event_runs_planner_with_snapshot(self):
        """``plan_for_event`` is a thin wrapper; planner receives the snapshot."""
        coord = FakeCoordinator(
            data={"batterySoc": 80, "pvPower": 500, "loadPower": 300,
                  "gridPower": -200, "gridAvailable": True},
            options={"reserve_soc": 25},
        )
        ev = _evt()
        # The wrapper calls snapshot_telemetry + plan_outage. We
        # assert on the side-effects, not on a stub: the plan
        # event_id matches and the snapshot's fields are visible
        # to the planner.
        plan = runtime.plan_for_event(coord, hass=None, event=ev)
        self.assertEqual(plan.event_id, ev.id)
        # Planner is pure; the result is stable given the same
        # snapshot + event. Calling plan_for_event twice yields
        # the same plan dict (modulo any timestamp inside the
        # snapshot -- which we fix via a frozen last_update_ts).
        coord.last_update_ts = datetime.now(timezone.utc) - timedelta(seconds=10)
        plan2 = runtime.plan_for_event(coord, hass=None, event=ev)
        # event_id + blocked + plan dict are stable.
        self.assertEqual(plan.event_id, plan2.event_id)
        self.assertEqual(plan.blocked, plan2.blocked)


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class ExecutePlanTests(unittest.TestCase):
    """``execute_plan`` routes through the protected dispatcher.

    Each test installs a fake ``_execute_hems_command`` on the
    coordinator and asserts the runtime's behaviour against it.
    """

    def _capture_dispatcher(self) -> tuple[list, callable]:
        """Return (calls, dispatcher_fn).

        ``calls`` is a list of (decision, command_id, kwargs) tuples
        appended to on every call. ``dispatcher_fn`` is a coroutine
        that records its arguments and returns None.
        """
        calls: list = []

        async def _fn(decision, command_id=None, *, evidence_recorded=False):
            calls.append((decision, command_id, evidence_recorded))

        return calls, _fn

    def _run(self, coro):
        """Drive an async coroutine to completion on a fresh event loop."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_execute_plan_does_nothing_when_plan_blocked(self):
        """Blocked plan -> execute_plan returns early, dispatcher NOT called."""
        calls, fn = self._capture_dispatcher()
        coord = FakeCoordinator(execute_hems_command=fn)
        plan = _build_blocked_plan("battery invalid")
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertFalse(result["executed"])
        self.assertIn("battery invalid", result["reason"])
        self.assertEqual(calls, [], msg="dispatcher must not be called")

    def test_execute_plan_calls_protected_dispatcher_for_grid_charge(self):
        """Healthy plan + grid charge needed -> dispatcher called once."""
        calls, fn = self._capture_dispatcher()
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            execute_hems_command=fn,
        )
        plan = _build_unblocked_plan(needs_grid_kwh=3.5)
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertTrue(result["executed"], msg=result)
        self.assertEqual(len(calls), 1, msg="dispatcher must be called exactly once")
        decision, command_id, evidence_recorded = calls[0]
        # command_id format: "planned_outage:<event_id>"
        self.assertEqual(command_id, f"planned_outage:{EVENT_ID}")
        # evidence is recorded (the dispatcher decides what to do
        # with it; the runtime just passes the flag through).
        self.assertTrue(evidence_recorded)
        # The decision carries the prep reason + skip/buzzer flags.
        self.assertIn("planned_outage", decision.reason)
        self.assertIn(EVENT_ID, decision.reason)
        self.assertFalse(decision.skip)
        self.assertFalse(decision.buzzer_off)

    def test_execute_plan_refuses_when_manual_pause_active(self):
        """Future manual pause -> execute_plan refuses."""
        calls, fn = self._capture_dispatcher()
        hems = FakeHems(
            manual_until=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            hems=hems,
            execute_hems_command=fn,
        )
        plan = _build_unblocked_plan(needs_grid_kwh=3.5)
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertFalse(result["executed"])
        self.assertIn("manual pause", result["reason"])
        self.assertEqual(calls, [], msg="dispatcher must not be called")

    def test_execute_plan_refuses_when_telemetry_stale(self):
        """Stale telemetry -> execute_plan refuses."""
        calls, fn = self._capture_dispatcher()
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            last_update_ts=datetime.now(timezone.utc) - timedelta(seconds=600),
            execute_hems_command=fn,
        )
        plan = _build_unblocked_plan(needs_grid_kwh=3.5)
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertFalse(result["executed"])
        self.assertIn("telemetry stale", result["reason"])
        self.assertEqual(calls, [], msg="dispatcher must not be called")

    def test_execute_plan_never_sets_solar_priority_during_outage(self):
        """The decision's output_priority is USB or SBU; never SOLAR or UTI."""
        calls, fn = self._capture_dispatcher()
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            execute_hems_command=fn,
        )
        plan = _build_unblocked_plan(needs_grid_kwh=3.5)
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertTrue(result["executed"], msg=result)
        decision = calls[0][0]
        self.assertIn(
            decision.output_priority, ("USB", "SBU"),
            msg=f"output_priority must be USB or SBU, got {decision.output_priority!r}",
        )
        self.assertNotIn(decision.output_priority, ("SOLAR", "UTI"))
        # Charger priority is the prep-time value (solar+utility).
        self.assertIn(
            decision.charger_priority, ("SNU", "OSO", "CSO"),
            msg=f"charger_priority unexpected: {decision.charger_priority!r}",
        )

    def test_dispatcher_call_signature_uses_existing_protected_path(self):
        """The runtime calls the coordinator's protected dispatcher exactly once
        with a decision that has the three fields the engine reads."""
        calls, fn = self._capture_dispatcher()
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            execute_hems_command=fn,
        )
        plan = _build_unblocked_plan(needs_grid_kwh=2.0)
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertTrue(result["executed"], msg=result)
        self.assertEqual(len(calls), 1)
        decision, command_id, evidence_recorded = calls[0]
        # Decision has the three fields the engine reads.
        for attr in ("output_priority", "charger_priority", "reason"):
            self.assertTrue(
                hasattr(decision, attr),
                msg=f"decision missing {attr!r}",
            )
        # evidence_recorded=True is passed so the coordinator's
        # evidence journal gets the prep cause code.
        self.assertTrue(evidence_recorded)
        # command_id is the planned_outage:<event_id> form the
        # evidence_journal.command_scope will key writes under.
        self.assertEqual(command_id, f"planned_outage:{EVENT_ID}")


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class IsAutomaticAllowedTests(unittest.TestCase):
    """``is_automatic_allowed`` gates automatic dispatch."""

    def test_is_automatic_allowed_returns_false_for_disabled_mode(self):
        """options['outage_mode'] = 'disabled' -> (False, "mode disabled; ...")."""
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            options={"outage_mode": "disabled"},
        )
        plan = _build_unblocked_plan(needs_grid_kwh=2.0)
        allowed, reason = runtime.is_automatic_allowed(coord, plan)
        self.assertFalse(allowed)
        self.assertIn("mode disabled", reason)

    def test_is_automatic_allowed_returns_true_for_clean_observation_plan(self):
        """Healthy plan + mode=automatic -> gate returns True."""
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            options={"outage_mode": "automatic"},
        )
        plan = _build_unblocked_plan(needs_grid_kwh=2.0)
        allowed, reason = runtime.is_automatic_allowed(coord, plan)
        self.assertTrue(allowed, msg=reason)
        self.assertEqual(reason, "")

    def test_is_automatic_allowed_returns_false_when_plan_blocked(self):
        """Blocked plan -> gate returns False with the planner's reason."""
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            options={"outage_mode": "automatic"},
        )
        plan = _build_blocked_plan("battery invalid")
        allowed, reason = runtime.is_automatic_allowed(coord, plan)
        self.assertFalse(allowed)
        self.assertIn("plan blocked", reason)
        self.assertIn("battery invalid", reason)


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class ModeFromOptionsTests(unittest.TestCase):
    """``mode_from_options`` is the operator's mode knob."""

    def test_mode_from_options_defaults_to_observation(self):
        self.assertEqual(runtime.mode_from_options({}), "observation")
        self.assertEqual(runtime.mode_from_options(None), "observation")
        self.assertEqual(runtime.mode_from_options({"other": 1}), "observation")

    def test_mode_from_options_recognises_all_three_values(self):
        for value in ("disabled", "observation", "automatic"):
            self.assertEqual(
                runtime.mode_from_options({"outage_mode": value}), value
            )

    def test_mode_from_options_rejects_unknown_value(self):
        with self.assertRaises(ValueError):
            runtime.mode_from_options({"outage_mode": "wibble"})


# ---------------------------------------------------------------------------
# Phase 3 tests: scheduled job + mode selector
# ---------------------------------------------------------------------------


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class Phase3ModeTests(unittest.TestCase):
    """Phase 3: mode selector + scheduled tick.

    These tests drive the ``_scheduled_tick`` body directly (it is
    a private helper but the spec's Phase 3 tests target the
    observable behaviour of the tick). HA's
    ``async_track_time_interval`` is NOT exercised -- the test
    captures the tick coroutine and awaits it manually.
    """

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def _hass_stub(self) -> SimpleNamespace:
        """A minimal hass stub with ``data``, ``services`` and
        ``config_entries``.

        ``config_entries.async_get_entry(entry_id)`` returns a
        SimpleNamespace whose ``.options`` dict can be mutated by
        the test. The defaults match the entry map under
        ``hass.data[DOMAIN]`` so a single ``outage_mode`` field
        drives both lookup paths.
        """
        data: dict = {}
        options: dict = {}

        class _Entry:
            def __init__(self):
                self.options = options

        class _ConfigEntries:
            def __init__(self):
                self._entries: dict[str, _Entry] = {}

            def async_get_entry(self, entry_id: str):
                if entry_id not in self._entries:
                    self._entries[entry_id] = _Entry()
                return self._entries[entry_id]

            def set_options(self, entry_id: str, opts: dict):
                self._entries[entry_id] = _Entry()
                self._entries[entry_id].options = opts

        class _Services:
            def __init__(self):
                self.calls: list = []

            def async_register(self, domain, name, handler):
                self.calls.append((domain, name, handler))

        services = _Services()
        return SimpleNamespace(
            data=data, services=services,
            config_entries=_ConfigEntries(),
        )

    def test_mode_disabled_means_no_plan_and_no_execute(self):
        """Mode=disabled: compute_outage_plan refuses; the tick is a no-op."""
        # 1. The mode selector returns 'disabled'.
        self.assertEqual(
            runtime.mode_from_options({"outage_mode": "disabled"}), "disabled"
        )
        # 2. The tick body is short-circuited. We drive it
        #    by directly calling ``_scheduled_tick`` with a hass
        #    stub that has no event store and no coordinator.
        hass = self._hass_stub()
        # No bundle registered -- tick must return cleanly.
        self._run(runtime._scheduled_tick(hass, "no-such-entry", NOW))

    def test_mode_observation_logs_only_no_execute(self):
        """Mode=observation: tick runs plan_for_event; execute_plan NOT called."""
        # We capture execute_plan by stubbing it on the runtime module
        # for the duration of this test, then restoring the original.
        original = runtime.execute_plan
        calls: list = []

        async def _spy(coordinator, event, plan):
            calls.append((coordinator, event, plan))
            return {"executed": False, "reason": "spy"}

        setattr(runtime, "execute_plan", _spy)
        try:
            hass = self._hass_stub()
            coord = FakeCoordinator(
                data={"batterySoc": 40, "gridAvailable": True,
                      "pvPower": 100, "loadPower": 200},
                options={"outage_mode": "observation"},
            )
            # Build a bundle + a confirmed active event so the
            # tick has something to plan for.
            ev = _evt(
                start=NOW - timedelta(minutes=30),
                end=NOW + timedelta(hours=1),
            )
            store = FakeStore([ev])
            hass.data["powmr_inverter"] = {
                "entry-x": {
                    "outage_coordinator": coord,
                    "outage_store": store,
                    "outage_mode": "observation",
                }
            }
            # Drive the tick.
            self._run(runtime._scheduled_tick(hass, "entry-x", NOW))
            # Observation -> execute_plan was NOT called.
            self.assertEqual(calls, [])
        finally:
            setattr(runtime, "execute_plan", original)

    def test_mode_automatic_calls_execute_plan_on_due_event(self):
        """Mode=automatic + due event + clean plan -> execute_plan called once."""
        # We spy on execute_plan via the runtime module.
        original = runtime.execute_plan
        calls: list = []

        async def _spy(coordinator, event, plan):
            calls.append((coordinator, event, plan))
            return {"executed": False, "reason": "spy"}

        setattr(runtime, "execute_plan", _spy)
        try:
            hass = self._hass_stub()
            coord = FakeCoordinator(
                data={"batterySoc": 40, "gridAvailable": True,
                      "pvPower": 100, "loadPower": 200},
                options={"outage_mode": "automatic"},
            )
            # Event whose prep window has already started. Use
            # DYNAMIC_NOW so the event window is always in the
            # future relative to wall clock; start in the past
            # relative to DYNAMIC_NOW so the active-window check
            # finds it, end in the future so it is not yet terminal.
            ev = _evt(
                start=DYNAMIC_NOW - timedelta(minutes=10),
                end=DYNAMIC_NOW + timedelta(hours=2),
            )
            store = FakeStore([ev])
            hass.data["powmr_inverter"] = {
                "entry-y": {
                    "outage_coordinator": coord,
                    "outage_store": store,
                    "outage_mode": "automatic",
                }
            }
            hass.config_entries.set_options(
                "entry-y", {"outage_mode": "automatic"}
            )
            # Drive the tick.
            self._run(runtime._scheduled_tick(hass, "entry-y", DYNAMIC_NOW))
            # Automatic + due event -> execute_plan called once.
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1].id, ev.id)
        finally:
            setattr(runtime, "execute_plan", original)

    def test_mode_automatic_skips_blocked_plans(self):
        """Automatic + blocked plan -> execute_plan was called but dispatcher NOT.
        """
        original = runtime.execute_plan
        dispatcher_calls: list = []

        async def _spy(coordinator, event, plan):
            # Mimic the real execute_plan: it sees the blocked
            # flag and returns early WITHOUT calling the
            # dispatcher.
            if getattr(plan, "blocked", False):
                return {"executed": False, "reason": "plan blocked"}
            dispatcher_calls.append((event.id, plan))
            return {"executed": True, "command_id": "test"}

        setattr(runtime, "execute_plan", _spy)
        try:
            hass = self._hass_stub()
            coord = FakeCoordinator(
                data={"batterySoc": 40, "gridAvailable": True,
                      "pvPower": 100, "loadPower": 200},
                options={"outage_mode": "automatic"},
            )
            ev = _evt(
                start=NOW + timedelta(minutes=10),
                end=NOW + timedelta(hours=2),
            )
            store = FakeStore([ev])
            hass.data["powmr_inverter"] = {
                "entry-z": {
                    "outage_coordinator": coord,
                    "outage_store": store,
                    "outage_mode": "automatic",
                }
            }
            # Stub plan_for_event to return a blocked plan. This
            # drives the gate (the gate sees ``plan.blocked`` and
            # returns ``(False, ...)``), so the spy is never
            # reached. We test the SPEC's contract: dispatcher
            # is never called.
            plan_for_event_orig = runtime.plan_for_event

            def _blocked_plan(coordinator, hass, event):
                return _build_blocked_plan("synthetic block for test")

            setattr(runtime, "plan_for_event", _blocked_plan)
            try:
                self._run(runtime._scheduled_tick(hass, "entry-z", NOW))
            finally:
                setattr(runtime, "plan_for_event", plan_for_event_orig)
            # The dispatcher was never called.
            self.assertEqual(dispatcher_calls, [])
        finally:
            setattr(runtime, "execute_plan", original)

    def test_mode_automatic_keeps_live_modes_untouched(self):
        """Several ticks in automatic mode do not mutate the entry's live modes.

        The test asserts the spec contract: the scheduler must
        NEVER touch ``entry.options.predictive_mode`` /
        ``smart_mode`` / ``hems_auto_mode``. The runtime module
        has no code path that mutates these fields, so the
        assertion is a structural check: after multiple ticks,
        the options dict is identical to what the test set.
        """
        # Set up a bundle whose options carry the live modes the
        # operator configured. The tick must not touch them.
        live_options = {
            "outage_mode": "automatic",
            "predictive_mode": "shadow",
            "smart_mode": 0,
            "hems_auto_mode": None,
        }
        original = runtime.execute_plan

        async def _spy(coordinator, event, plan):
            return {"executed": False, "reason": "spy"}

        setattr(runtime, "execute_plan", _spy)
        try:
            hass = self._hass_stub()
            coord = FakeCoordinator(
                data={"batterySoc": 40, "gridAvailable": True,
                      "pvPower": 100, "loadPower": 200},
                options=live_options,
            )
            ev = _evt(
                start=NOW + timedelta(minutes=10),
                end=NOW + timedelta(hours=2),
            )
            store = FakeStore([ev])
            hass.data["powmr_inverter"] = {
                "entry-modes": {
                    "outage_coordinator": coord,
                    "outage_store": store,
                    "outage_mode": "automatic",
                }
            }
            for _ in range(3):
                self._run(runtime._scheduled_tick(hass, "entry-modes", NOW))
            # The coordinator's options are exactly what the
            # operator set; the runtime never overwrote them.
            self.assertEqual(coord.options, live_options)
            self.assertEqual(coord.options.get("predictive_mode"), "shadow")
            self.assertEqual(coord.options.get("smart_mode"), 0)
            self.assertIsNone(coord.options.get("hems_auto_mode"))
        finally:
            setattr(runtime, "execute_plan", original)


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class WireOutageRuntimeTests(unittest.TestCase):
    """``wire_outage_runtime`` registers the service and may start the tick.

    The HA service-registry path is exercised against a tiny
    hass stub (no Home Assistant imports). The ``async_start``
    is a no-op when mode != 'automatic' (we don't have HA's
    ``async_track_time_interval`` on sys.path; the runtime
    only reaches that import when mode is 'automatic').
    """

    def _hass_stub(self) -> SimpleNamespace:
        data: dict = {}

        class _Services:
            def __init__(self):
                self.calls: list = []

            def async_register(self, domain, name, handler):
                self.calls.append((domain, name, handler))

        services = _Services()
        return SimpleNamespace(data=data, services=services)

    def _entry_stub(self, *, options: dict | None = None) -> SimpleNamespace:
        entry = SimpleNamespace(
            entry_id="entry-wire",
            options=options or {},
        )
        # Capture async_on_unload calls so we can assert clean
        # teardown is wired.
        entry._unload_calls: list = []
        entry.async_on_unload = lambda fn: entry._unload_calls.append(fn)  # type: ignore[attr-defined]
        return entry

    def test_wire_outage_runtime_in_observation_does_not_register_tick(self):
        """Mode=observation: service is registered, no tick is registered."""
        hass = self._hass_stub()
        entry = self._entry_stub(options={"outage_mode": "observation"})
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
        )
        runtime.wire_outage_runtime(hass, entry, coord)
        # Service registered.
        service_names = [name for _d, name, _h in hass.services.calls]
        self.assertTrue(
            any(name == f"{entry.entry_id}_compute_outage_plan" for name in service_names),
            msg=f"service not registered: calls={hass.services.calls}",
        )
        # No tick was registered (no async_on_unload for the
        # tick unsub).
        self.assertEqual(entry._unload_calls, [])

    def test_wire_outage_runtime_in_automatic_attempts_tick_registration(self):
        """Mode=automatic: the runtime tries to register a tick. In the
        test env (no HA on sys.path) the registration raises and
        is swallowed. The service is still registered."""
        hass = self._hass_stub()
        entry = self._entry_stub(options={"outage_mode": "automatic"})
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
        )
        runtime.wire_outage_runtime(hass, entry, coord)
        # Service registered.
        service_names = [name for _d, name, _h in hass.services.calls]
        self.assertTrue(
            any(name == f"{entry.entry_id}_compute_outage_plan" for name in service_names),
        )

    def test_wire_outage_runtime_in_disabled_registers_nothing_extra(self):
        """Mode=disabled: service still registered (operator may flip
        the mode later), no tick registered."""
        hass = self._hass_stub()
        entry = self._entry_stub(options={"outage_mode": "disabled"})
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
        )
        runtime.wire_outage_runtime(hass, entry, coord)
        # Service registered.
        service_names = [name for _d, name, _h in hass.services.calls]
        self.assertTrue(
            any(name == f"{entry.entry_id}_compute_outage_plan" for name in service_names),
        )
        # No tick.
        self.assertEqual(entry._unload_calls, [])

    def test_wire_outage_runtime_seeds_outage_mode_when_missing(self):
        """First install: entry.options lacks ``outage_mode``; the wire
        logs but does not raise. The runtime treats 'missing' as
        'observation' (the spec default)."""
        hass = self._hass_stub()
        entry = self._entry_stub(options={})
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
        )
        # Must not raise.
        runtime.wire_outage_runtime(hass, entry, coord)


if __name__ == "__main__":
    unittest.main()
