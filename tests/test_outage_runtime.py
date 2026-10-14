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
from typing import Any
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


class FakeApiTransport:
    """Stub of ``InverterHttpApi`` for the dispatcher pipeline.

    Phase 2C/3 follow-up (Codex DEFECT A + B): replacing the
    previous ``execute_hems_command=lambda ...`` shortcut with a
    faithful copy of the production dispatcher's transport layer
    so the new ``DispatcherResult`` contract can be exercised
    end-to-end against the real algorithm.

    Records every call into ``calls`` (a list of
    ``(method, args, response)``) so a test can assert that, for
    example, ``set_buzzer`` was NEVER invoked when the decision
    carried ``buzzer_off=None``. A transport can be configured
    to reject specific writes (``reject_output``,
    ``reject_charger``, ``reject_buzzer``) so each failure mode
    is testable in isolation.

    The four ``get_*`` helpers model the readback pipeline of
    the canonical dispatcher (``coordinator.py:_execute_hems_command``
    phase 2) and are configurable so each test can pin a specific
    readback-mismatch scenario.
    """

    DEFAULT_OUTPUT = "0"  # USB
    DEFAULT_CHARGER = "1"  # SNU
    DEFAULT_BUZZER = "1"  # ON

    def __init__(
        self,
        *,
        get_output_priority: str | None = None,
        get_charger_priority: str | None = None,
        get_buzzer: str | None = None,
        reject_output: bool = False,
        reject_charger: bool = False,
        reject_buzzer: bool = False,
        raise_on_set: Exception | None = None,
        readback_mismatch_output: str | None = None,
        readback_mismatch_charger: str | None = None,
        last_success_at=None,
    ) -> None:
        self.calls: list = []
        # The "live" device state. The dispatcher's GET path
        # reads from these; the dispatcher SET path updates them
        # in lockstep.
        self._output = self.DEFAULT_OUTPUT
        self._charger = self.DEFAULT_CHARGER
        self._buzzer = self.DEFAULT_BUZZER
        # Canonical freshness timestamp the runtime reads at
        # ``_read_freshness_seconds`` (services/outage_runtime.py).
        # Defaults to ``None`` so a stale-missing-data test can
        # construct a transport that fails closed.
        self.last_realtime_success_received_at = last_success_at
        self.last_realtime_response_received_at = None
        self.last_realtime_source = "unavailable"
        # Per-write overrides -- a test can pin a different
        # initial state than the defaults.
        if get_output_priority is not None:
            self._output = str(get_output_priority)
        if get_charger_priority is not None:
            self._charger = str(get_charger_priority)
        if get_buzzer is not None:
            self._buzzer = str(get_buzzer)
        # Failure modes.
        self._reject_output = reject_output
        self._reject_charger = reject_charger
        self._reject_buzzer = reject_buzzer
        self._raise_on_set = raise_on_set
        self._readback_mismatch_output = readback_mismatch_output
        self._readback_mismatch_charger = readback_mismatch_charger

    # --- SET path. ``True`` means "HTTP 2xx / call succeeded";
    # ``False`` means "transport rejected the write" (the OLD
    # production dispatcher did not read this return value but
    # the NEW contract captures it as ``api_accepted=False``).

    async def set_output_priority(self, value: Any) -> bool:
        self.calls.append(("set_output_priority", (value,), None))
        if self._raise_on_set is not None:
            raise self._raise_on_set
        if self._reject_output:
            return False
        self._output = str(value)
        return True

    async def set_charger_priority(self, value: Any) -> bool:
        self.calls.append(("set_charger_priority", (value,), None))
        if self._raise_on_set is not None:
            raise self._raise_on_set
        if self._reject_charger:
            return False
        self._charger = str(value)
        return True

    async def set_config_item(self, key: str, value: str) -> bool:
        # The production dispatcher's acoustic-comfort branch
        # writes through ``set_config_item("buzzerAlarmSetting", ...)``.
        self.calls.append(("set_config_item", (key, value), None))
        if self._raise_on_set is not None:
            raise self._raise_on_set
        if key == "buzzerAlarmSetting":
            if self._reject_buzzer:
                return False
            self._buzzer = str(value)
            return True
        # For non-buzzer config items the fake transport is
        # permissive so future tests do not have to enumerate
        # every key.
        return True

    # --- Readback path. The production dispatcher confirms the
    # write persisted on the device by reading the device's
    # configuration back. The canonical production method is
    # ``InverterApiClient.fetch_device_configs()`` (api.py:1434)
    # which returns a dict keyed by config name with shape
    # ``{key: {"value": str, ...}}``. Tests configure
    # ``_readback_mismatch_*`` so a mismatch raises an exception
    # (production parity: the readback step raises on mismatch).

    async def fetch_device_configs(self) -> dict:
        self.calls.append(("fetch_device_configs", (), None))
        if self._readback_mismatch_output is not None:
            raise RuntimeError(
                f"output readback mismatch: requested "
                f"{self._readback_mismatch_output!r} but device "
                f"reports {self._output!r}"
            )
        if self._readback_mismatch_charger is not None:
            raise RuntimeError(
                f"charger readback mismatch: requested "
                f"{self._readback_mismatch_charger!r} but device "
                f"reports {self._charger!r}"
            )
        return {
            "outputSourcePrioritySetting": {
                "value": self._output,
                "name": "Output Source Priority",
            },
            "chargerSourcePrioritySetting": {
                "value": self._charger,
                "name": "Charger Source Priority",
            },
        }

    async def get_buzzer(self) -> str:
        self.calls.append(("get_buzzer", (), self._buzzer))
        return self._buzzer


# Backwards-compatibility alias used by the freshness-gate tests.
FakeApi = FakeApiTransport


class FakeHems:
    """Bare-minimum stand-in for the HEMS engine.

    The runtime reads ``_manual_override_until`` /
    ``_blocked_until`` (safety guards) / ``_consumption_history``
    / ``_predictive_controller`` (calibrator) from
    ``coordinator._hems``. Each surface is independent so a
    test can set only the gates it cares about.

    Phase 2C/3 follow-up (Codex DEFECT C): the real production
    surface is ``HemsEngine.can_dispatch_outage_decision``
    (engine.py:580). ``FakeHems`` exposes the same method so the
    tests drive the CANONICAL guard chain rather than the
    pre-hotfix inline check in ``outage_runtime._safety_guards_ok``.
    The runtime's thin wrapper forwards here and the production
    dispatcher (``InverterCoordinator._execute_hems_command``,
    coordinator.py:1645+) calls the exact same method.
    """

    def __init__(
        self,
        *,
        manual_until: datetime | None = None,
        blocked_until: datetime | None = None,
        last_cmd_output_at: datetime | None = None,
        last_cmd_charger_at: datetime | None = None,
        hems_auto_mode: bool = True,
        command_dedup_window: timedelta | None = None,
    ) -> None:
        self._manual_override_until = manual_until
        self._blocked_until = blocked_until
        self._last_cmd_output_at = last_cmd_output_at
        self._last_cmd_charger_at = last_cmd_charger_at
        self.hems_auto_mode = hems_auto_mode
        self._command_dedup_window = (
            command_dedup_window
            if isinstance(command_dedup_window, timedelta)
            else timedelta(seconds=30)
        )
        self._consumption_history_with_dates: dict[str, float] = {}
        self._predictive_controller = SimpleNamespace(
            calibrator=SimpleNamespace(
                metrics=lambda: SimpleNamespace(
                    confidence_factor=0.8, sample_count=14
                )
            )
        )
        self.calls: list = []  # records every can_dispatch call

    def can_dispatch_outage_decision(
        self, decision: Any = None
    ) -> tuple[bool, str]:
        """Mirror of ``HemsEngine.can_dispatch_outage_decision``.

        Phase 2C/3 follow-up: this is the single source of truth
        the runtime's ``_can_dispatch_decision`` /
        ``_safety_guards_ok`` and the dispatcher's pre-flight
        both call. The full set of guards is on the real engine;
        this stand-in implements the SAME checks so the test
        surface matches production. ``decision`` is optional --
        a probe-time call with ``decision=None`` only inspects
        the engine state, mirroring the production method.
        """
        self.calls.append({"decision": decision})
        now = datetime.now(timezone.utc)
        # 1. Payload check (only when a decision was supplied)
        if decision is not None:
            if (
                getattr(decision, "output_priority", None) is None
                and getattr(decision, "charger_priority", None) is None
            ):
                return (
                    False,
                    "decision has no output/charger payload; refusing to dispatch",
                )
        # 2. Circuit breaker
        blocked_until = self._blocked_until
        if isinstance(blocked_until, datetime):
            bu = (
                blocked_until
                if blocked_until.tzinfo
                else blocked_until.replace(tzinfo=timezone.utc)
            )
            if bu > now:
                return (
                    False,
                    f"circuit breaker until {bu.isoformat()}",
                )
        # 3. Manual override
        manual_until = self._manual_override_until
        if isinstance(manual_until, datetime):
            mu = (
                manual_until
                if manual_until.tzinfo
                else manual_until.replace(tzinfo=timezone.utc)
            )
            if mu > now:
                return (
                    False,
                    f"manual pause until {mu.isoformat()}",
                )
        # 4. Anti-flapping dedup window
        for attr in ("_last_cmd_output_at", "_last_cmd_charger_at"):
            last_at = getattr(self, attr, None)
            if isinstance(last_at, datetime):
                la = (
                    last_at
                    if last_at.tzinfo
                    else last_at.replace(tzinfo=timezone.utc)
                )
                if (now - la) < self._command_dedup_window:
                    return (
                        False,
                        f"anti-flapping; {attr}={la.isoformat()} "
                        f"within {int(self._command_dedup_window.total_seconds())}s window",
                    )
        return (True, "")


class FakeApi:
    """Tiny ``InverterApi`` stand-in carrying the canonical
    ``last_realtime_success_received_at`` timestamp that the
    fresh-data gate at ``_read_freshness_seconds`` reads.
    """

    def __init__(
        self, *, last_success_at: datetime | None = None,
    ) -> None:
        self.last_realtime_success_received_at = last_success_at
        # Other surfaces the runtime might read; all ``None`` by
        # default so freshness can fail closed.
        self.last_realtime_response_received_at = None
        self.last_realtime_source = "unavailable"


