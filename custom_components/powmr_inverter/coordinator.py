"""DataUpdateCoordinator for Smart Solar Inverter — polls real-time data every 5s.

Integrates the HEMS engine for intelligent inverter control, including:
- Adaptive/Night Arbitrage/Storm modes
- Battery keepalive (anti-sleep)
- Manual override detection
- Circuit breaker for control writes
- Storm risk auto-activation
- Grid outage auto-Storm
- Acoustic comfort (night buzzer off)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import InverterApiClient, InverterOfflineError, TokenExpiredError
from .const import DOMAIN
from .hems.forecast import ForecastService
from .hems.soc_correction import get_real_soc
from .hems.engine import HemsEngine, SmartMode, OutputPriority, ChargerPriority, HemsDecision
from .hems.tuning import HemsTunables, HemsTuningService
from .hems.storm_risk import evaluate_storm_risk
from .hems.schedule_rules import ScheduleRulesService
from .hems.demand_forecast import DemandForecastService
from .hems.battery_soh import BatterySoH

_LOGGER = logging.getLogger(__name__)


class InverterCoordinator(DataUpdateCoordinator):
    """Coordinator that polls Inverter API and computes derived values.

    Now includes the full HEMS engine for intelligent control.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api: InverterApiClient,
        entry: ConfigEntry,
        update_interval: timedelta = timedelta(seconds=5),
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.api = api
        self._entry = entry
        self._consecutive_nulls = 0

        # HEMS state
        self.smart_mode: int = 0  # 0=Adaptive, 1=Arbitrage, 2=Storm
        self.hems_auto_mode: bool = True

        # ── HEMS Engine ───────────────────────────────────────────────
        tunables = HemsTunables(
            reserve_soc=float(entry.options.get("reserve_soc", 20.0)),
            pv_surplus_enter_w=float(entry.options.get("pv_surplus_threshold_w", 250.0)),
        )
        self._tuning = HemsTuningService(tunables)
        self._hems = HemsEngine(tunables=tunables, tuning=self._tuning)

        # ── Schedule Rules ────────────────────────────────────────────
        self._schedule_rules = ScheduleRulesService()
        rules_data = entry.data.get("schedule_rules", {})
        if rules_data:
            self._schedule_rules.load_from_dict(rules_data)

        # ── Demand Forecast ───────────────────────────────────────────
        self._demand_forecast = DemandForecastService()
        demand_data = entry.data.get("demand_forecast_profile", {})
        if demand_data:
            self._demand_forecast.load_from_dict(demand_data)

        # ── Battery SoH ───────────────────────────────────────────────
        soh_data = entry.data.get("battery_soh", {})
        install_date_str = entry.options.get("battery_install_date")
        install_date = None
        if install_date_str:
            try:
                from datetime import datetime as dt
                install_date = dt.fromisoformat(install_date_str)
            except (ValueError, TypeError):
                pass
        self._battery_soh = BatterySoH(
            cycle_count=soh_data.get("cycle_count", 0),
            in_low_state=soh_data.get("in_low_state", False),
            install_date=install_date,
        )

        # ── Auto House Load Reserve ───────────────────────────────────
        self._auto_house_reserve_enabled: bool = entry.options.get("auto_house_load_reserve", False)
        self._house_load_reserve_w: float = float(entry.options.get("house_load_reserve_w", 600.0))
        self._last_auto_reserve_persist_at: datetime | None = None

        # Grid outage detector state
        self._grid_outage_down_count: int = 0
        self._grid_outage_up_count: int = 0
        self._grid_available: bool = True
        self._grid_initialized: bool = False

        # Battery tracker state
        self._battery_in_low: bool = False
        self._battery_cycle_count: int = 0

        # SOC history
        self._soc_history: list[dict[str, Any]] = []
        self._last_soc_sample_at: datetime | None = None

        # Battery keepalive state (tracked in engine)
        self._keepalive_timer: datetime | None = None

        # Load demand EWMA profile
        self._load_profile: dict[int, float] = {}

        # ── Forecast & Economics ──────────────────────────────────────
        self._last_midnight: datetime | None = None
        self._daily_pv_kwh: float = 0.0
        self._daily_grid_import_day_kwh: float = 0.0
        self._daily_grid_import_night_kwh: float = 0.0
        self._daily_grid_export_kwh: float = 0.0
        self._daily_battery_discharge_day_kwh: float = 0.0
        self._daily_battery_discharge_night_kwh: float = 0.0
        self._daily_savings_uah: float = 0.0
        self._monthly_savings_uah: float = 0.0
        self._day_tariff_uah: float = float(entry.options.get("tariff_day", 4.32))
        self._night_tariff_uah: float = float(entry.options.get("tariff_night", 2.16))

        # Forecast service (activated on first update)
        self._forecast: ForecastService | None = None
        self._forecast_last_fetch: datetime | None = None
        self.forecast_tomorrow_kwh: float | None = None
        self.forecast_day_after_kwh: float | None = None
        self.forecast_learned_ratio: float = 0.12
        self.radiation_now_wm2: float | None = None
        self.hourly_forecast_today: list[float] = []  # 24 hourly power values (W) for sparkline

        # Storm risk tracking
        self._storm_risk_score: float = 0.0
        self._storm_risk_reason: str = ""
        self._auto_storm_active: bool = False
        self._previous_smart_mode_before_storm: int | None = None

        # ── HEMS diagnostics (readable by sensors) ────────────────────
        self.hems_last_reason: str = ""
        self.hems_last_output_cmd: str | None = None
        self.hems_last_charger_cmd: str | None = None
        self.hems_buzzer_off: bool = False

        # ── Public read-only properties for new modules ───────────────
        self.schedule_rules = self._schedule_rules
        self.demand_forecast = self._demand_forecast
        self.battery_soh = self._battery_soh
        self.house_load_reserve_w = self._house_load_reserve_w

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC PROPERTIES
    # ═══════════════════════════════════════════════════════════════════

    @property
    def grid_available(self) -> bool:
        return self._grid_available

    @property
    def hems_engine(self) -> HemsEngine:
        """Expose HEMS engine for external callers (services, manual override)."""
        return self._hems

    # ═══════════════════════════════════════════════════════════════════
    # MAIN UPDATE LOOP
    # ═══════════════════════════════════════════════════════════════════

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest data, compute derived values, and run HEMS engine."""
        now = datetime.now()

        try:
            raw = await self.api.fetch_realtime_data()
        except TokenExpiredError:
            _LOGGER.error("Token expired — triggering re-auth")
            self._entry.async_start_reauth(self.hass)
            return self._build_offline_state(now)
        except InverterOfflineError:
            self._consecutive_nulls += 1
            _LOGGER.warning(
                "Realtime offline (attempt #%d), using fallback state",
                self._consecutive_nulls,
            )
            return self._build_offline_state(now)

        if raw is None:
            self._consecutive_nulls += 1
            _LOGGER.warning(
                "No realtime data (attempt #%d), using fallback state",
                self._consecutive_nulls,
            )
            return self._build_offline_state(now)

        self._consecutive_nulls = 0

        # ── Compute corrected SOC ────────────────────────────────────
        reported_soc = raw.get("batterySoc", 100.0)
        voltage = raw.get("batteryVoltage", 52.0)
        current = raw.get("batteryCurrent", 0.0)
        corrected_soc = get_real_soc(reported_soc, voltage, current)

        # ── Grid outage detection ────────────────────────────────────
        grid_v = raw.get("gridVoltage", 230.0)
        grid_available, grid_transition = self._evaluate_grid(grid_v)

        # ── Grid outage auto-Storm ───────────────────────────────────
        if grid_transition == "outage" and self.smart_mode == SmartMode.ADAPTIVE and self.hems_auto_mode:
            _LOGGER.warning("⚡ Grid outage → auto-activating Storm mode")
            self._previous_smart_mode_before_storm = self.smart_mode
            self.smart_mode = SmartMode.STORM
            self._auto_storm_active = True

        # ── Battery cycle tracking ───────────────────────────────────
        cycle_completed = self._track_battery_cycle(corrected_soc)
        if cycle_completed:
            self._battery_cycle_count += 1

        # ── SOC history ──────────────────────────────────────────────
        self._add_soc_sample(raw, corrected_soc)

        # ── EWMA load profile update ─────────────────────────────────
        self._update_load_profile(now.hour, raw.get("loadPower", 0.0))

        # ── CO2 update ───────────────────────────────────────────────
        self.api._update_co2()

        # ── Device settings ──────────────────────────────────────────
        device_settings = self.data.get("deviceSettings", {}) if self.data else {}
        if self._consecutive_nulls == 0 and (
            not device_settings or now.second < 10
        ):
            try:
                device_settings = await self.api.fetch_device_configs()
                keys_found = len(device_settings)
                if keys_found:
                    _LOGGER.info("🔧 Device settings loaded: %d keys", keys_found)
            except Exception as exc:
                _LOGGER.debug("Device settings fetch skipped: %s", exc)
                device_settings = self.data.get("deviceSettings", {}) if self.data else {}

        # ── Daily energy & savings tracking ──────────────────────────
        self._accumulate_daily_energy(now, raw)

        # ── Forecast refresh (every 15 min) ─────────────────────────
        await self._maybe_refresh_forecast(now)

        # ── Storm risk evaluation ────────────────────────────────────
        await self._maybe_evaluate_storm_risk(now)

        # ═══════════════════════════════════════════════════════════════
        # HEMS ENGINE — Run decision cycle
        # ═══════════════════════════════════════════════════════════════
        if self.hems_auto_mode:
            await self._run_hems_engine(raw, corrected_soc, now)

        return {
            **raw,
            "correctedSoc": corrected_soc,
            "gridAvailable": grid_available,
            "gridTransition": grid_transition,
            "online": True,
            "lastUpdated": now.isoformat(),
            "deviceSettings": device_settings,
        }

    # ═══════════════════════════════════════════════════════════════════
    # HEMS ENGINE INTEGRATION
    # ═══════════════════════════════════════════════════════════════════

    async def _run_hems_engine(
        self, raw: dict[str, Any], corrected_soc: float, now: datetime
    ) -> None:
        """Run the HEMS engine and execute control commands."""
        current_output = raw.get("outputSourcePriority", "")
        current_charger = raw.get("chargerSourcePriority", "")
        # Detect manual override
        self._hems.detect_manual_override(current_output, current_charger, now)
        # ── Schedule Rules: override smart mode if active rule exists ──
        active_rule = self._schedule_rules.get_active_rule_now(now)
        effective_mode = active_rule.mode if active_rule is not None else self.smart_mode

        # ── Update demand forecast with current load ───────────────────
        load_power = raw.get("loadPower", 0.0)
        self._demand_forecast.update_ewma(now, load_power)

        # ── Track battery SoH ─────────────────────────────────────────
        self._battery_soh.track_soc(corrected_soc)

        # ── Auto-tune house load reserve ──────────────────────────────
        self._maybe_auto_tune_house_reserve(load_power, now)

        # Check keepalive
        battery_power = raw.get("batteryPower", 0.0)
        keepalive_decision = self._hems.check_keepalive(battery_power, corrected_soc, now)
        if keepalive_decision and keepalive_decision.output_priority:
            await self._execute_hems_command(keepalive_decision)
            # Schedule keepalive end
            self._keepalive_timer = now + timedelta(seconds=self._hems.keepalive.DURATION_SEC)
            return

        # Finish keepalive if timer expired
        if self._hems.keepalive.in_progress and self._keepalive_timer and now >= self._keepalive_timer:
            finish = self._hems.finish_keepalive(now)
            await self._execute_hems_command(finish)
            self._keepalive_timer = None
            return

        # Skip if keepalive in progress
        if self._hems.keepalive.in_progress:
            return

        # Run main HEMS evaluation
        pv_power = raw.get("pvPower", 0.0)
        grid_power = raw.get("gridPower", 0.0)
        load_power = raw.get("loadPower", 0.0)

        decision = self._hems.evaluate(
            smart_mode=effective_mode,
            hems_auto=self.hems_auto_mode,
            soc=corrected_soc,
            pv_power=pv_power,
            grid_power=grid_power,
            battery_power=battery_power,
            load_power=load_power,
            grid_voltage=raw.get("gridVoltage", 230.0),
            grid_available=self._grid_available,
            current_output=current_output,
            current_charger=current_charger,
            now=now,
            forecast_tomorrow_kwh=self.forecast_tomorrow_kwh,
            forecast_day_after_kwh=self.forecast_day_after_kwh,
            reserve_soc=float(self._entry.options.get("reserve_soc", 20.0)),
            tarif_day=self._day_tariff_uah,
            tarif_night=self._night_tariff_uah,
            is_online=True,
        )

        # Store diagnostics
        self.hems_last_reason = decision.reason
        self.hems_last_output_cmd = decision.output_priority
        self.hems_last_charger_cmd = decision.charger_priority
        self.hems_buzzer_off = decision.buzzer_off

        # Execute command
        if not decision.skip:
            await self._execute_hems_command(decision)

    async def _execute_hems_command(self, decision: HemsDecision) -> None:
        """Execute a HEMS decision by calling the API."""
        success = True

        try:
            if decision.output_priority is not None:
                ok = await self.api.set_output_priority(decision.output_priority)
                if not ok:
                    success = False
                    _LOGGER.warning("HEMS: failed to set output → %s (%s)", decision.output_priority, decision.reason)
                else:
                    _LOGGER.debug("HEMS: output → %s (%s)", decision.output_priority, decision.reason)

            if decision.charger_priority is not None:
                ok = await self.api.set_charger_priority(decision.charger_priority)
                if not ok:
                    success = False
                    _LOGGER.warning("HEMS: failed to set charger → %s (%s)", decision.charger_priority, decision.reason)
                else:
                    _LOGGER.debug("HEMS: charger → %s (%s)", decision.charger_priority, decision.reason)

            # Acoustic comfort: toggle buzzer
            if decision.buzzer_off and self._hems._last_buzzer != "0":
                await self.api.set_config_item("buzzerAlarmSetting", "0")
                self._hems._last_buzzer = "0"
                _LOGGER.debug("HEMS: buzzer OFF (acoustic comfort)")
            elif not decision.buzzer_off and self._hems._last_buzzer != "1":
                await self.api.set_config_item("buzzerAlarmSetting", "1")
                self._hems._last_buzzer = "1"
                _LOGGER.debug("HEMS: buzzer ON")

        except Exception as exc:
            _LOGGER.error("HEMS: control command failed: %s", exc)
            success = False

        if success:
            self._hems.report_control_success()
        else:
            self._hems.report_control_failure(datetime.now())

    # ═══════════════════════════════════════════════════════════════════
    # STORM RISK
    # ═══════════════════════════════════════════════════════════════════

    async def _maybe_evaluate_storm_risk(self, now: datetime) -> None:
        """Evaluate storm risk from forecast weather data (every 15 min)."""
        if self._forecast is None:
            return

        # Only check every 15 minutes
        if hasattr(self, "_last_storm_check") and self._last_storm_check:
            if (now - self._last_storm_check).total_seconds() < 900:
                return
        self._last_storm_check = now

        try:
            hourly = await self._forecast.get_hourly_forecast()
            if not hourly:
                return

            # Check next 6 hours
            from datetime import datetime as dt
            now_str = now.strftime("%Y-%m-%dT%H:00")
            upcoming = [h for h in hourly if h["time"] >= now_str][:6]

            max_risk_score = 0.0
            max_risk_reason = "clear"
            for h in upcoming:
                # Use radiation as proxy for weather intensity
                # (Open-Meteo weather_code would need separate call)
                risk = evaluate_storm_risk(
                    weather_code=None,
                    wind_speed_ms=0,
                    precipitation_probability=0,
                )
                if risk.score > max_risk_score:
                    max_risk_score = risk.score
                    max_risk_reason = risk.reason

            self._storm_risk_score = max_risk_score
            self._storm_risk_reason = max_risk_reason

            # Auto-activate Storm if risk high and not already in Storm
            auto_storm_enabled = self._entry.options.get("auto_storm_by_forecast", False)
            if (
                auto_storm_enabled
                and max_risk_score >= 0.6
                and not self._auto_storm_active
                and self.smart_mode != SmartMode.STORM
            ):
                _LOGGER.warning(
                    "🌊 Storm risk %.0f%% (%s) → auto-activating Storm mode",
                    max_risk_score * 100, max_risk_reason,
                )
                self._previous_smart_mode_before_storm = self.smart_mode
                self.smart_mode = SmartMode.STORM
                self._auto_storm_active = True

            # Clear auto-storm when risk drops
            if self._auto_storm_active and max_risk_score < 0.4:
                if self._previous_smart_mode_before_storm is not None:
                    _LOGGER.info("🌊 Storm risk cleared → restoring mode %d", self._previous_smart_mode_before_storm)
                    self.smart_mode = self._previous_smart_mode_before_storm
                self._auto_storm_active = False
                self._previous_smart_mode_before_storm = None

        except Exception as exc:
            _LOGGER.debug("Storm risk evaluation failed: %s", exc)

    # ═══════════════════════════════════════════════════════════════════
    # EXISTING HELPERS (unchanged from original)
    # ═══════════════════════════════════════════════════════════════════

    def _build_offline_state(self, now: datetime) -> dict[str, Any]:
        """Return a stable offline payload to keep entities available."""
        if self.data is not None:
            fallback = dict(self.data)
            fallback["online"] = False
            fallback["lastUpdated"] = now.isoformat()
            return fallback

        return {
            "pvPower": 0.0, "gridPower": 0.0, "batteryPower": 0.0,
            "loadPower": 0.0, "batterySoc": 100.0, "pvVoltage": 0.0,
            "gridVoltage": 230.0, "batteryVoltage": 52.0,
            "loadPercentage": 0.0, "workingMode": "unknown",
            "outputSourcePriority": "", "chargerSourcePriority": "",
            "batteryCurrent": 0.0, "correctedSoc": 100.0,
            "gridAvailable": True, "gridTransition": "none",
            "online": False, "lastUpdated": now.isoformat(),
        }

    def _evaluate_grid(self, grid_voltage: float) -> tuple[bool, str]:
        """Evaluate grid availability with hysteresis."""
        if not self._grid_initialized:
            self._grid_initialized = True
            self._grid_available = grid_voltage >= 130.0
            self._grid_outage_down_count = 0
            self._grid_outage_up_count = 0
            return self._grid_available, "none"

        transition = "none"

        if self._grid_available:
            if grid_voltage <= 90.0:
                self._grid_outage_down_count += 1
                if self._grid_outage_down_count >= 2:
                    self._grid_available = False
                    self._grid_outage_down_count = 0
                    self._grid_outage_up_count = 0
                    transition = "outage"
                    _LOGGER.warning("⚡ Grid OUTAGE detected (V=%.1f)", grid_voltage)
            else:
                self._grid_outage_down_count = 0
        else:
            if grid_voltage >= 130.0:
                self._grid_outage_up_count += 1
                if self._grid_outage_up_count >= 2:
                    self._grid_available = True
                    self._grid_outage_up_count = 0
                    self._grid_outage_down_count = 0
                    transition = "restored"
                    _LOGGER.info("🔌 Grid RESTORED (V=%.1f)", grid_voltage)
            else:
                self._grid_outage_up_count = 0

        return self._grid_available, transition

    def _track_battery_cycle(self, soc: float) -> bool:
        """Track low→high SOC transitions for cycle counting.

        Delegates to BatterySoH tracker.
        """
        return self._battery_soh.track_soc(soc)

    def _add_soc_sample(self, raw: dict, corrected_soc: float) -> None:
        """Add a sample to the rolling 24h SOC history."""
        now = datetime.now()
        if self._last_soc_sample_at is not None:
            if (now - self._last_soc_sample_at).total_seconds() < 270:
                return

        self._last_soc_sample_at = now
        sample = {
            "t": now.timestamp(), "soc": corrected_soc,
            "pv": raw.get("pvPower", 0.0), "load": raw.get("loadPower", 0.0),
            "battery": raw.get("batteryPower", 0.0),
        }
        self._soc_history.append(sample)
        cutoff = now - timedelta(hours=24)
        self._soc_history = [s for s in self._soc_history if s["t"] >= cutoff.timestamp()]
        if len(self._soc_history) > 288:
            self._soc_history = self._soc_history[-288:]

    def _update_load_profile(self, hour: int, load_w: float) -> None:
        """Update EWMA load profile (α=0.25)."""
        clamped = max(100.0, min(12000.0, load_w))
        alpha = 0.25
        old = self._load_profile.get(hour, clamped)
        self._load_profile[hour] = alpha * clamped + (1 - alpha) * old

    @staticmethod
    def _is_daytime(now: datetime) -> bool:
        """Ukrainian two-zone tariff: day 07:00–23:00, night 23:00–07:00."""
        return 7 <= now.hour < 23

    def _accumulate_daily_energy(self, now: datetime, raw: dict[str, Any]) -> None:
        """Integrate 5-second power samples into daily kWh totals."""
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self._last_midnight != today:
            self._last_midnight = today
            self._monthly_savings_uah += self._daily_savings_uah
            self._daily_pv_kwh = 0.0
            self._daily_grid_import_day_kwh = 0.0
            self._daily_grid_import_night_kwh = 0.0
            self._daily_grid_export_kwh = 0.0
            self._daily_battery_discharge_day_kwh = 0.0
            self._daily_battery_discharge_night_kwh = 0.0
            self._daily_savings_uah = 0.0
            if now.day == 1:
                self._monthly_savings_uah = 0.0
        dt_h = 5.0 / 3600.0
        daytime = self._is_daytime(now)

        pv_w = raw.get("pvPower", 0.0) or 0.0
        grid_w = raw.get("gridPower", 0.0) or 0.0
        battery_w = raw.get("batteryPower", 0.0) or 0.0

        self._daily_pv_kwh += pv_w * dt_h

        if grid_w > 10:
            if daytime:
                self._daily_grid_import_day_kwh += grid_w * dt_h
            else:
                self._daily_grid_import_night_kwh += grid_w * dt_h
        elif grid_w < -10:
            self._daily_grid_export_kwh += abs(grid_w) * dt_h

        # battery_w > 0 = charging, < 0 = discharging (solar.siseli.com API convention)
        if battery_w < -10:
            discharge_w = abs(battery_w)
            if daytime:
                self._daily_battery_discharge_day_kwh += discharge_w * dt_h
            else:
                self._daily_battery_discharge_night_kwh += discharge_w * dt_h

        # Savings = value of battery energy that displaced grid import.
        # When battery discharges, it powers the load instead of the grid.
        # Grid import still happens when battery is depleted or in SNU mode,
        # but that doesn't reduce the value of battery discharge.
        self._daily_savings_uah = round(
            self._daily_battery_discharge_day_kwh * self._day_tariff_uah
            + self._daily_battery_discharge_night_kwh * self._night_tariff_uah,
            2,
        )

    # ═══════════════════════════════════════════════════════════════════
    # AUTO HOUSE LOAD RESERVE
    # ═══════════════════════════════════════════════════════════════════

    def _estimate_house_load_reserve(self, load_w: float) -> float:
        """Estimate house load reserve from EWMA profile + live load.

        Uses max(profiled, live) × 1.15 + 150W, clamped to [200, 8000]W.
        """
        forecast = self._demand_forecast.to_demand_forecast()
        hour = datetime.now().hour
        profile_w = forecast.get_metrics_for_hour(hour).p50
        live_w = max(0.0, min(15000.0, load_w))

        if profile_w <= 0 and live_w <= 0:
            return max(200.0, min(8000.0, self._house_load_reserve_w))

        baseline = max(profile_w, live_w)
        with_headroom = baseline * 1.15 + 150.0
        return max(200.0, min(8000.0, with_headroom))

    def _maybe_auto_tune_house_reserve(self, load_w: float, now: datetime) -> None:
        """Auto-tune house load reserve with EMA smoothing."""
        if not self._auto_house_reserve_enabled:
            return

        suggested = self._estimate_house_load_reserve(load_w)
        delta = abs(suggested - self._house_load_reserve_w)
        if delta < 80.0:
            return  # dead-band

        # Smooth: 70% old + 30% new
        smoothed = max(200.0, min(8000.0, self._house_load_reserve_w * 0.7 + suggested * 0.3))
        changed_by = abs(smoothed - self._house_load_reserve_w)
        if changed_by < 30.0:
            return  # second dead-band

        old = self._house_load_reserve_w
        self._house_load_reserve_w = smoothed
        self.house_load_reserve_w = smoothed
        _LOGGER.debug(
            "Auto reserve: %.0fW → %.0fW (suggested=%.0fW, delta=%.0fW)",
            old, smoothed, suggested, changed_by,
        )

    @property
    def daily_savings_uah(self) -> float:
        return max(0.0, self._daily_savings_uah)

    @property
    def monthly_savings_uah(self) -> float:
        return max(0.0, self._monthly_savings_uah + self._daily_savings_uah)

    async def _maybe_refresh_forecast(self, now: datetime) -> None:
        """Fetch solar forecast every 15 minutes and update ratio daily at 21:00."""
        if self._forecast is None:
            lat = float(self._entry.options.get("site_latitude", 50.45))
            lon = float(self._entry.options.get("site_longitude", 30.52))
            self._forecast = ForecastService(latitude=lat, longitude=lon)

        if self._forecast_last_fetch is None or \
           (now - self._forecast_last_fetch).total_seconds() > 900:
            try:
                daily = await self._forecast.get_daily_forecasts(days=2)
                dates = sorted(daily.keys())
                if len(dates) >= 1:
                    self.forecast_tomorrow_kwh = daily[dates[0]].energy_kwh
                if len(dates) >= 2:
                    self.forecast_day_after_kwh = daily[dates[1]].energy_kwh
                self.forecast_learned_ratio = self._forecast.learned_ratio

                # Store hourly forecast for today (sparkline)
                hourly = await self._forecast.get_hourly_forecast()
                today_str = now.strftime("%Y-%m-%d")
                today_hours = [h["power_w"] for h in hourly if h["time"].startswith(today_str)]
                # Pad to 24 if needed
                if len(today_hours) < 24:
                    today_hours.extend([0.0] * (24 - len(today_hours)))
                self.hourly_forecast_today = today_hours[:24]

                self._forecast_last_fetch = now
                _LOGGER.debug(
                    "Forecast: tomorrow=%.1f kWh, day2=%.1f kWh, ratio=%.4f",
                    self.forecast_tomorrow_kwh or 0,
                    self.forecast_day_after_kwh or 0,
                    self.forecast_learned_ratio,
                )
            except Exception as exc:
                _LOGGER.warning("Forecast fetch failed: %s", exc)

        if now.hour == 21 and now.minute < 1 and self._daily_pv_kwh > 0.1:
            estimated_radiation = self._daily_pv_kwh / max(self.forecast_learned_ratio, 0.01)
            self._forecast.update_ratio(self._daily_pv_kwh, estimated_radiation)
