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
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import InverterApiClient, InverterOfflineError, TokenExpiredError
from .const import DOMAIN, HISTORY_POLL_INTERVAL_SEC
from .hems.forecast import ForecastService
from .hems.soc_correction import get_real_soc
from .hems.engine import (
    HemsEngine,
    SmartMode,
    OutputPriority,
    ChargerPriority,
    HemsDecision,
    _finite_number,
)
from .hems import debug_logging
from .hems.tuning import HemsTunables, HemsTuningService, PredictiveTuning
from .hems.storm_risk import evaluate_storm_risk
from .hems.schedule_rules import ScheduleRulesService
from .hems.demand_forecast import DemandForecastService
from .hems.battery_soh import BatterySoH
from .hems.pv_coordinator import PvLearningCoordinatorMixin
from .hems.predictive_control import PredictiveControlEngine, parse_predictive_options, apply_feedback, restore_feedback
from .hems.storm_risk import calibrated_storm_risk

_LOGGER = logging.getLogger(__name__)


# T12 follow-up (review of c056341 + 832447c): the forced-
# grid-charge path used to skip telemetry validation and
# hard-code ``valid_telemetry=True`` and ``grid_v=raw.get(
# "gridVoltage", 230.0)``. The audit's T12 review called
# this out: a missing or NaN SOC, a non-numeric PV/load
# or voltage, a malformed reserve_soc, or a grid_offline
# reading must all block the forced write, *exactly the
# same way* they block the regular plan.
#
# The helpers below mirror the engine's own validation
# (``_finite_number`` from ``hems.engine``) so the forced
# path is held to the same standard as the baseline
# engine. ``_validate_force_telemetry`` is a pure
# function — no side effects, no engine state, no
# exceptions on boundary conditions; the caller (the
# T12 block in ``_run_hems_engine``) inspects the
# returned ``valid`` flag and either dispatches the
# forced decision or falls through to the regular
# plan (or to a logged refusal when both paths
# short-circuit).
#
# Bounds follow the engine's own checks at
# ``hems/engine.py`` ~line 304: SOC 0..100, reserve_soc
# 0..100, grid_voltage 0..300 (the inverter's reportable
# range is 0..300 V AC; values above 300 V are either
# scaling artefacts or a corrupted report and must
# not dispatch).

_FORCE_TELEMETRY_BOUNDS = {
    "soc": (0.0, 100.0),
    "reserve_soc": (0.0, 100.0),
    "grid_voltage": (0.0, 300.0),
}


def _validate_force_telemetry(
    raw: dict,
    options_reserve_soc: Any,
) -> dict:
    """Return a typed snapshot of the telemetry fields the
    forced-grid-charge path must inspect.

    The returned dict always contains the same keys;
    ``valid`` is True only when every required field is
    a finite number, within the documented bounds, and
    the grid is on-line. The function never raises on
    malformed input; the caller decides what to do with
    the failure (refuse the forced dispatch, fall
    through to the engine plan, or escalate).
    """
    # --- SOC ---
    soc = _finite_number(raw.get("batterySoc"))
    soc_valid = (
        soc is not None
        and _FORCE_TELEMETRY_BOUNDS["soc"][0]
        <= soc
        <= _FORCE_TELEMETRY_BOUNDS["soc"][1]
    )

    # --- reserve_soc from entry options ---
    reserve_soc = _finite_number(options_reserve_soc)
    reserve_valid = (
        reserve_soc is not None
        and _FORCE_TELEMETRY_BOUNDS["reserve_soc"][0]
        <= reserve_soc
        <= _FORCE_TELEMETRY_BOUNDS["reserve_soc"][1]
    )

    # --- grid voltage (must be present, finite, and > 0) ---
    grid_v = _finite_number(raw.get("gridVoltage"))
    grid_v_valid = (
        grid_v is not None
        and _FORCE_TELEMETRY_BOUNDS["grid_voltage"][0]
        < grid_v
        <= _FORCE_TELEMETRY_BOUNDS["grid_voltage"][1]
    )

    # --- gridOk flag (must be a real bool, not truthy) ---
    grid_ok_raw = raw.get("gridOk")
    grid_ok_valid = isinstance(grid_ok_raw, bool)

    # --- PV / load / battery (used by the engine's
    # ``evaluate``; we require them to be finite so the
    # energy-balance model can run, but a single
    # invalid field does not by itself block the
    # forced path because the engine computes fresh
    # values each cycle. We still surface them so
    # the T12 test can assert the same field-bounds
    # as the engine. ---
    pv_w = _finite_number(raw.get("pvPower"))
    load_w = _finite_number(raw.get("loadPower"))
    grid_w = _finite_number(raw.get("gridPower"))
    battery_w = _finite_number(raw.get("batteryPower"))

    valid = (
        soc_valid
        and reserve_valid
        and grid_v_valid
        and grid_ok_valid
        and pv_w is not None
        and load_w is not None
        and grid_w is not None
        and battery_w is not None
    )
    # When ``gridOk`` is False the inverter is offline;
    # the forced USB+SNU command cannot dispatch because
    # the inverter is not feeding from the grid. We
    # surface that as a distinct ``is_online`` flag so
    # the caller can log it without short-circuiting
    # the SOC / reserve guards above. ``grid_v > 0``
    # is guarded by ``grid_v_valid`` because the
    # validator already established the field is a
    # finite number in (0, 300] V.
    is_online = (
        grid_ok_valid
        and bool(grid_ok_raw)
        and grid_v_valid
        and grid_v is not None
        and grid_v > 0
    )
    return {
        "valid": valid,
        "is_online": is_online,
        "soc": soc,
        "reserve_soc": reserve_soc,
        "grid_voltage": grid_v,
        "grid_ok": grid_ok_raw,
        "pv_w": pv_w,
        "load_w": load_w,
        "grid_w": grid_w,
        "battery_w": battery_w,
    }


