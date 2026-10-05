"""Standalone tests for hems.history_builder — pure stdlib, no HA."""

from __future__ import annotations

import math
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hems.history_builder import build_hourly_load_matrix, history_depth_days


_PASS = 0
_FAIL = 0
_FAILURES: list[str] = []


def _check(cond: bool, msg: str) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        _FAILURES.append(msg)
        print(f"  FAIL: {msg}")



def _row_payload(row):
    """Unwrap the dated tuple shape so legacy
    test bodies can keep iterating over a flat
    payload. Audit T19: ``build_hourly_load_matrix``
    now returns ``list[tuple[date, list[float],
    bool]]``. Test bodies that previously
    iterated over ``row`` directly use
    ``_row_payload(row)``.
    """
    if isinstance(row, tuple):
        return row[1]
    return row


def _row_date(row):
    if isinstance(row, tuple):
        return row[0]
    return None


def _row_gap_filled(row):
    if isinstance(row, tuple) and len(row) >= 3:
        return bool(row[2])
    return False


def _section(name: str) -> None:
    print(f"\n-- {name} --")


def _full_day(date_, watts: float) -> list:
    return [(datetime(date_.year, date_.month, date_.day, h, 0, 0), watts)
            for h in range(24)]


def test_empty_input_returns_empty():
    _section("empty input")
    out = build_hourly_load_matrix([], datetime(2026, 6, 10, 5, 0))
    _check(out == [], "empty samples -> empty matrix")


def test_three_full_days_constant():
    _section("3 full days constant 250W")
    base = date(2026, 6, 9)  # day strictly before now
    now = datetime(2026, 6, 12, 12, 0)
    samples = []
    for offset in range(3):
        samples.extend(_full_day(base + timedelta(days=offset), 250.0))
    out = build_hourly_load_matrix(samples, now)
    _check(len(out) == 3, f"len == 3 (got {len(out)})")
    _check(history_depth_days(out) == 3, "history_depth_days == 3")
    for i, row in enumerate(out):
        _check(len(_row_payload(row)) == 24, f"row {i} length 24")
        _check(all(abs(v - 250.0) < 1e-9 for v in _row_payload(row)),
               f"row[{i}] all 250.0")


def test_today_partial_excluded():
    _section("today's partial samples excluded")
    now = datetime(2026, 6, 12, 12, 0)
    yesterday = now - timedelta(days=1)
    samples = _full_day(yesterday.date(), 300.0)
    # add partial samples for today (should be ignored)
    for h in range(8):
        samples.append((datetime(2026, 6, 12, h, 30), 999.0))
    out = build_hourly_load_matrix(samples, now)
    _check(len(out) == 1, f"only yesterday included (got {len(out)})")
    _check(all(abs(v - 300.0) < 1e-9 for v in _row_payload(out[0])),
           "yesterday row all 300.0")


def test_day_with_only_10_hours_excluded():
    _section("10-hour day excluded")
    now = datetime(2026, 6, 12, 12, 0)
    sparse_day = now - timedelta(days=1)
    samples = [(datetime(sparse_day.year, sparse_day.month, sparse_day.day, h, 0), 200.0)
               for h in range(10)]
    out = build_hourly_load_matrix(samples, now)
    _check(out == [], "10-hour day not included")


def test_day_with_20_hours_fill_missing():
    _section("20-hour day included; missing hours filled with row avg")
    now = datetime(2026, 6, 12, 12, 0)
    the_day = (now - timedelta(days=1)).date()
    missing = {2, 5, 11, 19}
    samples = []
    for h in range(24):
        if h in missing:
            continue
        samples.append((datetime(the_day.year, the_day.month, the_day.day, h, 0),
                        400.0))
    out = build_hourly_load_matrix(samples, now)
    _check(len(out) == 1, "20-hour day included")
    row = _row_payload(out[0])
    for h in range(24):
        _check(abs(row[h] - 400.0) < 1e-9, f"hour {h} == 400.0")


