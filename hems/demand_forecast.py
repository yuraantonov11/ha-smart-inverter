"""Demand Forecast — EWMA load profile with probabilistic predictions.

Ported from Flutter DemandForecastService.
Uses Exponentially Weighted Moving Average (α=0.25) to learn hourly
load patterns, then converts to probabilistic p25/p50/p75/p90 forecasts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

_MIN_LOAD_W = 100.0
_MAX_LOAD_W = 12000.0
_DEFAULT_ALPHA = 0.25


@dataclass
class DemandMetrics:
    """Probabilistic demand metrics for a single hour."""

    p25: float  # 25th percentile (low estimate)
    p50: float  # 50th percentile (median)
    p75: float  # 75th percentile (high estimate)
    p90: float  # 90th percentile (very high estimate)


@dataclass
class DemandForecastData:
    """24-hour demand forecast with probabilistic metrics."""

    hourly_metrics: dict[int, DemandMetrics]

    def get_metrics_for_hour(self, hour: int) -> DemandMetrics:
        return self.hourly_metrics.get(hour, DemandMetrics(p25=80, p50=100, p75=120, p90=135))

    @property
    def peak_hour(self) -> int:
        """Hour with highest p50 demand."""
        if not self.hourly_metrics:
            return 19
        return max(self.hourly_metrics.keys(), key=lambda h: self.hourly_metrics[h].p50)

    @property
    def valley_hour(self) -> int:
        """Hour with lowest p50 demand."""
        if not self.hourly_metrics:
            return 3
        return min(self.hourly_metrics.keys(), key=lambda h: self.hourly_metrics[h].p50)


class DemandForecastService:
    """EWMA-based load profile learning and demand forecasting.

    Learns from actual load readings using exponential smoothing,
    then produces probabilistic forecasts for each hour.
    """

    DEFAULT_PROFILE: dict[int, float] = {
        0: 250, 1: 200, 2: 200, 3: 200, 4: 200, 5: 250,
        6: 500, 7: 1500, 8: 1200, 9: 600, 10: 500, 11: 500,
        12: 500, 13: 500, 14: 600, 15: 800, 16: 900, 17: 1500,
        18: 2000, 19: 3000, 20: 2500, 21: 2000, 22: 1000, 23: 500,
    }

    def __init__(self, profile: dict[int, float] | None = None) -> None:
        self._profile: dict[int, float] = dict(profile or self.DEFAULT_PROFILE)

    @property
    def profile(self) -> dict[int, float]:
        return dict(self._profile)

    def update_ewma(
        self,
        timestamp: datetime,
        load_w: float,
        alpha: float = _DEFAULT_ALPHA,
    ) -> None:
        """Update EWMA profile with a new load sample.

        Args:
            timestamp: When the sample was taken.
            load_w: Load power in watts.
            alpha: Smoothing factor (0-1). Lower = more smoothing.
        """
        hour = timestamp.hour
        sample = max(_MIN_LOAD_W, min(_MAX_LOAD_W, load_w))
        old = self._profile.get(hour, sample)
        self._profile[hour] = alpha * sample + (1 - alpha) * old

    def to_demand_forecast(self) -> DemandForecastData:
        """Convert EWMA profile to probabilistic demand forecast.

        Uses fixed multipliers as a lightweight Gaussian approximation:
        p25 = 0.8×, p50 = 1.0×, p75 = 1.2×, p90 = 1.35×
        """
        metrics: dict[int, DemandMetrics] = {}
        for hour in range(24):
            base = max(_MIN_LOAD_W, min(_MAX_LOAD_W, self._profile.get(hour, 500.0)))
            metrics[hour] = DemandMetrics(
                p25=base * 0.8,
                p50=base,
                p75=base * 1.2,
                p90=base * 1.35,
            )
        return DemandForecastData(hourly_metrics=metrics)

    def estimate_energy_deficit(self, horizon_hours: int = 24) -> float:
        """Estimate total energy deficit over next N hours (Wh).

        Simple heuristic: sum of p50 demand minus what PV/solar can provide.
        """
        forecast = self.to_demand_forecast()
        now_hour = datetime.now().hour
        total_wh = 0.0
        for i in range(horizon_hours):
            hour = (now_hour + i) % 24
            total_wh += forecast.get_metrics_for_hour(hour).p50
        return total_wh

    def to_dict(self) -> dict[int, float]:
        """Serialize profile for HA storage."""
        return {str(k): v for k, v in self._profile.items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DemandForecastService:
        """Deserialize profile from HA storage."""
        profile = {int(k): float(v) for k, v in data.items()}
        return cls(profile=profile if profile else None)

    def load_from_dict(self, data: dict[str, Any]) -> None:
        """Load profile from serialized dict."""
        if data:
            self._profile = {int(k): float(v) for k, v in data.items()}


from typing import Any  # noqa: E402
