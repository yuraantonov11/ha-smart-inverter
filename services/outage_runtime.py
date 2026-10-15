"""Outage runtime wiring (Phase 2C + Phase 3).

This module is the **single wire** between the pure planner
(``services/outage_planner.py``), the event store
(``services/outage.py``), and the protected inverter write path
(``InverterCoordinator._execute_hems_command``).

Public API
----------
* ``snapshot_telemetry(coordinator, hass, *, entry_id=None) -> Telemetry``
    Build a ``Telemetry`` snapshot from a live coordinator. Pure read;
    never mutates the coordinator and never calls ``api.set_*``.

* ``plan_for_event(coordinator, hass, event) -> PlannerPlan``
    Build the snapshot and run the planner. Single entry point
    used by both the dashboard (read-only) and the automatic
    scheduler (write-capable).

* ``execute_plan(coordinator, event, plan) -> dict``
    PROTECTED execution path. Routes a non-blocked plan through
    ``coordinator._execute_hems_command`` and records the outcome
    in the evidence journal. The planner's mode gates whether
    this is called at all.

* ``is_automatic_allowed(coordinator, plan) -> tuple[bool, str]``
    Pre-flight gate for the automatic scheduler. Combines the
    planner's ``blocked`` flag with the runtime safety guards
    (manual pause, telemetry freshness, grid availability, mode).

* ``mode_from_options(options) -> str``
    Read ``options['outage_mode']``; default ``'observation'``.
    Valid values: ``'disabled'``, ``'observation'``, ``'automatic'``.

* ``wire_outage_runtime(hass, entry, coordinator) -> None``
    Single bootstrap call used by ``__init__.py`` (one-line wire).
    Registers the ``compute_outage_plan`` service, starts the
    scheduled tick (only in ``'automatic'`` mode), and arranges
    clean teardown on entry unload.

Out of scope
------------
* Any HA service call beyond what ``_execute_hems_command`` already
  does (the protected dispatcher is the only path to the inverter).
* Mode policy changes (the mode is operator-driven; we never flip
  it from automatic side effects).
* The notification adapter                                (Phase 4).
* The UI                                                   (Phase 4+).

Design discipline
-----------------
* NO live inverter write outside ``_execute_hems_command``.
* NO mutation of ``coordinator`` state from the snapshot helpers.
* All defensive reads (``getattr`` / ``.get``); a missing field
  collapses to ``None`` and the planner handles "unknown".
* No HA imports at module top-level: the module is importable by
  unit tests that do not have Home Assistant on ``sys.path``.
  HA imports happen lazily inside the helpers that need them
  (service registration, interval registration, store access).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Optional

from .outage_planner import (
    DEFAULT_CHARGE_EFFICIENCY,
    DEFAULT_DISCHARGE_EFFICIENCY,
    DEFAULT_MAX_PLANNING_HORIZON_DAYS,
    DEFAULT_RESERVE_SOC_PCT,
    PlannerPlan,
    TELEMETRY_STALENESS_LIMIT_SECONDS,
    Telemetry,
    plan_outage,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTAGE_MODE_KEY = "outage_mode"
DEFAULT_OUTAGE_MODE = "observation"
OUTAGE_MODE_DISABLED = "disabled"
OUTAGE_MODE_OBSERVATION = "observation"
OUTAGE_MODE_AUTOMATIC = "automatic"
_VALID_MODES = frozenset(
    {OUTAGE_MODE_DISABLED, OUTAGE_MODE_OBSERVATION, OUTAGE_MODE_AUTOMATIC}
)

# Lower bound on the auto-prep check interval. The operator policy is
# "the operator can wait up to 5 minutes for a plan refresh after
# editing the event" -- the live scheduler uses 5 min by default.
DEFAULT_TICK_INTERVAL_SECONDS = 300
MIN_TICK_INTERVAL_SECONDS = 10

# In-line backstop: when the planner has not yet populated
# ``prep_start_iso`` (older planner, or a planner in observation-only
# mode), trigger prep this far ahead of the event start. Mirrors the
# operator-ratified "4 hours" buffer used in earlier drafts.
PREP_BACKSTOP_HOURS = 4

# Output-source priority values for the protected dispatcher. We pass
# the EXACT strings the protected dispatcher's
# ``self.api.set_output_priority`` (api.py) accepts. The canonical
# numeric strings are defined in hems/engine.py:118-125:
#
#     class OutputPriority:
#         USB = "0"  # Grid first
#         SBU = "2"  # Solar/Battery first
#     class ChargerPriority:
#         SNU = "1"  # Solar + Utility
#         OSO = "2"  # Solar only
#
# The runtime uses ``USB = "0"`` (utility-first; the battery
# preps from the grid before the outage) and ``SNU = "1"``
# (solar + utility). We DO NOT flip the buzzer
# (``decision.buzzer_off = None``) so the dispatcher leaves
# the acoustic state untouched -- the outage prep is a quiet
# operation. The protected dispatcher does the string<->int
# mapping; we stay symbolic here.
DECISION_OUTPUT_PRIORITY = "0"  # OutputPriority.USB
DECISION_CHARGER_PRIORITY = "1"  # ChargerPriority.SNU

# Reused in the evidence journal as the cause code for an
# outage-driven prep write.
OUTAGE_CAUSE_CODE = "planned_outage"

# Service name registered on the per-entry service namespace.
COMPUTE_OUTAGE_PLAN_SERVICE = "compute_outage_plan"


# ---------------------------------------------------------------------------
# Defensive read helpers
# ---------------------------------------------------------------------------


def _safe_getattr(obj: Any, *names: str, default: Any = None) -> Any:
    """Return the first attribute that resolves to a non-``None`` value."""
    for name in names:
        if obj is None:
            return default
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def _safe_dict_get(d: Any, *keys: str) -> Any:
    """Read ``keys`` from a dict-like; return ``None`` on any failure."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


# ---------------------------------------------------------------------------
# Public API: mode selector
# ---------------------------------------------------------------------------


def mode_from_options(options: Any) -> str:
    """Return the operator-configured outage mode.

    Valid values: ``'disabled'``, ``'observation'``, ``'automatic'``.
    Default: ``'observation'`` -- the operator's first install
    should land in safe "no writes" mode, with the dashboard showing
    the full plan so the operator can opt in.
    """
    if options is None:
        return DEFAULT_OUTAGE_MODE
    if not isinstance(options, dict):
        return DEFAULT_OUTAGE_MODE
    raw = options.get(OUTAGE_MODE_KEY, DEFAULT_OUTAGE_MODE)
    if raw is None:
        return DEFAULT_OUTAGE_MODE
    value = str(raw).strip()
    if not value:
        return DEFAULT_OUTAGE_MODE
    if value not in _VALID_MODES:
        raise ValueError(
            f"unknown outage_mode {value!r}; "
            f"valid: {sorted(_VALID_MODES)}"
        )
    return value


# ---------------------------------------------------------------------------
# Public API: telemetry snapshot
# ---------------------------------------------------------------------------


