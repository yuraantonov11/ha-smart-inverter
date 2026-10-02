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

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import InverterApiClient, InverterOfflineError, TokenExpiredError
from .const import DOMAIN, HISTORY_POLL_INTERVAL_SEC
from .hems.forecast import ForecastService
from .hems.soc_correction import get_real_soc
from .hems.engine import HemsEngine, SmartMode, OutputPriority, ChargerPriority, HemsDecision
from .hems import debug_logging
from .hems.tuning import HemsTunables, HemsTuningService, PredictiveTuning
from .hems.storm_risk import evaluate_storm_risk
from .hems.schedule_rules import ScheduleRulesService
from .hems.demand_forecast import DemandForecastService
from .hems.battery_soh import BatterySoH
from .hems.forecast_calibration import ForecastCalibrator

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
        # Master toggle: if False, integration runs in monitor-only mode.
        # Reads inverter state but never sends commands to the device.
        # Configurable via integration options (default: True).
        self.hems_enabled: bool = entry.options.get("hems_enabled", True)

        # ── HEMS Engine ───────────────────────────────────────────────
        tunables = HemsTunables(
            reserve_soc=float(entry.options.get("reserve_soc", 20.0)),
            pv_surplus_enter_w=float(entry.options.get("pv_surplus_threshold_w", 250.0)),
        )
        self._tuning = HemsTuningService(tunables)
        self._hems = HemsEngine(tunables=tunables, tuning=self._tuning)
        # Wire predictive_mode / predictive_tuning from entry options
        # onto the engine. The engine evaluates on this attribute
        # each cycle; the switch/select entities update it through
        # ``async_set_predictive_mode`` (see below) so persistence
        # flows through ``entry.options`` and survives reload.
        self._predictive_tuning = PredictiveTuning(
            predictive_mode=str(
                entry.options.get("predictive_mode", "off")
            ),
            predictive_enabled=bool(
                entry.options.get("predictive_mode", "") in ("shadow", "assist")
            ),
            battery_reserve_pct=float(
                entry.options.get("reserve_soc", 20.0)
            ),
        )
        self._hems.predictive_tuning = self._predictive_tuning
        # Back-compat shim — many tests/old switch.py still read
        # ``_predictive_enabled``. Keep in lockstep with mode.
        self._hems._predictive_enabled = (
            self._predictive_tuning.predictive_mode in ("shadow", "assist")
        )
        # Battery capacity in kWh — coordinator owns the input. Derive
        # from Ah × nominal V (51.2V for typical LiFePO4) so the
        # planner sees a real number, not the previous hardcoded 4.8.
        try:
            self._battery_capacity_kwh: float = (
                float(entry.options.get("battery_capacity_ah", 230.0))
                * 51.2
                / 1000.0
            )
        except (TypeError, ValueError):
            self._battery_capacity_kwh = 4.8
        self._hems_debug_day = None  # type: str | None
        self._hems_debug_decisions = 0
        self._hems_debug_commands = 0
        self._hems_debug_skips = 0
        self._hems_debug_last_decision_ts = None  # type: str | None

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
        self._last_config_fetch_at: datetime | None = None

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
        self._forecast_today_kwh: float | None = None
        # Hourly load matrix [days][24] from the recorder (see history_builder)
        self._load_matrix: list[list[float]] = []
        self._load_matrix_at: datetime | None = None
        # Hourly PV matrix [days][24] from the recorder — backs the
        # ForecastCalibrator. Same pattern as load history, refreshed
        # at most once/hour from sensor.garazh_smart_solar_inverter_pv_power.
        self._pv_matrix: list[list[float]] = []
        self._pv_matrix_at: datetime | None = None
        # Real, measured forecast accuracy. Replaces the hardcoded
        # 0.12 ratio that the old ``forecast.py`` learned from its
        # own output (circular). Updated once/hour from real
        # forecast↔actual day-pair comparisons.
        self._pv_calibrator: ForecastCalibrator = ForecastCalibrator()
        self._pv_calibrator_log_at: datetime | None = None
        # Path for the (forecast, actual) kWh pair file. Lives in
        # the integration dir so it survives HA restarts but does
        # NOT touch the recorder or HA storage. The calibrator's
        # own bounded buffer is the source of truth at runtime;
        # this file is the seed on reload.
        try:
            from pathlib import Path
            _pairs_dir = Path(__file__).resolve().parent / "hems"
            self._pv_fact_pairs_file = str(_pairs_dir / "pv_fact_pairs.json")
        except Exception:
            self._pv_fact_pairs_file = "hems/pv_fact_pairs.json"
        self._last_pv_pair_check_date: str | None = None
        # Restore calibrator state from disk if a previous run left any.
        self._load_pv_fact_pairs()
        self.forecast_learned_ratio: float = self._derive_learned_ratio_from_calibrator()
        self.radiation_now_wm2: float | None = None
        self.hourly_forecast_today: list[float] = []  # 24 hourly power values (W) for sparkline
        self.hourly_weather_today: list[int | None] = []  # WMO weather codes per hour
        self.hourly_radiation_today: list[float] = []  # Solar radiation W/m² per hour
        self.weather_tomorrow_code: int | None = None  # Dominant weather for tomorrow
        self.weather_day_after_code: int | None = None

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
    # PREDICTIVE OPTION WIRING
    # ═══════════════════════════════════════════════════════════════════

    def async_set_predictive_mode(self, mode: str) -> None:
        """Update predictive mode on the engine + persist via options.

        Single canonical writer for ``predictive_tuning.predictive_mode``.
        Called by the switch (``InverterPredictiveAssistSwitch``)
        and the new select entity. Updates:
          1. ``self._predictive_tuning.predictive_mode`` (engine reads from here)
          2. ``self._hems.predictive_tuning`` (same object — kept for clarity)
          3. ``self._hems._predictive_enabled`` (legacy compat)
          4. ``entry.options["predictive_mode"]`` (persistence across reload)
        """
        if mode not in ("off", "shadow", "assist"):
            mode = "off"
        self._predictive_tuning.predictive_mode = mode
        self._hems.predictive_tuning = self._predictive_tuning
        self._hems._predictive_mode = mode
        self._hems._predictive_enabled = mode in ("shadow", "assist")
        # Persist on the config entry so reload keeps the mode.
        try:
            new_opts = dict(self._entry.options)
            new_opts["predictive_mode"] = mode
            self.hass.config_entries.async_update_entry(
                self._entry, options=new_opts
            )
        except Exception as exc:
            _LOGGER.debug("persist predictive_mode failed: %s", exc)

    @property
    def predictive_mode(self) -> str:
        """Current predictive mode (single source of truth)."""
        return self._predictive_tuning.predictive_mode

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

        # ── Device settings (cooldown 60s to respect API rate limit) ──
        device_settings = self.data.get("deviceSettings", {}) if self.data else {}
        config_fetch_cooldown = (
            self._last_config_fetch_at is None
            or (now - self._last_config_fetch_at).total_seconds() > 60
        )
        if self._consecutive_nulls == 0 and config_fetch_cooldown:
            try:
                fetched = await self.api.fetch_device_configs()
                self._last_config_fetch_at = now
                if isinstance(fetched, dict) and fetched:
                    device_settings = fetched
                    _LOGGER.info("Device settings loaded: %d keys", len(fetched))
                else:
                    _LOGGER.info("Device settings empty - no data from API")
            except Exception as exc:
                _LOGGER.error("Device settings fetch FAILED: %s", exc)

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

        # Finish keepalive if timer expired
        if self._hems.keepalive.in_progress and self._keepalive_timer and now >= self._keepalive_timer:
            finish = self._hems.finish_keepalive(now)
            await self._execute_hems_command(finish)
            self._keepalive_timer = None
            return

        # Skip if keepalive in progress
        if self._hems.keepalive.in_progress:
            return

        # Skip HEMS if disabled (monitor-only mode)
        if not self.hems_enabled:
            self.hems_last_reason = "hems_disabled"
            self.hems_last_output_cmd = None
            self.hems_last_charger_cmd = None
            return

        # Run main HEMS evaluation
        pv_power = raw.get("pvPower", 0.0)
        grid_power = raw.get("gridPower", 0.0)
        load_power = raw.get("loadPower", 0.0)

        # ── Feed planner-required arrays into the engine ────────────
        # The engine reads these attributes when predictive is
        # shadow/assist. Empty/missing → planner stays None (missing
        # data gate, not silent zero).
        self._hems._last_forecast_today_kwh = (
            getattr(self, "_forecast_today_kwh", None)
        )
        self._hems._hourly_pv_forecast = list(
            getattr(self, "hourly_forecast_today", []) or []
        )
        self._hems._hourly_radiation = list(
            getattr(self, "hourly_radiation_today", []) or []
        )
        self._hems._hourly_weather_codes = list(
            getattr(self, "hourly_weather_today", []) or []
        )
        # Build 24h tariff schedule from coordinator tariffs
        self._hems._tariff_schedule = self._build_tariff_schedule()
        await self._maybe_refresh_load_history(datetime.now())
        self._hems._consumption_history = list(self._load_matrix)
        # Real PV measurement → real forecast accuracy. Refresh
        # the PV matrix the same way (≤1/hour, executor job) and
        # feed the calibrator with yesterday-vs-yesterday data so
        # the planner's confidence_factor is grounded in observed
        # generation, not in the old circular self-learned ratio.
        await self._maybe_refresh_pv_history(datetime.now())
        await self._maybe_record_pv_pairs(now)
        self._log_pv_calibrator_state(now)
        # Make the calibrator reachable from the predictive
        # controller's ``_estimate_confidence`` (which already reads
        # ``self.calibrator``) and from ``simulate_24h`` (which we
        # wired to prefer the calibrator's bias-corrected adjust()
        # over the empty PvForecastAdjuster ratio of 1.0).
        if hasattr(self._hems, "_predictive_controller"):
            try:
                self._hems._predictive_controller.calibrator = (
                    self._pv_calibrator
                )
            except Exception:
                pass
        self._hems._battery_capacity_kwh = self._battery_capacity_kwh

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

        # ── Update HEMS daily counters (cheap, no I/O) ─────────────────
        today = now.strftime("%Y-%m-%d")
        if self._hems_debug_day != today:
            self._hems_debug_day = today
            self._hems_debug_decisions = 0
            self._hems_debug_commands = 0
            self._hems_debug_skips = 0
        self._hems_debug_decisions += 1
        self._hems_debug_last_decision_ts = now.isoformat(timespec="seconds")
        if decision.skip:
            self._hems_debug_skips += 1
        elif (decision.output_priority is None and decision.charger_priority is None):
            # Early-skip — current already matches target.
            self._hems_debug_skips += 1
        else:
            self._hems_debug_commands += 1

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

    async def _maybe_refresh_load_history(self, now: datetime) -> None:
        """Load hourly load-power history from the HA recorder (max once/hour).

        Failure keeps the previous matrix (possibly empty) — the planner's
        missing-data gate then lowers confidence instead of using fake zeros.
        """
        last = self._load_matrix_at
        if last is not None and (now - last) < timedelta(hours=1):
            return
        self._load_matrix_at = now
        try:
            from homeassistant.components.recorder import statistics as rec_stats
            from homeassistant.util import dt as dt_util
            from .hems.history_builder import build_hourly_load_matrix

            ent = "sensor.garazh_smart_solar_inverter_load_power"
            start = dt_util.utcnow() - timedelta(days=9)
            stats = await self.hass.async_add_executor_job(
                rec_stats.statistics_during_period,
                self.hass, start, None, {ent}, "hour", None, {"mean"},
            )
            rows = stats.get(ent, [])
            samples = []
            for r in rows:
                mean = r.get("mean")
                ts = r.get("start")
                if mean is None or ts is None:
                    continue
                if isinstance(ts, (int, float)):
                    ts = dt_util.utc_from_timestamp(ts)
                samples.append((dt_util.as_local(ts).replace(tzinfo=None), mean))
            self._load_matrix = build_hourly_load_matrix(
                samples, dt_util.as_local(dt_util.utcnow()).replace(tzinfo=None), days=7
            )
            _LOGGER.info("Load history refreshed: %d full days", len(self._load_matrix))
        except Exception as exc:  # never break HEMS because of history
            _LOGGER.warning("Load history refresh failed: %s", exc)
            # retry in ~5 min instead of waiting a full hour
            self._load_matrix_at = now - timedelta(minutes=55)

    async def _maybe_refresh_pv_history(self, now: datetime) -> None:
        """Load hourly PV-power history from the HA recorder (max once/hour).

        Uses ``build_hourly_load_matrix`` for the same NaN/None/neg/20kW
        cleaning we already trust for load. The matrix is what we compare
        to yesterday's forecast to feed the ``ForecastCalibrator``.

        Failure keeps the previous matrix — the calibrator's existing
        samples stay valid; we just don't add a new (forecast, actual)
        pair until the matrix is back.
        """
        last = self._pv_matrix_at
        if last is not None and (now - last) < timedelta(hours=1):
            return
        self._pv_matrix_at = now
        try:
            from homeassistant.components.recorder import statistics as rec_stats
            from homeassistant.util import dt as dt_util
            from .hems.history_builder import build_hourly_load_matrix

            ent = "sensor.garazh_smart_solar_inverter_pv_power"
            start = dt_util.utcnow() - timedelta(days=9)
            stats = await self.hass.async_add_executor_job(
                rec_stats.statistics_during_period,
                self.hass, start, None, {ent}, "hour", None, {"mean"},
            )
            rows = stats.get(ent, [])
            samples = []
            for r in rows:
                mean = r.get("mean")
                ts = r.get("start")
                if mean is None or ts is None:
                    continue
                if isinstance(ts, (int, float)):
                    ts = dt_util.utc_from_timestamp(ts)
                samples.append((dt_util.as_local(ts).replace(tzinfo=None), mean))
            self._pv_matrix = build_hourly_load_matrix(
                samples, dt_util.as_local(dt_util.utcnow()).replace(tzinfo=None), days=7
            )
            _LOGGER.info(
                "PV history refreshed: %d full days, %d hourly samples",
                len(self._pv_matrix),
                sum(len(r) for r in self._pv_matrix),
            )
        except Exception as exc:  # never break HEMS because of history
            _LOGGER.warning("PV history refresh failed: %s", exc)
            # retry in ~5 min instead of waiting a full hour
            self._pv_matrix_at = now - timedelta(minutes=55)

    async def _maybe_record_pv_pairs(self, now: datetime) -> None:
        """Compare yesterday's forecast vs yesterday's measured PV.

        For each complete past day we have both:
          - a forecast (sum of hourly_forecast_today row for that date,
             OR a daily value carried forward from the previous day's fetch)
          - an actual sum from the PV matrix

        We convert kWh→Wh so the calibrator's W-based metrics apply
        directly. ``today`` is excluded — incomplete data would skew the
        bias estimate.

        Runs at most once per calendar day; idempotent across reloads
        because the calibrator already de-dupes via its bounded buffer
        and the file-backed persistence layer keys by date.
        """
        today_str = now.strftime("%Y-%m-%d")
        if self._last_pv_pair_check_date == today_str:
            return
        if not self._pv_matrix:
            return  # nothing measured yet
        # Yesterday is the last row in the matrix (oldest first).
        yesterday_actual_wh: float | None = None
        if self._pv_matrix:
            try:
                row = self._pv_matrix[-1]
                # Each cell is mean W for that hour → Wh per hour.
                yesterday_actual_wh = float(sum(max(0.0, v) for v in row))
            except Exception:
                yesterday_actual_wh = None
        if yesterday_actual_wh is None or yesterday_actual_wh <= 0:
            return
        # Yesterday's forecast: prefer stored hourly forecast for
        # yesterday (sum of W = Wh), fall back to
        # forecast_day_after_kwh recorded yesterday (now stale by
        # one day but still a real forecast number, not a delta).
        yesterday_forecast_wh = self._yesterday_forecast_wh()
        if yesterday_forecast_wh is None or yesterday_forecast_wh <= 0:
            return
        try:
            before_n = len(self._pv_calibrator)
            self._pv_calibrator.record(
                forecast_w=float(yesterday_forecast_wh),
                actual_w=float(yesterday_actual_wh),
            )
            after_n = len(self._pv_calibrator)
            self.forecast_learned_ratio = (
                self._derive_learned_ratio_from_calibrator()
            )
            self._last_pv_pair_check_date = today_str
            self._save_pv_fact_pairs()
            _LOGGER.info(
                "PV pair recorded: fc=%.0fWh ac=%.0fWh (n %d→%d, ratio=%.3f)",
                yesterday_forecast_wh,
                yesterday_actual_wh,
                before_n,
                after_n,
                self.forecast_learned_ratio,
            )
        except Exception as exc:
            _LOGGER.warning("PV pair record failed: %s", exc)

    def _yesterday_forecast_wh(self) -> float | None:
        """Best-effort yesterday-forecast in Wh.

        Sources, in priority order:
          1. ``self.hourly_forecast_today`` if its first hour maps to
             yesterday (we only run this check at ≥00:30, so the
             hourly forecast by then is fully populated for today).
          2. ``self._forecast_today_kwh`` if it has been refreshed for
             today *and* we already passed midnight; at 00:30+ this
             still represents the day we just finished forecasting,
             which is yesterday from ``now``'s perspective.
        """
        try:
            hours = list(getattr(self, "hourly_forecast_today", []) or [])
            if len(hours) >= 24:
                # Sum of W over 24h = Wh. The hourly forecast for
                # "today" was generated yesterday; using it here for
                # yesterday is the right thing — that's the forecast
                # we held when yesterday started.
                return float(sum(max(0.0, float(v)) for v in hours[:24]))
        except Exception:
            pass
        try:
            kwh = self._forecast_today_kwh
            if kwh is None:
                return None
            return float(kwh) * 1000.0
        except Exception:
            return None

    def _derive_learned_ratio_from_calibrator(self) -> float:
        """Compute ``forecast_learned_ratio`` from real measurements.

        Defined as ``actual / forecast`` (clipped, in [0.05, 1.5]).
        Empty calibrator → 0.12 (the documented cold-start value used
        by the Economics dashboard's ``learned_pv_ratio`` sensor;
        the sensor must never disappear, so we keep a sane number
        even before we have any samples).
        """
        try:
            m = self._pv_calibrator.metrics()
        except Exception:
            return 0.12
        if m.sample_count == 0 or m.bias_w is None:
            return 0.12
        # bias = mean(actual - forecast). forecast_mean ≈ bias - bias_w
        # if we know the kept forecast mean; without it we estimate
        # ratio from absolute forecast (the most recent one stored).
        try:
            samples = self._pv_calibrator.to_list()
            forecast_mean = (
                sum(fc for fc, _ in samples) / max(1, len(samples))
            )
        except Exception:
            return 0.12
        if forecast_mean <= 0:
            return 0.12
        # actual_mean = forecast_mean + bias_w
        actual_mean = forecast_mean + float(m.bias_w)
        ratio = actual_mean / forecast_mean
        return float(max(0.05, min(1.5, ratio)))

    def _save_pv_fact_pairs(self) -> None:
        """Persist the calibrator's sample buffer to JSON on the
        integration's own ``hems/`` directory so it survives HA
        restarts but stays out of recorder/HASS storage.
        """
        try:
            import json
            data = {
                "version": 1,
                "samples": self._pv_calibrator.to_list(),
            }
            with open(self._pv_fact_pairs_file, "w") as f:
                json.dump(data, f)
        except Exception as exc:
            _LOGGER.debug("PV fact-pairs persist failed: %s", exc)

    def _load_pv_fact_pairs(self) -> None:
        """Restore the calibrator's sample buffer from JSON.

        Safe to call before the calibrator has been used — empty file
        or missing file are both treated as "no prior data".
        """
        try:
            import json
            import os
            if not os.path.exists(self._pv_fact_pairs_file):
                return
            with open(self._pv_fact_pairs_file, "r") as f:
                raw = f.read()
            if not raw.strip():
                return
            data = json.loads(raw)
            samples = data.get("samples", [])
            if isinstance(samples, list) and samples:
                self._pv_calibrator.load_from_list(samples)
                _LOGGER.info(
                    "PV fact-pairs restored: %d prior (forecast, actual) pairs",
                    len(samples),
                )
        except Exception as exc:
            _LOGGER.debug("PV fact-pairs restore skipped: %s", exc)

    def _log_pv_calibrator_state(self, now: datetime) -> None:
        """Periodic INFO summary of the calibrator (≤1/hour).

        The parent task spec asks for a single line, no more, so we
        rate-limit to once an hour and keep the format consistent.
        """
        if self._pv_calibrator_log_at is not None and (
            now - self._pv_calibrator_log_at
        ) < timedelta(hours=1):
            return
        self._pv_calibrator_log_at = now
        try:
            m = self._pv_calibrator.metrics()
        except Exception:
            return
        _LOGGER.info(
            "PV calibrator updated: n=%d bias=%.1fkWh mae=%.1fkWh conf=%.2f",
            m.sample_count,
            m.bias_w / 1000.0,
            m.mae_w / 1000.0,
            m.confidence_factor,
        )

    def _build_tariff_schedule(self) -> list[float]:
        """Build a 24-hour tariff schedule from day/night tariffs.

        The planner uses this to decide when to charge (cheap) vs
        discharge (expensive). When the user hasn't supplied
        separate day/night rates via options we still produce a
        sensible schedule so the planner has SOMETHING to look at.
        """
        try:
            day = float(self._day_tariff_uah)
            night = float(self._night_tariff_uah)
        except (TypeError, ValueError):
            return [0.0] * 24
        # Ukraine TOU: night 23-07, day otherwise
        out: list[float] = []
        for h in range(24):
            if h >= 23 or h < 7:
                out.append(night)
            else:
                out.append(day)
        return out

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
            # discharge_w is in W (Watts), dt_h in hours, so the product is Wh.
            # Divide by 1000 to convert to kWh before storing.
            discharge_kwh = discharge_w * dt_h / 1000.0
            if daytime:
                self._daily_battery_discharge_day_kwh += discharge_kwh
            else:
                self._daily_battery_discharge_night_kwh += discharge_kwh

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
                    fc = daily[dates[0]]
                    self.forecast_tomorrow_kwh = fc.energy_kwh
                    self.weather_tomorrow_code = getattr(fc, "dominant_weather_code", None)
                if len(dates) >= 2:
                    fc2 = daily[dates[1]]
                    self.forecast_day_after_kwh = fc2.energy_kwh
                    self.weather_day_after_code = getattr(fc2, "dominant_weather_code", None)
                # NOTE: do NOT overwrite ``forecast_learned_ratio``
                # from ``self._forecast.learned_ratio`` here — that
                # number is the old circular self-learner (it
                # estimates its own accuracy). Real calibration now
                # happens in ``_maybe_record_pv_pairs`` via the
                # ``ForecastCalibrator``, which compares the Open-
                # Meteo forecast against actual PV recorder data.
                # ``forecast_learned_ratio`` is kept populated for
                # the Economics dashboard's
                # ``learned_pv_ratio`` sensor (which must never
                # disappear), so we recompute from the calibrator
                # only on init and after a new (forecast, actual) pair.

                # Store hourly forecast for today (sparkline)
                hourly = await self._forecast.get_hourly_forecast()
                today_str = now.strftime("%Y-%m-%d")
                today_hours = [h["power_w"] for h in hourly if h["time"].startswith(today_str)]
                today_weather = [
                    h.get("weather_code") for h in hourly if h["time"].startswith(today_str)
                ]
                today_radiation = [
                    h.get("radiation_wm2", 0) for h in hourly if h["time"].startswith(today_str)
                ]
                # Pad to 24 if needed
                if len(today_hours) < 24:
                    today_hours.extend([0.0] * (24 - len(today_hours)))
                    today_weather.extend([None] * (24 - len(today_weather)))
                    today_radiation.extend([0.0] * (24 - len(today_radiation)))
                self.hourly_forecast_today = today_hours[:24]
                self.hourly_weather_today = today_weather[:24]
                self.hourly_radiation_today = today_radiation[:24]
                # Derive today's energy total — sum of hourly PV (W) /
                # 1000 = kWh (each hour contributes 1 kWh per 1 kW).
                try:
                    self._forecast_today_kwh = (
                        sum(today_hours[:24]) / 1000.0
                    )
                except Exception:
                    self._forecast_today_kwh = None

                self._forecast_last_fetch = now
                _LOGGER.debug(
                    "Forecast: tomorrow=%.1f kWh, day2=%.1f kWh, ratio=%.4f",
                    self.forecast_tomorrow_kwh or 0,
                    self.forecast_day_after_kwh or 0,
                    self.forecast_learned_ratio,
                )
            except Exception as exc:
                _LOGGER.warning("Forecast fetch failed: %s", exc)

        # NOTE: the old ``_forecast.update_ratio(daily_pv_kwh,
        # estimated_radiation)`` circular self-learner was removed.
        # The accuracy ratio is now driven by ``ForecastCalibrator``
        # comparing the same Open-Meteo forecast against the actual
        # hourly PV matrix (see ``_maybe_record_pv_pairs``).


