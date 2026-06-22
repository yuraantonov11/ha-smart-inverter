"""Battery SoH (State of Health) estimation — cycle + age based.

Ported from Flutter BatteryTrackerService.
Uses partial DoD-aware cycle counting (30%→80%) and calendar aging
(3% per year) to estimate remaining battery health.
"""

from __future__ import annotations

import logging
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

# Constants
RATED_CYCLE_LIFE = 2000
LOW_THRESHOLD = 30.0  # SOC enters low state
HIGH_THRESHOLD = 80.0  # SOC exits low state → cycle complete
CALENDAR_AGING_PER_YEAR = 0.03  # 3% degradation per year


class BatterySoH:
    """Battery State of Health estimator.

    Tracks charge/discharge cycles using a state machine:
    - Enters "low state" when SOC drops ≤ 30%
    - Completes a cycle when SOC rises back to ≥ 80%
    - SoH = (1 - cycles/ratedLife) × ageFactor × 100
    """

    def __init__(
        self,
        cycle_count: int = 0,
        in_low_state: bool = False,
        install_date: datetime | None = None,
    ) -> None:
        self._cycle_count = cycle_count
        self._in_low_state = in_low_state
        self._install_date = install_date

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def in_low_state(self) -> bool:
        return self._in_low_state

    def track_soc(self, soc: float) -> bool:
        """Track SOC for cycle counting.

        Returns True when a full cycle is completed.
        """
        if not self._in_low_state and soc <= LOW_THRESHOLD:
            self._in_low_state = True

        if self._in_low_state and soc >= HIGH_THRESHOLD:
            self._in_low_state = False
            self._cycle_count += 1
            _LOGGER.info("Battery cycle completed: #%d", self._cycle_count)
            return True

        return False

    def estimated_soh_percent(
        self, install_date: datetime | None = None
    ) -> float:
        """Estimate battery State of Health as percentage.

        Uses two degradation factors:
        1. Cycle degradation: cycleCount / ratedLife (capped at 80% loss)
        2. Calendar aging: 3% per year from install date

        Returns SoH in [0, 100] percent.
        """
        cycle_degrade = min(0.8, self._cycle_count / RATED_CYCLE_LIFE)

        age_factor = 1.0
        date = install_date or self._install_date
        if date is not None:
            years = (datetime.now() - date).days / 365.0
            age_factor = max(0.0, 1.0 - years * CALENDAR_AGING_PER_YEAR)

        soh = (1.0 - cycle_degrade) * age_factor * 100.0
        return max(0.0, min(100.0, soh))

    def recommended_reserve_soc(self, base_reserve: float = 20.0) -> float:
        """Recommend reserve SOC based on battery health.

        Older batteries need higher reserve to prevent deep discharge.
        SoH < 80% → +5%, SoH < 90% → +2%.
        """
        soh = self.estimated_soh_percent()
        if soh < 80:
            return min(35.0, base_reserve + 5.0)
        elif soh < 90:
            return min(35.0, base_reserve + 2.0)
        return base_reserve

    def reset(self) -> None:
        """Reset cycle count and low state."""
        self._cycle_count = 0
        self._in_low_state = False
        _LOGGER.info("Battery tracker reset")

    def to_dict(self) -> dict:
        """Serialize for HA storage."""
        return {
            "cycle_count": self._cycle_count,
            "in_low_state": self._in_low_state,
            "install_date": self._install_date.isoformat() if self._install_date else None,
        }

    def load_from_dict(self, data: dict) -> None:
        """Load from serialized dict."""
        self._cycle_count = data.get("cycle_count", 0)
        self._in_low_state = data.get("in_low_state", False)
        install_str = data.get("install_date")
        if install_str:
            try:
                self._install_date = datetime.fromisoformat(install_str)
            except (ValueError, TypeError):
                self._install_date = None
