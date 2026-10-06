"""T19 regression tests: gap_filled coverage invariant.

Audit T19 follow-up (Windows review):
``history_builder.build_hourly_load_matrix``
admitted days with as few as 18 measured
hours but only flagged a row as
``gap_filled`` when 12+ hours were missing.
With ``known_hours >= 18`` enforced as a
pre-condition, the gap_filled flag could never
fire - the maximum gap-filled count was 6.
The audit's repro shows rows passing in the
filter that should not.

The audit requires propagating coverage so
filled-with-average rows do not become
real weekday samples. The fix lowers the
trigger from ``(24 - known_hours) > 12``
to ``(24 - known_hours) > 0``.

This test file asserts:

  * A row with 22 measured hours (2 filled)
    is flagged ``gap_filled=True``.
  * A row with 18 measured hours (6 filled)
    is flagged ``gap_filled=True`` (was
    missed before the fix).
  * A row with 24 measured hours (all real)
    is flagged ``gap_filled=False``.
  * The downstream ``ConsumptionPredictor``
    excludes rows flagged ``gap_filled=True``
    from the same-weekday sample set.
  * When all rows for a weekday are flagged
    gap-filled, the predictor falls back to
    all-history (and only all-history rows
    that are not gap-filled contribute).
"""

from __future__ import annotations

import os
import sys
from datetime import date

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from hems.history_builder import build_hourly_load_matrix  # noqa: E402
from hems.predictive import ConsumptionPredictor  # noqa: E402


def _samples_for_row(d, base_w: float = 600.0,
                     missing_hours: set[int] | None = None):
    """Build a (timestamp, watts) sample
    stream for a single day. The caller
    can mark hours as missing and the
    builder will fill them with the row
    average.
    """
    if missing_hours is None:
        missing_hours = set()
    samples = []
    for h in range(24):
        if h in missing_hours:
            continue
        ts = d.replace(hour=h, minute=30, second=0, microsecond=0)
        samples.append((ts, base_w))
    return samples


# Patch - from datetime - and add datetime import
import datetime  # noqa: E402


def test_row_with_two_missing_hours_is_gap_filled() -> None:
    """Audit T19 follow-up: a row with 2
    filled hours must be flagged
    ``gap_filled=True`` after the fix.
    Before the fix, the row was admitted
    with ``gap_filled=False``.
    """
    now = datetime.datetime(2026, 6, 15, 0, 0)
    samples = _samples_for_row(
        datetime.datetime(2026, 6, 14, 0, 0),
        base_w=600.0,
        missing_hours={5, 17},
    )
    matrix = build_hourly_load_matrix(samples, now, days=2)
    # The row was admitted (>= 18 known hours).
    assert len(matrix) == 1
    d, row, gap_filled = matrix[0]
    assert gap_filled is True, (
        f"Row with 22 measured hours (2 filled) "
        f"must be flagged gap_filled=True; "
        f"got {gap_filled!r}. The previous "
        f"threshold missed this case."
    )


def test_row_with_six_missing_hours_is_gap_filled() -> None:
    """Audit T19 follow-up: a row with 6
    filled hours (18 measured) must be
    flagged ``gap_filled=True`` - the
    audit's specific repro.
    """
    from datetime import datetime as _dt
    now = _dt(2026, 6, 15, 0, 0)
    samples = _samples_for_row(
        _dt(2026, 6, 14, 0, 0),
        base_w=600.0,
        missing_hours={2, 5, 9, 13, 17, 21},
    )
    matrix = build_hourly_load_matrix(samples, now, days=2)
    assert len(matrix) == 1
    d, row, gap_filled = matrix[0]
    assert gap_filled is True, (
        f"Row with 18 measured hours (6 filled) "
        f"must be flagged gap_filled=True; "
        f"got {gap_filled!r}. The previous "
        f"threshold missed this case."
    )


def test_row_with_all_measured_is_not_gap_filled() -> None:
    """Audit T19 follow-up: a fully
    measured row (no filled hours) must
    keep ``gap_filled=False``.
    """
    from datetime import datetime as _dt
    now = _dt(2026, 6, 15, 0, 0)
    samples = _samples_for_row(
        _dt(2026, 6, 14, 0, 0),
        base_w=600.0,
        missing_hours=set(),
    )
    matrix = build_hourly_load_matrix(samples, now, days=2)
    assert len(matrix) == 1
    d, row, gap_filled = matrix[0]
    assert gap_filled is False, (
        f"Fully measured row must be "
        f"gap_filled=False; got {gap_filled!r}."
    )


def test_predictor_excludes_gap_filled_from_same_weekday() -> None:
    """Audit T19 follow-up end-to-end:
    the ``ConsumptionPredictor`` must
    exclude ``gap_filled=True`` rows
    from the same-weekday sample set
    when dates are available.
    """
    # Three Mondays: two with a kettle
    # spike at 18:00, one fully
    # gap-filled (just baseline).
    real_a = [600.0] * 24
    real_a[18] = 1500.0
    real_b = [600.0] * 24
    real_b[18] = 1500.0
    gap_row = [600.0] * 24  # no spike
    rows = [
        (date(2026, 6, 1), real_a, False),
        (date(2026, 6, 8), real_b, False),
        (date(2026, 6, 15), gap_row, True),  # gap_filled
    ]
    predictor = ConsumptionPredictor(history=rows)
    mean_mon_18, _ = predictor.predict(18, 0)
    # Two real Mondays contribute: average
    # 1500. The gap-filled row must be
    # excluded.
    assert abs(mean_mon_18 - 1500.0) < 1e-6, (
        f"Monday 18:00 must be the average "
        f"of the two real Mondays (1500 W); "
        f"got {mean_mon_18!r}. The gap-filled "
        f"row leaked into the average."
    )


def test_predictor_falls_back_to_all_history_when_all_gap_filled() -> None:
    """Audit T19 follow-up: when all
    rows for a weekday are gap-filled,
    the predictor falls back to all-history.
    The audit says "filled with average
    value does not become a real weekday
    sample".
    """
    # Three Tuesdays: all gap-filled.
    gap_row = [600.0] * 24
    rows = [
        (date(2026, 6, 2), gap_row, True),
        (date(2026, 6, 9), gap_row, True),
        (date(2026, 6, 16), gap_row, True),
    ]
    predictor = ConsumptionPredictor(history=rows)
    # No real Tuesday samples -> same-dow
    # bucket empty -> falls back to
    # all-history. All-history is also
    # empty (all gap-filled), so the
    # fallback returns (250.0, 200.0)
    # per the documented contract.
    mean, stdev = predictor.predict(12, 1)
    # The exact fallback is 250 W +
    # 200 W stdev per the helper's
    # empty-history branch.
    assert mean == 250.0 and stdev == 200.0, (
        f"When all rows are gap-filled, "
        f"the predictor must return the "
        f"documented empty-history "
        f"fallback (250, 200); got "
        f"{mean!r}, {stdev!r}."
    )


def _run_all() -> None:
    failures: list[tuple[str, str]] = []
    tests = sorted(
        [
            (name, fn)
            for name, fn in globals().items()
            if name.startswith("test_") and callable(fn)
        ]
    )
    for name, fn in tests:
        try:
            fn()
            print(f"  {name}: PASS")
        except Exception as exc:
            failures.append((name, repr(exc)))
            print(f"  {name}: FAIL ({exc!r})")
    if failures:
        print(f"\n{len(failures)} of {len(tests)} tests failed:")
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")
    sys.exit(0)


if __name__ == "__main__":
    _run_all()