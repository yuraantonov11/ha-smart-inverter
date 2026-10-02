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
        print(f"  PASS: {msg}")
    else:
        _F += 1
        _FAILURES.append(msg)
        print(f"  FAIL: {msg}")


def _section(name: str) -> None:
    print(f"\n── {name} ──")


def test_a_single_pair_under_single_double() -> None:
    _section("(a) one pair forecast=4.0 actual=2.0 → bias=-2.0 conf<0.5")
    c = ForecastCalibrator(unit="kWh")
    c.record(forecast_w=4.0, actual_w=2.0)  # 4 kWh vs 2 kWh
    m = c.metrics()
    _check(
        m.bias_w == -2.0,
        f"bias_w=-2.0 kWh (got {m.bias_w})",
    )
    _check(
        m.mae_w == 2.0,
        f"mae_w=2.0 kWh (got {m.mae_w})",
    )
    _check(
        m.sample_count == 1,
        f"sample_count=1 (got {m.sample_count})",
    )
    _check(
        m.confidence_factor < 0.5,
        f"confidence_factor<0.5 for one over-forecast pair (got {m.confidence_factor})",
    )
    _check(
        m.coverage == 1.0,
        f"complete pair coverage=1.0 (got {m.coverage})",
    )


def test_b_ten_pairs_exact_match() -> None:
    _section("(b) 10 pairs fc=ac±0.1 → conf > 0.8")
    c = ForecastCalibrator(unit="kWh")
    for i in range(10):
        fc = 4.0
        ac = 4.0 + (0.1 if i % 2 else -0.1)  # ±0.1 kWh
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
        abs(m.mae_w - 0.1) < 1e-8,
        f"mae_w=0.1 kWh (got {m.mae_w})",
    )


def test_c_five_pairs_5x_overforecast() -> None:
    _section("(c) 5 pairs fc=5.0 ac=0.5 → conf<0.3")
    c = ForecastCalibrator(unit="kWh")
    for _ in range(5):
        c.record(forecast_w=5.0, actual_w=0.5)  # 5 kWh vs 0.5 kWh
    m = c.metrics()
    _check(
        m.sample_count == 5,
        f"sample_count=5 (got {m.sample_count})",
    )
    _check(
        m.bias_w == -4.5,
        f"bias_w=-4.5 kWh for systematic 10× over-forecast (got {m.bias_w})",
    )
    _check(
        m.confidence_factor < 0.3,
        f"confidence_factor below required threshold for wildly over-forecast (got {m.confidence_factor})",
    )
    _check(
        m.mae_w == 4.5,
        f"mae_w=4.5 kWh for 10× over-forecast (got {m.mae_w})",
    )


def test_d_empty_calibrator() -> None:
    _section("(d) empty calibrator → conf=0.0 sample_count=0")
    c = ForecastCalibrator(unit="kWh")
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
