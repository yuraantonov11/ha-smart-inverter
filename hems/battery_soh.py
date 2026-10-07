"""Battery SoH (State of Health) estimation — cycle + age based.

Ported from Flutter BatteryTrackerService.
Uses partial DoD-aware cycle counting (30%→80%) and calendar aging
(3% per year) to estimate remaining battery health.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Union

_LOGGER = logging.getLogger(__name__)

# Constants
RATED_CYCLE_LIFE = 2000
LOW_THRESHOLD = 30.0  # SOC enters low state
HIGH_THRESHOLD = 80.0  # SOC exits low state → cycle complete
CALENDAR_AGING_PER_YEAR = 0.03  # 3% degradation per year

# Upper bound for the SoH-driven reserve bump. The audit
# explicitly required that the recommended reserve never
# drops below the user-configured base reserve, so the
# final answer is ``max(base_reserve, min(MAX_BUMP_SOC, base + delta))``.
MAX_BUMP_SOC = 35.0


def _coerce_soc(value: object) -> float | None:
    """Return a finite SOC value or None.

    T26: the audit demanded
    validation of malformed
    input. The previous
    implementation crashed
    on a string, ``None``,
    or a NaN. We return
    ``None`` and let the
    caller decide (the
    engine treats missing
    SOC as ``unknown`` and
    refuses to write, per
    T01).
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    if not 0.0 <= v <= 100.0:
        # Out-of-range readings
        # are rejected (T01
        # contract).
        return None
    return v


def _coerce_cycle_count(value: object) -> int:
    """Return a finite, non-negative integer or ``0``.

    T26 round 3 (audit
    follow-up): the
    audit explicitly
    asked for shared
    validation on the
    constructor and
    restore paths. The
    previous code
    crashed with
    ``ValueError`` on a
    string, with
    ``OverflowError`` on
    ``float('inf')``,
    and silently
    accepted a negative
    number (``int(-5)``
    succeeded; the
    legitimate cycle
    count is a finite
    non-negative int).
    We treat any
    malformed value as
    ``0`` and clamp
    negatives to ``0``.
    """
    if value is None:
        return 0
    # ``bool`` is a
    # subclass of ``int``
    # but is *not* a
    # meaningful cycle
    # count, so reject it
    # explicitly.
    if isinstance(value, bool):
        return 0
    try:
        v = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    # ``int(float('inf'))``
    # raises
    # ``OverflowError`` on
    # Python 3 — but on
    # some platforms it
    # returns
    # ``sys.maxsize``.
    # Either way, the
    # answer is not a
    # finite non-negative
    # integer, so we
    # reject it.
    try:
        if not math.isfinite(float(v)):
            return 0
    except (ValueError, OverflowError):
        return 0
    if v < 0:
        return 0
    return v


