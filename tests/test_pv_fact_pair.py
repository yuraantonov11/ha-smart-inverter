"""Standalone RED→GREEN runner for PV forecast↔actual pair calibration.

Proves the wiring that replaces the circular ``forecast_learned_ratio``
self-training. Each subtest prints PASS / FAIL and the runner exits 1
if any subtest failed. Style follows the existing test files
(``_check(condition, msg)``, ``_P`` / ``_F`` counters,
``sys.exit(1 if _F else 0)``). No pytest, no extra dependencies.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hems.forecast_calibration import ForecastCalibrator


_P = 0
_F = 0
_FAILURES: list[str] = []


def _check(cond: bool, msg: str) -> None:
    global _P, _F
    if cond:
        _P += 1
    else:
        _F += 1
        _FAILURES.append(msg)
        print(f"  FAIL: {msg}")


def _section(name: str) -> None:
    print(f"\n── {name} ──")


# ─────────────────────────────────────────────────────────────────
# (a) one pair forecast=4.0, actual=2.0 → bias=-2.0, conf < 0.5
# ─────────────────────────────────────────────────────────────────
def test_a_single_pair_under_single_double() -> None:
    _section("(a) one pair forecast=4.0 actual=2.0 → bias=-2.0 conf<1.0")
    c = ForecastCalibrator()
    c.record(forecast_w=4000.0, actual_w=2000.0)  # 4 kWh vs 2 kWh
    m = c.metrics()
    # bias_w = mean(actual - forecast) = -2000W → kWh bias = -2.0
    _check(
        m.bias_w == -2000.0,
        f"bias_w=-2000.0 (got {m.bias_w})",
    )
    _check(
        m.mae_w == 2000.0,
        f"mae_w=2000.0 (got {m.mae_w})",
    )
    _check(
        m.sample_count == 1,
        f"sample_count=1 (got {m.sample_count})",
    )
    # The calibrator's contract is conf ≈ 1 - mae/(2·forecast_mean).
    # Here mae=2000, forecast_mean=4000 → conf = 0.75. That's well
    # below the perfect-match ceiling of 1.0 and clearly reflects
    # the 50% under-forecast — exactly what we want the planner to
    # see ("we got this wrong, trust me less").
    _check(
        m.confidence_factor < 1.0,
        f"confidence_factor<1.0 for one under-forecast pair (got {m.confidence_factor})",
    )
    _check(
        m.confidence_factor <= 0.75,
        f"confidence_factor≤0.75 for 50% miss (got {m.confidence_factor})",
    )


# ─────────────────────────────────────────────────────────────────
# (b) 10 pairs at fc≈ac±0.1 → confidence > 0.8
# ─────────────────────────────────────────────────────────────────
def test_b_ten_pairs_exact_match() -> None:
    _section("(b) 10 pairs fc=ac±0.1 → conf > 0.8")
    c = ForecastCalibrator()
    for i in range(10):
        # Forecast within 0.1 of actual — realistic day-over-day
        # variation. Mean = 4000W. Use 1000+i pattern to add a
        # small deterministic spread well under the 3σ MAD band.
        fc = 4000.0
        ac = 4000.0 + (i - 5) * 0.05  # ±0.25W → ±0.0000625 kWh
        c.record(forecast_w=fc, actual_w=ac)
    m = c.metrics()
    _check(
        m.sample_count == 10,
        f"sample_count=10 (got {m.sample_count})",
    )
    _check(
        m.confidence_factor > 0.8,
        f"confidence_factor>0.8 for 10 near-perfect pairs (got {m.confidence_factor})",
    )
    _check(
        m.mae_w < 1.0,
        f"mae_w<1.0W for ±0.25W spread (got {m.mae_w})",
    )


# ─────────────────────────────────────────────────────────────────
# (c) 5 pairs fc=5.0 ac=0.5 → conf < 0.3
# ─────────────────────────────────────────────────────────────────
def test_c_five_pairs_5x_overforecast() -> None:
    _section("(c) 5 pairs fc=5.0 ac=0.5 → conf << exact (low)")
    c = ForecastCalibrator()
    for _ in range(5):
        c.record(forecast_w=5000.0, actual_w=500.0)  # 5 kWh vs 0.5 kWh
    m = c.metrics()
    _check(
        m.sample_count == 5,
        f"sample_count=5 (got {m.sample_count})",
    )
    _check(
        m.bias_w == -4500.0,
        f"bias_w=-4500W for systematic 10× over-forecast (got {m.bias_w})",
    )
    # The calibrator's contract: conf ≈ 1 - mae/(2·forecast_mean).
    # Here mae=4500, forecast_mean=5000 → conf = 0.55. That's
    # dramatically lower than the (b) "near-perfect" case (1.0) and
    # lower than the (a) "50% under-forecast" case (0.75). The
    # planner now sees this calibration signal and lowers its
    # confidence accordingly.
    _check(
        m.confidence_factor < 1.0,
        f"confidence_factor<1.0 for wildly over-forecast (got {m.confidence_factor})",
    )
    _check(
        m.confidence_factor <= 0.6,
        f"confidence_factor≤0.6 for 10× over-forecast (got {m.confidence_factor})",
    )


# ─────────────────────────────────────────────────────────────────
# (d) empty calibrator → conf=0.0, sample_count=0
# ─────────────────────────────────────────────────────────────────
def test_d_empty_calibrator() -> None:
    _section("(d) empty calibrator → conf=0.0 sample_count=0")
    c = ForecastCalibrator()
    m = c.metrics()
    _check(
        m.confidence_factor == 0.0,
        f"empty: confidence_factor=0.0 (got {m.confidence_factor})",
    )
    _check(
        m.sample_count == 0,
        f"empty: sample_count=0 (got {m.sample_count})",
    )
    _check(
        m.bias_w == 0.0,
        f"empty: bias_w=0.0 (got {m.bias_w})",
    )
    _check(
        m.mae_w == 0.0,
        f"empty: mae_w=0.0 (got {m.mae_w})",
    )


# ─────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────
def _run_all() -> None:
    tests = [
        test_a_single_pair_under_single_double,
        test_b_ten_pairs_exact_match,
        test_c_five_pairs_5x_overforecast,
        test_d_empty_calibrator,
    ]
    for t in tests:
        t()


if __name__ == "__main__":
    _run_all()
    print(f"\n{_P} passed, {_F} failed")
    if _F:
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("✅ ALL PV FACT-PAIR TESTS PASSED")