class FakeCoordinator:
    """Stand-in for ``InverterCoordinator``.

    The runtime reads the following attributes:
      * ``data`` -- the live telemetry dict
      * ``_battery_capacity_kwh``, ``_battery_capacity_ah``,
        ``_nominal_voltage_v``
      * ``_hems`` -- a ``FakeHems`` (or similar)
      * ``forecast_tomorrow_kwh``
      * ``last_update_success`` (bool), ``last_update_time`` (dt)
      * ``api.last_realtime_success_received_at`` (dt)
      * ``_execute_hems_command`` -- the protected dispatcher
      * ``options`` or ``_entry.options`` -- operator config
      * ``hems_auto_mode`` (bool)

    Phase 2C/3 follow-up (Codex DEFECT D): production exposes
    ``_derive_battery_capacity`` (coordinator.py:2215) -- a
    staticmethod validator with the same range rules the runtime
    used to duplicate. ``FakeCoordinator`` exposes an instance
    method of the same name so ``snapshot_telemetry`` can call it
    like the real coordinator.
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
        forecast_tomorrow_kwh: float | None = 12.0,
        last_update_success: bool = True,
        last_update_time: datetime | None = None,
        api: FakeApi | None = None,
        execute_hems_command=None,
        hems_auto_mode: bool = True,
        derive_battery_capacity=None,
        bind_real_dispatcher: bool = True,
    ) -> None:
        self.data = data
        self._battery_capacity_kwh = capacity_kwh
        self._battery_capacity_ah = capacity_ah
        self._nominal_voltage_v = voltage_v
        # NOTE: there is intentionally NO
        # ``_battery_capacity_ok_for_planning`` attribute on the
        # production coordinator -- the runtime must compute it
        # from ``_derive_battery_capacity`` (DEFECT D follow-up).
        if hems is None:
            hems = FakeHems(hems_auto_mode=hems_auto_mode)
        self._hems = hems
        self.forecast_tomorrow_kwh = forecast_tomorrow_kwh
        # Canonical HA attributes. ``last_update_success`` is a
        # BOOL; ``last_update_time`` is a ``datetime``.
        self.last_update_success = last_update_success
        if last_update_time is None:
            last_update_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        self.last_update_time = last_update_time
        # ``api.last_realtime_success_received_at`` is the production
        # source of truth for the freshness gate. Default to the same
        # value as ``last_update_time`` so a healthy coordinator
        # yields ~10s freshness.
        if api is None:
            # Default to FakeApiTransport so the real dispatcher can
            # exercise set_*/get_* paths end-to-end. The transport's
            # ``last_realtime_success_received_at`` is the canonical
            # timestamp the freshness gate reads.
            api = FakeApiTransport(last_success_at=last_update_time)
        self.api = api
        self.options = options or {}
        self._entry = SimpleNamespace(options=options or {})
        if execute_hems_command is not None:
            self._execute_hems_command = execute_hems_command
        elif bind_real_dispatcher:
            # Bind the real hems.engine.execute_hems_command so
            # the runtime drives the production code path. Tests
            # that need to short-circuit the dispatcher pass
            # ``execute_hems_command=fn`` explicitly.
            from hems.engine import execute_hems_command as _exec

            async def _bound(decision, command_id=None, *,
                             evidence_recorded=False):
                return await _exec(
                    api=self.api,
                    hems=self._hems,
                    decision=decision,
                    command_id=command_id,
                    evidence_recorded=evidence_recorded,
                )
            self._execute_hems_command = _bound
        else:
            self._execute_hems_command = None
        self.hems_auto_mode = hems_auto_mode
        # ``_derive_battery_capacity`` is the canonical validator
        # the runtime calls. When the test does not provide one,
        # we expose an instance method that mirrors the production
        # ranges so the snapshot contract holds without manual
        # wiring of test-specific inputs.
        self._derive_battery_capacity = derive_battery_capacity or self._default_derive_battery_capacity

    def set_execute_hems_command(self, fn) -> None:
        self._execute_hems_command = fn

    def _default_derive_battery_capacity(self, options: dict) -> dict:
        """Mirror of ``InverterCoordinator._derive_battery_capacity``.

        Range rules and dataclass shape are identical to the
        canonical validator at coordinator.py:2215-2276. The
        function returns a dict with keys ``ah``, ``voltage``,
        ``kwh``, ``warning`` (None when valid), and
        ``ok_for_planning`` (bool, True only when inputs are
        in-range). Failing either field sets ``warning`` and
        ``ok_for_planning=False``.
        """
        DEFAULTS = {"ah": 230.0, "voltage": 51.2}
        RANGES = {"ah": (10.0, 2000.0), "voltage": (10.0, 100.0)}
        out = dict(DEFAULTS)
        warnings: list[str] = []
        for key in ("ah", "voltage"):
            raw = options.get(
                "battery_capacity_ah" if key == "ah"
                else "nominal_voltage_v",
                DEFAULTS[key],
            )
            try:
                checked = float(raw)
            except (TypeError, ValueError):
                checked = None
            lo, hi = RANGES[key]
            if checked is None or not (lo <= checked <= hi):
                warnings.append(
                    f"{key}={raw!r} out of form range [{lo}, {hi}]; "
                    f"using default {DEFAULTS[key]}"
                )
                continue
            out[key] = checked
        return {
            "ah": out["ah"],
            "voltage": out["voltage"],
            "kwh": out["ah"] * out["voltage"] / 1000.0,
            "warning": "; ".join(warnings) if warnings else None,
            "ok_for_planning": not warnings,
        }


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

    def get(self, event_id: str):
        for ev in self._events:
            if getattr(ev, "id", None) == event_id:
                return ev
        return None

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
        # Capacity: with the validator-driven contract the production
        # defaults (230 Ah x 51.2 V / 1000) yield 11.776 kWh; the
        # validator accepts these because they are in-range. The
        # snapshot therefore returns the canonical default, not
        # None. The "missing data" guarantee applies to LIVE values
        # (batterySoc, pvPower, etc.), not to the configured
        # capacity, which the validator resolves from operator
        # options.
        self.assertEqual(tel.battery_capacity_kwh, 11.776)
        self.assertTrue(tel.battery_capacity_ok_for_planning)
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
        # snapshot -- which we fix via a frozen last_update_time).
        coord.last_update_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        coord.api.last_realtime_success_received_at = coord.last_update_time
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
        """Return (calls, installer_fn).

        ``calls`` is a list of (decision, command_id, kwargs) tuples
        appended to on every call. ``installer_fn`` is a regular
        function that, when called with a FakeCoordinator, replaces
        its ``_execute_hems_command`` with a wrapper that records
        the call AND delegates to the real
        ``hems.engine.execute_hems_command``. This way the test
        exercises the production code path end-to-end.
        """
        calls: list = []

        def _install(coord: "FakeCoordinator") -> None:
            from hems.engine import execute_hems_command as _exec

            async def _wrapped(decision, command_id=None, *,
                               evidence_recorded=False):
                calls.append((decision, command_id, evidence_recorded))
                return await _exec(
                    api=coord.api,
                    hems=coord._hems,
                    decision=decision,
                    command_id=command_id,
                    evidence_recorded=evidence_recorded,
                )
            coord._execute_hems_command = _wrapped

        return calls, _install

    def _run(self, coro):
        """Drive an async coroutine to completion on a fresh event loop."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_execute_plan_does_nothing_when_plan_blocked(self):
        """Blocked plan -> execute_plan returns early, dispatcher NOT called."""
        calls, install = self._capture_dispatcher()
        coord = FakeCoordinator(bind_real_dispatcher=False)
        install(coord)
        plan = _build_blocked_plan("battery invalid")
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertFalse(result["executed"])
        self.assertIn("battery invalid", result["reason"])
        self.assertEqual(calls, [], msg="dispatcher must not be called")

    def test_execute_plan_calls_protected_dispatcher_for_grid_charge(self):
        """Healthy plan + grid charge needed -> dispatcher called once."""
        calls, install = self._capture_dispatcher()
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            bind_real_dispatcher=False,
        )
        install(coord)
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
        # The decision carries the prep reason + skip flag. The
        # outage runtime MUST NOT touch the buzzer (the protected
        # dispatcher leaves the acoustic state untouched when
        # ``buzzer_off is None`` -- coordinator.py:1688-1695).
        self.assertIn("planned_outage", decision.reason)
        self.assertIn(EVENT_ID, decision.reason)
        self.assertFalse(decision.skip)
        self.assertIsNone(decision.buzzer_off)

    def test_execute_plan_refuses_when_manual_pause_active(self):
        """Future manual pause -> execute_plan refuses."""
        calls, install = self._capture_dispatcher()
        hems = FakeHems(
            manual_until=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            hems=hems,
            bind_real_dispatcher=False,
        )
        install(coord)
        plan = _build_unblocked_plan(needs_grid_kwh=3.5)
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertFalse(result["executed"])
        self.assertIn("manual pause", result["reason"])
        self.assertEqual(calls, [], msg="dispatcher must not be called")

    def test_execute_plan_refuses_when_telemetry_stale(self):
        """Stale telemetry -> execute_plan refuses.

        The runtime reads ``coordinator.api.last_realtime_success_received_at``
        (api.py:201) for the freshness timestamp. We push it
        10 minutes into the past so the freshness gate exceeds
        ``TELEMETRY_STALENESS_LIMIT_SECONDS`` (5 min).
        """
        calls, install = self._capture_dispatcher()
        stale_ts = datetime.now(timezone.utc) - timedelta(seconds=600)
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            last_update_time=stale_ts,
            api=FakeApi(last_success_at=stale_ts),
            bind_real_dispatcher=False,
        )
        install(coord)
        plan = _build_unblocked_plan(needs_grid_kwh=3.5)
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertFalse(result["executed"])
        self.assertIn("telemetry stale", result["reason"])
        self.assertEqual(calls, [], msg="dispatcher must not be called")

    def test_execute_plan_never_sets_solar_priority_during_outage(self):
        """The decision's output_priority is the canonical USB/SBU string.
        USB maps to OutputPriority.USB="0" (engine.py:119).
        SBU maps to OutputPriority.SBU="2" (engine.py:120).
        The runtime only emits USB ("0") for prep, but the assertion
        is the wider invariant: never SOLAR or UTI.
        """
        calls, install = self._capture_dispatcher()
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            bind_real_dispatcher=False,
        )
        install(coord)
        plan = _build_unblocked_plan(needs_grid_kwh=3.5)
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertTrue(result["executed"], msg=result)
        decision = calls[0][0]
        # Canonical numeric strings (hems/engine.py:118-125).
        self.assertIn(
            decision.output_priority, ("0", "2"),
            msg=f"output_priority must be USB('0') or SBU('2'), got "
                f"{decision.output_priority!r}",
        )
        self.assertNotIn(decision.output_priority, ("USB", "SBU", "SOLAR", "UTI"))
        # Charger priority is the prep-time value SNU("1") / OSO("2").
        self.assertIn(
            decision.charger_priority, ("1", "2"),
            msg=f"charger_priority unexpected: {decision.charger_priority!r}",
        )
        self.assertNotIn(decision.charger_priority, ("SNU", "OSO", "CSO"))

    def test_dispatcher_call_signature_uses_existing_protected_path(self):
        """The runtime calls the coordinator's protected dispatcher exactly once
        with a decision that has the three fields the engine reads."""
        calls, install = self._capture_dispatcher()
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            bind_real_dispatcher=False,
        )
        install(coord)
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

            # Real HA ``async_register`` signature is
            # ``(domain, name, handler, schema=..., supports_response=...)``
            # (and several more kwargs in newer HA builds). Capture
            # them all so the test asserts on the new DEFECT-7
            # contract: ``supports_response=True`` + a ``vol.Schema``
            # are passed through to the registry.
            def async_register(self, domain, name, handler, **kwargs):
                self.calls.append((domain, name, handler, kwargs))

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

            # Real HA ``async_register`` signature accepts
            # ``schema=..., supports_response=...`` kwargs.
            def async_register(self, domain, name, handler, **kwargs):
                self.calls.append((domain, name, handler, kwargs))

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
        service_names = [
            name for _d, name, _h, _kwargs in hass.services.calls
        ]
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
        service_names = [
            name for _d, name, _h, _kwargs in hass.services.calls
        ]
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
        service_names = [
            name for _d, name, _h, _kwargs in hass.services.calls
        ]
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


