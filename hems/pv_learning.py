"""Station training on independent radiation, separate from forecast accuracy.

No HA/network imports. All energies are kWh; all day keys are local ISO dates.
Archive-fit metrics never seed the day-ahead ForecastCalibrator.
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .forecast_calibration import ForecastCalibrator


def finite(value, low=0.0, high=float("inf")):
    try:
        result = float(value)
        return result if math.isfinite(result) and low <= result <= high else None
    except (TypeError, ValueError):
        return None


def timestamp(value):
    """Normalize recorder seconds, websocket milliseconds and ISO timestamps."""
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Statistics timestamps must identify a UTC instant")
    return value.astimezone(timezone.utc)


def day_bounds(day, tz):
    d = date.fromisoformat(day) if isinstance(day, str) else day
    start = datetime.combine(d, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    return start, end


def complete_hourly_days(rows, tz, today, *, field="mean", ceiling=20000):
    """Integrate complete local days, including 23/25-hour DST days.

    Each hourly mean represents one UTC hour. Missing/invalid hours invalidate
    the day; repeated local DST hours remain distinct UTC instants.
    """
    buckets = {}
    for row in rows:
        try:
            ts = timestamp(row.get("start"))
        except (ValueError, TypeError, OverflowError, OSError):
            continue
        day = ts.astimezone(tz).date().isoformat()
        if day >= today.isoformat():
            continue
        value = finite(row.get(field), high=ceiling)
        if value is not None:
            buckets.setdefault(day, {})[ts] = value
    result = {}
    for day, hours in buckets.items():
        start, end = day_bounds(day, tz)
        expected = {start + timedelta(hours=h) for h in range(int((end-start).total_seconds()/3600))}
        if set(hours) == expected:
            result[day] = sum(hours.values()) / 1000.0
    return result


def daily_energy_deltas(rows, unit, tz, today):
    """HA sum is cumulative; hourly statistics sum belongs to interval END.

    Only use explicit local-midnight endpoints with monotonic sums throughout
    a fully covered day. Missing metadata/endpoints falls back to power means.
    """
    scale = {"Wh": .001, "kWh": 1., "MWh": 1000.}.get(unit)
    if scale is None:
        return {}
    endpoints = {}
    for row in rows:
        value = finite(row.get("sum"))
        if value is None:
            continue
        try:
            endpoints[timestamp(row.get("start")) + timedelta(hours=1)] = value * scale
        except (ValueError, TypeError, OverflowError, OSError):
            continue
    days = {ts.astimezone(tz).date().isoformat() for ts in endpoints}
    result = {}
    for day in days:
        if day >= today.isoformat():
            continue
        start, end = day_bounds(day, tz)
        count = int((end-start).total_seconds()/3600)
        points = [endpoints.get(start + timedelta(hours=h)) for h in range(count+1)]
        if any(v is None for v in points):
            continue
        if any(b < a for a, b in zip(points, points[1:])):
            continue
        total = points[-1] - points[0]
        if finite(total, high=500) is not None:
            result[day] = total
    return result


def train_station(actual, radiation):
    """Robust gain, chronological holdout, then refit on all eligible days."""
    pairs = [(day, float(actual[day]), float(radiation[day])) for day in sorted(actual.keys() & radiation.keys())
             if finite(actual[day], high=500) is not None
             and finite(radiation[day], low=.5, high=30) is not None][-90:]
    if len(pairs) < 7:
        return None
    validation = None
    if len(pairs) >= 21:
        split = min(len(pairs)-7, max(14, int(len(pairs)*.8)))
        gain = statistics.median(a/r for _, a, r in pairs[:split])
        errors = [a-r*gain for _, a, r in pairs[split:]]
        validation = {"source": "archive_weather_holdout", "train_days": split,
                      "test_days": len(errors), "train_end": pairs[split-1][0],
                      "test_start": pairs[split][0], "mae_kwh": sum(abs(e) for e in errors)/len(errors),
                      "bias_kwh": sum(errors)/len(errors)}
    gain = statistics.median(a/r for _, a, r in pairs)
    if gain <= 0 or not math.isfinite(gain):
        return None
    return {"gain": gain, "sample_count": len(pairs), "last_day": pairs[-1][0],
            "validation": validation}


class PvLearningState:
    VERSION = 2

    def __init__(self, timezone_name, latitude, longitude):
        self.identity = {"timezone": timezone_name, "latitude": latitude, "longitude": longitude}
        self.snapshots = {}
        self.pairs = {}
        self.radiation = {}
        self.archive_checked_day = None
        self.model = None
        self.calibrator = ForecastCalibrator(max_samples=30, unit="kWh")

    def snapshot(self, day, forecast_kwh, now):
        value = finite(forecast_kwh, high=500)
        if date.fromisoformat(day) <= now.date() or value is None:
            return False
        record = {"forecast_kwh": value, "issued_at": now.isoformat()}
        self.snapshots[day] = record
        self.snapshots = dict(sorted(self.snapshots.items())[-120:])
        return True

    def match(self, actual, now):
        count = 0
        for day, snapshot in sorted(self.snapshots.items()):
            value = finite(actual.get(day), high=500)
            if day in self.pairs or day >= now.date().isoformat() or value is None:
                continue
            self.pairs[day] = {"forecast_kwh": snapshot["forecast_kwh"],
                               "actual_kwh": value, "coverage": 1.0}
            count += 1
        self.pairs = dict(sorted(self.pairs.items())[-30:])
        if count:
            self.calibrator.load_from_list([[p["forecast_kwh"], p["actual_kwh"]] for p in self.pairs.values()])
        return count

    def save(self, path):
        path = Path(path)
        raw = {"version": self.VERSION, "unit": "kWh", **self.identity,
               "snapshots": self.snapshots, "pairs": self.pairs, "radiation": self.radiation,
               "archive_checked_day": self.archive_checked_day, "model": self.model}
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(raw, allow_nan=False), encoding="utf-8")
        temp.replace(path)

    def load(self, path, legacy_path=None):
        path = Path(path)
        if legacy_path and Path(legacy_path).exists():
            legacy = Path(legacy_path)
            backup = legacy.with_suffix(".json.legacy")
            if not backup.exists():
                backup.write_bytes(legacy.read_bytes())
        if not path.exists():
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("version") != self.VERSION or raw.get("unit") != "kWh":
            raise ValueError("Unsupported PV state version/unit")
        if any(raw.get(key) != value for key, value in self.identity.items()):
            raise ValueError("PV state belongs to different coordinates/timezone")
        # Validate everything before modifying the live state.
        snapshots, pairs, radiation = (raw.get(k, {}) for k in ("snapshots", "pairs", "radiation"))
        for day, snap in snapshots.items():
            d = date.fromisoformat(day)
            issued = datetime.fromisoformat(snap["issued_at"])
            if issued.date() >= d or finite(snap["forecast_kwh"], high=500) is None:
                raise ValueError("Invalid or retrospective PV snapshot")
        for day, pair in pairs.items():
            date.fromisoformat(day)
            if day not in snapshots or pair["forecast_kwh"] != snapshots[day]["forecast_kwh"]:
                raise ValueError("PV pair missing its issued forecast")
            if finite(pair["actual_kwh"], high=500) is None or pair["coverage"] != 1:
                raise ValueError("Incomplete PV pair")
        for day, value in radiation.items():
            date.fromisoformat(day)
            if finite(value, high=30) is None:
                raise ValueError("Invalid archive radiation")
        model = raw.get("model")
        if model is not None and (finite(model.get("gain"), low=.000001) is None
                                  or not 7 <= model.get("sample_count", 0) <= 90):
            raise ValueError("Invalid station model")
        checked = raw.get("archive_checked_day")
        if checked is not None:
            date.fromisoformat(checked)
        self.snapshots = {d: {**s, "forecast_kwh": float(s["forecast_kwh"])}
                          for d, s in sorted(snapshots.items())[-120:]}
        self.pairs = {d: {**p, "forecast_kwh": float(p["forecast_kwh"]), "actual_kwh": float(p["actual_kwh"])}
                      for d, p in sorted(pairs.items())[-30:]}
        self.radiation = {d: float(r) for d, r in sorted(radiation.items())[-120:]}
        if model is not None:
            model = {**model, "gain": float(model["gain"])}
        self.model, self.archive_checked_day = model, checked
        self.calibrator.load_from_list([[p["forecast_kwh"], p["actual_kwh"]] for p in self.pairs.values()])
