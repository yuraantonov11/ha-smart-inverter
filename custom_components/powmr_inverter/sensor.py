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
from .coordinator import InverterCoordinator, HistoryCoordinator

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
class InverterSensorDescription(SensorEntityDescription):
    """Description for Inverter sensor entities."""

    value_fn: callable[[dict[str, Any]], StateType] | None = None
    attr_fn: callable[[dict[str, Any]], dict[str, Any]] | None = None


SENSORS: tuple[InverterSensorDescription, ...] = (
    # ── Power sensors ──────────────────────────────────────────────
    InverterSensorDescription(
        key="pv_power",
        translation_key="pv_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        icon="mdi:solar-power",
        value_fn=lambda d: d.get("pvPower"),
    ),
    InverterSensorDescription(
        key="grid_power",
        translation_key="grid_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        icon="mdi:transmission-tower",
        value_fn=lambda d: d.get("gridPower"),
    ),
    InverterSensorDescription(
        key="battery_power",
        translation_key="battery_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        icon="mdi:battery-charging",
        value_fn=lambda d: d.get("batteryPower"),
    ),
    InverterSensorDescription(
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
    InverterSensorDescription(
        key="pv_voltage",
        translation_key="pv_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        icon="mdi:solar-panel",
        value_fn=lambda d: d.get("pvVoltage"),
    ),
    InverterSensorDescription(
        key="grid_voltage",
        translation_key="grid_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        icon="mdi:flash",
        value_fn=lambda d: d.get("gridVoltage"),
    ),
    InverterSensorDescription(
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
    InverterSensorDescription(
        key="battery_soc",
        translation_key="battery_soc",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        icon="mdi:battery",
        value_fn=lambda d: d.get("batterySoc"),
    ),
    InverterSensorDescription(
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
    InverterSensorDescription(
        key="load_percentage",
        translation_key="load_percentage",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        icon="mdi:gauge",
        value_fn=lambda d: d.get("loadPercentage"),
    ),
    InverterSensorDescription(
        key="working_mode",
        translation_key="working_mode",
        icon="mdi:cog",
        value_fn=lambda d: WORKING_MODE_LABELS_UK.get(
            str(d.get("workingMode", "")).strip().lower(),
            d.get("workingMode"),
        ),
    ),
    InverterSensorDescription(
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
    InverterSensorDescription(
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
    InverterSensorDescription(
        key="ac_output_power",
        translation_key="ac_output_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        icon="mdi:power-plug",
        value_fn=lambda d: d.get("acOutputPower"),
    ),
    InverterSensorDescription(
        key="feed_in_power",
        translation_key="feed_in_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        icon="mdi:transmission-tower-export",
        value_fn=lambda d: d.get("feedInPower"),
    ),
    InverterSensorDescription(
        key="grid_import_power",
        translation_key="grid_import_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        icon="mdi:transmission-tower-import",
        value_fn=lambda d: d.get("gridImportPower") or d.get("gridPower"),
    ),
    InverterSensorDescription(
        key="battery_charge_current",
        translation_key="battery_charge_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        icon="mdi:battery-plus",
        value_fn=lambda d: d.get("batteryChargeCurrent"),
    ),
    InverterSensorDescription(
        key="battery_discharge_current",
        translation_key="battery_discharge_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=1,
        icon="mdi:battery-minus",
        value_fn=lambda d: d.get("batteryDischargeCurrent"),
    ),
    InverterSensorDescription(
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
    """Set up Inverter sensors."""
    coordinator: InverterCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[InverterSensor] = []
    for desc in SENSORS:
        entities.append(InverterSensor(coordinator, desc))

    # Add dynamic energy sensors from API
    entities.append(InverterDailyEnergySensor(coordinator))
    entities.append(InverterTotalEnergySensor(coordinator))
    entities.append(InverterCO2Sensor(coordinator))

    # Add forecast & economics sensors
    entities.append(ForecastTomorrowSensor(coordinator))
    entities.append(ForecastDayAfterSensor(coordinator))
    entities.append(LearnedRatioSensor(coordinator))
    entities.append(DailySavingsSensor(coordinator))
    entities.append(MonthlySavingsSensor(coordinator))
    # HEMS diagnostics
    entities.append(HemsReasonSensor(coordinator))
    entities.append(HemsOutputCmdSensor(coordinator))
    entities.append(HemsChargerCmdSensor(coordinator))

    # ── History chart sensors (separate coordinator, 15-min polling) ──
    history_coordinator: HistoryCoordinator | None = hass.data[DOMAIN].get(
        entry.entry_id, {}
    ).get("history_coordinator")
    if history_coordinator is not None:
        entities.append(DailyPowerHistorySensor(history_coordinator))
        entities.append(MonthlyEnergyHistorySensor(history_coordinator))
        entities.append(YearlyEnergyHistorySensor(history_coordinator))
        entities.append(TotalEnergyHistorySensor(history_coordinator))

    async_add_entities(entities)


class InverterSensor(CoordinatorEntity, SensorEntity):
    """Base sensor for Inverter inverter data."""

    entity_description: InverterSensorDescription

    def __init__(
        self,
        coordinator: InverterCoordinator,
        description: InverterSensorDescription,
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


class InverterDailyEnergySensor(InverterSensor):
    """Sensor for daily PV energy from API (not from realtime data)."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            InverterSensorDescription(
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


class InverterTotalEnergySensor(InverterSensor):
    """Sensor for total PV energy from API."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            InverterSensorDescription(
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


class InverterCO2Sensor(InverterSensor):
    """Sensor for CO2 savings."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            InverterSensorDescription(
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


class ForecastTomorrowSensor(InverterSensor):
    """Sensor: forecasted PV energy for tomorrow (kWh)."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            InverterSensorDescription(
                key="forecast_tomorrow",
                translation_key="forecast_tomorrow",
                device_class=None,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                icon="mdi:solar-power-variant",
            ),
        )

    @property
    def native_value(self) -> float | None:
        return self.coordinator.forecast_tomorrow_kwh

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose hourly forecast for sparkline rendering."""
        hourly = self.coordinator.hourly_forecast_today
        if not hourly:
            return None
        return {
            "hourly_forecast_w": hourly,
            "peak_power_w": max(hourly) if hourly else 0,
            "total_kwh": round(sum(hourly) / 1000.0, 2),
        }


class ForecastDayAfterSensor(InverterSensor):
    """Sensor: forecasted PV energy for day after tomorrow (kWh)."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            InverterSensorDescription(
                key="forecast_day_after",
                translation_key="forecast_day_after",
                device_class=None,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                icon="mdi:solar-power-variant",
            ),
        )

    @property
    def native_value(self) -> float | None:
        return self.coordinator.forecast_day_after_kwh


class LearnedRatioSensor(InverterSensor):
    """Sensor: self-learned PV conversion ratio (W per W/m²)."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            InverterSensorDescription(
                key="learned_ratio",
                translation_key="learned_ratio",
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:brain",
            ),
        )

    @property
    def native_value(self) -> float:
        return round(self.coordinator.forecast_learned_ratio, 4)


class DailySavingsSensor(InverterSensor):
    """Sensor: estimated savings today (UAH) from battery usage."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            InverterSensorDescription(
                key="daily_savings",
                translation_key="daily_savings",
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement="UAH",
                icon="mdi:cash-check",
            ),
        )

    @property
    def native_value(self) -> float:
        return self.coordinator.daily_savings_uah


class MonthlySavingsSensor(InverterSensor):
    """Sensor: estimated savings this month (UAH) from battery usage."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            InverterSensorDescription(
                key="monthly_savings",
                translation_key="monthly_savings",
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement="UAH",
                icon="mdi:cash-multiple",
            ),
        )

    @property
    def native_value(self) -> float:
        return self.coordinator.monthly_savings_uah


class HemsReasonSensor(InverterSensor):
    """Sensor: last HEMS engine decision reason."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            InverterSensorDescription(
                key="hems_last_reason",
                translation_key="hems_last_reason",
                icon="mdi:brain",
            ),
        )

    @property
    def native_value(self) -> str | None:
        return self.coordinator.hems_last_reason or None