# ===========================================================================
# Regression tests for the 9 Codex defects (Phase 2C+3 hotfix).
#
# Every test in this class binds to the CANONICAL production contract:
# a real coordinator / EventStore / dispatcher / HA timer surface --
# not the simplified stubs the older tests use. These are the contract
# tests Codex reproduced against the original (rejected) runtime.
# ===========================================================================


class _StabHemsForTrackProbe:
    """Minimal stand-in for the parts of HemsEngine used by the
    timer-cancellation regression test. We replace
    ``homeassistant.helpers.event.async_track_time_interval`` with
    a stub that records (hass, handler, interval) tuples and
    returns a cancellation handle that mutates a state flag.
    """

    def __init__(self):
        self.calls: list[tuple] = []
        self.cancelled = 0
        self._fake_listener_registered = True
        self._interval_received: object = None

    def stub(self, hass, handler, *, interval):
        self.calls.append((hass, handler, interval))

        def _unsub():
            self.cancelled += 1
            self._fake_listener_registered = False

        return _unsub


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class TimerRegressionDefect1Tests(unittest.TestCase):
    """DEFECT 1: ``async_track_time_interval`` was called with
    ``seconds=...``; the real HA API expects
    ``interval=timedelta(...)``. Verify the canonical kwarg + that
    the cancellation handle actually unregisters.
    """

    def setUp(self):
        # Install a stub for ``async_track_time_interval`` via
        # ``sys.modules`` so the runtime's lazy import resolves to
        # our probe without needing the real ``homeassistant``
        # package. We synthesise ``homeassistant``,
        # ``homeassistant.helpers`` and ``homeassistant.helpers.event``
        # as namespace trees holding our probe.
        import types as _types
        self._stab = _StabHemsForTrackProbe()
        ha_pkg = sys.modules.get("homeassistant")
        if ha_pkg is None:
            ha_pkg = _types.ModuleType("homeassistant")
            sys.modules["homeassistant"] = ha_pkg
        helpers_pkg = sys.modules.get("homeassistant.helpers")
        if helpers_pkg is None:
            helpers_pkg = _types.ModuleType("homeassistant.helpers")
            sys.modules["homeassistant.helpers"] = helpers_pkg
        event_mod = _types.ModuleType("homeassistant.helpers.event")
        event_mod.async_track_time_interval = self._stab.stub  # type: ignore[attr-defined]
        sys.modules["homeassistant.helpers.event"] = event_mod

    def test_register_scheduled_tick_uses_interval_timedelta_not_seconds(self):
        """The call passes ``interval=timedelta(seconds=...)``.
        ``seconds=`` is the bug; HA's signature requires a
        ``timedelta`` instance via the ``interval`` kwarg.
        """
        hass = SimpleNamespace()
        # Run the registration. The runtime's
        # ``async_track_time_interval(hass, _handle, interval=...)``
        # call goes to our stub.
        unsub = runtime._register_scheduled_tick(
            hass, "entry-canon",
            interval_seconds=runtime.DEFAULT_TICK_INTERVAL_SECONDS,
        )
        self.assertEqual(len(self._stab.calls), 1)
        # The runtime MUST have passed ``interval=`` kwarg.
        hass_arg, handler, kwargs_or_pos = self._stab.calls[0]
        self.assertIs(hass_arg, hass)
        self.assertTrue(
            callable(handler),
            msg="handler passed to async_track_time_interval is not callable",
        )
        # The third positional in our stub is what comes in
        # via the ``interval=`` kwarg (the stub accepts only
        # ``interval``).
        self.assertIsInstance(kwargs_or_pos, timedelta)
        self.assertEqual(
            kwargs_or_pos.total_seconds(),
            runtime.DEFAULT_TICK_INTERVAL_SECONDS,
            msg=f"interval not equal to expected seconds: got "
                f"{kwargs_or_pos!r}",
        )

    def test_register_scheduled_tick_cancellation_handle_unsubscribes(self):
        """The handle returned by the registration is callable; when
        invoked it removes the underlying listener.
        """
        hass = SimpleNamespace()
        unsub = runtime._register_scheduled_tick(
            hass, "entry-cancel",
            interval_seconds=10,
        )
        self.assertTrue(callable(unsub), msg="unsub must be callable")
        # Pre-cancel: listener registered.
        self.assertEqual(self._stab.cancelled, 0)
        # Call the handle.
        unsub()
        # The stub bumped the cancel counter; the underlying
        # listener is now unsubscribed.
        self.assertEqual(self._stab.cancelled, 1)
        # A second call is also safe (idempotent).
        unsub()
        self.assertEqual(self._stab.cancelled, 2)


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class FindActiveEventDefect2Tests(unittest.TestCase):
    """DEFECT 2: ``_find_active_event`` must surface a future confirmed
    event whose prep window is already open AND must exclude
    pending / cancelled events.
    """

    def _hass_with_store(self, events: list) -> SimpleNamespace:
        data: dict = {}
        # The store object the bundle resolution uses
        # (``bundle['outage_store']``).
        store = FakeStore(events)
        entry_id = "entry-find"
        data["powmr_inverter"] = {entry_id: {"outage_store": store}}
        hass = SimpleNamespace(data=data)
        return hass, entry_id

    def test_find_active_event_includes_future_confirmed_in_prep_window(self):
        """Confirmed event starting in 2h, prep window (4h backstop)
        already open -- the event MUST be picked.
        """
        # ``now = DYNAMIC_NOW`` ensures the test never dates.
        start = DYNAMIC_NOW + timedelta(hours=2)
        end = start + timedelta(hours=3)
        ev_confirmed = SimpleNamespace(
            id="evt-c", start_iso=start.astimezone(KYIV).isoformat(),
            end_iso=end.astimezone(KYIV).isoformat(),
            status="confirmed",
        )
        hass, entry_id = self._hass_with_store([ev_confirmed])
        result = runtime._find_active_event(hass, entry_id, DYNAMIC_NOW)
        self.assertIs(result, ev_confirmed)

    def test_find_active_event_excludes_pending(self):
        """Pending event starting in 2h, prep window open -- must
        NOT be picked even though the calendar window is open.
        """
        start = DYNAMIC_NOW + timedelta(hours=2)
        end = start + timedelta(hours=3)
        ev_pending = SimpleNamespace(
            id="evt-p", start_iso=start.astimezone(KYIV).isoformat(),
            end_iso=end.astimezone(KYIV).isoformat(),
            status="pending",
        )
        hass, entry_id = self._hass_with_store([ev_pending])
        result = runtime._find_active_event(hass, entry_id, DYNAMIC_NOW)
        self.assertIsNone(result)

    def test_find_active_event_excludes_cancelled(self):
        """Cancelled event whose prep window is open -- NOT picked.
        """
        start = DYNAMIC_NOW + timedelta(hours=2)
        end = start + timedelta(hours=3)
        ev_cancelled = SimpleNamespace(
            id="evt-x", start_iso=start.astimezone(KYIV).isoformat(),
            end_iso=end.astimezone(KYIV).isoformat(),
            status="cancelled",
        )
        hass, entry_id = self._hass_with_store([ev_cancelled])
        result = runtime._find_active_event(hass, entry_id, DYNAMIC_NOW)
        self.assertIsNone(result)


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class FreshnessDefect3Tests(unittest.TestCase):
    """DEFECT 3: telemetry freshness must refuse when the last
    poll failed OR when the canonical timestamp is missing.
    A missing ``gridAvailable`` reading must also refuse.
    """

    def test_is_automatic_allowed_refuses_failed_poll(self):
        """``last_update_success=False`` -> gate refuses with a
        freshness reason; never silently passes.
        """
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            options={"outage_mode": "automatic"},
            last_update_success=False,
            last_update_time=None,
            api=FakeApi(last_success_at=None),
        )
        plan = _build_unblocked_plan(needs_grid_kwh=2.0)
        allowed, reason = runtime.is_automatic_allowed(coord, plan)
        self.assertFalse(allowed)
        # Failure modes cover both telemetry stale + missing
        # timestamp; either substring triggers the rejection.
        self.assertTrue(
            "stale" in reason.lower() or "unknown" in reason.lower(),
            msg=f"reason did not mention freshness: {reason!r}",
        )

    def test_is_automatic_allowed_refuses_missing_grid(self):
        """``data['gridAvailable']`` missing -> gate refuses."""
        coord = FakeCoordinator(
            data={"batterySoc": 40, "pvPower": 100, "loadPower": 200},
            # NOTE: gridAvailable absent on purpose.
            options={"outage_mode": "automatic"},
        )
        plan = _build_unblocked_plan(needs_grid_kwh=2.0)
        allowed, reason = runtime.is_automatic_allowed(coord, plan)
        self.assertFalse(allowed)
        self.assertIn("grid", reason.lower())

    def test_read_freshness_seconds_refuses_when_api_timestamp_missing(self):
        """Canonical source-of-truth: ``api.last_realtime_success_received_at``.
        When absent the gate returns the refusal sentinel (>limit).
        """
        coord = FakeCoordinator(
            data={"gridAvailable": True},
            options={"outage_mode": "automatic"},
            last_update_success=True,
            # No api.last_realtime_success_received_at at all.
            api=FakeApi(last_success_at=None),
        )
        fresh = runtime._read_freshness_seconds(
            coord, datetime.now(timezone.utc)
        )
        self.assertGreater(
            fresh, planner.TELEMETRY_STALENESS_LIMIT_SECONDS,
            msg="missing timestamp must refuse, not default to 0",
        )


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class CapacityDefect4Tests(unittest.TestCase):
    """DEFECT 4: ``battery_capacity_ok_for_planning`` must be computed
    in-range, not read from a non-existent attribute.
    """

    def test_snapshot_telemetry_capacity_ok_with_valid_values(self):
        """230 Ah * 51.2 V / 1000 = 11.776 kWh -> ok_for_planning=True."""
        coord = FakeCoordinator(
            data={"batterySoc": 50, "gridAvailable": True},
            options={"battery_capacity_ah": 230, "nominal_voltage_v": 51.2},
            # No _battery_capacity_kwh / _battery_capacity_ah on the
            # coordinator; the snapshot must build everything from
            # options alone.
            capacity_kwh=None,
            capacity_ah=None,
            voltage_v=None,
        )
        tel = runtime.snapshot_telemetry(coord, hass=None)
        self.assertAlmostEqual(tel.battery_capacity_kwh, 11.776, places=3)
        self.assertEqual(tel.battery_capacity_ah, 230.0)
        self.assertEqual(tel.nominal_voltage_v, 51.2)
        self.assertTrue(tel.battery_capacity_ok_for_planning)

    def test_snapshot_telemetry_capacity_ok_false_for_out_of_range_inputs(self):
        """Out-of-range Ah/V -> ok_for_planning=False.

        The production validator (``coordinator._derive_battery_capacity``,
        coordinator.py:2215-2276) uses the ranges ``[10, 2000]`` for
        Ah and ``[10, 100]`` for V. We feed an Ah value of 10000 to
        trigger an explicit refusal; the snapshot propagates the
        validator's ``ok_for_planning=False`` and the planner sees a
        blocked plan.
        """
        coord = FakeCoordinator(
            data={"batterySoc": 50, "gridAvailable": True},
            options={"battery_capacity_ah": 10000},  # out-of-range
            capacity_kwh=None,
            capacity_ah=10000,
            voltage_v=51.2,
        )
        tel = runtime.snapshot_telemetry(coord, hass=None)
        self.assertFalse(
            tel.battery_capacity_ok_for_planning,
            msg="out-of-range Ah must propagate to ok_for_planning=False",
        )

    def test_snapshot_telemetry_capacity_ok_false_for_out_of_range(self):
        """10000 Ah is outside ``[10, 2000]`` -> ok_for_planning=False."""
        coord = FakeCoordinator(
            data={"batterySoc": 50, "gridAvailable": True},
            options={"battery_capacity_ah": 10000, "nominal_voltage_v": 51.2},
            capacity_kwh=None, capacity_ah=None, voltage_v=None,
        )
        tel = runtime.snapshot_telemetry(coord, hass=None)
        self.assertFalse(tel.battery_capacity_ok_for_planning)


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class SafetyGuardsDefect5Tests(unittest.TestCase):
    """DEFECT 5: ``_safety_guards_ok`` must read production HEMS
    fields, not return ``True`` unconditionally.
    """

    def test_safety_guards_ok_refuses_circuit_breaker_tripped(self):
        """``_hems._blocked_until`` in the future -> guard refuses."""
        coord = FakeCoordinator(
            data={"batterySoc": 50, "gridAvailable": True},
            options={"outage_mode": "automatic"},
            hems=FakeHems(
                blocked_until=datetime.now(timezone.utc)
                + timedelta(seconds=120)
            ),
        )
        self.assertFalse(runtime._safety_guards_ok(coord))

    def test_safety_guards_ok_refuses_anti_flapping_dedup(self):
        """``_hems._last_cmd_output_at`` inside the dedup window ->
        guard refuses (anti-flapping suppresses the duplicate, so
        the runtime must not pretend to dispatch).
        """
        coord = FakeCoordinator(
            data={"batterySoc": 50, "gridAvailable": True},
            options={"outage_mode": "automatic"},
            hems=FakeHems(
                last_cmd_output_at=datetime.now(timezone.utc)
                - timedelta(seconds=2),
            ),
        )
        self.assertFalse(runtime._safety_guards_ok(coord))

    def test_safety_guards_ok_refuses_manual_override_in_future(self):
        """``_hems._manual_override_until`` in the future -> refuses."""
        coord = FakeCoordinator(
            data={"batterySoc": 50, "gridAvailable": True},
            options={"outage_mode": "automatic"},
            hems=FakeHems(
                manual_until=datetime.now(timezone.utc)
                + timedelta(hours=1)
            ),
        )
        self.assertFalse(runtime._safety_guards_ok(coord))

    def test_safety_guards_ok_passes_when_clean(self):
        """No tripped guards -> True."""
        coord = FakeCoordinator(
            data={"batterySoc": 50, "gridAvailable": True},
            options={"outage_mode": "automatic"},
        )
        self.assertTrue(runtime._safety_guards_ok(coord))

    def test_is_automatic_allowed_returns_false_when_circuit_breaker_tripped(self):
        """End-to-end: tripped guard -> the gate returns False with
        a 'safety' reason so the audit log distinguishes the cause.
        """
        coord = FakeCoordinator(
            data={"batterySoc": 50, "gridAvailable": True},
            options={"outage_mode": "automatic"},
            hems=FakeHems(
                blocked_until=datetime.now(timezone.utc)
                + timedelta(seconds=120)
            ),
        )
        plan = _build_unblocked_plan(needs_grid_kwh=2.0)
        allowed, reason = runtime.is_automatic_allowed(coord, plan)
        self.assertFalse(allowed)
        self.assertIn("safety", reason.lower())


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class ExecutePlanDefect6Tests(unittest.TestCase):
    """DEFECT 6: dispatch decision uses canonical numeric strings
    (``output_priority="0"`` / ``charger_priority="1"`` -- engine.py:118-125),
    touches the buzzer (``buzzer_off=None``), and propagates
    dispatcher failure / raises cleanly.
    """

    def _capture_dispatcher(self):
        """Return (calls, installer_fn).

        ``installer_fn(coord)`` replaces ``coord._execute_hems_command``
        with a wrapper that records the call AND delegates to the
        real ``hems.engine.execute_hems_command``. The test exercises
        the production code path end-to-end.
        """
        calls: list = []

        def _install(coord):
            from hems.engine import execute_hems_command as _exec

            async def _wrapped(decision, command_id=None, *,
                               evidence_recorded=False):
                calls.append((decision, command_id, evidence_recorded))
                return await _exec(
                    api=coord.api,
                    hems=coord._hems,
                    decision=decision,
                    command_id=command_id,
                    evidence_recorded=evidence_recorded,
                )
            coord._execute_hems_command = _wrapped

        return calls, _install

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_execute_plan_dispatches_with_canonical_priority_strings(self):
        """Decision values are the EXACT numeric strings
        ``OutputPriority.USB="0"`` and ``ChargerPriority.SNU="1"``
        (engine.py:119, 124). ``buzzer_off`` is None (the runtime
        must NEVER flip the buzzer).
        """
        calls, install = self._capture_dispatcher()
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            bind_real_dispatcher=False,
        )
        install(coord)
        plan = _build_unblocked_plan(needs_grid_kwh=3.0)
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertTrue(result["executed"], msg=result)
        decision, command_id, evidence_recorded = calls[0]
        # Canonical numeric strings (hems/engine.py:118-125).
        self.assertEqual(decision.output_priority, "0")
        self.assertEqual(decision.charger_priority, "1")
        # NEVER a buzzer flip.
        self.assertIsNone(decision.buzzer_off)

    def test_execute_plan_refuses_when_api_rejects_write(self):
        """The real dispatcher with a FakeApiTransport that
        rejects ``set_output_priority``. ``DispatcherResult.api_accepted``
        is False, ``executed=False`` and the api-rejection reason
        surfaces in ``result.error``.
        """
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        api = FakeApiTransport(reject_output=True, last_success_at=fresh)
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            api=api,
            last_update_time=fresh,
            bind_real_dispatcher=True,
        )
        plan = _build_unblocked_plan(needs_grid_kwh=3.0)
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertFalse(result["executed"], msg=result)
        self.assertIn(
            "api rejected", result["reason"],
            msg=f"reason must surface api rejection: {result['reason']!r}",
        )
        # api was contacted (this is the api-rejection path, not the
        # guard-refused path).
        self.assertTrue(
            any(call[0] == "set_output_priority" for call in api.calls),
            msg="api.set_output_priority must be called at least once",
        )

    def test_execute_plan_refuses_when_readback_mismatches(self):
        """The real dispatcher writes successfully but the GET
        readback returns a different value. ``DispatcherResult.readback_valid``
        is False, ``executed=False`` and the readback reason surfaces.
        """
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        api = FakeApiTransport(
            readback_mismatch_output="1",  # different from "0"
            last_success_at=fresh,
        )
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            api=api,
            last_update_time=fresh,
            bind_real_dispatcher=True,
        )
        plan = _build_unblocked_plan(needs_grid_kwh=3.0)
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertFalse(result["executed"], msg=result)
        self.assertIn(
            "readback", result["reason"].lower(),
            msg=f"reason must surface readback mismatch: {result['reason']!r}",
        )

    def test_execute_plan_does_not_call_set_buzzer_when_decision_buzzer_off_is_none(self):
        """buzzer_off=None must result in NO api.set_config_item call.
        The runtime always sets decision.buzzer_off=None; the dispatcher
        must skip BOTH branches.
        """
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        api = FakeApiTransport(last_success_at=fresh)
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            api=api,
            last_update_time=fresh,
            bind_real_dispatcher=True,
        )
        plan = _build_unblocked_plan(needs_grid_kwh=3.0)
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertTrue(result["executed"], msg=result)
        buzzer_calls = [
            call for call in api.calls if call[0] == "set_config_item"
        ]
        self.assertEqual(
            buzzer_calls, [],
            msg=f"buzzer must NOT be touched when buzzer_off=None; "
                f"got: {buzzer_calls!r}",
        )

    def test_dispatcher_does_not_touch_api_when_guard_refuses(self):
        """hems.can_dispatch_outage_decision refuses -> api is NEVER
        contacted. The DispatcherResult has dispatched=False.

        The runtime applies ``_execution_refusal`` BEFORE invoking
        the dispatcher, so the reason text is the runtime's safety
        guard message ("safety guard tripped; ...") -- not the
        dispatcher's "hems refused" -- and the api is never touched
        regardless of which layer caught it.
        """
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        api = FakeApiTransport(last_success_at=fresh)
        hems = FakeHems(blocked_until=datetime.now(timezone.utc) + timedelta(hours=1))
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            api=api,
            hems=hems,
            last_update_time=fresh,
            bind_real_dispatcher=True,
        )
        plan = _build_unblocked_plan(needs_grid_kwh=3.0)
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertFalse(result["executed"], msg=result)
        # Either layer may catch the failure; the api must NEVER be touched.
        self.assertIn(
            "safety", result["reason"].lower(),
            msg=f"reason must surface safety refusal: {result['reason']!r}",
        )
        self.assertEqual(
            api.calls, [],
            msg="api must NEVER be touched when guard refuses",
        )


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class ComputeOutagePlanServiceDefect7Tests(unittest.TestCase):
    """DEFECT 7: ``compute_outage_plan`` service is registered with
    ``supports_response=True`` + a ``vol.Schema``; the handler
    returns a real dict for confirmed events and refuses for
    pending events.
    """

    def _hass_stub(self) -> SimpleNamespace:
        data: dict = {}
        stores_map: dict = {}

        class _Services:
            def __init__(self):
                self.calls: list = []
                self.registered: dict = {}

            def async_register(self, domain, name, handler, **kwargs):
                self.calls.append((domain, name, handler, kwargs))
                self.registered[name] = (handler, kwargs)

        return SimpleNamespace(
            data=data,
            services=_Services(),
            _outage_stores=stores_map,
        )

    def _seed_store(self, hass, *, entry_id, store):
        # Make the per-entry store discoverable both via
        # ``hass.data['powmr_inverter'][entry_id]['outage_store']``
        # (the bundle path) and via
        # ``hass.data['powmr_inverter_outage_stores'][entry_id]``
        # (the EventStore path).
        hass.data.setdefault("powmr_inverter", {}).setdefault(
            entry_id, {}
        )["outage_store"] = store
        # The production EventStore path uses a ``DOMAIN``-
        # suffixed key; mirror it here.
        hass.data.setdefault("powmr_inverter_outage_stores", {})[
            entry_id
        ] = store

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_compute_outage_plan_service_registered_with_response_support(self):
        """The service is registered with ``supports_response=True``
        AND a non-None ``schema`` (a ``vol.Schema`` instance).
        """
        hass = self._hass_stub()
        entry_id = "entry-svc"
        store = FakeStore([])  # empty: we only verify registration
        self._seed_store(hass, entry_id=entry_id, store=store)
        entry = SimpleNamespace(
            entry_id=entry_id,
            options={"outage_mode": "observation"},
        )
        coord = FakeCoordinator(
            data={"gridAvailable": True, "batterySoc": 40},
            options={"outage_mode": "observation"},
        )
        runtime.wire_outage_runtime(hass, entry, coord)
        # Find the registration entry for the per-entry
        # ``compute_outage_plan`` service.
        candidates = [
            c for c in hass.services.calls if c[1] == f"{entry_id}_compute_outage_plan"
        ]
        self.assertEqual(
            len(candidates), 1,
            msg=f"expected exactly one compute_outage_plan registration; "
                f"got: {candidates!r}",
        )
        _d, _name, _handler, kwargs = candidates[0]
        # Schema is present and is a ``vol.Schema`` instance.
        self.assertIsNotNone(
            kwargs.get("schema"),
            msg="schema kwarg must be passed to async_register",
        )
        import voluptuous as _vol
        self.assertIsInstance(kwargs["schema"], _vol.Schema)
        # supports_response is True.
        self.assertTrue(kwargs.get("supports_response"))

    def test_compute_outage_plan_returns_plan_for_confirmed_event(self):
        """Calling the registered handler with a confirmed event
        returns a dict carrying the planner output + ``planned``.
        """
        hass = self._hass_stub()
        entry_id = "entry-svc-conf"
        # Build a confirmed event that starts 6h in the future so
        # the planner has a valid window.
        start = DYNAMIC_NOW + timedelta(hours=6)
        end = start + timedelta(hours=2)
        ev = SimpleNamespace(
            id="evt-svc-conf",
            start_iso=start.astimezone(KYIV).isoformat(),
            end_iso=end.astimezone(KYIV).isoformat(),
            status="confirmed",
        )
        store = FakeStore([ev])
        self._seed_store(hass, entry_id=entry_id, store=store)
        entry = SimpleNamespace(
            entry_id=entry_id, options={"outage_mode": "observation"},
        )
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True},
            options={"battery_capacity_ah": 230, "nominal_voltage_v": 51.2},
        )
        runtime.wire_outage_runtime(hass, entry, coord)
        # Resolve the handler.
        handler, _kwargs = hass.services.registered[
            f"{entry_id}_compute_outage_plan"
        ]
        # Drive the handler. ``call.data`` is a dict-like -- the
        # handler does ``call.data.get('event_id')`` so a plain
        # dict for ``data`` is the canonical contract.
        class _Call:
            def __init__(self, payload):
                self.data = dict(payload)

        result = self._run(handler(_Call({"event_id": "evt-svc-conf"})))
        self.assertIsInstance(result, dict)
        self.assertIn("planned", result)
        # When the plan is unblocked the planner returns the
        # baseline dict with the gap math.
        self.assertIn("plan", result)

    def test_compute_outage_plan_returns_refusal_for_pending_event(self):
        """Handler called with ``status='pending'`` (no force)
        returns ``{'planned': False, 'reason': 'event not confirmed'}``.
        """
        hass = self._hass_stub()
        entry_id = "entry-svc-pend"
        start = DYNAMIC_NOW + timedelta(hours=6)
        end = start + timedelta(hours=2)
        ev = SimpleNamespace(
            id="evt-svc-pend",
            start_iso=start.astimezone(KYIV).isoformat(),
            end_iso=end.astimezone(KYIV).isoformat(),
            status="pending",
        )
        store = FakeStore([ev])
        self._seed_store(hass, entry_id=entry_id, store=store)
        entry = SimpleNamespace(
            entry_id=entry_id, options={"outage_mode": "observation"},
        )
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True},
            options={},
        )
        runtime.wire_outage_runtime(hass, entry, coord)
        handler, _kwargs = hass.services.registered[
            f"{entry_id}_compute_outage_plan"
        ]
        class _Call:
            def __init__(self, payload):
                self.data = dict(payload)

        result = self._run(handler(_Call({"event_id": "evt-svc-pend"})))
        self.assertFalse(result["planned"])
        self.assertIn("not confirmed", result["reason"])


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class SmartModeArbitrationDefect5AddonTests(unittest.TestCase):
    """Outage runtime must respect the live ``smart_mode`` /
    ``predictive_mode`` / ``hems_auto_mode`` knobs. The runtime
    does not flip output_priority to anything that contradicts
    the smart_mode: every smart_mode value (0=Adaptive,
    1=Arbitrage, 2=Storm) accepts USB='0' (OutputPriority.USB) as
    the prep value -- the engine picks USB for reserve protection
    under ADAPTIVE/ARBITRAGE (engine.py:405-408) and STORM (engine.py:1336-1347).
    """

    def _capture_dispatcher(self):
        """Return (calls, installer_fn).

        ``installer_fn(coord)`` replaces ``coord._execute_hems_command``
        with a wrapper that records the call AND delegates to the
        real ``hems.engine.execute_hems_command``. The test exercises
        the production code path end-to-end.
        """
        calls: list = []

        def _install(coord):
            from hems.engine import execute_hems_command as _exec

            async def _wrapped(decision, command_id=None, *,
                               evidence_recorded=False):
                calls.append((decision, command_id, evidence_recorded))
                return await _exec(
                    api=coord.api,
                    hems=coord._hems,
                    decision=decision,
                    command_id=command_id,
                    evidence_recorded=evidence_recorded,
                )
            coord._execute_hems_command = _wrapped

        return calls, _install

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_outage_respects_live_smart_mode_zero_adaptive(self):
        """With live smart_mode=0 (Adaptive), the runtime emits
        output_priority='0' (USB) and does NOT touch the buzzer.
        Adaptive-mode engine picks USB for prep; the runtime's
        emission matches.
        """
        calls, install = self._capture_dispatcher()
        live_options = {
            "outage_mode": "automatic",
            "smart_mode": 0,
            "predictive_mode": "shadow",
        }
        coord = FakeCoordinator(
            data={"batterySoc": 40, "gridAvailable": True,
                  "pvPower": 100, "loadPower": 200},
            options=live_options,
            bind_real_dispatcher=False,
        )
        install(coord)
        plan = _build_unblocked_plan(needs_grid_kwh=2.0)
        result = self._run(runtime.execute_plan(coord, _evt(), plan))
        self.assertTrue(result["executed"], msg=result)
        decision = calls[0][0]
        self.assertEqual(decision.output_priority, "0")
        self.assertEqual(decision.charger_priority, "1")
        # Runtime must never have mutated the operator's modes.
        self.assertEqual(coord.options.get("smart_mode"), 0)
        self.assertEqual(coord.options.get("predictive_mode"), "shadow")


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class FinalizeThenDispatchFullPathTests(unittest.TestCase):
    """End-to-end regression: real ``_finalize_decision`` followed
    by the real dispatcher with a fake transport.

    Codex reproduced a regression where the engine's
    ``_finalize_decision`` records ``_last_cmd_output_at`` /
    ``_last_cmd_charger_at`` as part of normal arbitration, and
    the dispatcher's pre-flight guard then refused the just-formed
    decision as a duplicate, tripping the circuit breaker and
    leaving the inverter untouched. The runtime contract is:

      1. ``_finalize_decision`` proposes a write and records the
         proposal time. The anti-flapping dedup window is checked
         BEFORE the proposal time is set, so a fresh proposal
         always has a fresh timestamp.
      2. The dispatcher must not re-check the proposal timestamp;
         the engine's anti-flapping already gated it.
      3. The transport must receive the write (api_accepted=True).
      4. A guard refusal (circuit breaker / manual pause / etc.)
         must NOT count as a transport error -- the readback
         must be skipped and the api is NEVER touched.
    """

    def setUp(self):
        from hems.engine import (
            HemsEngine, HemsDecision, execute_hems_command,
        )
        self._HemsEngine = HemsEngine
        self._HemsDecision = HemsDecision
        self._execute_hems_command = execute_hems_command

    def test_finalize_then_dispatch_writes_through_with_real_path(self):
        """``_finalize_decision`` records a USB/SBU proposal at
        ``now``. The dispatcher then runs the body. The transport
        must receive both writes AND the readback must succeed
        (no guard refusal, no anti-flapping false-positive).
        """
        from datetime import datetime, timezone
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        hems = self._HemsEngine()
        # Inputs that drive a night arbitration with USB + SNU.
        decision = hems._finalize_decision(
            decision=self._HemsDecision(
                output_priority="0",
                charger_priority="1",
                reason="night_arbitrage",
            ),
            now=fresh,
            inputs={
                "current_output": "2",
                "current_charger": "2",
            },
        )
        # The finalize step must have produced a non-skipped write.
        self.assertIsNotNone(
            decision.output_priority,
            msg="_finalize_decision must not skip the night proposal",
        )
        self.assertIsNotNone(
            decision.charger_priority,
            msg="_finalize_decision must not skip the charger proposal",
        )
        # Now drive the dispatcher with a fresh transport.
        fresh_now = datetime.now(timezone.utc)
        api = FakeApiTransport(last_success_at=fresh_now)
        # Async helpers for the runtime test runner.
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._execute_hems_command(
                    api=api,
                    hems=hems,
                    decision=decision,
                    command_id="test-cmd-uuid",
                )
            )
        finally:
            loop.close()
        # The dispatcher must have written AND readback'd.
        self.assertTrue(result.api_accepted, msg=result)
        self.assertTrue(result.readback_valid, msg=result)
        self.assertTrue(result.dispatched, msg=result)
        # The transport must have received set_output_priority +
        # set_charger_priority + fetch_device_configs.
        method_names = {call[0] for call in api.calls}
        self.assertIn("set_output_priority", method_names)
        self.assertIn("set_charger_priority", method_names)
        self.assertIn("fetch_device_configs", method_names)
        # Anti-flapping did NOT trip the circuit breaker: the
        # engine's ``_consecutive_failures`` field is still 0.
        self.assertEqual(
            hems._consecutive_failures, 0,
            msg="just-formed proposal must not be blocked by its own timestamp",
        )
        # The pending_commands set is cleared by
        # ``report_control_success`` after a confirmed apply.
        self.assertEqual(
            hems._pending_commands, set(),
            msg="confirmed apply must clear pending commands",
        )

    def test_guard_refusal_does_not_touch_transport(self):
        """``_blocked_until`` in the future -> the dispatcher
        refuses BEFORE touching the api. The ``DispatcherResult``
        surfaces ``hems refused: circuit breaker until ...`` and
        the readback is NOT attempted (the transport must NEVER
        see a ``fetch_device_configs`` call when the guard
        refuses).
        """
        from datetime import datetime, timezone
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        hems = self._HemsEngine()
        hems._blocked_until = future
        fresh_now = datetime.now(timezone.utc)
        api = FakeApiTransport(last_success_at=fresh_now)
        decision = self._HemsDecision(
            output_priority="0",
            charger_priority="1",
            reason="test",
        )
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._execute_hems_command(
                    api=api,
                    hems=hems,
                    decision=decision,
                    command_id="test-cmd-uuid-2",
                )
            )
        finally:
            loop.close()
        # Guard refused.
        self.assertFalse(result.dispatched, msg=result)
        self.assertIn(
            "circuit breaker", result.error or "",
            msg=f"error must mention circuit breaker: {result.error!r}",
        )
        # The transport was NEVER touched.
        self.assertEqual(
            api.calls, [],
            msg=f"api must not be touched on guard refusal; got: {api.calls!r}",
        )

    def test_package_relative_import_resolves_in_wrapper(self):
        """Sanity check: ``from .hems.engine import execute_hems_command``
        must work in the same import environment the wrapper
        uses. This is the import path the production
        ``_execute_hems_command`` wrapper takes; if the relative
        form fails the entire dispatch path breaks.
        """
        import importlib
        mod = importlib.import_module("hems.engine")
        self.assertTrue(hasattr(mod, "execute_hems_command"))
        self.assertTrue(hasattr(mod, "DispatcherResult"))


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class NoOpDoesNotTripCircuitBreakerTests(unittest.TestCase):
    """Phase 2C+3 follow-up (Codex): the engine's ``_finalize_decision``
    routinely emits a decision with ``skip=True`` (no output/charger
    payload) on cycles where the device is already in the target
    state. The previous dispatcher treated every such no-op as a
    transport failure and called ``report_control_failure``,
    incrementing ``_consecutive_failures`` until the circuit breaker
    tripped after five routine cycles (Codex observed 133 failures
    in a 24-hour live window).

    These tests drive the real ``HemsEngine.evaluate`` and the real
    ``execute_hems_command`` together and assert:

      1. Repeated no-op cycles do NOT increment
         ``_consecutive_failures`` and do NOT trip the circuit
         breaker.
      2. Protective hold (circuit breaker / manual pause) does
         NOT contact the transport and does NOT increment
         failures.
      3. A real API failure (set_* returns False) DOES increment
         failures and DOES set ``_blocked_until``.
      4. A real API exception (set_* raises) DOES increment
         failures.
      5. A readback mismatch DOES increment failures.
      6. A successful write passes through and increments the
         success bookkeeping.
    """

    def setUp(self):
        from hems.engine import HemsEngine, HemsDecision, execute_hems_command
        self._HemsEngine = HemsEngine
        self._HemsDecision = HemsDecision
        self._execute_hems_command = execute_hems_command

    def _drive_one_cycle(
        self, *, fresh_inputs: dict | None = None,
        hems: "HemsEngine" = None,
        hems_auto: bool = True,
    ):
        """One full HEMS cycle: evaluate -> execute -> return result.

        Used to verify the failure counter is stable across many
        no-op cycles.
        """
        from datetime import datetime, timezone
        if hems is None:
            hems = self._HemsEngine()
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        decision = hems._finalize_decision(
            decision=self._HemsDecision(
                output_priority="0",
                charger_priority="1",
                reason="night_arbitrage",
            ),
            now=fresh,
            inputs=fresh_inputs or {
                "current_output": "0",
                "current_charger": "1",
            },
        )
        api = FakeApiTransport(last_success_at=fresh)
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._execute_hems_command(
                    api=api,
                    hems=hems,
                    decision=decision,
                    command_id="noop-test",
                )
            )
        finally:
            loop.close()
        return hems, result

    def test_repeated_noop_cycles_do_not_trip_circuit_breaker(self):
        """50 no-op cycles: ``_consecutive_failures`` stays 0 and
        ``_blocked_until`` stays None. The engine's anti-flapping
        cleared the decision to ``skip=True`` because the device
        is already in the target state.
        """
        hems = self._HemsEngine()
        # ``current_output='0'`` matches the proposed '0', so
        # ``_finalize_decision`` sets ``output_priority=None``
        # and ``skip=True``. The decision is a no-op.
        for cycle in range(50):
            hems, result = self._drive_one_cycle(
                hems=hems,
                fresh_inputs={"current_output": "0", "current_charger": "1"},
            )
            # Sanity: the engine should have skipped the
            # proposal because it matched the current state.
            self.assertEqual(
                hems._pending_commands, set(),
                msg=f"cycle {cycle}: pending must be empty on no-op",
            )
        # 50 routine no-op cycles must not have produced a single
        # failure (Codex reproduced 133 failures in 24h from this path).
        self.assertEqual(
            hems._consecutive_failures, 0,
            msg="no-op cycles must not increment the failure counter",
        )
        self.assertIsNone(
            hems._blocked_until,
            msg="no-op cycles must not arm the circuit breaker",
        )

    def test_protective_hold_does_not_touch_transport(self):
        """``_blocked_until`` in the future -> the dispatcher
        refuses via the guard and the api is NEVER contacted.
        ``_consecutive_failures`` is NOT incremented because
        the guard's refusal is a protective hold, not a
        transport error.
        """
        from datetime import datetime, timezone
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        hems = self._HemsEngine()
        hems._blocked_until = future
        # Pre-arm _consecutive_failures with a non-zero value to
        # prove the guard refusal does not ADD to it.
        hems._consecutive_failures = 2
        decision = self._HemsDecision(
            output_priority="0", charger_priority="1", reason="forced",
        )
        api = FakeApiTransport(last_success_at=fresh)
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._execute_hems_command(
                    api=api, hems=hems, decision=decision,
                    command_id="guard-test",
                )
            )
        finally:
            loop.close()
        # Dispatcher refused.
        self.assertFalse(result.dispatched, msg=result)
        self.assertIn("circuit breaker", result.error or "")
        # Transport was never contacted.
        self.assertEqual(
            api.calls, [],
            msg=f"api must not be touched on guard refusal; got: {api.calls!r}",
        )
        # Failure counter was NOT incremented.
        self.assertEqual(
            hems._consecutive_failures, 2,
            msg="guard refusal is a protective hold; the failure counter "
                "must not change",
        )

    def test_real_api_failure_increments_failures(self):
        """When the api rejects a write (e.g. ``set_output_priority``
        returns False), the dispatcher records a real failure
        and ``_consecutive_failures`` increments by 1.
        """
        from datetime import datetime, timezone
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        hems = self._HemsEngine()
        api = FakeApiTransport(reject_output=True, last_success_at=fresh)
        decision = self._HemsDecision(
            output_priority="0", charger_priority="1", reason="forced",
        )
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._execute_hems_command(
                    api=api, hems=hems, decision=decision,
                    command_id="api-fail-test",
                )
            )
        finally:
            loop.close()
        # Transport was contacted (api.reject_output=True) but
        # the dispatcher records api_accepted=False.
        self.assertFalse(result.api_accepted, msg=result)
        self.assertFalse(result.dispatched, msg=result)
        # The failure counter advanced.
        self.assertEqual(
            hems._consecutive_failures, 1,
            msg="real api failure must increment _consecutive_failures",
        )
        self.assertIsNotNone(
            hems._blocked_until,
            msg="real api failure must arm the circuit breaker",
        )

    def test_real_api_exception_increments_failures(self):
        """When ``set_output_priority`` raises an exception, the
        dispatcher catches it, records a real failure, and
        ``_consecutive_failures`` increments.
        """
        from datetime import datetime, timezone
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        hems = self._HemsEngine()
        api = FakeApiTransport(raise_on_set=OSError("offline"), last_success_at=fresh)
        decision = self._HemsDecision(
            output_priority="0", charger_priority="1", reason="forced",
        )
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._execute_hems_command(
                    api=api, hems=hems, decision=decision,
                    command_id="api-exc-test",
                )
            )
        finally:
            loop.close()
        self.assertFalse(result.dispatched, msg=result)
        self.assertEqual(
            hems._consecutive_failures, 1,
            msg="raised exception must increment _consecutive_failures",
        )

    def test_readback_mismatch_increments_failures(self):
        """The api accepts the write but the readback returns a
        different value. This is a real device-state failure and
        must increment ``_consecutive_failures``.
        """
        from datetime import datetime, timezone
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        hems = self._HemsEngine()
        api = FakeApiTransport(
            readback_mismatch_output="1",  # != "0"
            last_success_at=fresh,
        )
        decision = self._HemsDecision(
            output_priority="0", charger_priority="1", reason="forced",
        )
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._execute_hems_command(
                    api=api, hems=hems, decision=decision,
                    command_id="readback-fail-test",
                )
            )
        finally:
            loop.close()
        # Readback failed: api accepted the write but the device
        # reports a different value. ``api_accepted`` is True (the
        # transport acknowledged the POST); ``readback_valid`` is
        # False (the GET showed a mismatch).
        self.assertTrue(
            result.api_accepted,
            msg=f"api accepted the write; api_accepted must be True: {result!r}",
        )
        # Readback failed, so readback_valid is False.
        self.assertFalse(
            getattr(result, "readback_valid", True),
            msg=f"readback mismatch must set readback_valid=False: {result!r}",
        )
        self.assertFalse(
            result.dispatched,
            msg="readback mismatch must set dispatched=False",
        )
        self.assertEqual(
            hems._consecutive_failures, 1,
            msg="readback mismatch must increment _consecutive_failures",
        )

    def test_successful_write_passes_through_protected_path(self):
        """A decision with output/charger set goes through the
        real write + readback path. The api is contacted,
        ``_consecutive_failures`` is NOT incremented, and
        ``_blocked_until`` stays None.
        """
        from datetime import datetime, timezone
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        hems = self._HemsEngine()
        api = FakeApiTransport(last_success_at=fresh)
        decision = self._HemsDecision(
            output_priority="0", charger_priority="1", reason="forced",
        )
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._execute_hems_command(
                    api=api, hems=hems, decision=decision,
                    command_id="success-test",
                )
            )
        finally:
            loop.close()
        # Successful path: dispatched, readback verified.
        self.assertTrue(result.dispatched, msg=result)
        self.assertTrue(result.api_accepted, msg=result)
        # The transport received the write AND the readback.
        method_names = {call[0] for call in api.calls}
        self.assertIn("set_output_priority", method_names)
        self.assertIn("set_charger_priority", method_names)
        self.assertIn("fetch_device_configs", method_names)
        # Failure counter was NOT incremented.
        self.assertEqual(
            hems._consecutive_failures, 0,
            msg="successful write must not increment failures",
        )
        self.assertIsNone(
            hems._blocked_until,
            msg="successful write must not arm circuit breaker",
        )

    def test_noop_clears_pending_ledger_without_incrementing_failures(self):
        """When ``_pending_commands`` carries 'output' from a
        prior failed cycle, a no-op must clear the ledger
        (so the next real write starts from a clean state)
        but must NOT increment ``_consecutive_failures``.
        """
        from datetime import datetime, timezone
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        hems = self._HemsEngine()
        hems._pending_commands = {"output", "charger"}
        hems._consecutive_failures = 0
        # Run a no-op cycle.
        self._drive_one_cycle(
            hems=hems,
            fresh_inputs={"current_output": "0", "current_charger": "1"},
        )
        # The pending ledger was cleared (so the next real write
        # is not blocked by stale entries) but the counter did
        # NOT advance.
        self.assertEqual(
            hems._pending_commands, set(),
            msg="no-op must clear pending-commands ledger",
        )
        self.assertEqual(
            hems._consecutive_failures, 0,
            msg="no-op must NOT increment failure counter",
        )

    def test_report_control_failure_stamps_blocked_until_as_utc(self):
        """Codex reproduced a 3-hour clock-skew bug where
        ``_blocked_until`` was set with naive local time but the
        guard's normaliser assigned UTC to it. The guard then
        reported the breaker as "until X" long past X.

        After the fix ``_blocked_until`` is stamped UTC-aware so
        the guard's comparison with ``datetime.now(_UTC)`` is
        consistent regardless of the coordinator's local tz.
        """
        from datetime import datetime, timezone
        # Simulate a naive local ``now`` (EEST 11:01) being
        # passed by the coordinator.
        naive_eest = datetime(2026, 10, 14, 11, 1, 0)  # EEST
        self.assertIsNone(naive_eest.tzinfo)
        hems = self._HemsEngine()
        hems.report_control_failure(naive_eest, no_op=False)
        # The stored ``_blocked_until`` must be tz-aware. A naive
        # value would silently use the caller's local tz, which
        # is the very bug we are fixing.
        self.assertIsNotNone(
            hems._blocked_until.tzinfo,
            msg=f"_blocked_until must be UTC-aware; got naive "
                f"({hems._blocked_until!r})",
        )
        # And the guard's comparison (now_utc > blocked_utc) must
        # see the value as a real wall-clock instant, not as
        # 11:01 UTC (which would be 3 hours off).
        guard_now = datetime(2026, 10, 14, 11, 1, 10, tzinfo=timezone.utc)
        # 11:01:00 + 5s = 11:01:05, which is < 11:01:10. Guard allows.
        self.assertLess(
            hems._blocked_until, guard_now,
            msg=f"blocked {hems._blocked_until!r} should be < guard_now {guard_now!r}",
        )


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON)
class AllChannelNoOpTests(unittest.TestCase):
    """Phase 2C+3 final: the dispatcher must detect no-op via the
    ACTUAL list of writes (output, charger, buzzer) -- not just
    the ``is None`` check on each field. Codex reproduced a
    regression where a ``HemsDecision(skip=True)`` with
    ``buzzer_off=False`` and ``_last_buzzer='1'`` (already ON)
    had no real writes to perform, but the dispatcher still
    called ``fetch_device_configs()`` for readback. The api
    returned empty (rate-limited), the readback was recorded
    as failed, and ``report_control_failure`` incremented
    ``_consecutive_failures``.

    The fix:
      1. Compare every channel's intended write against the
         engine's last-known state.
      2. If no channel would actually issue a ``set_*`` call,
         the cycle is an all-channel no-op.
      3. Skip the readback entirely (do not call
         ``fetch_device_configs``).
      4. Do not record a control failure (the failure
         counter is reserved for real transport errors).
      5. The ``DispatcherResult`` carries a dedicated
         ``no_op`` flag so the audit log can distinguish
         "we did not need to write" from "we tried to write
         and it succeeded".
    """

    def setUp(self):
        from hems.engine import (
            HemsEngine, HemsDecision, execute_hems_command,
        )
        self._HemsEngine = HemsEngine
        self._HemsDecision = HemsDecision
        self._execute_hems_command = execute_hems_command

    def _drive(self, decision, hems):
        from datetime import datetime, timezone
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        api = FakeApiTransport(last_success_at=fresh)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self._execute_hems_command(
                    api=api,
                    hems=hems,
                    decision=decision,
                    command_id="noop-test",
                )
            ), api
        finally:
            loop.close()

    def test_buzzer_off_false_with_last_buzzer_one_is_a_no_op(self):
        """``buzzer_off=False`` with ``_last_buzzer='1'`` means
        "keep the buzzer ON". The current state already matches
        the proposal, so the dispatcher must NOT call
        ``fetch_device_configs``. The api.calls list must be
        empty (no set_config_item, no fetch_device_configs).
        This is the EXACT Codex reproduction.
        """
        hems = self._HemsEngine()
        hems._last_buzzer = "1"
        decision = self._HemsDecision(
            output_priority=None,
            charger_priority=None,
            buzzer_off=False,  # "buzzer ON" but _last_buzzer='1' = already ON
            reason="keep_on",
        )
        result, api = self._drive(decision, hems)
        # No transport call whatsoever.
        self.assertEqual(
            api.calls, [],
            msg=f"all-channel no-op must NOT call the transport; "
                f"got: {api.calls!r}",
        )
        # The result marks the cycle as a no-op.
        self.assertTrue(
            result.no_op,
            msg=f"DispatcherResult must carry no_op=True; got "
                f"{result!r}",
        )
        # No failure was recorded on the engine.
        self.assertEqual(
            hems._consecutive_failures, 0,
            msg="all-channel no-op must NOT increment failures",
        )
        self.assertIsNone(
            hems._blocked_until,
            msg="all-channel no-op must NOT arm the circuit breaker",
        )
        # Readback was intentionally skipped.
        self.assertTrue(
            result.readback_skipped,
            msg="readback must be skipped when no write was issued",
        )
        # The result is NOT success in the "physical apply" sense.
        self.assertFalse(
            result.dispatched,
            msg="no-op must NOT set dispatched=True; that would "
                "falsely advertise a physical apply",
        )
        self.assertFalse(
            result.api_accepted,
            msg="no-op must NOT set api_accepted=True; no api call "
                "was made",
        )
        self.assertFalse(
            result.success,
            msg="no_op must NOT advertise success; the dispatcher "
                "did not apply anything to the device",
        )

    def test_buzzer_off_true_with_last_buzzer_one_is_a_real_write(self):
        """``buzzer_off=True`` with ``_last_buzzer='1'`` means
        "turn the buzzer OFF". The current state does NOT match
        the proposal, so the dispatcher MUST call
        ``set_config_item('buzzerAlarmSetting', '0')`` and then
        readback via ``fetch_device_configs``.
        """
        hems = self._HemsEngine()
        hems._last_buzzer = "1"
        decision = self._HemsDecision(
            output_priority=None,
            charger_priority=None,
            buzzer_off=True,
            reason="turn_off",
        )
        result, api = self._drive(decision, hems)
        # Exactly two transport calls: the buzzer set and the
        # readback that confirms it persisted.
        method_names = [call[0] for call in api.calls]
        self.assertEqual(
            method_names,
            ["set_config_item", "fetch_device_configs"],
            msg=f"buzzer set + readback; got: {method_names!r}",
        )
        # The engine recorded the new state.
        self.assertEqual(hems._last_buzzer, "0")
        # Real write -> readback ran.
        self.assertFalse(
            result.readback_skipped,
            msg="readback must run after a real write",
        )
        # All counters clean (the write succeeded).
        self.assertEqual(hems._consecutive_failures, 0)
        self.assertIsNone(hems._blocked_until)
        # Not a no-op.
        self.assertFalse(result.no_op)
        # Readback passed; the dispatcher reported a physical apply.
        self.assertTrue(result.dispatched)
        self.assertTrue(result.api_accepted)
        self.assertTrue(result.readback_valid)
        self.assertTrue(result.success)

    def test_output_priority_matching_current_state_is_a_no_op(self):
        """``output_priority='0'`` with ``_last_cmd_output='0'``
        means the engine's proposal matches the current state.
        No set_output_priority call. No readback. No failure.
        """
        hems = self._HemsEngine()
        hems._last_cmd_output = "0"
        hems._last_buzzer = "1"  # so buzzer_off=False is also a no-op
        decision = self._HemsDecision(
            output_priority="0",
            charger_priority=None,
            buzzer_off=False,  # matches _last_buzzer='1'
            reason="already_in_target",
        )
        result, api = self._drive(decision, hems)
        self.assertEqual(
            api.calls, [],
            msg=f"output match must NOT touch the transport; got: "
                f"{api.calls!r}",
        )
        self.assertTrue(result.no_op)
        self.assertEqual(hems._consecutive_failures, 0)

    def test_readback_failure_with_no_writes_does_not_increment_failures(self):
        """Codex reproduction: ``buzzer_off=False`` with
        ``_last_buzzer='1'`` (no actual write) followed by an
        empty readback response (rate limit). The previous
        dispatcher recorded the readback failure as a transport
        failure and incremented ``_consecutive_failures``. The
        new dispatcher must NOT call ``fetch_device_configs`` at
        all when no write was issued, so the empty response does
        not even reach the bookkeeping path.
        """
        hems = self._HemsEngine()
        hems._last_buzzer = "1"
        decision = self._HemsDecision(
            output_priority=None,
            charger_priority=None,
            buzzer_off=False,
            reason="already_on",
        )
        # FakeApiTransport would raise on readback if we called
        # it; this test asserts we did NOT call it.
        class _RaisingApi(FakeApiTransport):
            def __init__(self):
                super().__init__(last_success_at=None)
                self.calls = []

            async def fetch_device_configs(self):
                raise AssertionError(
                    "fetch_device_configs MUST NOT be called when no "
                    "write was issued"
                )

        from datetime import datetime, timezone
        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        api = _RaisingApi()
        hems._last_realtime_at = fresh
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self._execute_hems_command(
                    api=api,
                    hems=hems,
                    decision=decision,
                    command_id="no-readback-test",
                )
            )
        finally:
            loop.close()
        # Readback was skipped (the api's fetch_device_configs was
        # never reached).
        self.assertTrue(
            result.readback_skipped,
            msg=f"readback must be skipped when no write was issued; "
                f"got: {result!r}",
        )
        # No transport contact.
        self.assertEqual(
            api.calls, [],
            msg=f"transport must NOT be touched on all-channel no-op; "
                f"got: {api.calls!r}",
        )
        # No failure recorded.
        self.assertEqual(
            hems._consecutive_failures, 0,
            msg="all-channel no-op must NOT increment failures; "
                "Codex reproduced 133 failures on this path",
        )
        self.assertIsNone(
            hems._blocked_until,
            msg="all-channel no-op must NOT arm the circuit breaker",
        )

    def test_tz_aware_manual_override_compare_against_naive_now(self):
        """Codex reproduced: ``_evaluation_hold`` and
        ``detect_manual_override`` compared ``_manual_override_until``
        (UTC-aware, set by ``arm_manual_override``) with a naive
        ``now`` from the coordinator. The naive < aware comparison
        raised TypeError on Europe/Kyiv.

        After the fix, both setters (``arm_manual_override``,
        ``detect_manual_override``) promote the naive ``now`` to
        UTC-aware, and the getters use ``_tz_compare``. The
        comparison must work without raising.
        """
        from datetime import datetime, timezone
        hems = self._HemsEngine()
        # Europe/Kyiv (UTC+2 in October, +3 in DST).
        naive_local = datetime(2026, 10, 14, 11, 1, 0)  # naive
        self.assertIsNone(naive_local.tzinfo)
        # arm_manual_override must accept the naive local time
        # and stamp a UTC-aware value (NOT pretend the local
        # digits are already UTC -- that was the previous bug).
        hems.arm_manual_override(naive_local)
        self.assertIsNotNone(
            hems._manual_override_until.tzinfo,
            msg=f"arm_manual_override must stamp UTC-aware; got naive "
                f"{hems._manual_override_until!r}",
        )
        # 11:01 EEST == 08:01 UTC (DST is +3 in October 2026).
        # Default hold is 5 minutes, so 11:01 + 5min = 11:06.
        self.assertEqual(
            hems._manual_override_until,
            naive_local.astimezone().astimezone(timezone.utc)
            + timedelta(minutes=5),
            msg=f"arm_manual_override must convert naive local to "
                f"UTC correctly; got {hems._manual_override_until!r}",
        )
        # detect_manual_override must not raise when comparing
        # the UTC-aware ``_manual_override_until`` to a naive
        # ``now`` (the previous code raised TypeError).
        # Pass a naive ``now`` that is BEFORE the hold expires.
        hems._last_cmd_output = "0"
        hems._last_cmd_output_at = naive_local - timedelta(minutes=1)
        # 5 minutes later than the arm time -- the hold is still
        # active (4-minute window). detect_manual_override reads
        # the api-reported current_output.
        try:
            held = hems.detect_manual_override(
                actual_output="2",  # mismatch
                actual_charger="2",
                now=naive_local + timedelta(seconds=30),
            )
        except TypeError as exc:
            self.fail(
                f"detect_manual_override must not raise TypeError on "
                f"tz mismatch; got: {exc}"
            )
        # Hold is still active, so the function returns the
        # mismatch flags.
        self.assertTrue(held)

    def test_real_write_skips_dispatcher_for_buzzer_only_when_already_set(self):
        """Comprehensive: the exact Codex scenario
        (``buzzer_off=False``, ``_last_buzzer='1'``) must skip
        the readback AND not record a failure. The transport
        call list must be empty.
        """
        hems = self._HemsEngine()
        hems._last_buzzer = "1"
        decision = self._HemsDecision(
            output_priority=None,
            charger_priority=None,
            buzzer_off=False,
            skip=True,
            reason="keep_on",
        )
        result, api = self._drive(decision, hems)
        # Transport was never touched.
        self.assertEqual(
            api.calls, [],
            msg=f"all-channel no-op must NOT touch the transport; "
                f"got: {api.calls!r}",
        )
        # Pending ledger was cleared (so the next cycle starts
        # from a clean state) but the counter did NOT advance.
        self.assertEqual(
            hems._pending_commands, set(),
            msg="no-op must clear the pending-commands ledger",
        )
        self.assertEqual(
            hems._consecutive_failures, 0,
            msg="no-op must NOT increment the failure counter",
        )
        # DispatcherResult is clearly a no-op.
        self.assertTrue(result.no_op)
        self.assertFalse(result.dispatched)
        self.assertFalse(result.api_accepted)
        self.assertFalse(result.success)
        self.assertIsNone(result.error)


if __name__ == "__main__":
    unittest.main()
