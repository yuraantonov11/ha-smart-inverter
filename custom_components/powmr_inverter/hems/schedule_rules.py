"""Schedule Rules — time-based HEMS mode overrides.

Ported from Flutter ScheduleRule model + ScheduleRulesService.
Rules override the user-selected smart mode during specific time windows
with priority-based conflict resolution.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)


@dataclass
class ScheduleRule:
    """A time-based override rule for HEMS.

    When a rule is enabled and its time window is currently active,
    the HEMS engine executes the specified mode instead of the user-selected one.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""

    # ISO weekdays: 1=Monday … 7=Sunday
    days_of_week: list[int] = field(default_factory=lambda: [1, 2, 3, 4, 5])

    start_hour: int = 0  # 0-23
    start_minute: int = 0  # 0-59
    end_hour: int = 23  # 0-23
    end_minute: int = 0  # 0-59

    mode: int = 0  # 0=adaptive, 1=arbitrage, 2=storm
    enabled: bool = True
    priority: int = 5  # 1-10, higher wins

    MIN_PRIORITY = 1
    MAX_PRIORITY = 10
    DEFAULT_PRIORITY = 5

    @property
    def start_total_minutes(self) -> int:
        return max(0, min(23, self.start_hour)) * 60 + max(0, min(59, self.start_minute))

    @property
    def end_total_minutes(self) -> int:
        return max(0, min(23, self.end_hour)) * 60 + max(0, min(59, self.end_minute))

    @property
    def is_overnight(self) -> bool:
        return self.start_total_minutes > self.end_total_minutes

    def is_active_at(self, dt: datetime) -> bool:
        """Check if this rule covers the given datetime.

        Handles both same-day windows (08:00–22:00) and
        overnight windows (23:00–06:00).
        """
        if not self.enabled:
            return False
        if not self.days_of_week:
            return False

        now_min = dt.hour * 60 + dt.minute
        start_min = self.start_total_minutes
        end_min = self.end_total_minutes

        if start_min == end_min:
            return False  # degenerate

        if start_min < end_min:
            # Same-day window
            if dt.isoweekday() not in self.days_of_week:
                return False
            return start_min <= now_min < end_min
        else:
            # Overnight window
            if now_min >= start_min:
                # Late segment — belongs to current weekday
                return dt.isoweekday() in self.days_of_week
            if now_min < end_min:
                # Early segment — belongs to previous weekday's rule
                prev_weekday = 7 if dt.isoweekday() == 1 else dt.isoweekday() - 1
                return prev_weekday in self.days_of_week
            return False

    @property
    def is_active_now(self) -> bool:
        return self.is_active_at(datetime.now())

    @property
    def time_range_label(self) -> str:
        return f"{self.start_hour:02d}:{self.start_minute:02d} – {self.end_hour:02d}:{self.end_minute:02d}"

    @property
    def days_label(self) -> str:
        day_names = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
        return " ".join(day_names.get(d, "?") for d in sorted(self.days_of_week))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "days_of_week": self.days_of_week,
            "start_hour": self.start_hour,
            "start_minute": self.start_minute,
            "end_hour": self.end_hour,
            "end_minute": self.end_minute,
            "mode": self.mode,
            "enabled": self.enabled,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduleRule:
        return cls(
            id=data.get("id", uuid.uuid4().hex[:12]),
            name=data.get("name", ""),
            days_of_week=data.get("days_of_week", [1, 2, 3, 4, 5]),
            start_hour=data.get("start_hour", 0),
            start_minute=data.get("start_minute", 0),
            end_hour=data.get("end_hour", 23),
            end_minute=data.get("end_minute", 0),
            mode=data.get("mode", 0),
            enabled=data.get("enabled", True),
            priority=data.get("priority", 5),
        )


class ScheduleRulesService:
    """Manages time-based HEMS override rules with persistence."""

    STORAGE_KEY = "schedule_rules_v1"

    def __init__(self) -> None:
        self._rules: list[ScheduleRule] = []

    @property
    def rules(self) -> list[ScheduleRule]:
        return list(self._rules)

    def load_from_dict(self, data: dict[str, Any]) -> None:
        """Load rules from a serialized dict (from HA storage)."""
        rules_raw = data.get(self.STORAGE_KEY, [])
        self._rules = [ScheduleRule.from_dict(r) for r in rules_raw]
        _LOGGER.info("ScheduleRules: loaded %d rules", len(self._rules))

    def save_to_dict(self) -> dict[str, Any]:
        """Serialize rules for HA storage."""
        return {self.STORAGE_KEY: [r.to_dict() for r in self._rules]}

    def add_rule(self, rule: ScheduleRule) -> None:
        self._rules.append(rule)
        _LOGGER.info("ScheduleRules: added rule '%s' (mode=%d, priority=%d)", rule.name, rule.mode, rule.priority)

    def update_rule(self, updated: ScheduleRule) -> None:
        for i, r in enumerate(self._rules):
            if r.id == updated.id:
                self._rules[i] = updated
                _LOGGER.info("ScheduleRules: updated rule '%s'", updated.name)
                return

    def delete_rule(self, rule_id: str) -> None:
        self._rules = [r for r in self._rules if r.id != rule_id]
        _LOGGER.info("ScheduleRules: deleted rule %s", rule_id)

    def toggle_rule(self, rule_id: str) -> None:
        for r in self._rules:
            if r.id == rule_id:
                r.enabled = not r.enabled
                _LOGGER.info("ScheduleRules: toggled rule '%s' → %s", r.name, r.enabled)
                return

    def get_active_rule_now(self, now: datetime | None = None) -> ScheduleRule | None:
        """Return the active rule with the highest priority at [now].

        When priorities are equal, first-added rule wins (list order).
        """
        dt = now or datetime.now()
        winner: ScheduleRule | None = None
        for rule in self._rules:
            if not rule.is_active_at(dt):
                continue
            if winner is None or rule.priority > winner.priority:
                winner = rule
        return winner
