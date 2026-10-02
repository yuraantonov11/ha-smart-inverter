"""Forecast calibration for Predictive planner.

The original ``PvForecastAdjuster`` in ``predictive.py`` used a rolling
list of 50 samples and a 20 % trimmed mean. That produces a number
called ``ratio`` that becomes part of the planner confidence — but
nothing ever measured actual MAE/bias, so the user could not tell
whether their forecasts were systematically too low or too high.

This module:
- stores up to ``max_samples`` (default 30 days of hourly) of
  ``(forecast, actual)`` pairs in a bounded rolling buffer
- exposes ``metrics()`` that returns MAE, bias, sample-count, and a
  confidence factor that the planner uses directly
- rejects outliers (>3σ or <0) so a sensor glitch can't poison
  the mean
- has zero external dependencies (no numpy/pandas — keeps the
  integration package installable in any HA environment)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class CalibrationMetrics:
    """Real, measured forecast accuracy.

    All values are simple scalars so they round-trip cleanly to
    HA sensor state_attributes for the dashboard.
    """

    sample_count: int
    mae_w: float          # W by default; kWh in daily mode (legacy field name)
    bias_w: float         # same unit; mean(actual - forecast)
    coverage: float       # 0.0-1.0, fraction of valid samples
    confidence_factor: float  # 0.0-1.0, calibrated (not fake)

    def as_dict(self) -> dict[str, float | int]:
        return {
            "sample_count": int(self.sample_count),
            "mae_w": round(self.mae_w, 1),
            "bias_w": round(self.bias_w, 1),
            "coverage": round(self.coverage, 2),
            "confidence_factor": round(self.confidence_factor, 2),
        }


class ForecastCalibrator:
    """Bounded, outlier-robust forecast calibrator.

    Usage:
        c = ForecastCalibrator()
        c.record(forecast_w=2400, actual_w=1800)
        ...
        metrics = c.metrics()
        adjusted = c.adjust(forecast_w=2000)  # raw forecast
    """

    MIN_SAMPLES_FOR_ADJUST = 4
    MAX_SAMPLES = 720            # 30 days at 1 sample/hour
    MIN_FORECAST_W = 50.0         # ignore samples where forecast < this (low-light noise)
    OUTLIER_SIGMA = 3.0

    def __init__(self, max_samples: int = MAX_SAMPLES, *, unit: str = "W") -> None:
        if unit not in ("W", "kWh"):
            raise ValueError("unit must be W or kWh")
        self.unit = unit
        if max_samples < 0:
            max_samples = 0
        self._max = int(max_samples)
        self._samples: list[tuple[float, float]] = []  # (forecast, actual)
        self._dirty = True
        self._cached: CalibrationMetrics | None = None

    def __len__(self) -> int:
        return len(self._samples)

    def record(self, forecast_w: float, actual_w: float, *, ts_unix: float | None = None) -> None:
        """Append a sample. Oldest samples are dropped to enforce the cap."""
        try:
            fc = float(forecast_w)
            ac = float(actual_w)
        except (TypeError, ValueError):
            return
        if not math.isfinite(fc) or not math.isfinite(ac):
            return
        if fc < 0 or ac < 0:
            return
        ceiling = 50_000 if self.unit == "W" else 500
        if fc > ceiling or ac > ceiling:
            return
        self._samples.append((fc, ac))
        if len(self._samples) > self._max:
            drop = len(self._samples) - self._max
            self._samples = self._samples[drop:]
        self._dirty = True
        self._cached = None

    def extend(self, samples: Iterable[tuple[float, float]]) -> None:
        for fc, ac in samples:
            self.record(fc, ac)

    def reset(self) -> None:
        self._samples.clear()
        self._dirty = True
        self._cached = None

    # ─────────────────────────────────────────────────────────────
    # Metrics
    # ─────────────────────────────────────────────────────────────

    def metrics(self) -> CalibrationMetrics:
        """Return measured metrics. Recomputed only on dirty state."""
        if self._cached is not None and not self._dirty:
            return self._cached
        m = self._compute()
        self._cached = m
        self._dirty = False
        return m

    def _compute(self) -> CalibrationMetrics:
        n_total = len(self._samples)
        if n_total == 0:
            return CalibrationMetrics(0, 0.0, 0.0, 0.0, 0.0)

        # Reject low-light + non-finite and NaN/inf already filtered in record()
        valid = [
            (fc, ac) for fc, ac in self._samples
            if fc >= (self.MIN_FORECAST_W if self.unit == "W" else 0) and ac >= 0
        ]
        n = len(valid)
        coverage = n / n_total if n_total else 0.0
        if n == 0:
            return CalibrationMetrics(0, 0.0, 0.0, coverage, 0.0)

        # Outlier rejection: drop samples whose residual is >OUTLIER_SIGMA
        # from the median residual. Median is robust to a single outlier.
        residuals = sorted(ac - fc for fc, ac in valid)
        median = residuals[n // 2]
        # Median absolute deviation
        abs_dev = sorted(abs(r - median) for r in residuals)
        mad = abs_dev[n // 2]
        # Robust sigma estimator (Gaussian-consistent): MAD * 1.4826
        sigma = max(1.0 if self.unit == "W" else 0.001, mad * 1.4826)

        kept = [(fc, ac) for fc, ac in valid if abs((ac - fc) - median) <= self.OUTLIER_SIGMA * sigma]
        if self.unit == "kWh":
            # Complete, measured bad forecast days are evidence, not glitches.
            # Sensor coverage was checked before daily pairs were recorded.
            kept = valid
        if not kept:
            kept = valid  # never end up with zero samples if we had any

        abs_err_sum = 0.0
        bias_sum = 0.0
        for fc, ac in kept:
            abs_err_sum += abs(ac - fc)
            bias_sum += ac - fc
        k = len(kept)
        mae = abs_err_sum / k
        bias = bias_sum / k

        # Confidence factor combines coverage + relative MAE
        # - 1.0 when mae ≈ 0
        # - 0.5 when mae ≈ forecast_mean (useless)
        # - 0.0 when mae > 2× forecast_mean (counter-productive)
        forecast_mean = sum(fc for fc, _ in kept) / k
        if forecast_mean <= 0:
            rel = 1.0
        else:
            rel = max(0.0, 1.0 - (mae / (2.0 * forecast_mean)))
        confidence = round(min(1.0, coverage * rel), 2)
        if self.unit == "kWh":
            confidence = round(coverage * min(k / 10.0, 1.0)
                               * max(0.0, 1.0 - mae / max(forecast_mean, 0.05)) ** 2, 2)

        return CalibrationMetrics(
            sample_count=k,
            mae_w=round(mae, 4 if self.unit == "kWh" else 1),
            bias_w=round(bias, 4 if self.unit == "kWh" else 1),
            coverage=round(coverage, 2),
            confidence_factor=confidence,
        )

    # ─────────────────────────────────────────────────────────────
    # Adjustment
    # ─────────────────────────────────────────────────────────────

    def adjust(self, forecast_w: float) -> float:
        """Apply measured bias correction to a raw forecast.

        Only corrects bias (systematic over/under-forecast), never
        multiplies by an arbitrary ratio. Falls back to identity when
        not enough data.
        """
        try:
            f = float(forecast_w)
        except (TypeError, ValueError):
            return 0.0
        if f < 0 or not math.isfinite(f):
            return 0.0
        if len(self._samples) < self.MIN_SAMPLES_FOR_ADJUST:
            return f
        m = self.metrics()
        # bias_w = E[actual - forecast]. Positive ⇒ forecast too low,
        # negative ⇒ forecast too high. To correct a fresh forecast
        # we add the bias. E.g. bias=-200, fc=1000 → corrected=800.
        delta = max(-0.5 * f, min(0.5 * f, m.bias_w))
        return max(0.0, f + delta)

    # ─────────────────────────────────────────────────────────────
    # Serialisation
    # ─────────────────────────────────────────────────────────────

    def to_list(self) -> list[list[float]]:
        return [[fc, ac] for fc, ac in self._samples]

    def load_from_list(self, raw_list: list[list[float]]) -> None:
        self.reset()
        if not raw_list:
            self._dirty = True
            self._cached = None
            return
        for pair in raw_list:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            try:
                self.record(float(pair[0]), float(pair[1]))
            except (TypeError, ValueError):
                continue