def snapshot_telemetry(
    coordinator: Any,
    hass: Any = None,
    *,
    entry_id: str | None = None,
) -> Telemetry:
    """Build a fresh ``Telemetry`` from a live ``InverterCoordinator``.

    Pure read. NEVER mutates ``coordinator``; NEVER calls
    ``api.set_*``; NEVER calls ``_execute_hems_command``.

    All fields default to ``None`` (or the documented numeric
    default) when the underlying coordinator attribute is missing.
    The planner treats ``None`` as "unknown" and produces a
    meaningful plan in either case.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # ---- Battery capacity (Phase 2C/3 follow-up: DEFECT D)
    # The runtime no longer duplicates the canonical range check
    # (coordinator._derive_battery_capacity at coordinator.py:2215).
    # We delegate to the same validator the coordinator uses for
    # the live ``_battery_capacity_kwh`` chain so the snapshot and
    # the runtime planners can NEVER disagree. When the coordinator
    # lacks a ``_derive_battery_capacity`` method (older builds,
    # test doubles) we fall back to the prior inline check so the
    # contract test still has a target.
    derived = None
    derive_fn = getattr(coordinator, "_derive_battery_capacity", None)
    options = _read_options(coordinator, entry_id=entry_id)
    data = getattr(coordinator, "data", None)
    capacity_warning: str | None = None

    def _fallback_derive(_opts: dict) -> dict:
        """Inline derivation matching the legacy snapshot path.

        Used only when the coordinator does not expose
        ``_derive_battery_capacity``. The legacy semantics were
        "no explicit input -> None chain"; we preserve that so the
        ``data=None`` / ``options=None`` tests keep their
        ``battery_capacity_kwh is None`` assertion. The
        canonical range check is part of the validator so a
        production coordinator never reaches this branch.
        """
        opt = _opts or {}
        ah_raw = opt.get("battery_capacity_ah")
        volt_raw = opt.get("nominal_voltage_v")
        if ah_raw is None and volt_raw is None:
            return {
                "ah": None,
                "voltage": None,
                "kwh": None,
                "warning": None,
                "ok_for_planning": False,
            }
        ah_value = _as_float(ah_raw)
        if ah_value is None:
            ah_value = _as_float(_safe_getattr(coordinator, "_battery_capacity_ah"))
        voltage_value = _as_float(volt_raw)
        if voltage_value is None:
            voltage_value = _as_float(
                _safe_getattr(coordinator, "_nominal_voltage_v")
            )
        if (
            ah_value is not None
            and voltage_value is not None
            and voltage_value > 0
        ):
            try:
                kwh = (ah_value * voltage_value) / 1000.0
            except (TypeError, ValueError, ZeroDivisionError):
                kwh = None
            ok = bool(
                10.0 <= ah_value <= 2000.0
                and 10.0 <= voltage_value <= 100.0
            )
            return {
                "ah": ah_value,
                "voltage": voltage_value,
                "kwh": kwh if ok else None,
                "warning": None if ok else "out of form range",
                "ok_for_planning": ok,
            }
        return {
            "ah": ah_value,
            "voltage": voltage_value,
            "kwh": None,
            "warning": None,
            "ok_for_planning": False,
        }

    if callable(derive_fn):
        try:
            derived = derive_fn(options or {})
        except Exception:  # noqa: BLE001
            derived = None
    if derived is None:
        derived = _fallback_derive(options or {})

    if isinstance(derived, dict) and "ok_for_planning" in derived:
        capacity_warning = (
            derived.get("warning") if isinstance(derived.get("warning"), str) else None
        )
        battery_capacity_kwh = (
            _as_float(derived.get("kwh"))
            if derived.get("ok_for_planning")
            else None
        )
        battery_capacity_ah = _as_float(derived.get("ah"))
        nominal_voltage_v = _as_float(derived.get("voltage"))
    else:
        battery_capacity_kwh = None
        battery_capacity_ah = None
        nominal_voltage_v = None

    # The planner at outage_planner.py:433 reads
    # ``battery_capacity_ok_for_planning`` as the planner's gate.
    battery_capacity_ok_for_planning = bool(
        isinstance(derived, dict)
        and derived.get("ok_for_planning") is True
        and battery_capacity_kwh is not None
    )

    # Surface the validator's warning as a Telemetry footnote so
    # the dashboard can render it directly without a second pass.
    # ``telemetry_capacity_warning`` is a private extension on the
    # Telemetry dataclass; if it is missing the attribute is
    # silently skipped (older planners ignore it).
    telemetry_capacity_warning = capacity_warning

    # ---- Battery telemetry (live readings from ``coordinator.data``)
    battery_soc_pct = _as_float(_safe_dict_get(data, "batterySoc"))
    battery_soc_corrected_pct = _as_float(
        _safe_dict_get(data, "correctedSoc")
    )
    if battery_soc_corrected_pct is None:
        battery_soc_corrected_pct = battery_soc_pct
    battery_voltage_v = _as_float(_safe_dict_get(data, "batteryVoltage"))

    # ---- Efficiency: explicit options first, then the planner default
    charge_eff = options.get("battery_charge_efficiency")
    if charge_eff is None:
        charge_eff = options.get("charge_efficiency")
    if charge_eff is None:
        charge_eff = DEFAULT_CHARGE_EFFICIENCY
    charge_eff_f = _as_float(charge_eff)
    if charge_eff_f is None:
        charge_eff_f = DEFAULT_CHARGE_EFFICIENCY

    discharge_eff = options.get("battery_discharge_efficiency")
    if discharge_eff is None:
        discharge_eff = options.get("discharge_efficiency")
    if discharge_eff is None:
        discharge_eff = DEFAULT_DISCHARGE_EFFICIENCY
    discharge_eff_f = _as_float(discharge_eff)
    if discharge_eff_f is None:
        discharge_eff_f = DEFAULT_DISCHARGE_EFFICIENCY

    # ---- Power readings
    pv_power_w = _as_float(_safe_dict_get(data, "pvPower"))
    load_power_w = _as_float(_safe_dict_get(data, "loadPower"))
    grid_power_w = _as_float(_safe_dict_get(data, "gridPower"))
    grid_available = _as_bool(_safe_dict_get(data, "gridAvailable", "grid_available"))
    if grid_available is None:
        # Default to True: absence of a "gridAvailable" key is the
        # most common reading and the safe default for a plan that
        # needs to decide whether to charge from the grid.
        grid_available = True

    # ---- Operator config
    reserve_soc_pct = _as_float(options.get("reserve_soc", DEFAULT_RESERVE_SOC_PCT))
    if reserve_soc_pct is None:
        reserve_soc_pct = DEFAULT_RESERVE_SOC_PCT

    max_utility_charging_current_a = _as_float(
        options.get("max_utility_charging_current")
    )
    if max_utility_charging_current_a is None:
        max_utility_charging_current_a = _as_float(
            options.get("max_utility_charging_current_a")
        )
    if max_utility_charging_current_a is None and isinstance(data, dict):
        max_utility_charging_current_a = _as_float(
            data.get("maxUtilityChargingCurrent")
        )

    max_charging_current_a = _as_float(options.get("max_charging_current"))
    if max_charging_current_a is None:
        max_charging_current_a = _as_float(options.get("max_charging_current_a"))
    if max_charging_current_a is None and isinstance(data, dict):
        max_charging_current_a = _as_float(data.get("maxChargingCurrent"))

    # ---- PV forecast (tomorrow + calibrator)
    pv_forecast_tomorrow_kwh = _as_float(
        _safe_getattr(coordinator, "forecast_tomorrow_kwh")
    )
    pv_forecast_confidence, pv_forecast_samples = _read_calibrator_metrics(
        coordinator
    )

    # ---- Consumption history
    tarpit_consumption_avg_kwh = _read_consumption_history(coordinator)

    # ---- Manual-override hold
    current_pause_until_iso = _read_pause_until(coordinator)

    # ---- Planning horizon cap
    max_horizon = options.get("max_planning_horizon_days")
    if max_horizon is None:
        max_horizon = DEFAULT_MAX_PLANNING_HORIZON_DAYS
    try:
        max_horizon_int = int(max_horizon)
    except (TypeError, ValueError):
        max_horizon_int = DEFAULT_MAX_PLANNING_HORIZON_DAYS

    # ---- Telemetry freshness
    freshness_seconds = _read_freshness_seconds(coordinator, now)

    return Telemetry(
        now_iso=now_iso,
        telemetry_freshness_seconds=freshness_seconds,
        battery_capacity_kwh=battery_capacity_kwh,
        battery_capacity_ah=battery_capacity_ah,
        nominal_voltage_v=nominal_voltage_v,
        battery_capacity_ok_for_planning=battery_capacity_ok_for_planning,
        battery_soc_pct=battery_soc_pct,
        battery_soc_corrected_pct=battery_soc_corrected_pct,
        battery_voltage_v=battery_voltage_v,
        battery_charge_efficiency=charge_eff_f,
        battery_discharge_efficiency=discharge_eff_f,
        pv_power_w=pv_power_w,
        load_power_w=load_power_w,
        grid_power_w=grid_power_w,
        grid_available=grid_available,
        reserve_soc_pct=reserve_soc_pct,
        max_utility_charging_current_a=max_utility_charging_current_a,
        max_charging_current_a=max_charging_current_a,
        pv_forecast_tomorrow_kwh=pv_forecast_tomorrow_kwh,
        pv_forecast_confidence=pv_forecast_confidence,
        pv_forecast_samples=pv_forecast_samples,
        tarpit_consumption_avg_kwh=tarpit_consumption_avg_kwh,
        current_pause_until_iso=current_pause_until_iso,
        max_planning_horizon_days=max_horizon_int,
        is_holiday_or_weekend=False,
    )


# ---------------------------------------------------------------------------
# Public API: plan for event
# ---------------------------------------------------------------------------


def plan_for_event(
    coordinator: Any,
    hass: Any,
    event: Any,
) -> PlannerPlan:
    """Build telemetry + plan for a confirmed ``OutageEvent``.

    Thin wrapper around ``snapshot_telemetry`` + ``plan_outage``.
    The single entry point that the dashboard and the automatic
    scheduler both call. Returns a ``PlannerPlan`` -- the caller
    decides whether to dispatch it.
    """
    telemetry = snapshot_telemetry(coordinator, hass)
    return plan_outage(telemetry, event)


# ---------------------------------------------------------------------------
# Public API: pre-flight gate
# ---------------------------------------------------------------------------


def is_automatic_allowed(
    coordinator: Any,
    plan: PlannerPlan,
) -> tuple[bool, str]:
    """Return ``(True, "")`` iff the automatic scheduler may dispatch.

    Combines four gates:

        1. The operator's live mode is ``'automatic'`` (the function
           reads ``coordinator._entry.options['outage_mode']`` so a
           mode flip lands on the next tick without any explicit
           invalidation).
        2. The planner did not block the plan.
        3. The grid is available (no point asking the protected
           dispatcher to charge from the grid if the grid is
           down).
        4. Telemetry freshness is within the operator-ratified
           budget (``TELEMETRY_STALENESS_LIMIT_SECONDS``) -- the
           function reads the live coordinator's ``last_update_ts``
           rather than the planner's cached freshness, so a stale
           coordinator is caught even when the planner was happy
           with a previous snapshot.
        5. No manual-override hold is in effect (read from
           ``coordinator._hems._manual_override_until``).

    The reason string is always concrete (e.g. ``"plan blocked;
    <planner reason>"`` or ``"telemetry stale; 600s > 300s"``)
    so the audit log distinguishes "blocked because the planner
    refused" from "blocked because the data is too old".
    """
    # 0. Live mode
    options = _read_options(coordinator)
    try:
        live_mode = mode_from_options(options)
    except ValueError:
        live_mode = DEFAULT_OUTAGE_MODE
    if live_mode != OUTAGE_MODE_AUTOMATIC:
        return (False, f"mode {live_mode}; automatic prep is suspended")

    # 1. Planner-level block
    if getattr(plan, "blocked", False):
        reason = getattr(plan, "reason_if_blocked", None) or "unknown planner reason"
        return (False, f"plan blocked; {reason}")

    # 2. Grid availability -- read the live coordinator, not the
    #    cached plan dict, so a grid drop between snapshots is
    #    caught. ``None`` from the helper means "field absent";
    #    we refuse rather than default to True.
    grid_available = _read_grid_available(coordinator)
    if grid_available is None:
        return (
            False,
            "grid availability unknown; refusing without explicit reading",
        )
    if grid_available is False:
        return (False, "grid unavailable; cannot dispatch auto-prep from grid")

    # 3. Manual pause -- live read.
    pause_iso = _read_pause_until(coordinator)
    if pause_iso:
        try:
            until = datetime.fromisoformat(str(pause_iso))
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if until > now:
                return (
                    False,
                    f"manual pause until {until.isoformat()}; "
                    "automatic prep is suspended",
                )
        except (TypeError, ValueError):
            # The pause string is malformed; treat as no pause
            # rather than crash the gate.
            pass

    # 4. Telemetry freshness -- live read.
    freshness = _read_freshness_seconds(
        coordinator, datetime.now(timezone.utc)
    )
    if freshness > TELEMETRY_STALENESS_LIMIT_SECONDS:
        return (
            False,
            f"telemetry stale; {freshness}s > "
            f"{TELEMETRY_STALENESS_LIMIT_SECONDS}s",
        )

    # 5. Existing safety guards. The protected dispatcher has its
    # own dedup / circuit-breaker / anti-flapping, but a visible
    # surface here keeps the audit trail honest.
    if not _safety_guards_ok(coordinator):
        return (False, "safety guard tripped; automatic prep is suspended")

    return (True, "")


# ---------------------------------------------------------------------------
# Public API: protected execution
# ---------------------------------------------------------------------------


async def execute_plan(
    coordinator: Any,
    event: Any,
    plan: PlannerPlan,
) -> dict:
    """Route a non-blocked plan through the protected dispatcher.

    Behaviour
    ---------
    * Re-snapshots telemetry at execution time. If the snapshot
      is stale, paused, grid-down, or the planner blocked, returns
      ``{"executed": False, "reason": "..."}`` WITHOUT touching
      the inverter.
    * Calls ``coordinator._execute_hems_command`` exactly once
      with a ``SimpleNamespace`` decision shaped like a
      ``HemsDecision``. The dispatcher is the existing protected
      path; we do NOT call ``coordinator.api.set_*`` directly.
    * Uses a ``command_id`` of the form ``planned_outage:<event_id>``
      so the evidence journal's ``command_scope`` keys all writes
      from a single dispatch under one cause code.
    * Never raises -- every code path returns a dict.

    Caller responsibility
    ---------------------
    This function does NOT check the live outage mode -- the
    scheduler (or any caller) is expected to have already gated
    on ``mode == 'automatic'``. ``is_automatic_allowed`` is the
    full pre-flight gate; the live scheduler calls both. The
    separation is intentional: ``execute_plan`` is the protected
    runtime guard (stale / grid / pause / planner block), and
    ``is_automatic_allowed`` is the policy gate (mode + the same
    runtime guards, surfaced as a single tuple).

    Mapping rules
    -------------
    * ``decision.output_priority`` is set to ``"USB"`` (utility-
      first) ONLY when the planner flagged
      ``needs_charging_from_grid_kwh > 0`` AND the grid is
      available. Otherwise the field is ``None`` so the
      dispatcher leaves the existing output-source untouched.
    * ``decision.charger_priority`` is set to ``"SNU"`` (solar +
      utility) under the same condition. The protected dispatcher
      does the string<->int mapping.
    * The decision is never ``"SOLAR"`` or ``"UTI"`` -- utility-
      only output is unsafe during an outage window because the
      grid will be gone; solar-only is unsafe because the prep
      window runs before sunrise.

    The decision object also carries ``reason`` so the evidence
    journal records the operator-ratified cause string and
    ``skip=False`` / ``buzzer_off=False`` -- the dispatcher is
    the one that decides whether to toggle the buzzer.
    """
    event_id = getattr(event, "id", None) or "unknown"

    # 1. Runtime guard. The mode check is the caller's
    #    responsibility; here we re-check the things the live
    #    coordinator may have changed between snapshot and
    #    execution: pause marker, grid availability, telemetry
    #    freshness, and the planner's blocked flag.
    refusal = _execution_refusal(coordinator, plan, event_id=event_id)
    if refusal is not None:
        _LOGGER.info(
            "outage execute_plan: refused (event=%s, reason=%s)",
            event_id, refusal,
        )
        return {"executed": False, "reason": refusal}

    # 2. Decide what to write. The mapping is conservative: we
    #    only touch output/charger if the planner explicitly
    #    says we need grid charging, and only when the grid is
    #    currently up.
    plan_dict = getattr(plan, "plan", {}) or {}
    try:
        needs_grid = float(plan_dict.get("needs_charging_from_grid_kwh", 0) or 0)
    except (TypeError, ValueError):
        needs_grid = 0.0

    grid_available = _read_grid_available(coordinator)
    if needs_grid > 0 and grid_available:
        output_priority: str | None = DECISION_OUTPUT_PRIORITY
        charger_priority: str | None = DECISION_CHARGER_PRIORITY
    else:
        # Nothing to do -- surface a no-op so the audit log shows
        # the planner considered a write and decided against it.
        return {
            "executed": False,
            "reason": (
                f"no grid charge needed "
                f"(needs_charging_from_grid_kwh={needs_grid}, "
                f"grid_available={grid_available})"
            ),
        }

    # 3. Build the decision payload. ``SimpleNamespace`` is the
    #    same shape the engine uses for ``HemsDecision`` without
    #    importing the dataclass (which would pull in HA via the
    #    engine module). The protected dispatcher's reads via
    #    ``getattr`` are satisfied.
    # NOTE: ``buzzer_off=None`` so the dispatcher's
    # ``if decision.buzzer_off ... elif not decision.buzzer_off``
    # branch at coordinator.py:1688-1695 is skipped entirely; the
    # runtime must NEVER silently toggle the buzzer.
    decision = SimpleNamespace(
        output_priority=output_priority,
        charger_priority=charger_priority,
        reason=(
            f"{OUTAGE_CAUSE_CODE}: prep for event {event_id} "
            f"(needs_grid={needs_grid:.3f} kWh)"
        ),
        skip=False,
        buzzer_off=None,
    )

    command_id = f"{OUTAGE_CAUSE_CODE}:{event_id}"

    execute_fn = getattr(coordinator, "_execute_hems_command", None)
    if execute_fn is None:
        return {
            "executed": False,
            "reason": (
                "coordinator._execute_hems_command is not available; "
                "refusing to bypass the protected path"
            ),
        }

    # 4. Call the protected dispatcher. Production signature
    #    (coordinator.py:1645-1922): ``async def _execute_hems_command(
    #    self, decision, command_id=None, *, evidence_recorded=False)
    #    -> DispatcherResult``. The dispatcher NEVER returns
    #    ``None``; it returns a ``DispatcherResult`` whose
    #    ``.success`` property is the strict AND of
    #    ``dispatched`` / ``api_accepted`` / ``readback_valid``.
    #    The runtime maps ``result.success`` to ``executed=True``
    #    and forwards ``result.error`` as the audit-log reason
    #    on failure. Older builds that returned ``None`` (the
    #    pre-hotfix contract) are tolerated defensively but
    #    flagged as "no signal" so the audit log does not claim
    #    success on a missing return value.
    try:
        result = await _call_execute_hems_command(
            execute_fn, decision, command_id, evidence_recorded=True
        )
    except Exception as exc:  # noqa: BLE001 -- execute_plan never raises
        return {
            "executed": False,
            "reason": (
                f"dispatcher raised {type(exc).__name__}: {exc}"
            ),
        }

    # ``result.success`` is the canonical execution signal.
    # ``DispatcherResult`` is the strict-AND of three phases so
    # the runtime can read ``executed = bool(result.success)``.
    if result is None:
        return {
            "executed": False,
            "reason": (
                f"dispatcher returned None for command_id={command_id}; "
                "refusing to treat absence of a return value as success"
            ),
        }
    success_attr = getattr(result, "success", None)
    # ``DispatcherResult.success`` is a ``@property``; the legacy
    # pre-hotfix dispatcher returned either ``None`` or the bool
    # directly. Read either form defensively so the canonical
    # contract is the new ``DispatcherResult``.
    if isinstance(success_attr, property):
        # New canonical shape: read the property.
        try:
            success = bool(getattr(result, "success"))
        except Exception:  # noqa: BLE001
            success = False
    else:
        success = bool(success_attr) if success_attr is not None else bool(result)
    if not success:
        reason = getattr(result, "error", None) or (
            f"dispatcher returned {result!r}; command_id={command_id}"
        )
        return {
            "executed": False,
            "reason": f"dispatcher refused; {reason}",
            "command_id": command_id,
            "evidence": {
                "event_id": event_id,
                "needs_charging_from_grid_kwh": needs_grid,
                "output_priority": output_priority,
                "charger_priority": charger_priority,
                "dispatcher_readback": getattr(result, "readback", None),
            },
        }

    return {
        "executed": True,
        "command_id": command_id,
        "evidence": {
            "event_id": event_id,
            "needs_charging_from_grid_kwh": needs_grid,
            "output_priority": output_priority,
            "charger_priority": charger_priority,
            "dispatcher_readback": getattr(result, "readback", None),
        },
    }


def _execution_refusal(
    coordinator: Any,
    plan: PlannerPlan,
    *,
    event_id: str,
) -> str | None:
    """Return a refusal reason string, or ``None`` to proceed.

    The same gates the spec test
    ``test_execute_plan_refuses_when_*`` exercises, minus the
    mode check (the caller owns that). Each refusal carries a
    concrete reason so the audit log distinguishes the four
    failure modes.
    """
    if getattr(plan, "blocked", False):
        reason = getattr(plan, "reason_if_blocked", None) or "unknown planner reason"
        return f"plan blocked; {reason}"
    grid_available = _read_grid_available(coordinator)
    if grid_available is None:
        return "grid availability unknown; refusing without explicit reading"
    if grid_available is False:
        return "grid unavailable; cannot dispatch auto-prep from grid"
    pause_iso = _read_pause_until(coordinator)
    if pause_iso:
        try:
            until = datetime.fromisoformat(str(pause_iso))
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if until > now:
                return (
                    f"manual pause until {until.isoformat()}; "
                    "automatic prep is suspended"
                )
        except (TypeError, ValueError):
            pass
    freshness = _read_freshness_seconds(
        coordinator, datetime.now(timezone.utc)
    )
    if freshness > TELEMETRY_STALENESS_LIMIT_SECONDS:
        return (
            f"telemetry stale; {freshness}s > "
            f"{TELEMETRY_STALENESS_LIMIT_SECONDS}s"
        )
    if not _safety_guards_ok(coordinator):
        return "safety guard tripped; automatic prep is suspended"
    return None


# ---------------------------------------------------------------------------
# Public API: bootstrap wire
# ---------------------------------------------------------------------------


def wire_outage_runtime(
    hass: Any,
    entry: Any,
    coordinator: Any,
) -> None:
    """Single bootstrap call used by ``__init__.py``.

    What it does
    ------------
    1. Loads the entry's ``EventStore`` (lazy; never raises).
    2. Registers the per-entry service
       ``powmr_inverter.compute_outage_plan``. The service is
       scoped to the entry's domain and reads the live
       ``EventStore`` to resolve ``event_id``.
    3. Reads the live mode via ``entry.options['outage_mode']``.
       In ``'automatic'`` mode it schedules a 5-minute tick via
       ``async_track_time_interval``. The tick body lives in
       ``_scheduled_tick`` below; it calls
       ``plan_for_event`` + ``execute_plan`` and is gated by
       ``is_automatic_allowed``.
    4. Arranges clean teardown via ``entry.async_on_unload`` so
       HA Core shutdown and entry reload cancel the tick.

    The function never raises; an unexpected error in any
    sub-step is logged and swallowed so the surrounding
    ``async_setup_entry`` always returns ``True``.
    """
    entry_id = getattr(entry, "entry_id", None)
    if not entry_id:
        _LOGGER.warning("outage runtime: entry missing entry_id; skipping wire")
        return

    options = getattr(entry, "options", None) or {}
    try:
        mode = mode_from_options(options)
    except ValueError as exc:
        _LOGGER.warning("outage runtime: invalid mode; defaulting to observation: %s", exc)
        mode = DEFAULT_OUTAGE_MODE

    bundle = _get_bundle(hass, entry_id)

    # Always create a fresh runtime bundle on wire so a reload
    # starts from a clean slate.
    bundle["outage_mode"] = mode
    bundle["outage_coordinator"] = coordinator

    # Service registration (idempotent on reload -- ``async_register``
    # raises ``ServiceAlreadyRegistered`` which we catch).
    try:
        _register_compute_outage_plan_service(hass, entry_id)
    except Exception as exc:  # noqa: BLE001 -- wire never raises
        _LOGGER.warning(
            "outage runtime: compute_outage_plan registration failed: %s", exc
        )

    # Scheduled tick. Only in 'automatic'. 'disabled' / 'observation'
    # never register the interval.
    if mode == OUTAGE_MODE_AUTOMATIC:
        try:
            unsub = _register_scheduled_tick(hass, entry_id, interval_seconds=DEFAULT_TICK_INTERVAL_SECONDS)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "outage runtime: scheduled tick registration failed: %s", exc
            )
            unsub = None
        if unsub is not None:
            bundle["outage_tick_unsub"] = unsub
            # Cancellation hook for reload / unload.
            try:
                entry.async_on_unload(unsub)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug(
                    "outage runtime: async_on_unload failed (non-fatal): %s", exc
                )

    _LOGGER.info(
        "outage runtime: wired entry=%s mode=%s", entry_id, mode
    )


# ---------------------------------------------------------------------------
# Scheduled tick (Phase 3)
# ---------------------------------------------------------------------------


def _register_scheduled_tick(
    hass: Any,
    entry_id: str,
    *,
    interval_seconds: int,
) -> Optional[Callable[[], None]]:
    """Register a 5-minute tick via HA's ``async_track_time_interval``.

    The tick body is ``_scheduled_tick``. The cancellation handle
    is returned; the caller is responsible for calling it on
    unload.

    The function is defined to live in this module so the wire
    is self-contained: ``__init__.py`` only has to call
    ``wire_outage_runtime`` and the rest is in here.
    """
    try:
        interval = int(interval_seconds)
    except (TypeError, ValueError):
        interval = DEFAULT_TICK_INTERVAL_SECONDS
    if interval < MIN_TICK_INTERVAL_SECONDS:
        interval = MIN_TICK_INTERVAL_SECONDS

    # Lazy HA import -- the function is never reached in unit tests
    # that do not have HA on sys.path.
    from homeassistant.helpers.event import async_track_time_interval

    async def _handle(now: datetime) -> None:
        await _scheduled_tick(hass, entry_id, now)

    # The HA API expects ``interval=timedelta(...)``; passing
    # ``seconds=...`` raises ``TypeError`` at registration time
    # and silently breaks the runtime in automatic mode. The
    # returned call is the cancellation handle; the wire stores
    # it so ``entry.async_on_unload`` can release the listener
    # on reload / shutdown.
    unsub = async_track_time_interval(
        hass, _handle, interval=timedelta(seconds=interval)
    )
    return unsub


async def _scheduled_tick(
    hass: Any,
    entry_id: str,
    now: datetime,
) -> None:
    """Single scheduled tick. Never raises.

    Reads the live mode + event store at tick time so a mode
    change lands without restarting the interval. The body is
    broken into small helpers so the test can drive each branch
    deterministically.
    """
    bundle = _get_bundle(hass, entry_id)
    coordinator = bundle.get("outage_coordinator")
    if coordinator is None:
        return

    options = _read_options_for_entry(hass, entry_id)
    try:
        mode = mode_from_options(options)
    except ValueError as exc:
        _LOGGER.debug("outage tick: invalid mode; treating as observation: %s", exc)
        mode = DEFAULT_OUTAGE_MODE

    # Belt-and-braces: 'disabled' ticks are a no-op even if the
    # interval was registered before a mode flip.
    if mode == OUTAGE_MODE_DISABLED:
        return

    # Resolve the active event. If none, nothing to do.
    event = _find_active_event(hass, entry_id, now)
    if event is None:
        return

    # Build the plan. The snapshot+planner pair is the same
    # call the dashboard makes; the tick is just doing it on
    # the operator's behalf.
    try:
        plan = plan_for_event(coordinator, hass, event)
    except Exception as exc:  # noqa: BLE001 -- tick never raises
        _LOGGER.debug("outage tick: plan_for_event raised: %s", exc)
        return

    # Observation: log only, never dispatch.
    if mode == OUTAGE_MODE_OBSERVATION:
        _LOGGER.info(
            "outage tick OBSERVATION (entry=%s event=%s blocked=%s reason=%s)",
            entry_id,
            getattr(event, "id", None),
            getattr(plan, "blocked", None),
            getattr(plan, "reason_if_blocked", None),
        )
        return

    # Automatic: in-line backstop for the prep window. The
    # planner's prep_start_iso is the source of truth; the
    # backstop catches planner outputs that do not surface a
    # prep_start_iso (older planner builds).
    if not _is_in_prep_window(event, plan, now):
        return

    # Pre-flight gate.
    allowed, reason = is_automatic_allowed(coordinator, plan)
    if not allowed:
        _LOGGER.info(
            "outage tick: auto-prep refused (entry=%s event=%s reason=%s)",
            entry_id,
            getattr(event, "id", None),
            reason,
        )
        return

    # Dispatch. The wrapper returns a dict; we just log the
    # outcome.
    result = await execute_plan(coordinator, event, plan)
    if result.get("executed"):
        _LOGGER.info(
            "outage tick: dispatched (entry=%s event=%s command_id=%s)",
            entry_id,
            getattr(event, "id", None),
            result.get("command_id"),
        )
    else:
        _LOGGER.warning(
            "outage tick: dispatch refused (entry=%s event=%s reason=%s)",
            entry_id,
            getattr(event, "id", None),
            result.get("reason"),
        )


# ---------------------------------------------------------------------------
# Service registration
# ---------------------------------------------------------------------------


def _register_compute_outage_plan_service(hass: Any, entry_id: str) -> None:
    """Register the per-entry ``compute_outage_plan`` service.

    The service takes an ``event_id`` (required) and an optional
    ``force_recompute`` flag. It returns a dict suitable for
    surfacing in the HA developer-tools "Call Service" panel.
    The service is registered under the integration's DOMAIN
    with a per-entry name suffix so multiple loaded entries do
    not collide; HA's service registry is global per domain.

    HA service contract:
      * ``schema`` -- a ``vol.Schema`` describing the accepted
        payload. Required for HA to validate the ``return_response=True``
        ``service_call`` flow.
      * ``supports_response=True`` -- the service must return a
        serialisable dict so HA can surface it in the
        "Call Service" developer-tools panel. Without this, the
        panel rejects the call with HTTP 400 "service does not
        support responses" (DEFECT 7).
    """
    from homeassistant.core import ServiceCall

    domain = _read_domain()
    service_name = f"{entry_id}_{COMPUTE_OUTAGE_PLAN_SERVICE}"

    async def _handle(call: ServiceCall) -> dict:
        event_id = call.data.get("event_id")
        if not event_id:
            return {"planned": False, "reason": "event_id is required"}
        force = bool(call.data.get("force_recompute", False))

        event = _get_event_by_id(hass, entry_id, event_id)
        if event is None:
            return {"planned": False, "reason": f"event {event_id!r} not found"}

        status = getattr(event, "status", None)
        if status != "confirmed" and not force:
            return {
                "planned": False,
                "reason": (
                    f"event not confirmed (status={status!r}); "
                    "pass force_recompute=true to plan anyway"
                ),
            }

        bundle = _get_bundle(hass, entry_id)
        coordinator = bundle.get("outage_coordinator")
        if coordinator is None:
            return {"planned": False, "reason": "coordinator not bound"}

        try:
            plan = plan_for_event(coordinator, hass, event)
        except Exception as exc:  # noqa: BLE001 -- service handler never raises
            return {"planned": False, "reason": f"planner raised: {exc}"}

        payload = plan.to_dict()
        payload["planned"] = not bool(getattr(plan, "blocked", False))
        return payload

    # Schema -- validate the call payload up front.
    schema: vol.Schema | None = None
    try:
        import voluptuous as vol  # type: ignore
        schema = vol.Schema(
            {
                vol.Required("event_id"): str,
                vol.Optional("force_recompute", default=False): bool,
            }
        )
    except Exception:  # noqa: BLE001
        # HA/``voluptuous`` is unavailable in the unit-test env --
        # register without schema (the runtime still accepts a
        # plain dict at the handler boundary). The production
        # service registration path uses the real ``vol.Schema``.
        schema = None

    # Idempotent re-registration. The HA service registry raises
    # ``ServiceAlreadyRegistered`` if a previous wire already
    # registered the same name; we catch it so a reload does
    # not crash setup.
    try:
        if schema is None:
            hass.services.async_register(domain, service_name, _handle)
        else:
            hass.services.async_register(
                domain,
                service_name,
                _handle,
                schema=schema,
                supports_response=True,
            )
    except Exception as exc:  # noqa: BLE001
        # ``ServiceAlreadyRegistered`` is the only expected race;
        # any other exception we log and swallow.
        if "already" not in str(exc).lower():
            raise
        _LOGGER.debug(
            "outage runtime: service %s already registered; reusing", service_name
        )


# ---------------------------------------------------------------------------
# Internal helpers (used by the public API)
# ---------------------------------------------------------------------------


def _read_options(
    coordinator: Any,
    *,
    entry_id: str | None = None,
) -> dict[str, Any]:
    """Return the entry options as a plain dict, defaulting to ``{}``.

    Tolerant read: ``coordinator.options`` first, then
    ``coordinator._entry.options``, then the live
    ``hass.config_entries.async_get_entry(entry_id).options`` if
    ``entry_id`` is supplied and HA is on sys.path.
    """
    opts = _safe_getattr(coordinator, "options")
    if isinstance(opts, dict):
        return opts
    entry = _safe_getattr(coordinator, "_entry")
    if entry is not None:
        opts = getattr(entry, "options", None)
        if isinstance(opts, dict):
            return opts
    if entry_id:
        opts = _read_options_for_entry(None, entry_id)
        if isinstance(opts, dict):
            return opts
    return {}


def _read_options_for_entry(
    hass: Any | None,
    entry_id: str,
) -> dict[str, Any]:
    """Read entry options via HA's config entry registry (when available)."""
    if hass is None:
        return {}
    getter = getattr(hass, "config_entries", None)
    if getter is None:
        return {}
    try:
        entry = getter.async_get_entry(entry_id)
    except Exception:  # noqa: BLE001 -- never raise
        return {}
    if entry is None:
        return {}
    opts = getattr(entry, "options", None)
    return opts if isinstance(opts, dict) else {}


def _read_domain() -> str:
    """Return the integration DOMAIN string. Lazy import: keep tests happy."""
    try:
        from ..const import DOMAIN  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        try:
            from . import const as _const  # type: ignore[import-not-found]
            DOMAIN = getattr(_const, "DOMAIN", "powmr_inverter")
        except Exception:  # noqa: BLE001
            DOMAIN = "powmr_inverter"
    return DOMAIN


def _get_bundle(hass: Any, entry_id: str) -> dict[str, Any]:
    """Return the per-entry bundle, creating it on demand."""
    if hass is None:
        return {}
    domain = _read_domain()
    data = hass.data.setdefault(domain, {})
    return data.setdefault(entry_id, {})


def _get_event_by_id(
    hass: Any, entry_id: str, event_id: str
) -> Any | None:
    """Return the event with ``id == event_id`` from the store, or ``None``.

    Uses the store loaded by ``wire_outage_runtime``. The store
    is HA-flavored in production; in tests we accept any object
    that exposes a ``get(event_id)`` method.
    """
    bundle = _get_bundle(hass, entry_id)
    store = bundle.get("outage_store")
    if store is None:
        # Lazy creation: the production path stores the EventStore
        # under ``hass.data[<DOMAIN>_outage_stores][entry_id]``;
        # the bootstrap registers it on first service call. If
        # the test path skipped that, fall back to the
        # EventStore's convenience in-memory ctor.
        store = _try_get_or_create_store(hass, entry_id)
        if store is not None:
            bundle["outage_store"] = store
    if store is None:
        return None
    try:
        get = getattr(store, "get", None)
        if callable(get):
            return get(event_id)
    except Exception:  # noqa: BLE001
        return None
    return None


def _find_active_event(
    hass: Any, entry_id: str, now: datetime
) -> Any | None:
    """Return the first ``confirmed`` event whose prep window or window contains ``now``.

    Loop policy (R0.2 follow-up):
      * Iterate the ``EventStore`` directly (so the production
        semantics — ``iter_active`` over non-terminal events —
        is exercised). We additionally require ``status == 'confirmed'``;
        a ``pending`` event whose prep window happens to be open
        must NOT be picked for dispatch (the audit gate is
        confirmation, not the calendar).
      * For each candidate, compute the prep-window start as
        ``event.start - prep_window_hours`` (default 4h). The
        planner's own ``prep_start_iso`` (if populated) takes
        precedence.
      * The event is eligible iff ``now >= prep_window_start``
        AND ``now < event.end``.
      * Pending / cancelled events are excluded. Cancelled
        events are excluded even when the window is still open
        in the calendar — operator intent is to abort.
    """
    bundle = _get_bundle(hass, entry_id)
    store = bundle.get("outage_store")
    if store is None:
        store = _try_get_or_create_store(hass, entry_id)
        if store is not None:
            bundle["outage_store"] = store
    if store is None:
        return None

    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)

    for ev in _iter_all_events(store):
        status = getattr(ev, "status", None)
        # Production contract: only confirmed events are eligible
        # for dispatch. Pending is plan-only (services/outage.py:529-533);
        # cancelled / completed are terminal (services/outage.py:531-344).
        if status != "confirmed":
            continue
        # Phase 4 v2: events that were rescheduled carry a
        # ``superseded_by`` link to the replacement event. The
        # replacement event is the one that should be acted on;
        # the old event stays in the store for audit but is
        # EXCLUDED from runtime selection. Picking the old
        # event would dispatch against a window the operator
        # already corrected.
        if getattr(ev, "superseded_by", None) is not None:
            continue
        if _event_in_eligible_window(ev, now_utc):
            return ev
    return None


