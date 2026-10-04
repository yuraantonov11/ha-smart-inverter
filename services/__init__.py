"""Services for Smart Solar Inverter — register custom services."""

from __future__ import annotations

from datetime import datetime, timedelta
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
        vol.Optional("entry_id"): str,
        vol.Optional("duration_minutes", default=60): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=480)
        ),
    }
)

# T11 follow-up: every hardware-writing service schema now
# accepts an optional ``entry_id``. The schema-only addition
# is safe for existing callers (the field is optional and
# the existing service is invoked the same way); the actual
# routing is done by ``_resolve_entry``.
_HARDWARE_SERVICE_ENTRY_ID = {"entry_id": str}

# Schemas for the new control services — each gets
# ``_HARDWARE_SERVICE_ENTRY_ID`` mixed in. The dict-spread
# keeps the optional ``entry_id`` first so HA's service UI
# shows it as a target selector.
SET_GRID_CHARGING_SCHEMA = vol.Schema(
    {vol.Optional("entry_id"): str, vol.Required("enable"): bool}
)
SET_GRID_FEED_IN_SCHEMA = vol.Schema(
    {vol.Optional("entry_id"): str, vol.Required("enable"): bool}
)
SET_BACKUP_MODE_SCHEMA = vol.Schema(
    {vol.Optional("entry_id"): str, vol.Required("enable"): bool}
)
SET_BATTERY_CHARGE_LIMIT_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Required("percent"): vol.All(vol.Coerce(int), vol.Range(min=10, max=100)),
    }
)
SET_GRID_CHARGE_POWER_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Required("watts"): vol.All(vol.Coerce(int), vol.Range(min=0, max=5000)),
    }
)

# Schema for the priority/charger/smart_mode services: also
# accept ``entry_id``. We add it as a separate ``vol.Optional``
# so the existing required fields stay ordered.
SET_OUTPUT_PRIORITY_SCHEMA = vol.Schema(
    {vol.Optional("entry_id"): str, vol.Required("priority"): vol.In(["USB", "SBU"])}
)
SET_CHARGER_PRIORITY_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Required("priority"): vol.In(["CSO", "SNU", "OSO", "UTO"]),
    }
)
SET_SMART_MODE_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Required("mode"): vol.In(["adaptive", "arbitrage", "storm"]),
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


def _resolve_entry(
    hass: HomeAssistant, call: ServiceCall
):
    """Resolve a config entry for a service call.

    T11 fix: a service call that targets an inverter must
    address the *requested* config entry. The previous
    implementation always picked ``entries[0]`` regardless
    of ``entry_id`` in the call, so a user with two
    inverters could send a command to one inverter and
    have it delivered to the other. The fix:

      * If ``entry_id`` is supplied, it must match a
        *loaded* entry. Anything else (unknown id, or an
        id whose data is not in ``hass.data[DOMAIN]``)
        is an explicit failure.
      * If ``entry_id`` is not supplied and there is
        exactly one loaded entry, that entry is used.
      * If ``entry_id`` is not supplied and there are
        multiple loaded entries, refuse to act so the
        user can pick a target explicitly. The previous
        code would have silently routed to the first
        entry — that is the bug.
      * If no entry is loaded at all, raise. ``predictive_feedback``
        already had similar logic; we mirror it here so
        *every* hardware-writing service uses the same
        contract.

    Returns ``(api, coordinator)`` for the resolved entry.
    Raises ``ValueError`` (mapped to HA service-call error)
    if the target is ambiguous or missing.

    This function is module-level (not a closure) so it can
    be unit-tested without spinning up a full ``hass``.
    """
    loaded = hass.data.get(DOMAIN, {})
    if not loaded:
        raise ValueError("No Inverter config entry is loaded")
    requested = call.data.get("entry_id")
    entries = hass.config_entries.async_entries(DOMAIN)
    if requested is not None:
        # Explicit target — must match a loaded entry.
        matches = [
            entry
            for entry in entries
            if entry.entry_id == requested and entry.entry_id in loaded
        ]
        if not matches:
            # Distinguish unknown vs. unloaded for the
            # log message; both are equally fatal for a
            # write.
            if any(e.entry_id == requested for e in entries):
                raise ValueError(
                    f"Inverter config entry {requested!r} is not loaded"
                )
            raise ValueError(
                f"Unknown inverter config entry {requested!r}"
            )
        entry = matches[0]
    else:
        # No explicit target — require exactly one loaded entry.
        loaded_ids = [e.entry_id for e in entries if e.entry_id in loaded]
        if len(loaded_ids) == 0:
            raise ValueError("No Inverter config entry is loaded")
        if len(loaded_ids) > 1:
            raise ValueError(
                "Multiple Inverter config entries loaded; "
                "specify entry_id to choose one"
            )
        entry = next(e for e in entries if e.entry_id == loaded_ids[0])
    data = loaded[entry.entry_id]
    return data["api"], data["coordinator"]


