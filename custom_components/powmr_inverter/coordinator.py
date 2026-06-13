"""DataUpdateCoordinator for Smart Solar Inverter — polls real-time data every 5s."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import PowMrApiClient, PowMrOfflineError, TokenExpiredError
from .const import DOMAIN
from .hems.soc_correction import get_real_soc

_LOGGER = logging.getLogger(__name__)


class PowMrCoordinator(DataUpdateCoordinator):
    """Coordinator that polls PowMr API and computes derived values."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: PowMrApiClient,
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

        # HEMS state (mirrors AppProvider fields)
        self.smart_mode: int = 0  # 0=Adaptive, 1=Arbitrage, 2=Storm
        self.hems_auto_mode: bool = True

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

        # Command dedup
        self._last_cmd_output: str | None = None
        self._last_cmd_charger: str | None = None
        self._last_cmd_output_at: datetime | None = None
        self._last_cmd_charger_at: datetime | None = None
        self._last_output_switch_at: datetime | None = None
        self._manual_override_until: datetime | None = None

        # Battery keepalive
        self._last_battery_activity_at: datetime | None = None
        self._keepalive_in_progress: bool = False

        # Load demand EWMA profile
        self._load_profile: dict[int, float] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest data and compute derived values."""
        now = datetime.now()

        try:
            raw = await self.api.fetch_realtime_data()
        except TokenExpiredError:
            _LOGGER.error("Token expired — triggering re-auth")
            self._entry.async_start_reauth(self.hass)
            return self._build_offline_state(now)
        except PowMrOfflineError:
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

        # ── Compute corrected SOC ────────────────────────────────
        reported_soc = raw.get("batterySoc", 100.0)
        voltage = raw.get("batteryVoltage", 52.0)
        current = raw.get("batteryCurrent", 0.0)
        corrected_soc = get_real_soc(reported_soc, voltage, current)

        # ── Grid outage detection ────────────────────────────────
        grid_v = raw.get("gridVoltage", 230.0)
        grid_available, grid_transition = self._evaluate_grid(grid_v)

        # ── Battery cycle tracking ───────────────────────────────
        cycle_completed = self._track_battery_cycle(corrected_soc)
        if cycle_completed:
            self._battery_cycle_count += 1

        # ── SOC history ──────────────────────────────────────────
        self._add_soc_sample(raw, corrected_soc)

        # ── EWMA load profile update ─────────────────────────────
        self._update_load_profile(now.hour, raw.get("loadPower", 0.0))

        # ── CO2 update ───────────────────────────────────────────
        self.api._update_co2()

        # ── Device settings (fetched immediately on first poll, then every ~60s) ─
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

        return {
            **raw,
            "correctedSoc": corrected_soc,
            "gridAvailable": grid_available,
            "gridTransition": grid_transition,
            "online": True,
            "lastUpdated": now.isoformat(),
            "deviceSettings": device_settings,
        }

    def _build_offline_state(self, now: datetime) -> dict[str, Any]:
        """Return a stable offline payload to keep entities available."""
        if self.data is not None:
            fallback = dict(self.data)
            fallback["online"] = False
            fallback["lastUpdated"] = now.isoformat()
            return fallback

        # Initial startup fallback if we have not received any valid data yet.
        return {
            "pvPower": 0.0,
            "gridPower": 0.0,
            "batteryPower": 0.0,
            "loadPower": 0.0,
            "batterySoc": 100.0,
            "pvVoltage": 0.0,
            "gridVoltage": 230.0,
            "batteryVoltage": 52.0,
            "loadPercentage": 0.0,
            "workingMode": "unknown",
            "outputSourcePriority": "",
            "chargerSourcePriority": "",
            "batteryCurrent": 0.0,
            "correctedSoc": 100.0,
            "gridAvailable": True,
            "gridTransition": "none",
            "online": False,
            "lastUpdated": now.isoformat(),
        }

    # ── Grid outage detection (ported from GridOutageDetector) ────────

    def _evaluate_grid(self, grid_voltage: float) -> tuple[bool, str]:
        """Evaluate grid availability with hysteresis.

        Returns (available: bool, transition: str).
        """
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
                    _LOGGER.warning(
                        "⚡ Grid OUTAGE detected (V=%.1f)", grid_voltage
                    )
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
                    _LOGGER.info(
                        "🔌 Grid RESTORED (V=%.1f)", grid_voltage
                    )
            else:
                self._grid_outage_up_count = 0

        return self._grid_available, transition

    # ── Battery cycle tracking (ported from BatteryTrackerService) ────

    def _track_battery_cycle(self, soc: float) -> bool:
        """Track low→high SOC transitions for cycle counting.

        Returns True if a full cycle was completed.
        """
        if not self._battery_in_low and soc <= 30.0:
            self._battery_in_low = True
        elif self._battery_in_low and soc >= 80.0:
            self._battery_in_low = False
            _LOGGER.info("Battery cycle completed: #%d", self._battery_cycle_count + 1)
            return True
        return False

    # ── SOC History ──────────────────────────────────────────────────

    def _add_soc_sample(self, raw: dict, corrected_soc: float) -> None:
        """Add a sample to the rolling 24h SOC history."""
        now = datetime.now()

        # Throttle: max one sample per ~4.5 minutes
        if self._last_soc_sample_at is not None:
            if (now - self._last_soc_sample_at).total_seconds() < 270:
                return

        self._last_soc_sample_at = now

        sample = {
            "t": now.timestamp(),
            "soc": corrected_soc,
            "pv": raw.get("pvPower", 0.0),
            "load": raw.get("loadPower", 0.0),
            "battery": raw.get("batteryPower", 0.0),
        }
        self._soc_history.append(sample)

        # Keep only last 24h / max 288 entries
        cutoff = now - timedelta(hours=24)
        self._soc_history = [
            s for s in self._soc_history
            if s["t"] >= cutoff.timestamp()
        ]
        if len(self._soc_history) > 288:
            self._soc_history = self._soc_history[-288:]

    # ── EWMA Load Profile ────────────────────────────────────────────

    def _update_load_profile(self, hour: int, load_w: float) -> None:
        """Update EWMA load profile (α=0.25)."""
        clamped = max(100.0, min(12000.0, load_w))
        alpha = 0.25
        old = self._load_profile.get(hour, clamped)
        self._load_profile[hour] = alpha * clamped + (1 - alpha) * old
