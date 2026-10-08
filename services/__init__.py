"""Services for Smart Solar Inverter — register custom services."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import os
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ServiceValidationError,
)

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


def _validate_days_of_week(
    value: Any,
) -> list[int]:
    """T25 round 4: validate
    that ``days_of_week``
    is a non-empty list of
    integers in the range
    1..7 (ISO weekday
    numbering). Reject empty
    lists, non-int entries,
    and out-of-range values
    with a
    ``ServiceValidationError``
    that voluptuous will
    propagate.
    """
    if not isinstance(value, list):
        raise ServiceValidationError(
            "days_of_week must be a list of "
            "integers in 1..7"
        )
    if not value:
        raise ServiceValidationError(
            "days_of_week must be non-empty"
        )
    out: list[int] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(
            v, int
        ):
            raise ServiceValidationError(
                f"days_of_week entry {v!r} must "
                "be an integer in 1..7"
            )
        if v < 1 or v > 7:
            raise ServiceValidationError(
                f"days_of_week entry {v} out of "
                "range 1..7"
            )
        out.append(int(v))
    return out


def _validate_priority(value: Any) -> int:
    """T25 round 4: validate
    that ``priority`` is an
    integer in 1..10. Reject
    zero, eleven, non-int.
    """
    if isinstance(value, bool) or not isinstance(
        value, int
    ):
        raise ServiceValidationError(
            f"priority {value!r} must be an "
            "integer in 1..10"
        )
    if value < 1 or value > 10:
        raise ServiceValidationError(
            f"priority {value} out of range 1..10"
        )
    return int(value)


async def _add_schedule_rule_impl(
    call: ServiceCall,
    coordinator: Any,
) -> None:
    """Round 4 (T25): atomic
    add for schedule rules.

    Snapshots the in-memory
    registry, mutates it,
    and calls the
    coordinator's persist
    helper. On persist
    failure the registry is
    restored from the
    snapshot and a
    ``ServiceValidationError``
    is raised so the HA UI
    surfaces the error and
    the entry options stay
    unchanged.
    """
    from ..hems.schedule_rules import (
        ScheduleRule,
    )
    snapshot = (
        coordinator.schedule_rules.save_to_dict()
    )
    rule = ScheduleRule(
        name=call.data.get("name", ""),
        days_of_week=call.data.get(
            "days_of_week", [1, 2, 3, 4, 5]
        ),
        start_hour=call.data.get(
            "start_hour", 0
        ),
        start_minute=call.data.get(
            "start_minute", 0
        ),
        end_hour=call.data.get(
            "end_hour", 23
        ),
        end_minute=call.data.get(
            "end_minute", 0
        ),
        mode=_MODE_VALUE.get(
            call.data.get(
                "mode", "adaptive"
            ),
            0,
        ),
        enabled=call.data.get(
            "enabled", True
        ),
        priority=call.data.get(
            "priority", 5
        ),
    )
    coordinator.schedule_rules.add_rule(rule)
    ok = coordinator._persist_schedule_rules()
    if not ok:
        # Rollback the
        # in-memory
        # mutation. The
        # entry.options
        # were NOT
        # touched (persist
        # only writes when
        # it succeeds).
        coordinator.schedule_rules.load_from_dict(
            snapshot
        )
        _LOGGER.error(
            "Service: failed to persist schedule "
            "rule '%s'; in-memory registry rolled "
            "back to snapshot.",
            rule.name,
        )
        raise ServiceValidationError(
            "Failed to persist schedule rule "
            f"'{rule.name}'; in-memory registry "
            "restored from snapshot."
        )
    _LOGGER.info(
        "Service: added schedule rule '%s'",
        rule.name,
    )


async def _delete_schedule_rule_impl(
    call: ServiceCall,
    coordinator: Any,
) -> None:
    """Round 4 (T25): atomic
    delete for schedule rules.
    Snapshots the in-memory
    registry, deletes the
    requested rule, and
    calls the coordinator's
    persist helper. On
    persist failure the
    registry is restored
    from the snapshot.
    """
    snapshot = (
        coordinator.schedule_rules.save_to_dict()
    )
    rule_id = call.data["rule_id"]
    coordinator.schedule_rules.delete_rule(rule_id)
    ok = coordinator._persist_schedule_rules()
    if not ok:
        coordinator.schedule_rules.load_from_dict(
            snapshot
        )
        _LOGGER.error(
            "Service: failed to persist schedule "
            "rule delete %s; in-memory registry "
            "rolled back to snapshot.",
            rule_id,
        )
        raise ServiceValidationError(
            "Failed to persist schedule rule "
            f"deletion (rule_id={rule_id}); "
            "in-memory registry restored from "
            "snapshot."
        )
    _LOGGER.info(
        "Service: deleted schedule rule %s",
        rule_id,
    )


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
        """Arm a timed hold for ``force_grid_charge``.

        T12 follow-up (review ``c056341``): the previous
        implementation sent the USB+SNU commands directly
        *before* the coordinator had a chance to inspect
        the guards. The very first command of a
        ``force_grid_charge`` therefore bypassed the
        ``soc_unknown`` gate, the manual-override hold,
        the circuit breaker, and any other safety check
        the coordinator would normally run. The fix is
        to make this service a *pure timer arming* call.
        It does **no** hardware writes. The actual writes
        happen on the *next* coordinator cycle, where the
        same ``_evaluation_hold`` that protects every
        other engine decision is consulted. If a hold is
        active, the cycle logs the reason, the timer
        keeps running, and the next cycle tries again —
        the user's request is honoured as soon as the
        safety condition clears, but never before.

        Three pieces of validation still happen on the
        service side, *before* the timer is armed:

        1. The targeted config entry is resolved via
           ``_resolve_entry`` (T11). Without a loaded
           inverter the service raises.
        2. ``hems_auto_mode`` must be True. The user-facing
           HEMS Auto switch is the only user-visible
           control that gates automatic commands;
           turning it off means "I want to drive the
           inverter myself". ``force_grid_charge`` is
           still allowed but the *engine* will not write
           while ``hems_auto_mode`` is False, and a
           timer armed under that condition would never
           fire. We refuse to arm it up front so the
           user gets an immediate error.
        3. ``hems_enabled`` is also required for the
           same reason — a coordinator that is in
           monitor-only mode will never dispatch.

        The duration is also clamped to the same range the
        service schema already advertises (5–480 min).
        """
        api, coordinator = await _get_api(call)
        duration = call.data["duration_minutes"]
        # Boundary check: the schema already coerces
        # ``duration_minutes`` to ``vol.Range(min=5,
        # max=480)``, but a hostile caller can still send
        # raw ``call.data`` through. Defensive check.
        if not 5 <= int(duration) <= 480:
            raise ValueError(
                f"duration_minutes must be 5..480 (got {duration!r})"
            )
        # Safety: the HEMS Auto switch must be on. If
        # the user disabled HEMS, ``_evaluation_hold``
        # will refuse the write anyway — but we want
        # the user to learn *now*, not after a 60-min
        # timer that will never fire.
        if not getattr(coordinator, "hems_enabled", True):
            raise ValueError(
                "force_grid_charge requires HEMS Auto to be on"
            )
        if not getattr(coordinator, "hems_auto_mode", True):
            raise ValueError(
                "force_grid_charge requires HEMS Auto to be on"
            )
        now = datetime.now()
        deadline = now + timedelta(minutes=int(duration))
        # T12: this assignment is the *only* state
        # change the service makes. No ``api.set_*`` call
        # happens here. The coordinator's next cycle
        # reads ``_forced_charge_until``, runs the same
        # guards the engine would normally run, and
        # dispatches the forced USB+SNU decision only
        # when those guards pass.
        coordinator._forced_charge_until = deadline
        _LOGGER.info(
            "Service: force_grid_charge armed for %d min "
            "(deadline %s); coordinator will dispatch after guards",
            int(duration),
            deadline.isoformat(),
        )

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

    # T25 round 3:
    # ``services/control.py``
    # (a legacy *second*
    # registry) defined
    # ``add_schedule_rule``
    # and
    # ``delete_schedule_rule``
    # handlers, but that
    # module was never
    # imported by
    # ``__init__.py`` — so
    # the handlers were
    # dead code and the
    # REST service list
    # did not include
    # them. We now
    # register the
    # schedule handlers
    # in this *active*
    # registry and route
    # them through
    # ``_resolve_entry``
    # so multi-entry
    # installations are
    # isolated.
    async def handle_add_schedule_rule(call: ServiceCall) -> None:
        """Add a schedule
        rule via service
        call.

        T25 round 3: the
        only ownership path
        for
        ``coordinator.schedule_rules``
        is the coordinator
        itself. This
        handler is a thin
        façade that
        constructs the
        ``ScheduleRule``,
        delegates to
        ``add_rule``, and
        persists the
        registry through
        the coordinator's
        ``_persist_schedule_rules``
        helper. We use
        ``_resolve_entry``
        so multi-entry
        installations do
        not race each
        other."""
        api, coordinator = await _get_api(call)
        from ..hems.schedule_rules import (
            ScheduleRule,
        )
        rule = ScheduleRule(
            name=call.data.get("name", ""),
            days_of_week=call.data.get(
                "days_of_week", [1, 2, 3, 4, 5]
            ),
            start_hour=call.data.get(
                "start_hour", 0
            ),
            start_minute=call.data.get(
                "start_minute", 0
            ),
            end_hour=call.data.get(
                "end_hour", 23
            ),
            end_minute=call.data.get(
                "end_minute", 0
            ),
            mode=_MODE_VALUE.get(
                call.data.get(
                    "mode", "adaptive"
                ),
                0,
            ),
            enabled=call.data.get(
                "enabled", True
            ),
            priority=call.data.get(
                "priority", 5
            ),
        )
        coordinator.schedule_rules.add_rule(rule)
        # T25: persist the
        # registry so the
        # new rule
        # survives an HA
        # restart.
        ok = coordinator._persist_schedule_rules()
        if not ok:
            _LOGGER.error(
                "Service: failed to persist "
                "schedule rule '%s'",
                rule.name,
            )
            raise ValueError(
                "Failed to persist schedule rule"
            )
        _LOGGER.info(
            "Service: added schedule rule '%s'",
            rule.name,
        )

    async def handle_delete_schedule_rule(
        call: ServiceCall
    ) -> None:
        """Delete a schedule
        rule via service
        call.

        T25 round 3:
        identical
        routing /
        persistence to
        ``handle_add_schedule_rule``
        so the
        coordinator
        remains the
        sole write
        owner of the
        schedule
        registry."""
        api, coordinator = await _get_api(call)
        rule_id = call.data["rule_id"]
        coordinator.schedule_rules.delete_rule(rule_id)
        ok = coordinator._persist_schedule_rules()
        if not ok:
            _LOGGER.error(
                "Service: failed to persist "
                "schedule rule delete %s",
                rule_id,
            )
            raise ValueError(
                "Failed to persist "
                "schedule rule deletion"
            )
        _LOGGER.info(
            "Service: deleted schedule rule %s",
            rule_id,
        )

    hass.services.async_register(
        DOMAIN,
        "add_schedule_rule",
        handle_add_schedule_rule,
        schema=vol.Schema({
            vol.Optional("entry_id"): str,
            vol.Required("name"): str,
            vol.Optional(
                "days_of_week",
                default=[1, 2, 3, 4, 5],
            ): _validate_days_of_week,
            vol.Optional(
                "start_hour", default=0
            ): vol.All(int, vol.Range(min=0, max=23)),
            vol.Optional(
                "start_minute", default=0
            ): vol.All(int, vol.Range(min=0, max=59)),
            vol.Optional(
                "end_hour", default=23
            ): vol.All(int, vol.Range(min=0, max=23)),
            vol.Optional(
                "end_minute", default=0
            ): vol.All(int, vol.Range(min=0, max=59)),
            vol.Optional(
                "mode", default="adaptive"
            ): vol.In(["adaptive", "arbitrage", "storm"]),
            vol.Optional(
                "enabled", default=True
            ): bool,
            vol.Optional(
                "priority", default=5
            ): _validate_priority,
        }),
    )
    hass.services.async_register(
        DOMAIN,
        "delete_schedule_rule",
        handle_delete_schedule_rule,
        schema=vol.Schema({
            vol.Optional("entry_id"): str,
            vol.Required("rule_id"): str,
        }),
    )

    async def handle_migrate_dashboard(
        call: ServiceCall
    ) -> None:
        """One-shot opt-in to overwrite
        the Smart Solar dashboard.

        Audit T23 round 6 (R6.2):
        a documented way for the
        user to enable a fresh
        install of the AI view.
        Without this service the
        user has no path to opt
        in — the integration's
        ``async_setup_entry``
        skips dashboard generation
        when the file exists, so
        the existing user edit is
        preserved but the new AI
        view never lands.

        Usage::

            action: powmr_inverter.migrate_dashboard
            data:
              entry_id: 01M3XWJ8DRYDQC8A0NCPRVB53N  # optional
              confirm: true  # required safeguard

        The handler:
          * requires ``confirm=true``
            in the call data so
            accidental triggers
            do not overwrite the
            dashboard;
          * sets
            ``hass.data[DOMAIN][entry_id]
            ["dashboard_migration_opt_in"] = True``
            for the matching entry
            only;
          * invokes the integration's
            dashboard installer via
            the already-loaded module
            object — we avoid the
            circular
            ``__init__ ↔ services``
            import by going through
            ``sys.modules``;
          * the registration helper
            resets the flag back
            to False after a
            successful migration
            (one-shot).
        """
        # Audit T22 round 7 (R7.5):
        # the handler MUST use
        # ``try/finally`` to reset
        # the opt-in flag on every
        # exit path. The previous
        # implementation logged
        # ``migration complete``
        # AFTER the helper raised,
        # which left the flag
        # stuck at ``True`` and
        # silently clobbered
        # future dashboards. We
        # also raise on ambiguity
        # so the user cannot
        # trigger a multi-entry
        # migration by accident.
        from homeassistant.exceptions import (
            ServiceValidationError,
        )
        confirm = bool(call.data.get("confirm", False))
        if not confirm:
            raise ServiceValidationError(
                "migrate_dashboard requires "
                "confirm=true (one-shot opt-in)."
            )
        requested = call.data.get("entry_id")
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError(
                "No powmr_inverter config entries loaded."
            )
        if requested is None and len(entries) > 1:
            raise ServiceValidationError(
                "Multiple config entries loaded; "
                "specify entry_id to disambiguate. "
                f"Loaded ids: {sorted(e.entry_id for e in entries)}"
            )
        target_entries = [
            e for e in entries
            if requested is None or e.entry_id == requested
        ]
        if not target_entries:
            raise ServiceValidationError(
                f"Unknown entry_id={requested!r}; "
                f"loaded ids: {sorted(e.entry_id for e in entries)}"
            )
        # Resolve the integration
        # module via sys.modules to
        # avoid the circular import
        # ``__init__ ↔ services``.
        import sys as _sys
        mod = _sys.modules.get(
            "custom_components.powmr_inverter"
        )
        if mod is None:
            raise ServiceValidationError(
                "powmr_inverter module not loaded; "
                "this service is unavailable until "
                "the integration is set up."
            )
        auto_install = getattr(
            mod, "_auto_install_dashboard", None
        )
        if auto_install is None:
            raise ServiceValidationError(
                "_auto_install_dashboard missing in "
                "powmr_inverter module; cannot migrate."
            )
        loaded = hass.data.setdefault(DOMAIN, {})
        # Track per-entry results so
        # the bus event carries an
        # honest outcome.
        results: list[dict] = []
        for entry in target_entries:
            bundle = loaded.setdefault(entry.entry_id, {})
            # Audit R7.5: the
            # opt-in flag MUST
            # reset to False on
            # every exit path,
            # including failures.
            bundle["dashboard_migration_opt_in"] = True
            entry_result = {
                "entry_id": entry.entry_id,
                "ok": False,
                "error": None,
            }
            try:
                await auto_install(hass, entry)
                entry_result["ok"] = True
                backup_path = os.path.join(
                    hass.config.config_dir,
                    ".storage",
                    "lovelace.powmr_energy.bak",
                )
                _LOGGER.info(
                    "Dashboard migration complete for entry=%s; "
                    "backup at %s; opt-in flag reset.",
                    entry.entry_id,
                    backup_path,
                )
            except Exception as exc:
                # Audit R7.5:
                # propagate the
                # failure to the
                # bus so the
                # dashboard
                # caller can
                # surface it. The
                # flag is reset in
                # finally.
                entry_result["error"] = str(exc)
                _LOGGER.error(
                    "Dashboard migration FAILED for entry=%s: %s",
                    entry.entry_id, exc,
                )
            finally:
                # Audit R7.5: the
                # flag MUST reset
                # on every exit
                # path — success
                # AND failure. The
                # audit rejects any
                # implementation
                # that only resets
                # on success.
                bundle["dashboard_migration_opt_in"] = False
            results.append(entry_result)
        # Emit a bus event so the
        # caller can subscribe to
        # the outcome. The audit
        # requires a clear
        # success/failure signal
        # — ``hass.bus.async_fire``
        # is the supported HA
        # API for service outcome
        # notifications.
        hass.bus.async_fire(
            f"{DOMAIN}_dashboard_migration_complete",
            {"results": results},
        )
        if not all(r["ok"] for r in results):
            # At least one entry
            # failed; raise so the
            # HA UI surfaces the
            # error and the next
            # reload does NOT
            # silently re-run the
            # partial migration.
            failed_ids = [
                    r["entry_id"] for r in results if not r["ok"]
                ]
            raise ServiceValidationError(
                "Dashboard migration failed for entries: "
                f"{failed_ids}"
            )

    hass.services.async_register(
        DOMAIN,
        "migrate_dashboard",
        handle_migrate_dashboard,
        schema=vol.Schema({
            vol.Optional("entry_id"): str,
            vol.Required("confirm"): bool,
        }),
    )
