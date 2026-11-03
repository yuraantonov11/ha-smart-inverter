"""Dated demand forecasting with chronological, out-of-sample comparison.

No weather coordinates, household constants, or control writes live here.
Only complete measured days before the issue date can train or validate.
The candidate uses recency weights and weekday/weekend grouping; it replaces
the existing weekday mean only after beating it and yesterday on held-out days.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from functools import lru_cache

MODEL = "recency_day_type_v1"
MIN_TRAIN_DAYS = 7
MIN_TEST_DAYS = 7


def measured_days(history, issued_on):
    """Canonical, unique past dates; reject filled, partial or invalid rows."""
    rows = {}
    duplicates = set()
    for item in history or ():
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        day, values = item[:2]
        if isinstance(day, datetime) or not isinstance(day, date) or day >= issued_on:
            continue
        if day in rows:
            duplicates.add(day)
        if len(item) >= 3 and item[2]:
            continue
        if not isinstance(values, (list, tuple)) or len(values) != 24:
            continue
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 20000 for v in values):
            continue
        rows[day] = tuple(float(v) for v in values)
    return tuple((d, rows[d]) for d in sorted(rows) if d not in duplicates)[-90:]


def _bucket(rows, target, *, candidate):
    same = [r for r in rows if r[0].weekday() == target.weekday()]
    if len(same) >= 2:
        return same
    if candidate:
        group = [r for r in rows if (r[0].weekday() >= 5) == (target.weekday() >= 5)]
        if len(group) >= 3:
            return group
    return rows


def _predict(rows, target, *, candidate):
    selected = _bucket(rows, target, candidate=candidate)
    if not selected:
        return None
    weights = [2 ** (-(target - day).days / 7) if candidate else 1.0 for day, _ in selected]
    total = sum(weights)
    return tuple(sum(w * values[h] for w, (_, values) in zip(weights, selected)) / total for h in range(24))


def _quantile(values, p):
    values = sorted(values)
    position = (len(values) - 1) * p
    i = int(position)
    j = min(i + 1, len(values) - 1)
    return values[i] + (values[j] - values[i]) * (position - i)


@lru_cache(maxsize=16)
def _evaluate(rows, lead_days):
    """Predict each test date using only earlier dates; never refit its score."""
    errors = []
    for index in range(MIN_TRAIN_DAYS, len(rows)):
        target, actual = rows[index]
        issue_date = target - timedelta(days=lead_days)
        train = tuple(r for r in rows[:index] if r[0] < issue_date)
        if len(train) < MIN_TRAIN_DAYS:
            continue
        yesterday = next((v for d, v in train if d == issue_date - timedelta(days=1)), None)
        if yesterday is None:
            continue  # identical dates and denominator for every compared model
        candidate = _predict(train, target, candidate=True)
        legacy = _predict(train, target, candidate=False)
        errors.append((target, tuple(a - p for a, p in zip(actual, candidate)),
                       tuple(a - p for a, p in zip(actual, legacy)),
                       tuple(a - p for a, p in zip(actual, yesterday))))
    return tuple(errors[-28:])


def forecast_load(history, issued_on, target):
    """Forecast W and an empirical error range, with explicit selection evidence.

    The 10/90 residual endpoints describe historical errors, not a guaranteed
    future coverage probability. Selection and intervals use only past days.
    """
    rows = measured_days(history, issued_on)
    lead_days = (target - issued_on).days
    if lead_days < 0:
        raise ValueError("Cannot issue a forecast for a past date")
    errors = _evaluate(rows, lead_days)
    count = len(errors)
    maes = [sum(abs(e[h]) for row in errors for e in [row[i]] for h in range(24)) / (count * 24)
            if count else None for i in (1, 2, 3)]
    candidate_mae, legacy_mae, yesterday_mae = maes
    selected = bool(count >= MIN_TEST_DAYS and legacy_mae > 0 and yesterday_mae > 0
                    and candidate_mae <= .95 * legacy_mae and candidate_mae <= yesterday_mae)
    model = MODEL if selected else "weekday_mean"
    values = _predict(rows, target, candidate=selected)
    candidate_values = _predict(rows, target, candidate=True)
    error_index = 1 if selected else 2
    bounds = None
    daily_bounds = None
    if count >= MIN_TEST_DAYS and values is not None:
        bounds = tuple((max(0., v + _quantile([r[error_index][h] for r in errors], .1)),
                        max(0., v + _quantile([r[error_index][h] for r in errors], .9)))
                       for h, v in enumerate(values))
        daily_residuals = [sum(row[error_index]) / 1000 for row in errors]
        daily_forecast = sum(values) / 1000
        daily_bounds = (max(0., daily_forecast + _quantile(daily_residuals, .1)),
                        max(0., daily_forecast + _quantile(daily_residuals, .9)))
    return {
        "model": model, "candidate_model": MODEL, "candidate_selected": selected,
        "selection_reason": "validated_improvement" if selected else
            "insufficient_test_days" if count < MIN_TEST_DAYS else "baseline_not_beaten",
        "training_days": len(rows), "validation_days": count,
        "validation_basis": "walk_forward_complete_measured_days",
        "validation_lead_days": lead_days,
        "baseline": "last_complete_day_before_issuance",
        "candidate_mae_w": round(candidate_mae, 1) if count else None,
        "legacy_mae_w": round(legacy_mae, 1) if count else None,
        "yesterday_mae_w": round(yesterday_mae, 1) if count else None,
        "skill_vs_legacy": round(1 - candidate_mae / legacy_mae, 4) if legacy_mae else None,
        "skill_vs_yesterday": round(1 - candidate_mae / yesterday_mae, 4) if yesterday_mae else None,
        "issued_on": issued_on.isoformat(), "target_date": target.isoformat(),
        "last_measured_day": rows[-1][0].isoformat() if rows else None,
        "hourly_w": values, "candidate_hourly_w": candidate_values,
        "hourly_error_range_w": bounds,
        "daily_error_range_kwh": daily_bounds,
        "range_method": "historical_p10_p90_residuals" if bounds else "insufficient_test_days",
        "range_is_probability_guarantee": False,
    }
