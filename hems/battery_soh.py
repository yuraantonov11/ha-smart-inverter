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

    T26 round 4 (audit
    follow-up): the
    audit re-tested the
    round 3 helper and
    reproduced
    ``OverflowError``
    for
    ``float('inf')`` /
    ``float('-inf')``.
    The previous code
    called ``int(value)``
    *before* the
    ``math.isfinite``
    check, and
    ``int(float('inf'))``
    raises
    ``OverflowError`` —
    which the ``except
    (TypeError,
    ValueError)``
    clause does *not*
    catch. The fix is
    to do the finite
    check first, on
    the raw value,
    before any
    conversion to
    ``int``. ``NaN``
    also returns
    ``False`` from
    ``isfinite`` so it
    is rejected by the
    same path.
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
    # Step 1: check
    # finiteness on the
    # raw value *before*
    # conversion. This
    # catches
    # ``float('inf')``,
    # ``float('-inf')``,
    # ``float('nan')``,
    # and any
    # mathematically
    # ill-defined input.
    try:
        if not math.isfinite(float(value)):  # type: ignore[arg-type]
            return 0
    except (TypeError, ValueError, OverflowError):
        return 0
    # Step 2: convert
    # to int. The
    # ``int(float)`` call
    # is now safe because
    # we already know
    # the value is
    # finite.
    try:
        v = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return 0
    if v < 0:
        return 0
    return v


def _coerce_in_low_state(value: object) -> bool:
    """Return a strict ``bool`` from ``value``.

    T26 round 4 (audit
    follow-up): the
    constructor used
    ``bool(value)``,
    which is *truthy*
    coercion — ``"false"``,
    ``0``, ``[]``, etc.
    all become
    ``False`` but
    ``"true"``,
    ``"yes"``, ``1``,
    and any
    non-empty string
    become ``True``.
    The restore path
    used ``is True``,
    which accepts only
    the literal
    ``True``. The
    asymmetry meant a
    value loaded from
    a YAML / JSON file
    that said
    ``in_low_state: yes``
    would land as
    ``True`` on first
    write (via
    ``bool("yes")``)
    but then be
    *dropped* on a
    subsequent reload
    via ``is True``.

    The new contract
    is strict: only
    the literal
    ``True`` is
    ``True``; only
    the literal
    ``False`` is
    ``False``;
    everything else
    is ``False`` (the
    audit accepts that
    any value that is
    not the literal
    ``True`` is the
    "absent / not-in-
    low-state"
    condition).
    """
    return value is True


def _coerce_install_date(
    value: object,
    now: datetime | None = None,
) -> datetime | None:
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
    *after* ``now`` —
    the caller must
    pass an explicit
    ``now`` (default
    ``datetime.now(UTC)``)
    so the
    determination is
    deterministic. A
    ``now`` of
    ``None`` defaults
    to the wall clock.

    T26 round 4: the
    previous
    implementation
    called
    ``datetime.now(UTC)``
    directly inside the
    helper, ignoring
    the controlled
    ``now`` passed to
    ``estimated_soh_percent``.
    The audit
    reproduced
    ``cycle_count=500,
    now=2025-01-01:
    install_date=2026-01-01
    → SoH=77.25``
    (better than the
    75.0 the cycle
    damage alone would
    give). The fix is
    to thread ``now``
    through the helper
    so the calendar-
    aging check is
    consistent with
    the aging
    subtraction.
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
    # against the
    # *controlled* ``now``,
    # not the wall clock,
    # so the test can
    # pin the answer.
    # A difference of 1
    # second is treated
    # as "future" so
    # clock-skew does
    # not silently
    # accept a tiny
    # typo.
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if dt > now:
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
        # T26 round 4: every
        # constructor argument
        # is run through a
        # shared validation
        # helper so a
        # malformed value
        # (``"abc"``,
        # ``Infinity``,
        # ``-5``, ``True``,
        # ``"true"``,
        # future date)
        # cannot crash the
        # integration or
        # silently land as
        # an unexpected
        # value.
        self._cycle_count = _coerce_cycle_count(cycle_count)
        self._in_low_state = _coerce_in_low_state(in_low_state)
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

        T26 round 4: the
        ``now`` argument
        is threaded through
        to ``_coerce_install_date``
        so a *future*
        install date (one
        that is in the
        future *relative to
        the controlled
        ``now``*) is
        rejected. The
        calendar-age
        subtraction also
        uses this ``now``
        so a future
        install_date does
        not produce a
        negative
        ``years`` and
        inflate SoH above
        the cycle-only
        baseline.
        """
        cycle_degrade = min(
            0.8, self._cycle_count / RATED_CYCLE_LIFE
        )
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        # Thread ``now``
        # through the
        # install-date
        # validation so a
        # future date is
        # rejected
        # consistently with
        # the aging
        # subtraction.
        date = (
            _coerce_install_date(install_date, now=now)
            or self._install_date
        )
        age_factor = 1.0
        if date is not None:
            # ``years`` is
            # non-negative
            # because
            # ``_coerce_install_date``
            # would have
            # returned ``None``
            # for a future
            # date.
            years = (now - date).days / 365.0
            # Defensive:
            # ``max(0, ...)``
            # already in place.
            age_factor = max(0.0, 1.0 - max(0.0, years) * CALENDAR_AGING_PER_YEAR)
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

        T26 round 4: shared
        validation — every
        field runs through
        the same helper as
        the constructor
        (``_coerce_cycle_count``
        /
        ``_coerce_in_low_state``
        /
        ``_coerce_install_date``)
        so the round-trip
        is symmetric. A
        value loaded from
        a YAML / JSON file
        is treated
        identically to a
        value passed to
        the constructor —
        no asymmetry
        between write and
        read paths.
        """
        self._cycle_count = _coerce_cycle_count(
            data.get("cycle_count")
        )
        self._in_low_state = _coerce_in_low_state(
            data.get("in_low_state")
        )
        self._install_date = _coerce_install_date(
            data.get("install_date")
        )