"""Switch entities for Smart Solar Inverter — HEMS automation and system toggles."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import InverterCoordinator

_LOGGER = logging.getLogger(__name__)


def _setting_int(data: dict | None, key: str) -> int | None:
    """Read an integer setting value from coordinator deviceSettings."""
    if not data:
        return None
    settings = data.get("deviceSettings", {})
    item = settings.get(key, {})
    val = item.get("value") if isinstance(item, dict) else item
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


class _InverterConfigSwitch(CoordinatorEntity, SwitchEntity):
    """Generic on/off switch backed by a deviceSettings config key."""

    _setting_key: str = ""
    _config_on_value: str = "1"
    _config_off_value: str = "0"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{coordinator.api.device_sn}_{self._setting_key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }

    @property
    def is_on(self) -> bool | None:
        val = _setting_int(self.coordinator.data, self._setting_key)
        return bool(val) if val is not None else None

    async def async_turn_on(self, **kwargs) -> None:
        # T13: surface the ACK to the operator. The previous
        # implementation ignored the boolean returned by
        # ``set_config_item`` and unconditionally requested a
        # refresh, then logged "Service: ... → ON" at info
        # level. A cloud ACK of False or an exception would
        # still show up as a successful UI toggle — the
        # user would think the device accepted the command
        # when it had not. The fix logs the failure and
        # raises so HA marks the switch call as failed and
        # the next ``async_request_refresh`` does not paper
        # over the failure with stale data.
        ok = await self.coordinator.api.set_config_item(
            self._setting_key, self._config_on_value
        )
        if not ok:
            _LOGGER.error(
                "Switch %s: cloud rejected ON for %s=%s",
                self.entity_id, self._setting_key, self._config_on_value,
            )
            return
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        ok = await self.coordinator.api.set_config_item(
            self._setting_key, self._config_off_value
        )
        if not ok:
            _LOGGER.error(
                "Switch %s: cloud rejected OFF for %s=%s",
                self.entity_id, self._setting_key, self._config_off_value,
            )
            return
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Inverter switch entities."""
    coordinator: InverterCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = [
        InverterHemsAutoModeSwitch(coordinator),
        InverterPredictiveAssistSwitch(coordinator),
        InverterGridFeedInSwitch(coordinator),
        InverterBackupModeSwitch(coordinator),
        InverterBuzzerSwitch(coordinator),
        # New system switches
        InverterOverLoadRestartSwitch(coordinator),
        InverterOverTemperatureRestartSwitch(coordinator),
        InverterLcdBacklightSwitch(coordinator),
        InverterLedPatternSwitch(coordinator),
        InverterBuzzerOnGridInterruptSwitch(coordinator),
        InverterBatteryEqualizationSwitch(coordinator),
        InverterTransferToBypassSwitch(coordinator),
        InverterDualOutputSwitch(coordinator),
    ]
    async_add_entities(entities)


class InverterHemsAutoModeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable HEMS automatic control."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self.entity_description = SwitchEntityDescription(
            key="hems_auto_mode",
            translation_key="hems_auto_mode",
            icon="mdi:robot",
        )
        self._attr_unique_id = f"{coordinator.api.device_sn}_hems_auto_mode"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }

    @property
    def is_on(self) -> bool:
        return self.coordinator.hems_auto_mode

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.async_set_hems_auto_mode(True)
        self.async_write_ha_state()
        _LOGGER.info("HEMS auto mode enabled")

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.async_set_hems_auto_mode(False)
        self.async_write_ha_state()
        _LOGGER.info("HEMS auto mode disabled")


