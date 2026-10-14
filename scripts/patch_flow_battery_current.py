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
