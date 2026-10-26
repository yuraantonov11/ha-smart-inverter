"""Diagnostic-only sensors for the RWB1 BLE reader.

Every entity here is ``EntityCategory.DIAGNOSTIC`` and reads straight from the
runner in ``hass.data``. They deliberately do **not** subclass
``CoordinatorEntity`` and never touch ``self.coordinator.data``: that is the
structural guarantee that a BLE reading cannot reach the HEMS engine, the outage
planner, SOC selection or any automation. Cloud keeps every selected field.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_diagnostic import PUBLISHED_FIELDS, runner_for

_LOGGER = logging.getLogger(__name__)

_FIELD_UNITS: dict[str, tuple[str | None, SensorDeviceClass | None]] = {
    "grid_voltage": (UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
    "battery_voltage": (UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
    "battery_soc": (PERCENTAGE, SensorDeviceClass.BATTERY),
    "pv_power": (UnitOfPower.WATT, SensorDeviceClass.POWER),
    "load_power": (UnitOfPower.WATT, SensorDeviceClass.POWER),
}


def _parse_ts(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class _BleDiagSensor(SensorEntity):
    """Base: reads the runner's state dict, never the coordinator."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        entry: ConfigEntry,
        key: str,
        name: str,
        value_fn: Callable[[dict[str, Any]], Any],
        attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        unit: str | None = None,
        device_class: SensorDeviceClass | None = None,
    ) -> None:
        self._entry = entry
        self._key = key
        self._name = name
        self._value_fn = value_fn
        self._attrs_fn = attrs_fn
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        # Voltages are published with one decimal: the raw register has 0.1 V
        # resolution, and HA would otherwise round the display to whole volts.
        if device_class is SensorDeviceClass.VOLTAGE:
            self._attr_suggested_display_precision = 1
        self._attr_unique_id = f"{entry.entry_id}_ble_diag_{key}"

    @property
    def name(self) -> str:
        return self._name

    async def async_added_to_hass(self) -> None:
        """Attach to the runner so every cycle pushes a fresh state."""
        await super().async_added_to_hass()
        runner = runner_for(self.hass, self._entry)
        if runner is not None:
            runner.attach(self)

    async def async_will_remove_from_hass(self) -> None:
        runner = runner_for(self.hass, self._entry)
        if runner is not None:
            runner.detach(self)
        await super().async_will_remove_from_hass()

    @property
    def available(self) -> bool:
        return runner_for(self.hass, self._entry) is not None

    @property
    def native_value(self) -> Any:
        runner = runner_for(self.hass, self._entry)
        if runner is None:
            return None
        return self._value_fn(runner.state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        runner = runner_for(self.hass, self._entry)
        attrs: dict[str, Any] = {}
        if runner is not None:
            if self._attrs_fn is not None:
                attrs.update(self._attrs_fn(runner.state))
            attrs["source"] = "rwb1_ble_diagnostic"
            attrs["affects_control"] = False
            attrs["cloud_is_primary"] = True
        return attrs

async def async_add_ble_diagnostic_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register the diagnostic sensors.

    Called from ``sensor.async_setup_entry`` rather than as a separate platform,
    so this needs no change to ``PLATFORMS`` and no extra forward. Safe whether
    or not BLE polling is enabled: the entities exist either way and report the
    disabled state.
    """
    entities: list[SensorEntity] = []

    entities.append(
        _BleDiagSensor(
            entry,
            "read_status",
            "RWB1 BLE read status",
            lambda s: s.get("status"),
            attrs_fn=lambda s: {
                "enabled": s.get("enabled"),
                "error_kind": s.get("error_kind"),
                "last_error": s.get("last_error"),
                "cycle": s.get("cycle"),
                "last_attempt": s.get("last_attempt"),
                "last_success": s.get("last_success"),
                "interval_s": s.get("interval_s"),
                "device_address": s.get("address"),
                "device_name": s.get("device_name"),
                "device_known_to_ha": s.get("device_known"),
                "adapter_for_device": s.get("adapter_for_device"),
                "device_rssi": s.get("rssi"),
                "dtu_id_configured": s.get("dtu_id_configured"),
                "client_class": s.get("client_class"),
                "profile_id": s.get("profile_id"),
                "last_received_at": s.get("received_at"),
                # Long-lived-session accounting: how many cycles opened a new
                # BLE session vs. reused the existing one, and the measured gap
                # between two successful reads.
                "response_timeout_s": s.get("response_timeout_s"),
                "session_built": s.get("session_built"),
                "session_reused": s.get("session_reused"),
                "skipped_cycles": s.get("skipped_cycles"),
                "reads_ok": s.get("reads_ok"),
                "reads_failed": s.get("reads_failed"),
                "reconnects": s.get("reconnects"),
                # When the counting started. Without it a reset to zero after a
                # restart is indistinguishable from "nothing ever happened".
                "counters_started_at": s.get("counters_started_at"),
                "sessions_dropped": s.get("sessions_dropped"),
                # Fault and recovery: what the reads proved, and what the
                # runner did about it.
                "consecutive_failures": s.get("consecutive_failures"),
                "last_fault": s.get("last_fault"),
                "recovery": s.get("recovery"),
                "rescheduled_delay_s": s.get("rescheduled_delay_s"),
                "seconds_since_previous_success": s.get(
                    "seconds_since_previous_success"
                ),
                "last_disconnect": s.get("last_disconnect"),
                "note": (
                    "Diagnostic only. No BLE value is selected for control; "
                    "cloud remains the primary source for every field."
                ),
            },
        )
    )

    entities.append(
        _BleDiagSensor(
            entry,
            "proxy",
            "RWB1 BLE proxy",
            lambda s: (
                s.get("adapter_for_device")
                or ", ".join((s.get("proxy") or {}).get("adapters") or [])
                or None
            ),
            attrs_fn=lambda s: {
                "adapter_for_device": s.get("adapter_for_device"),
                "ha_adapters": (s.get("proxy") or {}).get("adapters"),
                "adapter_count": (s.get("proxy") or {}).get("adapter_count"),
                "device_known_to_ha": s.get("device_known"),
                "device_name": s.get("device_name"),
                "device_rssi": s.get("rssi"),
                "integration": (s.get("proxy") or {}).get("integration"),
            },
        )
    )

    for field, _unit_hint, _icon in PUBLISHED_FIELDS:
        unit, device_class = _FIELD_UNITS.get(field, (None, None))
        entities.append(
            _BleDiagSensor(
                entry,
                f"value_{field}",
                f"RWB1 BLE {field.replace('_', ' ')}",
                lambda s, f=field: (s.get("values") or {}).get(f),
                attrs_fn=lambda s, f=field: {
                    "field": f,
                    "last_received_at": s.get("received_at"),
                    "last_success": s.get("last_success"),
                    "status": s.get("status"),
                    "error_kind": s.get("error_kind"),
                    "measured_at": None,
                    "quality": "diagnostic-unvalidated",
                },
                unit=unit,
                device_class=device_class,
            )
        )

    async_add_entities(entities)
    _LOGGER.info(
        "RWB1 BLE diagnostic sensors registered: %s (cloud stays primary)", len(entities)
    )


__all__ = ["async_add_ble_diagnostic_entities"]