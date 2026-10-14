"""Bind flow-card current to the inverter's registered sensor, preserving other cards."""

from copy import deepcopy


def patch_flow_battery_current(config: dict, registry: list[dict]) -> tuple[dict, int]:
    patched = deepcopy(config)
    by_id = {item["entity_id"]: item for item in registry}
    changed = 0

    def visit(value):
        nonlocal changed
        if isinstance(value, dict):
            if value.get("type") == "custom:k-flow-card" and not value.get("battery_current"):
                power = by_id.get(value.get("battery_power"))
                if not power or not power.get("config_entry_id"):
                    raise ValueError("Flow battery power has no registered inverter entry")
                candidates = [
                    item["entity_id"] for item in registry
                    if item.get("config_entry_id") == power["config_entry_id"]
                    and item.get("platform") == power.get("platform")
                    and item["entity_id"].startswith("sensor.")
                    and (item.get("unique_id") or "").endswith("_battery_current")
                    and not item.get("disabled_by")
                ]
                if len(candidates) != 1:
                    raise ValueError("Expected one enabled current sensor for the same inverter")
                value["battery_current"] = candidates[0]
                changed += 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(patched)
    return patched, changed


def patch_flow_card_data(config: dict, registry: list[dict], *,
                         grid_power_estimated: bool | None = None,
                         battery_power_estimated: bool | None = None) -> tuple[dict, int]:
    """Fill missing sensor bindings for the same device; retain custom bindings."""
    patched = deepcopy(config)
    by_id = {item["entity_id"]: item for item in registry}
    changed = 0
    bindings = {
        "battery_current": "battery_current", "grid_voltage": "grid_voltage",
        "inv_temp": "inverter_temperature", "inverter_load_percentage": "load_percentage",
        "inverter_rated_power": "rated_active_power",
    }

    def visit(value):
        nonlocal changed
        if isinstance(value, dict):
            if value.get("type") == "custom:k-flow-card":
                before = deepcopy(value)
                power = by_id.get(value.get("battery_power"))
                if not power or not power.get("config_entry_id"):
                    raise ValueError("Flow card has no registered inverter entry")
                device_entities = [item for item in registry
                                   if item.get("config_entry_id") == power["config_entry_id"]
                                   and item.get("device_id") == power.get("device_id")
                                   and item.get("platform") == power.get("platform")
                                   and not item.get("disabled_by")]
                for setting, key in bindings.items():
                    if value.get(setting):
                        continue
                    candidates = [item["entity_id"] for item in device_entities
                                  if item["entity_id"].startswith("sensor.")
                                  and (item.get("unique_id") or "").endswith("_" + key)]
                    if len(candidates) > 1:
                        raise ValueError("Ambiguous flow sensor: " + key)
                    if candidates:
                        value[setting] = candidates[0]
                for setting, flag in [("grid_power_estimated", grid_power_estimated),
                                      ("battery_power_estimated", battery_power_estimated)]:
                    if flag is not None:
                        value[setting] = flag
                changed += value != before
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(patched)
    return patched, changed
