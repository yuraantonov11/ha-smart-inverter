"""Adaptive HEMS tuning — variance-aware thresholds, acoustic comfort.

Ported from Flutter HemsTuningService + HemsTunables.
Provides dynamically computed thresholds that adapt to current conditions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class HemsTunables:
    """Static tunable constants (mirrors Dart HemsTunables)."""

    reserve_soc: float = 20.0
    min_operating_soc: float = 30.0
    mid_soc: float = 50.0
    pv_surplus_enter_w: float = 250.0
    pv_surplus_exit_w: float = 50.0
    min_mode_hold_min: int = 20
    manual_override_hold_min: int = 30
    command_dedup_window_sec: int = 30


@dataclass
class AdaptiveThresholds:
    """Computed adaptive thresholds for current conditions."""

    pv_surplus_enter_w: float = 250.0
    pv_surplus_exit_w: float = 50.0
    dwell_time_min: int = 15
    reserve_soc: float = 20.0
    hour_penalty_active: bool = False


class HemsTuningService:
    """Computes adaptive thresholds based on runtime conditions.

    Ported from Flutter HemsTuningService.
    """

    def __init__(self, tunables: HemsTunables | None = None) -> None:
        self.tun = tunables or HemsTunables()
        self._recent_surplus: list[float] = []

    def update_surplus(self, surplus_w: float) -> None:
        """Feed a new surplus sample for variance tracking."""
        self._recent_surplus.append(surplus_w)
        if len(self._recent_surplus) > 30:
            self._recent_surplus = self._recent_surplus[-30:]

    def compute_adaptive_pv_surplus(
        self, pv_peak_w: float = 3000.0
    ) -> float:
        """Adaptive PV surplus threshold — variance-aware.

        Ported from Flutter computeAdaptivePvSurplus.
        Base: 10% of pvPeakW.
        Hour penalty: 1.5× before 10am/after 4pm (cloudy hours).
        Variance penalty: up to 1.5× based on stdDev of recent surplus.
        Clamped to [70, 600] W.
        """
        now = datetime.now()
        base = pv_peak_w * 0.10  # 10% of peak

        # Hour penalty: less reliable solar in early morning / late afternoon
        hour = now.hour
        hour_mult = 1.0
        if hour < 10 or hour > 16:
            hour_mult = 1.5

        # Variance penalty
        variance_mult = 1.0
        if len(self._recent_surplus) >= 5:
            std_dev = self._compute_std(self._recent_surplus)
            # High variance (>300W) → raise threshold to avoid flapping
            variance_mult = min(1.5, 1.0 + std_dev / 600.0)

        result = base * hour_mult * variance_mult
        return max(70.0, min(600.0, result))

    def compute_adaptive_dwell(self) -> int:
        """Adaptive dwell time between mode switches (minutes).

        High variance (>300): 8min (cloudy, react fast).
        Low variance (<50): 25min (clear, stable).
        Default: 15min.
        """
        if len(self._recent_surplus) < 5:
            return 15

        std_dev = self._compute_std(self._recent_surplus)

        if std_dev > 300:
            return 8  # Very cloudy, react fast
        elif std_dev < 50:
            return 25  # Clear sky, stable — be conservative
        else:
            return 15  # Default

    def compute_adaptive_reserve_soc(
        self,
        battery_health_percent: float = 100.0,
        strategy: str = "hybrid",
        has_tou_tariff: bool = True,
    ) -> float:
        """Adaptive reserve SOC — battery health + strategy aware.

        Ported from Flutter computeAdaptiveReserveSoc.
        Clamped to [15, 35]%.
        """
        base = self.tun.reserve_soc

        # Battery health factor
        if battery_health_percent < 80:
            base += 5.0  # Older battery → keep more reserve
        elif battery_health_percent < 90:
            base += 2.0

        # Strategy adjustment
        if strategy == "solar_maxed":
            base -= 2.0  # More aggressive
        elif strategy == "battery_life":
            base += 3.0  # More conservative

        # TOU tariff reduces need for reserve (can charge cheaply at night)
        if has_tou_tariff:
            base -= 1.0

        return max(15.0, min(35.0, base))

    def should_reduce_buzzer(self) -> bool:
        """Acoustic comfort — return True if buzzer should be OFF (night).

        Ported from Flutter acoustic comfort: night (22:00-07:00) → buzzer OFF.
        """
        hour = datetime.now().hour
        return hour >= 22 or hour < 7

    @staticmethod
    def _compute_std(values: list[float]) -> float:
        """Compute standard deviation."""
        if len(values) < 2:
            return 0.0
        avg = sum(values) / len(values)
        variance = sum((v - avg) ** 2 for v in values) / len(values)
        return math.sqrt(variance)
