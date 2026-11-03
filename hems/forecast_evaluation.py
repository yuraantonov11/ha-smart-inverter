"""Read-only forecast scores on dated, already observed pairs.

These scores never modify forecasts or train on evaluation outcomes. Baseline
skill is calculated on the same dates for both predictors; missing days cannot
quietly change one denominator. WAPE is an error ratio, not an accuracy percent.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from .battery_limits import nonnegative_number


def evaluate_daily_forecasts(pairs, snapshots=None, actual_history=None, *, timezone_name=None):
    snapshots = snapshots or {}
    actual_history = actual_history or pairs
    site_timezone = ZoneInfo(timezone_name) if timezone_name else None
    rows = []
    for day, pair in sorted(pairs.items()):
        try:
            target = date.fromisoformat(day)
        except (TypeError, ValueError):
            continue
        forecast = nonnegative_number(pair.get("forecast_kwh"))
        actual = nonnegative_number(pair.get("actual_kwh"))
        if forecast is None or actual is None:
            continue
        issued = snapshots.get(day, {}).get("issued_at") or pair.get("captured_at")
        lead_days = None
        try:
            issued_at = datetime.fromisoformat(issued)
            if issued_at.tzinfo is not None:
                if site_timezone is not None:
                    issued_at = issued_at.astimezone(site_timezone)
                lead_days = (target - issued_at.date()).days
        except (TypeError, ValueError):
            pass
        previous = actual_history.get((target - timedelta(days=1)).isoformat(), {})
        baseline = nonnegative_number(previous.get("actual_kwh"))
        rows.append((forecast, actual, baseline, lead_days))

    def score(selected):
        n = len(selected)
        if not n:
            return {"samples": 0, "mae_kwh": None, "bias_kwh": None,
                    "wape_percent": None, "baseline_samples": 0,
                    "baseline_mae_kwh": None, "model_mae_on_baseline_dates_kwh": None,
                    "skill_vs_yesterday": None}
        errors = [abs(fc - ac) for fc, ac, _, _ in selected]
        energy = sum(ac for _, ac, _, _ in selected)
        matched = [(fc, ac, base) for fc, ac, base, _ in selected if base is not None]
        baseline_mae = sum(abs(base - ac) for _, ac, base in matched) / len(matched) if matched else None
        model_mae = sum(abs(fc - ac) for fc, ac, _ in matched) / len(matched) if matched else None
        skill = 1 - model_mae / baseline_mae if baseline_mae is not None and baseline_mae > 0 else None
        return {"samples": n, "mae_kwh": round(sum(errors)/n, 4),
                "bias_kwh": round(sum(ac-fc for fc, ac, _, _ in selected)/n, 4),
                "wape_percent": round(100*sum(errors)/energy, 2) if energy > 0 else None,
                "baseline_samples": len(matched),
                "baseline_mae_kwh": round(baseline_mae, 4) if baseline_mae is not None else None,
                "model_mae_on_baseline_dates_kwh": round(model_mae, 4) if model_mae is not None else None,
                "skill_vs_yesterday": round(skill, 4) if skill is not None else None}

    result = score(rows)
    result["by_lead_days"] = {
        str(lead) if lead is not None else "unknown": score([r for r in rows if r[3] == lead])
        for lead in sorted({r[3] for r in rows}, key=lambda value: -1 if value is None else value)
    }
    result["basis"] = "stored_forecast_actual_pairs"
    result["baseline"] = "previous_calendar_day_actual"
    return result
