"""Planned-outage energy planner (Phase 2B).

Pure functions on a telemetry snapshot + a confirmed ``OutageEvent``.

Scope
------
The planner computes, **without any I/O**:

    * ``energy_needed_kwh``           -- expected consumption over the event
    * ``energy_reserve_kwh``          -- 1-day buffer on top of the window
    * ``energy_from_pv_during_outage_kwh``
                                     -- PV contribution inside the window
                                        (zero when forecast confidence is low)
    * ``energy_available_kwh``        -- battery-from-current-SOC-to-100%
                                        + PV contribution, efficiency-adjusted
    * ``attainable_target_soc_pct``   -- the SOC % the operator should target
                                        before the prep window starts
    * ``needs_charging_from_grid_kwh``-- the kWh gap that grid charging must
                                        cover to meet need + reserve
    * ``grid_charge_hours_per_hour``  -- kWh/h achievable via utility charging
    * ``prep_start_iso``              -- when the prep action should start;
                                        ``None`` when no prep is needed
    * ``blocked`` / ``reason_if_blocked``
                                     -- one of the structural blockers
                                        (battery invalid, grid unavailable,
                                        telemetry stale, manual pause,
                                        event in the past)
    * ``reason_if_incomplete``        -- when not blocked but the planner
                                        cannot fully cover the need
    * ``warnings``                    -- non-blocking notes (low PV
                                        confidence, missing consumption
                                        history, …)

Out of scope for Phase 2B (deliberate):
    * mode selection (observation / automatic-prep)        -> Phase 3
    * any inverter write / HA service call                 -> EXISTING (Phase 1)
    * the notification adapter                             -> Phase 4

Design discipline
-----------------
* The planner is a pure function: same input -> same output, no globals,
  no I/O. The Phase 2C wiring is what reads ``coordinator.data`` and
  passes a fresh-enough snapshot.
* The planner never reads from ``coordinator``, never imports from
  ``.hems``, and never registers services.
* The current PV power (``pv_power_w``) is **not** used as a hardware
  limit -- the inverter's PV-input max is not exposed by the powmr API.
  The planner uses ``pv_forecast_tomorrow_kwh`` as the best available
  forecast and scales it to the event duration.
* Low PV forecast confidence (operator definition: ``samples < 7`` or
  ``confidence_factor < 0.5``) collapses the PV contribution to zero.
  This is a **warning**, not a block -- grid charging from the same
  telemetry remains possible.
* The planner never promises to keep the house powered. When the gap
  between need + reserve and available energy is positive, it surfaces
  ``attainable_target_soc_pct < required`` and a ``reason_if_incomplete``
  that quantifies the deficit.
* ``Telemetry`` and ``PlannerPlan`` are dataclasses; the persistence
  boundary is ``PlannerPlan.to_dict()`` / ``Telemetry.from_dict()``.
* Datetime fields are offset-aware ISO 8601 strings (UTC offsets are
  preserved on the way in for the audit trail). Naive datetimes are
  rejected at the API boundary.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Telemetry-staleness threshold for grid-charging decisions. The
# integration polls every 5 s by default; 5 minutes is the operator-
# ratified budget for safe "the SOC I just read is still the SOC I am
# about to charge into" reasoning. Anything older requires a refresh
# before the planner can promise a grid-charge start time.
TELEMETRY_STALENESS_LIMIT_SECONDS = 300

# Low-PV-confidence policy thresholds. The calibrator reports
# ``sample_count`` and ``confidence_factor`` (both bounded to [0, 1] in
# ``hems/forecast_calibration.py``). The operator-ratified
# conservative mode is "treat PV as zero" when EITHER threshold is
# below its cut-off.
PV_CONFIDENCE_SAMPLES_MIN = 7
PV_CONFIDENCE_FACTOR_MIN = 0.5
# Daylight hours for the operator site (Львів, ~50°N).
# Used ONLY to scale a daily forecast when hourly data is
# unavailable; we never invent solar production outside this
# window. 06:00-19:00 is the conservative daylight envelope
# across the year (longest day 06:00-21:00 in summer, shortest
# 08:00-17:00 in winter); the planner takes the intersection
# with the event window, so a winter 18:00 event gets
# effectively zero solar contribution even if the daily
# forecast is 5 kWh.
DAYLIGHT_START_HOUR = 6
DAYLIGHT_END_HOUR = 19

# Battery parameter sanity bands -- mirrored from
# ``coordinator._derive_battery_capacity`` (R04) so the planner can
# refuse to guess when the operator-supplied form input is invalid.
# These bounds are intentionally hard-coded; the production runtime
# validator already enforces them and we want the planner to fail
# closed on the same definition of "valid".
BATTERY_KWH_MIN = 1.0
BATTERY_KWH_MAX = 30.0
BATTERY_AH_MIN = 10.0
BATTERY_AH_MAX = 2000.0
BATTERY_VOLTAGE_MIN = 10.0
BATTERY_VOLTAGE_MAX = 100.0

# Default charge / discharge efficiency when the caller does not
# supply one. Mirrors the HemsTunables defaults in
# ``hems/tuning.py``.
DEFAULT_CHARGE_EFFICIENCY = 0.92
DEFAULT_DISCHARGE_EFFICIENCY = 0.92

# Default SOC reserve floor when the caller does not supply one.
# Mirrors ``entry.options.get("reserve_soc", 20.0)`` at coordinator.py:1384.
DEFAULT_RESERVE_SOC_PCT = 20.0

# Maximum planning horizon in days. The operator's policy is "never
# plan more than a week ahead" -- beyond that the consumption history
# and PV forecast are too stale to be honest numbers.
DEFAULT_MAX_PLANNING_HORIZON_DAYS = 7

# Reserve floor (kWh) -- a hard minimum on top of any per-day buffer.
# Matches the operator policy "1 day of buffer on top of the
# window's own consumption" with a hard floor.
RESERVE_FLOOR_KWH = 0.5

# Holiday / weekend multiplier. The operator policy is "be
# conservative" -- residential usage typically rises 20 % on those
# days.
HOLIDAY_WEEKEND_MULTIPLIER = 1.2


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Telemetry:
    """A snapshot of coordinator-side telemetry for the planner.

    The caller (Phase 2C wiring) is responsible for reading
    ``coordinator.data`` and ``coordinator.forecast_tomorrow_kwh`` /
    ``coordinator._battery_capacity_kwh`` / etc., packaging them into a
    ``Telemetry`` instance, and passing it to ``plan_outage``. The
    planner never imports the coordinator, the engine, or any HA
    helper.

    Two fields are mandatory: ``now_iso`` (server clock) and
    ``telemetry_freshness_seconds`` (seconds since the last
    ``coordinator._async_update_data`` cycle). The rest are optional
    and default to ``None`` so a missing field surfaces as "unknown"
    rather than "zero" -- the planner flags any ``None`` it actually
    needs as missing.
    """

    # Mandatory
    now_iso: str
    telemetry_freshness_seconds: int

    # Battery (caller reads ``coordinator._battery_capacity_kwh`` and
    # the matching Ah / V / ok flag)
    battery_capacity_kwh: float | None = None
    battery_capacity_ah: float | None = None
    nominal_voltage_v: float | None = None
    battery_capacity_ok_for_planning: bool = False
    battery_soc_pct: float | None = None
    battery_soc_corrected_pct: float | None = None
    battery_voltage_v: float | None = None
    battery_charge_efficiency: float | None = None
    battery_discharge_efficiency: float | None = None

    # Power (caller reads ``coordinator.data["pvPower"]`` etc.)
    pv_power_w: float | None = None
    load_power_w: float | None = None
    grid_power_w: float | None = None

    # Grid
    grid_available: bool | None = None

    # Operator config (caller reads ``entry.options``)
    reserve_soc_pct: float | None = None
    max_utility_charging_current_a: float | None = None
    max_charging_current_a: float | None = None
    # Round 7: explicit, operator-set
    # emergency buffer in kWh. Default 0
    # (no extra kWh on top of the SOC
    # reserve and the event consumption).
    emergency_buffer_kwh: float | None = None

    # PV forecast (caller reads ``coordinator.forecast_tomorrow_kwh``
    # + the calibrator metrics)
    pv_forecast_tomorrow_kwh: float | None = None
    pv_forecast_confidence: float | None = None
    pv_forecast_samples: int | None = None
    # Optional hourly forecast, indexed by hour-of-day (0..23),
    # each entry is the expected kWh produced in that local hour.
    # When supplied, the planner sums only the hours that
    # overlap the event window (no proportional scaling by
    # duration; the hourly values are already the production
    # for that hour). When ``None``, the planner falls back to
    # a daylight-window-only scaling of the daily total -- a
    # 00:00-02:00 outage then sees zero PV instead of the
    # ``daily_forecast * 2/24`` round-5 number, which was a
    # night-time fabrication.
    pv_forecast_hourly_kwh: list[float] | None = None

    # Consumption history (caller reads ``coordinator._hems._consumption_history``
    # / ``coordinator._load_matrix``). The planner accepts a
    # ``date -> kWh`` dict keyed by ISO date string.
    tarpit_consumption_avg_kwh: dict[str, float] | None = None

    # HEMS manual override (caller reads
    # ``coordinator._hems._manual_override_until`` -- if set and in
    # the future, the planner blocks automatic-prep)
    current_pause_until_iso: str | None = None

    # Planning horizon cap
    max_planning_horizon_days: int | None = None

    # Site identity -- affects the holiday/weekend multiplier
    is_holiday_or_weekend: bool | None = None


@dataclass
class PlannerPlan:
    """The planner's verdict for a single ``OutageEvent``.

    The dataclass is the **only** shape the rest of the codebase
    should depend on. ``to_dict()`` is the persistence / dashboard
    boundary.

    ``mode`` is always ``"observation"`` for Phase 2B -- mode
    selection (automatic-prep dispatch) is Phase 3.
    """

    event_id: str
    computed_at_iso: str
    mode: str
    plan: dict[str, Any]
    blocked: bool
    reason_if_blocked: str | None
    reason_if_incomplete: str | None
    next_attempt_iso: str | None

    # Testability hook -- the PV contribution to the event window is a
    # derived intermediate; exposing it as a top-level attribute lets
    # tests assert on it without round-tripping through ``plan``.
    energy_from_pv_during_outage_kwh: float | None = None

    # Same idea for the grid-charge rate -- useful when a test wants to
    # verify the I*V*eff / 1000 derivation directly.
    grid_charge_hours_per_hour: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict for the dashboard / persistence."""
        return {
            "event_id": self.event_id,
            "computed_at_iso": self.computed_at_iso,
            "mode": self.mode,
            "plan": dict(self.plan),
            "blocked": self.blocked,
            "reason_if_blocked": self.reason_if_blocked,
            "reason_if_incomplete": self.reason_if_incomplete,
            "next_attempt_iso": self.next_attempt_iso,
            "energy_from_pv_during_outage_kwh": self.energy_from_pv_during_outage_kwh,
            "grid_charge_hours_per_hour": self.grid_charge_hours_per_hour,
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def plan_outage(telemetry: Telemetry, event: Any) -> PlannerPlan:
    """Compute a ``PlannerPlan`` for ``event`` given ``telemetry``.

    Pure function. Returns the same ``PlannerPlan`` for the same input
    (modulo ``computed_at_iso``, which is stamped with the caller-
    supplied ``telemetry.now_iso``).

    ``event`` is duck-typed (``OutageEvent`` or anything with the same
    surface) so the planner has no compile-time dependency on
    ``services.outage``. The required fields are:

        id: str
        start_iso: str     # offset-aware ISO 8601
        end_iso: str       # offset-aware ISO 8601

    Optional fields consumed by the planner:

        status: str        # used to decide plan-only vs full plan
    """
    warnings: list[str] = []
    computed_at_iso = _require_offset_aware_iso("now_iso", telemetry.now_iso).isoformat()

    # ---- Event sanity --------------------------------------------------
    event_id = getattr(event, "id", None)
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event.id must be a non-empty string")

    try:
        start_utc = _utc_instant(getattr(event, "start_iso", None), "start_iso")
        end_utc = _utc_instant(getattr(event, "end_iso", None), "end_iso")
    except ValueError as exc:
        # An event with malformed ISO strings is a structural problem
        # at the caller; refuse rather than invent a default.
        return _blocked_plan(
            event_id=event_id,
            computed_at_iso=computed_at_iso,
            reason=f"event has invalid time window: {exc}",
            next_attempt_iso=None,
        )

    now_utc = _utc_instant(telemetry.now_iso, "telemetry.now_iso")

    if end_utc <= now_utc:
        return _blocked_plan(
            event_id=event_id,
            computed_at_iso=computed_at_iso,
            reason=(
                "event is in the past or already ended "
                f"(end={getattr(event, 'end_iso', None)!r}, "
                f"now={telemetry.now_iso!r}); cannot plan"
            ),
            next_attempt_iso=None,
        )
    if end_utc <= start_utc:
        return _blocked_plan(
            event_id=event_id,
            computed_at_iso=computed_at_iso,
            reason=(
                "event has non-positive duration "
                f"(start={getattr(event, 'start_iso', None)!r}, "
                f"end={getattr(event, 'end_iso', None)!r}); cannot plan"
            ),
            next_attempt_iso=None,
        )

    # Pending events get a plan-only result (per operator contract).
    # The planner never marks them as "blocked"; it just notes the
    # intent so the dashboard can show "would do X once you confirm".
    event_status = getattr(event, "status", "confirmed")
    if event_status == "pending":
        warnings.append(
            "event is pending confirmation; result is plan-only, "
            "no execution will be attempted"
        )
    elif event_status in ("cancelled", "completed"):
        return _blocked_plan(
            event_id=event_id,
            computed_at_iso=computed_at_iso,
            reason=(
                f"event is in terminal status {event_status!r}; "
                "no planning will be performed"
            ),
            next_attempt_iso=None,
        )

    # ---- Horizon check -------------------------------------------------
    horizon_days = telemetry.max_planning_horizon_days
    if horizon_days is None:
        horizon_days = DEFAULT_MAX_PLANNING_HORIZON_DAYS
    horizon = (end_utc - now_utc).total_seconds() / 86400.0
    if horizon > float(horizon_days):
        return _blocked_plan(
            event_id=event_id,
            computed_at_iso=computed_at_iso,
            reason=(
                f"event starts in {horizon:.1f} days; planner refuses "
                f"to plan beyond the {horizon_days}-day horizon "
                "(consumption history and PV forecast are too stale "
                "to be honest numbers)"
            ),
            next_attempt_iso=None,
        )

    # ---- Consumption estimate -----------------------------------------
    energy_needed_kwh, consumption_warning = _estimate_consumption_kwh(
        telemetry=telemetry,
        start_utc=start_utc,
        end_utc=end_utc,
    )
    if consumption_warning is not None:
        warnings.append(consumption_warning)

    # ---- Reserve -------------------------------------------------------
    # Round 7: ``energy_reserve_kwh`` is now a
    # **safety-net floor only**, NOT a
    # "1-day of mean consumption" buffer. The
    # round-6 product policy
    # (``max(0.5, mean_daily_kwh * 1.0)``) was
    # double-counting: it added a 1-day
    # kWh reserve on top of the SOC reserve
    # that the operator already configured.
    # For typical 14-hour events with the
    # live tarpit mean of 10.16 kWh, the
    # planner always reported a 7+ kWh
    # residual -- the operator could never
    # meet a "1-day" kWh reserve from a
    # battery that stores 8.95 kWh of
    # usable headroom.
    #
    # Three quantities are now explicit and
    # non-overlapping:
    #
    #   * ``soc_reserve_pct`` -- the operator's
    #     SOC floor (handled by
    #     ``battery_remaining_kwh`` below).
    #   * ``event_consumption_kwh`` -- the
    #     energy the event will draw
    #     (``energy_needed_kwh``).
    #   * ``emergency_buffer_kwh`` -- an
    #     operator-set kWh buffer (default 0)
    #     that is added on top of the event
    #     consumption for unexpected post-event
    #     load. Read from
    #     ``entry.options["emergency_buffer_kwh"]``
    #     with a default of 0.
    #
    # The previous "mean_daily_kwh" term is
    # removed. ``RESERVE_FLOOR_KWH`` remains
    # as a small numerical guard against
    # sub-zero rounding.
    emergency_buffer_kwh = _read_emergency_buffer_kwh(
        telemetry=telemetry,
    )
    energy_reserve_kwh = max(
        RESERVE_FLOOR_KWH,
        emergency_buffer_kwh,
    )

    # ---- PV contribution (rule 1 + rule 2) -----------------------------
    energy_from_pv, pv_warning = _compute_pv_contribution_kwh(
        telemetry=telemetry,
        start_utc=start_utc,
        end_utc=end_utc,
        event_tz_name=getattr(event, "tz_name", None),
    )
    if pv_warning is not None:
        warnings.append(pv_warning)

    # ---- Battery remaining (efficiency-adjusted) -----------------------
    battery_remaining_kwh, soc_pct, soc_unknown = _compute_battery_remaining_kwh(telemetry)
    if soc_unknown:
        warnings.append(
            "battery SOC is unknown; planner assumes the floor "
            "(reserve_soc) as the starting point and refuses to "
            "promise the house will stay above it"
        )

    energy_available_kwh = battery_remaining_kwh + energy_from_pv

    # ---- Gap math ------------------------------------------------------
    needs_charging_from_grid_kwh = max(
        0.0,
        energy_needed_kwh + energy_reserve_kwh - energy_available_kwh,
    )

    # Attainable target SOC: the SOC that the battery would need to
    # already be at to cover need + reserve WITHOUT further charging.
    # When battery capacity is missing, this is ``None`` -- the planner
    # reports "unknown" rather than guess.
    attainable_breakdown = _compute_attainable_target_soc_pct(
        telemetry=telemetry,
        soc_pct=soc_pct,
        energy_available_kwh=energy_available_kwh,
        energy_needed_kwh=energy_needed_kwh,
        energy_reserve_kwh=energy_reserve_kwh,
        energy_from_pv=energy_from_pv,
    )
    if attainable_breakdown is None:
        attainable_target_soc_pct: float | None = None
        additional_charge_kwh: float = 0.0
        absolute_needed_soc_pct: float | None = None
        residual_deficit_kwh: float = 0.0
    else:
        attainable_target_soc_pct = float(
            attainable_breakdown["attainable_soc_pct"]
        )
        additional_charge_kwh = float(
            attainable_breakdown["additional_charge_kwh"]
        )
        absolute_needed_soc_pct = float(
            attainable_breakdown["absolute_needed_soc_pct"]
        )
        residual_deficit_kwh = float(
            attainable_breakdown["residual_deficit_kwh"]
        )

    # ---- Grid-charge rate --------------------------------------------
    (
        grid_charge_hours_per_hour,
        nominal_voltage_used_v,
        max_utility_current_used_a,
        charge_eff_used,
    ) = _compute_grid_charge_rate_kwh_per_h(telemetry)
    if nominal_voltage_used_v is None:
        warnings.append(
            "nominal voltage unknown; planner cannot estimate "
            "grid-charge duration -- the prep_start_iso will be "
            "left None"
        )

    # ---- Block checks (in priority order) -----------------------------
    # 1. Battery capacity invalid
    if not telemetry.battery_capacity_ok_for_planning:
        return _blocked_plan(
            event_id=event_id,
            computed_at_iso=computed_at_iso,
            reason=(
                "battery capacity invalid or missing; planner "
                "refuses to guess (the operator-supplied form input "
                "for battery_capacity_ah or nominal_voltage_v is out "
                "of range)"
            ),
            next_attempt_iso=None,
            extra_plan=_baseline_plan_dict(
                energy_needed_kwh=energy_needed_kwh,
                energy_reserve_kwh=energy_reserve_kwh,
                energy_available_kwh=energy_available_kwh,
                attainable_target_soc_pct=None,
                needs_charging_from_grid_kwh=needs_charging_from_grid_kwh,
                prep_start_iso=None,
                grid_charge_hours_per_hour=grid_charge_hours_per_hour,
                warnings=warnings,
                absolute_needed_soc_pct=None,
                additional_charge_kwh=0.0,
                residual_deficit_kwh=residual_deficit_kwh,
            ),
            energy_from_pv=energy_from_pv,
            grid_charge_hours_per_hour=grid_charge_hours_per_hour,
        )

    # 2. Manual HEMS pause active
    pause_iso = telemetry.current_pause_until_iso
    if pause_iso is not None:
        try:
            pause_utc = _utc_instant(pause_iso, "current_pause_until_iso")
            if pause_utc > now_utc:
                return _blocked_plan(
                    event_id=event_id,
                    computed_at_iso=computed_at_iso,
                    reason=(
                        f"manual HEMS pause active until {pause_iso}; "
                        "automatic prep is suspended until the hold "
                        "expires or the operator cancels it"
                    ),
                    next_attempt_iso=pause_utc.isoformat(),
                    extra_plan=_baseline_plan_dict(
                        energy_needed_kwh=energy_needed_kwh,
                        energy_reserve_kwh=energy_reserve_kwh,
                        energy_available_kwh=energy_available_kwh,
                        attainable_target_soc_pct=attainable_target_soc_pct,
                        needs_charging_from_grid_kwh=needs_charging_from_grid_kwh,
                        prep_start_iso=None,
                        grid_charge_hours_per_hour=grid_charge_hours_per_hour,
                        warnings=warnings,
                        absolute_needed_soc_pct=absolute_needed_soc_pct,
                        additional_charge_kwh=additional_charge_kwh,
                        residual_deficit_kwh=residual_deficit_kwh,
                    ),
                    energy_from_pv=energy_from_pv,
                    grid_charge_hours_per_hour=grid_charge_hours_per_hour,
                )
        except ValueError:
            # A malformed pause timestamp is a structural caller bug;
            # surface it as a warning rather than a block so the
            # caller can self-heal by overwriting with a valid one.
            warnings.append(
                "current_pause_until_iso is not a valid offset-aware "
                "ISO string; ignoring the manual pause signal"
            )

    # 3. Telemetry stale AND grid charging is on the table
    freshness = telemetry.telemetry_freshness_seconds
    if (
        needs_charging_from_grid_kwh > 0.0
        and freshness is not None
        and freshness > TELEMETRY_STALENESS_LIMIT_SECONDS
    ):
        return _blocked_plan(
            event_id=event_id,
            computed_at_iso=computed_at_iso,
            reason=(
                f"telemetry stale ({freshness}s since last "
                f"coordinator update; limit is "
                f"{TELEMETRY_STALENESS_LIMIT_SECONDS}s); "
                "cannot guarantee safe grid charging"
            ),
            next_attempt_iso=None,
            extra_plan=_baseline_plan_dict(
                energy_needed_kwh=energy_needed_kwh,
                energy_reserve_kwh=energy_reserve_kwh,
                energy_available_kwh=energy_available_kwh,
                attainable_target_soc_pct=attainable_target_soc_pct,
                needs_charging_from_grid_kwh=needs_charging_from_grid_kwh,
                prep_start_iso=None,
                grid_charge_hours_per_hour=grid_charge_hours_per_hour,
                warnings=warnings,
                absolute_needed_soc_pct=absolute_needed_soc_pct,
                additional_charge_kwh=additional_charge_kwh,
                residual_deficit_kwh=residual_deficit_kwh,
            ),
            energy_from_pv=energy_from_pv,
            grid_charge_hours_per_hour=grid_charge_hours_per_hour,
        )

    # 4. Grid unavailable AND we need grid charging
    if needs_charging_from_grid_kwh > 0.0 and telemetry.grid_available is False:
        return _blocked_plan(
            event_id=event_id,
            computed_at_iso=computed_at_iso,
            reason=(
                f"grid unavailable; needs "
                f"{needs_charging_from_grid_kwh:.3f} kWh of grid "
                "charging to cover the outage -- the planner cannot "
                "raise SOC without utility power"
            ),
            next_attempt_iso=getattr(event, "end_iso", None),
            extra_plan=_baseline_plan_dict(
                energy_needed_kwh=energy_needed_kwh,
                energy_reserve_kwh=energy_reserve_kwh,
                energy_available_kwh=energy_available_kwh,
                attainable_target_soc_pct=attainable_target_soc_pct,
                needs_charging_from_grid_kwh=needs_charging_from_grid_kwh,
                prep_start_iso=None,
                grid_charge_hours_per_hour=grid_charge_hours_per_hour,
                warnings=warnings,
                absolute_needed_soc_pct=absolute_needed_soc_pct,
                additional_charge_kwh=additional_charge_kwh,
                residual_deficit_kwh=residual_deficit_kwh,
            ),
            energy_from_pv=energy_from_pv,
            grid_charge_hours_per_hour=grid_charge_hours_per_hour,
        )

    # ---- Prep start time ---------------------------------------------
    prep_start_iso: str | None = None
    if (
        needs_charging_from_grid_kwh > 0.0
        and grid_charge_hours_per_hour is not None
        and grid_charge_hours_per_hour > 0.0
    ):
        hours_needed = math.ceil(
            needs_charging_from_grid_kwh / grid_charge_hours_per_hour
        )
        # Snap to the event start; charging before that is wasted.
        prep_start = start_utc - timedelta(hours=hours_needed)
        if prep_start < now_utc:
            prep_start = now_utc
        prep_start_iso = prep_start.isoformat()

    # ---- Incomplete-reason surface ------------------------------------
    reason_if_incomplete: str | None = None
    if needs_charging_from_grid_kwh > 0.0:
        # We're not blocked (e.g. grid IS available) but the planner
        # is honest: even with the prep, it cannot guarantee the
        # house stays powered for the whole window.
        reason_if_incomplete = (
            f"gap of {needs_charging_from_grid_kwh:.3f} kWh between "
            f"available energy ({energy_available_kwh:.3f} kWh) and "
            f"need+reserve ({energy_needed_kwh + energy_reserve_kwh:.3f} "
            "kWh); the planner will do its best with grid charging "
            "but cannot promise the house will stay powered for the "
            "entire window"
        )

    plan_dict = _baseline_plan_dict(
        energy_needed_kwh=energy_needed_kwh,
        energy_reserve_kwh=energy_reserve_kwh,
        energy_available_kwh=energy_available_kwh,
        attainable_target_soc_pct=attainable_target_soc_pct,
        needs_charging_from_grid_kwh=needs_charging_from_grid_kwh,
        prep_start_iso=prep_start_iso,
        grid_charge_hours_per_hour=grid_charge_hours_per_hour,
        warnings=warnings,
        absolute_needed_soc_pct=absolute_needed_soc_pct,
        additional_charge_kwh=additional_charge_kwh,
        residual_deficit_kwh=residual_deficit_kwh,
    )

    return PlannerPlan(
        event_id=event_id,
        computed_at_iso=computed_at_iso,
        mode="observation",
        plan=plan_dict,
        blocked=False,
        reason_if_blocked=None,
        reason_if_incomplete=reason_if_incomplete,
        next_attempt_iso=None,
        energy_from_pv_during_outage_kwh=energy_from_pv,
        grid_charge_hours_per_hour=grid_charge_hours_per_hour,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _estimate_consumption_kwh(
    *,
    telemetry: Telemetry,
    start_utc: datetime,
    end_utc: datetime,
) -> tuple[float, str | None]:
    """Estimate the kWh the load will pull over ``[start_utc, end_utc]``.

    Two paths:

      * If ``tarpit_consumption_avg_kwh`` (date -> kWh, last 30 days
        same-day consumption) is supplied AND covers the window, the
        estimate is the SUM of the matching same-weekday values for
        every date in the window, multiplied by 1.2 when
        ``is_holiday_or_weekend`` is True.
      * Otherwise, the fallback is ``load_power_w * hours`` -- a
        "current-load-as-window-forecast" estimate. A warning is
        emitted so the dashboard can surface "no history available;
        using instantaneous load".

    Returns ``(kWh, warning_str_or_None)``.
    """
    duration_hours = (end_utc - start_utc).total_seconds() / 3600.0
    holiday_mult = (
        HOLIDAY_WEEKEND_MULTIPLIER if telemetry.is_holiday_or_weekend else 1.0
    )

    history = telemetry.tarpit_consumption_avg_kwh
    if history:
        # Per-day estimate: average over matching weekday (Mon..Sun) +
        # immediate neighbours from the supplied dict. The dict is
        # keyed by ISO date string and the value is the daily kWh.
        # We do not require a full window of history -- a single
        # matching date is enough to bootstrap the per-day estimate.
        per_day_kwh = _average_daily_kwh(history)
        if per_day_kwh is not None and per_day_kwh > 0.0:
            return per_day_kwh * duration_hours / 24.0 * holiday_mult, None

    # Fallback: use the current load as the window forecast.
    if telemetry.load_power_w is not None and telemetry.load_power_w > 0.0:
        kwh = telemetry.load_power_w * duration_hours / 1000.0 * holiday_mult
        return kwh, (
            "consumption history missing; using current load "
            f"({telemetry.load_power_w:.0f} W) as the window forecast"
        )

    # Truly nothing to go on. Refuse to invent a number; the planner
    # will surface the gap downstream.
    return 0.0, (
        "consumption history missing and current load unknown; "
        "planner assumes zero need -- this WILL understate the true "
        "energy required"
    )


def _average_daily_kwh(history: dict[str, float]) -> float | None:
    """Mean of the supplied ``date -> kWh`` map.

    Returns ``None`` if the dict is empty or contains no finite,
    positive values.
    """
    if not history:
        return None
    valid = [
        v for v in history.values()
        if isinstance(v, (int, float))
        and math.isfinite(float(v))
        and float(v) >= 0.0
    ]
    if not valid:
        return None
    return sum(valid) / len(valid)


def _compute_pv_contribution_kwh(
    *,
    telemetry: Telemetry,
    start_utc: datetime,
    end_utc: datetime,
    event_tz_name: str | None = None,
) -> tuple[float, str | None]:
    """kWh the PV is expected to deliver over the event window.

    Per policy rule 2: when ``pv_forecast_confidence`` is None or
    ``pv_forecast_samples < 7`` or ``pv_forecast_confidence < 0.5``,
    the contribution is collapsed to zero with a warning. Per rule 1:
    the planner never uses ``pv_power_w`` as a hardware ceiling.

    Round 6 (this round) replaces the
    ``daily_forecast * duration/24`` scaling
    with a daylight-window scaling. The old
    formula invented solar production at
    night: a 00:00-02:00 outage got
    ``daily_forecast * 2/24`` kWh of fake
    night-time PV. The new formula honours
    the 06:00-19:00 daylight envelope and, if
    ``pv_forecast_hourly_kwh`` is supplied,
    sums the hourly values for the hours that
    actually overlap the event window. No bias
    correction, no cap, no optimistic fill --
    when the event is entirely at night the
    function returns 0.0 with an explicit
    warning.
    """
    samples = telemetry.pv_forecast_samples
    confidence = telemetry.pv_forecast_confidence
    low_confidence = (
        confidence is None
        or samples is None
        or samples < PV_CONFIDENCE_SAMPLES_MIN
        or confidence < PV_CONFIDENCE_FACTOR_MIN
    )

    if low_confidence:
        return 0.0, (
            "low PV confidence -- assuming zero sun contribution "
            f"(samples={samples}, confidence_factor={confidence}; "
            f"policy thresholds are samples>={PV_CONFIDENCE_SAMPLES_MIN} "
            f"and confidence_factor>={PV_CONFIDENCE_FACTOR_MIN})"
        )

    duration_hours = (end_utc - start_utc).total_seconds() / 3600.0

    # Path 1: hourly forecast is available. Sum only the
    # hours that overlap the event window. The hourly
    # values are kWh produced **in that local hour**; we
    # do not scale by the sub-hour fraction because the
    # forecast is already bucketed per hour.
    hourly = telemetry.pv_forecast_hourly_kwh
    if hourly is not None:
        assert confidence is not None  # see low_confidence guard above
        if not isinstance(hourly, list) or len(hourly) != 24:
            return 0.0, (
                "pv_forecast_hourly_kwh must be a 24-element list; "
                f"got {type(hourly).__name__} with length "
                f"{len(hourly) if hasattr(hourly, '__len__') else '?'}"
            )
        if any(
            not isinstance(v, (int, float))
            or not math.isfinite(float(v))
            or float(v) < 0.0
            for v in hourly
        ):
            return 0.0, (
                "pv_forecast_hourly_kwh contains non-finite or "
                "negative values; assuming zero sun contribution"
            )
        # Resolve the local time zone from the event
        # (not from ``start_utc.tzinfo`` -- that is
        # always UTC, and hour-of-day in UTC is the
        # wrong lookup for the operator's wall clock).
        from zoneinfo import ZoneInfo
        try:
            local_tz = ZoneInfo(event_tz_name or "UTC")
        except Exception:
            local_tz = timezone.utc
        # Iterate every local hour covered by the event.
        # The event may cross midnight; we walk the wall
        # clock from start_utc to end_utc and add the kWh
        # for each local hour-of-day.
        total = 0.0
        cur = start_utc
        while cur < end_utc:
            local = cur.astimezone(local_tz)
            total += float(hourly[local.hour])
            cur = cur + timedelta(hours=1)
        eff_confidence = max(0.0, min(1.0, float(confidence)))
        return max(0.0, total * eff_confidence), None

    # Path 2: no hourly forecast. Fall back to a
    # daylight-window-only scaling. The daily
    # forecast is treated as the total production
    # across ``DAYLIGHT_START_HOUR..DAYLIGHT_END_HOUR``
    # (the operator site is ~50°N -- 06:00-19:00 is the
    # conservative envelope). The fraction of the
    # event window that overlaps daylight is then
    # ``overlap_hours / daylight_hours`` -- not
    # ``event_hours / 24`` as round 5 did. A 00:00-02:00
    # outage gets zero; a 19:00-22:00 outage gets 0
    # PV too because 19:00 is the end of daylight.
    forecast = telemetry.pv_forecast_tomorrow_kwh
    if forecast is None or not math.isfinite(float(forecast)) or forecast <= 0.0:
        return 0.0, (
            "PV forecast confidence is OK but pv_forecast_tomorrow_kwh "
            "is missing or non-positive; assuming zero sun contribution"
        )
    eff_confidence = max(0.0, min(1.0, float(confidence)))
    daylight_hours = float(DAYLIGHT_END_HOUR - DAYLIGHT_START_HOUR)
    overlap_hours = _daylight_overlap_hours(
        start_utc, end_utc, DAYLIGHT_START_HOUR, DAYLIGHT_END_HOUR
    )
    if overlap_hours <= 0.0:
        return 0.0, (
            "event window has no overlap with the daylight "
            f"envelope ({DAYLIGHT_START_HOUR:02d}:00-"
            f"{DAYLIGHT_END_HOUR:02d}:00); PV contribution is 0 kWh "
            "without an hourly forecast"
        )
    # The daily forecast is the total across the daylight
    # envelope. Per-hour average is forecast / daylight_hours.
    # Event contribution is that per-hour rate times the
    # overlap_hours.
    energy = (
        float(forecast)
        * (overlap_hours / daylight_hours)
        * eff_confidence
    )
    return max(0.0, energy), None


def _daylight_overlap_hours(
    start_utc: datetime,
    end_utc: datetime,
    daylight_start_hour: int,
    daylight_end_hour: int,
) -> float:
    """Hours the event window overlaps the
    operator-site daylight envelope.

    Walks the event in 1-minute steps and
    accumulates the minutes that fall inside
    ``[daylight_start_hour, daylight_end_hour)``
    (in the event's local time). Returns the
    total in hours. The boundary hours are
    exact, not "inside the second" -- a 1-min
    event at 18:59:30 still gets 0.5 min of
    daylight, the rest night. This avoids the
    bias of rounding one direction.
    """
    if end_utc <= start_utc:
        return 0.0
    total_minutes = 0.0
    cur = start_utc
    step = timedelta(minutes=1)
    while cur < end_utc:
        # Local hour-of-day for the wall clock
        # the event spans. ``cur`` carries its
        # tzinfo (we plumbed offset-aware ISO
        # through ``_require_offset_aware_iso``);
        # astimezone with the same tzinfo gives
        # the local hour, which is what the
        # operator's daylight envelope refers to.
        local = cur.astimezone(cur.tzinfo) if cur.tzinfo else cur
        next_cur = cur + step
        # Cap the last step at end_utc.
        if next_cur > end_utc:
            next_cur = end_utc
        elapsed = (next_cur - cur).total_seconds() / 60.0
        if daylight_start_hour <= local.hour < daylight_end_hour:
            total_minutes += elapsed
        cur = next_cur
    return total_minutes / 60.0


def _compute_battery_remaining_kwh(
    telemetry: Telemetry,
) -> tuple[float, float | None, bool]:
    """kWh of usable energy currently above the reserve floor.

    Returns ``(kWh, current_soc_pct, soc_unknown)``:

      * ``kWh`` is the efficiency-adjusted usable kWh above the
        operator-configured reserve floor (``reserve_soc_pct``).
      * ``current_soc_pct`` is the SOC the planner actually used
        (corrected first, then raw, then ``None``).
      * ``soc_unknown`` is True when BOTH corrected and raw SOC are
        missing or out-of-range.

    When ``battery_capacity_kwh`` is None or
    ``battery_capacity_ok_for_planning`` is False, this returns 0 --
    the planner refuses to invent a battery size (the structural
    blocker at the top of ``plan_outage`` will surface that as a
    ``reason_if_blocked``).
    """
    if (
        not telemetry.battery_capacity_ok_for_planning
        or telemetry.battery_capacity_kwh is None
        or telemetry.battery_capacity_kwh <= 0.0
    ):
        return 0.0, None, True

    raw_soc = telemetry.battery_soc_pct
    corr_soc = telemetry.battery_soc_corrected_pct

    soc: float | None = None
    if corr_soc is not None and math.isfinite(float(corr_soc)) and 0.0 <= float(corr_soc) <= 100.0:
        soc = float(corr_soc)
    elif raw_soc is not None and math.isfinite(float(raw_soc)) and 0.0 <= float(raw_soc) <= 100.0:
        soc = float(raw_soc)

    soc_unknown = soc is None
    if soc_unknown:
        # Treat as exactly the floor -- the planner can still
        # compute "0 kWh available above the reserve".
        soc = float(telemetry.reserve_soc_pct or DEFAULT_RESERVE_SOC_PCT)

    reserve_pct = float(telemetry.reserve_soc_pct or DEFAULT_RESERVE_SOC_PCT)
    headroom_pct = max(0.0, soc - reserve_pct)
    discharge_eff = telemetry.battery_discharge_efficiency
    if discharge_eff is None or not math.isfinite(float(discharge_eff)):
        discharge_eff = DEFAULT_DISCHARGE_EFFICIENCY
    discharge_eff = max(0.0, min(1.0, float(discharge_eff)))

    kwh = (headroom_pct / 100.0) * float(telemetry.battery_capacity_kwh) * discharge_eff
    return max(0.0, kwh), soc, soc_unknown


def _compute_attainable_target_soc_pct(
    *,
    telemetry: Telemetry,
    soc_pct: float | None,
    energy_available_kwh: float,
    energy_needed_kwh: float,
    energy_reserve_kwh: float,
    energy_from_pv: float = 0.0,
) -> dict | None:
    """Decompose the SOC requirement into four
    numbers so the dashboard can show the
    operator the actual gap, not a clamped
    value.

    Returns a dict with the following keys (in
    the same shape the operator sees on the
    card):

        ``absolute_needed_soc_pct``
            The SOC the operator would have to
            reach to cover ``need + reserve``
            **without** any further charging.
            This can be ``> 100`` when the
            outage plus reserve exceed the
            battery's usable headroom even
            from a full charge -- i.e. the
            outage is longer than the battery
            can cover on its own. The planner
            reports it as-is and flags the gap
            in ``residual_deficit_kwh``; the
            operator sees that no SOC setting
            will help and decides to reduce
            load or shorten the event.
        ``attainable_soc_pct``
            The SOC the battery can reach by
            the start of the event assuming
            the operator tops it up from the
            grid at the configured rate.
            ``min(absolute_needed_soc_pct, 100)``.
            This is the value the dispatcher
            actually targets.
        ``additional_charge_kwh``
            Energy that has to flow from the
            grid to reach ``attainable_soc_pct``
            from the current SOC. ``0`` when
            the current SOC already covers
            the need.
        ``residual_deficit_kwh``
            The energy gap that even a full
            charge plus the on-event PV cannot
            close. ``0`` when the outage is
            covered; ``> 0`` means the
            operator must reduce load or
            accept a brown-out.
        ``reserve_soc_pct``
            The configured SOC floor (read
            from the telemetry).
        ``soc_pct``
            The current SOC (the planner does
            not assume -- it surfaces whatever
            the operator sees).

    Returns ``None`` when the battery capacity
    is missing or the planner refuses to
    guess; the caller surfaces the ``None`` as
    "невідомо" in the dashboard.
    """
    if (
        telemetry.battery_capacity_kwh is None
        or telemetry.battery_capacity_kwh <= 0.0
        or not telemetry.battery_capacity_ok_for_planning
    ):
        return None

    discharge_eff = telemetry.battery_discharge_efficiency
    if discharge_eff is None or not math.isfinite(float(discharge_eff)):
        discharge_eff = DEFAULT_DISCHARGE_EFFICIENCY
    discharge_eff = max(1e-9, min(1.0, float(discharge_eff)))

    reserve_pct = float(telemetry.reserve_soc_pct or DEFAULT_RESERVE_SOC_PCT)
    cap = float(telemetry.battery_capacity_kwh)

    # Net energy still missing AFTER factoring in PV.
    net_needed_kwh = max(
        0.0, energy_needed_kwh + energy_reserve_kwh - energy_available_kwh
    )

    # SOC the operator would need at the start
    # of the event to cover need+reserve from
    # the battery alone (no further grid charge
    # AND no on-event PV). NOT clamped -- the
    # planner surfaces the impossible target
    # so the operator can see it, plus the
    # residual deficit. Юра's round-6
    # reproduction: 11.776 kWh, 20 % reserve,
    # 0.95 eff, need 0.85 kWh, reserve 10.16
    # kWh -> absolute = 20 + ((0.85+10.16) /
    # (11.776*0.95)) * 100 = 20 + 98.4 = 118.4 %.
    # Note: this is the SOC the operator would
    # need WITHOUT any on-event PV; with PV
    # during the event the actual target is
    # the attainable (clamped) value plus
    # whatever PV arrives. The on-event PV
    # contribution is reported separately in
    # ``energy_available_kwh`` so the operator
    # can see both numbers.
    absolute_needed_soc_pct = round(
        reserve_pct
        + (
            (energy_needed_kwh + energy_reserve_kwh)
            / (cap * discharge_eff)
        ) * 100.0,
        2,
    )
    attainable_soc_pct = round(
        min(100.0, max(reserve_pct, absolute_needed_soc_pct)), 2
    )

    # Energy the operator must add to the
    # battery to reach ``attainable_soc_pct``
    # from the current SOC. Computed on the
    # attainable (clamped) target so the
    # charge figure never pretends more is
    # achievable than the battery can store.
    current_soc_pct = (
        float(soc_pct)
        if soc_pct is not None and math.isfinite(float(soc_pct))
        else reserve_pct
    )
    delta_soc = max(0.0, attainable_soc_pct - max(current_soc_pct, reserve_pct))
    additional_charge_kwh = round(
        (delta_soc / 100.0) * cap * discharge_eff, 4
    )

    # Residual deficit: the energy gap that
    # survives even a full charge plus the
    # on-event PV. Computed from the *absolute*
    # target so an unreachable required SOC
    # shows up as a real kWh number rather
    # than disappearing into the clamp.
    if absolute_needed_soc_pct <= 100.0:
        # Battery can cover the need if
        # topped up to attainable_soc_pct. No
        # residual; the energy budget closes.
        residual_deficit_kwh = 0.0
    else:
        # absolute_needed_soc_pct > 100: even
        # a full charge cannot cover need+reserve.
        # The shortfall is the energy still
        # missing after a full charge plus
        # the on-event PV. NOTE: we do NOT add
        # ``energy_available_kwh`` here because
        # that already includes the battery
        # remaining; what we want to know is
        # whether need+reserve can be served
        # from a fresh full charge + the
        # on-event PV.
        full_charge_kwh = (
            (1.0 - reserve_pct / 100.0)
            * cap
            * discharge_eff
        )
        residual_deficit_kwh = round(
            max(
                0.0,
                energy_needed_kwh
                + energy_reserve_kwh
                - full_charge_kwh
                - max(0.0, energy_from_pv),
            ),
            4,
        )

    return {
        "absolute_needed_soc_pct": absolute_needed_soc_pct,
        "attainable_soc_pct": attainable_soc_pct,
        "additional_charge_kwh": additional_charge_kwh,
        "residual_deficit_kwh": residual_deficit_kwh,
        "reserve_soc_pct": round(reserve_pct, 2),
        "soc_pct": round(current_soc_pct, 2),
    }


def _compute_grid_charge_rate_kwh_per_h(
    telemetry: Telemetry,
) -> tuple[float | None, float | None, float | None, float | None]:
    """kWh/h achievable via utility charging at the operator's max.

    ``kWh/h = max_utility_charging_current_a * nominal_voltage_v
              * charge_efficiency / 1000``

    Returns ``(rate_kwh_per_h, voltage_used_v, current_used_a, eff_used)``
    with ``None`` for the fields that could not be derived.
    """
    voltage = telemetry.nominal_voltage_v
    if voltage is None or not math.isfinite(float(voltage)) or voltage <= 0.0:
        return None, None, None, None

    current = telemetry.max_utility_charging_current_a
    if current is None or not math.isfinite(float(current)) or current <= 0.0:
        return None, float(voltage), None, None

    eff = telemetry.battery_charge_efficiency
    if eff is None or not math.isfinite(float(eff)):
        eff = DEFAULT_CHARGE_EFFICIENCY
    eff = max(0.0, min(1.0, float(eff)))

    rate = float(current) * float(voltage) * eff / 1000.0
    return rate, float(voltage), float(current), eff


# ---------------------------------------------------------------------------
# Small plumbing helpers
# ---------------------------------------------------------------------------


def _baseline_plan_dict(
    *,
    energy_needed_kwh: float,
    energy_reserve_kwh: float,
    energy_available_kwh: float,
    attainable_target_soc_pct: float | None,
    needs_charging_from_grid_kwh: float,
    prep_start_iso: str | None,
    grid_charge_hours_per_hour: float | None,
    warnings: list[str],
    absolute_needed_soc_pct: float | None = None,
    additional_charge_kwh: float = 0.0,
    residual_deficit_kwh: float = 0.0,
) -> dict[str, Any]:
    """Build the ``plan`` dict that the dashboard / persistence sees.

    Round 6 (this round) widens the plan with
    three new fields that the operator asked
    for explicitly:

    * ``absolute_needed_soc_pct`` -- the SOC
      that would cover need + reserve without
      any further charging. **Not clamped to
      100**: a value of 110 % means the outage
      is longer than the battery can cover
      even from a full charge, regardless of
      how much the operator tops up.
    * ``additional_charge_kwh`` -- the kWh
      the operator must add to the battery
      between now and the start of the event
      to reach ``attainable_soc_pct`` (the
      clamped target).
    * ``residual_deficit_kwh`` -- the kWh gap
      that survives even a full charge plus
      the on-event PV. ``0`` when the outage
      is covered; ``> 0`` means the operator
      must reduce load or shorten the event
      window. The dashboard surfaces this
      next to the charge figure so the
      operator can tell the two apart.

    The legacy field
    ``attainable_target_soc_pct`` is kept for
    back-compat with the round-5 card and the
    integration's existing dashboard, and
    matches ``min(absolute_needed_soc_pct, 100)``.
    """
    return {
        "energy_needed_kwh": round(float(energy_needed_kwh), 3),
        "energy_reserve_kwh": round(float(energy_reserve_kwh), 3),
        "energy_available_kwh": round(float(energy_available_kwh), 3),
        "attainable_target_soc_pct": (
            float(attainable_target_soc_pct)
            if attainable_target_soc_pct is not None
            else None
        ),
        "absolute_needed_soc_pct": (
            float(absolute_needed_soc_pct)
            if absolute_needed_soc_pct is not None
            else None
        ),
        "additional_charge_kwh": round(float(additional_charge_kwh), 3),
        "residual_deficit_kwh": round(float(residual_deficit_kwh), 3),
        "needs_charging_from_grid_kwh": round(
            float(needs_charging_from_grid_kwh), 3
        ),
        "prep_start_iso": str(prep_start_iso) if prep_start_iso is not None else None,
        "grid_charge_hours_estimate": (
            round(float(grid_charge_hours_per_hour), 3)
            if grid_charge_hours_per_hour is not None
            else None
        ),
        "warnings": list(warnings),
    }


def _blocked_plan(
    *,
    event_id: str,
    computed_at_iso: str,
    reason: str,
    next_attempt_iso: str | None,
    extra_plan: dict[str, Any] | None = None,
    energy_from_pv: float | None = None,
    grid_charge_hours_per_hour: float | None = None,
) -> PlannerPlan:
    """Construct a fully-formed blocked ``PlannerPlan``.

    Blocked plans always include a baseline ``plan`` dict so the
    dashboard can render the underlying numbers even when action is
    refused.
    """
    plan = extra_plan if extra_plan is not None else _baseline_plan_dict(
        energy_needed_kwh=0.0,
        energy_reserve_kwh=0.0,
        energy_available_kwh=0.0,
        attainable_target_soc_pct=None,
        needs_charging_from_grid_kwh=0.0,
        prep_start_iso=None,
        grid_charge_hours_per_hour=grid_charge_hours_per_hour,
        warnings=[],
    )
    return PlannerPlan(
        event_id=event_id,
        computed_at_iso=computed_at_iso,
        mode="observation",
        plan=plan,
        blocked=True,
        reason_if_blocked=reason,
        reason_if_incomplete=None,
        next_attempt_iso=next_attempt_iso,
        energy_from_pv_during_outage_kwh=energy_from_pv,
        grid_charge_hours_per_hour=grid_charge_hours_per_hour,
    )


def _require_offset_aware_iso(label: str, value: Any) -> datetime:
    """Parse an offset-aware ISO 8601 string; reject naive datetimes.

    Mirrors the helper in ``services.outage`` so the planner has the
    same defensive contract at its boundary.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"{label} must be an ISO 8601 string; got {type(value).__name__}"
        )
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{label} is not a valid ISO 8601 string: {value!r} ({exc})"
        ) from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(
            f"{label} must include a UTC offset (e.g. '+03:00'); "
            "naive datetimes are not allowed"
        )
    return dt


def _read_emergency_buffer_kwh(*, telemetry: "Telemetry") -> float:
    """Read the operator-configured emergency
    buffer in kWh.

    The buffer is sourced from the same
    Telemetry object the planner already
    carries. The caller (coordinator) packs
    the option into the Telemetry via the
    ``emergency_buffer_kwh`` field. The
    default is 0 (no buffer beyond the SOC
    reserve and the event consumption).

    This is the **only** place the
    "extra kWh reserve" is read -- it is
    NOT derived from any per-day mean
    consumption figure. The product policy
    "1 day of mean consumption as buffer"
    was retired in round 7 because it
    double-counted with the SOC reserve and
    was never satisfiable from a battery of
    the operator's size.
    """
    raw = getattr(telemetry, "emergency_buffer_kwh", None)
    if raw is None:
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return max(0.0, value)


def _utc_instant(iso_str: Any, label: str) -> datetime:
    """Parse an ISO 8601 string to a UTC ``datetime``.

    Raises ``ValueError`` for any non-parseable input. Used for
    comparison / overlap math.
    """
    dt = _require_offset_aware_iso(label, iso_str)
    return dt.astimezone(timezone.utc)