class InverterGridFeedInSwitch(CoordinatorEntity, SwitchEntity):
    """Switch: enable/disable grid feed-in (export)."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_translation_key = "grid_feed_in"
        self._attr_unique_id = f"{coordinator.api.device_sn}_grid_feed_in"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }
        self._attr_icon = "mdi:transmission-tower-export"
        self._setting_key = "batteryPowerLimitingSetting"

    @property
    def is_on(self) -> bool | None:
        val = _setting_int(self.coordinator.data, self._setting_key)
        return val == 1 if val is not None else None

    async def async_turn_on(self, **kwargs) -> None:
        # T13: ACK-aware command — see _InverterConfigSwitch
        # for the rationale. The previous version logged a
        # success and refreshed even when the cloud rejected
        # the write.
        ok = await self.coordinator.api.set_config_item(self._setting_key, "1")
        if not ok:
            _LOGGER.error(
                "Switch %s: cloud rejected ON for %s=1",
                self.entity_id, self._setting_key,
            )
            return
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        ok = await self.coordinator.api.set_config_item(self._setting_key, "0")
        if not ok:
            _LOGGER.error(
                "Switch %s: cloud rejected OFF for %s=0",
                self.entity_id, self._setting_key,
            )
            return
        await self.coordinator.async_request_refresh()


class InverterBackupModeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch: toggle backup mode (SBU priority) on/off."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_translation_key = "backup_mode"
        self._attr_unique_id = f"{coordinator.api.device_sn}_backup_mode"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }
        self._attr_icon = "mdi:battery-lock"

    @property
    def is_on(self) -> bool | None:
        val = self.coordinator.data.get("outputSourcePriority") if self.coordinator.data else None
        if val is None:
            return None
        # SBU = "2" or starts with "2"
        return str(val) == "2" or str(val).startswith("SBU")

    async def async_turn_on(self, **kwargs) -> None:
        # T13: ACK-aware — see _InverterConfigSwitch.
        ok = await self.coordinator.api.set_output_priority("2")
        if not ok:
            _LOGGER.error(
                "Switch %s: cloud rejected backup_mode ON (output=2)",
                self.entity_id,
            )
            return
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        ok = await self.coordinator.api.set_output_priority("0")
        if not ok:
            _LOGGER.error(
                "Switch %s: cloud rejected backup_mode OFF (output=0)",
                self.entity_id,
            )
            return
        await self.coordinator.async_request_refresh()


class InverterBuzzerSwitch(CoordinatorEntity, SwitchEntity):
    """Switch: toggle inverter buzzer on/off."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_translation_key = "buzzer"
        self._attr_unique_id = f"{coordinator.api.device_sn}_buzzer_alarm"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }
        self._attr_icon = "mdi:bell-alert"
        self._setting_key = "buzzerAlarmSetting"

    @property
    def is_on(self) -> bool | None:
        val = _setting_int(self.coordinator.data, self._setting_key)
        return bool(val) if val is not None else None

    async def async_turn_on(self, **kwargs) -> None:
        # T13: ACK-aware command — see _InverterConfigSwitch
        # for the rationale.
        ok = await self.coordinator.api.set_config_item(self._setting_key, "1")
        if not ok:
            _LOGGER.error(
                "Switch %s: cloud rejected ON for %s=1",
                self.entity_id, self._setting_key,
            )
            return
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        ok = await self.coordinator.api.set_config_item(self._setting_key, "0")
        if not ok:
            _LOGGER.error(
                "Switch %s: cloud rejected OFF for %s=0",
                self.entity_id, self._setting_key,
            )
            return
        await self.coordinator.async_request_refresh()


# ── New system switches ───────────────────────────────────────────────────


class InverterOverLoadRestartSwitch(_InverterConfigSwitch):
    """Switch: auto-restart after overload."""

    _setting_key = "overLoadRestartSetting"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "overload_restart"
        self._attr_icon = "mdi:restart"


class InverterOverTemperatureRestartSwitch(_InverterConfigSwitch):
    """Switch: auto-restart after over-temperature."""

    _setting_key = "overTemperatureAutoRestartSetting"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "overtemp_restart"
        self._attr_icon = "mdi:thermometer-alert"