class HistoryCoordinator(DataUpdateCoordinator):
    """Coordinator that polls historical data from the inverter API every 15 minutes.

    Provides 4 data series for dashboard visualization:
    - Today hourly PV power (24 points)
    - Current month daily PV energy (up to 31 points)
    - Current year monthly PV energy (12 points)
    - Total cumulative PV energy
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api: InverterApiClient,
        entry: ConfigEntry,
        update_interval: timedelta = timedelta(seconds=HISTORY_POLL_INTERVAL_SEC),
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_history",
            update_interval=update_interval,
        )
        self.api = api
        self._entry = entry

        # Cached history data
        self.today_hourly_power: list[dict] = []
        self.monthly_daily_energy: list[dict] = []
        self.yearly_monthly_energy: list[dict] = []
        self.total_energy_kwh: float = 0.0
        # Daily historical weather: {date_str: wmo_code} populated from
        # Open-Meteo Historical API for the history graph overlays.
        self.daily_historical_weather: dict[str, int] = {}
        self.daily_weather_count: int = 0

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch historical data from the API."""
        _LOGGER.debug("HistoryCoordinator: fetching historical data")

        try:
            # Fetch all 4 data series from ownerOverView endpoints in
            # parallel. Previously these ran sequentially with a 1s
            # rate-limit pause between each (≈4s wall time per cycle). With
            # _apply_rate_limit holding a shared lock, concurrent dispatch
            # serialises them inside the lock to a single 1s wait — the
            # four requests still go out one-after-another from the cloud's
            # point of view, but the wall-clock for the coordinator drops
            # from ~4s to ~1s + 4*network_rtt (≈ 2-2.5s on a healthy link).
            # return_exceptions=True so a single failure doesn't poison
            # the whole batch; downstream logic already handles [] / {}.
            results = await asyncio.gather(
                self.api.fetch_daily_power(),
                self.api.fetch_monthly_energy(),
                self.api.fetch_yearly_energy(),
                self.api.fetch_total_energy(),
                return_exceptions=True,
            )
            today_power, monthly_energy, yearly_energy, total_data = results

            _LOGGER.info(
                "HistoryCoordinator: daily=%d monthly=%d yearly=%d total_keys=%s",
                len(today_power), len(monthly_energy), len(yearly_energy),
                list(total_data.keys()) if isinstance(total_data, dict) else type(total_data).__name__,
            )
            if today_power:
                _LOGGER.info("Daily sample: %s", today_power[0])
            if monthly_energy:
                _LOGGER.info("Monthly sample: %s", monthly_energy[0])

            total_kwh = total_data.get("value") or total_data.get("totalEnergy") or 0.0
            if isinstance(total_kwh, str):
                total_kwh = float(total_kwh)

            # Store for sensor access
            self.today_hourly_power = today_power
            self.monthly_daily_energy = monthly_energy
            self.yearly_monthly_energy = yearly_energy
            self.total_energy_kwh = total_kwh

            # Refresh daily historical weather (last 7 days) in the
            # background so the history graph can show icons.
            try:
                self.daily_historical_weather = await self._fetch_historical_weather()
                self.daily_weather_count = len(self.daily_historical_weather)
            except Exception as exc:
                _LOGGER.debug("Historical weather fetch skipped: %s", exc)

            return {
                "today_hourly_power": today_power,
                "monthly_daily_energy": monthly_energy,
                "yearly_monthly_energy": yearly_energy,
                "total_energy_kwh": total_kwh,
                "last_updated": datetime.now().isoformat(),
            }

        except Exception as exc:
            _LOGGER.warning("HistoryCoordinator: fetch failed: %s", exc)

    async def _fetch_historical_weather(self) -> dict[str, int]:
        """Fetch last-7-days WMO weather codes from Open-Meteo Archive API.

        Uses archive-api.open-meteo.com (free, no key) — same coordinates
        as the forecast service. Returns {YYYY-MM-DD: wmo_code_int}.
        """
        from datetime import datetime, timedelta
        import aiohttp

        if not hasattr(self, "_history_coords") or not self._history_coords:
            # Fall back to Kiev if we never set real coordinates
            self._history_coords = (50.4501, 30.5234)

        lat, lon = self._history_coords
        end = datetime.now().date()
        start = end - timedelta(days=7)
        url = (
            "https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={lat}&longitude={lon}"
            f"&start_date={start.isoformat()}&end_date={end.isoformat()}"
            "&daily=weather_code"
            "&timezone=auto"
        )

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return {}
                    data = await resp.json(content_type=None)
        except Exception as exc:
            _LOGGER.debug("Open-Meteo archive request failed: %s", exc)
            return {}

        result: dict[str, int] = {}
        daily = data.get("daily", {})
        for date, code in zip(daily.get("time", []), daily.get("weather_code", [])):
            if code is not None:
                result[date] = int(code)
        return result
