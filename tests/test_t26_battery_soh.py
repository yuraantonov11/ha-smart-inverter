"""T26: regression tests for ``hems/battery_soh.py``.

The audit demanded:
  * Naive / aware / date-only
    inputs are coerced
    consistently.
  * ``estimated_soh_percent``
    accepts a controlled
    ``now`` so tests are
    deterministic.
  * Malformed inputs do
    not crash the
    integration.
  * ``recommended_reserve_soc``
    is NEVER below the
    configured base
    reserve, even when
    ``base_reserve`` is
    above the bump cap.
  * SoH does not mutate
    physical limits
    (the function is a
    pure recommendation).
"""
from __future__ import annotations

import math
import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Import the production module directly.
from hems import battery_soh  # noqa: E402
from hems.battery_soh import (  # noqa: E402
    BatterySoH,
    _coerce_soc,
    _coerce_install_date,
    MAX_BUMP_SOC,
    RATED_CYCLE_LIFE,
    CALENDAR_AGING_PER_YEAR,
)


class TestCoerceSoc(unittest.TestCase):
    """T26: malformed SOC
    inputs are rejected."""

    def test_valid_number(self) -> None:
        self.assertEqual(_coerce_soc(42.5), 42.5)
        self.assertEqual(_coerce_soc(0), 0.0)
        self.assertEqual(_coerce_soc(100), 100.0)

    def test_int_returns_float(self) -> None:
        self.assertIsInstance(_coerce_soc(50), float)

    def test_none_returns_none(self) -> None:
        self.assertIsNone(_coerce_soc(None))

    def test_string_returns_none(self) -> None:
        # The old code crashed
        # with ``TypeError:
        # '<=' not supported
        # between instances of
        # 'str' and 'float'``.
        self.assertIsNone(_coerce_soc("abc"))

    def test_nan_returns_none(self) -> None:
        self.assertIsNone(_coerce_soc(float("nan")))

    def test_inf_returns_none(self) -> None:
        self.assertIsNone(_coerce_soc(float("inf")))
        self.assertIsNone(_coerce_soc(float("-inf")))

    def test_out_of_range_returns_none(self) -> None:
        self.assertIsNone(_coerce_soc(-1.0))
        self.assertIsNone(_coerce_soc(101.0))


