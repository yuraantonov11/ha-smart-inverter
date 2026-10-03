"""Validate measured cloud PV facts; never synthesize forecast samples."""
from datetime import date
import math


def measured_pv_days(properties, start, end):
    """Only explicit real kWh measurements for completed requested dates."""
    days, conflicts = {}, set()
    if not isinstance(properties, list):
        return days
    for group in properties:
        if not isinstance(group, dict):
            continue
        prop = group.get("property", {})
        if not isinstance(prop, dict) or prop.get("key") != "pvGeneratedEnergy" or prop.get("unit") != "kWh":
            continue
        points = group.get("timePoints", [])
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, dict) or point.get("isRealValue") is not True:
                continue
            try:
                day = date.fromisoformat(point["time"])
                value = point["value"]
                if isinstance(value, bool):
                    continue
                value = float(value)
                if not math.isfinite(value) or not 0 <= value <= 500 or not start <= day <= end:
                    continue
                key = day.isoformat()
                if key in days and days[key] != value:
                    conflicts.add(key)
                days[key] = value
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
    return {key: value for key, value in days.items() if key not in conflicts}
