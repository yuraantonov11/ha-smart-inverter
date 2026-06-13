"""Binary sensor entities for Smart Solar Inverter — grid, battery status + fault flags."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    EASUN_BOOLEAN_ALL_DISPLAYS,
    EASUN_BOOLEAN_ON_DISPLAYS,
    EASUN_SENSOR_META,
)
from .coordinator import InverterCoordinator

_LOGGER = logging.getLogger(__name__)


def _is_binary_key(key: str, info: dict) -> bool:
    """Return True if this key should be exposed as a binary_sensor."""
    meta = EASUN_SENSOR_META.get(key)
    if meta:
        return meta.get("kind") == "binary"
    display = info.get("valueDisplay")
    if isinstance(display, str) and display in EASUN_BOOLEAN_ALL_DISPLAYS:
        return True
    return False


def _humanize(key: str) -> str:
    """Convert camelCase or snake_case to Title Case."""
    import re
    s = re.sub(r'([A-Z])', r' \1', key)
    s = re.sub(r'_+', ' ', s)
    return s.strip().title()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities — manual + dynamic from available rawFields."""
    coordinator: InverterCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    device_sn = coordinator.api.device_sn or "unknown"

    # ── Clean up stale v1 binary sensors (unique_id format: {sn}_bin_{key}) ──
    entity_registry = er.async_get(hass)
    removed = 0
    for entity_entry in list(entity_registry.entities.values()):
        if not entity_entry.unique_id:
            continue
        if f"{device_sn}_bin_" in entity_entry.unique_id and "_bin_v2_" not in entity_entry.unique_id:
            entity_registry.async_remove(entity_entry.entity_id)
            removed += 1
    if removed:
        _LOGGER.info("🧹 Cleaned up %d stale v1 binary sensor entities", removed)

    entities: list[BinarySensorEntity] = [
        InverterGridAvailableSensor(coordinator),
        InverterLowBatterySensor(coordinator),
        InverterChargingFromGridSensor(coordinator),
        InverterGridOutageSensor(coordinator),
    ]

    # Debug: log all available rawFields keys
    raw_fields = (coordinator.data or {}).get("rawFields", {})
    raw_keys = sorted(raw_fields.keys()) if isinstance(raw_fields, dict) else []
    _LOGGER.info(
        "🔍 RawFields keys available (%d): %s",
        len(raw_keys),
        ", ".join(raw_keys[:20]) + ("..." if len(raw_keys) > 20 else ""),
    )

    # Only create binary sensors for keys actually present in raw data
    found_binary: list[str] = []
    missing_binary: list[str] = []
    for key, meta in EASUN_SENSOR_META.items():
        if meta.get("kind") != "binary":
            continue
        if key in raw_fields:
            entities.append(InverterFaultFlagSensor(coordinator, device_sn, key, meta))
            found_binary.append(key)
        else:
            missing_binary.append(key)

    _LOGGER.info(
        "✅ Binary sensors created: %d found (%s)",
        len(found_binary),
        ", ".join(found_binary[:10]) if found_binary else "none",
    )
    if missing_binary:
        _LOGGER.info(
            "⏭️  Binary sensors skipped (key not in rawFields, %d): %s",
            len(missing_binary),
            ", ".join(missing_binary[:15]) + (
                f" +{len(missing_binary)-15} more" if len(missing_binary) > 15 else ""
            ),
        )
        _LOGGER.info(
            "💡 These fields may not be supported by your inverter model. "
            "If you see stale 'Недоступно' entities, remove them in "
            "Settings → Devices → Entities."
        )

    async_add_entities(entities)


# ── Manual sensors (existing) ───────────────────────────────────────────────

class InverterBinarySensorBase(CoordinatorEntity, BinarySensorEntity):
    """Base binary sensor for Inverter."""

    def __init__(
        self,
        coordinator: InverterCoordinator,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self._attr_has_entity_name = True
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.api.device_sn}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.api.device_sn or "unknown")},
        }


class InverterGridAvailableSensor(InverterBinarySensorBase):
    """Binary sensor: is the grid available?"""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            BinarySensorEntityDescription(
                key="grid_available",
                translation_key="grid_available",
                device_class=BinarySensorDeviceClass.POWER,
                icon="mdi:transmission-tower",
            ),
        )

    @property
    def is_on(self) -> bool:
        if self.coordinator.data is None:
            return True
        return self.coordinator.data.get("gridAvailable", True)


class InverterGridOutageSensor(InverterBinarySensorBase):
    """Binary sensor: is there a grid outage?"""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            BinarySensorEntityDescription(
                key="grid_outage",
                translation_key="grid_outage",
                device_class=BinarySensorDeviceClass.PROBLEM,
                icon="mdi:power-plug-off",
            ),
        )

    @property
    def is_on(self) -> bool:
        if self.coordinator.data is None:
            return False
        return not self.coordinator.data.get("gridAvailable", True)


class InverterLowBatterySensor(InverterBinarySensorBase):
    """Binary sensor: low battery alert (SOC < 25%)."""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            BinarySensorEntityDescription(
                key="low_battery",
                translation_key="low_battery",
                device_class=BinarySensorDeviceClass.BATTERY,
                icon="mdi:battery-alert",
            ),
        )

    @property
    def is_on(self) -> bool:
        if self.coordinator.data is None:
            return False
        soc = self.coordinator.data.get("correctedSoc", 100.0) or 100.0
        return soc < 25.0


class InverterChargingFromGridSensor(InverterBinarySensorBase):
    """Binary sensor: is battery charging from grid?"""

    def __init__(self, coordinator: InverterCoordinator) -> None:
        super().__init__(
            coordinator,
            BinarySensorEntityDescription(
                key="charging_from_grid",
                translation_key="charging_from_grid",
                device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
                icon="mdi:battery-charging",
            ),
        )

    @property
    def is_on(self) -> bool:
        if self.coordinator.data is None:
            return False
        grid = self.coordinator.data.get("gridPower", 0.0) or 0.0
        battery = self.coordinator.data.get("batteryPower", 0.0) or 0.0
        return grid > 50 and battery > 50


# ── Dynamic binary sensors from EASUN_SENSOR_META ────────────────────────────

class InverterFaultFlagSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for fault flags / run state / grid presence / lights."""

    def __init__(
        self,
        coordinator: InverterCoordinator,
        device_sn: str,
        sensor_key: str,
        meta: dict,
    ) -> None:
        super().__init__(coordinator)
        self._sensor_key = sensor_key
        self._meta = meta
        self._attr_has_entity_name = True
        self._attr_name = _humanize(sensor_key)
        self._attr_unique_id = f"{device_sn}_bin_v2_{sensor_key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_sn)},
        }
        if meta.get("icon"):
            self._attr_icon = meta["icon"]
        dc = meta.get("device_class")
        if dc:
            try:
                self._attr_device_class = BinarySensorDeviceClass(dc)
            except ValueError:
                _LOGGER.debug("unknown device_class %r for %s", dc, sensor_key)

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        raw_fields = self.coordinator.data.get("rawFields", {})
        info = raw_fields.get(self._sensor_key)
        if info is None or not isinstance(info, dict):
            return None
        display = info.get("valueDisplay")
        if display is not None:
            return str(display) in EASUN_BOOLEAN_ON_DISPLAYS
        val = info.get("value")
        if val is None:
            return None
        return str(val).strip().lower() in ("1", "true", "on")