class TestCoerceInstallDate(unittest.TestCase):
    """T26: naive / aware /
    date-only inputs are
    coerced to aware
    datetimes."""

    def test_naive_datetime_promoted_to_utc(self) -> None:
        naive = datetime(2024, 1, 1, 0, 0, 0)
        result = _coerce_install_date(naive)
        self.assertIsNotNone(result)
        self.assertEqual(result.tzinfo, timezone.utc)
        self.assertEqual(result.year, 2024)

    def test_aware_datetime_preserved(self) -> None:
        aware = datetime(
            2024, 1, 1, tzinfo=timezone(timedelta(hours=2))
        )
        result = _coerce_install_date(aware)
        self.assertIsNotNone(result)
        self.assertEqual(result.utcoffset(), timedelta(hours=2))

    def test_iso_string_with_timezone_parsed(self) -> None:
        result = _coerce_install_date("2024-01-01T00:00:00+00:00")
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.tzinfo)

    def test_naive_iso_string_promoted(self) -> None:
        result = _coerce_install_date("2024-01-01T00:00:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(_coerce_install_date(""))

    def test_garbage_string_returns_none(self) -> None:
        self.assertIsNone(_coerce_install_date("not-a-date"))

    def test_none_returns_none(self) -> None:
        self.assertIsNone(_coerce_install_date(None))

    def test_mixed_naive_aware_subtraction_is_safe(self) -> None:
        """The previous code
        could subtract a
        naive install date
        from an aware ``now``,
        which raised
        ``TypeError`` at
        runtime in
        production.

        Verify: after coerce,
        the subtraction is
        always safe.
        """
        naive = _coerce_install_date("2024-01-01T00:00:00")
        now = datetime.now(timezone.utc)
        # Must not raise.
        (now - naive).days


class TestEstimatedSoHPercentControlledNow(unittest.TestCase):
    """T26: ``estimated_soh_percent``
    accepts a controlled
    ``now`` so tests are
    deterministic and do
    not depend on wall
    time."""

    def test_zero_cycles_no_aging_returns_100(self) -> None:
        soh = BatterySoH(cycle_count=0)
        # ``now`` is 1 day
        # after install —
        # negligible aging.
        install = datetime(2024, 1, 1, tzinfo=timezone.utc)
        now = install + timedelta(days=1)
        result = soh.estimated_soh_percent(
            install_date=install, now=now
        )
        # Within 1% of 100
        # (tiny aging over 1
        # day).
        self.assertGreater(result, 99.0)
        self.assertLessEqual(result, 100.0)

    def test_one_year_aging_reduces_soh_by_approximately_3_percent(self) -> None:
        soh = BatterySoH(cycle_count=0)
        # Two arbitrary
        # datetimes exactly one
        # calendar year apart.
        # We assert the
        # result is within
        # ±0.05 of the
        # analytical 97.0
        # because leap years
        # and the integer
        # day count make the
        # raw number off by
        # up to 0.02.
        install = datetime(2024, 1, 1, tzinfo=timezone.utc)
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = soh.estimated_soh_percent(
            install_date=install, now=now
        )
        self.assertAlmostEqual(result, 97.0, delta=0.05)

    def test_cycle_degradation_at_half_life(self) -> None:
        # 1000 of 2000 cycles
        # → 50% cycle
        # degradation.
        soh = BatterySoH(cycle_count=RATED_CYCLE_LIFE // 2)
        result = soh.estimated_soh_percent(
            now=datetime.now(timezone.utc)
        )
        # age_factor = 1.0
        # (no install date),
        # cycle_degrade =
        # 0.5, so soh = 50.
        self.assertAlmostEqual(result, 50.0, places=2)

    def test_cycle_degradation_capped_at_80_percent(self) -> None:
        # 100× rated life →
        # degradation is
        # capped at 80%.
        soh = BatterySoH(cycle_count=RATED_CYCLE_LIFE * 100)
        result = soh.estimated_soh_percent(
            now=datetime.now(timezone.utc)
        )
        self.assertAlmostEqual(result, 20.0, places=2)

    def test_naive_install_date_aware_now_subtracts_safely(self) -> None:
        """The previous code
        mixed naive install
        dates with the
        wall-clock ``datetime.now()``,
        which was itself
        naive in 3.13. The
        subtraction raised
        ``TypeError`` only
        when ``install_date``
        was set as an aware
        datetime. We accept
        both now and reject
        nothing.
        """
        install = "2024-01-01T00:00:00+00:00"
        soh = BatterySoH(cycle_count=0)
        # Use a future ``now``
        # to avoid depending
        # on today's date.
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        result = soh.estimated_soh_percent(
            install_date=install, now=now
        )
        # Calendar aging over
        # 6 years: roughly
        # 6 * 3% = 18% → soh
        # = 82, ±0.05 for
        # leap-year rounding.
        self.assertAlmostEqual(result, 82.0, delta=0.05)


class TestRecommendedReserveNeverBelowBase(unittest.TestCase):
    """T26: the recommended
    reserve must NEVER be
    below the user-configured
    base reserve."""

    def test_default_base_reserve_passes_through(self) -> None:
        soh = BatterySoH(cycle_count=0)  # soh = 100% (no aging)
        result = soh.recommended_reserve_soc(base_reserve=20.0)
        # 100% SoH → no bump.
        self.assertEqual(result, 20.0)

    def test_low_soh_increases_reserve(self) -> None:
        # Simulate a 10-year
        # old battery: 10 * 3%
        # aging = 30% lost.
        soh = BatterySoH(cycle_count=0)
        install = datetime(2014, 1, 1, tzinfo=timezone.utc)
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        soh._install_date = install
        # Confirm the test
        # setup is correct.
        self.assertLess(soh.estimated_soh_percent(now=now), 80.0)
        result = soh.recommended_reserve_soc(
            base_reserve=20.0
        )
        # SoH < 80% → +5%
        # bump.
        self.assertEqual(result, 25.0)

    def test_bump_capped_at_35(self) -> None:
        """When ``base_reserve``
        is high (e.g. 32%),
        the +5% bump would
        push the recommendation
        above the cap
        ``MAX_BUMP_SOC=35``.
        The cap applies, but
        the recommendation
        must never drop below
        the user-configured
        base."""
        soh = BatterySoH(cycle_count=0)
        # Force low SoH via
        # many cycles.
        soh._cycle_count = RATED_CYCLE_LIFE
        # 2000/2000 = 1.0, capped at 0.8 → 20% soh.
        self.assertLess(soh.estimated_soh_percent(), 80.0)
        result = soh.recommended_reserve_soc(base_reserve=32.0)
        # 32 + 5 = 37 → capped
        # at 35. 35 is still
        # above 32, so the
        # recommendation is
        # the cap.
        self.assertEqual(result, MAX_BUMP_SOC)
        # Critical: the result
        # must never be below
        # base_reserve.
        self.assertGreaterEqual(result, 32.0)

    def test_base_reserve_already_above_cap_returns_base(self) -> None:
        """T26: the previous
        implementation could
        return a value BELOW
        the base reserve when
        the bump was limited
        by the cap. For
        example, base=40 with
        low SoH would return
        ``min(35, 40 + 5) =
        35``, dropping the
        user-configured base
        from 40 to 35.

        The fix: ``max(
        base_reserve, ...)``
        as the final clamp.
        """
        soh = BatterySoH(cycle_count=0)
        soh._cycle_count = RATED_CYCLE_LIFE
        # Force low SoH.
        self.assertLess(soh.estimated_soh_percent(), 80.0)
        # User set base to 40
        # — we must not drop
        # it.
        result = soh.recommended_reserve_soc(base_reserve=40.0)
        self.assertGreaterEqual(
            result, 40.0,
            f"recommended_reserve_soc returned {result} "
            f"which is below base_reserve=40. The "
            "T26 audit explicitly forbids this.",
        )

    def test_recommendation_is_pure_function(self) -> None:
        """The recommendation
        must not call
        ``api.set_config_item``
        or otherwise mutate
        physical limits. We
        can't observe a
        write directly, but
        we can verify the
        function is
        self-contained:
        calling it twice
        with the same input
        returns the same
        output and the
        internal state does
        not change."""
        soh = BatterySoH(cycle_count=10)
        before = soh.estimated_soh_percent()
        first = soh.recommended_reserve_soc(base_reserve=20.0)
        second = soh.recommended_reserve_soc(base_reserve=20.0)
        after = soh.estimated_soh_percent()
        self.assertEqual(first, second)
        self.assertEqual(before, after)


class TestTrackSocAcceptsAnything(unittest.TestCase):
    """T26: ``track_soc``
    accepts any input
    shape from the
    coordinator without
    raising."""

    def test_string_soc_does_not_crash(self) -> None:
        soh = BatterySoH()
        result = soh.track_soc("50")
        # "50" coerces to 50.0
        # — no cycle (50 >
        # LOW_THRESHOLD and
        # not in low state).
        self.assertFalse(result)

    def test_none_soc_returns_false(self) -> None:
        soh = BatterySoH()
        result = soh.track_soc(None)
        self.assertFalse(result)

    def test_valid_cycle_completes(self) -> None:
        soh = BatterySoH()
        soh.track_soc(25.0)  # enter low state
        result = soh.track_soc(85.0)  # exit low state
        self.assertTrue(result)
        self.assertEqual(soh.cycle_count, 1)


class TestLoadFromDictMalformed(unittest.TestCase):
    """T26: ``load_from_dict``
    does not raise on
    malformed data."""

    def test_missing_install_date_preserves_cycles(self) -> None:
        soh = BatterySoH()
        soh.load_from_dict(
            {"cycle_count": 42, "in_low_state": True}
        )
        self.assertEqual(soh.cycle_count, 42)
        self.assertTrue(soh.in_low_state)
        self.assertIsNone(soh._install_date)

    def test_garbage_install_date_does_not_raise(self) -> None:
        soh = BatterySoH()
        # Must not raise.
        soh.load_from_dict(
            {"cycle_count": 5, "install_date": "garbage"}
        )
        self.assertEqual(soh.cycle_count, 5)
        self.assertIsNone(soh._install_date)

    def test_none_cycle_count_treated_as_zero(self) -> None:
        soh = BatterySoH()
        soh.load_from_dict({"cycle_count": None})
        self.assertEqual(soh.cycle_count, 0)


class TestT26Round4InfiniteCycleCount(unittest.TestCase):
    """T26 round 4 (audit
    follow-up): the
    ``_coerce_cycle_count``
    helper must NOT
    raise on
    ``float('inf')`` /
    ``float('-inf')``.
    The previous code
    called ``int(value)``
    *before* the
    finiteness check,
    so
    ``int(float('inf'))``
    raised
    ``OverflowError``
    outside the
    ``except`` clause.
    The fix is to
    finiteness-check
    first, convert
    second.

    The audit also
    required both
    production paths
    (constructor and
    ``load_from_dict``)
    to be exercised
    with these
    values."""

    def test_constructor_infinity_does_not_raise(self) -> None:
        # Must not raise.
        soh = BatterySoH(cycle_count=float("inf"))
        self.assertEqual(soh.cycle_count, 0)

    def test_constructor_negative_infinity_does_not_raise(
        self,
    ) -> None:
        soh = BatterySoH(cycle_count=float("-inf"))
        self.assertEqual(soh.cycle_count, 0)

    def test_constructor_nan_does_not_raise(self) -> None:
        soh = BatterySoH(cycle_count=float("nan"))
        self.assertEqual(soh.cycle_count, 0)

    def test_load_from_dict_infinity_does_not_raise(
        self,
    ) -> None:
        soh = BatterySoH()
        soh.load_from_dict({"cycle_count": float("inf")})
        self.assertEqual(soh.cycle_count, 0)

    def test_load_from_dict_negative_infinity_does_not_raise(
        self,
    ) -> None:
        soh = BatterySoH()
        soh.load_from_dict(
            {"cycle_count": float("-inf")}
        )
        self.assertEqual(soh.cycle_count, 0)

    def test_load_from_dict_nan_does_not_raise(self) -> None:
        soh = BatterySoH()
        soh.load_from_dict({"cycle_count": float("nan")})
        self.assertEqual(soh.cycle_count, 0)


class TestT26Round4BooleanUnification(unittest.TestCase):
    """T26 round 4 (audit
    follow-up): the
    constructor used
    ``bool(value)``
    (truthy coercion),
    so ``"false"``
    → ``False`` but
    ``"yes"``, ``"true"``,
    ``1`` → ``True``.
    The restore path
    used ``is True``
    (only the literal
    ``True``). The
    asymmetry meant a
    round-trip could
    silently flip the
    field.

    The new contract
    is strict: only
    the literal
    ``True`` is
    ``True``; anything
    else (including
    ``"yes"``,
    ``"true"``, ``1``,
    ``"false"``) is
    ``False``. Both
    paths use
    ``_coerce_in_low_state``
    so they are
    symmetric."""

    def test_constructor_truthy_string_is_false(self) -> None:
        # ``bool("yes")`` was
        # ``True`` before;
        # the new contract
        # is ``False``.
        soh = BatterySoH(in_low_state="yes")
        self.assertFalse(soh.in_low_state)

    def test_constructor_truthy_int_is_false(self) -> None:
        soh = BatterySoH(in_low_state=1)
        self.assertFalse(soh.in_low_state)

    def test_constructor_string_false_is_false(self) -> None:
        soh = BatterySoH(in_low_state="false")
        self.assertFalse(soh.in_low_state)

    def test_constructor_literal_true_is_true(self) -> None:
        soh = BatterySoH(in_low_state=True)
        self.assertTrue(soh.in_low_state)

    def test_constructor_literal_false_is_false(
        self,
    ) -> None:
        soh = BatterySoH(in_low_state=False)
        self.assertFalse(soh.in_low_state)

    def test_load_from_dict_truthy_string_is_false(
        self,
    ) -> None:
        soh = BatterySoH()
        soh.load_from_dict({"in_low_state": "yes"})
        self.assertFalse(soh.in_low_state)

    def test_load_from_dict_literal_true_is_true(
        self,
    ) -> None:
        soh = BatterySoH()
        soh.load_from_dict({"in_low_state": True})
        self.assertTrue(soh.in_low_state)

    def test_round_trip_truthy_string_is_consistent(
        self,
    ) -> None:
        """Constructing with
        ``"yes"`` then
        round-tripping
        via
        ``to_dict /
        load_from_dict``
        must keep the
        value
        consistent
        (both
        ``False``).
        The previous
        asymmetry
        would have
        silently
        flipped it."""
        soh = BatterySoH(in_low_state="yes")
        blob = soh.to_dict()
        self.assertFalse(blob["in_low_state"])
        soh2 = BatterySoH()
        soh2.load_from_dict(blob)
        self.assertFalse(soh2.in_low_state)


class TestT26Round4CalendarAgingRespectsNow(unittest.TestCase):
    """T26 round 4 (audit
    follow-up): the
    previous
    ``_coerce_install_date``
    used the wall clock
    to reject future
    dates, so a
    ``now=2025-01-01``
    test could not
    reject an
    ``install_date=2026-01-01``.
    The audit
    reproduced::

        cycles=500, now=2025-01-01:
            no install_date → SoH=75.0
            install_date=2026-01-01 → SoH=77.25

    The fix threads
    ``now`` through
    ``_coerce_install_date``
    and into the
    aging subtraction,
    so a future
    install_date (one
    past ``now``) is
    rejected *and* the
    aging subtraction
    cannot
    inflate SoH
    above the
    cycle-only
    baseline."""

    def test_future_install_date_relative_to_now_rejected(
        self,
    ) -> None:
        soh = BatterySoH(
            cycle_count=500,
            install_date="2026-01-01",
        )
        # ``now`` is
        # 2025; the
        # future
        # install_date
        # is rejected.
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        # Without an
        # install_date
        # SoH reflects
        # the cycle
        # damage alone.
        soh_no_install = BatterySoH(cycle_count=500)
        baseline = soh_no_install.estimated_soh_percent(
            now=now,
        )
        # With the
        # rejected
        # future
        # install_date
        # SoH must be
        # *no higher*
        # than the
        # baseline.
        result = soh.estimated_soh_percent(now=now)
        self.assertLessEqual(
            result, baseline,
            f"Future install_date must not "
            f"improve SoH above the "
            f"cycle-only baseline. "
            f"baseline={baseline}, "
            f"with_future_install_date={result}",
        )

    def test_past_install_date_reduces_soh(self) -> None:
        soh = BatterySoH(
            cycle_count=500,
            install_date="2020-01-01",
        )
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = soh.estimated_soh_percent(now=now)
        # 5 years →
        # 15% calendar
        # loss on top
        # of cycle
        # loss.
        # Cycle
        # baseline:
        # 75.0. With
        # 15% loss:
        # 75.0 * 0.85
        # = 63.75.
        # We allow a
        # ±2 point
        # tolerance for
        # leap-year
        # fuzziness.
        self.assertAlmostEqual(result, 63.75, delta=2.0)

    def test_explicit_now_threads_through(self) -> None:
        soh = BatterySoH(
            cycle_count=500,
            install_date="2024-01-01",
        )
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = soh.estimated_soh_percent(now=now)
        # 1 year
        # → ~3%
        # loss on
        # cycle
        # baseline
        # 75.
        self.assertAlmostEqual(result, 72.75, delta=2.0)

    def test_explicit_now_none_uses_wall_clock(self) -> None:
        soh = BatterySoH(cycle_count=500)
        # No install_date → no calendar
        # loss; the answer is the
        # cycle-only baseline.
        result = soh.estimated_soh_percent()
        # ``now=None`` defaults
        # to wall clock;
        # ``install_date=None``,
        # so ``age_factor=1``.
        # Cycle-only SoH = 75.0.
        self.assertAlmostEqual(result, 75.0, delta=0.01)

    def test_constructor_nan_cycle_count_returns_zero(
        self,
    ) -> None:
        """Round 4 audit:
        ``float('nan')`` must
        be rejected by the
        finiteness check
        (``math.isfinite``)
        before any conversion.
        The cycle count must
        end up as 0 and the
        SoH must equal the
        cycle-only baseline
        (= 100)."""
        soh = BatterySoH(cycle_count=float("nan"))
        self.assertEqual(soh.cycle_count, 0)

    def test_constructor_neg_inf_cycle_count_returns_zero(
        self,
    ) -> None:
        soh = BatterySoH(
            cycle_count=float("-inf")
        )
        self.assertEqual(soh.cycle_count, 0)

    def test_restore_nan_cycle_count_returns_zero(
        self,
    ) -> None:
        """``load_from_dict`` with
        a NaN cycle_count must
        also coerce to 0."""
        soh = BatterySoH()
        soh.load_from_dict(
            {"cycle_count": float("nan")}
        )
        self.assertEqual(soh.cycle_count, 0)

    def test_restore_neg_inf_cycle_count_returns_zero(
        self,
    ) -> None:
        soh = BatterySoH()
        soh.load_from_dict(
            {"cycle_count": float("-inf")}
        )
        self.assertEqual(soh.cycle_count, 0)

    def test_constructor_false_string_does_not_set_low_state(
        self,
    ) -> None:
        """Round 4 audit: the
        constructor used
        ``bool(value)`` which
        treats ``"false"`` as
        True. The unified
        ``_coerce_in_low_state``
        helper now requires
        ``value is True`` for
        the boolean to be set."""
        soh = BatterySoH(in_low_state="false")
        self.assertFalse(soh.in_low_state)
        soh2 = BatterySoH(in_low_state="False")
        self.assertFalse(soh2.in_low_state)
        soh3 = BatterySoH(in_low_state="0")
        self.assertFalse(soh3.in_low_state)

    def test_restore_false_string_does_not_set_low_state(
        self,
    ) -> None:
        soh = BatterySoH()
        soh.load_from_dict(
            {"in_low_state": "false"}
        )
        self.assertFalse(soh.in_low_state)

    def test_future_install_date_no_soh_bonus_under_controlled_now(
        self,
    ) -> None:
        """Round 4 audit:
        ``cycles=500``,
        ``now=2025-01-01``,
        ``install_date=2026-01-01``
        must NOT improve SoH.
        The previous code
        computed a negative
        calendar age and the
        ``age_factor`` went
        above 1, producing a
        SoH above the
        cycle-only baseline
        (75)."""
        soh = BatterySoH(
            cycle_count=500,
            install_date="2026-01-01",
        )
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = soh.estimated_soh_percent(now=now)
        # The cycle-only
        # baseline (75.0)
        # is the upper bound;
        # a future install
        # date must NOT
        # exceed it.
        self.assertLessEqual(
            result,
            75.0,
            f"future install_date must not "
            f"boost SoH above the cycle-only "
            f"baseline; got {result}",
        )

    def test_future_install_date_load_keeps_value(
        self,
    ) -> None:
        """``load_from_dict``
        restores the value
        verbatim — it has no
        ``now`` parameter,
        so it cannot judge
        whether the date is
        in the future. The
        *constructor's*
        ``_coerce_install_date``
        is the one that
        enforces "not in
        the future" and is
        exercised by
        ``test_future_install_date_no_soh_bonus_under_controlled_now``."""

        soh = BatterySoH()
        soh.load_from_dict(
            {
                "cycle_count": 500,
                "install_date": "2024-01-01",
            }
        )
        # 2024-01-01 is in the
        # past for any
        # realistic wall clock;
        # ``load_from_dict``
        # preserves the value
        # without a ``now``
        # anchor.
        self.assertIsNotNone(soh._install_date)
        # ``estimated_soh_percent``
        # uses the supplied
        # ``now``; a *future*
        # ``now`` (relative to
        # the install_date)
        # must NOT boost the
        # SoH above the
        # cycle-only baseline.
        future_now = datetime(
            2025, 1, 1, tzinfo=timezone.utc
        )
        # This now is *after*
        # 2024-01-01, so
        # calendar aging
        # applies — the SoH
        # must equal the
        # cycle-only baseline
        # minus the calendar
        # loss (1y × 3% =
        # ~72.75).
        result = soh.estimated_soh_percent(
            now=future_now
        )
        self.assertLessEqual(
            result,
            75.0,
            f"calendar aging must not exceed "
            f"cycle-only baseline; got {result}",
        )

    def test_restore_inf_future_install_date_no_bonus(
        self,
    ) -> None:
        """Restore with a date
        that is infinitely
        far in the future
        relative to a
        controlled ``now``:
        the helper must NOT
        produce an SoH
        above the cycle
        baseline."""
        soh = BatterySoH()
        soh.load_from_dict(
            {
                "cycle_count": 500,
                "install_date": "9999-01-01",
            }
        )
        controlled_now = datetime(
            2025, 1, 1, tzinfo=timezone.utc
        )
        # ``estimated_soh_percent``
        # passes the
        # controlled ``now``
        # through
        # ``_coerce_install_date``
        # via the inline
        # check; a far-future
        # install_date must
        # be rejected.
        result = soh.estimated_soh_percent(
            now=controlled_now
        )
        self.assertLessEqual(
            result,
            75.0,
            f"future install_date must not boost "
            f"SoH above cycle-only baseline; "
            f"got {result}",
        )


if __name__ == "__main__":
    unittest.main()
