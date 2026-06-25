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
        await self.coordinator.api.set_config_item(self._setting_key, self._config_on_value)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.api.set_config_item(self._setting_key, self._config_off_value)
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
        InverterGridChargingSwitch(coordinator),
        InverterGridFeedInSwitch(coordinator),
        InverterBackupModeSwitch(coordinator),
        InverterBuzzerSwitch(coordinator),
        InverterEcoModeSwitch(coordinator),
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
        self.coordinator.hems_auto_mode = True
        self.async_write_ha_state()
        _LOGGER.info("HEMS auto mode enabled")

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.hems_auto_mode = False
        self.async_write_ha_state()
        _LOGGER.info("HEMS auto mode disabled")


class InverterGridChargingSwitch(CoordinatorEntity, SwitchEntity):
    """Switch: enable/disable grid (AC) battery charging."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_translation_key = "grid_charging"
        self._attr_unique_id = f"{coordinator.api.device_sn}_grid_charging"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }
        self._attr_icon = "mdi:transmission-tower-import"
        self._setting_key = "acChargingSwitch"

    @property
    def is_on(self) -> bool | None:
        val = _setting_int(self.coordinator.data, self._setting_key)
        return bool(val) if val is not None else None

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.api.set_config_item(self._setting_key, "1")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.api.set_config_item(self._setting_key, "0")
        await self.coordinator.async_request_refresh()


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
        await self.coordinator.api.set_config_item(self._setting_key, "1")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.api.set_config_item(self._setting_key, "0")
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
        await self.coordinator.api.set_output_priority("2")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.api.set_output_priority("0")
        await self.coordinator.async_request_refresh()


class InverterBuzzerSwitch(CoordinatorEntity, SwitchEntity):
    """Switch: toggle inverter buzzer on/off."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_translation_key = "buzzer"
        self._attr_unique_id = f"{coordinator.api.device_sn}_buzzer"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }
        self._attr_icon = "mdi:bell-alert"
        self._setting_key = "buzzerOn"

    @property
    def is_on(self) -> bool | None:
        val = _setting_int(self.coordinator.data, self._setting_key)
        return bool(val) if val is not None else None

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.api.set_config_item(self._setting_key, "1")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.api.set_config_item(self._setting_key, "0")
        await self.coordinator.async_request_refresh()


class InverterEcoModeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch: toggle ECO mode on/off."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_translation_key = "eco_mode"
        self._attr_unique_id = f"{coordinator.api.device_sn}_eco_mode"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }
        self._attr_icon = "mdi:leaf"
        self._setting_key = "ecoMode"

    @property
    def is_on(self) -> bool | None:
        val = _setting_int(self.coordinator.data, self._setting_key)
        return bool(val) if val is not None else None

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.api.set_config_item(self._setting_key, "1")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.api.set_config_item(self._setting_key, "0")
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
