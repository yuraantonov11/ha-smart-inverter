"""Explicit battery-side power constraints for predictive planning.

Instantaneous current is not a current limit. Missing settings stay unknown;
they must not become a guessed inverter rating or an unlimited charger.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from collections.abc import Mapping

POWER_LIMIT_FIELDS = (
    "max_charge_power_w", "max_grid_charge_power_w", "max_discharge_power_w",
)


def nonnegative_number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and 0 <= number <= 100_000 else None


def read_charge_current(settings, *keys):
    """A configuration readback, never a UI default or instantaneous current."""
    if not isinstance(settings, Mapping):
        return None
    for key in keys:
        item = settings.get(key)
        if isinstance(item, Mapping):
            if item.get("unit") not in (None, "", "A", "a", "Amp", "amp"):
                continue
            item = item.get("value")
        value = nonnegative_number(item)
        if value is not None and value <= 200:
            return value
    return None


def record_settings_snapshot(owner, settings, received_at):
    """Keep the last non-empty response and its own receipt timestamp together."""
    if not isinstance(settings, Mapping) or not settings:
        return False
    owner._device_settings_snapshot = dict(settings)
    owner._device_settings_received_at = received_at if isinstance(received_at, datetime) else None
    return True


def settings_readback_fresh(received_at, now, max_age_s=180):
    """Never adopt an unidentifiable, future-dated or expired config snapshot."""
    if not isinstance(received_at, datetime) or not isinstance(now, datetime):
        return False
    if received_at.tzinfo is None or now.tzinfo is None:
        return False
    elapsed = (now.astimezone(timezone.utc) - received_at.astimezone(timezone.utc)).total_seconds()
    return 0 <= elapsed <= max_age_s


def limits_known(inputs) -> bool:
    """Zero is a known disabled capability, None is an unknown capability."""
    return all(nonnegative_number(getattr(inputs, key, None)) is not None
               for key in POWER_LIMIT_FIELDS)


def resolve_battery_power_limits(options, settings, nominal_voltage_v):
    """Take the tightest configured/read-back limit, in battery-side DC W.

    Only read-back current settings with ampere units (or no unit) are used.
    Operator W options may tighten them. Discharge needs an explicit option;
    an AC nameplate alone cannot establish a battery/BMS discharge limit.
    """
    options = options if isinstance(options, Mapping) else {}
    settings = settings if isinstance(settings, Mapping) else {}
    voltage = nonnegative_number(nominal_voltage_v)
    if voltage is not None and not 1 <= voltage <= 1000:
        voltage = None

    def read_current(*keys):
        values = []
        for key in keys:
            item = settings.get(key)
            if isinstance(item, Mapping):
                if item.get("unit") not in (None, "", "A", "a", "Amp", "amp"):
                    continue
                item = item.get("value")
            value = nonnegative_number(item)
            if value is not None and value <= 1000 and voltage is not None:
                values.append(value * voltage)
        return values

    result = {}
    readbacks = {
        "max_charge_power_w": read_current("setMaxChargingCurrent", "maxChargingCurrent"),
        "max_grid_charge_power_w": read_current("setUtilityMaxChargingCurrent", "maxUtilityChargingCurrent"),
        "max_discharge_power_w": [],
    }
    for field in POWER_LIMIT_FIELDS:
        candidates = readbacks[field]
        configured = nonnegative_number(options.get(field))
        if configured is not None:
            candidates.append(configured)
        result[field] = min(candidates) if candidates else None
    # Utility charging is always bounded by the total charge capability.
    total = result["max_charge_power_w"]
    grid = result["max_grid_charge_power_w"]
    if total is not None and grid is not None:
        result["max_grid_charge_power_w"] = min(total, grid)
    return result
