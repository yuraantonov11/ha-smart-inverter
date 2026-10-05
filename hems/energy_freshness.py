"""Energy freshness helpers.

Audit T20: ``InverterDailyEnergySensor`` reads
``api.daily_energy`` directly. The Powmr cloud
API returns ``dailyProducedQuantity`` *without
a date stamp*, so the sensor cannot tell
"today's value so far" from "yesterday's
final value".

This module holds the canonical freshness
contract so the API client, the sensor, and
the regression tests share one source of
truth.

Three fields:

  * ``daily_energy_date``: ``date | None``
    - the calendar date the API attached to
    the value. ``None`` means unknown.
  * ``daily_energy_at``: ``datetime | None``
    - when the API most recently refreshed
    the value. ``None`` means never.
  * ``daily_energy_stale``: ``bool``
    - whether the freshness threshold
    (default 6 hours) has been crossed.

The freshness threshold is configurable so
the coordinator can tune it. The default of
6 hours covers a normal Pollen / network
outage without flipping the sensor to stale
on every 30-minute polling slip.

Pure stdlib - no Home Assistant imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


DEFAULT_FRESHNESS_HOURS = 6


@dataclass(frozen=True)
class DailyEnergyFreshness:
    """The freshness contract the API
    publishes alongside ``daily_energy``.

    The fields are intentionally flat (a
    ``dict[str, Any]`` would not be testable
    without an HA runtime) so the sensor can
    read the dataclass directly.
    """

    daily_energy_date: date | None
    daily_energy_at: datetime | None
    daily_energy_stale: bool

    def as_dict(self) -> dict:
        """Return a plain ``dict`` for the
        ``extra_state_attributes`` slot. ``None``
        values are kept so the consumer can
        distinguish "unknown" from "zero".
        """
        return {
            "daily_energy_date": (
                self.daily_energy_date.isoformat()
                if self.daily_energy_date is not None
                else None
            ),
            "daily_energy_at": (
                self.daily_energy_at.isoformat()
                if self.daily_energy_at is not None
                else None
            ),
            "daily_energy_stale": self.daily_energy_stale,
        }


def compute_daily_energy_freshness(
    daily_energy_at: datetime | None,
    daily_energy_date: date | None,
    now: datetime,
    freshness_hours: float = DEFAULT_FRESHNESS_HOURS,
) -> DailyEnergyFreshness:
    """Compute the freshness triple for a
    ``daily_energy`` value.

    Audit T20.1 contract:

      * ``daily_energy_at=None`` or
        ``daily_energy_date=None`` produces
        a stale ``DailyEnergyFreshness`` -
        we have never refreshed, so the
        value cannot be trusted.

      * A value older than ``freshness_hours``
        produces ``daily_energy_stale=True``.

      * The contract never substitutes a
        measured value with a forecast - the
        caller (the sensor) decides whether
        to surface ``None``, ``0.0``, or a
        cached value; the freshness helper
        only reports the truth.
    """
    if daily_energy_at is None or daily_energy_date is None:
        return DailyEnergyFreshness(
            daily_energy_date=None,
            daily_energy_at=None,
            daily_energy_stale=True,
        )
    # Compare the timestamp against
    # ``now`` in a way that tolerates
    # timezone-naive timestamps (the API
    # returns naive datetimes in the
    # production wiring; the audit
    # accepts both).
    elapsed = now - daily_energy_at
    if elapsed < timedelta(0):
        # A timestamp from the future is
        # suspicious. Mark stale rather
        # than silently trusting.
        stale = True
    else:
        stale = elapsed > timedelta(hours=freshness_hours)
    return DailyEnergyFreshness(
        daily_energy_date=daily_energy_date,
        daily_energy_at=daily_energy_at,
        daily_energy_stale=stale,
    )


def daily_energy_for_today(
    value: float,
    freshness: DailyEnergyFreshness,
    today: date,
) -> float | None:
    """Return the value the sensor should
    publish *for today*.

    Audit T20.2 contract: when the API's
    ``daily_energy_date`` is older than today,
    the value is yesterday's final reading and
    the sensor must report ``0.0`` - today's
    accumulator starts at zero.

    Audit T20.3 contract: when the freshness
    is stale and we have *no* date, the
    function returns ``None`` so the sensor
    publishes ``unknown``. The audit forbids
    substituting the measured value with a
    forecast.

    The "today value present" case returns the
    raw value unchanged.
    """
    if freshness.daily_energy_date is None:
        # We have never refreshed the value.
        return None
    if freshness.daily_energy_date < today:
        # Yesterday's final value, never
        # roll-forward.
        return 0.0
    return float(value)


__all__ = [
    "DEFAULT_FRESHNESS_HOURS",
    "DailyEnergyFreshness",
    "compute_daily_energy_freshness",
    "daily_energy_for_today",
]