async def async_register_services(hass: HomeAssistant) -> None:
    """Register Inverter custom services."""

    async def handle_auto_check_assist(call: ServiceCall) -> None:
        """Read-only readiness diagnostic; never enables Assist or writes API."""
        requested = call.data.get("entry_id")
        entries = hass.config_entries.async_entries(DOMAIN)
        selected = [entry for entry in entries if requested is None or entry.entry_id == requested]
        if not selected:
            raise ValueError("No matching inverter config entry")
        for entry in selected:
            data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if data is not None:
                data["coordinator"].check_assist_ready()

    hass.services.async_register(
        DOMAIN, "auto_check_assist", handle_auto_check_assist,
        schema=vol.Schema({vol.Optional("entry_id"): str}),
    )

    async def _get_api(call: ServiceCall):
        """T11: delegate to the module-level resolver."""
        return _resolve_entry(hass, call)

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
        coordinator.async_set_smart_mode(mode_val)
        _LOGGER.info("Service: smart mode → %s (%d)", mode, mode_val)

    async def handle_force_grid_charge(call: ServiceCall) -> None:
        api, coordinator = await _get_api(call)
        duration = call.data["duration_minutes"]
        _LOGGER.info("Service: force grid charge for %d min", duration)
        # T12 follow-up: the previous implementation sent
        # the commands once and then dropped the request on
        # the floor — the next engine cycle could (and
        # often did) immediately override them. The new
        # behaviour arms a *timed hold* on the coordinator
        # so the engine keeps the inverter on USB+SNU for
        # the whole window. After the deadline the engine
        # resumes normal planning from current data — we
        # never restore a stale mode mechanically.
        ok1 = await api.set_charger_priority("1")  # SNU
        ok2 = await api.set_output_priority("0")  # USB
        if ok1 and ok2:
            now = datetime.now()
            deadline = now + timedelta(minutes=duration)
            coordinator._forced_charge_until = deadline
            _LOGGER.info(
                "Force grid charge started for %d min (until %s)",
                duration,
                deadline.isoformat(),
            )
        else:
            _LOGGER.error("Force grid charge failed")
            # T12: if the ACK is partial, do NOT arm the
            # timed hold. The previous code would have
            # armed nothing *and* reported success; now we
            # fail loudly so the user re-issues the
            # service.
            return

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
        # T13: surface ACK failure rather than logging
        # success regardless of cloud response.
        ok = await api.set_config_item(key, "1" if enable else "0")
        if not ok:
            _LOGGER.error(
                "Service: grid charging → %s: cloud rejected %s=%s",
                "ON" if enable else "OFF",
                key,
                "1" if enable else "0",
            )
            return
        await coordinator.async_request_refresh()
        _LOGGER.info("Service: grid charging → %s", "ON" if enable else "OFF")

    async def handle_set_grid_feed_in(call: ServiceCall) -> None:
        api, coordinator = await _get_api(call)
        enable = call.data["enable"]
        key = "batteryPowerLimitingSetting"
        ok = await api.set_config_item(key, "1" if enable else "0")
        if not ok:
            _LOGGER.error(
                "Service: grid feed-in → %s: cloud rejected %s=%s",
                "ON" if enable else "OFF",
                key,
                "1" if enable else "0",
            )
            return
        await coordinator.async_request_refresh()
        _LOGGER.info("Service: grid feed-in → %s", "ON" if enable else "OFF")

    async def handle_set_backup_mode(call: ServiceCall) -> None:
        api, coordinator = await _get_api(call)
        enable = call.data["enable"]
        target = "2" if enable else "0"
        ok = await api.set_output_priority(target)
        if not ok:
            _LOGGER.error(
                "Service: backup mode → %s: cloud rejected output=%s",
                "ON" if enable else "OFF",
                target,
            )
            return
        await coordinator.async_request_refresh()
        _LOGGER.info("Service: backup mode → %s", "ON" if enable else "OFF")

    async def handle_set_battery_charge_limit(call: ServiceCall) -> None:
        api, coordinator = await _get_api(call)
        percent = int(call.data["percent"])
        key = "batteryChargeLimit"
        ok = await api.set_config_item(key, str(percent))
        if not ok:
            _LOGGER.error(
                "Service: battery charge limit → %d%%: cloud rejected %s=%s",
                percent,
                key,
                percent,
            )
            return
        await coordinator.async_request_refresh()
        _LOGGER.info("Service: battery charge limit → %d%%", percent)

    async def handle_set_grid_charge_power(call: ServiceCall) -> None:
        api, coordinator = await _get_api(call)
        watts = int(call.data["watts"])
        key = "gridConnectedPowers"
        ok = await api.set_config_item(key, str(watts))
        if not ok:
            _LOGGER.error(
                "Service: grid charge power → %d W: cloud rejected %s=%s",
                watts,
                key,
                watts,
            )
            return
        await coordinator.async_request_refresh()
        _LOGGER.info("Service: grid charge power → %d W", watts)

    hass.services.async_register(
        DOMAIN,
        "set_grid_charging",
        handle_set_grid_charging,
        schema=SET_GRID_CHARGING_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "set_grid_feed_in",
        handle_set_grid_feed_in,
        schema=SET_GRID_FEED_IN_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "set_backup_mode",
        handle_set_backup_mode,
        schema=SET_BACKUP_MODE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "set_battery_charge_limit",
        handle_set_battery_charge_limit,
        schema=SET_BATTERY_CHARGE_LIMIT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "set_grid_charge_power",
        handle_set_grid_charge_power,
        schema=SET_GRID_CHARGE_POWER_SCHEMA,
    )

    async def handle_predictive_feedback(call: ServiceCall) -> None:
        """Feedback is scoped to one loaded inverter and never writes its API."""
        requested = call.data.get("entry_id")
        loaded = hass.data.get(DOMAIN, {})
        ids = [entry.entry_id for entry in hass.config_entries.async_entries(DOMAIN)
               if entry.entry_id in loaded and (requested is None or entry.entry_id == requested)]
        if len(ids) != 1:
            raise ValueError("Specify entry_id when multiple inverters are loaded; entry must be loaded")
        coordinator = loaded[ids[0]]["coordinator"]
        coordinator.async_predictive_feedback(
            call.data["action"], call.data.get("duration_min", 30), call.data.get("new_target_soc"))

    hass.services.async_register(
        DOMAIN, "predictive_feedback", handle_predictive_feedback,
        schema=vol.Schema({
            vol.Optional("entry_id"): str,
            vol.Required("action"): vol.In(["approve", "reject", "modify"]),
            vol.Optional("duration_min", default=30): vol.All(int, vol.Range(min=1, max=1440)),
            vol.Optional("new_target_soc"): vol.All(int, vol.Range(min=20, max=100)),
        }),
    )
