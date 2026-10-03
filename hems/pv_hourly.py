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
    available_days = len(eligible)
    training_reason = "recent_history"
    ordered = sorted(eligible)
    # A sustained change in generation must not be diluted by an older regime.
    # Compare weather-normalized energy, not fixed dates or peak wattages.
    if len(ordered) >= 8:
        ratios_by_day = {d: days[d] / archive_days[d] for d in ordered
                         if archive_days[d] >= .5}
        if all(d in ratios_by_day for d in ordered[-3:]):
            older = [ratios_by_day[d] for d in ordered[:-3] if d in ratios_by_day]
            if len(older) >= 5:
                baseline = median(older)
                recent = [ratios_by_day[d] for d in ordered[-3:]]
                if baseline > 0 and min(recent) > 2 * baseline and median(recent) > 2.5 * baseline:
                    selected = []
                    for d in reversed(ordered):
                        if ratios_by_day.get(d, 0) <= 2 * baseline:
                            break
                        selected.append(d)
                    eligible = set(selected)
                    training_reason = "sustained_recent_gain_increase"
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
            "cloud_sample_days": len(eligible & cloud_days),
            "available_days": available_days, "training_reason": training_reason}


def validate_hourly_response(power_rows, radiation_rows, tz, today):
    """Walk forward: each tested day is absent from its own training set.

    Archive weather is known after the event. These diagnostics assess station
    response only, not weather forecast accuracy and not live AI confidence.
    """
    days = complete_hourly_days(power_rows, tz, today)
    archive = complete_hourly_days(radiation_rows, tz, today, ceiling=2000)
    cutoff = (today - timedelta(days=14)).isoformat()
    tested = sorted(d for d in days.keys() & archive.keys() if d >= cutoff)
    power, radiation = {}, {}
    for target, rows in ((power, power_rows), (radiation, radiation_rows)):
        for row in rows:
            try:
                ts = timestamp(row.get("start"))
            except (ValueError, TypeError, OverflowError, OSError):
                continue
            value = finite(row.get("mean"), high=20000 if target is power else 2000)
            if value is not None:
                target[ts] = value
    errors, baseline_errors, peaks, test_dates = [], [], [], []
    from datetime import date
    for day in tested:
        test_day = date.fromisoformat(day)
        prior_power = [{"start": t, "mean": v} for t, v in power.items() if t.astimezone(tz).date() < test_day]
        prior_rad = [{"start": t, "mean": v} for t, v in radiation.items() if t.astimezone(tz).date() < test_day]
        model = train_hourly_response(prior_power, prior_rad, tz, test_day)
        if model is None:
            continue
        training_days = sorted(d for d in tested if d < day and archive[d] >= .5 and days[d] >= .1)
        if len(training_days) < 2:
            continue
        baseline = median(days[d]/archive[d] for d in training_days)
        instants = sorted(t for t in power if t.astimezone(tz).date().isoformat() == day and t in radiation)
        if not instants or any(radiation[t] <= 1 and power[t] > 20 for t in instants):
            continue
        predicted = []
        for ts in instants:
            gain = model["gains"][ts.astimezone(tz).hour]
            fc = min(20000, radiation[ts] * (baseline if gain is None else gain))
            predicted.append(fc)
            if radiation[ts] >= 20:  # Daylight MAE, not diluted by night zeros.
                errors.append(abs(fc-power[ts]))
                baseline_errors.append(abs(min(20000, radiation[ts]*baseline)-power[ts]))
        actual_peak = max(range(len(instants)), key=lambda i: power[instants[i]])
        predicted_peak = max(range(len(instants)), key=lambda i: predicted[i])
        peaks.append(abs((instants[actual_peak]-instants[predicted_peak]).total_seconds())/3600)
        test_dates.append(day)
    if not errors:
        return None
    return {"source": "archive_weather_walk_forward", "test_days": len(test_dates),
            "test_dates": test_dates, "daylight_mae_w": round(sum(errors)/len(errors), 2),
            "baseline_daylight_mae_w": round(sum(baseline_errors)/len(baseline_errors), 2),
            "mean_peak_time_error_h": round(sum(peaks)/len(peaks), 2),
            "live_forecast_accuracy": False}
