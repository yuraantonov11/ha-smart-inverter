"""Number entities for Inverter Inverter — max charging currents."""

from __future__ import annotations

import logging

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import InverterCoordinator

_LOGGER = logging.getLogger(__name__)


def _normalize_amps(value: float, min_value: int, max_value: int, step: int) -> int:
    """Clamp and snap slider value to a stable step before sending to API."""
    clamped = max(min_value, min(max_value, int(round(value))))
    snapped = int(round(clamped / step) * step)
    return max(min_value, min(max_value, snapped))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Inverter number entities."""
    coordinator: InverterCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = [
        InverterMaxChargingCurrent(coordinator),
        InverterMaxUtilityChargingCurrent(coordinator),
        InverterBatteryChargeLimitPercent(coordinator),
        InverterBatteryDischargeLimitPercent(coordinator),
        InverterGridChargePowerLimit(coordinator),
    ]
    async_add_entities(entities)


class InverterMaxChargingCurrent(CoordinatorEntity, NumberEntity):
    """Number entity for max total charging current."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self.entity_description = NumberEntityDescription(
            key="max_charging_current",
            translation_key="max_charging_current",
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            mode=NumberMode.SLIDER,
            icon="mdi:current-dc",
        )
        self._attr_unique_id = f"{coordinator.api.device_sn}_max_charging_current"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }
        self._attr_native_min_value = 0
        self._attr_native_max_value = 200
        self._attr_native_step = 5
        self._attr_native_value = 60  # default

    async def async_set_native_value(self, value: float) -> None:
        amps = _normalize_amps(value, 0, 200, 5)
        ok = await self.coordinator.api.set_max_charging_current(amps)
        if ok:
            self._attr_native_value = float(amps)
            self.async_write_ha_state()


class InverterMaxUtilityChargingCurrent(CoordinatorEntity, NumberEntity):
    """Number entity for max utility (grid) charging current."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self.entity_description = NumberEntityDescription(
            key="max_utility_charging_current",
            translation_key="max_utility_charging_current",
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            mode=NumberMode.SLIDER,
            icon="mdi:current-dc",
        )
        self._attr_unique_id = f"{coordinator.api.device_sn}_max_utility_charging_current"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }
        self._attr_native_min_value = 0
        self._attr_native_max_value = 200
        self._attr_native_step = 5
        self._attr_native_value = 30  # default

    async def async_set_native_value(self, value: float) -> None:
        amps = _normalize_amps(value, 0, 200, 5)
        ok = await self.coordinator.api.set_max_utility_charging_current(amps)
        if ok:
            self._attr_native_value = float(amps)
            self.async_write_ha_state()


def _setting_number(coordinator_data: dict | None, key: str) -> float | None:
    """Extract a numeric setting value from coordinator deviceSettings."""
    if not coordinator_data:
        return None
    settings = coordinator_data.get("deviceSettings", {})
    item = settings.get(key, {})
    if isinstance(item, dict):
        val = item.get("value")
    else:
        val = item
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


class InverterBatteryChargeLimitPercent(CoordinatorEntity, NumberEntity):
    """Number: battery charge limit in percent (0-100)."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_translation_key = "battery_charge_limit"
        self._attr_unique_id = f"{coordinator.api.device_sn}_battery_charge_limit"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }
        self._attr_native_min_value = 10
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "%"
        self._attr_mode = NumberMode.SLIDER
        self._attr_icon = "mdi:battery-plus"
        self._setting_key = "batteryChargeLimit"

    @property
    def native_value(self) -> float | None:
        return _setting_number(self.coordinator.data, self._setting_key)

    async def async_set_native_value(self, value: float) -> None:
        v = int(value)
        ok = await self.coordinator.api.set_config_item(self._setting_key, str(v))
        if ok:
            self.async_write_ha_state()


class InverterBatteryDischargeLimitPercent(CoordinatorEntity, NumberEntity):
    """Number: battery discharge limit in percent (0-100)."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_translation_key = "battery_discharge_limit"
        self._attr_unique_id = f"{coordinator.api.device_sn}_battery_discharge_limit"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "%"
        self._attr_mode = NumberMode.SLIDER
        self._attr_icon = "mdi:battery-minus"
        self._setting_key = "batteryDischargeLimit"

    @property
    def native_value(self) -> float | None:
        return _setting_number(self.coordinator.data, self._setting_key)

    async def async_set_native_value(self, value: float) -> None:
        v = int(value)
        ok = await self.coordinator.api.set_config_item(self._setting_key, str(v))
        if ok:
            self.async_write_ha_state()


class InverterGridChargePowerLimit(CoordinatorEntity, NumberEntity):
    """Number: max grid charge power in watts (0-5000)."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_translation_key = "grid_charge_power_limit"
        self._attr_unique_id = f"{coordinator.api.device_sn}_grid_charge_power_limit"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }
        self._attr_native_min_value = 0
        self._attr_native_max_value = 5000
        self._attr_native_step = 50
        self._attr_native_unit_of_measurement = "W"
        self._attr_mode = NumberMode.SLIDER
        self._attr_icon = "mdi:flash"
        self._setting_key = "gridConnectedPowers"

    @property
    def native_value(self) -> float | None:
        return _setting_number(self.coordinator.data, self._setting_key)

    async def async_set_native_value(self, value: float) -> None:
        v = int(value)
        ok = await self.coordinator.api.set_config_item(self._setting_key, str(v))
        if ok:
            self.async_write_ha_state()