class HemsOutputCmdSensor(InverterSensor):
    """Sensor: last HEMS output priority command."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            InverterSensorDescription(
                key="hems_last_output_cmd",
                translation_key="hems_last_output_cmd",
                icon="mdi:export",
            ),
        )

    @property
    def native_value(self) -> str | None:
        return self.coordinator.hems_last_output_cmd or None


class HemsChargerCmdSensor(InverterSensor):
    """Sensor: last HEMS charger priority command."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            InverterSensorDescription(
                key="hems_last_charger_cmd",
                translation_key="hems_last_charger_cmd",
                icon="mdi:battery-charging",
            ),
        )

    @property
    def native_value(self) -> str | None:
        return self.coordinator.hems_last_charger_cmd or None


# ═══════════════════════════════════════════════════════════════════════
# HISTORY CHART SENSORS (use HistoryCoordinator, 15-min polling)
# ═══════════════════════════════════════════════════════════════════════


class DailyPowerHistorySensor(CoordinatorEntity, SensorEntity):
    """Sensor for today's hourly PV power curve (24 data points).

    Exposes hourly_power and hourly_labels as attributes for charting.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "history_daily_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_icon = "mdi:chart-line"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: HistoryCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.api.device_sn}_history_daily_power"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }

    @staticmethod
    def _extract_power(record: dict) -> float:
        """Extract power value from API record, trying common key names."""
        for key in ("pvPower", "value", "power", "pvInputPower", "generationPower"):
            v = record.get(key)
            if v is not None:
                try:
                    return float(v)
                except (ValueError, TypeError):
                    continue
        return 0.0

    @staticmethod
    def _extract_label(record: dict) -> str:
        """Extract time label from API record."""
        for key in ("time", "timestamp", "label", "x", "name", "date"):
            v = record.get(key)
            if v is not None:
                return str(v)
        return ""

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        hourly = self.coordinator.data.get("today_hourly_power", [])
        if not hourly:
            return None
        for point in reversed(hourly):
            pw = self._extract_power(point)
            if pw > 0:
                return round(pw / 1000, 2)
        return 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None:
            return None
        hourly = self.coordinator.data.get("today_hourly_power", [])
        if not hourly:
            return None
        labels = [self._extract_label(p) for p in hourly]
        values_kw = [round(self._extract_power(p) / 1000, 3) for p in hourly]
        return {
            "hourly_power_kw": values_kw,
            "hourly_labels": labels,
            "total_today_kwh": round(sum(values_kw), 2),
            "last_updated": self.coordinator.data.get("last_updated"),
            "_raw_sample": hourly[:3] if len(hourly) > 3 else hourly,
        }


class MonthlyEnergyHistorySensor(CoordinatorEntity, SensorEntity):
    """Sensor for current month's daily PV energy (up to 31 data points).

    Exposes daily_energy and daily_labels as attributes for charting.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "history_monthly_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:chart-bar"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: HistoryCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.api.device_sn}_history_monthly_energy"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }

    @staticmethod
    def _extract_energy(record: dict) -> float:
        """Extract energy value from API record."""
        for key in ("pvEnergy", "value", "energy", "pvGenerated", "y"):
            v = record.get(key)
            if v is not None:
                try:
                    return float(v)
                except (ValueError, TypeError):
                    continue
        return 0.0

    @staticmethod
    def _extract_label(record: dict) -> str:
        for key in ("time", "timestamp", "label", "x", "name", "date"):
            v = record.get(key)
            if v is not None:
                return str(v)
        return ""

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        daily = self.coordinator.data.get("monthly_daily_energy", [])
        if not daily:
            return None
        return round(sum(self._extract_energy(p) for p in daily), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None:
            return None
        daily = self.coordinator.data.get("monthly_daily_energy", [])
        if not daily:
            return None
        labels = [self._extract_label(p) for p in daily]
        values = [round(self._extract_energy(p), 3) for p in daily]
        return {
            "daily_energy_kwh": values,
            "daily_labels": labels,
            "total_month_kwh": round(sum(values), 2),
            "last_updated": self.coordinator.data.get("last_updated"),
            "_raw_sample": daily[:3] if len(daily) > 3 else daily,
        }


class YearlyEnergyHistorySensor(CoordinatorEntity, SensorEntity):
    """Sensor for current year's monthly PV energy (12 data points).

    Exposes monthly_energy and monthly_labels as attributes for charting.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "history_yearly_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:chart-bar-stacked"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: HistoryCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.api.device_sn}_history_yearly_energy"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }

    @staticmethod
    def _extract_energy(record: dict) -> float:
        for key in ("pvEnergy", "value", "energy", "pvGenerated", "y"):
            v = record.get(key)
            if v is not None:
                try:
                    return float(v)
                except (ValueError, TypeError):
                    continue
        return 0.0

    @staticmethod
    def _extract_label(record: dict) -> str:
        for key in ("time", "timestamp", "label", "x", "name", "date"):
            v = record.get(key)
            if v is not None:
                return str(v)
        return ""

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        monthly = self.coordinator.data.get("yearly_monthly_energy", [])
        if not monthly:
            return None
        return round(sum(self._extract_energy(p) for p in monthly), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None:
            return None
        monthly = self.coordinator.data.get("yearly_monthly_energy", [])
        if not monthly:
            return None
        labels = [self._extract_label(p) for p in monthly]
        values = [round(self._extract_energy(p), 3) for p in monthly]
        return {
            "monthly_energy_kwh": values,
            "monthly_labels": labels,
            "total_year_kwh": round(sum(values), 2),
            "last_updated": self.coordinator.data.get("last_updated"),
            "_raw_sample": monthly[:3] if len(monthly) > 3 else monthly,
        }


class TotalEnergyHistorySensor(CoordinatorEntity, SensorEntity):
    """Sensor for total cumulative PV energy.

    Uses the API's total energy stat (from device list) and exposes
    cumulative energy data for dashboard visualization.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "history_total_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:solar-power-variant"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: HistoryCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.api.device_sn}_history_total_energy"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        total = self.coordinator.data.get("total_energy_kwh", 0.0)
        return total if total > 0 else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.data is None:
            return None
        return {
            "total_energy_kwh": self.coordinator.data.get("total_energy_kwh", 0.0),
            "daily_energy_kwh": self.coordinator.api.daily_energy,
            "yearly_energy_kwh": round(
                sum(
                    next(
                        (float(v) for k, v in p.items() if k in ("pvEnergy", "value", "energy") and v is not None),
                        0.0,
                    )
                    for p in self.coordinator.data.get("yearly_monthly_energy", [])
                ), 2
            ),
            "last_updated": self.coordinator.data.get("last_updated"),
        }
