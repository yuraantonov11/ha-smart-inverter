"""Select entities for Inverter Smart Inverter.

Provides output priority, charger priority, HEMS mode, battery type,
and AC input range selection.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CHARGER_CSO,
    CHARGER_OSO,
    CHARGER_SNU,
    CHARGER_UTO,
    DOMAIN,
    OUTPUT_SBU,
    OUTPUT_USB,
    SMART_MODE_ADAPTIVE,
    SMART_MODE_ARBITRAGE,
    SMART_MODE_STORM,
)
from .coordinator import InverterCoordinator

_LOGGER = logging.getLogger(__name__)

OUTPUT_OPTIONS = ["USB (Grid First)", "SBU (Solar/Battery First)"]
CHARGER_OPTIONS = [
    "CSO (Solar First)",
    "SNU (Solar + Utility)",
    "OSO (Solar Only)",
    "UTO (Utility Only)",
]
SMART_MODE_OPTIONS = ["Adaptive", "Arbitrage", "Storm"]
BATTERY_TYPE_OPTIONS = ["AGM", "FLD", "USE", "LIB", "PYL", "TQF", "GRO", "LIA", "LIC", "FEL"]
AC_INPUT_RANGE_OPTIONS = ["Appliance", "UPS"]

OUTPUT_VALUE_MAP = {
    "USB (Grid First)": OUTPUT_USB,
    "SBU (Solar/Battery First)": OUTPUT_SBU,
}
# Maps API value (numeric string OR text label) -> option label
OUTPUT_LABEL_MAP: dict[str, str] = {
    "0": "USB (Grid First)",
    "2": "SBU (Solar/Battery First)",
    "USB": "USB (Grid First)",
    "SBU": "SBU (Solar/Battery First)",
    "SUB": "SBU (Solar/Battery First)",
}

CHARGER_VALUE_MAP = {
    "CSO (Solar First)": CHARGER_CSO,
    "SNU (Solar + Utility)": CHARGER_SNU,
    "OSO (Solar Only)": CHARGER_OSO,
    "UTO (Utility Only)": CHARGER_UTO,
}
# Maps API value (numeric string OR text label) -> option label
CHARGER_LABEL_MAP: dict[str, str] = {
    "0": "CSO (Solar First)",
    "1": "SNU (Solar + Utility)",
    "2": "OSO (Solar Only)",
    "3": "UTO (Utility Only)",
    "CSO": "CSO (Solar First)",
    "SNU": "SNU (Solar + Utility)",
    "OSO": "OSO (Solar Only)",
    "UTO": "UTO (Utility Only)",
}

SMART_MODE_VALUE_MAP = {
    "Adaptive": 0,
    "Arbitrage": 1,
    "Storm": 2,
}
SMART_MODE_LABEL_MAP = {v: k for k, v in SMART_MODE_VALUE_MAP.items()}

# Battery type: API numeric value -> option label
BATTERY_TYPE_VALUE_MAP: dict[str, str] = {
    "AGM": "0", "FLD": "1", "USE": "2", "LIB": "3",
    "PYL": "4", "TQF": "5", "GRO": "6", "LIA": "7",
    "LIC": "8", "FEL": "9",
}
BATTERY_TYPE_LABEL_MAP: dict[str, str] = {v: k for k, v in BATTERY_TYPE_VALUE_MAP.items()}
# Also handle numeric strings from API
BATTERY_TYPE_LABEL_MAP.update({
    "0": "AGM", "1": "FLD", "2": "USE", "3": "LIB",
    "4": "PYL", "5": "TQF", "6": "GRO", "7": "LIA",
    "8": "LIC", "9": "FEL",
})

AC_INPUT_RANGE_VALUE_MAP: dict[str, str] = {
    "Appliance": "0",
    "UPS": "1",
}
AC_INPUT_RANGE_LABEL_MAP: dict[str, str] = {
    "0": "Appliance",
    "1": "UPS",
    "APL": "Appliance",
}


def _setting_str(data: dict | None, key: str) -> str | None:
    """Read a string setting value from coordinator deviceSettings."""
    if not data:
        return None
    settings = data.get("deviceSettings", {})
    item = settings.get(key, {})
    if isinstance(item, dict):
        return str(item.get("value", ""))
    return str(item) if item else None


def _setting_display(data: dict | None, key: str) -> str | None:
    """Read the valueDisplay string from coordinator deviceSettings."""
    if not data:
        return None
    settings = data.get("deviceSettings", {})
    item = settings.get(key, {})
    if isinstance(item, dict):
        return item.get("valueDisplay")
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Inverter select entities."""
    coordinator: InverterCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = [
        InverterOutputPrioritySelect(coordinator),
        InverterChargerPrioritySelect(coordinator),
        InverterSmartModeSelect(coordinator),
        InverterBatteryTypeSelect(coordinator),
        InverterAcInputRangeSelect(coordinator),
    ]
    async_add_entities(entities)


class InverterSelectBase(CoordinatorEntity, SelectEntity):
    """Base class for Inverter select entities."""

    def __init__(
        self,
        coordinator: InverterCoordinator,
        description: SelectEntityDescription,
        options: list[str],
    ) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.api.device_sn}_{description.key}"
        )
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }
        self._attr_options = options