def _coerce_install_date(value: object) -> datetime | None:
    """Coerce ``value`` to a
    timezone-aware datetime,
    or None.

    T26: the previous
    implementation stored
    a naive ``datetime``
    from ``datetime.now()``
    and accepted whatever
    ``datetime.fromisoformat``
    produced. Mixing
    timezone-aware and
    naive datetimes raises
    ``TypeError`` on
    subtraction. We force
    a timezone-aware UTC
    value here so the
    subtraction in
    ``estimated_soh_percent``
    is always safe.

    T26 round 3: a
    *future* install
    date would produce
    a negative ``years``
    value, which the
    calendar-aging
    factor
    ``max(0, 1 - years * 0.03)``
    treats as zero loss
    and would let SoH
    exceed 100% (cycle
    degradation still
    applies, but a
    future date is a
    data-entry error
    and must not
    *improve* SoH).
    We return ``None``
    for any install
    date that is
    *after* the current
    wall clock — this
    is the same
    contract as a
    malformed value
    (the calendar
    factor is dropped).
    The caller already
    coerces ``now`` via
    this helper, so the
    check below uses
    the helper's
    result for
    ``now``.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
    else:
        return None
    if dt.tzinfo is None:
        # Promote naive to
        # UTC. The audit
        # accepts that the
        # legacy stored value
        # was naive — the
        # promotion is
        # deterministic and
        # does not silently
        # shift the value
        # in time.
        dt = dt.replace(tzinfo=timezone.utc)
    # Reject future
    # dates. We compare
    # against ``datetime.now(UTC)``;
    # a difference of 1
    # second is treated
    # as "future" so
    # clock-skew does
    # not silently
    # accept a tiny
    # typo.
    if dt > datetime.now(timezone.utc):
        return None
    return dt


class BatterySoH:
    """Battery State of Health estimator.

    Tracks charge/discharge cycles using a state machine:
    - Enters "low state" when SOC drops ≤ 30%
    - Completes a cycle when SOC rises back to ≥ 80%
    - SoH = (1 - cycles/ratedLife) × ageFactor × 100
    """

    def __init__(
        self,
        cycle_count: object = 0,
        in_low_state: object = False,
        install_date: Union[datetime, str, None] = None,
    ) -> None:
        # T26 round 3: every
        # constructor argument
        # is run through a
        # shared validation
        # helper so a
        # malformed value
        # (``"abc"``,
        # ``Infinity``,
        # ``-5``, ``True``)
        # cannot crash the
        # integration or
        # silently land as a
        # negative cycle
        # count.
        self._cycle_count = _coerce_cycle_count(cycle_count)
        self._in_low_state = bool(in_low_state) if not isinstance(in_low_state, bool) else in_low_state
        # T26: coerce the
        # install date to a
        # timezone-aware
        # datetime so the
        # subtraction is safe
        # when the value was
        # loaded from a naive
        # ISO string.
        self._install_date = _coerce_install_date(install_date)

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def in_low_state(self) -> bool:
        return self._in_low_state

    def track_soc(self, soc: object) -> bool:
        """Track SOC for cycle counting.

        Returns True when a full cycle is completed.

        T26: accepts
        ``object`` so the
        caller can pass
        whatever the engine
        provides. Malformed
        values are dropped
        silently — the
        coordinator's T01
        guard already refuses
        to write when SOC is
        unknown.
        """
        v = _coerce_soc(soc)
        if v is None:
            return False
        if not self._in_low_state and v <= LOW_THRESHOLD:
            self._in_low_state = True
        if self._in_low_state and v >= HIGH_THRESHOLD:
            self._in_low_state = False
            self._cycle_count += 1
            _LOGGER.info(
                "Battery cycle completed: #%d",
                self._cycle_count,
            )
            return True
        return False

    def estimated_soh_percent(
        self,
        install_date: Union[datetime, str, None] = None,
        now: Union[datetime, None] = None,
    ) -> float:
        """Estimate battery State of Health as percentage.

        Uses two degradation factors:
        1. Cycle degradation: cycleCount / ratedLife (capped at 80% loss)
        2. Calendar aging: 3% per year from install date

        Returns SoH in [0, 100] percent.

        T26: the previous
        implementation read
        the wall clock via
        ``datetime.now()``,
        which made the
        result impossible
        to test deterministically
        and made naive /
        aware datetimes mix
        in production. The
        new contract:

          * ``install_date`` may
            be a datetime, an
            ISO string, or
            None. Strings are
            parsed via
            ``fromisoformat``
            and promoted to
            ``UTC`` if naive.
          * ``now`` is a
            controlled
            argument;
            ``None`` defaults
            to the current UTC
            time. Tests must
            pass an explicit
            ``now`` to pin the
            answer.
        """
        cycle_degrade = min(
            0.8, self._cycle_count / RATED_CYCLE_LIFE
        )
        age_factor = 1.0
        date = _coerce_install_date(install_date) or self._install_date
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if date is not None:
            years = (now - date).days / 365.0
            age_factor = max(0.0, 1.0 - years * CALENDAR_AGING_PER_YEAR)
        soh = (1.0 - cycle_degrade) * age_factor * 100.0
        return max(0.0, min(100.0, soh))

    def recommended_reserve_soc(self, base_reserve: float = 20.0) -> float:
        """Recommend reserve SOC based on battery health.

        Older batteries need higher reserve to prevent deep discharge.
        SoH < 80% → +5%, SoH < 90% → +2%.

        T26: the previous
        implementation used
        ``min(MAX_BUMP_SOC, base + delta)``
        which silently
        returned a value
        LOWER than
        ``base_reserve`` when
        ``base_reserve`` was
        already above
        ``MAX_BUMP_SOC``. The
        audit explicitly
        required: the
        recommended reserve
        must NEVER be below
        the configured base
        reserve. The fix is
        to take
        ``max(base_reserve, ...)``
        as the final clamp.

        The recommendation
        is a pure function:
        it does NOT call
        ``api.set_config_item``
        or otherwise mutate
        physical limits. The
        caller must apply the
        number explicitly.
        """
        soh = self.estimated_soh_percent()
        if soh < 80:
            bumped = base_reserve + 5.0
        elif soh < 90:
            bumped = base_reserve + 2.0
        else:
            bumped = base_reserve
        # The recommendation
        # must never be below
        # the user-configured
        # base reserve.
        return max(base_reserve, min(MAX_BUMP_SOC, bumped))

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
            "install_date": (
                self._install_date.isoformat()
                if self._install_date is not None
                else None
            ),
        }

    def load_from_dict(self, data: dict) -> None:
        """Load from serialized dict.

        T26 round 3:
        shared
        validation —
        ``cycle_count``
        is run through
        ``_coerce_cycle_count``
        so a
        malformed
        value
        (``"abc"``,
        ``Infinity``,
        ``-5``) lands
        as ``0``
        instead of
        crashing the
        integration.
        ``install_date``
        is run through
        ``_coerce_install_date``
        which rejects
        malformed
        inputs and
        future dates.
        ``in_low_state``
        is coerced via
        ``bool`` — the
        only legal
        representations
        are ``True`` /
        ``False`` (we
        reject
        ``"true"`` /
        ``1``).
        """
        self._cycle_count = _coerce_cycle_count(
            data.get("cycle_count")
        )
        self._in_low_state = (
            data.get("in_low_state") is True
        )
        self._install_date = _coerce_install_date(
            data.get("install_date")
        )
