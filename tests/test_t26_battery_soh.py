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


if __name__ == "__main__":
    unittest.main()
