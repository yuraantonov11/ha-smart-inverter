"""Energy freshness helpers.

Audit T20: ``InverterDailyEnergySensor`` reads
``api.daily_energy`` directly. The Powmr cloud
API returns ``dailyProducedQuantity`` *without
a date stamp* - the value is a raw number the
inverter reported most recently. The integrator
cannot ask "for which day?" from the API
itself.

To recover the freshness contract, the
coordinator attaches the date and timestamp
*at refresh time* and surfaces them to the
sensor. This module holds the canonical
freshness contract so the API client, the
sensor, and the regression tests share one
source of truth.

Three fields:

  * ``daily_energy_date``: ``date | None``
    - the calendar date (in the HA site's
    local timezone) when the coordinator
    most recently refreshed the value.
    ``None`` means we have never refreshed.
    The audit explicitly says this date is
    **not** API-attached - the API does not
    return it.
  * ``daily_energy_at``: ``datetime | None``
    - the timestamp the coordinator most
    recently refreshed the value. The
    contract is UTC; the helper accepts
    naive timestamps (assumed UTC) and
    timezone-aware timestamps in any zone.
    ``None`` means never.
  * ``daily_energy_stale``: ``bool``
    - whether the freshness threshold
    (default 6 hours) has been crossed.

The freshness threshold is configurable so
the coordinator can tune it. The default of
6 hours covers a normal Pollen / network
outage without flipping the sensor to stale
on every 30-minute polling slip.

Audit T20 follow-up (timezone):
``compute_daily_energy_freshness`` normalises
both ``daily_energy_at`` and ``now`` to UTC
before subtracting them. A naive
``datetime`` is assumed to already be UTC
(this matches the historical API-client
wiring where the integrator ran on a UTC
VM). The alternative is exactly the bug we
are fixing - naive ``datetime.now()`` minus
aware ``datetime.now(tz=timezone.utc)`` raises
``TypeError``.

Pure stdlib - no Home Assistant imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone


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


def _normalize_utc(ts: datetime | None) -> datetime | None:
    """Return ``ts`` as a timezone-aware
    ``datetime`` in UTC.

    Audit T20 follow-up: the production API
    client historically wrote
    ``datetime.now()`` (a naive timestamp in
    the host system's local time) while the
    sensor wrote
    ``datetime.now(tz=timezone.utc)``. The
    mismatch crashed the freshness helper
    with::

        TypeError: can't subtract
        offset-naive and offset-aware
        datetimes

    The helper now normalises both sides to
    UTC:

      * ``None`` stays ``None``.
      * A naive ``datetime`` is assumed to
        already be in UTC - this matches the
        API client's intent on the original
        Powmr wiring (the integrator ran on
        a UTC VM and wrote the naive value
        *expecting* UTC). The audit accepts
        the assumption because the alternative
        - assuming local time - is what made
        midnight-reset calculations wrong on
        every host not configured as ``UTC``.
      * An aware ``datetime`` is converted
        via ``astimezone(timezone.utc)``.

    Returning a normalized UTC timestamp
    rather than a timedelta keeps the
    contract symmetric: both
    ``daily_energy_at`` and ``now`` flow
    through the same path.
    """
    if ts is None:
        return None
    if ts.tzinfo is None:
        # Audit T20 follow-up: assume
        # UTC for naive timestamps. The
        # alternative (assume local)
        # is exactly the bug we are
        # fixing.
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


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

    Audit T20 follow-up: the helper accepts
    ``daily_energy_at`` and ``now`` in any
    combination of naive / aware datetimes.
    Both are normalized to UTC internally
    so the subtraction never raises
    ``TypeError``. The freshness triple
    surfaces the normalized UTC values
    so consumers do not have to repeat the
    work.
    """
    if daily_energy_at is None or daily_energy_date is None:
        return DailyEnergyFreshness(
            daily_energy_date=None,
            daily_energy_at=None,
            daily_energy_stale=True,
        )
    # Audit T20 follow-up:
    # normalize both timestamps
    # to UTC before subtraction.
    api_at_utc = _normalize_utc(daily_energy_at)
    now_utc = _normalize_utc(now)
    if api_at_utc is None or now_utc is None:
        # Defensive: ``_normalize_utc``
        # only returns ``None`` when
        # ``ts`` is ``None``, which we
        # already filtered above.
        return DailyEnergyFreshness(
            daily_energy_date=None,
            daily_energy_at=None,
            daily_energy_stale=True,
        )
    elapsed = now_utc - api_at_utc
    if elapsed < timedelta(0):
        # A timestamp from the future
        # is suspicious. Mark stale
        # rather than silently trusting.
        stale = True
    else:
        stale = elapsed > timedelta(hours=freshness_hours)
    return DailyEnergyFreshness(
        daily_energy_date=daily_energy_date,
        daily_energy_at=api_at_utc,
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