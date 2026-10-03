"""Validate measured cloud PV facts; never synthesize forecast samples."""
from datetime import date, datetime, timedelta, timezone
import math
import json
from pathlib import Path


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


def measured_pv_hours(properties, day, tz):
    """Mean of two real half-hour samples; never an energy/calibration fact.

    Bare server wall-clock timestamps are interpreted in HA's site timezone.
    An ambiguous DST timestamp without offset is rejected rather than guessed.
    Missing or conflicting samples invalidate their hour, including night.
    """
    from .pv_learning import timestamp
    samples, conflicts = {}, set()
    for group in properties if isinstance(properties, list) else []:
        if not isinstance(group, dict):
            continue
        prop = group.get("property", {})
        if not isinstance(prop, dict) or prop.get("key") != "generationPower":
            continue
        scale = {"kW": 1000., "W": 1.}.get(prop.get("unit"))
        if scale is None:
            continue
        points = group.get("timePoints", [])
        for point in points if isinstance(points, list) else []:
            if not isinstance(point, dict) or point.get("isRealValue") is not True:
                continue
            try:
                ts = datetime.fromisoformat(point["time"])
                if ts.tzinfo is None:
                    a, b = ts.replace(tzinfo=tz, fold=0), ts.replace(tzinfo=tz, fold=1)
                    if a.utcoffset() != b.utcoffset():
                        continue
                    ts = a
                    if ts.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != ts.replace(tzinfo=None):
                        continue
                if ts.astimezone(tz).date() != day or ts.minute not in (0, 30) or ts.second or ts.microsecond:
                    continue
                value = point["value"]
                if isinstance(value, bool):
                    continue
                value = float(value) * scale
                if not math.isfinite(value) or not 0 <= value <= 20000:
                    continue
                instant = timestamp(ts)
                if instant in samples and samples[instant] != value:
                    conflicts.add(instant)
                samples[instant] = value
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
    rows = []
    for instant in sorted(samples):
        if instant.astimezone(tz).minute != 0:
            continue
        half = instant + timedelta(minutes=30)
        if half in samples and instant not in conflicts and half not in conflicts:
            rows.append({"start": instant.timestamp(), "mean": (samples[instant]+samples[half])/2,
                         "source": "cloud_half_hour_samples"})
    return rows


class CloudHourlyHistory:
    """Per-entry bounded cache of validated cloud power samples."""
    def __init__(self, identity):
        self.identity = identity
        self.days = {}

    def load(self, path):
        path = Path(path)
        if not path.exists():
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("version") != 1 or raw.get("identity") != self.identity:
            raise ValueError("Cloud hourly cache identity/version mismatch")
        from .pv_learning import finite, timestamp
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(self.identity["timezone"])
        for day, rows in raw.get("days", {}).items():
            date.fromisoformat(day)
            if not isinstance(rows, list) or len(rows) > 25:
                raise ValueError("Invalid hourly cloud rows")
            for row in rows:
                instant = timestamp(row["start"])
                if (instant.astimezone(tz).date().isoformat() != day
                        or row.get("source") != "cloud_half_hour_samples"
                        or finite(row.get("mean"), high=20000) is None):
                    raise ValueError("Invalid hourly cloud power")
        self.days = raw.get("days", {})

    def save(self, path):
        path = Path(path)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps({"version": 1, "identity": self.identity, "days": self.days},
                                   allow_nan=False), encoding="utf-8")
        temp.replace(path)
