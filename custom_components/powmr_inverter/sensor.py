"""Sensor platform for Smart Solar Inverter.

Provides 15+ sensors for real-time inverter data, energy stats,
and computed values like corrected SOC, grid status, and economics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfMass,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PowMrCoordinator

_LOGGER = logging.getLogger(__name__)

WORKING_MODE_LABELS_UK: dict[str, str] = {
    "line mode": "Мережевий режим",
    "battery mode": "Режим АКБ",
    "standby mode": "Режим очікування",
    "fault mode": "Аварійний режим",
    "bypass mode": "Байпас",
    "charging mode": "Режим заряджання",
}


@dataclass(frozen=True, kw_only=True)
class PowMrSensorDescription(SensorEntityDescription):
    """Description for PowMr sensor entities."""

    value_fn: callable[[dict[str, Any]], StateType] | None = None
    attr_fn: callable[[dict[str, Any]], dict[str, Any]] | None = None


SENSORS: tuple[PowMrSensorDescription, ...] = (
    # ── Power sensors ──────────────────────────────────────────────
    PowMrSensorDescription(
        key="pv_power",
        translation_key="pv_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        icon="mdi:solar-power",
        value_fn=lambda d: d.get("pvPower"),
    ),
    PowMrSensorDescription(
        key="grid_power",
        translation_key="grid_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        icon="mdi:transmission-tower",
        value_fn=lambda d: d.get("gridPower"),
    ),
    PowMrSensorDescription(
        key="battery_power",
        translation_key="battery_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        icon="mdi:battery-charging",
        value_fn=lambda d: d.get("batteryPower"),
    ),
    PowMrSensorDescription(
        key="load_power",
        translation_key="load_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        icon="mdi:home-lightning-bolt",
        value_fn=lambda d: d.get("loadPower"),
    ),
    # ── Voltage sensors ────────────────────────────────────────────
    PowMrSensorDescription(
        key="pv_voltage",
        translation_key="pv_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        icon="mdi:solar-panel",
        value_fn=lambda d: d.get("pvVoltage"),
    ),
    PowMrSensorDescription(
        key="grid_voltage",
        translation_key="grid_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        icon="mdi:flash",
        value_fn=lambda d: d.get("gridVoltage"),
    ),
    PowMrSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        icon="mdi:battery",
        value_fn=lambda d: d.get("batteryVoltage"),
    ),
    # ── SOC sensors ────────────────────────────────────────────────
    PowMrSensorDescription(
        key="battery_soc",
        translation_key="battery_soc",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        icon="mdi:battery",
        value_fn=lambda d: d.get("batterySoc"),
    ),
    PowMrSensorDescription(
        key="battery_soc_corrected",
        translation_key="battery_soc_corrected",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        icon="mdi:battery-check",
        value_fn=lambda d: d.get("correctedSoc"),
        attr_fn=lambda d: {
            "reported_soc": d.get("batterySoc"),
            "correction_method": "LiFePO4 OCV + IR-drop compensation",
        },
    ),
    # Energy/CO2 are exposed by dedicated API-backed sensors below to avoid duplicates.
    # ── Other ──────────────────────────────────────────────────────
    PowMrSensorDescription(
        key="load_percentage",
        translation_key="load_percentage",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        icon="mdi:gauge",
        value_fn=lambda d: d.get("loadPercentage"),
    ),
    PowMrSensorDescription(
        key="working_mode",
        translation_key="working_mode",
        icon="mdi:cog",
        value_fn=lambda d: WORKING_MODE_LABELS_UK.get(
            str(d.get("workingMode", "")).strip().lower(),
            d.get("workingMode"),
        ),
    ),
    PowMrSensorDescription(
        key="pv_surplus",
        translation_key="pv_surplus",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        icon="mdi:flash-plus",
        value_fn=lambda d: max(
            0.0,
            (d.get("pvPower", 0.0) or 0.0)
            - (d.get("loadPower", 0.0) or 0.0),
        ),
    ),
    PowMrSensorDescription(
        key="battery_current",
        translation_key="battery_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        icon="mdi:current-dc",
        value_fn=lambda d: d.get("batteryCurrent", 0.0),
    ),
    # ── New metrics from latest_state API ───────────────────────────
    PowMrSensorDescription(
        key="ac_output_power",
        translation_key="ac_output_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        icon="mdi:power-plug",
        value_fn=lambda d: d.get("acOutputPower"),
    ),
    PowMrSensorDescription(
        key="feed_in_power",
        translation_key="feed_in_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        icon="mdi:transmission-tower-export",
        value_fn=lambda d: d.get("feedInPower"),
    ),
    PowMrSensorDescription(
        key="grid_import_power",
        translation_key="grid_import_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        icon="mdi:transmission-tower-import",
        value_fn=lambda d: d.get("gridImportPower") or d.get("gridPower"),
    ),
    PowMrSensorDescription(
        key="battery_charge_current",
        translation_key="battery_charge_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        icon="mdi:battery-plus",
        value_fn=lambda d: d.get("batteryChargeCurrent"),
    ),
    PowMrSensorDescription(
        key="battery_discharge_current",
        translation_key="battery_discharge_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        icon="mdi:battery-minus",
        value_fn=lambda d: d.get("batteryDischargeCurrent"),
    ),
    PowMrSensorDescription(
        key="inverter_temperature",
        translation_key="inverter_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        icon="mdi:thermometer",
        value_fn=lambda d: d.get("inverterTemperature"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PowMr sensors."""
    coordinator: PowMrCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[PowMrSensor] = []
    for desc in SENSORS:
        entities.append(PowMrSensor(coordinator, desc))

    # Add dynamic energy sensors from API
    entities.append(PowMrDailyEnergySensor(coordinator))
    entities.append(PowMrTotalEnergySensor(coordinator))
    entities.append(PowMrCO2Sensor(coordinator))

    async_add_entities(entities)