def _event_in_eligible_window(event: Any, now_utc: datetime) -> bool:
    """True iff ``now`` is in the event's prep window or active window."""
    start = _safe_parse_iso(getattr(event, "start_iso", None))
    end = _safe_parse_iso(getattr(event, "end_iso", None))
    if start is None or end is None or end <= start:
        return False
    # Prep window opens 4h before the event start (engine/code
    # backstop: planner's own prep_start_iso is the source of
    # truth, but older planner builds do not populate it).
    prep_start = start - timedelta(hours=PREP_BACKSTOP_HOURS)
    # Check the planner-supplied prep_start_iso first. The
    # ``_is_in_prep_window`` helper already does this; we
    # re-implement a minimal version here so an event whose
    # planner-side prep window opens earlier than the 4h
    # backstop is still surfaced. Plain attribute access —
    # any planner-shaped object with a ``plan`` dict will do.
    plan_dict = getattr(event, "_plan_dict", None) or getattr(
        event, "plan", None
    ) or {}
    if isinstance(plan_dict, dict):
        planner_prep = _safe_parse_iso(plan_dict.get("prep_start_iso"))
        if planner_prep is not None and planner_prep < prep_start:
            prep_start = planner_prep
    return prep_start <= now_utc < end


def _iter_all_events(store: Any) -> list[Any]:
    """Return all events in the store, preferring ``list_events()``.

    ``EventStore.list_events()`` (outage.py:447) returns every
    event regardless of status; the caller filters. We do NOT
    use ``iter_active()`` (which excludes terminal events)
    because we need to see cancelled events so we can prove
    they are excluded by status, not by accident of being
    terminal.
    """
    list_events = getattr(store, "list_events", None)
    if callable(list_events):
        try:
            events = list_events()
        except Exception:  # noqa: BLE001
            events = None
        if events is not None:
            return _iter_safely(events)
    iter_active = getattr(store, "iter_active", None)
    if callable(iter_active):
        try:
            active_iter = iter_active()
        except Exception:  # noqa: BLE001
            return []
        return _iter_safely(active_iter)
    return []


