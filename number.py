"""Number entities for Smart Solar Inverter — charging currents, voltages, and limits."""

from __future__ import annotations

import logging

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfElectricPotential
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
        # New voltage entities
        InverterBulkChargingVoltage(coordinator),
        InverterFloatChargingVoltage(coordinator),
        InverterLowBatteryCutoffVoltage(coordinator),
        InverterEqualizationVoltage(coordinator),
        InverterEqualizationTime(coordinator),
        InverterEqualizationInterval(coordinator),
        InverterSbuReturnToGridVoltage(coordinator),
        InverterSbuReturnToBatteryVoltage(coordinator),
        InverterGridTieCurrent(coordinator),
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
            mode=NumberMode.BOX,
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
            mode=NumberMode.BOX,
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


class InverterBatteryChargeLimitPercent(CoordinatorEntity, NumberEntity):
    """Number: max battery charging current in Amperes (API: setMaxChargingCurrent).

    The API exposes this as max current in Amps, NOT a percent. Values are
    typically 10-100A depending on inverter model. The translation_key was
    previously misleading.
    """

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_translation_key = "max_charging_current"
        self._attr_unique_id = f"{coordinator.api.device_sn}_max_charging_current"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }
        self._attr_native_min_value = 10
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "A"
        self._attr_mode = NumberMode.BOX
        self._attr_icon = "mdi:battery-plus"
        self._setting_key = "setMaxChargingCurrent"

    @property
    def native_value(self) -> float | None:
        return _setting_number(self.coordinator.data, self._setting_key)

    async def async_set_native_value(self, value: float) -> None:
        v = int(value)
        ok = await self.coordinator.api.set_config_item(self._setting_key, str(v))
        if ok:
            self.async_write_ha_state()


class InverterBatteryDischargeLimitPercent(CoordinatorEntity, NumberEntity):
    """Number: low-battery cutoff voltage (API: LowBatteryCutOffVoltageSetting).

    The API does NOT expose a discharge-limit percent. It exposes the
    low-battery cutoff voltage, which determines when the inverter stops
    drawing from the battery. Range typically 40-52V for 48V systems.
    """

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_translation_key = "low_battery_cutoff_voltage"
        self._attr_unique_id = f"{coordinator.api.device_sn}_low_battery_cutoff_voltage"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }
        self._attr_native_min_value = 40
        self._attr_native_max_value = 54
        self._attr_native_step = 0.1
        self._attr_native_unit_of_measurement = "V"
        self._attr_mode = NumberMode.BOX
        self._attr_icon = "mdi:battery-minus"
        self._setting_key = "LowBatteryCutOffVoltageSetting"

    @property
    def native_value(self) -> float | None:
        return _setting_number(self.coordinator.data, self._setting_key)

    async def async_set_native_value(self, value: float) -> None:
        v = int(value)
        ok = await self.coordinator.api.set_config_item(self._setting_key, str(v))
        if ok:
            self.async_write_ha_state()


class InverterGridChargePowerLimit(CoordinatorEntity, NumberEntity):
    """Number: battery power limiting setting in Watts (API: batteryPowerLimitingSetting).

    This is the inverter own battery-power cap, NOT a generic grid-charge
    power limit. Range and unit are taken from the inverter firmware.
    """

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_translation_key = "battery_power_limit"
        self._attr_unique_id = f"{coordinator.api.device_sn}_battery_power_limit"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }
        self._attr_native_min_value = 0
        self._attr_native_max_value = 5000
        self._attr_native_step = 50
        self._attr_native_unit_of_measurement = "W"
        self._attr_mode = NumberMode.BOX
        self._attr_icon = "mdi:flash"
        self._setting_key = "batteryPowerLimitingSetting"

    @property
    def native_value(self) -> float | None:
        return _setting_number(self.coordinator.data, self._setting_key)

    async def async_set_native_value(self, value: float) -> None:
        v = int(value)
        ok = await self.coordinator.api.set_config_item(self._setting_key, str(v))
        if ok:
            self.async_write_ha_state()