class InverterCoordinator(PvLearningCoordinatorMixin, DataUpdateCoordinator):
    """Coordinator that polls Inverter API and computes derived values.

    Now includes the full HEMS engine for intelligent control.
    """

    # T06: maximum interval between two consecutive samples. Anything
    # longer than this is treated as an offline gap and the stale
    # reading is NOT integrated into the daily counters. The default
    # of 60 s is far above the documented 5 s fetch cadence but
    # tolerates brief transient gaps without dropping data.
    _MAX_SAMPLE_GAP_S: float = 60.0

    # Number of days the daily counters may live in a single persisted
    # blob. Older days are not needed for current display but we keep
    # one extra so a mid-day restart can detect rollover properly.
    _ENERGY_STATE_SCHEMA_VERSION: int = 1

    # T06 follow-up: persistence of the energy-state blob is
    # rate-limited. The default 5 s sample cadence would otherwise
    # trigger a config_entries.async_update_entry call every cycle,
    # which is wasteful and races with other option writers (the
    # predictive persistence, the user-toggle setters, the HEMS
    # auto-mode toggle). 30 s strikes a balance: the dashboard
    # never shows a counter older than half a minute, but HA is
    # not hammered with writes.
    _ENERGY_PERSIST_MIN_INTERVAL_S: float = 30.0

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

        # HEMS state. T02 fix: user toggles for HEMS auto mode and smart
        # mode are read from ``entry.options`` so they survive reload. The
        # defaults below match the previous in-memory behaviour for
        # entries that don't carry the keys yet.
        self.smart_mode: int = int(entry.options.get("smart_mode", 0))  # 0=Adaptive, 1=Arbitrage, 2=Storm
        self.hems_auto_mode: bool = bool(entry.options.get("hems_auto_mode", True))
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
        self._hems = PredictiveControlEngine(tunables=tunables, tuning=self._tuning)
        predictive_options = parse_predictive_options({**entry.data, **entry.options})
        self._hems.predictive_min_confidence = predictive_options["predictive_min_confidence_for_assist"]
        restore_feedback(self._hems, entry.options.get("predictive_feedback_override", {}), datetime.now())
        # Wire predictive_mode / predictive_tuning from entry options
        # onto the engine. The engine evaluates on this attribute
        # each cycle; the switch/select entities update it through
        # ``async_set_predictive_mode`` (see below) so persistence
        # flows through ``entry.options`` and survives reload.
        self._predictive_tuning = PredictiveTuning(
            predictive_mode=str(
                entry.options.get("predictive_mode", predictive_options["predictive_default_mode"].lower())
            ),
            predictive_enabled=bool(
                entry.options.get("predictive_mode", predictive_options["predictive_default_mode"].lower()) in ("shadow", "assist")
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

        # T12: force_grid_charge service holds the inverter
        # on USB+SNU for a user-supplied duration. The
        # ``_forced_charge_until`` timestamp is consulted
        # at the top of ``_run_hems_engine``; while the
        # current time is before it, the engine returns
        # a forced USB+SNU decision instead of running
        # the regular plan. After the deadline passes,
        # the field is cleared and the engine resumes
        # normal operation. The forced decision is
        # *not* a hard override of guards — the SOC
        # gate (``soc_unknown``), BMS, and the no-write
        # rule still apply.
        self._forced_charge_until: datetime | None = None

        # Load demand EWMA profile
        self._load_profile: dict[int, float] = {}

        # ── Forecast & Economics ──────────────────────────────────────
        # T06 fix: counters are restored from ``entry.options`` if a
        # previous coordinator instance persisted them. The persisted
        # state is a single JSON blob keyed by ``_energy_state`` so the
        # option list stays flat. We keep in-memory mirrors here for
        # hot-path access and rewrite the blob on each successful sample.
        self._last_midnight: datetime | None = None
        self._last_sample_ts: datetime | None = None
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
        # T06 follow-up: throttle write attempts so the per-cycle
        # 5 s cadence does not hammer ``async_update_entry`` (which
        # wakes up storage listeners and may race with the other
        # option writers in this coordinator).
        self._last_energy_persist_at: datetime | None = None
        self._energy_state_dirty: bool = False
        self._restore_energy_state(entry.options.get("_energy_state"))

        # Forecast service (activated on first update)
        self._forecast: ForecastService | None = None
        self._forecast_last_fetch: datetime | None = None
        self.forecast_tomorrow_kwh: float | None = None
        self.forecast_day_after_kwh: float | None = None
        self._forecast_today_kwh: float | None = None
        # Hourly load matrix [days][24] from the recorder (see history_builder)
        self._load_matrix: list[list[float]] = []
        self._load_matrix_at: datetime | None = None
        self._init_pv_learning()
        self.radiation_now_wm2: float | None = None
        self.hourly_forecast_today: list[float] = []  # 24 hourly power values (W) for sparkline
        self.hourly_weather_today: list[int | None] = []  # WMO weather codes per hour
        self.hourly_radiation_today: list[float] = []  # Solar radiation W/m² per hour
        self.weather_tomorrow_code: int | None = None  # Dominant weather for tomorrow
        self.weather_day_after_code: int | None = None

        # Storm risk tracking
        self._storm_risk_score: float = 0.0
        self._storm_risk_reason: str = ""
        # T10 follow-up: the previous code had a
        # single ``_auto_storm_active`` flag that
        # conflated the grid-outage cause and the
        # weather-risk cause, and overwrote
        # ``self.smart_mode`` directly. The fix
        # splits the cause into two booleans and
        # keeps the user's intent (``_user_smart_mode``)
        # separate from the effective mode the
        # engine sees.
        self._auto_storm_weather: bool = False
        self._auto_storm_outage: bool = False
        self._user_smart_mode: int = self.smart_mode
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
    def _auto_storm_active(self) -> bool:
        """T10: combined auto-storm cause.

        Returns ``True`` while at least one of the
        two independent auto-storm causes is
        active. The previous code stored a single
        bool and conflated the grid-outage cause
        with the weather-risk cause; clearing
        either one used to drop the whole flag,
        which could take the operator out of Storm
        even though the *other* cause was still
        valid. This property preserves the old
        attribute-style read surface
        (``coord._auto_storm_active``) for every
        caller that already exists; new code
        should branch on the two specific causes
        instead.
        """
        return self._auto_storm_weather or self._auto_storm_outage

    @_auto_storm_active.setter
    def _auto_storm_active(self, value: bool) -> None:
        """T10: legacy single-flag setter.

        Some pre-T10 callers (the outage branch in
        ``_run_hems_engine`` and the T12 test
        stub) still write the boolean directly. We
        preserve that surface: writing ``True``
        activates the outage cause, writing
        ``False`` clears *both* causes. The new
        weather path in ``_maybe_evaluate_storm_risk``
        uses the dedicated ``_auto_storm_weather``
        bool, so this setter is exercised only by
        the outage branch and by tests.
        """
        if value:
            self._auto_storm_outage = True
        else:
            self._auto_storm_weather = False
            self._auto_storm_outage = False

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
        self._hems.invalidate_predictive("predictive_mode_changed")
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

    def async_predictive_feedback(self, action, duration_min=30, new_target_soc=None):
        """User feedback changes the hold, never the inverter or Assist mode."""
        record = apply_feedback(self._hems, action, datetime.now(), duration_min, new_target_soc)
        if record is not None:
            options = dict(self._entry.options)
            options["predictive_feedback_override"] = record
            self.hass.config_entries.async_update_entry(self._entry, options=options)
            state = self._hems.predictive_decision_state
            state.update(applied=False, target_soc=record["target_soc"], override_pending_until=record["until"])
            self.async_update_listeners()
        _LOGGER.info("Predictive feedback: action=%s duration_min=%d target_soc=%s", action, duration_min, new_target_soc)

    # ═══════════════════════════════════════════════════════════════════
    # T02: persistence for HEMS user toggles
    # ═══════════════════════════════════════════════════════════════════

    def async_set_hems_auto_mode(self, enabled: bool) -> None:
        """Toggle HEMS automatic control; persist to ``entry.options``.

        The user-off path here is the one called from the Lovelace switch
        and the matching HA service. The integration must NOT re-enable
        HEMS after reload simply because a downstream component queried
        the option. ``self.hems_auto_mode`` is updated atomically with the
        persistent value so a partial write can't leave the coordinator
        in a state that disagrees with the entry.
        """
        self.hems_auto_mode = bool(enabled)
        self._persist_user_option("hems_auto_mode", self.hems_auto_mode)

    def async_set_smart_mode(self, mode: int) -> None:
        """Change HEMS strategy (Adaptive / Arbitrage / Storm) and persist.

        T02: this is the user choice, distinct from any temporary
        auto-Storm activation triggered by forecast or grid loss. A
        later code path that flips ``smart_mode`` for automation must
        not overwrite the value persisted here.
        """
        try:
            mode_int = int(mode)
        except (TypeError, ValueError):
            mode_int = 0
        if mode_int not in (0, 1, 2):
            mode_int = 0
        # T10: ``self.smart_mode`` is the *user*
        # intent. The auto-storm layer no longer
        # writes to it; instead it drives the
        # two ``_auto_storm_*`` causes which feed
        # the effective-mode calculation in
        # ``_run_hems_engine``. When the user
        # changes the mode while an auto-storm
        # cause is active, we treat their pick as
        # the new "remembered" intent — the
        # previous pointer is updated so the
        # cause-clear path does not restore a
        # stale value.
        self.smart_mode = mode_int
        self._user_smart_mode = mode_int
        if self._auto_storm_active:
            # User chose a new mode during an
            # active auto-storm. Adopt it as the
            # "last user pick" so a future
            # cause-clear path restores the new
            # value rather than the pre-storm one.
            self._previous_smart_mode_before_storm = mode_int
        self._persist_user_option("smart_mode", self.smart_mode)

    def _persist_user_option(self, key: str, value) -> None:
        """Update a single user-visible option on the config entry.

        Failures are logged at debug level and never raise — caller code
        that already updated the in-memory attribute must not be undone
        by a write error. Returning ``False`` here would invite races
        where the UI claims success while the value is lost; instead we
        log and keep the in-memory change. The next successful reload
        will still read the in-memory value because ``_async_update_data``
        does not overwrite these fields.
        """
        if self._entry is None or self.hass is None:
            return
        try:
            new_opts = dict(self._entry.options)
            new_opts[key] = value
            self.hass.config_entries.async_update_entry(
                self._entry, options=new_opts
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("persist user option %s=%s failed: %s", key, value, exc)

    # ═══════════════════════════════════════════════════════════════════
    # MAIN UPDATE LOOP
    # ═══════════════════════════════════════════════════════════════════

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest data, compute derived values, and run HEMS engine."""
        now = datetime.now()
        self._hems.invalidate_predictive("awaiting_telemetry")

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
        # T01 hardening: a missing or invalid batterySoc must NOT
        # collapse to 100 %. The previous code path used
        # ``raw.get("batterySoc", 100.0)`` which meant a critically
        # empty battery looked full and a totally missing reading
        # drove the engine as if the station was at 100 %. We now
        # treat absence and out-of-range as ``None`` and propagate
        # that signal all the way to the control command gate.
        raw_soc = raw.get("batterySoc")
        if raw_soc is None:
            reported_soc = None
            soc_unknown = True
        else:
            try:
                reported_soc = float(raw_soc)
            except (TypeError, ValueError):
                reported_soc = None
                soc_unknown = True
            else:
                if (
                    reported_soc != reported_soc  # NaN
                    or reported_soc < 0
                    or reported_soc > 100
                ):
                    reported_soc = None
                    soc_unknown = True
                else:
                    soc_unknown = False
        voltage = raw.get("batteryVoltage", 52.0)
        current = raw.get("batteryCurrent", 0.0)
        if reported_soc is None:
            corrected_soc = None
        else:
            corrected_soc = get_real_soc(reported_soc, voltage, current)

        # ── Grid outage detection ────────────────────────────────────
        grid_v = raw.get("gridVoltage", 230.0)
        grid_available, grid_transition = self._evaluate_grid(grid_v)

        # ── Grid outage auto-Storm ───────────────────────────────────
        # T10: drive the dedicated outage cause
        # from the grid transition, never touch
        # ``self.smart_mode`` here (that is the
        # user intent). The effective mode the
        # engine sees is computed from the cause
        # flags later in this method.
        if (
            grid_transition == "outage"
            and self.smart_mode == SmartMode.ADAPTIVE
            and self.hems_auto_mode
        ):
            if not self._auto_storm_outage:
                _LOGGER.warning(
                    "⚡ Grid outage → auto-activating Storm mode "
                    "(outage cause)"
                )
                # Snapshot the user's pick only on
                # the *first* outage. The storm-risk
                # branch does the same; whichever
                # lands first wins.
                if (
                    self._previous_smart_mode_before_storm
                    is None
                ):
                    self._previous_smart_mode_before_storm = (
                        self.smart_mode
                    )
            self._auto_storm_outage = True
        elif grid_transition == "restored":
            if self._auto_storm_outage:
                _LOGGER.info(
                    "🔌 Grid restored → clearing outage cause"
                )
                self._auto_storm_outage = False
                if not self._auto_storm_weather:
                    self._previous_smart_mode_before_storm = None
        # T10: a forecast error path in
        # ``_maybe_evaluate_storm_risk`` returns
        # before it can clear the weather cause;
        # we keep that guarantee here too — the
        # outage branch must not be cleared by a
        # forecast call. The setter that clears
        # both causes is only invoked by the
        # outage-restore path above and by tests
        # that explicitly opt in.

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
        else:
            self._hems.invalidate_predictive("hems_auto_off")

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
        self, raw: dict[str, Any], corrected_soc: float | None, now: datetime
    ) -> None:
        """Run the HEMS engine and execute control commands.

        T01 hardening: when the SOC reading is missing or invalid,
        the engine still runs for diagnostics (so the user sees
        ``predictive_hint`` with a clear "soc_unknown" reason) but
        no command is dispatched to the inverter. This is a hard
        gate — even a Storm pre-emption or user override cannot
        drive a write while the meter is unreadable.
        """
        soc_unknown = corrected_soc is None
        # Synthetic SOC for engine display only — the gate below makes
        # sure it never reaches ``_execute_hems_command``.
        display_soc = 100.0 if soc_unknown else corrected_soc
        current_output = raw.get("outputSourcePriority", "")
        current_charger = raw.get("chargerSourcePriority", "")
        # Detect manual override
        self._hems.detect_manual_override(current_output, current_charger, now)
        # ── Schedule Rules: override smart mode if active rule exists ──
        active_rule = self._schedule_rules.get_active_rule_now(now)
        # T10: build the effective mode from the
        # two auto-storm causes plus the user's
        # intent. Precedence:
        #   1. An active schedule rule wins.
        #   2. If the user already picked Storm by
        #      hand (``_user_smart_mode == STORM``)
        #      OR an auto-storm cause is active,
        #      effective is Storm.
        #   3. Otherwise, the user's pick.
        # The user's intent (``self.smart_mode``)
        # is *never* overwritten by the auto-storm
        # layer; only the two cause flags change.
        # Restoring the user's pick on cause-clear
        # therefore reads ``self.smart_mode``
        # directly, which the engine sees as the
        # same value the operator picked.
        if active_rule is not None:
            effective_mode = active_rule.mode
        elif (
            self._auto_storm_active
            or self._user_smart_mode == SmartMode.STORM
        ):
            effective_mode = SmartMode.STORM
        else:
            effective_mode = self.smart_mode

        # ── Update demand forecast with current load ───────────────────
        load_power = raw.get("loadPower", 0.0)
        self._demand_forecast.update_ewma(now, load_power)

        # ── Track battery SoH ─────────────────────────────────────────
        if not soc_unknown:
            self._battery_soh.track_soc(corrected_soc)

        # ── Auto-tune house load reserve ──────────────────────────────
        self._maybe_auto_tune_house_reserve(load_power, now)

        # Check keepalive
        battery_power = raw.get("batteryPower", 0.0)

        # Finish keepalive if timer expired
        if self._hems.keepalive.in_progress and self._keepalive_timer and now >= self._keepalive_timer:
            self._hems.invalidate_predictive("keepalive_end")
            finish = self._hems.finish_keepalive(now)
            # T01: never write to the inverter with an unknown SOC,
            # even to finish a keepalive. The keepalive timer simply
            # expires without a write; the next valid sample will
            # resume normal operation.
            if not soc_unknown:
                await self._execute_hems_command(finish)
            else:
                _LOGGER.warning(
                    "HEMS: keepalive ended with unknown SOC; command skipped"
                )
            self._keepalive_timer = None
            return

        # Skip if keepalive in progress
        if self._hems.keepalive.in_progress:
            self._hems.invalidate_predictive("keepalive_in_progress")
            return

        # Skip HEMS if disabled (monitor-only mode)
        if not self.hems_enabled:
            self._hems.invalidate_predictive("hems_disabled")
            self.hems_last_reason = "hems_disabled"
            self.hems_last_output_cmd = None
            self.hems_last_charger_cmd = None
            return

        # Run main HEMS evaluation
        pv_power = raw.get("pvPower", 0.0)
        grid_power = raw.get("gridPower", 0.0)
        load_power = raw.get("loadPower", 0.0)

        await self._maybe_refresh_load_history(now)
        await self._maybe_refresh_pv_history(now)
        await self._save_real_forecast_pair(now)
        await self._save_pv_state()
        self._log_pv_calibrator_state(now)

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
        self._hems._dated_hourly_pv_forecast = dict(getattr(self, "_dated_hourly_pv_forecast", {}) or {})
        self._hems._planner_forecast_now = self._pv_local_now()
        self._hems._hourly_radiation = list(
            getattr(self, "hourly_radiation_today", []) or []
        )
        self._hems._hourly_weather_codes = list(
            getattr(self, "hourly_weather_today", []) or []
        )
        # Build 24h tariff schedule from coordinator tariffs
        self._hems._tariff_schedule = self._build_tariff_schedule()
        self._hems._consumption_history = list(self._load_matrix)
        self._hems._battery_capacity_kwh = self._battery_capacity_kwh
        self._configure_night_window()

        controller = self._hems._predictive_controller
        self._hems.predictive_min_confidence = parse_predictive_options({**self._entry.data, **self._entry.options})["predictive_min_confidence_for_assist"]
        controller.calibrated_storm_alert = calibrated_storm_risk(self._pv_calibrator)
        self._hems.predictive_storm_allowed = bool(self._entry.options.get("auto_storm_by_forecast", False))
        decision = self._hems.evaluate(
            smart_mode=effective_mode,
            hems_auto=self.hems_auto_mode,
            soc=display_soc,  # synthetic 100 % when soc_unknown; gate below blocks writes
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
            # T01 follow-up: forward the unknown-SOC flag so the
            # planner path sees ``soc_unknown=True`` and refuses
            # to make a per-hour prediction. The dispatch gate
            # below already blocks inverter writes; this flag
            # closes the diagnostic leak that previously made
            # the planner emit a "fully charged" recommendation
            # while the meter was unreadable.
            soc_unknown=soc_unknown,
        )

        self._persist_night_recommendation(now)
        # Store diagnostics. The audit requires that the
        # last-cmd attributes do not advertise a decision the
        # gate refused to dispatch: a user inspecting the
        # diagnostic sensor must not see "the engine wanted SBU"
        # when the actual write was suppressed because the SOC
        # was unreadable. We therefore record the *real* state
        # here and overwrite the *would-have-been* values below
        # when the gate fires.
        self.hems_last_reason = decision.reason
        self.hems_last_output_cmd = decision.output_priority
        self.hems_last_charger_cmd = decision.charger_priority
        # ``hems_buzzer_off`` is a sensor-facing attribute the
        # dashboard reads. It must reflect the *actual* state
        # rather than the engine's recommendation. We set it
        # here from the engine's output and reset it on the
        # gate below when we refuse to write. (The audit's
        # review caught a previous version that wrote to a
        # never-read ``buzzer_off_candidate`` attribute,
        # leaving ``hems_buzzer_off`` permanently stale.)
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

        # ── T01 hard gate: no commands while SOC is unknown ────────────
        # Even if the engine would happily dispatch a charge or
        # output change, the audit requires us to refuse to write
        # anything to the inverter while the meter is unreadable.
        # We log the suppressed action so a future operator can
        # see what would have happened, and *also* reset the
        # diagnostic ``hems_last_*`` attributes so the dashboard
        # does not advertise a decision we did not actually
        # write. The reason field is left as the engine's reason
        # — with a ``soc_unknown:`` prefix — so the operator can
        # still diagnose why nothing happened.
        if soc_unknown:
            if not decision.skip and (
                decision.output_priority is not None
                or decision.charger_priority is not None
                or decision.buzzer_off
            ):
                _LOGGER.warning(
                    "HEMS: SOC unknown, command suppressed "
                    "(would have set output=%s charger=%s buzzer_off=%s reason=%s)",
                    decision.output_priority,
                    decision.charger_priority,
                    decision.buzzer_off,
                    decision.reason,
                )
                self._hems_debug_skips += 1
                if self._hems_debug_commands:
                    self._hems_debug_commands -= 1
            # Clear the last-cmd diagnostics so the user does
            # not mistake the engine's recommendation for a
            # written command. The reason field carries the
            # ``soc_unknown`` prefix so the absence of a real
            # dispatch is itself diagnosable.
            self.hems_last_reason = f"soc_unknown: {decision.reason}"
            self.hems_last_output_cmd = None
            self.hems_last_charger_cmd = None
            self.hems_buzzer_off = False
            return

        # T12: ``force_grid_charge`` is in effect. Hold the
        # inverter on USB+SNU until the user-supplied
        # deadline. This block runs *after* the ``soc_unknown``
        # gate above so a missing SOC still blocks the
        # forced write, and *after* ``detect_manual_override``
        # so the user's manual command is still respected.
        # The forced decision is a regular ``HemsDecision``
        # — the same dedup, hysteresis, and ack logic in
        # ``_execute_hems_command`` apply. We replace the
        # engine's regular plan with the forced one only
        # while the deadline is in the future; after the
        # deadline we clear the field and fall through to
        # the engine's plan computed above.
        if (
            self._forced_charge_until is not None
            and not soc_unknown
        ):
            deadline = self._forced_charge_until
            if deadline.tzinfo is None and now.tzinfo is not None:
                deadline = deadline.replace(tzinfo=now.tzinfo)
            if now < deadline:
                # T12 follow-up (audit of c056341 / 832447c): the
                # forced-grid-charge path must use the *same*
                # telemetry the regular engine plan sees, not a
                # hard-coded ``valid_telemetry=True`` and
                # ``grid_v=raw.get("gridVoltage", 230.0)``. We
                # delegate to ``_validate_force_telemetry``,
                # which applies ``_finite_number`` and the
                # documented bounds, so a missing SOC, a NaN
                # voltage, or a malformed reserve_soc all block
                # the forced write — the very same way they
                # block the regular plan via the engine's own
                # checks at ``hems/engine.py``.
                forced_check = _validate_force_telemetry(
                    raw,
                    self._entry.options.get("reserve_soc", 20.0),
                )
                if not forced_check["valid"]:
                    _LOGGER.info(
                        "HEMS: force_grid_charge hold active but "
                        "telemetry invalid (soc=%r, reserve_soc=%r, "
                        "grid_v=%r, grid_ok=%r); not overriding",
                        forced_check["soc"],
                        forced_check["reserve_soc"],
                        forced_check["grid_voltage"],
                        forced_check["grid_ok"],
                    )
                    # Do not dispatch the forced decision. Fall
                    # through to the engine plan so the existing
                    # decision (which respects the same hold) is
                    # logged and applied.
                else:
                    # All telemetry fields are finite and within
                    # bounds. Pass the validated values to the
                    # engine's own hold so it can re-check the
                    # remaining guards (manual override, circuit
                    # breaker, hems_auto_off, mode validity,
                    # reserve floor). The forced path does not
                    # bypass any of those checks.
                    grid_v = forced_check["grid_voltage"]
                    is_online = forced_check["is_online"]
                    hold = self._hems._evaluation_hold(
                        hems_auto=self.hems_auto_mode,
                        smart_mode=self.smart_mode,
                        is_online=is_online,
                        valid_telemetry=True,
                        now=now,
                        buzzer_off=True,
                        soc=forced_check["soc"],
                        reserve_soc=forced_check["reserve_soc"],
                        soc_safety_margin=5.0,
                    )
                    if hold is not None and hold.skip:
                        # T12 does NOT bypass the engine's
                        # own holds. A manual override or a
                        # circuit breaker still wins; we
                        # only log a single info-level line so
                        # the operator can see why the timed
                        # hold has no effect right now.
                        _LOGGER.info(
                            "HEMS: force_grid_charge hold active but "
                            "engine has reason=%s; not overriding",
                            hold.reason,
                        )
                        # Honour the hold — fall through to
                        # the engine's plan so the existing
                        # decision (which respects the same
                        # hold) gets logged/applied.
                    else:
                        forced = self._hems.build_forced_decision(
                            "forced_grid_charge",
                            output_priority="0",  # USB
                            charger_priority="1",  # SNU
                            buzzer_off=True,
                        )
                        # Apply the same dedup the engine
                        # would have used. ``_apply_anti_flapping``
                        # honours the previous command's
                        # dedup window and the dwell lock; if
                        # the user just changed the inverter
                        # the forced command may be deduped
                        # into a no-op, which is the desired
                        # behaviour — the timer keeps running
                        # but the actuator does not race the
                        # user.
                        forced = self._hems._apply_anti_flapping(forced, now)
                        if forced is not None and not forced.skip:
                            self._log_decision(
                                forced,
                                current_output=raw.get("outputSourcePriority", ""),
                                current_charger=raw.get("chargerSourcePriority", ""),
                                soc=forced_check["soc"],
                                pv_w=forced_check["pv_w"],
                                load_w=forced_check["load_w"],
                                grid_w=forced_check["grid_w"],
                                batt_w=forced_check["battery_w"],
                                grid_v=forced_check["grid_voltage"],
                                grid_ok=bool(forced_check["grid_ok"]),
                                hour=now.hour,
                                forecast_today_kwh=getattr(
                                    self, "_forecast_today_kwh", None
                                ),
                                forecast_tomorrow_kwh=getattr(
                                    self, "_forecast_tomorrow_kwh", None
                                ),
                                reserve_soc=forced_check["reserve_soc"],
                                now=now,
                            )
                            await self._execute_hems_command(forced)
                        return
            else:
                # Deadline passed — clear and let the next
                # cycle run the regular plan.
                self._forced_charge_until = None
                _LOGGER.info("HEMS: force_grid_charge window ended; "
                             "engine resumes normal planning")

        # Execute command
        if not decision.skip:
            await self._execute_hems_command(decision)

    async def _execute_hems_command(self, decision: HemsDecision) -> None:
        """Execute a HEMS decision by calling the API."""
        success = True
        # A channel remains failed until its write is acknowledged, including
        # writes skipped because an earlier API call raised an exception.
        output_failed = decision.output_priority is not None
        charger_failed = decision.charger_priority is not None

        try:
            if decision.output_priority is not None:
                ok = await self.api.set_output_priority(decision.output_priority)
                output_failed = not ok
                if not ok:
                    success = False
                    _LOGGER.warning("HEMS: failed to set output → %s (%s)", decision.output_priority, decision.reason)
                else:
                    _LOGGER.debug("HEMS: output → %s (%s)", decision.output_priority, decision.reason)

            if decision.charger_priority is not None:
                ok = await self.api.set_charger_priority(decision.charger_priority)
                charger_failed = not ok
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
            self._hems.report_control_failure(
                datetime.now(), output_failed=output_failed, charger_failed=charger_failed)
        if hasattr(self._hems, "confirm_predictive_delivery"):
            self._hems.confirm_predictive_delivery(success)

    # ═══════════════════════════════════════════════════════════════════
    # STORM RISK
    # ═══════════════════════════════════════════════════════════════════

    async def _maybe_evaluate_storm_risk(self, now: datetime) -> None:
        """Evaluate storm risk from forecast weather data (every 15 min).

        T09 follow-up: feed the real
        ``weather_code`` / ``wind_speed_ms`` /
        ``precipitation_probability`` from the
        hourly forecast into
        ``evaluate_storm_risk``. The previous
        code passed three zeros, so the storm
        path never tripped regardless of the
        forecast. We also pick the next 6 hours
        by comparing ``datetime`` instances in the
        HA local timezone, not by string
        comparison (which misbehaves across date
        boundaries and at the DST cliff).
        """
        if self._forecast is None:
            return

        # Only check every 15 minutes
        if hasattr(self, "_last_storm_check") and self._last_storm_check:
            if (now - self._last_storm_check).total_seconds() < 900:
                return
        self._last_storm_check = now

        try:
            hourly = await self._forecast.get_hourly_forecast()
        except Exception as exc:
            # T09: a request failure must NOT clear
            # the previous risk state. We log the
            # reason and leave ``_storm_risk_*``
            # untouched so the operator keeps the
            # last valid signal.
            _LOGGER.debug(
                "Storm risk forecast fetch failed; "
                "preserving last valid score: %s",
                exc,
            )
            return
        if not hourly:
            _LOGGER.debug(
                "Storm risk forecast returned no hours; "
                "preserving last valid score"
            )
            return

        # T09: select the next 6 hours in the HA
        # timezone, not by lexicographic string
        # comparison. The forecast returns a
        # ``time`` field formatted in the
        # configured ``timezone_name`` (HA tz);
        # we convert it to a real ``datetime``
        # before comparing to ``now``.
        try:
            tz = ZoneInfo(self.hass.config.time_zone)
        except Exception:
            tz = ZoneInfo(self.timezone_name)
        now_local = (
            now.astimezone(tz) if now.tzinfo else
            now.replace(tzinfo=tz)
        )
        upcoming: list[dict] = []
        for h in hourly:
            ts = h.get("timestamp")
            if ts is None:
                continue
            try:
                local_dt = datetime.fromtimestamp(
                    int(ts), tz=timezone.utc
                ).astimezone(tz)
            except (TypeError, ValueError, OSError):
                continue
            if local_dt < now_local:
                continue
            upcoming.append(h)
            if len(upcoming) >= 6:
                break

        # T09: if the forecast has no upcoming
        # hours at all (e.g. only stale samples in
        # cache after a long pause), do not
        # synthesise a "clear" decision; keep the
        # last valid score.
        if not upcoming:
            _LOGGER.debug(
                "Storm risk forecast has no upcoming "
                "hours; preserving last valid score"
            )
            return

        max_risk_score = 0.0
        max_risk_reason = "clear"
        valid_evaluation = False
        for h in upcoming:
            # T09: read the real forecast values.
            # The forecast sanitiser (``_fetch_hourly``)
            # has already replaced NaN/inf/out-of-
            # range values with ``None``; we leave
            # the evaluator's defaults alone when
            # the field is missing so a hole in the
            # forecast never masks a real risk in
            # the *other* hours.
            weather_code = h.get("weather_code")
            wind_ms = h.get("wind_speed_ms")
            precip_pct = h.get("precipitation_probability")
            # A "no information" hour is *not* the
            # same as "all clear". The evaluator
            # only treats missing fields as zero
            # when ALL of them are missing; if at
            # least one is present we still call
            # the evaluator so the other fields
            # can flag the risk.
            if (
                weather_code is None
                and wind_ms is None
                and precip_pct is None
            ):
                continue
            risk = evaluate_storm_risk(
                weather_code=(
                    int(weather_code)
                    if weather_code is not None else None
                ),
                wind_speed_ms=(
                    float(wind_ms) if wind_ms is not None else 0.0
                ),
                precipitation_probability=(
                    float(precip_pct)
                    if precip_pct is not None else 0.0
                ),
            )
            valid_evaluation = True
            if risk.score > max_risk_score:
                max_risk_score = risk.score
                max_risk_reason = risk.reason

        if not valid_evaluation:
            # All 6 hours had no storm fields at
            # all. Preserve the last valid score;
            # do not synthesise a "clear" decision.
            _LOGGER.debug(
                "Storm risk forecast has 6 upcoming "
                "hours but no storm fields; "
                "preserving last valid score"
            )
            return

        self._storm_risk_score = max_risk_score
        self._storm_risk_reason = max_risk_reason

        # T10: only the *weather* cause is allowed
        # to act here. The grid-outage cause has
        # its own driver (``_evaluate_grid`` in
        # ``_run_hems_engine``) and must NOT be
        # touched by a forecast update — clearing
        # the risk score below must not clear
        # ``_auto_storm_outage``.
        auto_storm_enabled = self._entry.options.get(
            "auto_storm_by_forecast", False
        )
        if (
            auto_storm_enabled
            and max_risk_score >= 0.6
            and not self._auto_storm_weather
            and self.smart_mode != SmartMode.STORM
        ):
            _LOGGER.warning(
                "🌊 Storm risk %.0f%% (%s) → auto-activating "
                "Storm mode (weather cause)",
                max_risk_score * 100, max_risk_reason,
            )
            if self._previous_smart_mode_before_storm is None:
                # Remember the user's choice so we
                # can restore it when the cause
                # clears. If they had already picked
                # Storm by hand, we leave the
                # pointer at None — the engine
                # treats that as "user already
                # wanted Storm" and does not
                # overwrite it.
                self._previous_smart_mode_before_storm = (
                    self.smart_mode
                )
            self._auto_storm_weather = True

        # T10: the *clear* path only ever touches
        # the weather cause. A passing "all clear"
        # never silently clears an active outage
        # cause, and a forecast error does not
        # clear the weather cause either — that
        # already happens because we ``return``
        # before reaching this point on error.
        if self._auto_storm_weather and max_risk_score < 0.4:
            self._auto_storm_weather = False
            if not self._auto_storm_outage:
                # Both causes now clear — drop the
                # backup pointer so the next user
                # change to ``smart_mode`` doesn't
                # restore a stale value.
                self._previous_smart_mode_before_storm = None
            _LOGGER.info(
                "🌊 Weather storm risk cleared (score=%.2f)",
                max_risk_score,
            )

    # ═══════════════════════════════════════════════════════════════════
    # EXISTING HELPERS (unchanged from original)
    # ═══════════════════════════════════════════════════════════════════

    def _build_offline_state(self, now: datetime) -> dict[str, Any]:
        """Return a stable offline payload to keep entities available."""
        self._hems.invalidate_predictive("inverter_offline")
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

            ent = self._history_entity("load_power", "sensor.garazh_smart_solar_inverter_load_power")
            start = dt_util.utcnow() - timedelta(days=120)
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
                samples, dt_util.as_local(dt_util.utcnow()).replace(tzinfo=None), days=self._load_matrix_days
            )
            _LOGGER.info("Load history refreshed: %d full days", len(self._load_matrix))
        except Exception as exc:  # never break HEMS because of history
            _LOGGER.warning("Load history refresh failed: %s", exc)
            # Keep the one-hour limit even when recorder is unavailable.

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

    def _track_battery_cycle(self, soc: float | None) -> bool:
        """Track low→high SOC transitions for cycle counting.

        Delegates to BatterySoH tracker. T01: a None reading is
        dropped on the floor — we cannot infer a transition from
        "we don't know".
        """
        if soc is None:
            return False
        return self._battery_soh.track_soc(soc)

    def _add_soc_sample(self, raw: dict, corrected_soc: float | None) -> None:
        """Add a sample to the rolling 24h SOC history.

        T01: a missing/invalid SOC is NOT recorded. Recording a
        fabricated value here would bias the calibrator and the
        storm-risk scorer that consume this history downstream.
        """
        if corrected_soc is None:
            return
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
        """Integrate samples into daily kWh totals.

        T05: every counter is in kWh (not Wh).
        T06: the integration interval is the *real* elapsed time since
        the previous sample, not a hardcoded 5 s. Gaps larger than
        ``_MAX_SAMPLE_GAP_S`` are treated as offline: we do not
        integrate the stale power reading, so a 30-minute outage does
        not dump 30 minutes of phantom energy into the daily totals.
        Counters and the last sample timestamp are persisted at the
        end of every successful run so a restart does not zero out
        the day's running total.
        """
        # T06 follow-up: check out-of-order *before* we touch any
        # state. An out-of-order sample must not advance
        # ``_last_sample_ts`` (which would stretch the next
        # in-order interval and double-count the integration
        # window), and must not move ``_last_midnight`` (which
        # would zero a previous day's contribution if the
        # out-of-order sample happened to land on the *other*
        # day from the previous sample). We therefore defer the
        # midnight rollover to *after* this check.
        if self._last_sample_ts is not None:
            elapsed_s = (now - self._last_sample_ts).total_seconds()
            if elapsed_s < 0:
                _LOGGER.debug(
                    "Energy state: out-of-order sample ignored "
                    "(elapsed_s=%.3f, now=%s, last=%s)",
                    elapsed_s, now, self._last_sample_ts,
                )
                if self._energy_state_dirty:
                    self._maybe_persist_energy_state(now)
                return

        # ── Midnight rollover. Done *after* the out-of-order
        # check so a stray out-of-order sample that crosses
        # midnight does not zero the previous day. The check
        # itself runs before any state mutation. ──────────────
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self._last_midnight is not None and self._last_midnight != today:
            # Day changed since the previous sample. Decide whether
            # the *previous* day was the last day of a month, and
            # act accordingly. We do NOT just check ``now.day == 1``
            # because the integration may have been offline across
            # the month boundary (the audit explicitly calls out
            # the "Sep30 → Oct3 without any Oct1 call" scenario):
            # in that case the daily savings for Sep30 still live
            # in ``self._daily_savings_uah`` and must be folded
            # into the *September* monthly total before the new
            # month zeroes it. With the old ``if now.day == 1``
            # check, those savings were silently dropped.
            previous_was_last_day = (
                self._last_midnight.month != today.month
            )
            self._monthly_savings_uah += self._daily_savings_uah
            self._daily_pv_kwh = 0.0
            self._daily_grid_import_day_kwh = 0.0
            self._daily_grid_import_night_kwh = 0.0
            self._daily_grid_export_kwh = 0.0
            self._daily_battery_discharge_day_kwh = 0.0
            self._daily_battery_discharge_night_kwh = 0.0
            self._daily_savings_uah = 0.0
            if previous_was_last_day:
                # The previous day was the last day of an older
                # month. The accumulated ``_monthly_savings_uah``
                # belongs to that month, so we close it out and
                # start the new one at zero.
                self._monthly_savings_uah = 0.0
            # The rollover itself is a high-value state transition;
            # mark the energy state dirty so the next
            # ``_maybe_persist_energy_state`` bypasses the throttle
            # and writes immediately. Without this, a crash in the
            # throttle window would lose the previous day's total.
            self._mark_energy_state_dirty()
        self._last_midnight = today

        # Compute real elapsed time.
        if self._last_sample_ts is None:
            # First sample of a fresh coordinator. The audit accepts
            # using a nominal interval here — there is no previous
            # measurement to integrate. 5 s matches the documented
            # fetch cadence.
            dt_h = 5.0 / 3600.0
        else:
            elapsed_s = (now - self._last_sample_ts).total_seconds()
            # The out-of-order branch above already returned; if
            # we reach this point ``elapsed_s`` is either non-negative
            # or we passed the first-sample path. We do not need to
            # re-check ``elapsed_s < 0`` here.
            if elapsed_s > self._MAX_SAMPLE_GAP_S:
                # Offline gap. We do not integrate the stale reading
                # because the previous sample's power no longer
                # represents the present. The next valid sample will
                # start a fresh interval. We *do* still flush a
                # pending dirty state from a prior rollover, so a
                # midnight rollover that was followed by an offline
                # period does not lose the previous day's totals.
                self._last_sample_ts = now
                if self._energy_state_dirty:
                    self._maybe_persist_energy_state(now)
                return
            else:
                dt_h = elapsed_s / 3600.0
        self._last_sample_ts = now

        daytime = self._is_daytime(now)

        pv_w = raw.get("pvPower", 0.0) or 0.0
        grid_w = raw.get("gridPower", 0.0) or 0.0
        battery_w = raw.get("batteryPower", 0.0) or 0.0

        # T05: convert W·h to kWh before storing.
        self._daily_pv_kwh += pv_w * dt_h / 1000.0

        if grid_w > 10:
            if daytime:
                self._daily_grid_import_day_kwh += grid_w * dt_h / 1000.0
            else:
                self._daily_grid_import_night_kwh += grid_w * dt_h / 1000.0
        elif grid_w < -10:
            self._daily_grid_export_kwh += abs(grid_w) * dt_h / 1000.0

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

        # T06: persist the running state. We do this *after* the
        # counters are updated, so a crash mid-update only loses the
        # current interval, not the accumulated day. Persistence
        # is rate-limited to avoid hammering ``async_update_entry``
        # on every 5 s cycle; day/month rollovers set the dirty
        # flag so the next persist call always goes through.
        self._maybe_persist_energy_state(now)

    # ═══════════════════════════════════════════════════════════════════
    # T06: persistence helpers for energy counters
    # ═══════════════════════════════════════════════════════════════════

    def _energy_state_snapshot(self, now: datetime) -> dict[str, Any]:
        """Return the JSON-serialisable blob that we persist on options."""
        return {
            "schema": self._ENERGY_STATE_SCHEMA_VERSION,
            "now_iso": now.isoformat() if now else None,
            "last_midnight_iso": (
                self._last_midnight.isoformat() if self._last_midnight else None
            ),
            "last_sample_ts_iso": (
                self._last_sample_ts.isoformat() if self._last_sample_ts else None
            ),
            "daily_pv_kwh": self._daily_pv_kwh,
            "daily_grid_import_day_kwh": self._daily_grid_import_day_kwh,
            "daily_grid_import_night_kwh": self._daily_grid_import_night_kwh,
            "daily_grid_export_kwh": self._daily_grid_export_kwh,
            "daily_battery_discharge_day_kwh": self._daily_battery_discharge_day_kwh,
            "daily_battery_discharge_night_kwh": self._daily_battery_discharge_night_kwh,
            "daily_savings_uah": self._daily_savings_uah,
            "monthly_savings_uah": self._monthly_savings_uah,
        }

    def _persist_energy_state(self, now: datetime, *, force: bool = False) -> bool:
        """Write the running counter snapshot to ``entry.options``.

        T06 follow-up: rate-limited. Without throttling, a 5 s
        fetch cadence would issue one ``async_update_entry`` call
        per cycle, which both wakes HA storage listeners and races
        with the other option writers in this coordinator (the
        user-toggle setters, the predictive persistence, the HEMS
        auto-mode toggle). The throttle window is
        ``_ENERGY_PERSIST_MIN_INTERVAL_S`` (30 s by default). The
        first persist after a fresh boot always goes through
        because ``_last_energy_persist_at`` is ``None``.

        Returns True iff an actual write was performed, so the
        caller can keep an eye on persistence health. Failure is
        logged at debug level only; the in-memory counters remain
        the source of truth.
        """
        if getattr(self, "_entry", None) is None or getattr(self, "hass", None) is None:
            return False
        if not force and self._last_energy_persist_at is not None:
            elapsed = (now - self._last_energy_persist_at).total_seconds()
            if elapsed < self._ENERGY_PERSIST_MIN_INTERVAL_S:
                return False
        try:
            blob = self._energy_state_snapshot(now)
            import json as _json
            new_opts = dict(self._entry.options)
            new_opts["_energy_state"] = _json.dumps(blob, ensure_ascii=False)
            self.hass.config_entries.async_update_entry(
                self._entry, options=new_opts
            )
            self._last_energy_persist_at = now
            return True
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("persist energy state failed: %s", exc)
            return False

    def _mark_energy_state_dirty(self) -> None:
        """T06 follow-up: mark the energy state as needing a flush.

        Callers (midnight rollover, month rollover, large counter
        jumps) flip this flag so the next ``_maybe_persist_energy_state``
        call will write even if the throttle window has not
        elapsed. Without this, a midnight rollover could sit in
        memory for up to 30 s before the next sample triggered a
        write, which means a crash in that window would lose the
        previous day's total.
        """
        self._energy_state_dirty = True

    def _maybe_persist_energy_state(self, now: datetime, *, force: bool = False) -> bool:
        """Persist the energy state when due.

        Honours the throttle, but bypasses it when ``force=True``
        or when the dirty flag is set (a midnight rollover or a
        month rollover has just happened). Returns True if a write
        actually went through.
        """
        if force or self._energy_state_dirty:
            ok = self._persist_energy_state(now, force=True)
            if ok:
                self._energy_state_dirty = False
            return ok
        return self._persist_energy_state(now, force=False)

    def _restore_energy_state(self, blob: Any) -> None:
        """Hydrate in-memory counters from a previously persisted blob.

        Malformed or stale blobs are silently ignored — the counters
        start at zero and the integration accumulates from the next
        sample. We do not raise, because the integration is allowed
        to start fresh if its prior state is no longer trustworthy.
        """
        if not blob:
            return
        import json as _json
        try:
            data = _json.loads(blob) if isinstance(blob, str) else blob
        except (TypeError, ValueError):
            return
        if not isinstance(data, dict):
            return
        if data.get("schema") != self._ENERGY_STATE_SCHEMA_VERSION:
            # Older or newer schema — keep current defaults.
            return

        def _ts(key: str) -> datetime | None:
            value = data.get(key)
            if not value:
                return None
            try:
                # ``fromisoformat`` understands both naive and tz-aware
                # ISO-8601 strings. We keep whatever the saved value
                # had; the next sample will normalise to naive.
                return datetime.fromisoformat(value)
            except (TypeError, ValueError):
                return None

        self._last_midnight = _ts("last_midnight_iso")
        self._last_sample_ts = _ts("last_sample_ts_iso")
        for name in (
            "daily_pv_kwh",
            "daily_grid_import_day_kwh",
            "daily_grid_import_night_kwh",
            "daily_grid_export_kwh",
            "daily_battery_discharge_day_kwh",
            "daily_battery_discharge_night_kwh",
            "daily_savings_uah",
            "monthly_savings_uah",
        ):
            value = data.get(name)
            try:
                if value is None:
                    continue
                setattr(self, "_" + name, float(value))
            except (TypeError, ValueError):
                continue

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

    @staticmethod
    def _unwrap_history_results(
        results: list[Any],
    ) -> tuple[tuple[list, bool], tuple[list, bool], tuple[list, bool], tuple[dict, float]]:
        """T14: convert ``asyncio.gather(return_exceptions=True)``
        results into four safely-typed slots **with a fallback flag**.

        The audit's review point is that the previous version
        returned ``[]`` (or ``{}``) for a failed endpoint and the
        caller then wrote that empty value into the coordinator
        cache, overwriting the last known good series. The chart
        would show a flat line every time the cloud hiccupped.

        This variant returns a ``(value, is_fallback)`` pair for
        each slot. The caller (``_async_update_data``) is
        required to skip the cache write whenever
        ``is_fallback`` is True: the audit explicitly forbids
        replacing real data with a placeholder, and a partial
        failure in one endpoint must not poison the other
        three.

        The ``total_data`` slot additionally returns the numeric
        ``total_kwh`` the caller would have computed — an
        exception in the total endpoint therefore leaves the
        existing ``self.total_energy_kwh`` untouched instead of
        zeroing it.

        Returns
        -------
        ((today_power, today_fallback),
         (monthly_energy, monthly_fallback),
         (yearly_energy, yearly_fallback),
         (total_data, total_kwh))

        ``is_fallback`` is True for the slot iff the source value
        was an exception, a wrong type, or the slot was missing
        from the gather result. Real values — even an empty list
        from a successful endpoint — keep ``is_fallback=False``.

        Defensive guarantees (the audit's three axes):

        1. exception instances → ``([], True)`` / ``({}, 0.0)``;
        2. wrong types (string, None) for the dict slot → ``({}, 0.0)``;
        3. short result lists → missing slots default to
           ``([], True)`` / ``({}, 0.0)`` rather than raising
           ``IndexError``.

        Exception text is deliberately NOT included in the
        returned value (the audit calls out that an error message
        can carry a URL with a token).
        """
        # Pad to length 4 so the unpack never fails; treat missing
        # slots as "we know nothing" (fallback).
        padded = list(results) + [None] * (4 - len(results))
        today_power_raw, monthly_raw, yearly_raw, total_raw = padded[:4]

        def _safe_list(value: Any, label: str) -> tuple[list, bool]:
            if isinstance(value, BaseException):
                _LOGGER.warning(
                    "HistoryCoordinator: %s endpoint failed: %s",
                    label,
                    type(value).__name__,
                )
                return [], True
            if isinstance(value, list):
                return value, False
            _LOGGER.warning(
                "HistoryCoordinator: %s endpoint returned unexpected %s",
                label,
                type(value).__name__,
            )
            return [], True

        def _safe_total(value: Any, label: str) -> tuple[dict, float, bool]:
            """Convert the total-energy endpoint's payload.

            Returns ``(value_dict, total_kwh, is_fallback)``. The
            dict returned to the caller mirrors the input when
            the input was a real dict; it is the empty ``{}``
            for every fallback path.

            The audit's review point: a real API response from
            ``api.fetch_total_energy`` always includes **both**
            ``value`` and ``totalEnergy`` keys (see
            ``api.py:fetch_total_energy``). A dict that has
            only one of them — or a non-dict — is therefore
            suspect. A transient backend error that returns
            ``{"value": 0}`` instead of the proper pair would
            otherwise overwrite the cached total with 0.0. We
            require the pair to be present and equal; otherwise
            the slot is treated as a fallback and the cache
            write is suppressed by the caller.
            """
            if isinstance(value, BaseException):
                _LOGGER.warning(
                    "HistoryCoordinator: %s endpoint failed: %s",
                    label,
                    type(value).__name__,
                )
                return {}, 0.0, True
            if not isinstance(value, dict):
                _LOGGER.warning(
                    "HistoryCoordinator: %s endpoint returned unexpected %s",
                    label,
                    type(value).__name__,
                )
                return {}, 0.0, True
            # Real API responses always have both ``value`` and
            # ``totalEnergy`` and they match. Anything else is a
            # signal that the payload is malformed and we should
            # not trust the number on its own.
            v = value.get("value")
            e = value.get("totalEnergy")
            if v is None or e is None:
                return {}, 0.0, True
            try:
                v_f = float(v)
                e_f = float(e)
            except (TypeError, ValueError):
                return {}, 0.0, True
            if v_f != e_f:
                # The two fields disagree; the cloud has a stale
                # value somewhere. Treat as fallback rather than
                # silently pick one.
                return {}, 0.0, True
            if not math.isfinite(v_f):
                return {}, 0.0, True
            # The audit's follow-up review pointed out that the
            # API path can return a perfectly-formed pair even
            # when the underlying data is missing: the
            # ``_raw_value`` field the API client now stamps onto
            # the dict carries the raw ``v`` it managed to extract
            # from the cloud. A real cumulative reading always
            # has ``_raw_value is not None``; a fallback path
            # (empty list, missing keys, non-numeric v) sets it
            # to ``None`` so the helper can distinguish a *real*
            # 0.0 from a *placeholder* 0.0. We mirror that
            # contract here.
            if value.get("_raw_value") is None:
                return {}, 0.0, True
            # T14 follow-up: ``_pair_valid`` is False when the
            # cloud's ``totalEnergy`` was missing or non-numeric
            # and the API body therefore left that field as
            # ``None``. The previous version of the body masked
            # the field with ``0.0``, which the pair check
            # (which demands both fields agree) then accepted as
            # a valid reading for ``{"value": 0,
            # "totalEnergy": "bad"}`` — overwriting a populated
            # cache with 0.0. We now require an explicit
            # ``_pair_valid=True`` flag, in addition to the
            # equality and finiteness checks above, to accept
            # the dict as a real reading.
            if value.get("_pair_valid") is False:
                return {}, 0.0, True
            return value, v_f, False

        today_pair = _safe_list(today_power_raw, "daily")
        monthly_pair = _safe_list(monthly_raw, "monthly")
        yearly_pair = _safe_list(yearly_raw, "yearly")
        total_pair = _safe_total(total_raw, "total")
        return today_pair, monthly_pair, yearly_pair, total_pair

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch historical data from the API.

        T14: ``asyncio.gather(return_exceptions=True)`` returns exception
        instances in the result list when a coroutine raises. The
        previous code path then called ``len(...)`` and ``.get(...)``
        on those instances, which crashed the whole coordinator and
        hid the successful series.

        The follow-up review pointed out that the *cache write*
        path was still wrong: the previous version returned ``[]``
        for a failed endpoint and the caller wrote that empty
        value into ``self.today_hourly_power``, overwriting the
        last known good series. The chart would show a flat line
        every time the cloud hiccupped. The fix is the
        ``(value, is_fallback)`` return contract on
        ``_unwrap_history_results``: when ``is_fallback`` is True
        we keep the previous cached value untouched and only
        update the diagnostics attributes the dashboard reads.
        The total slot additionally preserves the previous
        ``self.total_energy_kwh`` when the total endpoint fails.
        """
        _LOGGER.debug("HistoryCoordinator: fetching historical data")

        try:
            results = await asyncio.gather(
                self.api.fetch_daily_power(),
                self.api.fetch_monthly_energy(),
                self.api.fetch_yearly_energy(),
                self.api.fetch_total_energy(),
                return_exceptions=True,
            )
            (
                today_pair,
                monthly_pair,
                yearly_pair,
                total_pair,
            ) = self._unwrap_history_results(results)
            today_power, today_fallback = today_pair
            monthly_energy, monthly_fallback = monthly_pair
            yearly_energy, yearly_fallback = yearly_pair
            total_data, total_kwh, total_fallback = total_pair

            _LOGGER.info(
                "HistoryCoordinator: daily=%d monthly=%d yearly=%d total_keys=%s "
                "(fallback daily=%s monthly=%s yearly=%s total=%s)",
                len(today_power) if isinstance(today_power, list) else -1,
                len(monthly_energy) if isinstance(monthly_energy, list) else -1,
                len(yearly_energy) if isinstance(yearly_energy, list) else -1,
                (
                    list(total_data.keys())
                    if isinstance(total_data, dict)
                    else type(total_data).__name__
                ),
                today_fallback, monthly_fallback, yearly_fallback,
                not isinstance(total_data, dict) or not total_data,
            )
            if isinstance(today_power, list) and today_power:
                _LOGGER.info("Daily sample: %s", today_power[0])
            if isinstance(monthly_energy, list) and monthly_energy:
                _LOGGER.info("Monthly sample: %s", monthly_energy[0])

            # ── T14 follow-up: last-known-good cache writes ────────────
            # The cache is only updated when the endpoint produced a
            # real value. A failure leaves the previous data in
            # place, so a single endpoint hiccup never blanks a
            # chart. The dashboard still gets a *consistent* view
            # from the public ``data`` dict, which we always
            # populate from the (possibly fallback) values we
            # computed here.
            if not today_fallback and isinstance(today_power, list):
                self.today_hourly_power = today_power
            if not monthly_fallback and isinstance(monthly_energy, list):
                self.monthly_daily_energy = monthly_energy
            if not yearly_fallback and isinstance(yearly_energy, list):
                self.yearly_monthly_energy = yearly_energy
            # The total endpoint is more subtle: the audit's
            # follow-up pointed out that ``total_data`` may be a
            # non-empty dict (``{"value": 0}``) that nonetheless
            # does not come from the real API path — the field
            # pairing check in ``_safe_total`` is what tells us
            # whether to trust the number. Use the explicit
            # ``total_fallback`` flag rather than the
            # ``isinstance(..., dict) and total_data`` heuristic
            # so a malformed ``{"value": 0}`` cannot zero the
            # cached total.
            if not total_fallback:
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