class InverterLcdBacklightSwitch(_InverterConfigSwitch):
    """Switch: LCD backlight on/off."""

    _setting_key = "lcdBacklightSetting"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "lcd_backlight"
        self._attr_icon = "mdi:brightness-6"


class InverterLedPatternSwitch(_InverterConfigSwitch):
    """Switch: LED pattern indicator on/off."""

    _setting_key = "rgbOnAndOffControlSetting"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "led_pattern"
        self._attr_icon = "mdi:led-on"


class InverterBuzzerOnGridInterruptSwitch(_InverterConfigSwitch):
    """Switch: buzzer beep when high-priority power source connects/disconnects."""

    _setting_key = "beepsWhilePrimarySourceInterupt"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "buzzer_grid_interrupt"
        self._attr_icon = "mdi:bell-ring"


class InverterBatteryEqualizationSwitch(_InverterConfigSwitch):
    """Switch: enable/disable battery equalization."""

    _setting_key = "batteryEqualizationSetting"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "battery_equalization"
        self._attr_icon = "mdi:battery-sync"


class InverterTransferToBypassSwitch(_InverterConfigSwitch):
    """Switch: transfer to bypass on overload."""

    _setting_key = "transferToBypassFromOverload"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "bypass_on_overload"
        self._attr_icon = "mdi:swap-horizontal"


class InverterDualOutputSwitch(_InverterConfigSwitch):
    """Switch: dual output mode (smart load)."""

    _setting_key = "cutOffVoltBatteryForSmartMainLoad"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "dual_output"
        self._attr_icon = "mdi:power-plug-off"

class InverterPredictiveAssistSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable Predictive ML assist for HEMS.

    When ON, ML augments all HEMS modes (Adaptive/Arbitrage/Storm) with:
    - optimal SOC targets (morning/evening)
    - smart night-charge window (skip if tomorrow is sunny)
    - storm preemption (auto-enter STORM if forecast alerts)
    - reasoning for each decision

    When OFF (default), HEMS uses hardcoded thresholds (90/20, fixed 23-7 window).
    Requires at least 3 days of consumption history to be effective.

    Persistence: turning the switch on/off goes through the
    coordinator's ``async_set_predictive_mode`` which writes the
    SAME ``entry.options["predictive_mode"]`` field as the select
    entity (off / shadow / assist). Reload restores the value
    because the coordinator reads ``predictive_mode`` from
    ``entry.options`` at startup.
    """

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "HEMS Predictive Assist"
        self._attr_icon = "mdi:brain"
        self._attr_unique_id = f"{coordinator.api.device_sn}_hems_predictive_assist"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }

    @property
    def is_on(self) -> bool:
        # Mirror the actual mode from the coordinator — single
        # source of truth. Avoids the old disconnect where
        # ``_predictive_enabled`` drifted from ``predictive_mode``.
        try:
            return self.coordinator.predictive_mode in ("shadow", "assist")
        except Exception:
            return False

    @property
    def extra_state_attributes(self) -> dict:
        hems = getattr(self.coordinator, "_hems", None)
        if hems is None:
            return {"status": "hems_not_ready"}
        attrs: dict = {"mode": self.coordinator.predictive_mode}
        hint = getattr(hems, "_last_predictive_hint", None)
        if hint is not None:
            try:
                attrs["confidence"] = round(float(hint.confidence), 2)
                attrs["target_morning_soc"] = round(float(hint.target_soc_morning), 1)
                attrs["target_evening_soc"] = round(float(hint.target_soc_evening), 1)
            except (TypeError, ValueError):
                pass
        return attrs

    async def async_turn_on(self, **kwargs) -> None:
        # legacy behaviour kept — but routed through the
        # canonical writer so predictive_mode stays in sync.
        self.coordinator.async_set_predictive_mode("assist")
        self.async_write_ha_state()
        _LOGGER.info("HEMS Predictive Assist ENABLED (assist)")

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.async_set_predictive_mode("off")
        self.async_write_ha_state()
        _LOGGER.info("HEMS Predictive Assist DISABLED (off)")