# ── New voltage / time entities ────────────────────────────────────────────


class _InverterConfigNumber(CoordinatorEntity, NumberEntity):
    """Generic number entity backed by a deviceSettings config key."""

    _setting_key: str = ""
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{coordinator.api.device_sn}_{self._setting_key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }

    @property
    def native_value(self) -> float | None:
        return _setting_number(self.coordinator.data, self._setting_key)

    async def async_set_native_value(self, value: float) -> None:
        ok = await self.coordinator.api.set_config_item(
            self._setting_key, str(value)
        )
        if ok:
            self.async_write_ha_state()


class InverterBulkChargingVoltage(_InverterConfigNumber):
    """Number: bulk (absorption) charging voltage."""

    _setting_key = "setBatteryCVChargeVoltage"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "bulk_charging_voltage"
        self._attr_native_min_value = 40.0
        self._attr_native_max_value = 60.0
        self._attr_native_step = 0.1
        self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
        self._attr_icon = "mdi:battery-charging"


class InverterFloatChargingVoltage(_InverterConfigNumber):
    """Number: float charging voltage."""

    _setting_key = "setBatteryFloatChargingVoltage"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "float_charging_voltage"
        self._attr_native_min_value = 40.0
        self._attr_native_max_value = 60.0
        self._attr_native_step = 0.1
        self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
        self._attr_icon = "mdi:battery-heart"


class InverterLowBatteryCutoffVoltage(_InverterConfigNumber):
    """Number: low battery cutoff voltage."""

    _setting_key = "LowBatteryCutOffVoltageSetting"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "low_low_battery_cutoff_voltage"
        self._attr_native_min_value = 30.0
        self._attr_native_max_value = 56.0
        self._attr_native_step = 0.1
        self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
        self._attr_icon = "mdi:battery-alert"


class InverterEqualizationVoltage(_InverterConfigNumber):
    """Number: battery equalization voltage."""

    _setting_key = "setBatteryEqualizationVoltage"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "equalization_voltage"
        self._attr_native_min_value = 40.0
        self._attr_native_max_value = 60.0
        self._attr_native_step = 0.1
        self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
        self._attr_icon = "mdi:battery-sync"


class InverterEqualizationTime(_InverterConfigNumber):
    """Number: battery equalization time in minutes."""

    _setting_key = "setBatteryEqualizationTime"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "equalization_time"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 240
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "min"
        self._attr_icon = "mdi:timer"


class InverterEqualizationInterval(_InverterConfigNumber):
    """Number: battery equalization interval in days."""

    _setting_key = "batteryEqualizationIntervalSetting"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "equalization_interval"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 90
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = "day"
        self._attr_icon = "mdi:calendar-clock"


class InverterSbuReturnToGridVoltage(_InverterConfigNumber):
    """Number: SBU priority — voltage to switch back to grid mode."""

    _setting_key = "comebackUtilityModeVolSBUPriority"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "sbu_return_to_grid_voltage"
        self._attr_native_min_value = 30.0
        self._attr_native_max_value = 56.0
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
        self._attr_icon = "mdi:transmission-tower"


class InverterSbuReturnToBatteryVoltage(_InverterConfigNumber):
    """Number: SBU priority — voltage to switch back to battery mode."""

    _setting_key = "comebackBatteryModeVolSBUPriority"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "sbu_return_to_battery_voltage"
        self._attr_native_min_value = 30.0
        self._attr_native_max_value = 56.0
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
        self._attr_icon = "mdi:battery-arrow-up"


class InverterGridTieCurrent(_InverterConfigNumber):
    """Number: grid-tie current limit in amps."""

    _setting_key = "outputDelaySetting"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = "grid_tie_current"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 50
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
        self._attr_icon = "mdi:current-ac"
