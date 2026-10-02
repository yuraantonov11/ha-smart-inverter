"""Build hourly load history matrix for HEMS planning.

Pure-stdlib. No Home Assistant imports. Stable contract used by forecast
calibration and predictive layers.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Iterable


_MAX_VALID_W = 20000.0
_MIN_HOURS = 18


def _is_valid_w(w) -> bool:
    if w is None:
        return False
    try:
        v = float(w)
    except (TypeError, ValueError):
        return False
    if math.isnan(v) or math.isinf(v):
        return False
    if v < 0 or v > _MAX_VALID_W:
        return False
    return True


def build_hourly_load_matrix(samples, now, days: int = 7):
    """Aggregate (timestamp, watts) samples into a days x 24 load matrix.

    Returns day rows ordered OLDEST first, MOST RECENT last. Only COMPLETE
    past calendar days (strictly before now.date()) are considered. A day
    is included only when at least 18 of its 24 hours have data; missing
    hours inside an included row are filled with that row's known-hour
    average. At most `days` most-recent complete days are returned.
    """
    today = now.date()
    sums: dict[date, list[float]] = {}
    counts: dict[date, list[int]] = {}
    # keyed by (date, hour) for efficient bucketing
    for ts, w in samples:
        if not _is_valid_w(w):
            continue
        d = ts.date()
        if d >= today:
            continue
        h = ts.hour
        if d not in sums:
            sums[d] = [0.0] * 24
            counts[d] = [0] * 24
        sums[d][h] += float(w)
        counts[d][h] += 1

    rows: list[tuple[date, list[float]]] = []
    for d, hour_counts in counts.items():
        known_hours = sum(1 for c in hour_counts if c > 0)
        if known_hours < _MIN_HOURS:
            continue
        row = [0.0] * 24
        for h in range(24):
            if hour_counts[h] > 0:
                row[h] = sums[d][h] / hour_counts[h]
        known_vals = [v for v, c in zip(row, hour_counts) if c > 0]
        fill = sum(known_vals) / len(known_vals)
        for h in range(24):
            if hour_counts[h] == 0:
                row[h] = fill
        rows.append((d, row))

    rows.sort(key=lambda r: r[0])
    rows = rows[-days:] if days > 0 else []
    return [r[1] for r in rows]


def history_depth_days(matrix) -> int:
    return len(matrix)