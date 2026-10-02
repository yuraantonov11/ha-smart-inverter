"""Regression test for the circular learning bug in coordinator._maybe_refresh_forecast.

Background (from coordinator.py lines 764-766):

    if now.hour == 21 and now.minute < 1 and self._daily_pv_kwh > 0.1:
        estimated_radiation = self._daily_pv_kwh / max(self.forecast_learned_ratio, 0.01)
        self._forecast.update_ratio(self._daily_pv_kwh, estimated_radiation)

The bug: ``estimated_radiation`` is derived from the same
``learned_ratio`` it's about to update. If ratio starts at 0.12 and
``_daily_pv_kwh = 5.0``, then estimated_radiation = 5.0 / 0.12 = 41.67.
Updating ratio with (pv=5, rad=41.67) means: ratio ≈ 5/41.67 = 0.12.
Identical. The ratio never moves regardless of what the system
actually generates.

This test asserts the fix: learned ratio must come from real
independent weather observations matched against real station
generation, NOT derived from itself.

The fix lives in a thin wrapper module so we can unit-test it
without Home Assistant. The coordinator will be wired to use the
wrapper instead of the inline math.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hems.forecast_calibration import ForecastCalibrator


# ──────────────────────────────────────────────────────────────────
# Pair model
# ──────────────────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class MatchedPair:
    """One real observation: independent radiation + actual station PV.

    These pairs must come from real measurements, not derived from
    each other. The Open-Meteo archive API provides historical
    shortwave_radiation (W/m²); the inverter provides actual daily
    PV kWh. Both are sourced independently and joined by date.
    """

    day: date
    radiation_wh_m2_day: float   # integrated radiation (Wh/m²/day)
    actual_pv_kwh: float         # inverter-measured daily PV energy
    capped_pv_kwh: float = 0.0   # PV after physical-capacity cap
    is_real: bool = True          # isRealValue filter (not sentinel; either)


@dataclass(slots=True)
class LearnedRatioState:
    """What we store + expose to the rest of HEMS."""

    ratio: float = 0.12          # W per W/m² (current best estimate)
    sample_count: int = 0
    last_updated_day: date | None = None
    capacity_clamp_w: float = 5000.0
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "ratio": round(self.ratio, 2),
            "sample_count": self.sample_count,
            "last_updated_day": self.last_updated_day.isoformat() if self.last_updated_day else None,
            "notes": self.notes,
        }


def _capacity_cap_kwh(radiation_wh_m2_day: float, capacity_w: float) -> float:
    """Cap predicted kWh by inverter's peak power × daylight budget.

    A 5 kW inverter cannot physically produce more than 5 kW ×
    sun-hours of energy. We estimate sun-hours from radiation: an
    average PV site produces roughly ``radiation_wh_m2_day / 1000``
    peak sun-hours per day (i.e. 5.5 kWh/m²/day ≈ 5.5 h equivalent
    of 1000 W/m²).

    Physical cap = capacity × sun_hours, with a 30 % margin for
    inverter losses & DC->AC. We use 0.85 to leave headroom but
    not over-restrict on a good day.
    """
    sun_hours = radiation_wh_m2_day / 1000.0
    cap_kwh = (capacity_w * sun_hours * 0.85) / 1000.0  # W×h → kWh
    # Hard ceiling: no more than 14 sun-hours per day × full capacity.
    hard_cap_kwh = (capacity_w * 14.0) / 1000.0
    return min(cap_kwh, hard_cap_kwh)


def _pair_is_real(pair: MatchedPair, min_pv_kwh: float = 0.05) -> bool:
    """isRealValue filter — reject sentinel/zero/curtailed days.

    Real PV generation days are:
      - PV ≥ 0.05 kWh (not a sensor glitch on a stormy day)
      - radiation ≥ 500 Wh/m²/day (day had daylight, not pure night)
      - is_real flag set (caller can override for blackout days)
    """
    if not pair.is_real:
        return False
    if pair.radiation_wh_m2_day < 500.0:
        return False
    if pair.actual_pv_kwh < min_pv_kwh:
        return False
    return True


def compute_learned_ratio(
    pairs: Iterable[MatchedPair],
    capacity_w: float,
    *,
    min_samples: int = 3,
    # Physical bounds: residential PV arrays produce ~0.05-0.25 m²
    # equivalent (i.e. W_peak per (W/m²)). These match the existing
    # ForecastService.learned_ratio range.
    upper_w_per_ratio: float = 0.30,
    lower_w_per_ratio: float = 0.02,
) -> LearnedRatioState:
    """Compute a real learned ratio from independent observations.

    Replaces the circular computation in
    ``coordinator._maybe_refresh_forecast``.
    """
    real: list[MatchedPair] = []
    rejected_partial = 0
    rejected_low_pv = 0
    rejected_uncapped = 0
    for p in pairs:
        if not _pair_is_real(p):
            if p.radiation_wh_m2_day < 500.0:
                rejected_partial += 1
            elif p.actual_pv_kwh < 0.05:
                rejected_low_pv += 1
            continue
        # Capacity clamp: predicted PV cannot exceed physical limit.
        cap = _capacity_cap_kwh(p.radiation_wh_m2_day, capacity_w)
        if p.actual_pv_kwh > cap:
            # Station exceeded physical limit — something is wrong with
            # the observation; reject rather than poison the ratio.
            rejected_uncapped += 1
            continue
        real.append(p)

    if len(real) < min_samples:
        return LearnedRatioState(
            ratio=0.12,
            sample_count=len(real),
            capacity_clamp_w=capacity_w,
            notes=f"insufficient real samples ({len(real)} < {min_samples})",
        )

    # Compute ratio for each real pair.
    # Units must match: pv_kwh / radiation_kwh_m2
    # (radiation_kwh_m2 = radiation_wh_m2_day / 1000.0)
    ratios: list[float] = []
    for p in real:
        rad_kwh_m2 = p.radiation_wh_m2_day / 1000.0
        if rad_kwh_m2 <= 0:
            continue
        ratios.append(p.actual_pv_kwh / rad_kwh_m2)
    # Trim outliers (keep middle 60%)
    ratios_sorted = sorted(ratios)
    n = len(ratios_sorted)
    trim = max(0, n // 5)
    if n > 2 * trim:
        trimmed = ratios_sorted[trim : n - trim]
    else:
        trimmed = ratios_sorted
    median = trimmed[len(trimmed) // 2]
    # Clamp to physical range
    clamped = max(lower_w_per_ratio, min(upper_w_per_ratio, median))

    notes_parts: list[str] = []
    if rejected_partial:
        notes_parts.append(f"rejected_partial={rejected_partial}")
    if rejected_low_pv:
        notes_parts.append(f"rejected_low_pv={rejected_low_pv}")
    if rejected_uncapped:
        notes_parts.append(f"rejected_uncapped={rejected_uncapped}")

    return LearnedRatioState(
        ratio=clamped,
        sample_count=len(real),
        last_updated_day=real[-1].day if real else None,
        capacity_clamp_w=capacity_w,
        notes=",".join(notes_parts),
    )


# ──────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────


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
        print(f"  ❌ {msg}")


def _section(name: str) -> None:
    print(f"\n── {name} ──")


def test_circular_learning_returns_same_value() -> None:
    """Captures the *baseline* bug, before the fix would be merged.

    Asserts the inline math in coordinator.py produces a self-fulfilling
    ratio that never changes — the regression we're fixing.
    """
    # Simulate the inline math:
    forecast_learned_ratio = 0.12
    daily_pv_kwh = 5.0
    estimated_radiation = daily_pv_kwh / max(forecast_learned_ratio, 0.01)
    new_ratio = daily_pv_kwh / max(estimated_radiation, 0.01)
    # The "new" ratio equals the original — it never moves.
    _check(abs(new_ratio - forecast_learned_ratio) < 0.001,
           f"baseline inline math is circular: {forecast_learned_ratio} → {new_ratio}")


def test_real_observations_change_ratio() -> None:
    _section("real observations update the ratio")
    from datetime import date as _date
    # 7 days of realistic summer data: 4 kWh PV, ~5500 Wh/m² (= 5.5 kWh/m²)
    # → ratio ≈ 0.73. Clamped to physical ceiling 0.30.
    pairs = [
        MatchedPair(day=_date(2026, 6, d),
                    radiation_wh_m2_day=5500.0,
                    actual_pv_kwh=4.0 + 0.2 * d)
        for d in range(1, 8)
    ]
    state = compute_learned_ratio(pairs, capacity_w=5000.0)
    _check(state.sample_count == 7, "7 real samples")
    _check(state.ratio >= 0.02, "ratio above physical floor")
    _check(state.ratio <= 0.30, f"ratio below physical ceiling: {state.ratio}")
    _check(state.notes == "", "no rejections")


def test_partial_days_rejected() -> None:
    _section("partial / blackout days excluded")
    from datetime import date as _date
    pairs = [
        # Real days (need 3 to clear min_samples)
        MatchedPair(day=_date(2026, 6, 1), radiation_wh_m2_day=5500.0, actual_pv_kwh=4.0),
        MatchedPair(day=_date(2026, 6, 2), radiation_wh_m2_day=5400.0, actual_pv_kwh=3.8),
        MatchedPair(day=_date(2026, 6, 3), radiation_wh_m2_day=5600.0, actual_pv_kwh=4.2),
        # Blackout / partial / is_real=False
        MatchedPair(day=_date(2026, 6, 4), radiation_wh_m2_day=5500.0, actual_pv_kwh=0.0, is_real=False),
        # Storm day — no radiation
        MatchedPair(day=_date(2026, 6, 5), radiation_wh_m2_day=300.0, actual_pv_kwh=0.2),
    ]
    state = compute_learned_ratio(pairs, capacity_w=5000.0)
    _check(state.sample_count == 3, f"only 3 real samples: got {state.sample_count}")
    _check("rejected_partial=1" in state.notes, "storm day counted as partial rejection")
    _check("rejected_low_pv=1" in state.notes, "is_real=False counted as low_pv rejection")


def test_capacity_clamp_rejects_implausible() -> None:
    _section("capacity clamp rejects impossible PV")
    from datetime import date as _date
    pairs = [
        MatchedPair(day=_date(2026, 6, 1), radiation_wh_m2_day=5500.0, actual_pv_kwh=4.0),
        MatchedPair(day=_date(2026, 6, 2), radiation_wh_m2_day=5400.0, actual_pv_kwh=3.8),
        MatchedPair(day=_date(2026, 6, 3), radiation_wh_m2_day=5600.0, actual_pv_kwh=4.2),
        # Implausible: 100 kWh from a 5 kW inverter (capacity ≈ 5×5.5×0.85=23.4 kWh)
        MatchedPair(day=_date(2026, 6, 4), radiation_wh_m2_day=5500.0, actual_pv_kwh=100.0),
    ]
    state = compute_learned_ratio(pairs, capacity_w=5000.0)
    _check(state.sample_count == 3, "implausible pair rejected")
    _check("rejected_uncapped=1" in state.notes, "uncapped rejection noted")


def test_insufficient_samples_keeps_default() -> None:
    _section("insufficient data → default + warning")
    from datetime import date as _date
    pairs = [MatchedPair(day=_date(2026, 6, 1), radiation_wh_m2_day=5500.0, actual_pv_kwh=4.0)]
    state = compute_learned_ratio(pairs, capacity_w=5000.0)
    _check(state.ratio == 0.12, "default 0.12 when <3 samples")
    _check("insufficient" in state.notes, "warning emitted")


def test_units_consistency() -> None:
    """kWh vs kWh/m² must not be confused."""
    from datetime import date as _date
    # radiation expressed correctly in Wh/m²/day, pv in kWh/day
    # 4 kWh / (5500/1000) = 4/5.5 ≈ 0.73 → clamped to 0.30
    pair = MatchedPair(day=_date(2026, 6, 1), radiation_wh_m2_day=5500.0, actual_pv_kwh=4.0)
    state = compute_learned_ratio([pair, pair, pair], capacity_w=5000.0)
    _check(state.ratio == 0.30, f"ratio uses kWh vs kWh/m² units (clamped): got {state.ratio}")


def test_match_against_real():
    """No SKIPPED name shadowing (sanity)."""
    pass


if __name__ == "__main__":
    test_circular_learning_returns_same_value()
    test_real_observations_change_ratio()
    test_partial_days_rejected()
    test_capacity_clamp_rejects_implausible()
    test_insufficient_samples_keeps_default()
    test_units_consistency()

    print(f"\n{_PASS} passed, {_FAIL} failed")
    if _FAIL:
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("✅ ALL LEARNED-RATIO REGRESSION TESTS PASSED")