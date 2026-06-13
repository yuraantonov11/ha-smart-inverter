"""Select entities for PowMr Smart Inverter.

Provides output priority, charger priority, and HEMS mode selection.
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
from .coordinator import PowMrCoordinator

_LOGGER = logging.getLogger(__name__)

OUTPUT_OPTIONS = ["USB (Grid First)", "SBU (Solar/Battery First)"]
CHARGER_OPTIONS = [
    "CSO (Solar First)",
    "SNU (Solar + Utility)",
    "OSO (Solar Only)",
    "UTO (Utility Only)",
]
SMART_MODE_OPTIONS = ["Adaptive", "Arbitrage", "Storm"]

OUTPUT_VALUE_MAP = {
    "USB (Grid First)": OUTPUT_USB,
    "SBU (Solar/Battery First)": OUTPUT_SBU,
}
# Maps API value (numeric string OR text label) → option label
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
# Maps API value (numeric string OR text label) → option label
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PowMr select entities."""
    coordinator: PowMrCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = [
        PowMrOutputPrioritySelect(coordinator),
        PowMrChargerPrioritySelect(coordinator),
        PowMrSmartModeSelect(coordinator),
    ]
    async_add_entities(entities)


class PowMrSelectBase(CoordinatorEntity, SelectEntity):
    """Base class for PowMr select entities."""

    def __init__(
        self,
        coordinator: PowMrCoordinator,
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


class PowMrOutputPrioritySelect(PowMrSelectBase):
    """Select entity for inverter output source priority."""

    def __init__(self, coordinator: PowMrCoordinator) -> None:
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


class PowMrChargerPrioritySelect(PowMrSelectBase):
    """Select entity for inverter charger source priority."""

    def __init__(self, coordinator: PowMrCoordinator) -> None:
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


class PowMrSmartModeSelect(PowMrSelectBase):
    """Select entity for HEMS smart mode."""

    def __init__(self, coordinator: PowMrCoordinator) -> None:
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
