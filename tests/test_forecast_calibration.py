"""Standalone runner for forecast calibration RED→GREEN verification.

Covers the calibration metrics + bounded buffer + outlier rejection
that replaces the old ``PvForecastAdjuster``.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hems.forecast_calibration import ForecastCalibrator, CalibrationMetrics


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


def test_metrics_with_no_samples() -> None:
    _section("empty calibrator")
    c = ForecastCalibrator()
    m = c.metrics()
    _check(m.sample_count == 0, "empty: sample_count=0")
    _check(m.mae_w == 0.0, "empty: mae=0")
    _check(m.bias_w == 0.0, "empty: bias=0")
    _check(m.confidence_factor == 0.0, "empty: confidence=0")


def test_metrics_known_constant() -> None:
    _section("known constant: forecast=actual")
    c = ForecastCalibrator()
    # 10 samples, all match exactly
    for _ in range(10):
        c.record(forecast_w=1000.0, actual_w=1000.0)
    m = c.metrics()
    _check(m.sample_count == 10, "10 samples kept")
    _check(m.mae_w == 0.0, "MAE=0 when exact")
    _check(m.bias_w == 0.0, "bias=0 when exact")
    _check(m.coverage == 1.0, "coverage=1.0")
    _check(m.confidence_factor == 1.0, "confidence=1.0 for exact match")


def test_metrics_with_systematic_bias() -> None:
    _section("systematic 20% over-forecast")
    c = ForecastCalibrator()
    # forecast always 1000, actual always 800
    for _ in range(10):
        c.record(forecast_w=1000.0, actual_w=800.0)
    m = c.metrics()
    _check(m.bias_w == -200.0, "bias=-200W (over-forecast)")
    _check(m.mae_w == 200.0, "MAE=200W")
    _check(m.confidence_factor > 0.0, "confidence>0 even with bias")


def test_adjust_corrects_bias() -> None:
    _section("adjust() corrects bias")
    c = ForecastCalibrator()
    for _ in range(10):
        c.record(forecast_w=1000.0, actual_w=800.0)
    adjusted = c.adjust(1000.0)
    # Should be close to 800 (negative bias of -200 → add 200 to forecast)
    _check(800.0 <= adjusted <= 850.0, f"adjusted in [800,850]: got {adjusted}")


def test_adjust_too_few_samples_passthrough() -> None:
    _section("adjust() needs enough data")
    c = ForecastCalibrator()
    # Only 2 samples (below MIN_SAMPLES_FOR_ADJUST=4)
    c.record(forecast_w=1000.0, actual_w=500.0)
    c.record(forecast_w=1000.0, actual_w=500.0)
    adjusted = c.adjust(1000.0)
    _check(adjusted == 1000.0, "insufficient data → identity")


def test_outlier_rejection() -> None:
    _section("outlier rejection (single bad sample)")
    c = ForecastCalibrator()
    # 9 good samples (fc=1000, ac=1000), 1 insane outlier (fc=1000, ac=100000)
    for _ in range(9):
        c.record(forecast_w=1000.0, actual_w=1000.0)
    c.record(forecast_w=1000.0, actual_w=100_000.0)
    m = c.metrics()
    # Outlier should be dropped, so MAE ~ 0 not 9000
    _check(m.mae_w < 500.0, f"MAE ignores outlier: got {m.mae_w}")


def test_bounded_buffer() -> None:
    _section("bounded rolling buffer")
    c = ForecastCalibrator(max_samples=5)
    for _ in range(20):
        c.record(forecast_w=1000.0, actual_w=1000.0)
    _check(len(c) == 5, f"len capped at 5: got {len(c)}")


def test_invalid_inputs_dropped() -> None:
    _section("invalid inputs silently dropped")
    c = ForecastCalibrator()
    c.record(forecast_w=float("nan"), actual_w=1000.0)
    c.record(forecast_w=1000.0, actual_w=float("nan"))
    c.record(forecast_w=-100.0, actual_w=1000.0)
    c.record(forecast_w=1000.0, actual_w=-1000.0)
    c.record(forecast_w=999_999.0, actual_w=1000.0)  # out-of-range
    _check(len(c) == 0, f"no valid samples kept: got {len(c)}")


def test_low_light_samples_ignored_for_metrics() -> None:
    _section("low-light samples ignored")
    c = ForecastCalibrator()
    # 5 samples at forecast<50 (ignored for metrics but counted in len)
    for _ in range(5):
        c.record(forecast_w=20.0, actual_w=10.0)
    # 5 samples at forecast>=50 (counted)
    for _ in range(5):
        c.record(forecast_w=1000.0, actual_w=1000.0)
    _check(len(c) == 10, f"buffer holds all 10: got {len(c)}")
    m = c.metrics()
    _check(m.sample_count == 5, f"metrics sees only 5: got {m.sample_count}")


def test_serialization_roundtrip() -> None:
    _section("to_list / load_from_list")
    c = ForecastCalibrator()
    for fc, ac in [(1000, 900), (1100, 1050), (950, 920)]:
        c.record(forecast_w=fc, actual_w=ac)
    data = c.to_list()
    c2 = ForecastCalibrator()
    c2.load_from_list(data)
    _check(len(c2) == 3, "roundtrip preserves 3 samples")
    _check(c2.metrics().bias_w == c.metrics().bias_w, "metrics match")


if __name__ == "__main__":
    test_metrics_with_no_samples()
    test_metrics_known_constant()
    test_metrics_with_systematic_bias()
    test_adjust_corrects_bias()
    test_adjust_too_few_samples_passthrough()
    test_outlier_rejection()
    test_bounded_buffer()
    test_invalid_inputs_dropped()
    test_low_light_samples_ignored_for_metrics()
    test_serialization_roundtrip()

    print(f"\n{_PASS} passed, {_FAIL} failed")
    if _FAIL:
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("✅ ALL CALIBRATION TESTS PASSED")