class InverterOutputPrioritySelect(InverterSelectBase):
    """Select entity for inverter output source priority."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            SelectEntityDescription(
                key="output_priority",
                translation_key="output_priority",
                icon="mdi:transmission-tower-import",
            ),
            OUTPUT_OPTIONS,
        )

    @property
    def current_option(self) -> str | None:
        if self.coordinator.data is None:
            return None
        val = self.coordinator.data.get("outputSourcePriority", "")
        return OUTPUT_LABEL_MAP.get(val)

    async def async_select_option(self, option: str) -> None:
        value = OUTPUT_VALUE_MAP.get(option, OUTPUT_USB)
        api = self.coordinator.api
        success = await api.set_output_priority(value)
        if success:
            self.coordinator._last_cmd_output = value
            self.coordinator._last_cmd_output_at = __import__("datetime").datetime.now()
            _LOGGER.info("Output priority set to %s (%s)", option, value)
        else:
            _LOGGER.error("Failed to set output priority to %s", option)


class InverterChargerPrioritySelect(InverterSelectBase):
    """Select entity for inverter charger source priority."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            SelectEntityDescription(
                key="charger_priority",
                translation_key="charger_priority",
                icon="mdi:battery-charging",
            ),
            CHARGER_OPTIONS,
        )

    @property
    def current_option(self) -> str | None:
        if self.coordinator.data is None:
            return None
        val = self.coordinator.data.get("chargerSourcePriority", "")
        return CHARGER_LABEL_MAP.get(val)

    async def async_select_option(self, option: str) -> None:
        value = CHARGER_VALUE_MAP.get(option, CHARGER_SNU)
        api = self.coordinator.api
        success = await api.set_charger_priority(value)
        if success:
            self.coordinator._last_cmd_charger = value
            self.coordinator._last_cmd_charger_at = __import__("datetime").datetime.now()
            _LOGGER.info("Charger priority set to %s (%s)", option, value)
        else:
            _LOGGER.error("Failed to set charger priority to %s", option)


class InverterSmartModeSelect(InverterSelectBase):
    """Select entity for HEMS smart mode."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            SelectEntityDescription(
                key="smart_mode",
                translation_key="smart_mode",
                icon="mdi:auto-fix",
            ),
            SMART_MODE_OPTIONS,
        )

    @property
    def current_option(self) -> str | None:
        mode = self.coordinator.smart_mode
        return SMART_MODE_LABEL_MAP.get(mode)

    async def async_select_option(self, option: str) -> None:
        mode = SMART_MODE_VALUE_MAP.get(option, 0)
        self.coordinator.smart_mode = mode
        _LOGGER.info("HEMS smart mode set to %s (%d)", option, mode)


class InverterBatteryTypeSelect(InverterSelectBase):
    """Select entity for battery type."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            SelectEntityDescription(
                key="battery_type",
                translation_key="battery_type",
                icon="mdi:battery",
            ),
            BATTERY_TYPE_OPTIONS,
        )

    @property
    def current_option(self) -> str | None:
        # Prefer valueDisplay from API (e.g. "LIB", "LIA" etc.)
        display = _setting_display(self.coordinator.data, "settingBatteryType")
        if display:
            return display
        val = _setting_str(self.coordinator.data, "settingBatteryType")
        if val is None:
            return None
        return BATTERY_TYPE_LABEL_MAP.get(val, val)

    async def async_select_option(self, option: str) -> None:
        value = BATTERY_TYPE_VALUE_MAP.get(option)
        if value is None:
            # option might be a valueDisplay from the API - try reverse lookup
            for label, val in BATTERY_TYPE_LABEL_MAP.items():
                if label == option:
                    value = val
                    break
        if value is None:
            _LOGGER.error("Unknown battery type option: %s", option)
            return
        ok = await self.coordinator.api.set_config_item("settingBatteryType", value)
        if ok:
            await self.coordinator.async_request_refresh()
            _LOGGER.info("Battery type set to %s (%s)", option, value)
        else:
            _LOGGER.error("Failed to set battery type to %s", option)


class InverterAcInputRangeSelect(InverterSelectBase):
    """Select entity for AC input range (Appliance/UPS)."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            SelectEntityDescription(
                key="ac_input_range",
                translation_key="ac_input_range",
                icon="mdi:power-plug",
            ),
            AC_INPUT_RANGE_OPTIONS,
        )

    @property
    def current_option(self) -> str | None:
        val = _setting_str(self.coordinator.data, "acInputRangeSetting")
        if val is None:
            return None
        return AC_INPUT_RANGE_LABEL_MAP.get(val)

    async def async_select_option(self, option: str) -> None:
        value = AC_INPUT_RANGE_VALUE_MAP.get(option, "0")
        ok = await self.coordinator.api.set_config_item("acInputRangeSetting", value)
        if ok:
            await self.coordinator.async_request_refresh()
            _LOGGER.info("AC input range set to %s (%s)", option, value)
        else:
            _LOGGER.error("Failed to set AC input range to %s", option)