def _safe_parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _iter_safely(value: Any) -> list[Any]:
    """Coerce a value to a list of items, swallowing any iteration error.

    The store's ``iter_active()`` returns an ``Iterable[OutageEvent]``
    in production and ``list_events()`` returns a list. Tests
    pass mock objects that may not be iterable. A bare ``list()``
    call on a non-iterable raises ``TypeError``; this helper
    returns ``[]`` instead so the surrounding loop is a no-op.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    try:
        return list(value)
    except TypeError:
        return []


def _try_get_or_create_store(hass: Any, entry_id: str) -> Any | None:
    """Return the live store, or build a no-HA in-memory one for tests.

    In production this is the EventStore registered by
    ``async_register_outage_services``; in unit tests we do not
    have HA, so we fall back to ``EventStore.in_memory``.
    """
    if hass is not None:
        domain = _read_domain()
        stores_map = hass.data.get(f"{domain}_outage_stores", {}) if hasattr(hass, "data") else {}
        if isinstance(stores_map, dict) and entry_id in stores_map:
            return stores_map[entry_id]
    # Test path: try to construct the in-memory store. The
    # outage module imports HA at module load, so this may
    # raise; we swallow and return None so the caller can no-op.
    try:
        from .outage import EventStore  # noqa: F401 -- may import HA
        return EventStore.in_memory(entry_id=entry_id)
    except Exception:  # noqa: BLE001
        return None


# Sentinel used by the freshness gate when the canonical
# coordinator timestamp is missing or the last poll failed.
# The HA DocTells us "telemetry stale" must refuse automatic
# dispatch (DEFECT 3). We return a value well above
# ``TELEMETRY_STALENESS_LIMIT_SECONDS`` so the caller falls
# through to the refusal branch.
_FRESHNESS_REFUSE = 10**9


def _read_freshness_seconds(coordinator: Any, now: datetime) -> int:
    """Return seconds since the last successful poll.

    Canonical timestamp (production contract):
      * ``coordinator.api.last_realtime_success_received_at``
        (api.py:201) -- datetime set on the last successful poll.
        This is the SOURCE OF TRUTH. We do NOT fall back to
        ``coordinator.last_update_time`` because the HA
        ``DataUpdateCoordinator`` base class only sets that
        timestamp on a successful update; a failed poll leaves
        it stale or None, and the runtime cannot distinguish
        "no poll yet" from "poll just succeeded". The api
        attribute is updated in lockstep with the inverter
        response, so it is the only safe source.

    Refusal semantics
      * When ``coordinator.last_update_success`` is ``False`` we
        return ``_FRESHNESS_REFUSE`` so the gate never silently
        passes on a failed poll.
      * When the canonical api timestamp is ``None`` we return
        ``_FRESHNESS_REFUSE``; an unknown freshness must NOT
        default to "fresh" (the bug Codex reproduced).
    """
    success = getattr(coordinator, "last_update_success", None)
    if success is False:
        # Failed poll -- refuse.
        return _FRESHNESS_REFUSE
    api = getattr(coordinator, "api", None)
    ts = _safe_getattr(api, "last_realtime_success_received_at")
    if ts is None:
        # No canonical timestamp. Production contract: refuse.
        return _FRESHNESS_REFUSE
    if not isinstance(ts, datetime):
        try:
            ts = datetime.fromisoformat(str(ts))
        except (TypeError, ValueError):
            return _FRESHNESS_REFUSE
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = now - ts.astimezone(timezone.utc)
    return int(max(0, delta.total_seconds()))


def _read_calibrator_metrics(
    coordinator: Any,
) -> tuple[float | None, int | None]:
    """Read ``confidence_factor`` and ``sample_count`` from the calibrator."""
    controller = _safe_getattr(coordinator, "_hems")
    if controller is None:
        return None, None
    calibrator = _safe_getattr(controller, "_predictive_controller", "calibrator")
    if calibrator is None:
        return None, None
    metrics = None
    metrics_callable = getattr(calibrator, "metrics", None)
    if callable(metrics_callable):
        try:
            metrics = metrics_callable()
        except Exception:  # noqa: BLE001
            metrics = None
    if metrics is None:
        return None, None
    confidence = _as_float(_safe_getattr(metrics, "confidence_factor"))
    samples_raw = _safe_getattr(metrics, "sample_count")
    try:
        samples = int(samples_raw) if samples_raw is not None else None
    except (TypeError, ValueError):
        samples = None
    return confidence, samples


def _read_consumption_history(coordinator: Any) -> dict[str, float] | None:
    """Return the ``{date_str: kwh}`` consumption-history dict, or ``None``."""
    hems = _safe_getattr(coordinator, "_hems")
    if hems is None:
        return None
    raw = _safe_getattr(
        hems, "_consumption_history_with_dates", "_consumption_history"
    )
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple, dict)):
        return None
    out: dict[str, float] = {}
    items = raw.items() if isinstance(raw, dict) else raw
    for item in items:
        try:
            if isinstance(item, tuple) and len(item) == 2:
                date_key, kwh_value = item
            else:
                date_key = getattr(item, "date", None) or getattr(item, "date_str", None)
                kwh_value = getattr(item, "kwh", None)
            if date_key is None or kwh_value is None:
                continue
            out[str(date_key)] = float(kwh_value)
        except (TypeError, ValueError, AttributeError):
            continue
    return out or None


def _read_pause_until(coordinator: Any) -> str | None:
    """Return ``_hems._manual_override_until`` as an offset-aware ISO string."""
    hems = _safe_getattr(coordinator, "_hems")
    if hems is None:
        return None
    until = _safe_getattr(hems, "_manual_override_until")
    if until is None or not isinstance(until, datetime):
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until.isoformat()


def _read_grid_available(coordinator: Any) -> bool | None:
    """Return the live ``grid_available`` reading.

    Returns ``None`` when the field is absent (DEFECT 3). The
    upstream callers (``is_automatic_allowed`` /
    ``_execution_refusal``) treat ``None`` as "unknown"
    and refuse automatic dispatch; the previous version
    defaulted ``True``, which let a missing reading bypass
    the grid-down gate.

    The dispatch reads ``coordinator.data['gridAvailable']``
    (coordinator.py:1161). The legacy ``grid_available`` key
    is also accepted for back-compat.
    """
    data = getattr(coordinator, "data", None)
    if not isinstance(data, dict):
        return None
    raw = _safe_dict_get(data, "gridAvailable", "grid_available")
    if raw is None:
        return None
    return bool(raw)


def _read_telemetry_from_plan(plan: PlannerPlan) -> Telemetry:
    """Reconstruct the Telemetry that fed the plan, or return a minimal stub.

    The runtime does not store a back-reference to the Telemetry
    on the PlannerPlan (the planner's dataclass does not have
    one). For the gate we only need a handful of fields; if the
    caller did not attach the snapshot via ``plan.evidence`` or
    similar, we return a fresh zero-freshness ``Telemetry`` with
    the fields we can reconstruct from the plan dict.
    """
    plan_dict = getattr(plan, "plan", {}) or {}
    grid = plan_dict.get("grid_available")
    if grid is None:
        grid = True
    freshness = plan_dict.get("telemetry_freshness_seconds", 0)
    pause = plan_dict.get("current_pause_until_iso")
    return Telemetry(
        now_iso=getattr(plan, "computed_at_iso", datetime.now(timezone.utc).isoformat()),
        telemetry_freshness_seconds=int(freshness or 0),
        grid_available=bool(grid),
        current_pause_until_iso=pause,
    )


def _safety_guards_ok(coordinator: Any) -> bool:
    """Return ``True`` iff the real HEMS guards permit a write.

    Phase 2C/3 follow-up (Codex DEFECT C): the runtime previously
    re-derived the engine guards on its own (circuit breaker,
    manual override, anti-flapping) with logic that drifted from
    ``hems.engine._evaluation_hold``. The canonical guard now
    lives on the engine as
    ``HemsEngine.can_dispatch_outage_decision`` -- both the
    runtime pre-flight gate (``is_automatic_allowed``) and the
    protected dispatcher
    (``InverterCoordinator._execute_hems_command``) call the SAME
    method so the two cannot disagree on what is safe.

    This function remains a thin wrapper around
    ``can_dispatch_outage_decision`` so older call sites that
    read its boolean do not have to change. The tuple version is
    the canonical surface.
    """
    allowed, _reason = _can_dispatch_decision(coordinator, decision=None)
    return bool(allowed)


def _can_dispatch_decision(
    coordinator: Any, *, decision: Any = None
) -> tuple[bool, str]:
    """Return ``(can_dispatch, concrete_reason)`` for the canonical guard.

    Phase 2C/3 follow-up (Codex DEFECT C): both the pre-flight
    gate and the protected dispatcher must agree on what is
    safe. The single source of truth is
    ``HemsEngine.can_dispatch_outage_decision`` (engine.py:580).
    Older builds / test doubles without the method on the engine
    fall through to ``True`` (the runtime used to do its own
    range checks; we keep that legacy behaviour for back-compat
    with the pre-hotfix tests).
    """
    hems = _safe_getattr(coordinator, "_hems")
    if hems is None:
        return (False, "hems not bound; refusing to dispatch")
    fn = getattr(hems, "can_dispatch_outage_decision", None)
    if not callable(fn):
        # Old engine; fall back to the pre-hotfix boolean chain.
        return (
            (True, "")
            if _legacy_safety_guards_ok(hems)
            else (False, "safety guard tripped; automatic prep is suspended"),
        )
    try:
        return tuple(fn(decision))
    except TypeError:
        return tuple(fn())
    except Exception as exc:  # noqa: BLE001 -- defensive
        return (False, f"hems guard raised {type(exc).__name__}: {exc}")


def _legacy_safety_guards_ok(hems: Any) -> bool:
    """Pre-hotfix guard chain kept for ``FakeHems`` without ``can_dispatch_*``.

    The original runtime re-derived the engine's circuit breaker,
    manual override and anti-flapping checks with logic that
    drifted from ``hems.engine._evaluation_hold`` (DEFECT C).
    Production paths always go through
    ``can_dispatch_outage_decision``; this helper is only here
    so older test doubles still satisfy the boolean surface.
    """
    now = datetime.now(timezone.utc)

    # Circuit breaker (engine.py:510-511 / 788-818).
    blocked_until = getattr(hems, "_blocked_until", None)
    if isinstance(blocked_until, datetime):
        if blocked_until.tzinfo is None:
            blocked_until = blocked_until.replace(tzinfo=timezone.utc)
        if blocked_until > now:
            return False

    # Manual override (engine.py:512-517 / 720-786).
    manual_until = getattr(hems, "_manual_override_until", None)
    if isinstance(manual_until, datetime):
        if manual_until.tzinfo is None:
            manual_until = manual_until.replace(tzinfo=timezone.utc)
        if manual_until > now:
            return False

    # Anti-flapping dedup window: a recent same-target write is
    # silently dropped at the dispatcher (engine.py:1359-1373).
    # Surface this as a guard trip so the runtime refuses to claim
    # a dispatch that the dispatcher would absorb.
    dedup_window = getattr(hems, "_command_dedup_window", None)
    if not isinstance(dedup_window, timedelta):
        dedup_window = timedelta(seconds=30)
    last_output_at = getattr(hems, "_last_cmd_output_at", None)
    last_charger_at = getattr(hems, "_last_cmd_charger_at", None)
    for last_at in (last_output_at, last_charger_at):
        if isinstance(last_at, datetime):
            if last_at.tzinfo is None:
                last_at = last_at.replace(tzinfo=timezone.utc)
            if (now - last_at) < dedup_window:
                return False
    return True


def _is_in_prep_window(
    event: Any,
    plan: PlannerPlan,
    now: datetime,
) -> bool:
    """Return ``True`` iff the operator should start prepping now.

    Source of truth: ``plan.plan['prep_start_iso']`` when the
    planner populated it. Backstop: ``now >= event.start - 4h``
    so older planner builds that did not populate the field
    still trigger prep during the operator-ratified 4-hour
    window.
    """
    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    plan_dict = getattr(plan, "plan", {}) or {}
    prep_iso = plan_dict.get("prep_start_iso")
    if isinstance(prep_iso, str) and prep_iso:
        prep_dt = _safe_parse_iso(prep_iso)
        if prep_dt is not None and now_utc >= prep_dt:
            return True
    # Backstop.
    start_dt = _safe_parse_iso(getattr(event, "start_iso", None))
    if start_dt is None:
        return False
    return now_utc >= (start_dt - timedelta(hours=PREP_BACKSTOP_HOURS))


async def _call_execute_hems_command(
    execute_fn: Callable[..., Awaitable[Any]],
    decision: Any,
    command_id: str,
    *,
    evidence_recorded: bool,
) -> Any:
    """Call the protected dispatcher with positional/keyword-tolerant signature.

    Returns the dispatcher's return value (production: ``None`` on
    success; tests may return ``False`` to model a refusal).

    Production signature (coordinator.py:1644-1707)::

        async def _execute_hems_command(
            self,
            decision: HemsDecision,
            command_id: str | None = None,
            *,
            evidence_recorded: bool = False,
        ) -> None: ...

    We accept both positional and keyword forms so the
    scheduler / tests can drive the call with either style.
    """
    try:
        result = await execute_fn(
            decision, command_id, evidence_recorded=evidence_recorded
        )
        return result
    except TypeError:
        # Older signature -- try without the keyword.
        result = await execute_fn(decision, command_id)
        return result
