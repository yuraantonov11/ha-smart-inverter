"""HEMS Engine — core decision logic for inverter control.

Ported from Flutter HemsAlgorithmService (1034 lines).
Manages output priority, charger priority, battery keepalive,
manual override, anti-flapping, circuit breaker, and all smart modes.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any

from .tuning import HemsTuningService, HemsTunables
from . import debug_logging

_LOGGER = logging.getLogger(__name__)


# Normalize output/charger display strings to numeric for comparison
_OUTPUT_DISPLAY_TO_NUM = {"USB": "0", "SBU": "2", "Line Mode": "0", "Battery Mode": "2"}
_CHARGER_DISPLAY_TO_NUM = {"CSO": "0", "SNU": "1", "OSO": "2", "UTO": "3"}

def _normalize_setting(value: str | int | None, labels: dict[str, str]) -> str | None:
    """Accept display labels and numeric API codes at the engine boundary."""
    if isinstance(value, str):
        key = value.strip()
    elif type(value) is int:
        key = str(value)
    else:
        return None
    return labels.get(key, key)


def _normalize_output(value: str | int | None) -> str | None:
    return _normalize_setting(value, _OUTPUT_DISPLAY_TO_NUM)


def _normalize_charger(value: str | int | None) -> str | None:
    return _normalize_setting(value, _CHARGER_DISPLAY_TO_NUM)


def _coerce_tariff_fallback(value: Any, *, default: float) -> float:
    """R05: validate a tariff-fallback rate.

    Contract:
      - ``None``           → engine has not been bound
                             to a coordinator (cold
                             start / test fixture).
                             Returns ``default``.
      - ``0.0``            → valid operator input
                             (free electricity, e.g.
                             solar surplus). PRESERVED
                             — NOT replaced by
                             ``default``. This is the
                             explicit fix for the
                             ``... or default`` pattern
                             which silently swallowed
                             valid zero.
      - finite, ``0 ≤ x``  → operator's choice.
      - NaN / Inf / bool /
        string / negative /
        too-large          → rejected. Returns
                             ``default``.

    The "too-large" bound is 50 UAH/kWh — well above
    the documented Ukraine peak of 4.32 day/2.16 night
    plus any reasonable margin. The lower bound is
    exactly 0 (not -0): tariffs cannot be negative.
    """
    n = _finite_number(value)
    if n is None:
        return default
    if n < 0 or n > 50:
        return default
    return n


def _finite_number(value: Any) -> float | None:
    """Unknown/nonfinite telemetry must never masquerade as a full battery.

    Returns ``None`` for anything that is not a real (non-bool)
    number. Strings, even parseable ones like ``"230"``, are
    refused: the production sources emit real floats, and a
    string is the symptom of a corrupted HA option or a
    bug in a future contributor's code. Rejecting parseable
    strings at the gate keeps the failure mode obvious.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _forecast_energy(value: Any) -> float | None:
    number = _finite_number(value)
    return number if number is not None and number >= 0 else None


def _hint_soc_target(hint: Any, attribute: str) -> float | None:
    number = _finite_number(getattr(hint, attribute, None))
    return number if number is not None and 0 <= number <= 100 else None


class SmartMode(IntEnum):
    ADAPTIVE = 0
    ARBITRAGE = 1
    STORM = 2


class OutputPriority:
    USB = "0"  # Grid first
    SBU = "2"  # Solar/Battery first


class ChargerPriority:
    SNU = "1"  # Solar + Utility
    OSO = "2"  # Solar only


class _Reason:
    """Machine-readable reason codes for control writes."""

    MANUAL_OVERRIDE = "manual_override_hold"
    DEDUP_OUTPUT = "dedup_skip_output"
    DEDUP_CHARGER = "dedup_skip_charger"
    DWELL_LOCK = "dwell_lock"
    RESERVE_PROTECTION = "reserve_soc_protection"
    NIGHT_USB = "night_window_usb"
    TARIFF_DEFER = "tariff_expensive_defer"
    NIGHT_CHEAP_NOW = "night_charge_deficit_cheap_now"
    NIGHT_NO_CHEAP = "night_charge_no_cheap_window"
    CHARGER_DAY_SOLAR = "charger_day_solar_only"
    SURPLUS_SBU = "surplus_enter_sbu"
    EVENING_PROTECT = "evening_reserve_protection"
    EVENING_BATTERY_USE = "evening_battery_use"
    FORECAST_DEFICIT_LOW = "day_forecast_deficit_low_soc"
    FORECAST_OK = "day_forecast_ok"
    HOLD = "hold_current_state"
    KEEPALIVE_START = "keepalive_start"
    KEEPALIVE_END = "keepalive_end"
    GRID_OUTAGE_PRECHARGE = "grid_outage_precharge"
    STORM_AUTO = "storm_auto_forecast"
    STORM_GRID_OUTAGE = "storm_grid_outage"
    EMERGENCY_STALE = "emergency_stale_data"
    EMERGENCY_LOW_SOC = "emergency_low_soc_daytime"


@dataclass
class HemsDecision:
    """Result of one HEMS evaluation cycle."""

    output_priority: str | None = None  # OutputUSB or OutputSBU
    charger_priority: str | None = None  # ChargerSNU or ChargerOSO
    reason: str = ""
    skip: bool = False
    buzzer_off: bool = False  # Acoustic comfort


@dataclass
class BatteryKeepaliveState:
    """Tracks battery keepalive timing."""

    last_activity_at: datetime | None = None
    in_progress: bool = False

    # Constants (port from Dart)
    INTERVAL_HOURS: int = 2
    DURATION_SEC: int = 90
    MIN_SOC: float = 22.0
    POWER_INACTIVE_THRESHOLD: float = 50.0


