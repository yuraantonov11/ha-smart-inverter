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

    Audit T19 follow-up: the return shape is now
    ``list[tuple[date, list[float]]]``. Earlier
    revisions returned ``list[list[float]]`` which
    silently lost the calendar date and prevented
    the weekday predictor from filtering by
    ``date.weekday()``. The dated shape is the
    canonical contract; the coordinator forwards
    it to ``ConsumptionPredictor`` which then
    keeps the all-history fallback for legacy
    callers.

    Rows are ordered OLDEST first, MOST RECENT last.
    Only COMPLETE past calendar days (strictly
    before ``now.date()``) are considered. A day
    is included only when at least 18 of its 24
    hours have data; missing hours inside an
    included row are filled with that row's
    known-hour average. At most ``days``
    most-recent complete days are returned.

    A row whose average fills more than half of
    its 24 hours is marked as ``gap_filled=True``
    via a parallel ``rows_with_meta`` list; the
    predictor treats such rows as untrusted
    measurements.

    Two helpers round-trip the shape:

      * ``matrix_to_rows(matrix)`` converts a
        flat list[list[float]] into the dated
        shape by pairing with ``None`` dates. The
        predictor treats those rows as
        ``dated=False`` and falls back to
        all-history.

      * ``history_depth_days(matrix)`` counts the
        rows regardless of which shape the caller
        passed.
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

    rows: list[tuple[date, list[float], bool]] = []
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
        # Audit T19.3 (Windows review):
        # the previous threshold of
        # ``(24 - known_hours) > 12``
        # never fired because the
        # builder itself only includes
        # rows with ``known_hours >= 18``,
        # so the maximum gap-filled
        # count is 6 - below the
        # 12-hour threshold. The
        # gap_filled flag never
        # fired in practice.
        #
        # The audit asks us to
        # propagate coverage so the
        # filled rows do not become
        # weekday samples. The fix
        # uses ``(24 - known_hours) > 0``:
        # any hour that the recorder
        # never saw is a filled
        # hour and the row is
        # untrusted. The threshold
        # ``> 0`` matches the
        # audit's framing: "filled
        # with average value does
        # not become a real weekday
        # sample".
        gap_filled = (24 - known_hours) > 0
        rows.append((d, row, gap_filled))

    rows.sort(key=lambda r: r[0])
    rows = rows[-days:] if days > 0 else []
    # Audit T19.3: each row is
    # ``(date, list[float],
    # gap_filled)``. The
    # predictor treats the third
    # element as a trust signal.
    return [(d, r, g) for d, r, g in rows]


def history_depth_days(matrix) -> int:
    """Return the row count of either the dated
    shape ``list[tuple[date, list[float]]]`` or
    the legacy shape ``list[list[float]]``.
    """
    return len(matrix)


def matrix_gap_filled_flags(matrix) -> list[bool]:
    """Return a parallel ``list[bool]`` of
    gap-filled flags for each row in the matrix.
    Legacy ``list[list[float]]`` callers get
    all-``False`` so the predictor treats every
    row as measured.
    """
    flags: list[bool] = []
    for row in matrix:
        if isinstance(row, tuple) and len(row) >= 3:
            flags.append(bool(row[2]))
        else:
            flags.append(False)
    return flags


def matrix_to_dated(matrix, dates=None) -> list[tuple]:
    """Convert a flat ``list[list[float]]`` into
    the dated shape, optionally pairing each row
    with a calendar date. ``dates=None`` keeps
    the rows as ``(None, row)`` tuples - the
    predictor treats those as legacy
    non-dated history and falls back to
    all-history.
    """
    if not matrix:
        return []
    if dates is not None and len(dates) == len(matrix):
        return list(zip(dates, matrix))
    return [(None, row) for row in matrix]


def matrix_legacy_unwrap(matrix) -> list[list[float]]:
    """Unwrap a possibly-dated matrix back to the
    legacy ``list[list[float]]`` shape. The
    coordinator passes this to consumers that
    have not yet been migrated to the dated
    shape (for example the morning-savings
    sensor). The predictor itself accepts the
    dated shape directly.
    """
    out: list[list[float]] = []
    for row in matrix:
        if isinstance(row, tuple):
            out.append(row[1])
        else:
            out.append(row)
    return out