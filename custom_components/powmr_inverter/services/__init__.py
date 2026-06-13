"""Services for PowMr Inverter — register custom services."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_OUTPUT_PRIORITY = "set_output_priority"
SERVICE_SET_CHARGER_PRIORITY = "set_charger_priority"
SERVICE_SET_SMART_MODE = "set_smart_mode"
SERVICE_FORCE_GRID_CHARGE = "force_grid_charge"

SET_OUTPUT_PRIORITY_SCHEMA = vol.Schema(
    {
        vol.Required("priority"): vol.In(["USB", "SBU"]),
    }
)

SET_CHARGER_PRIORITY_SCHEMA = vol.Schema(
    {
        vol.Required("priority"): vol.In(["CSO", "SNU", "OSO", "UTO"]),
    }
)

SET_SMART_MODE_SCHEMA = vol.Schema(
    {
        vol.Required("mode"): vol.In(["adaptive", "arbitrage", "storm"]),
    }
)

FORCE_GRID_CHARGE_SCHEMA = vol.Schema(
    {
        vol.Optional("duration_minutes", default=60): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=480)
        ),
    }
)

# Priority value map
_PRIORITY_VALUE = {
    "USB": "0",
    "SBU": "2",
    "CSO": "0",
    "SNU": "1",
    "OSO": "2",
    "UTO": "3",
}

_MODE_VALUE = {
    "adaptive": 0,
    "arbitrage": 1,
    "storm": 2,
}


async def async_register_services(hass: HomeAssistant) -> None:
    """Register PowMr custom services."""

    async def _get_api(call: ServiceCall):
        """Get API client from first config entry."""
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            raise RuntimeError("No PowMr config entry found")
        entry_id = entries[0].entry_id
        data = hass.data[DOMAIN].get(entry_id)
        if data is None:
            raise RuntimeError("PowMr integration not loaded")
        return data["api"], data["coordinator"]

    async def handle_set_output_priority(call: ServiceCall) -> None:
        api, coordinator = await _get_api(call)
        priority = call.data["priority"]
        value = _PRIORITY_VALUE.get(priority, "0")
        ok = await api.set_output_priority(value)
        if ok:
            _LOGGER.info("Service: output priority → %s", priority)
        else:
            _LOGGER.error("Service: failed to set output priority → %s", priority)

    async def handle_set_charger_priority(call: ServiceCall) -> None:
        api, coordinator = await _get_api(call)
        priority = call.data["priority"]
        value = _PRIORITY_VALUE.get(priority, "1")
        ok = await api.set_charger_priority(value)
        if ok:
            _LOGGER.info("Service: charger priority → %s", priority)
        else:
            _LOGGER.error("Service: failed to set charger priority → %s", priority)

    async def handle_set_smart_mode(call: ServiceCall) -> None:
        api, coordinator = await _get_api(call)
        mode = call.data["mode"]
        mode_val = _MODE_VALUE.get(mode, 0)
        coordinator.smart_mode = mode_val
        _LOGGER.info("Service: smart mode → %s (%d)", mode, mode_val)

    async def handle_force_grid_charge(call: ServiceCall) -> None:
        api, coordinator = await _get_api(call)
        duration = call.data["duration_minutes"]
        _LOGGER.info("Service: force grid charge for %d min", duration)
        # Set charger to SNU (grid + solar) and output to USB (grid first)
        ok1 = await api.set_charger_priority("1")  # SNU
        ok2 = await api.set_output_priority("0")  # USB
        if ok1 and ok2:
            _LOGGER.info("Force grid charge started (%d min)", duration)
        else:
            _LOGGER.error("Force grid charge failed")

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_OUTPUT_PRIORITY,
        handle_set_output_priority,
        schema=SET_OUTPUT_PRIORITY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CHARGER_PRIORITY,
        handle_set_charger_priority,
        schema=SET_CHARGER_PRIORITY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SMART_MODE,
        handle_set_smart_mode,
        schema=SET_SMART_MODE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_FORCE_GRID_CHARGE,
        handle_force_grid_charge,
        schema=FORCE_GRID_CHARGE_SCHEMA,
    )

    # ── New control services ──────────────────────────────────────────────

    async def handle_set_grid_charging(call: ServiceCall) -> None:
        api, coordinator = await _get_api(call)
        enable = call.data["enable"]
        key = "acChargingSwitch"
        await api.set_config_item(key, "1" if enable else "0")
        await coordinator.async_request_refresh()
        _LOGGER.info("Service: grid charging → %s", "ON" if enable else "OFF")

    async def handle_set_grid_feed_in(call: ServiceCall) -> None:
        api, coordinator = await _get_api(call)
        enable = call.data["enable"]
        key = "batteryPowerLimitingSetting"
        await api.set_config_item(key, "1" if enable else "0")
        await coordinator.async_request_refresh()
        _LOGGER.info("Service: grid feed-in → %s", "ON" if enable else "OFF")

    async def handle_set_backup_mode(call: ServiceCall) -> None:
        api, coordinator = await _get_api(call)
        enable = call.data["enable"]
        await api.set_output_priority("2" if enable else "0")
        await coordinator.async_request_refresh()
        _LOGGER.info("Service: backup mode → %s", "ON" if enable else "OFF")

    async def handle_set_battery_charge_limit(call: ServiceCall) -> None:
        api, coordinator = await _get_api(call)
        percent = int(call.data["percent"])
        await api.set_config_item("batteryChargeLimit", str(percent))
        await coordinator.async_request_refresh()
        _LOGGER.info("Service: battery charge limit → %d%%", percent)

    async def handle_set_grid_charge_power(call: ServiceCall) -> None:
        api, coordinator = await _get_api(call)
        watts = int(call.data["watts"])
        await api.set_config_item("gridConnectedPowers", str(watts))
        await coordinator.async_request_refresh()
        _LOGGER.info("Service: grid charge power → %d W", watts)

    hass.services.async_register(
        DOMAIN,
        "set_grid_charging",
        handle_set_grid_charging,
        schema=vol.Schema({vol.Required("enable"): bool}),
    )
    hass.services.async_register(
        DOMAIN,
        "set_grid_feed_in",
        handle_set_grid_feed_in,
        schema=vol.Schema({vol.Required("enable"): bool}),
    )
    hass.services.async_register(
        DOMAIN,
        "set_backup_mode",
        handle_set_backup_mode,
        schema=vol.Schema({vol.Required("enable"): bool}),
    )
    hass.services.async_register(
        DOMAIN,
        "set_battery_charge_limit",
        handle_set_battery_charge_limit,
        schema=vol.Schema({vol.Required("percent"): vol.All(vol.Coerce(int), vol.Range(min=10, max=100))}),
    )
    hass.services.async_register(
        DOMAIN,
        "set_grid_charge_power",
        handle_set_grid_charge_power,
        schema=vol.Schema({vol.Required("watts"): vol.All(vol.Coerce(int), vol.Range(min=0, max=5000))}),
    )
