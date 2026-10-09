"""Sensor platform for Smart Solar Inverter.

Provides 15+ sensors for real-time inverter data, energy stats,
and computed values like corrected SOC, grid status, and economics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
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
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfMass,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

# WMO weather codes → (HA weather state, emoji, Ukrainian label)
# Used to translate the Open-Meteo weather_code values into icons and
# labels for the forecast card and the weather_condition attribute.
WMO_WEATHER_MAP = {
    0: ("sunny", "☀️", "Ясно"),
    1: ("partlycloudy", "🌤️", "Переважно ясно"),
    2: ("partlycloudy", "⛅", "Хмарно з проясненнями"),
    3: ("cloudy", "☁️", "Хмарно"),
    45: ("fog", "🌫️", "Туман"),
    48: ("fog", "🌫️", "Паморозний туман"),
    51: ("rainy", "🌧️", "Легка мрипа"),
    53: ("rainy", "🌧️", "Мрипа"),
    55: ("rainy", "🌧️", "Сильна мрипа"),
    61: ("rainy", "🌧️", "Легкий дощ"),
    63: ("rainy", "🌧️", "Дощ"),
    65: ("pouring", "🌧️", "Сильний дощ"),
    71: ("snowy", "🌨️", "Легкий сніг"),
    73: ("snowy", "🌨️", "Сніг"),
    75: ("snowy", "❄️", "Сильний сніг"),
    77: ("snowy", "❄️", "Снігова крупа"),
    80: ("rainy", "🌦️", "Короткочасний дощ"),
    81: ("rainy", "🌦️", "Злива"),
    82: ("pouring", "🌧️", "Сильна злива"),
    95: ("lightning", "⛈️", "Гроза"),
    96: ("lightning-rainy", "⛈️", "Гроза з градом"),
    99: ("lightning-rainy", "⛈️", "Сильна гроза з градом"),
}


from .const import DOMAIN
from .coordinator import InverterCoordinator, HistoryCoordinator
from .hems import debug_logging

# Audit T20: freshness helpers
# live in ``hems.energy_freshness``
# so the sensor does not invent
# its own midnight-reset logic.
# ``hems.energy_freshness`` is a
# pure-stdlib module - no Home
# Assistant imports.
from .hems.energy_freshness import (
    compute_daily_energy_freshness,
    daily_energy_for_today,
)

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
        suggested_display_precision=0,
        icon="mdi:thermometer",
        value_fn=lambda d: d.get("inverterTemperature"),
    ),
    # Inverter temperature uses the API field ntcMaximumTemperature (NTC sensor
    # on the inverter heatsink), which is the only temperature value the
    # POWMR inverter actually reports in latest_state. Earlier radiatorTemperature
    # / invTemperature fallbacks were never returned by this firmware.

    # ── Nominal / rated inverter specs (from API) ─────────────────────
    InverterSensorDescription(
        key="nominal_ac_voltage",
        translation_key="nominal_ac_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=0,
        icon="mdi:flash-triangle-outline",
        value_fn=lambda d: d.get("nominalAcVoltage"),
    ),
    InverterSensorDescription(
        key="nominal_ac_current",
        translation_key="nominal_ac_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=0,
        icon="mdi:current-ac",
        value_fn=lambda d: d.get("nominalAcCurrent"),
    ),
    InverterSensorDescription(
        key="rated_active_power",
        translation_key="rated_active_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        icon="mdi:lightning-bolt",
        value_fn=lambda d: d.get("ratedActivePower"),
    ),
    InverterSensorDescription(
        key="rated_apparent_power",
        translation_key="rated_apparent_power",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        suggested_display_precision=0,
        icon="mdi:lightning-bolt-outline",
        value_fn=lambda d: d.get("acOutputRatingApparentPower"),
    ),
    InverterSensorDescription(
        key="output_apparent_power",
        translation_key="output_apparent_power",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        suggested_display_precision=0,
        icon="mdi:sine-wave",
        value_fn=lambda d: d.get("outputApparentPower"),
    ),
    InverterSensorDescription(
        key="output_frequency",
        translation_key="output_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        suggested_display_precision=1,
        icon="mdi:sine-wave",
        value_fn=lambda d: d.get("outputFrequency"),
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
    entities.append(WeatherYesterdaySensor(coordinator))
    entities.append(WeatherTomorrowSensor(coordinator))
    entities.append(LearnedRatioSensor(coordinator))
    entities.append(DailySavingsSensor(coordinator))
    entities.append(MonthlySavingsSensor(coordinator))
    # HEMS diagnostics
    entities.append(HemsReasonSensor(coordinator))
    entities.append(HemsOutputCmdSensor(coordinator))
    entities.append(HemsChargerCmdSensor(coordinator))
    # HEMS observability: daily counters + last-20 timeline
    # HEMS observability sensors removed — HA 2026.9 beta refuses to register SensorEntity classes with unknown translation_keys. Use /config/powmr_hems_debug.log directly (see dashboard panel).

    # ── Predictive ML sensors ─────────────────────────────────
    entities.append(PredictiveHintSensor(coordinator, entry))
    entities.append(PredictiveDayAheadSensor(coordinator, entry))
    entities.append(PredictiveDecisionStateSensor(coordinator, entry))

    # ── History chart sensors (separate coordinator, 15-min polling) ──
    history_coordinator: HistoryCoordinator | None = hass.data[DOMAIN].get(
        entry.entry_id, {}
    ).get("history_coordinator")
    if history_coordinator is not None:
        entities.append(DailyPowerHistorySensor(history_coordinator))
        entities.append(PvGenerationCurveSensor(history_coordinator, coordinator))
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
    """Sensor for daily PV energy from API (not from realtime data).

    Audit T20: the sensor must honour three
    freshness rules:

      1. **Midnight reset**: when the API
         still reports yesterday's value
         (the inverter's daily counter
         rolled back to zero at 00:00 but
         the API response is cached), the
         sensor must report ``0.0`` for
         today rather than yesterday's
         final reading.
      2. **Never refreshed**: when the API
         has never refreshed the value,
         the sensor must report ``None``
         (HA surfaces ``unknown``). The
         audit forbids substituting a
         measured value with a forecast.
      3. **Stale**: when the API value is
         older than the freshness threshold
         (default 6 hours), the sensor
         exposes ``daily_energy_stale=True``
         in ``extra_state_attributes`` so a
         consumer can warn the user.

    The freshness logic itself lives in
    ``hems.energy_freshness`` so the API
    client, the sensor, and the regression
    tests share one source of truth.
    """

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
    def native_value(self):
        api = self.coordinator.api
        # The API must publish
        # ``daily_energy_at`` and
        # ``daily_energy_date``. If the
        # coordinator has not yet wired
        # those attributes (older
        # integration), we report ``None``
        # so HA surfaces ``unknown``
        # rather than a stale number.
        daily_energy_at = getattr(api, "daily_energy_at", None)
        daily_energy_date = getattr(api, "daily_energy_date", None)
        # Audit T20 follow-up (timezone):
        # we use the HA site timezone for
        # both ``now`` and ``today``. The
        # previous version used UTC for
        # ``now`` and ``today``, which
        # broke the midnight-reset rule
        # for users east of UTC (the audit
        # repro was 6 October 00:01 Kyiv:
        # the sensor saw ``daily_energy_date``
        # dated 5 October from the API but
        # ``today`` was 6 October UTC -
        # the comparison passed and the
        # sensor reported yesterday's
        # 18.5 kWh as today's reading).
        # ``_site_tz_offset_minutes`` is
        # exposed by the coordinator as
        # ``entry.options.get("site_tz_offset_minutes")``
        # or computed from
        # ``dt_util.as_local(now).utcoffset()``.
        # ``None`` means "use UTC".
        site_tz = getattr(
            self.coordinator.api, "_site_tz", None
        )
        now = datetime.now(tz=site_tz or timezone.utc)
        freshness = compute_daily_energy_freshness(
            daily_energy_at=daily_energy_at,
            daily_energy_date=daily_energy_date,
            now=now,
        )
        try:
            raw = float(api.daily_energy)
        except (TypeError, ValueError, AttributeError):
            raw = 0.0
        try:
            return daily_energy_for_today(
                value=raw,
                freshness=freshness,
                today=now.date(),
            )
        except Exception:
            # Defensive: never let the
            # helper crash the sensor.
            return None

    @property
    def extra_state_attributes(self):
        base = super().extra_state_attributes
        if base is None:
            base = {}
        api = self.coordinator.api
        daily_energy_at = getattr(api, "daily_energy_at", None)
        daily_energy_date = getattr(api, "daily_energy_date", None)
        # Audit T20 follow-up (timezone):
        # use the HA site timezone so
        # the freshness triple and the
        # ``today`` comparison both
        # see the same wall clock.
        # ``_site_tz`` is set by the
        # coordinator at init time;
        # ``None`` falls back to UTC
        # for standalone / unit tests.
        site_tz = getattr(api, "_site_tz", None)
        now = datetime.now(tz=site_tz or timezone.utc)
        freshness = compute_daily_energy_freshness(
            daily_energy_at=daily_energy_at,
            daily_energy_date=daily_energy_date,
            now=now,
        )
        # Audit T20.4: multi-device
        # warning. The production API
        # client picks ``devices[0]``
        # without warning the user; we
        # surface a flag here so the
        # consumer can decide whether to
        # raise a repair.
        device_count = getattr(api, "_account_device_count", 1)
        if device_count is None:
            device_count = 1
        attrs = dict(freshness.as_dict())
        attrs["multi_device_warning"] = device_count > 1
        base.update(attrs)
        return base


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


class WeatherYesterdaySensor(InverterSensor):
    """Sensor: weather summary emoji for yesterday.

    Pulls the WMO weather_code for yesterday from the coordinator's
    daily_historical_weather dict (populated by Open-Meteo Archive API)
    and exposes the corresponding emoji as native_value plus a label.
    """

    _attr_icon = "mdi:weather-sunny"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            InverterSensorDescription(
                key="weather_yesterday",
                translation_key="weather_yesterday",
                device_class=None,
                state_class=None,
                native_unit_of_measurement=None,
                icon="mdi:weather-sunny",
            ),
        )

    @property
    def native_value(self) -> str:
        from datetime import datetime, timedelta
        daily = getattr(self.coordinator, "daily_historical_weather", {}) or {}
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
        code = daily.get(yesterday)
        if code is None:
            return "❓"  # question mark
        return WMO_WEATHER_MAP.get(int(code), ("unknown", "❓", "Невідомо"))[1]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        from datetime import datetime, timedelta
        daily = getattr(self.coordinator, "daily_historical_weather", {}) or {}
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
        code = daily.get(yesterday)
        if code is None:
            return None
        ha_state, emoji, label = WMO_WEATHER_MAP.get(int(code), ("unknown", "?", "?"))
        return {
            "date": yesterday,
            "code": int(code),
            "ha_state": ha_state,
            "label_uk": label,
        }


class WeatherTomorrowSensor(InverterSensor):
    """Sensor: weather summary emoji for tomorrow.

    Reads the forecast_tomorrow attribute exposed by ForecastTomorrowSensor
    (a {code, emoji, label_uk, ha_state} dict built by the coordinator).
    """

    _attr_icon = "mdi:weather-sunny"

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            InverterSensorDescription(
                key="weather_tomorrow",
                translation_key="weather_tomorrow",
                device_class=None,
                state_class=None,
                native_unit_of_measurement=None,
                icon="mdi:weather-sunny",
            ),
        )

    @property
    def native_value(self) -> str:
        # Find ForecastTomorrowSensor and read its attributes.
        for s in getattr(self, "platform", None) and self.platform.entities.values() or []:
            key = getattr(getattr(s, "entity_description", None), "key", "")
            if key == "forecast_tomorrow":
                attrs = s.extra_state_attributes or {}
                ft = attrs.get("forecast_tomorrow") or {}
                if isinstance(ft, dict) and ft.get("emoji"):
                    return ft["emoji"]
                break
        return "❓"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        for s in getattr(self, "platform", None) and self.platform.entities.values() or []:
            key = getattr(getattr(s, "entity_description", None), "key", "")
            if key == "forecast_tomorrow":
                attrs = s.extra_state_attributes or {}
                ft = attrs.get("forecast_tomorrow") or {}
                if isinstance(ft, dict):
                    return {
                        "code": ft.get("code"),
                        "ha_state": ft.get("ha_state"),
                        "label_uk": ft.get("label_uk"),
                    }
                break
        return None


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
        # Weather conditions for each hour (WMO codes)
        weather = list(getattr(self.coordinator, "hourly_weather_today", []) or [])
        # Solar radiation in W/m² for each hour (drives the forecast)
        radiation = list(getattr(self.coordinator, "hourly_radiation_today", []) or [])
        # Pad to 24
        while len(weather) < 24:
            weather.append(None)
        while len(radiation) < 24:
            radiation.append(0)

        # Determine dominant weather. hourly_weather_today stores raw
        # WMO weather codes (ints) — sometimes dicts if the coordinator
        # was updated. Normalize both shapes to a raw int via .get().
        tomorrow_code = getattr(self.coordinator, "weather_tomorrow_code", None)
        today_code = None
        from collections import Counter
        valid_codes = []
        for c in weather:
            if c is None:
                continue
            if isinstance(c, dict):
                code = c.get("code")
                if code is not None:
                    valid_codes.append(code)
            elif isinstance(c, int):
                valid_codes.append(c)
        if valid_codes:
            today_code = Counter(valid_codes).most_common(1)[0][0]

        # Find dominant hour-by-hour weather
        hourly_weather = []
        for c in weather[:24]:
            if c is None:
                hourly_weather.append(None)
            else:
                info = WMO_WEATHER_MAP.get(c, ("unknown", "❓", "?"))
                hourly_weather.append({
                    "code": c,
                    "ha_state": info[0],
                    "emoji": info[1],
                    "label_uk": info[2],
                })

        return {
            "hourly_forecast_w": hourly,
            "hourly_forecast_date": self.coordinator._pv_local_now().date().isoformat(),
            "hourly_forecast_basis": "hourly_mean_power",
            "hourly_response": getattr(getattr(self.coordinator, "_forecast", None), "hourly_response", None),
            "hourly_radiation_wm2": radiation[:24],
            "peak_radiation_wm2": max(radiation) if radiation else 0,
            "peak_power_w": max(hourly) if hourly else 0,
            "total_kwh": round(sum(hourly) / 1000.0, 2),
            "hourly_weather": hourly_weather,
            "dominant_today": {
                "code": today_code,
                **{
                    "ha_state": WMO_WEATHER_MAP.get(today_code, ("unknown", "❓", "?"))[0],
                    "emoji": WMO_WEATHER_MAP.get(today_code, ("unknown", "❓", "?"))[1],
                    "label_uk": WMO_WEATHER_MAP.get(today_code, ("unknown", "❓", "?"))[2],
                },
            } if today_code else None,
            "forecast_tomorrow": {
                "code": tomorrow_code,
                **{
                    "ha_state": WMO_WEATHER_MAP.get(tomorrow_code, ("unknown", "❓", "?"))[0],
                    "emoji": WMO_WEATHER_MAP.get(tomorrow_code, ("unknown", "❓", "?"))[1],
                    "label_uk": WMO_WEATHER_MAP.get(tomorrow_code, ("unknown", "❓", "?"))[2],
                },
            } if tomorrow_code else None,
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
    """Sensor: gross estimate of the value of grid import that
    battery discharge replaced today.

    The formula is::

        savings_uah = discharge_day_kwh * day_tariff
                     + discharge_night_kwh * night_tariff

    Audit T21: this is a **gross** estimate of the import value
    that battery discharge replaced - it is NOT net savings.
    The formula has three explicit limitations:

      1. **Energy origin not considered**. Every discharged kWh
         is credited at the day/night tariff, even if the
         battery was charged from the grid at night (cheap
         tariff) and discharged during the day (expensive
         tariff). This is an arbitrage profit, not a
         saving against the counterfactual of grid-only
         operation.
      2. **Losses not subtracted**. Battery round-trip
         efficiency is not 100 %; the discharged kWh does
         not equal the energy that was originally available
         to the load. The formula does not subtract
         charge/discharge losses.
      3. **Imports not subtracted**. The formula does not
         subtract the kWh imported from the grid during
         the same period. A setup that imports 30 kWh and
         discharges 25 kWh shows positive savings instead
         of negative net.

    The audit requires that we do NOT add a net-savings model
    without an agreed formula. Consumers reading this
    sensor should treat the value as an estimate of
    "import-value replaced by battery discharge", not as
    net savings.

    The entity ID, translation_key, unit (UAH), and
    state class (MEASUREMENT) are preserved so consumer
    dashboards and automations continue to work.
    """

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

    @property
    def extra_state_attributes(self):
        """Audit T21: expose the formula and limitations
        so a consumer reading the sensor can see what
        it represents."""
        return {
            "savings_formula": (
                "gross_estimate_of_import_value_replaced "
                "= discharge_day_kwh * day_tariff "
                "+ discharge_night_kwh * night_tariff"
            ),
            "savings_limitations": [
                "energy_origin_not_considered",
                "losses_not_subtracted",
                "imports_not_subtracted",
            ],
            "is_net_savings": False,
        }



class MonthlySavingsSensor(InverterSensor):
    """Sensor: gross estimate of the value of grid import
    that battery discharge replaced this month.

    Audit T21: the formula mirrors ``DailySavingsSensor``::

        monthly_savings_uah = sum of daily_savings_uah over
                              the calendar month (with the
                              current day included if not
                              yet rolled over)

    The three limitations in ``DailySavingsSensor``
    (energy origin, losses, imports not subtracted) apply
    here too. The audit forbids a net-savings model without
    an agreed formula.

    Entity ID, translation_key, unit (UAH), and state class
    are preserved.
    """

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

    @property
    def extra_state_attributes(self):
        """Audit T21: expose the formula and limitations
        so a consumer reading the sensor can see what
        it represents."""
        return {
            "savings_formula": (
                "gross_estimate_of_import_value_replaced "
                "= sum over the month of discharge_kwh * tariff"
            ),
            "savings_limitations": [
                "energy_origin_not_considered",
                "losses_not_subtracted",
                "imports_not_subtracted",
            ],
            "is_net_savings": False,
        }



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


class PvGenerationCurveSensor(CoordinatorEntity, SensorEntity):
    """Correct W conversion of real cloud points without changing legacy API."""
    _attr_has_entity_name = True
    _attr_name = "PV generation curve"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator, inverter):
        super().__init__(coordinator)
        self._inverter = inverter
        self._attr_unique_id = f"{coordinator.api.device_sn}_pv_generation_curve"
        self._attr_device_info = {"identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")}}

    def _points(self):
        from .hems.pv_chart import chart_points
        return chart_points((self.coordinator.data or {}).get("today_hourly_power", []),
                            self._inverter._pv_local_now())

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self._inverter.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self):
        points = self._points()
        return points[-1]["power_w"] if points else None

    @property
    def extra_state_attributes(self):
        from .hems.pv_chart import previous_curve
        now = self._inverter._pv_local_now()
        return {"date": now.date().isoformat(), "points": self._points(),
                "source": "cloud_api_real_samples", "sample_interval_minutes": 30,
                "previous_day": previous_curve(getattr(self._inverter, "_cloud_hourly_cache", None), now)}


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

        # Build per-timestamp weather list (emoji) from the forecast sensor.
        # We piggy-back on sensor.pv_forecast_tomorrow's hourly_weather
        # attribute (24 dicts {code, emoji, ...}) which already has the
        # icon computed. Each label here is a 30-min timestamp; we map by
        # hour-of-day so 00:00 and 00:30 both pull the "00:00" forecast.
        hourly_weather = []
        from datetime import datetime as _dt

        # Find the forecast sensor among our known sensors.
        forecast_emojis: dict[int, str] = {}
        for s in getattr(self, "platform", None) and self.platform.entities.values() or []:
            key = getattr(getattr(s, "entity_description", None), "key", "")
            if key == "forecast_tomorrow":
                hw = (s.extra_state_attributes or {}).get("hourly_weather", [])
                if hw:
                    for i, w in enumerate(hw):
                        if isinstance(w, dict):
                            forecast_emojis[i] = w.get("emoji", "")
                break

        for lbl in labels:
            emoji = ""
            try:
                ts = _dt.strptime(lbl, "%Y-%m-%d %H:%M:%S")
                emoji = forecast_emojis.get(ts.hour, "")
            except (ValueError, TypeError):
                pass
            hourly_weather.append(emoji)

        return {
            "hourly_power_kw": values_kw,
            "hourly_labels": labels,
            "hourly_weather": hourly_weather,
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
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
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
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
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



class PredictiveHintSensor(CoordinatorEntity, SensorEntity):
    """ML hint that augments the active mode (Adaptive/Arbitrage/Storm).

    Shows what ML SUGGESTS as optimal SOC targets and night-charge window.
    The active mode still makes the final decision - this is just guidance.
    """
    _attr_name = "Predictive Hint"
    _attr_icon = "mdi:brain"
    _attr_should_poll = False

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_predictive_hint"
        self._entry = entry

    @property
    def native_value(self) -> str:
        hems = getattr(self.coordinator, "_hems", None)
        if hems is None:
            return "unavailable"
        hint = getattr(hems, "_last_predictive_hint", None)
        if hint is None:
            return "disabled"
        return f"conf={hint.confidence}"

    @property
    def extra_state_attributes(self) -> dict:
        hems = getattr(self.coordinator, "_hems", None)
        if hems is None:
            return {}
        hint = getattr(hems, "_last_predictive_hint", None)
        if hint is None:
            return {"enabled": False}
        return {
            "enabled": True,
            "reason": hint.reason,
            "target_morning_soc": round(hint.target_soc_morning, 1),
            "target_evening_soc": round(hint.target_soc_evening, 1),
            "night_charge_window": f"{hint.night_charge_start_hour}-{hint.night_charge_end_hour}",
            "storm_preemption": hint.storm_preemption,
            "storm_reason": hint.storm_reason,
            "confidence": round(hint.confidence, 2),
        }


class PredictiveDecisionStateSensor(CoordinatorEntity, SensorEntity):
    """Distinguish the AI recommendation from acknowledged control."""
    _attr_has_entity_name = True
    _attr_name = "Predictive Decision State"
    _attr_icon = "mdi:brain"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_predictive_decision_state"
        self._attr_device_info = {"identifiers": {(DOMAIN, coordinator.api.device_sn or entry.entry_id)}}

    @property
    def native_value(self):
        state = self.coordinator._hems.predictive_decision_state
        if state.get("override_pending_until"):
            return "override"
        return "applied" if state.get("applied") else state.get("mode", "Off").lower()

    @property
    def extra_state_attributes(self):
        attributes = dict(self.coordinator._hems.predictive_decision_state)
        learning = getattr(self.coordinator, "_pv_learning", None)
        if learning is not None:
            attributes["forecast_calibration"] = learning.calibration_status(
                self.coordinator._pv_local_now().date().isoformat())
        # R01 production probe source: a small, documented
        # diagnostic describing the *last fetched* hourly
        # forecast. This is the field the live-probe parses —
        # not ``_raw_hourly_forecast`` directly, which is
        # coordinator-internal and never reaches the
        # ``/api/states/`` response. The diagnostic includes
        # only metadata + a single example row, never the
        # full array.
        attributes["forecast_diagnostic"] = (
            self._build_forecast_diagnostic()
        )
        return attributes

    def _build_forecast_diagnostic(self):
        """Return a compact description of the most recent
        hourly forecast the coordinator consumed. Probe reads
        this from the published ``extra_state_attributes`` of
        ``sensor.garazh_smart_solar_inverter_predictive_decision_state``.

        Fields:
            forecast_received_at      ISO-8601, last fetch time (UTC).
            forecast_timezone         The local timezone the
                                      forecast was built for.
            radiation_contract_version  2 (= interval-start
                                         contract).
            forecast_dates            Sorted list of the 3
                                      local dates the forecast
                                      covers.
            intervals_per_date        {date: count} map of
                                      hourly rows in the
                                      trimmed forecast.
            sample_row                Single example hourly row
                                      with timestamp,
                                      weather_timestamp,
                                      radiation, power_w, and
                                      model tag.
            rows_with_diff_ne_3600    Count of rows whose
                                      ``weather_timestamp -
                                      timestamp != 3600`` —
                                      must be 0 in v2.
            forecast_model_tags       Sorted list of unique
                                      ``forecast_model`` tags
                                      seen in the forecast.
            forecast_rows_total       Total row count (for
                                      cross-check).
        """
        raw = getattr(self.coordinator, "_raw_hourly_forecast", None) or []
        if not raw:
            return {"forecast_received_at": None, "forecast_rows_total": 0}
        # Per-date interval count
        intervals_per_date: dict[str, int] = {}
        sample = None
        bad_diff = 0
        model_tags: set[str] = set()
        for h in raw:
            day = h.get("time", "")[:10]
            if day:
                intervals_per_date[day] = intervals_per_date.get(day, 0) + 1
            ts = h.get("timestamp")
            wts = h.get("weather_timestamp")
            if isinstance(ts, (int, float)) and isinstance(wts, (int, float)):
                if int(wts) - int(ts) != 3600:
                    bad_diff += 1
            tag = h.get("forecast_model")
            if isinstance(tag, str):
                model_tags.add(tag)
            if sample is None and isinstance(ts, (int, float)):
                sample = {
                    "timestamp": int(ts),
                    "weather_timestamp": int(wts) if isinstance(wts, (int, float)) else None,
                    "radiation_wm2": h.get("radiation_wm2"),
                    "power_w": h.get("power_w"),
                    "forecast_model": h.get("forecast_model"),
                }
        last_at = getattr(self.coordinator, "_forecast_last_fetch", None)
        if isinstance(last_at, (int, float)) and last_at > 0:
            from datetime import datetime, timezone as _tz
            received_at = datetime.fromtimestamp(last_at, _tz.utc).isoformat()
        else:
            received_at = None
        learning = getattr(self.coordinator, "_pv_learning", None)
        contract = getattr(learning, "radiation_contract_version", None) if learning is not None else None
        tz_name = getattr(self.coordinator, "_site_timezone_name", None) or getattr(self.coordinator, "timezone_name", None)
        return {
            "forecast_received_at": received_at,
            "forecast_timezone": tz_name,
            "radiation_contract_version": contract,
            "forecast_dates": sorted(intervals_per_date.keys()),
            "intervals_per_date": dict(sorted(intervals_per_date.items())),
            "sample_row": sample,
            "rows_with_diff_ne_3600": bad_diff,
            "forecast_model_tags": sorted(model_tags),
            "forecast_rows_total": len(raw),
        }


class PredictiveDayAheadSensor(CoordinatorEntity, SensorEntity):
    """24h Predictive ML plan.

    Shows what ML would do if it had full control.
    """
    _attr_name = "Predictive Day-Ahead"
    _attr_icon = "mdi:calendar-clock"
    _attr_should_poll = False

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_predictive_plan"
        self._entry = entry

    @property
    def native_value(self) -> int:
        plan = getattr(self.coordinator._hems, "_last_predictive_plan", None)
        if plan is None:
            return 0
        return len(plan.hourly)

    @property
    def extra_state_attributes(self) -> dict:
        plan = getattr(self.coordinator._hems, "_last_predictive_plan", None)
        if plan is None:
            return {}
        return {
            "generated_at": plan.generated_at.isoformat(),
            "expected_pv_kwh": round(plan.expected_pv_kwh, 2),
            "expected_load_kwh": round(plan.expected_load_kwh, 2),
            "plan": [
                {
                    "hour": p.hour,
                    "timestamp": p.timestamp.isoformat(),
                    "pv_w": round(p.pv_w, 0),
                    "load_w": round(p.load_w, 0),
                    "soc_pred": round(p.soc_pred, 1),
                    "output": p.output,
                    "charger": p.charger,
                    "reason": p.reason,
                }
                for p in plan.hourly
            ],
        }





# WMO weather code mapping (subset; full list at open-meteo.com/docs)
WMO_WEATHER_MAP: dict[int, tuple[str, str, str]] = {
    0: ("sunny", "☀️", "Ясно"),
    1: ("partlycloudy", "🌤️", "Переважно ясно"),
    2: ("partlycloudy", "⛅", "Хмарно з проясненнями"),
    3: ("cloudy", "☁️", "Хмарно"),
    45: ("fog", "🌫️", "Туман"),
    48: ("fog", "🌫️", "Паморозний туман"),
    51: ("rainy", "🌦️", "Легка мряка"),
    53: ("rainy", "🌦️", "Мряка"),
    55: ("rainy", "🌧️", "Сильна мряка"),
    61: ("rainy", "🌧️", "Слабкий дощ"),
    63: ("rainy", "🌧️", "Дощ"),
    65: ("rainy", "🌧️", "Сильний дощ"),
    71: ("snowy", "🌨️", "Слабкий сніг"),
    73: ("snowy", "🌨️", "Сніг"),
    75: ("snowy", "❄️", "Сильний сніг"),
    80: ("rainy", "🌦️", "Зливи"),
    81: ("rainy", "🌧️", "Сильні зливи"),
    82: ("pouring", "⛈️", "Дуже сильні зливи"),
    95: ("lightning", "⛈️", "Гроза"),
    96: ("lightning-rainy", "⛈️", "Гроза з градом"),
    99: ("lightning-rainy", "⛈️", "Сильна гроза з градом"),
}
