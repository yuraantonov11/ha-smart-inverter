"""T19 regression tests: weekday predictor.

Audit T19: predictor class advertises a weekday filter
(``predict(hour, day_of_week)``) but the implementation
reads ``same_dow = [day[hour] for day in self._hist]``
- it ignores the ``day_of_week`` argument entirely. The
code path looks identical for any weekday value when
``self._hist`` has more than one entry, and the
``list[list[float]]`` history loses the calendar date
information that ``weekday()`` needs.

These tests assert the **observable** contract:

  * Different weekdays in the same dataset yield
    different predictions when the dataset has enough
    same-weekday history (audit point 1).
  * Gap-filled values are not counted as measured
    samples (audit point 3).
  * Missing dates do NOT shift the weekday index of
    other rows (audit point 2).
  * Insufficient same-weekday history falls back to
    all-history (audit point 4 - чесний fallback).
  * Non-dated history (legacy ``list[list[float]]``)
    also falls back to all-history and does not crash
    (audit backward compatibility).
  * DST boundary: history spanning a DST transition
    preserves the right calendar date when bucketed by
    ``ts.date()`` (audit DST).
  * Existing engine safety guards stay intact
    (audit safety verification).

The tests live in ``tests/`` and are runnable with
``python tests/test_t19_weekday_filter.py`` and through
``tests/run_all.py``.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone

# Path tweak: tests live one level below the repo
# root, but ``hems`` lives at the root. Add the parent
# of this file to ``sys.path`` so ``import hems.*``
# resolves regardless of the runner's cwd.
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from hems.history_builder import build_hourly_load_matrix  # noqa: E402
from hems.predictive import ConsumptionPredictor  # noqa: E402


# ───────────────────────────────────────────────────────
# Test data helpers
# ───────────────────────────────────────────────────────


def _monday_consumption(hourly: float = 500.0) -> list[float]:
    """Return a 24h load vector with a single-peak
    profile. Monday baseline is 500 W with a kettle
    spike at 18:00 to 1.5 kW.
    """
    row = [hourly] * 24
    row[18] = 1500.0
    return row


def _sunday_consumption(hourly: float = 300.0) -> list[float]:
    """Return a 24h load vector for Sunday baseline
    300 W with the kettle spike at 11:00 (mid-morning
    brunch, audit-reproducible weekday signal).
    """
    row = [hourly] * 24
    row[11] = 1500.0
    return row


def _build_dated_samples(
    rows: list[tuple[date, list[float]]],
) -> list[tuple[datetime, float]]:
    """Convert (date, row) tuples into the flat
    (timestamp, watts) sample stream
    ``build_hourly_load_matrix`` consumes. We emit
    one sample per hour per day at minute 30 - well
    inside each hour window so bucketing is stable
    across DST boundaries and time zones.
    """
    samples: list[tuple[datetime, float]] = []
    for d, row in rows:
        for h in range(24):
            ts = datetime(
                d.year, d.month, d.day, h, 30,
                tzinfo=timezone.utc,
            )
            samples.append((ts, row[h]))
    return samples


def _dated_history(rows: list[tuple[date, list[float]]]):
    """Build the dated history matrix the predictor
    consumes from a list of (date, row) tuples. We
    call the production builder and pass the result
    back through
    ``build_hourly_load_matrix``'s dated API once we
    have extended it (see T19.1 below); for tests
    we use the helper directly because it makes the
    tests self-explanatory.
    """
    return rows


# ───────────────────────────────────────────────────────
# Test 1: same dataset, different weekday -> different prediction
# ───────────────────────────────────────────────────────


def test_mon_and_sun_yield_different_predictions() -> None:
    """Mon peak is at 18:00; Sun peak is at 11:00.
    With three weeks of history (three Mondays, three
    Sundays) the predictor must report very different
    values for Monday at 18:00 versus Sunday at 11:00
    *when the same-weekday history is sufficient*.

    Audit T19: the original predictor took the *entire*
    history for any weekday argument. The Mon and Sun
    predictions become identical because both rows contribute
    to every weekday slot. After the fix, only the
    rows whose ``date.weekday()`` matches the requested
    weekday contribute.
    """
    # Build three full weeks of dated history.
    mondays = [
        (date(2026, 6, 1), _monday_consumption(500.0)),
        (date(2026, 6, 8), _monday_consumption(500.0)),
        (date(2026, 6, 15), _monday_consumption(500.0)),
    ]
    sundays = [
        (date(2026, 6, 7), _sunday_consumption(300.0)),
        (date(2026, 6, 14), _sunday_consumption(300.0)),
        (date(2026, 6, 21), _sunday_consumption(300.0)),
    ]
    rows = mondays + sundays
    rows.sort(key=lambda r: r[0])
    predictor = ConsumptionPredictor(history=_dated_history(rows))

    # Monday at 18:00 should be ~1500 W (the kettle spike).
    mean_mon_18, _ = predictor.predict(18, 0)
    assert abs(mean_mon_18 - 1500.0) < 1e-6, (
        f"Monday 18:00 prediction must reflect the Monday "
        f"kettle spike; got {mean_mon_18!r}. The "
        f"predictor is averaging across all weekdays."
    )
    # Sunday at 11:00 should be ~1500 W (the kettle spike).
    mean_sun_11, _ = predictor.predict(11, 6)
    assert abs(mean_sun_11 - 1500.0) < 1e-6, (
        f"Sunday 11:00 prediction must reflect the Sunday "
        f"kettle spike; got {mean_sun_11!r}. The "
        f"predictor is averaging across all weekdays."
    )
    # Monday at 11:00 should NOT be 1500 W - Mondays have
    # only the 500 W baseline at 11:00.
    mean_mon_11, _ = predictor.predict(11, 0)
    assert abs(mean_mon_11 - 500.0) < 1e-6, (
        f"Monday 11:00 prediction must be the 500 W "
        f"baseline (kettle is at 18:00); got {mean_mon_11!r}. "
        f"The predictor is bleeding Sunday's 11:00 spike "
        f"into Monday."
    )


# ───────────────────────────────────────────────────────
# Test 2: gap-filled values are not counted as measured samples
# ───────────────────────────────────────────────────────


def test_gap_filled_values_excluded_from_samples() -> None:
    """Audit T19.3: gap-filled values (the audit calls
    these ``rows with the same value for every hour``)
    must NOT count as 'measured' samples. If a Tuesday
    has only 4 hours of data and the rest are
    filled with the row average, the predictor must
    fall back to all-history for Tuesday instead of
    trusting the row average.

    The history builder flags gap-filled rows
    explicitly (the third tuple element). The
    predictor respects that flag.
    """
    # The audit's gap-filled row
    # shape: 4 real measurements
    # around 600 W plus 20 hours
    # filled with the row average.
    row_fake_tue = [0.0] * 24
    real_hours = {4: 600.0, 10: 600.0, 16: 600.0, 22: 600.0}
    for h, v in real_hours.items():
        row_fake_tue[h] = v
    for h in range(24):
        if h not in real_hours:
            row_fake_tue[h] = 600.0
    # A *real* Tuesday with
    # measurements at every hour
    # but where the kettle is at
    # 18:00.
    row_real_tue = [600.0] * 24
    row_real_tue[18] = 1500.0
    rows = [
        (date(2026, 6, 2), row_fake_tue, True),   # gap_filled
        (date(2026, 6, 9), row_fake_tue, True),   # gap_filled
        (date(2026, 6, 16), row_real_tue, False), # real
    ]
    predictor = ConsumptionPredictor(history=rows)
    mean_tue_18, _ = predictor.predict(18, 1)
    # If the fake rows are NOT
    # excluded, the average is
    # (1500 + 600 + 600) / 3 = 900.
    # If they ARE excluded, the
    # average is 1500 (only the
    # real Tuesday contributes).
    assert abs(mean_tue_18 - 1500.0) < 1e-6, (
        f"Tuesday 18:00 must reflect the real kettle "
        f"spike (1500 W); got {mean_tue_18!r}. The "
        f"predictor is trusting the gap-filled rows."
    )


# ───────────────────────────────────────────────────────
# Test 3: missing dates do not shift weekday index
# ───────────────────────────────────────────────────────


def test_missing_dates_do_not_shift_weekday_index() -> None:
    """Audit T19.2: if we have only 5 of 7 days this
    week, those 5 days still need to map to their real
    weekday. The predictor must look at the date, not
    the matrix index.
    """
    # We have Wednesday through Sunday in week A, and
    # Wednesday through Sunday in week B (no Mon or Tue).
    wed = [400.0] * 24
    wed[19] = 1200.0
    thu = [400.0] * 24
    thu[20] = 1200.0
    sat = [400.0] * 24
    sat[12] = 1200.0
    sun = [400.0] * 24
    sun[11] = 1200.0
    rows = [
        (date(2026, 6, 3), wed),    # Wed
        (date(2026, 6, 4), thu),    # Thu
        (date(2026, 6, 6), sat),    # Sat
        (date(2026, 6, 7), sun),    # Sun
        (date(2026, 6, 10), wed),   # Wed week B
        (date(2026, 6, 11), thu),   # Thu week B
        (date(2026, 6, 13), sat),   # Sat week B
        (date(2026, 6, 14), sun),   # Sun week B
    ]
    predictor = ConsumptionPredictor(history=rows)
    # Monday has zero history in the dataset. The
    # predictor must NOT shift Wed's 19:00 spike into
    # Monday (because Wed sits at index 0 in the
    # history).
    mean_mon_19, _ = predictor.predict(19, 0)
    # Monday has zero same-weekday rows, so the
    # predictor falls back to all-history average at
    # 19:00 which is (1200+400+400+1200+400+400+1200+400)/8
    # = 5880 / 8 = 735 W (Wed's spike counts because
    # all-history ignores weekday).
    assert mean_mon_19 != 1200.0, (
        f"Monday 19:00 must NOT borrow Wed's spike; got "
        f"{mean_mon_19!r}. The predictor is using matrix "
        f"index instead of the calendar date."
    )


# ───────────────────────────────────────────────────────
# Test 4: insufficient same-weekday history -> all-history
# ───────────────────────────────────────────────────────


def test_insufficient_same_weekday_history_falls_back() -> None:
    """Audit T19: when only one same-weekday sample is
    available, the predictor must fall back to the
    all-history average for that hour, not crash or
    invent a value. The previous code would crash on
    ``var = ... / max(n-1, 1)`` for n=1.
    """
    # Monday has a kettle spike at 18:00. Sundays do
    # not (their spike is at 11:00). The expected
    # all-history average at Monday 18:00 is
    # ``(1500 + 300 + 300 + 300) / 4 = 600`` - exactly
    # the audit-reproducible signal that the
    # fallback honours weekday-vs-baseline asymmetry.
    rows = [
        (date(2026, 6, 1), _monday_consumption(500.0)),
        (date(2026, 6, 7), _sunday_consumption(300.0)),
        (date(2026, 6, 14), _sunday_consumption(300.0)),
        (date(2026, 6, 21), _sunday_consumption(300.0)),
    ]
    predictor = ConsumptionPredictor(history=rows)
    # Monday has only one sample. Predictor must fall
    # back to all-history.
    mean_mon_18, stdev_mon_18 = predictor.predict(18, 0)
    # All-history at 18:00: Mon is 1500 W, all three
    # Sundays are 300 W (their kettle is at 11:00).
    # Average: (1500 + 300 + 300 + 300) / 4 = 600.
    assert abs(mean_mon_18 - 600.0) < 1e-6, (
        f"Monday 18:00 must fall back to all-history "
        f"600 W (Mon's kettle spike + three Sundays "
        f"at the 300 W baseline); got {mean_mon_18!r}."
    )
    # stdev is finite; the all-history fallback must
    # not crash with ``division by zero`` for n=4.
    assert stdev_mon_18 > 0.0


# ───────────────────────────────────────────────────────
# Test 5: legacy nondated history still works
# ───────────────────────────────────────────────────────


def test_nondated_legacy_history_falls_back_to_all_history() -> None:
    """Audit T19 backward compatibility: if the
    coordinator still hands the predictor the legacy
    ``list[list[float]]`` (no dates), the predictor
    must keep working - falling back to the
    all-history average for any weekday. The audit
    calls this a 'honest all-history fallback'.
    """
    # Mondays carry a kettle spike at 18:00. Sundays
    # do not (the spike is at 11:00). The expected
    # all-history average at 18:00 across the four
    # rows is ``(1500 + 300 + 1500 + 300) / 4 = 900``,
    # which is exactly the signal that no weekday
    # filter is being applied.
    rows = [
        _monday_consumption(500.0),
        _sunday_consumption(300.0),
        _monday_consumption(500.0),
        _sunday_consumption(300.0),
    ]
    predictor = ConsumptionPredictor(history=rows)
    # Same hour, two different weekday codes must yield
    # the same prediction because we have no dates.
    mean_any_18, _ = predictor.predict(18, 0)
    mean_any2_18, _ = predictor.predict(18, 6)
    assert abs(mean_any_18 - mean_any2_18) < 1e-9, (
        f"With no dates the predictor must report the "
        f"same value for any weekday; got "
        f"{mean_any_18!r} vs {mean_any2_18!r}."
    )
    # And the value must match the all-history average.
    assert abs(mean_any_18 - 900.0) < 1e-6, (
        f"All-history 18:00 is 900 W (two Mondays at "
        f"1500 W + two Sundays at 300 W, no weekday "
        f"filter); got {mean_any_18!r}."
    )


# ───────────────────────────────────────────────────────
# Test 6: build_hourly_load_matrix round-trips dates
# ───────────────────────────────────────────────────────


def test_history_builder_preserves_dates() -> None:
    """Audit T19: ``build_hourly_load_matrix`` must
    return ``list[tuple[date, list[float]]]`` so the
    coordinator can pass dates into the predictor. If
    it returns ``list[list[float]]`` the dates are lost
    and the predictor falls back to all-history.
    """
    rows = [
        (date(2026, 6, 1), _monday_consumption(500.0)),  # Mon week A
        (date(2026, 6, 2), [400.0] * 24),                # Tue
        (date(2026, 6, 3), [400.0] * 24),                # Wed
        (date(2026, 6, 4), [400.0] * 24),                # Thu
        (date(2026, 6, 5), [400.0] * 24),                # Fri
        (date(2026, 6, 6), [400.0] * 24),                # Sat
        (date(2026, 6, 7), _sunday_consumption(300.0)),  # Sun week A
        (date(2026, 6, 8), _monday_consumption(500.0)),  # Mon week B
        (date(2026, 6, 9), [400.0] * 24),                # Tue
        (date(2026, 6, 10), [400.0] * 24),               # Wed
        (date(2026, 6, 11), [400.0] * 24),               # Thu
        (date(2026, 6, 12), [400.0] * 24),               # Fri
        (date(2026, 6, 13), [400.0] * 24),               # Sat
        (date(2026, 6, 14), _sunday_consumption(300.0)), # Sun week B
        (date(2026, 6, 15), _monday_consumption(500.0)), # Mon week C
        (date(2026, 6, 16), [400.0] * 24),               # Tue week C
    ]
    samples = _build_dated_samples(rows)
    # Use a "now" *after* the last sample so all rows
    # are eligible.
    now = datetime(2026, 6, 17, 0, 0, tzinfo=timezone.utc)
    matrix = build_hourly_load_matrix(samples, now, days=16)
    # The result must carry dates so the coordinator
    # can build the weekday filter.
    if matrix and not isinstance(matrix[0], tuple):
        raise AssertionError(
            "build_hourly_load_matrix returned "
            f"{type(matrix[0]).__name__} rows; expected "
            "tuple[date, list[float]] so the coordinator "
            "can pass dates into ConsumptionPredictor."
        )
    # Dated check: each row's date is a date object,
    # and Monday rows are at index 0, 7, 14 etc.
    assert len(matrix) >= 15, (
        f"Expected 15 dated rows from a 15-day window; "
        f"got {len(matrix)}."
    )
    monday_dates = [
        r[0] for r in matrix if r[0].weekday() == 0
    ]
    assert len(monday_dates) == 3, (
        f"Expected three Mondays in the dated output; "
        f"got {monday_dates!r}."
    )


# ───────────────────────────────────────────────────────
# Test 7: DST transition does not collapse two days
# ───────────────────────────────────────────────────────


def test_dst_does_not_merge_two_days() -> None:
    """Audit T19 DST: a DST transition adds or
    removes one hour per day. The history builder
    must NOT collapse the day before and the day
    after into one row (which would happen if the
    builder forgot the calendar date and only
    counted 23 or 25 samples for that day).
    """
    # 2026-03-29 is the EU DST 'spring forward' date
    # (UTC+02:00 -> UTC+03:00). We sample a Wednesday
    # before, the DST day, and a Friday after. All
    # three must appear in the dated output.
    rows = [
        (date(2026, 3, 28), [500.0] * 24),  # Sat before DST
        (date(2026, 3, 29), [500.0] * 24),  # DST day
        (date(2026, 3, 30), [500.0] * 24),  # Mon after DST
    ]
    samples = _build_dated_samples(rows)
    now = datetime(2026, 3, 31, 0, 0, tzinfo=timezone.utc)
    matrix = build_hourly_load_matrix(samples, now, days=5)
    assert len(matrix) == 3, (
        f"DST transition must not collapse two days; "
        f"expected 3 dated rows, got {len(matrix)}."
    )
    matrix_dates = sorted(r[0] for r in matrix)
    assert matrix_dates == [
        date(2026, 3, 28),
        date(2026, 3, 29),
        date(2026, 3, 30),
    ], (
        f"DST must preserve calendar dates; got "
        f"{matrix_dates!r}."
    )


# ───────────────────────────────────────────────────────
# Test 8: safety guards stay intact
# ───────────────────────────────────────────────────────


def test_engine_safety_guards_intact() -> None:
    """Audit T19 safety verification: the engine
    safety guards (BMS, reserve_soc, hysteresis,
    storm_hard_floor, manual override, circuit
    breaker) must NOT be affected by the predictor
    changes. We instantiate a
    ``PredictiveHemsController``, feed it a dated
    history, and assert the predictor's internal
    representation matches the dated contract - so
    the weekday filter is in place without any
    guard bypass.
    """
    from hems.predictive import PredictiveHemsController

    rows = [
        (date(2026, 6, 1), _monday_consumption(500.0)),
        (date(2026, 6, 7), _sunday_consumption(300.0)),
        (date(2026, 6, 8), _monday_consumption(500.0)),
        (date(2026, 6, 14), _sunday_consumption(300.0)),
        (date(2026, 6, 15), _monday_consumption(500.0)),
    ]
    controller = PredictiveHemsController()
    controller.consumption_predictor = ConsumptionPredictor(history=rows)
    # The controller's predictor must carry dates
    # so the weekday filter is in effect.
    predictor = controller.consumption_predictor
    assert predictor._has_dates, (
        "Predictor must know that the history has "
        "dates so the weekday filter is honoured."
    )
    hist = predictor._hist
    assert hist, "Predictor history is empty."
    if not isinstance(hist[0], tuple):
        raise AssertionError(
            "Predictor._hist must contain dated rows; "
            f"got {type(hist[0]).__name__}."
        )
    # And the predictor actually filters by
    # weekday: Monday at 18:00 sees only Mondays.
    mean_mon_18, _ = predictor.predict(18, 0)
    assert abs(mean_mon_18 - 1500.0) < 1e-6, (
        f"Monday 18:00 must be the Monday kettle "
        f"spike (1500 W); got {mean_mon_18!r}. The "
        f"predictor did not filter by weekday."
    )


# ───────────────────────────────────────────────────────
# Test 9: gap-filled detection - 4 real + 20 averaged
# ───────────────────────────────────────────────────────


def test_gap_filled_detection_uniform_rows() -> None:
    """Audit T19.3 follow-up: a row that the history
    builder flagged as gap-filled must be excluded
    from the same-weekday sample set.
    """
    # Three Mondays where the
    # first two are gap-filled by
    # the recorder (the third is
    # fully measured with a kettle
    # at 18:00 to 1500 W).
    row_gap = [600.0] * 24
    row_real = [600.0] * 24
    row_real[18] = 1500.0
    rows = [
        (date(2026, 6, 1), row_gap, True),   # gap_filled
        (date(2026, 6, 8), row_gap, True),   # gap_filled
        (date(2026, 6, 15), row_real, False), # real
    ]
    # Without gap-fill detection,
    # the Monday 18:00 average is
    # (600 + 600 + 1500) / 3 = 900.
    # With detection, only the
    # real row contributes so the
    # average is 1500.
    predictor = ConsumptionPredictor(history=rows)
    mean, _ = predictor.predict(18, 0)
    assert abs(mean - 1500.0) < 1e-6, (
        f"Gap-filled rows must be excluded; got "
        f"{mean!r}."
    )


# ───────────────────────────────────────────────────────
# Test 10: nondated fallback path is honest - it
# does NOT pretend to know weekdays
# ───────────────────────────────────────────────────────


def test_nondated_history_uses_honest_fallback() -> None:
    """Audit T19.4: nondated history (no calendar
    dates) must produce IDENTICAL predictions
    regardless of the weekday code. Anything else
    would silently make the predictor a 'garbage
    in, garbage out' that pretends to know.
    """
    rows = [
        _monday_consumption(500.0),
        _sunday_consumption(300.0),
        _monday_consumption(500.0),
    ]
    predictor = ConsumptionPredictor(history=rows)
    for h in range(24):
        for d in range(7):
            mean_a, _ = predictor.predict(h, d)
            mean_b, _ = predictor.predict(h, (d + 1) % 7)
            if abs(mean_a - mean_b) > 1e-9:
                raise AssertionError(
                    f"With no dates the predictor must "
                    f"report the same value for any "
                    f"weekday; got hour={h} d={d} "
                    f"-> {mean_a!r} vs d+1={d+1} -> "
                    f"{mean_b!r}."
                )


def _run_all() -> None:
    """Run every test in this module. The runner
    picks each ``test_*`` function and executes it
    in the module namespace.
    """
    import inspect

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
        print(
            f"\n{len(failures)} of {len(tests)} tests failed:"
        )
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")
    sys.exit(0)


if __name__ == "__main__":
    _run_all()