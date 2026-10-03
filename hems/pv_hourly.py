"""Recent station response by local hour, independent of forecast calibration.

Radiation timestamps use the same convention as ForecastService. This is an
empirical predictor for the corresponding recorder hour, not panel geometry.
Only complete measured days enter the fit; no filled history values are used.
"""
from datetime import timedelta
from statistics import median

from .pv_learning import complete_hourly_days, finite, timestamp


def train_hourly_response(power_rows, radiation_rows, tz, today):
    """Fit provisional hour gains from at least two distinct recent days.

    Two days permit a useful provisional shape, but do not establish forecast
    accuracy. Live calibration samples/confidence are never modified here.
    Positive PV during archive night rejects the entire day (stale telemetry).
    """
    radiation = {}
    for row in radiation_rows:
        try:
            instant = timestamp(row.get("start"))
        except (ValueError, TypeError, OverflowError, OSError):
            continue
        value = finite(row.get("mean"), high=2000)
        if value is not None:
            radiation[instant] = value
    days = complete_hourly_days(power_rows, tz, today)
    archive_days = complete_hourly_days(radiation_rows, tz, today, ceiling=2000)
    cutoff = (today - timedelta(days=14)).isoformat()
    eligible = {d for d in days.keys() & archive_days.keys()
                if d >= cutoff and days[d] >= .1}
    hours = {}
    for row in power_rows:
        try:
            instant = timestamp(row.get("start"))
        except (ValueError, TypeError, OverflowError, OSError):
            continue
        day = instant.astimezone(tz).date().isoformat()
        value = finite(row.get("mean"), high=20000)
        if day in eligible and value is not None and instant in radiation:
            hours.setdefault(day, {})[instant] = value
    rejected = sorted(d for d, values in hours.items()
                      if any(radiation[t] <= 1 and p > 20 for t, p in values.items()))
    eligible -= set(rejected)
    if len(eligible) < 2:
        return None
    ratios = [{} for _ in range(24)]
    for day in sorted(eligible):
        for instant, power in hours[day].items():
            rad = radiation[instant]
            if rad >= 20:
                # Repeated DST hour remains one day's evidence for this hour.
                ratios[instant.astimezone(tz).hour].setdefault(day, []).append(power / rad)
    gains = [median([median(v) for v in values.values()]) if len(values) >= 2 else None
             for values in ratios]
    if not any(g is not None for g in gains):
        return None
    cloud_days = set()
    for row in power_rows:
        if row.get("source") == "cloud_half_hour_samples":
            cloud_days.add(timestamp(row["start"]).astimezone(tz).date().isoformat())
    return {"gains": gains, "hour_samples": [len(v) for v in ratios],
            "sample_days": len(eligible), "last_day": max(eligible),
            "rejected_days": rejected, "provisional": len(eligible) < 7,
            "cloud_sample_days": len(eligible & cloud_days)}