def test_invalid_samples_ignored():
    _section("None/NaN/negative/30000 ignored")
    now = datetime(2026, 6, 12, 12, 0)
    the_day = (now - timedelta(days=1)).date()
    # 24 clean samples + several invalid
    samples = []
    for h in range(24):
        samples.append((datetime(the_day.year, the_day.month, the_day.day, h, 0),
                        500.0))
    samples.append((datetime(the_day.year, the_day.month, the_day.day, 0, 1), None))
    samples.append((datetime(the_day.year, the_day.month, the_day.day, 0, 2), float('nan')))
    samples.append((datetime(the_day.year, the_day.month, the_day.day, 0, 3), -100.0))
    samples.append((datetime(the_day.year, the_day.month, the_day.day, 0, 4), 30000.0))
    samples.append((datetime(the_day.year, the_day.month, the_day.day, 0, 5), float('inf')))
    out = build_hourly_load_matrix(samples, now)
    _check(len(out) == 1, "exactly one row")
    _check(all(abs(v - 500.0) < 1e-9 for v in _row_payload(out[0])),
           "row unaffected by invalid samples")


def test_days_cap_returns_most_recent():
    _section("10 days input, days=7 -> 7 most recent, oldest first")
    now = datetime(2026, 6, 20, 0, 0)
    samples = []
    for offset in range(10):
        d = (now - timedelta(days=offset + 1)).date()
        # 24 samples, value = offset+1 * 100 (so rows distinguishable)
        for h in range(24):
            samples.append((datetime(d.year, d.month, d.day, h, 0),
                            float((offset + 1) * 100)))
    out = build_hourly_load_matrix(samples, now, days=7)
    _check(len(out) == 7, f"len == 7 (got {len(out)})")
    # Oldest-of-kept is 7 days back (offset 6) -> 700; newest is 1 day back (offset 0) -> 100.
    # Matrix is ordered OLDEST first, so row 0 = 700, row 6 = 100.
    expected = [(i + 1) * 100.0 for i in range(6, -1, -1)]
    _check(len(out) == len(expected), f"len(out) {len(out)} matches expected")
    for i, row in enumerate(out):
        _check(len(_row_payload(row)) == 24, f"row {i} length 24")
        _check(all(abs(v - expected[i]) < 1e-9 for v in _row_payload(row)),
               f"row {i} values == {expected[i]} W")


def test_hourly_averaging_two_samples():
    _section("hourly averaging: 100 and 300 -> 200.0")
    now = datetime(2026, 6, 12, 12, 0)
    the_day = (now - timedelta(days=1)).date()
    # hour 5 has two samples
    samples = []
    for h in range(24):
        if h == 5:
            samples.append((datetime(the_day.year, the_day.month, the_day.day, 5, 0), 100.0))
            samples.append((datetime(the_day.year, the_day.month, the_day.day, 5, 30), 300.0))
        else:
            samples.append((datetime(the_day.year, the_day.month, the_day.day, h, 0), 250.0))
    out = build_hourly_load_matrix(samples, now)
    _check(len(out) == 1, "one row returned")
    payload = _row_payload(out[0])
    _check(abs(payload[5] - 200.0) < 1e-9, f"hour 5 avg == 200.0 (got {payload[5]})")
    _check(abs(payload[0] - 250.0) < 1e-9, "hour 0 still 250.0")


if __name__ == "__main__":
    test_empty_input_returns_empty()
    test_three_full_days_constant()
    test_today_partial_excluded()
    test_day_with_only_10_hours_excluded()
    test_day_with_20_hours_fill_missing()
    test_invalid_samples_ignored()
    test_days_cap_returns_most_recent()
    test_hourly_averaging_two_samples()

    print(f"\n{_PASS} passed, {_FAIL} failed")
    if _FAIL:
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL HISTORY BUILDER TESTS PASSED")