class HemsEngine:
    """Full HEMS decision engine — ported from Dart HemsAlgorithmService.

    Runs every coordinator update cycle (5s). Returns control decisions
    that the coordinator applies via the API.
    """

    def __init__(
        self,
        tunables: HemsTunables | None = None,
        tuning: HemsTuningService | None = None,
    ) -> None:
        self.tun = tunables or HemsTunables()
        self.tuning = tuning or HemsTuningService(self.tun)

        # Anti-flapping state
        self._last_cmd_output: str | None = None
        self._last_cmd_charger: str | None = None
        self._last_cmd_output_at: datetime | None = None
        self._last_cmd_charger_at: datetime | None = None
        self._last_output_switch_at: datetime | None = None
        self._pending_commands: set[str] = set()
        self._pending_output_previous_switch_at: datetime | None = None
        self._manual_override_until: datetime | None = None
        self._last_manual_override_log: datetime | None = None

        # Command dedup
        self._command_dedup_window = timedelta(
            seconds=self.tun.command_dedup_window_sec
        )

        # Circuit breaker
        self._consecutive_failures: int = 0
        self._blocked_until: datetime | None = None
        self._last_blocked_log: datetime | None = None

        # Keepalive
        self.keepalive = BatteryKeepaliveState()

        # Dwell
        self._current_dwell_min: int = self.tun.min_mode_hold_min

        # Storm auto-activation
        self._auto_storm_active: bool = False
        self._previous_smart_mode: int | None = None

        # Emergency stale data
        self._last_realtime_at: datetime | None = None

        # R05: configured day/night tariffs. The
        # coordinator writes to these from
        # ``apply_capacity_and_tariff_options`` so the
        # planner fallback path has the operator's
        # rates, not module-level constants. Default
        # to the documented UA rates (4.32 day /
        # 2.16 night UAH/kWh) when the coordinator has
        # not yet bound the engine.
        self._day_tariff_uah: float | None = None
        self._night_tariff_uah: float | None = None
        self._tariff_schedule: list[float] | None = None

        # Last applied buzzer state
        self._last_buzzer: str | None = None

        # Predictive ML planner (mode read from ``predictive_tuning.predictive_mode``)
        from .tuning import PredictiveTuning as _PT
        self.predictive_tuning: _PT = _PT()
        # Convenience booleans kept in lockstep with ``predictive_tuning``.
        # Legacy code (test fixtures, switch.is_on) reads
        # ``_predictive_enabled`` — keep it set to the same value the
        # coordinator computed from entry.options at startup. The
        # coordinator's ``async_set_predictive_mode`` updates both the
        # tuning object AND this attribute so the two cannot diverge.
        self._predictive_enabled: bool = (
            self.predictive_tuning.predictive_mode in ("shadow", "assist")
        )
        self._predictive_mode: str = self.predictive_tuning.predictive_mode
        # Hint/plan attributes are set on the engine when the
        # planner runs (shadow/assist + valid inputs); default them
        # to None so callers can read them unconditionally.
        self._last_predictive_hint: Any | None = None
        self._last_predictive_plan: Any | None = None
        self._last_predictive_inputs: Any | None = None

    # ═══════════════════════════════════════════════════════════════════════
    # PUBLIC API — called by coordinator
    # ═══════════════════════════════════════════════════════════════════════

    def build_forced_decision(
        self,
        reason: str,
        *,
        output_priority: str,
        charger_priority: str,
        buzzer_off: bool = False,
    ) -> HemsDecision:
        """Build a forced decision for service-level overrides.

        T12 follow-up: ``force_grid_charge`` and similar
        service-level overrides need to drive the inverter
        on a fixed output/charger pair without going
        through the regular adaptive plan. We expose this
        as a separate builder so the rest of the engine —
        ``evaluate``, ``_evaluate_adaptive``,
        ``_evaluate_arbitrage``, ``_evaluate_storm``,
        ``detect_manual_override``, hysteresis — is
        untouched. The forced decision still goes through
        ``_execute_hems_command`` in the coordinator and is
        therefore subject to the same manual-override and
        dedup guards as any other decision.

        The decision has ``skip=False`` and the supplied
        ``reason`` so the dashboard surfaces the source
        of the override.
        """
        return HemsDecision(
            output_priority=output_priority,
            charger_priority=charger_priority,
            reason=reason,
            skip=False,
            buzzer_off=buzzer_off,
        )

    def evaluate(
        self,
        *,
        smart_mode: int,
        hems_auto: bool,
        soc: float,
        pv_power: float,
        grid_power: float,
        battery_power: float,
        load_power: float,
        grid_voltage: float,
        grid_available: bool,
        current_output: str | None = None,
        current_charger: str | None = None,
        now: datetime | None = None,
        forecast_tomorrow_kwh: float | None = None,
        forecast_day_after_kwh: float | None = None,
        pv_surplus_w: float | None = None,
        reserve_soc: float | None = None,
        min_operating_soc: float | None = None,
        tarif_day: float = 4.32,
        tarif_night: float = 2.16,
        battery_health_percent: float = 100.0,
        is_online: bool = True,
        soc_unknown: bool = False,
        entry_id: str | None = None,
    ) -> HemsDecision:
        """Run one HEMS evaluation cycle.

        Returns a HemsDecision with the recommended output/charger priorities.
        The coordinator is responsible for applying (or skipping) the commands.
        """
        now = now or datetime.now()
        predictive_mode = self._reset_predictive_state()
        current_output = _normalize_output(current_output)
        current_charger = _normalize_charger(current_charger)
        reserve_soc = self.tun.reserve_soc if reserve_soc is None else reserve_soc
        min_operating_soc = self.tun.min_operating_soc if min_operating_soc is None else min_operating_soc
        forecast_tomorrow_kwh = _forecast_energy(forecast_tomorrow_kwh)
        forecast_day_after_kwh = _forecast_energy(forecast_day_after_kwh)
        numeric = {key: _finite_number(value) for key, value in {
            "soc": soc, "pv_power": pv_power, "grid_power": grid_power,
            "battery_power": battery_power, "load_power": load_power,
            "grid_voltage": grid_voltage, "reserve_soc": reserve_soc,
            "min_operating_soc": min_operating_soc,
            # T08 follow-up: the engine reads its
            # efficiency bounds from the inputs dict
            # (validated upstream by
            # ``build_planner_inputs``) so the planner
            # receives the same values the engine
            # uses for its own anti-flapping
            # calculations. The defaults match the
            # documented inverter specification.
            "charge_efficiency": _finite_number(
                getattr(self.tun, "charge_efficiency", 0.85)
            ),
            "discharge_efficiency": _finite_number(
                getattr(self.tun, "discharge_efficiency", 0.90)
            ),
        }.items()}
        inputs = {**numeric, "smart_mode": smart_mode, "grid_available": grid_available,
                  "current_output": current_output, "current_charger": current_charger,
                  "forecast_today_kwh": _forecast_energy(getattr(self, "_last_forecast_today_kwh", None)),
                  "forecast_tomorrow_kwh": forecast_tomorrow_kwh,
                  # T01 follow-up: propagate the unknown-SOC flag
                  # through to the planner path. The baseline
                  # engine still uses ``numeric["soc"]`` (set to 100
                  # when the meter is unreadable) for its own
                  # control math, but the planner reads
                  # ``soc_unknown`` from this dict and treats
                  # ``soc`` as advisory display only.
                  "soc_unknown": bool(soc_unknown)}
        valid_telemetry = (all(value is not None for value in numeric.values())
                           and 0 <= numeric["reserve_soc"] <= 100
                           and 0 <= numeric["min_operating_soc"] <= 100)
        # Valid samples still refresh history during manual/automatic holds.
        # Offline or malformed samples must not advance the freshness clock.
        if is_online and valid_telemetry:
            soc = max(0.0, min(100.0, numeric["soc"]))
            pv_power, grid_power = numeric["pv_power"], numeric["grid_power"]
            battery_power, load_power = numeric["battery_power"], numeric["load_power"]
            grid_voltage, reserve_soc = numeric["grid_voltage"], numeric["reserve_soc"]
            min_operating_soc = numeric["min_operating_soc"]
            inputs["soc"] = soc
            self._last_realtime_at = now
            self._track_battery_activity(battery_power, now)
            self.tuning.update_surplus(pv_power - load_power)
            self._current_dwell_min = self.tuning.compute_adaptive_dwell()

        buzzer_off = self.tuning.should_reduce_buzzer(now=now)
        hold = self._evaluation_hold(hems_auto=hems_auto, smart_mode=smart_mode,
                                     is_online=is_online, valid_telemetry=valid_telemetry,
                                     now=now, buzzer_off=buzzer_off)
        if hold is not None:
            return self._log_decision(hold, now, inputs, entry_id)

        predictive_hint, predictive_plan = self._evaluate_predictive(now, inputs)

        # ── Dispatch by smart mode ────────────────────────────────────
        if smart_mode in (SmartMode.ADAPTIVE, SmartMode.ARBITRAGE) and soc <= reserve_soc + 2:
            decision = HemsDecision(output_priority=OutputPriority.USB,
                                    charger_priority=ChargerPriority.SNU,
                                    reason=_Reason.RESERVE_PROTECTION, buzzer_off=buzzer_off)
        elif smart_mode == SmartMode.ADAPTIVE:
            decision = self._evaluate_adaptive(
                soc=soc,
                pv_power=pv_power,
                grid_power=grid_power,
                battery_power=battery_power,
                load_power=load_power,
                grid_available=grid_available,
                current_output=current_output,
                current_charger=current_charger,
                now=now,
                forecast_tomorrow_kwh=forecast_tomorrow_kwh,
                forecast_day_after_kwh=forecast_day_after_kwh,
                reserve_soc=reserve_soc,
                min_operating_soc=min_operating_soc,
                tarif_day=tarif_day,
                tarif_night=tarif_night,
                buzzer_off=buzzer_off,
                predictive_hint=predictive_hint,
                predictive_plan=predictive_plan,
                predictive_mode=predictive_mode,
            )
        elif smart_mode == SmartMode.ARBITRAGE:
            decision = self._evaluate_arbitrage(
                soc=soc,
                pv_power=pv_power,
                load_power=load_power,
                current_output=current_output,
                current_charger=current_charger,
                now=now,
                reserve_soc=reserve_soc,
                buzzer_off=buzzer_off,
                predictive_hint=predictive_hint,
                predictive_plan=predictive_plan,
                predictive_mode=predictive_mode,
            )
        elif smart_mode == SmartMode.STORM:
            decision = self._evaluate_storm(
                soc=soc,
                current_output=current_output,
                current_charger=current_charger,
                buzzer_off=buzzer_off,
                predictive_hint=predictive_hint,
                predictive_mode=predictive_mode,
            )
        else:
            return HemsDecision(reason="unknown_mode", skip=True, buzzer_off=buzzer_off)

        return self._finalize_decision(decision, now, inputs, entry_id)

    def _reset_predictive_state(self) -> str:
        """No recommendation survives an Off/hold/error evaluation cycle."""
        mode = getattr(self.predictive_tuning, "predictive_mode", "off")
        mode = mode if mode in ("off", "shadow", "assist") else "off"
        self._predictive_mode = self._last_predictive_mode = mode
        self._predictive_enabled = mode != "off"
        self._last_predictive_hint = None
        self._last_predictive_plan = None
        self._last_predictive_inputs = None
        controller = getattr(self, "_predictive_controller", None)
        if controller is not None:
            controller.last_plan = None
        return mode

    def _evaluation_hold(
        self,
        *,
        hems_auto: bool,
        smart_mode: int,
        is_online: bool,
        valid_telemetry: bool,
        now: datetime,
        buzzer_off: bool,
        soc: float | None = None,
        reserve_soc: float | None = None,
        soc_safety_margin: float = 5.0,
    ) -> HemsDecision | None:
        """User control, transport, and safety holds precede
        automatic decisions.

        T12 follow-up: ``soc`` and ``reserve_soc`` are
        optional. When both are supplied, this function
        returns ``reserve_floor`` if the SOC has dropped
        to ``reserve_soc + soc_safety_margin`` or below.
        The forced-grid-charge path uses this to avoid
        draining a battery that is already near the
        configured reserve: the timer keeps running but
        no command is dispatched. This mirrors the
        reserve-SOC guard the engine already enforces in
        ``_evaluate_adaptive`` for the regular plan.

        The other holds — hems_auto_off, circuit breaker,
        manual override, unknown mode, inverter offline,
        invalid telemetry — are unchanged. The audit's
        T12 review specifically asked for the *whole*
        stack of guards to apply to the timed hold, not
        just the four the original commit covered.
        """
        reason = None
        if not hems_auto:
            reason = "hems_auto_off"
        elif self._blocked_until and now < self._blocked_until:
            reason = "circuit_breaker"
        elif self._manual_override_until and now < self._manual_override_until:
            if self._last_manual_override_log is None or now - self._last_manual_override_log > timedelta(minutes=5):
                remaining = (self._manual_override_until - now).total_seconds()
                _LOGGER.info("HEMS: manual override hold (%.0fs remaining)", remaining)
                self._last_manual_override_log = now
            reason = _Reason.MANUAL_OVERRIDE
        elif smart_mode not in (SmartMode.ADAPTIVE, SmartMode.ARBITRAGE, SmartMode.STORM):
            reason = "unknown_mode"
        elif not is_online:
            stale = self._last_realtime_at and now - self._last_realtime_at > timedelta(minutes=30)
            reason = _Reason.EMERGENCY_STALE if stale else "inverter_offline"
        elif not valid_telemetry:
            reason = "invalid_telemetry"
        elif (
            soc is not None
            and reserve_soc is not None
            and soc <= reserve_soc + soc_safety_margin
        ):
            # Reserve-floor guard: refuse to drive the
            # inverter into a state that would drain the
            # battery below the configured reserve. The
            # forced-grid-charge path uses this to wait
            # until the SOC recovers (e.g. via PV) before
            # it starts the timed hold. The timer keeps
            # running.
            reason = "reserve_floor"
        if reason is not None:
            return HemsDecision(reason=reason, skip=True, buzzer_off=buzzer_off)
        return None

    def _evaluate_predictive(self, now: datetime, inputs: dict[str, Any]) -> tuple[Any, Any]:
        """Build current hints/plan; failures leave the baseline engine usable."""
        if not self._predictive_enabled:
            return None, None
        try:
            from .predictive import PredictiveHemsController
            from .telemetry import build_planner_inputs

            hourly_pv = list(getattr(self, "_hourly_pv_forecast", []) or [])
            radiation = list(getattr(self, "_hourly_radiation", []) or [])
            weather = list(getattr(self, "_hourly_weather_codes", []) or [])
            capacity = _finite_number(getattr(self, "_battery_capacity_kwh", 4.8))
            forecast_now = getattr(self, "_planner_forecast_now", now)
            dated_pv = getattr(self, "_dated_hourly_pv_forecast", None)
            if dated_pv is not None:
                if not isinstance(forecast_now, datetime) or forecast_now.tzinfo is None:
                    return None, None
                first = int(forecast_now.replace(minute=0, second=0, microsecond=0).timestamp())
                values = [_finite_number(dated_pv.get(first + h*3600)) for h in range(24)]
                if any(value is None or not 0 <= value <= 20000 for value in values):
                    return None, None
            if (inputs["forecast_tomorrow_kwh"] is None or inputs["forecast_today_kwh"] is None
                    or any(len(series) != 24 for series in (hourly_pv, radiation, weather))
                    or capacity is None or capacity <= 0):
                return None, None
            if not hasattr(self, "_predictive_controller"):
                self._predictive_controller = PredictiveHemsController()
            controller = self._predictive_controller
            # T08 follow-up: propagate the user-
            # configured ``reserve_soc`` from the
            # engine inputs into ``build_planner_inputs``.
            # The previous code path left the planner
            # on the dataclass default (20 %), so a
            # 35 % reserve was silently dropped before
            # any planner call. ``inputs["reserve_soc"]``
            # is set by the coordinator from
            # ``entry.options["reserve_soc"]`` and is
            # already validated by ``evaluate()``.
            planner_reserve = _finite_number(
                inputs.get("reserve_soc")
            )
            if planner_reserve is None or not 0 <= planner_reserve <= 100:
                planner_reserve = 20.0
            # R05: validate the configured day/night rates
            # before passing them as planner fallbacks. The
            # ``_coerce_tariff_fallback`` helper preserves
            # valid zero and rejects NaN/Inf/bool/negative.
            _d = _coerce_tariff_fallback(
                getattr(self, "_day_tariff_uah", None),
                default=4.32,
            )
            _n = _coerce_tariff_fallback(
                getattr(self, "_night_tariff_uah", None),
                default=2.16,
            )
            pi = build_planner_inputs(
                raw={"gridVoltage": inputs["grid_voltage"],
                     # T01 follow-up: when ``soc_unknown`` is set, do
                     # NOT pass a synthetic 100 into the planner
                     # payload. The audit's complaint is exactly
                     # this: the planner used to see 100 % and act
                     # on it. We pass ``None`` for the raw field
                     # and let the explicit ``soc_unknown=True``
                     # override below carry the truth.
                     "batterySoc": (None if inputs.get("soc_unknown") else inputs["soc"]),
                     "pvPower": inputs["pv_power"], "loadPower": inputs["load_power"],
                     "gridPower": inputs["grid_power"], "batteryPower": inputs["battery_power"]},
                now=forecast_now, smart_mode=inputs["smart_mode"],
                forecast_tomorrow_kwh=inputs["forecast_tomorrow_kwh"],
                forecast_today_kwh=inputs["forecast_today_kwh"],
                hourly_pv=hourly_pv, hourly_radiation=radiation, hourly_weather_codes=weather,
                dated_hourly_pv=dated_pv,
                tariff_schedule=list(getattr(self, "_tariff_schedule", []) or []),
                # R05: the coordinator's configured
                # day/night rates become the planner's
                # fallbacks. The validator in
                # ``build_planner_inputs`` may have refused
                # the schedule (returned an empty list),
                # in which case the planner rebuilds the
                # 24-hour day/night schedule from these
                # fallbacks. They are the operator's
                # settings, NOT module-level constants.
                tariff_day_fallback=_d,
                tariff_night_fallback=_n,
                # Audit T19: the dated
                # ``list[tuple[date,
                # list[float], bool]]``
                # shape is the canonical
                # history contract. The
                # fallback to the legacy
                # flat shape is kept for
                # tests and migration
                # safety.
                consumption_history=list(getattr(self, "_consumption_history_with_dates", None) or getattr(self, "_consumption_history", []) or []),
                battery_capacity_kwh=capacity, grid_available=inputs["grid_available"],
                # T01 follow-up: propagate the unknown-SOC flag all the
                # way into PlannerInputs so the predictive controller
                # sees a coherent ``soc=None / soc_unknown=True``
                # state instead of the synthetic 100 % fallback that
                # would otherwise leak into the planner's display
                # fields and any planning math that reads them.
                # The baseline engine still uses ``inputs["soc"]``
                # (=100) for its own non-predictive code paths; the
                # the gate in the coordinator already prevents any
                # downstream inverter write. This flag is the
                # diagnostic truth for the planner.
                soc_unknown=bool(inputs.get("soc_unknown", False)),
                # T08 follow-up: the user-configured
                # reserve reaches the planner here,
                # validated by ``build_planner_inputs``
                # at the boundary.
                reserve_soc=planner_reserve,
            )
            hint = controller.suggest(pi)
            try:
                _, plan = controller.decide(pi)
            except Exception as exc:
                _LOGGER.debug("Predictive plan build skipped: %s", exc)
                plan = None
                controller.last_plan = None
            self._last_predictive_hint = hint
            self._last_predictive_plan = plan
            self._last_predictive_inputs = pi
            return hint, plan
        except Exception as exc:
            _LOGGER.debug("Predictive hint unavailable: %s", exc)
            return None, None

    def _finalize_decision(self, decision: HemsDecision, now: datetime,
                           inputs: dict[str, Any],
                           entry_id: str | None = None) -> HemsDecision:
        """Filter redundant writes, apply dwell/dedup and record the proposal."""
        if not decision.skip:
            for command, current in (("output_priority", inputs["current_output"]),
                                     ("charger_priority", inputs["current_charger"])):
                target = getattr(decision, command)
                if target is not None and target == current:
                    setattr(decision, command, None)
                    if decision.reason == "day_default":
                        decision.reason = "already_in_target"
            previous_switch_at = self._last_output_switch_at
            decision = self._apply_anti_flapping(decision, now)
            if decision.output_priority is not None:
                self._pending_commands.add("output")
                self._pending_output_previous_switch_at = previous_switch_at
                self._last_cmd_output = decision.output_priority
                self._last_cmd_output_at = now
            if decision.charger_priority is not None:
                self._pending_commands.add("charger")
                self._last_cmd_charger = decision.charger_priority
                self._last_cmd_charger_at = now
        return self._log_decision(decision, now, inputs, entry_id)

    @staticmethod
    def _log_decision(decision: HemsDecision, now: datetime,
                      inputs: dict[str, Any],
                      entry_id: str | None = None) -> HemsDecision:
        """Include early holds in the existing trace; logging cannot stop HEMS."""
        try:
            applied = None
            if decision.skip:
                skip_reason = decision.reason
            elif decision.output_priority is None and decision.charger_priority is None:
                skip_reason = "already_in_target"
            else:
                skip_reason = None
                # Legacy trace field: proposed writes; the coordinator owns
                # delivery and reports transport success/failure separately.
                applied = {"output_priority": decision.output_priority,
                           "charger_priority": decision.charger_priority,
                           "buzzer_off": decision.buzzer_off}
            debug_logging.log_evaluation(timestamp=now, inputs=inputs, decision=decision,
                                         applied=applied, skip_reason=skip_reason,
                                         entry_id=entry_id)
        except Exception:
            pass
        return decision

    def detect_manual_override(
        self,
        actual_output: str | None,
        actual_charger: str | None,
        now: datetime | None = None,
    ) -> bool:
        """Detect if user manually changed mode.

        If device mode differs from last HEMS command for >30s,
        arm a manual override hold for 30 minutes.
        """
        now = now or datetime.now()

        norm_out = _normalize_output(actual_output)
        norm_last = _normalize_output(self._last_cmd_output)
        if norm_out and norm_last and norm_out != norm_last:
            if self._last_cmd_output_at and (now - self._last_cmd_output_at).total_seconds() > 30:
                # Don't keep re-arming if we already detected override recently
                # (prevents log-spam loops when HEMS keeps writing same target)
                if self._manual_override_until and self._manual_override_until > now:
                    return True
                self._manual_override_until = now + timedelta(minutes=self.tun.manual_override_hold_min)
                _LOGGER.info(
                    "HEMS: manual override detected (output %s → %s), hold for %d min",
                    self._last_cmd_output, actual_output, self.tun.manual_override_hold_min,
                )
                return True

        norm_chg = _normalize_charger(actual_charger)
        norm_last_c = _normalize_charger(self._last_cmd_charger)
        if norm_chg and norm_last_c and norm_chg != norm_last_c:
            if self._last_cmd_charger_at and (now - self._last_cmd_charger_at).total_seconds() > 30:
                if self._manual_override_until and self._manual_override_until > now:
                    return True
                self._manual_override_until = now + timedelta(minutes=self.tun.manual_override_hold_min)
                _LOGGER.info(
                    "HEMS: manual override detected (charger %s → %s), hold for %d min",
                    self._last_cmd_charger, actual_charger, self.tun.manual_override_hold_min,
                )
                return True

        return False

    def arm_manual_override(self, now: datetime | None = None) -> None:
        """Externally arm manual override (e.g. from UI control panel)."""
        now = now or datetime.now()
        self._manual_override_until = now + timedelta(minutes=self.tun.manual_override_hold_min)

    def report_control_failure(
        self, now: datetime | None = None, *,
        output_failed: bool = True, charger_failed: bool = True,
    ) -> None:
        """Back off failed writes without treating them as applied commands.

        Partial success preserves the successful channel. Failed/uncertain
        writes cannot cause a false manual override or block a retry via dedup.
        """
        now = now or datetime.now()
        if output_failed and "output" in self._pending_commands:
            self._last_cmd_output = self._last_cmd_output_at = None
            self._last_output_switch_at = self._pending_output_previous_switch_at
        if charger_failed and "charger" in self._pending_commands:
            self._last_cmd_charger = self._last_cmd_charger_at = None
        self._pending_commands.clear()
        self._pending_output_previous_switch_at = None
        self._consecutive_failures += 1

        # Exponential backoff: 5s, 12s, 25s, 45s
        delays = [5, 12, 25, 45]
        delay_idx = min(self._consecutive_failures - 1, len(delays) - 1)
        delay_sec = delays[delay_idx]

        self._blocked_until = now + timedelta(seconds=delay_sec)
        if self._last_blocked_log is None or now - self._last_blocked_log > timedelta(minutes=2):
            _LOGGER.warning(
                "HEMS: circuit breaker — %d consecutive failures, blocked for %ds",
                self._consecutive_failures, delay_sec,
            )
            self._last_blocked_log = now

    def report_control_success(self) -> None:
        """Report a successful control write — reset circuit breaker."""
        self._consecutive_failures = 0
        self._blocked_until = None
        self._pending_commands.clear()
        self._pending_output_previous_switch_at = None

    def check_keepalive(self, battery_power: float, soc: float, now: datetime | None = None) -> HemsDecision | None:
        """Check if battery keepalive is needed.

        If battery inactive (|power| < 50W) for 2+ hours while on USB mode,
        briefly switch to SBU to wake the BMS.

        Returns a HemsDecision to apply, or None if no keepalive needed.
        """
        now = now or datetime.now()
        self._track_battery_activity(battery_power, now)

        if self.keepalive.in_progress:
            return None

        if soc <= self.keepalive.MIN_SOC:
            return None

        if self.keepalive.last_activity_at is None:
            return None

        inactive_duration = now - self.keepalive.last_activity_at
        if inactive_duration < timedelta(hours=self.keepalive.INTERVAL_HOURS):
            return None

        # Battery has been inactive for 2+ hours → wake it up
        self.keepalive.in_progress = True
        _LOGGER.info(
            "HEMS: battery keepalive — inactive for %.1fh, switching to SBU for %ds",
            inactive_duration.total_seconds() / 3600,
            self.keepalive.DURATION_SEC,
        )

        return HemsDecision(
            output_priority=OutputPriority.SBU,
            reason=_Reason.KEEPALIVE_START,
        )

    def finish_keepalive(self, now: datetime | None = None) -> HemsDecision:
        """Finish keepalive — switch back to USB."""
        now = now or datetime.now()
        self.keepalive.in_progress = False
        self.keepalive.last_activity_at = now
        _LOGGER.info("HEMS: keepalive finished — back to USB")
        return HemsDecision(
            output_priority=OutputPriority.USB,
            reason=_Reason.KEEPALIVE_END,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # PRIVATE — Adaptive mode (full decision tree)
    # ═══════════════════════════════════════════════════════════════════════

    def _evaluate_adaptive(
        self,
        *,
        soc: float,
        pv_power: float,
        grid_power: float,
        battery_power: float,
        load_power: float,
        grid_available: bool,
        current_output: str | None,
        current_charger: str | None,
        now: datetime,
        forecast_tomorrow_kwh: float | None,
        forecast_day_after_kwh: float | None,
        reserve_soc: float,
        min_operating_soc: float,
        tarif_day: float,
        tarif_night: float,
        buzzer_off: bool,
        predictive_hint: Any | None = None,
        predictive_plan: Any | None = None,
        predictive_mode: str = "off",
    ) -> HemsDecision:
        """Full adaptive mode — ported from Dart executeAdaptiveMode.

        Decision flow:
        0. Safety hard floor handled by evaluate() before mode dispatch
        1. Manual override (already handled above)
        2. Night tariff window: USB always, tariff-aware charging
        3. Daytime: charger OSO, critical recovery, surplus detection
        4. Evening protection: 5 conditions
        """
        hour = now.hour
        is_night = hour >= 23 or hour < 7
        surplus = pv_power - load_power

        # ── Step 1: Night window (23:00–07:00) ───────────────────────
        if is_night:
            return self._adaptive_night(
                soc=soc,
                pv_power=pv_power,
                load_power=load_power,
                battery_power=battery_power,
                forecast_tomorrow_kwh=forecast_tomorrow_kwh,
                current_output=current_output,
                current_charger=current_charger,
                now=now,
                reserve_soc=reserve_soc,
                tarif_day=tarif_day,
                tarif_night=tarif_night,
                buzzer_off=buzzer_off,
                predictive_hint=predictive_hint,
                predictive_mode=predictive_mode,
            )

        # ── Step 2: Daytime ───────────────────────────────────────────
        return self._adaptive_day(
            soc=soc,
            pv_power=pv_power,
            load_power=load_power,
            battery_power=battery_power,
            surplus=surplus,
            forecast_tomorrow_kwh=forecast_tomorrow_kwh,
            current_output=current_output,
            current_charger=current_charger,
            now=now,
            reserve_soc=reserve_soc,
            min_operating_soc=min_operating_soc,
            tarif_day=tarif_day,
            tarif_night=tarif_night,
            buzzer_off=buzzer_off,
            predictive_hint=predictive_hint,
            predictive_plan=predictive_plan,
            predictive_mode=predictive_mode,
        )

    def _adaptive_night(
        self,
        *,
        soc: float,
        pv_power: float,
        load_power: float,
        battery_power: float,
        forecast_tomorrow_kwh: float | None,
        current_output: str | None,
        current_charger: str | None,
        now: datetime,
        reserve_soc: float,
        tarif_day: float,
        tarif_night: float,
        buzzer_off: bool,
        predictive_hint: Any | None = None,
        predictive_mode: str = "off",
    ) -> HemsDecision:
        """Night mode — USB output, tariff-aware charging.

        Ported from Dart Adaptive Mode Step 2 (night window).
        """
        # Charger decision: charge from grid at night if cheap
        # Simple heuristic: always SNU at night (cheap tariff)
        charger = ChargerPriority.SNU
        output = OutputPriority.USB

        # If SOC is near full and no load deficit, use OSO (solar only)
        if soc >= 80 and pv_power > load_power:
            charger = ChargerPriority.OSO

        # Assist can stop grid charging once a valid morning target is met.
        # The common reserve floor has priority over this optimization.
        morning_target = _hint_soc_target(predictive_hint, "target_soc_morning")
        if predictive_mode == "assist" and morning_target is not None and soc >= morning_target:
            charger = ChargerPriority.OSO

        return HemsDecision(
            output_priority=output,
            charger_priority=charger,
            reason=_Reason.NIGHT_USB,
            buzzer_off=buzzer_off,
        )

    def _adaptive_day(
        self,
        *,
        soc: float,
        pv_power: float,
        load_power: float,
        battery_power: float,
        surplus: float,
        forecast_tomorrow_kwh: float | None,
        current_output: str | None,
        current_charger: str | None,
        now: datetime,
        reserve_soc: float,
        min_operating_soc: float,
        tarif_day: float,
        tarif_night: float,
        buzzer_off: bool,
        predictive_hint: Any | None = None,
        predictive_plan: Any | None = None,
        predictive_mode: str = "off",
    ) -> HemsDecision:
        """Daytime mode — solar priority when surplus, grid fallback.

        Ported from Dart Adaptive Mode Step 3 (daytime).
        """
        hour = now.hour
        is_evening = hour >= 17

        # Charger: solar only during the day
        charger = ChargerPriority.OSO

        # ── Critical recovery: SOC < 35% → Force USB + SNU until 45% ──
        if soc < 35:
            if current_output != OutputPriority.USB or current_charger != ChargerPriority.SNU:
                _LOGGER.info(
                    "HEMS adaptive: critical recovery SOC=%.1f%% < 35%% → USB+SNU",
                    soc,
                )
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.SNU,
                reason="critical_recovery",
                buzzer_off=buzzer_off,
            )

        # ── Hysteresis: SOC 35-45% + on USB → maintain SNU ──────────
        if 35 <= soc <= 45 and current_output == OutputPriority.USB:
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.SNU,
                reason="hysteresis_recovery",
                buzzer_off=buzzer_off,
            )

        # ── Realtime surplus detection ───────────────────────────────
        pv_threshold = self.tuning.compute_adaptive_pv_surplus(now=now)

        if (
            pv_power > 0
            and soc >= min_operating_soc
            and surplus >= pv_threshold
        ):
            _LOGGER.debug(
                "HEMS adaptive: PV surplus %.0fW ≥ %.0fW → SBU+OSO",
                surplus, pv_threshold,
            )
            return HemsDecision(
                output_priority=OutputPriority.SBU,
                charger_priority=ChargerPriority.OSO,
                reason=_Reason.SURPLUS_SBU,
                buzzer_off=buzzer_off,
            )

        # ── Evening protection ────────────────────────────────────────
        if is_evening:
            return self._adaptive_evening_protection(
                soc=soc,
                surplus=surplus,
                battery_power=battery_power,
                forecast_tomorrow_kwh=forecast_tomorrow_kwh,
                now=now,
                reserve_soc=reserve_soc,
                buzzer_off=buzzer_off,
            )

        # ── Default branch: decide SBU vs USB based on whether
        # running the battery now would actually save anything.
        #
        # Round-trip losses through the inverter are ~10-15%. So draining
        # a fully-charged battery to feed a 700W load for an hour only
        # makes sense if we expect to recharge it cheaply (sun today /
        # tonight at off-peak tariff). Otherwise we waste the SOC and pay
        # to refill it later.
        #
        # Rule:
        #   - PV surplus (PV > load) → SBU+OSO so we store solar in
        #     the battery as we go. Handled above as SURPLUS_SBU.
        #   - Good forecast (tomorrow >= 1.0 kWh) → we'll recharge from
        #     sun, so SBU is fine even on cloudy afternoons.
        #   - SOC near full (>= 80%) and reasonable load (>150 W):
        #     draining saves noticeable grid imports → SBU.
        #   - Otherwise → USB+SNU. Don't touch the battery; let the
        #     charger top it up at off-peak if the forecast is bad.
        soc_healthy = soc >= max(reserve_soc + 5.0, 30.0)
        soc_full = soc >= 80.0
        good_forecast = forecast_tomorrow_kwh is not None and forecast_tomorrow_kwh >= 1.0
        load_significant = load_power >= 150.0

        if soc_full and load_significant and (good_forecast or pv_power > 50.0):
            return HemsDecision(
                output_priority=OutputPriority.SBU,
                charger_priority=ChargerPriority.OSO,
                reason="battery_drives_load",
                buzzer_off=buzzer_off,
            )

        if soc_healthy and good_forecast and load_significant:
            return HemsDecision(
                output_priority=OutputPriority.SBU,
                charger_priority=ChargerPriority.OSO,
                reason="forecast_good_use_battery",
                buzzer_off=buzzer_off,
            )

        # ── Default: don't waste grid energy on a battery that
        # doesn't need it. If we keep output=USB (load from grid),
        # the charger should be OSO (solar-only) unless SOC is low
        # enough that we'd genuinely benefit from topping up. This
        # eliminates the ~15% round-trip loss when SNU charges from
        # grid into a near-full battery.
        if soc >= 90.0:
            # Battery already topped off — don't waste grid power on it.
            # Keep it as a real backup reserve.
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.OSO,
                reason="day_default",
                buzzer_off=buzzer_off,
            )

        # ── SOC healthy but not full — still prefer solar-only charging.
        # Grid assistance (SNU) only makes sense if we expect to use the
        # stored energy soon (good evening load forecast).
        default_decision = HemsDecision(
            output_priority=OutputPriority.USB,
            charger_priority=ChargerPriority.OSO,
            reason="day_default",
            buzzer_off=buzzer_off,
        )

        # ── Predictive ASSIST override: when the planner is in
        # assist mode AND produced a hint AND that hint says we
        # need to be at/above the ML evening target by now AND the
        # SOC is close to (but not necessarily above) that target,
        # we may switch to battery-driven output (SBU+OSO) to use
        # the banked energy for the current load instead of
        # importing from grid.
        #
        # Hard guards — must all be true:
        #   1. predictive_mode == "assist"
        #   2. hint is not None (gate already enforced above)
        #   3. SOC ≥ reserve_soc (already on this branch)
        #   4. ML target evening is reasonable (50..90)
        #   5. SOC is within 5% of ML target — we're already
        #      "full enough", promoting to SBU makes sense
        #   6. forecast_tomorrow_kwh ≥ 1.0 OR pv_power > 250 —
        #      we'll recharge from sun tomorrow or now, so draining
        #      the battery doesn't cost us anything
        #   7. load_significant — there must be load to serve,
        #      otherwise the SBU change wastes a state transition
        if (
            predictive_mode == "assist"
            and predictive_hint is not None
            and getattr(predictive_hint, "target_soc_evening", None) is not None
            and _normalize_output(current_output) in (None, '', '0')
            and load_significant
        ):
            evening_target = _hint_soc_target(predictive_hint, "target_soc_evening")
            if (
                evening_target is not None
                and 50.0 <= evening_target <= 90.0
                and soc >= evening_target - 5.0
                and (good_forecast or pv_power > 250.0)
            ):
                return HemsDecision(
                    output_priority=OutputPriority.SBU,
                    charger_priority=ChargerPriority.OSO,
                    reason="predictive_assist_evening",
                    buzzer_off=buzzer_off,
                )

        return default_decision

    def _adaptive_evening_protection(
        self,
        *,
        soc: float,
        surplus: float,
        battery_power: float,
        forecast_tomorrow_kwh: float | None,
        now: datetime,
        reserve_soc: float,
        buzzer_off: bool,
    ) -> HemsDecision:
        """Evening protection — 5 conditions from Dart.

        1. SOC near reserve → Emergency USB + SNU
        2. Available energy ≤ safety margin → USB
        3. Deficit > 30% of available → USB
        4. SOC ≥ reserve+10 + deficit manageable → SBU
        5. Default: keep USB for safety
        """
        available_energy = soc  # simplified: SOC as percentage of available

        # Condition 1: SOC near reserve → emergency
        if soc <= reserve_soc + 5:
            _LOGGER.info(
                "HEMS adaptive: evening emergency SOC=%.1f%% near reserve → USB+SNU",
                soc,
            )
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.SNU,
                reason=_Reason.EVENING_PROTECT,
                buzzer_off=buzzer_off,
            )

        # Condition 2: Available energy very low
        if available_energy <= 15:
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.SNU,
                reason=_Reason.EVENING_PROTECT,
                buzzer_off=buzzer_off,
            )

        # Condition 3: High deficit (surplus negative, large)
        if surplus < 0 and abs(surplus) > available_energy * 0.3 * 100:
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.SNU,
                reason=_Reason.EVENING_PROTECT,
                buzzer_off=buzzer_off,
            )

        # Condition 4: SOC comfortable, deficit manageable → use battery
        if soc >= reserve_soc + 10 and surplus >= -200:
            return HemsDecision(
                output_priority=OutputPriority.SBU,
                charger_priority=ChargerPriority.OSO,
                reason=_Reason.EVENING_BATTERY_USE,
                buzzer_off=buzzer_off,
            )

        # Condition 5: Default safety → USB but with OSO (solar-only
        # charging). Charging from grid at evening peak rates makes no
        # sense — we'd pay full price for energy we already have in the
        # battery (or will get tomorrow from solar).
        return HemsDecision(
            output_priority=OutputPriority.USB,
            charger_priority=ChargerPriority.OSO,
            reason=_Reason.EVENING_PROTECT,
            buzzer_off=buzzer_off,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # PRIVATE — Night Arbitrage mode
    # ═══════════════════════════════════════════════════════════════════════

    def _evaluate_arbitrage(
        self,
        *,
        soc: float,
        pv_power: float,
        load_power: float,
        current_output: str | None,
        current_charger: str | None,
        now: datetime,
        reserve_soc: float,
        buzzer_off: bool,
        predictive_hint: Any | None = None,
        predictive_plan: Any | None = None,
        predictive_mode: str = "off",
    ) -> HemsDecision:
        """Night Arbitrage mode — charge at night (cheap), discharge at day (expensive).

        Night (23:00-07:00): USB + SNU (charge from grid)
        Daytime: SBU if surplus + SOC ok; charger always OSO
        """
        hour = now.hour
        is_night = hour >= 23 or hour < 7

        if is_night:
            charger = ChargerPriority.SNU
            morning_target = _hint_soc_target(predictive_hint, "target_soc_morning")
            if predictive_mode == "assist" and morning_target is not None and soc >= morning_target:
                charger = ChargerPriority.OSO
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=charger,
                reason="arbitrage_night",
                buzzer_off=buzzer_off,
            )
        else:
            # Daytime: use solar, charge only from solar
            surplus = pv_power - load_power
            if soc >= reserve_soc + 10 and surplus > 0:
                return HemsDecision(
                    output_priority=OutputPriority.SBU,
                    charger_priority=ChargerPriority.OSO,
                    reason="arbitrage_day_sbu",
                    buzzer_off=buzzer_off,
                )
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.OSO,
                reason="arbitrage_day_usb",
                buzzer_off=buzzer_off,
            )

    # ═══════════════════════════════════════════════════════════════════════
    # PRIVATE — Storm mode
    # ═══════════════════════════════════════════════════════════════════════

    def _evaluate_storm(
        self,
        *,
        soc: float,
        current_output: str | None,
        current_charger: str | None,
        buzzer_off: bool,
        predictive_hint: Any | None = None,
        predictive_mode: str = "off",
    ) -> HemsDecision:
        """Storm mode — maximize backup readiness.

        Force USB + SNU (precharge battery from grid for expected outage).
        Predictive ML CANNOT weaken this — storm always wins over ML.
        """
        # Storm never weakens safety — the storm decision is a hard
        # "charge for the outage". Even if ML hint says "drop charger
        # to OSO", we keep SNU. ML presence is recorded for sensor
        # surfaces but ignored here.
        return HemsDecision(
            output_priority=OutputPriority.USB,
            charger_priority=ChargerPriority.SNU,
            reason="storm_mode",
            buzzer_off=buzzer_off,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # PRIVATE — Anti-flapping
    # ═══════════════════════════════════════════════════════════════════════

    def _apply_anti_flapping(
        self, decision: HemsDecision, now: datetime
    ) -> HemsDecision:
        """Suppress rapid mode switching via dwell time + command dedup."""

        # Command dedup: skip if same command within window
        if decision.output_priority and decision.output_priority == self._last_cmd_output:
            if (
                self._last_cmd_output_at
                and (now - self._last_cmd_output_at) < self._command_dedup_window
            ):
                decision.output_priority = None  # skip
                decision.reason = _Reason.DEDUP_OUTPUT

        if decision.charger_priority and decision.charger_priority == self._last_cmd_charger:
            if (
                self._last_cmd_charger_at
                and (now - self._last_cmd_charger_at) < self._command_dedup_window
            ):
                decision.charger_priority = None  # skip
                decision.reason = _Reason.DEDUP_CHARGER

        # Dwell lock: don't switch output too frequently
        if decision.output_priority and self._last_output_switch_at:
            elapsed = (now - self._last_output_switch_at).total_seconds() / 60
            if elapsed < self._current_dwell_min:
                # Allow USB (safety) to always go through
                if decision.output_priority != OutputPriority.USB:
                    decision.output_priority = None
                    decision.reason = _Reason.DWELL_LOCK
                else:
                    # Reset dwell when switching to USB (safety override)
                    self._last_output_switch_at = now
            else:
                self._last_output_switch_at = now
        elif decision.output_priority:
            self._last_output_switch_at = now

        return decision

    # ═══════════════════════════════════════════════════════════════════════
    # PRIVATE — Battery activity tracking
    # ═══════════════════════════════════════════════════════════════════════

    def _track_battery_activity(self, battery_power: float, now: datetime) -> None:
        """Track battery activity for keepalive timing."""
        if abs(battery_power) > self.keepalive.POWER_INACTIVE_THRESHOLD:
            self.keepalive.last_activity_at = now
