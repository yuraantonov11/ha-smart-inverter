"""HEMS Engine — core decision logic for inverter control.

Ported from Flutter HemsAlgorithmService (1034 lines).
Manages output priority, charger priority, battery keepalive,
manual override, anti-flapping, circuit breaker, and all smart modes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any

from .tuning import AdaptiveThresholds, HemsTuningService, HemsTunables

_LOGGER = logging.getLogger(__name__)


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

        # Last applied buzzer state
        self._last_buzzer: str | None = None

    # ═══════════════════════════════════════════════════════════════════════
    # PUBLIC API — called by coordinator
    # ═══════════════════════════════════════════════════════════════════════

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
    ) -> HemsDecision:
        """Run one HEMS evaluation cycle.

        Returns a HemsDecision with the recommended output/charger priorities.
        The coordinator is responsible for applying (or skipping) the commands.
        """
        now = now or datetime.now()
        soc = max(0.0, min(100.0, soc))
        reserve_soc = reserve_soc if reserve_soc is not None else self.tun.reserve_soc
        min_operating_soc = (
            min_operating_soc
            if min_operating_soc is not None
            else self.tun.min_operating_soc
        )

        # Update tracking
        self._last_realtime_at = now
        self._track_battery_activity(battery_power, now)

        # Update adaptive thresholds
        self.tuning.update_surplus(pv_power - load_power)
        self._current_dwell_min = self.tuning.compute_adaptive_dwell()

        # Acoustic comfort
        buzzer_off = self.tuning.should_reduce_buzzer()

        if not hems_auto:
            return HemsDecision(
                reason="hems_auto_off", skip=True, buzzer_off=buzzer_off
            )

        # ── Circuit breaker: block writes during backoff ──────────────
        if self._blocked_until and now < self._blocked_until:
            return HemsDecision(reason="circuit_breaker", skip=True, buzzer_off=buzzer_off)

        # ── Manual override hold ──────────────────────────────────────
        if self._manual_override_until and now < self._manual_override_until:
            remaining = (self._manual_override_until - now).total_seconds()
            if now - (self._last_manual_override_log or now) > timedelta(minutes=5):
                _LOGGER.info("HEMS: manual override hold (%.0fs remaining)", remaining)
                self._last_manual_override_log = now
            return HemsDecision(reason=_Reason.MANUAL_OVERRIDE, skip=True, buzzer_off=buzzer_off)

        # ── Emergency: stale data ─────────────────────────────────────
        if self._last_realtime_at and (now - self._last_realtime_at) > timedelta(minutes=30):
            if soc < 30 or (now.hour >= 23 or now.hour < 7):
                _LOGGER.warning("HEMS: stale data >30min, forcing USB (emergency)")
                return HemsDecision(
                    output_priority=OutputPriority.USB,
                    charger_priority=ChargerPriority.SNU,
                    reason=_Reason.EMERGENCY_STALE,
                    buzzer_off=buzzer_off,
                )

        # ── Dispatch by smart mode ────────────────────────────────────
        if smart_mode == SmartMode.ADAPTIVE:
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
            )
        elif smart_mode == SmartMode.STORM:
            decision = self._evaluate_storm(
                soc=soc,
                current_output=current_output,
                current_charger=current_charger,
                buzzer_off=buzzer_off,
            )
        else:
            return HemsDecision(reason="unknown_mode", skip=True, buzzer_off=buzzer_off)

        # ── Apply anti-flapping ───────────────────────────────────────
        if not decision.skip:
            decision = self._apply_anti_flapping(decision, now)

        # ── Track command history ─────────────────────────────────────
        if decision.output_priority is not None:
            self._last_cmd_output = decision.output_priority
            self._last_cmd_output_at = now
        if decision.charger_priority is not None:
            self._last_cmd_charger = decision.charger_priority
            self._last_cmd_charger_at = now

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

        if actual_output and self._last_cmd_output and actual_output != self._last_cmd_output:
            if self._last_cmd_output_at and (now - self._last_cmd_output_at).total_seconds() > 30:
                self._manual_override_until = now + timedelta(minutes=self.tun.manual_override_hold_min)
                _LOGGER.info(
                    "HEMS: manual override detected (output %s → %s), hold for %d min",
                    self._last_cmd_output, actual_output, self.tun.manual_override_hold_min,
                )
                return True

        if actual_charger and self._last_cmd_charger and actual_charger != self._last_cmd_charger:
            if self._last_cmd_charger_at and (now - self._last_cmd_charger_at).total_seconds() > 30:
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

    def report_control_failure(self, now: datetime | None = None) -> None:
        """Report a control write failure — circuit breaker logic."""
        now = now or datetime.now()
        self._consecutive_failures += 1

        # Exponential backoff: 5s, 12s, 25s, 45s
        delays = [5, 12, 25, 45]
        delay_idx = min(self._consecutive_failures - 1, len(delays) - 1)
        delay_sec = delays[delay_idx]

        self._blocked_until = now + timedelta(seconds=delay_sec)
        if now - (self._last_blocked_log or now) > timedelta(minutes=2):
            _LOGGER.warning(
                "HEMS: circuit breaker — %d consecutive failures, blocked for %ds",
                self._consecutive_failures, delay_sec,
            )
            self._last_blocked_log = now

    def report_control_success(self) -> None:
        """Report a successful control write — reset circuit breaker."""
        self._consecutive_failures = 0
        self._blocked_until = None

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
    ) -> HemsDecision:
        """Full adaptive mode — ported from Dart executeAdaptiveMode.

        Decision flow:
        0. Safety hard floor: SOC ≤ reserve+2% → USB + SNU
        1. Manual override (already handled above)
        2. Night tariff window: USB always, tariff-aware charging
        3. Daytime: charger OSO, critical recovery, surplus detection
        4. Evening protection: 5 conditions
        """
        hour = now.hour
        is_night = hour >= 23 or hour < 7
        surplus = pv_power - load_power

        # ── Step 0: SAFETY hard floor ────────────────────────────────
        if soc <= reserve_soc + 2:
            if current_output != OutputPriority.USB or current_charger != ChargerPriority.SNU:
                _LOGGER.info(
                    "HEMS adaptive: safety floor SOC=%.1f%% ≤ reserve+2%% (%.1f%%) → USB+SNU",
                    soc, reserve_soc + 2,
                )
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.SNU,
                reason=_Reason.RESERVE_PROTECTION,
                buzzer_off=buzzer_off,
            )

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
        pv_threshold = self.tuning.compute_adaptive_pv_surplus()

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

        # ── Default: not enough sun → USB + SNU ──────────────────────
        return HemsDecision(
            output_priority=OutputPriority.USB,
            charger_priority=ChargerPriority.SNU,
            reason="day_default",
            buzzer_off=buzzer_off,
        )

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

        # Condition 5: Default safety → USB
        return HemsDecision(
            output_priority=OutputPriority.USB,
            charger_priority=ChargerPriority.SNU,
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
    ) -> HemsDecision:
        """Night Arbitrage mode — charge at night (cheap), discharge at day (expensive).

        Night (23:00-07:00): USB + SNU (charge from grid)
        Daytime: SBU if surplus + SOC ok; charger always OSO
        """
        hour = now.hour
        is_night = hour >= 23 or hour < 7

        if is_night:
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.SNU,
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
    ) -> HemsDecision:
        """Storm mode — maximize backup readiness.

        Force USB + SNU (precharge battery from grid for expected outage).
        """
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