class PowMrSensor(CoordinatorEntity, SensorEntity):
    """Base sensor for PowMr inverter data."""

    entity_description: PowMrSensorDescription

    def __init__(
        self,
        coordinator: PowMrCoordinator,
        description: PowMrSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.api.device_sn}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        fn = self.entity_description.value_fn
        if fn is not None:
            return fn(self.coordinator.data)
        return self.coordinator.data.get(self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional attributes."""
        fn = self.entity_description.attr_fn
        if fn is not None and self.coordinator.data is not None:
            return fn(self.coordinator.data)
        return None


class PowMrDailyEnergySensor(PowMrSensor):
    """Sensor for daily PV energy from API (not from realtime data)."""

    def __init__(self, coordinator: PowMrCoordinator) -> None:
        super().__init__(
            coordinator,
            PowMrSensorDescription(
                key="daily_energy_api",
                translation_key="daily_energy",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                icon="mdi:solar-power-variant",
            ),
        )

    @property
    def native_value(self) -> float:
        return self.coordinator.api.daily_energy


class PowMrTotalEnergySensor(PowMrSensor):
    """Sensor for total PV energy from API."""

    def __init__(self, coordinator: PowMrCoordinator) -> None:
        super().__init__(
            coordinator,
            PowMrSensorDescription(
                key="total_energy_api",
                translation_key="total_energy",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                icon="mdi:solar-power-variant",
            ),
        )

    @property
    def native_value(self) -> float:
        return self.coordinator.api.total_energy


class PowMrCO2Sensor(PowMrSensor):
    """Sensor for CO2 savings."""

    def __init__(self, coordinator: PowMrCoordinator) -> None:
        super().__init__(
            coordinator,
            PowMrSensorDescription(
                key="co2_saved_api",
                translation_key="co2_saved",
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfMass.KILOGRAMS,
                icon="mdi:molecule-co2",
            ),
        )

    @property
    def native_value(self) -> float:
        return self.coordinator.api.co2_reduction
