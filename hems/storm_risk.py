"""Storm risk model — weather-based auto-Storm activation.

Ported from Flutter WeatherService's WMO weather code scoring.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

# WMO weather code thresholds for storm risk scoring
_WMO_STORM_SCORES: list[tuple[int, float, str]] = [
    (95, 1.0, "thunderstorm"),
    (80, 0.8, "heavy showers"),
    (63, 0.7, "heavy rain"),
    (61, 0.5, "rain"),
    (51, 0.2, "drizzle"),
]

_HIGH_RISK_THRESHOLD = 0.6
_CLEAR_THRESHOLD = 0.4


@dataclass
class StormRisk:
    """Computed storm risk assessment."""

    score: float  # 0.0 – 1.0
    reason: str
    is_high_risk: bool  # score >= 0.6 → triggers auto-Storm

    @property
    def is_clear(self) -> bool:
        return self.score < _CLEAR_THRESHOLD


def evaluate_storm_risk(
    weather_code: int | None = None,
    wind_speed_ms: float = 0.0,
    precipitation_probability: float = 0.0,
) -> StormRisk:
    """Evaluate storm risk from weather data.

    Scoring:
    - WMO weather code: >=95 → 1.0, >=80 → 0.8, >=63 → 0.7, >=61 → 0.5, >=51 → 0.2
    - Wind: >=25m/s → 0.8, >=15m/s → 0.4
    - Precipitation: >=80% → 0.6, >=60% → 0.4
    Combined score = max of all factors.
    """
    scores: list[tuple[float, str]] = []

    # Weather code
    if weather_code is not None:
        for threshold, score, reason in _WMO_STORM_SCORES:
            if weather_code >= threshold:
                scores.append((score, reason))
                break

    # Wind speed
    if wind_speed_ms >= 25:
        scores.append((0.8, "strong wind"))
    elif wind_speed_ms >= 15:
        scores.append((0.4, "moderate wind"))

    # Precipitation probability
    if precipitation_probability >= 80:
        scores.append((0.6, "heavy precipitation"))
    elif precipitation_probability >= 60:
        scores.append((0.4, "moderate precipitation"))

    if not scores:
        return StormRisk(score=0.0, reason="clear", is_high_risk=False)

    # Take the highest risk factor
    best_score, best_reason = max(scores, key=lambda x: x[0])

    return StormRisk(
        score=best_score,
        reason=best_reason,
        is_high_risk=best_score >= _HIGH_RISK_THRESHOLD,
    )
