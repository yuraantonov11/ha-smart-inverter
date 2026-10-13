"""Outage event model + service registration.

Phase 2A: pure data + service layer for planned-outage events.

Scope:
    * ``OutageEvent`` dataclass with the operator-ratified field set.
    * ``EventStore`` wrapper around ``homeassistant.helpers.storage.Store``
      keyed by ``entry_id`` so foreign and native entries never collide.
    * ``OutageParser`` for the FIRST iteration of free-text parsing
      (UA + EN). Anything it cannot decide confidently is returned
      with ``needs_confirmation=True`` and a human-readable reason.
    * ``async_register_outage_services(hass)`` registers the
      add / update / confirm / cancel / list services under the
      existing integration domain.

Out of scope for Phase 2A (deliberate):
    * the outage planner (charge / energy math)        -> Phase 2B
    * the disabled / observation / automatic mode      -> Phase 3
    * the notification adapter                         -> Phase 4
    * any hardware write                               -> EXISTING (Phase 1)

Design discipline
-----------------
* Datetimes are stored as **offset-aware ISO 8601** strings; the
  IANA zone name is stored separately (``tz_name``). All
  comparison / overlap math is done in UTC. Naive datetimes are
  rejected at the API boundary.
* Optimistic concurrency is enforced via the ``version`` field;
  ``update_outage_event`` requires the caller to pass the version
  they observed.
* History is never deleted. ``cancelled`` and ``completed`` are
  terminal states; no transitions leave them.
* Duplicate detection scans non-cancelled, non-completed events
  and rejects overlap with the same source unless ``force=True``
  is passed.

This module depends only on the stdlib
(``datetime``, ``zoneinfo``, ``uuid``, ``re``, ``dataclasses``,
``typing``) plus ``homeassistant.core`` / ``homeassistant.exceptions``
/ ``homeassistant.helpers.storage``. It does not import the
coordinator, the planner, or any UI module.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_LOGGER = logging.getLogger(__name__)

# Home Assistant imports are guarded so the pure data-model code
# (dataclass, parser, overlap math, in-memory store variant) can be
# unit-tested without HA. The service-registration code path
# (``async_register_outage_services``) requires HA and is exercised
# by integration tests, not by these model tests.
try:
    import voluptuous as vol  # noqa: F401

    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.exceptions import ServiceValidationError
    from homeassistant.helpers.storage import Store

    from ..const import DOMAIN

    _HA_AVAILABLE = True
except ImportError:  # noqa: BLE001 -- testing without HA
    HomeAssistant = Any  # type: ignore[assignment,misc]
    ServiceCall = Any  # type: ignore[assignment,misc]
    ServiceValidationError = Exception  # type: ignore[assignment,misc]
    Store = None  # type: ignore[assignment]
    DOMAIN = "powmr_inverter"  # type: ignore[assignment]
    _HA_AVAILABLE = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STORE_KEY_BASE = DOMAIN + "_outage_events"
STORE_VERSION = 1

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_CANCELLED = "cancelled"
STATUS_COMPLETED = "completed"

_TERMINAL_STATUSES = frozenset({STATUS_CANCELLED, STATUS_COMPLETED})
_VALID_STATUSES = frozenset(
    {STATUS_PENDING, STATUS_CONFIRMED, STATUS_CANCELLED, STATUS_COMPLETED}
)

# Source taxonomy. ``adapter:<name>`` is accepted as long as the
# prefix matches; the bare prefix ``"adapter"`` is also accepted as
# a defensive fallback so a future caller that forgets the
# ``:<name>`` suffix is at least syntactically valid.
_VALID_SOURCES = frozenset(
    {"manual:service", "manual:text", "system", "adapter"}
)


PARSER_AMBIGUOUS_REASON = "parser: complex phrasing"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


def _now_iso_utc() -> str:
    """Return the current server time as an offset-aware ISO 8601 string.

    The audit-trail fields (``received_at``, ``updated_at``) are
    UTC so they remain unambiguous regardless of HA's configured
    timezone.
    """
    return datetime.now(timezone.utc).isoformat()


@dataclass
class OutageEvent:
    """A planned-outage event.

    Every field has a purpose -- see the operator-ratified contract.
    The dataclass is the **only** shape the rest of the codebase
    should depend on. ``to_dict`` / ``from_dict`` are the persistence
    boundary; no other module should read or write raw event dicts.
    """

    # Identity
    id: str
    source: str
    raw_text: str | None

    # Time window (stored as offset-aware ISO; tz_name is the IANA zone)
    start_iso: str
    end_iso: str
    tz_name: str

    # Audit
    received_at: str
    status: str
    needs_confirmation: bool
    ambiguity_reason: str | None

    # Optimistic concurrency
    version: int
    updated_at: str

    # Operator note
    notes: str | None

    def __post_init__(self) -> None:
        """Validate the time-window fields at construction.

        ``OutageEvent`` is the only public shape the rest of the
        codebase depends on; validating in ``__post_init__`` means
        every code path -- service handler, parser, programmatic
        caller -- gets the same enforcement. Cheap because the
        string fields are already small and ``fromisoformat``
        rejects malformed inputs in microseconds.

        We do NOT validate ``received_at`` / ``updated_at`` here
        because the model itself produces them via ``_now_iso_utc``
        which always emits an offset-aware string. External code
        that bypasses the constructor with ``from_dict`` is
        similarly audited (the field exists, the parser enforces
        it on the way in for the service-call path).
        """
        _require_offset_aware_iso("start_iso", self.start_iso)
        _require_offset_aware_iso("end_iso", self.end_iso)
        _validate_tz_name(self.tz_name)
        if self.status not in _VALID_STATUSES:
            raise ValueError(
                f"status {self.status!r} is not one of {sorted(_VALID_STATUSES)}"
            )
        if not isinstance(self.version, int) or self.version < 1:
            raise ValueError(
                f"version must be a positive int; got {self.version!r}"
            )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict for JSON storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OutageEvent":
        """Build an event from a stored dict.

        Defensive: missing required fields raise ``ValueError``;
        unknown fields are silently dropped. The contract is the
        field set above -- the dataclass is the source of truth.
        """
        if not isinstance(data, dict):
            raise ValueError(
                f"event payload must be a dict; got {type(data).__name__}"
            )
        allowed = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for name in allowed:
            if name not in data:
                raise ValueError(f"event missing required field {name!r}")
            kwargs[name] = data[name]
        unknown = set(data) - allowed
        if unknown:
            _LOGGER.debug("Dropping unknown keys on event load: %s", sorted(unknown))
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _require_offset_aware_iso(label: str, value: Any) -> datetime:
    """Parse an offset-aware ISO 8601 string; reject naive datetimes.

    Returns the parsed ``datetime`` (with tzinfo set). Raises
    ``ValueError`` with a clear message when the input is missing,
    malformed, or naive.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"{label} must be an ISO 8601 string; got {type(value).__name__}"
        )
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{label} is not a valid ISO 8601 string: {value!r} ({exc})"
        ) from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(
            f"{label} must include a UTC offset (e.g. '+03:00'); "
            "naive datetimes are not allowed"
        )
    return dt


def _validate_tz_name(tz_name: str | None) -> str:
    """Validate an IANA tz name; raise ``ValueError`` on unknown zones."""
    if not isinstance(tz_name, str) or not tz_name:
        raise ValueError("tz must be a non-empty IANA tz name string")
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone {tz_name!r}") from exc
    return tz_name


def _validate_source(source: str | None) -> str:
    """Validate a source string; fall back to ``"manual:service"`` when None."""
    if source is None:
        return "manual:service"
    if not isinstance(source, str):
        raise ValueError("source must be a string")
    # ``adapter:<name>`` is accepted as long as the prefix matches.
    if source == "adapter" or source.startswith("adapter:"):
        return source
    if source in _VALID_SOURCES:
        return source
    raise ValueError(
        f"source {source!r} is not one of {sorted(_VALID_SOURCES)} "
        "or 'adapter:<name>'"
    )


def _default_tz_name(hass: HomeAssistant | None) -> str:
    """Return HA's configured timezone, or raise a clear ``ValueError``."""
    if hass is None:
        raise ValueError(
            "tz is required when no hass is provided (cannot read "
            "hass.config.time_zone)"
        )
    tz_name = None
    try:
        tz_name = getattr(getattr(hass, "config", None), "time_zone", None)
    except Exception:  # noqa: BLE001 -- hass.config may be unavailable in tests
        tz_name = None
    if not tz_name:
        raise ValueError(
            "tz is required and no hass.config.time_zone is available; "
            "pass tz explicitly"
        )
    return _validate_tz_name(tz_name)


def _utc_instant(iso_str: str) -> datetime:
    """Convert an offset-aware ISO string to a UTC ``datetime``.

    Used for comparison / overlap math only. The stored ISO strings
    always preserve their original offset for the audit trail.
    """
    dt = _require_offset_aware_iso("iso datetime", iso_str)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Overlap math
# ---------------------------------------------------------------------------


def events_overlap(a: OutageEvent, b: OutageEvent) -> bool:
    """Open-interval overlap test.

    Two events overlap iff ``max(start_a, start_b) < min(end_a, end_b)``.
    Same-start-equals-same-end (zero-length windows) is **not**
    considered overlap.

    Returns ``False`` if either event has a zero / negative duration.
    """
    a_start = _utc_instant(a.start_iso)
    a_end = _utc_instant(a.end_iso)
    b_start = _utc_instant(b.start_iso)
    b_end = _utc_instant(b.end_iso)
    if a_end <= a_start or b_end <= b_start:
        return False
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    return latest_start < earliest_end


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class _InMemoryStore:
    """Tiny async store with the surface ``EventStore`` needs.

    Used by the model tests so the data-model can be exercised
    without HA. The HA ``Store`` helper is a superset (it also
    persists to disk); the production code path uses it via the
    ``Store`` parameter of ``EventStore``.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] | None = None

    async def async_load(self) -> dict[str, Any] | None:
        return self._data

    async def async_save(self, data: dict[str, Any]) -> None:
        # Deep-copy via serialisation so the caller's mutation after
        # save does not corrupt the stored payload.
        import json as _json

        self._data = _json.loads(_json.dumps(data))


class EventStore:
    """Wrapper around a ``Store``-like helper.

    The constructor accepts any object that exposes
    ``async_load() -> dict | None`` and ``async_save(dict) -> None``.
    The production path constructs an HA ``Store``; the unit-test
    path uses ``_InMemoryStore`` so tests do not need HA.

    Storage version is pinned at ``STORE_VERSION``. A version
    mismatch raises -- the migration story will land in a later
    phase if and when the shape changes; silently dropping data
    would violate the operator's audit requirement.

    The store is keyed by ``entry_id`` (per-load) so multiple loaded
    inverter entries (foreign + native) never collide.
    """

    def __init__(
        self,
        storage: Any,
        entry_id: str,
    ) -> None:
        if not entry_id:
            raise ValueError("entry_id is required for the outage event store")
        self._storage = storage
        self._entry_id = entry_id
        self._events: dict[str, OutageEvent] = {}

    @classmethod
    def in_memory(cls, entry_id: str = "test-entry") -> "EventStore":
        """Construct a store backed by the in-memory helper.

        Convenience for unit tests.
        """
        return cls(_InMemoryStore(), entry_id)

    @property
    def entry_id(self) -> str:
        return self._entry_id

    async def async_load(self) -> None:
        """Load the persisted state into memory.

        ``None`` from the underlying storage means "no events yet"
        and leaves ``self._events`` empty. A version mismatch
        raises ``ValueError`` instead of dropping data.
        """
        data = await self._storage.async_load()
        if data is None:
            self._events = {}
            return
        if not isinstance(data, dict):
            raise ValueError(
                f"outage event store {self._entry_id!r} payload must be a "
                f"dict; got {type(data).__name__}"
            )
        stored_version = data.get("__version__")
        if stored_version is not None and stored_version != STORE_VERSION:
            raise ValueError(
                f"outage event store {self._entry_id!r} has version "
                f"{stored_version!r}; expected {STORE_VERSION}. "
                "Migration is not implemented yet; refusing to load."
            )
        events_raw = data.get("events", {})
        if not isinstance(events_raw, dict):
            raise ValueError(
                f"outage event store {self._entry_id!r} 'events' must be a dict"
            )
        loaded: dict[str, OutageEvent] = {}
        for event_id, payload in events_raw.items():
            loaded[event_id] = OutageEvent.from_dict(payload)
        self._events = loaded

    async def async_save(self) -> None:
        """Persist the in-memory state."""
        payload: dict[str, Any] = {
            "__version__": STORE_VERSION,
            "events": {
                event_id: ev.to_dict() for event_id, ev in self._events.items()
            },
        }
        await self._storage.async_save(payload)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def get(self, event_id: str) -> OutageEvent | None:
        return self._events.get(event_id)

    def list_events(
        self,
        status_filter: str | None = None,
    ) -> list[OutageEvent]:
        """Return events sorted newest-first by ``received_at``.

        Ties on ``received_at`` are broken by ``updated_at`` so a
        later confirm / cancel floats above the create for the
        same event.
        """
        events = list(self._events.values())
        if status_filter is not None:
            if status_filter not in _VALID_STATUSES:
                raise ValueError(f"unknown status filter {status_filter!r}")
            events = [e for e in events if e.status == status_filter]
        events.sort(key=lambda e: (e.received_at, e.updated_at), reverse=True)
        return events

    async def add(self, event: OutageEvent) -> OutageEvent:
        """Insert a new event. ``event.id`` must be unique."""
        if event.id in self._events:
            raise ValueError(f"event id {event.id!r} already exists")
        self._events[event.id] = event
        await self.async_save()
        return event

    async def update(self, event: OutageEvent) -> OutageEvent:
        """Replace an existing event in-place.

        Caller is responsible for bumping ``version`` and
        ``updated_at``; this is a primitive for the state-machine
        code paths and does not enforce optimistic concurrency on
        its own. ``async_update_outage_event`` is the version-checked
        entry point.
        """
        if event.id not in self._events:
            raise ValueError(f"event id {event.id!r} does not exist")
        self._events[event.id] = event
        await self.async_save()
        return event

    def iter_active(self) -> Iterable[OutageEvent]:
        """Iterate non-terminal events.

        Used by duplicate detection. Completed events are kept in
        the store for audit but do not block a new overlapping
        insert -- the operator may want to schedule the next one
        without clearing history.
        """
        for ev in self._events.values():
            if ev.status not in _TERMINAL_STATUSES:
                yield ev


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def _bump(event: OutageEvent, *, status: str | None = None) -> OutageEvent:
    """Return a copy with ``version+1`` and ``updated_at`` refreshed."""
    return replace(
        event,
        status=status if status is not None else event.status,
        version=event.version + 1,
        updated_at=_now_iso_utc(),
    )


async def confirm_event(store: EventStore, event_id: str) -> OutageEvent:
    """Transition ``pending`` -> ``confirmed``.

    Idempotent on already-confirmed events: returns a copy with
    version+1 so the caller's audit shows that the confirm was
    acknowledged. Raises ``ValueError`` for any other status.
    """
    event = store.get(event_id)
    if event is None:
        raise ValueError(f"event {event_id!r} not found")
    if event.status == STATUS_CONFIRMED:
        return await store.update(_bump(event, status=STATUS_CONFIRMED))
    if event.status != STATUS_PENDING:
        raise ValueError(
            f"cannot confirm event {event_id!r} from status {event.status!r}"
        )
    return await store.update(_bump(event, status=STATUS_CONFIRMED))


async def cancel_event(
    store: EventStore,
    event_id: str,
    *,
    reason: str | None = None,
) -> OutageEvent:
    """Transition ``pending`` / ``confirmed`` -> ``cancelled``.

    Cancelling a ``cancelled`` event is a no-op that bumps version
    so the caller's audit shows the intent. Cancelling a
    ``completed`` event is a hard error: history is preserved and
    terminal states are terminal.
    """
    event = store.get(event_id)
    if event is None:
        raise ValueError(f"event {event_id!r} not found")
    if event.status == STATUS_COMPLETED:
        raise ValueError(
            f"cannot cancel event {event_id!r}: status is 'completed' (terminal)"
        )
    notes = event.notes
    if reason:
        sep = "\n" if notes else ""
        notes = f"{notes}{sep}cancel: {reason}" if notes else f"cancel: {reason}"
    bumped = _bump(event, status=STATUS_CANCELLED)
    if notes != event.notes:
        bumped = replace(bumped, notes=notes)
    if event.status == STATUS_CANCELLED:
        # No-op that still bumps so audit sees the intent.
        return await store.update(bumped)
    return await store.update(bumped)


async def complete_event(store: EventStore, event_id: str) -> OutageEvent:
    """Transition ``confirmed`` -> ``completed``.

    Phase 2A exposes this so Phase 2B can wire it up without
    touching the model. Idempotent on already-completed events.
    """
    event = store.get(event_id)
    if event is None:
        raise ValueError(f"event {event_id!r} not found")
    if event.status == STATUS_COMPLETED:
        return event
    if event.status != STATUS_CONFIRMED:
        raise ValueError(
            f"cannot complete event {event_id!r} from status {event.status!r}; "
            "must be 'confirmed'"
        )
    return await store.update(_bump(event, status=STATUS_COMPLETED))


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


def _make_probe(
    *, start_iso: str, end_iso: str, source: str
) -> OutageEvent:
    """Build a throwaway event used purely for overlap comparison.

    ``version`` is set to 1 so the dataclass ``__post_init__``
    validation (positive int) accepts the probe; the value is
    never persisted and never compared, so the choice is
    arbitrary as long as it is a positive int.
    """
    return OutageEvent(
        id="__probe__",
        source=source,
        raw_text=None,
        start_iso=start_iso,
        end_iso=end_iso,
        tz_name="UTC",  # tz not consulted in overlap math
        received_at="1970-01-01T00:00:00+00:00",
        status=STATUS_PENDING,
        needs_confirmation=False,
        ambiguity_reason=None,
        version=1,
        updated_at="1970-01-01T00:00:00+00:00",
        notes=None,
    )


def find_duplicate(
    store: EventStore,
    *,
    start_iso: str,
    end_iso: str,
    source: str,
    exclude_id: str | None = None,
) -> OutageEvent | None:
    """Return the first active event that overlaps AND shares a source.

    ``exclude_id`` lets the update path ignore the event being
    updated. Returns ``None`` when no duplicate exists.
    """
    probe = _make_probe(start_iso=start_iso, end_iso=end_iso, source=source)
    for candidate in store.iter_active():
        if exclude_id is not None and candidate.id == exclude_id:
            continue
        if candidate.source != source:
            continue
        if events_overlap(probe, candidate):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Free-text parser
# ---------------------------------------------------------------------------


_HHMM = r"(\d{1,2}):(\d{2})"
_DDMMYYYY = r"(\d{1,2})\.(\d{1,2})\.(\d{4})"
_ISOISH = r"(\d{4})-(\d{1,2})-(\d{1,2})"


@dataclass
class ParsedOutage:
    """Result of parsing a single free-text phrase.

    ``needs_confirmation=True`` means the operator must explicitly
    confirm before the planner will act on the event.
    ``start_iso`` / ``end_iso`` may still be populated when the
    parser had enough information to compute them, so the
    operator sees what the system would have used.
    """

    start_iso: str | None
    end_iso: str | None
    tz_name: str
    needs_confirmation: bool
    ambiguity_reason: str | None

    def to_event(
        self,
        *,
        source: str = "manual:text",
        raw_text: str | None = None,
        notes: str | None = None,
    ) -> OutageEvent:
        """Build an ``OutageEvent`` with the standard create defaults.

        ``start_iso`` / ``end_iso`` may be empty strings when the
        parser could not determine them -- in that case the caller
        (``async_add_outage_event``) is expected to reject the
        event with a clear ``ValueError``.
        """
        now = _now_iso_utc()
        return OutageEvent(
            id=uuid.uuid4().hex,
            source=source,
            raw_text=raw_text,
            start_iso=self.start_iso or "",
            end_iso=self.end_iso or "",
            tz_name=self.tz_name,
            received_at=now,
            status=STATUS_PENDING,
            needs_confirmation=self.needs_confirmation,
            ambiguity_reason=self.ambiguity_reason,
            version=1,
            updated_at=now,
            notes=notes,
        )


class OutageParser:
    """First-iteration free-text parser.

    Conservative by design. Every public method either returns a
    fully-decided ``ParsedOutage`` with ``needs_confirmation=False``
    OR returns one with ``needs_confirmation=True`` and a clear
    ``ambiguity_reason``. The parser never invents dates or times.

    Supported phrasings (UA + EN):
        1. ``завтра з 9:00 до 18:00 буде відключення``
        2. ``15.10.2026 08:00-18:00`` (dd.mm.yyyy + range)
        3. ``2026-10-15 08:00 to 18:00`` (ISO-ish date + range)
        4. ``сьогодні з 14:00``           (single endpoint -> needs_confirmation)
        5. ``через 2 години на 3 години``  (relative -> needs_confirmation)

    Anything else returns ``needs_confirmation=True`` with
    reason ``parser: complex phrasing``.
    """

    def __init__(self, tz_name: str) -> None:
        self._tz_name = _validate_tz_name(tz_name)
        self._tz = ZoneInfo(self._tz_name)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def parse(self, text: str, *, now: datetime | None = None) -> ParsedOutage:
        """Parse a free-text phrase.

        ``now`` is injected for tests; defaults to the current
        server time converted into the parser's tz. ``now`` MUST
        be offset-aware; a naive ``now`` is a programming error
        and raises ``ValueError``.
        """
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if now is None:
            now = datetime.now(self._tz)
        elif now.tzinfo is None:
            raise ValueError("parser 'now' must be offset-aware")

        cleaned = text.strip().lower()
        if not cleaned:
            return ParsedOutage(
                start_iso=None,
                end_iso=None,
                tz_name=self._tz_name,
                needs_confirmation=True,
                ambiguity_reason="parser: empty input",
            )

        # Order matters: each handler is conservative.
        for handler in (
            self._parse_ukrainian_tomorrow_today,
            self._parse_dd_mm_yyyy_range,
            self._parse_iso_date_range,
            self._parse_relative,
        ):
            result = handler(cleaned, now)
            if result is not None:
                return result

        # Catch-all single-endpoint when no range / relative matched.
        single_endpoint = self._parse_single_endpoint(cleaned, now)
        if single_endpoint is not None:
            return single_endpoint

        return ParsedOutage(
            start_iso=None,
            end_iso=None,
            tz_name=self._tz_name,
            needs_confirmation=True,
            ambiguity_reason=PARSER_AMBIGUOUS_REASON,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _local(self, year: int, month: int, day: int, hour: int, minute: int) -> datetime:
        """Return a tz-aware datetime in the parser's zone."""
        return datetime(year, month, day, hour, minute, tzinfo=self._tz)

    def _ok(self, start_local: datetime, end_local: datetime) -> ParsedOutage:
        return ParsedOutage(
            start_iso=start_local.isoformat(),
            end_iso=end_local.isoformat(),
            tz_name=self._tz_name,
            needs_confirmation=False,
            ambiguity_reason=None,
        )

    def _needs_confirm(
        self,
        *,
        start_iso: str | None,
        end_iso: str | None,
        reason: str,
    ) -> ParsedOutage:
        return ParsedOutage(
            start_iso=start_iso,
            end_iso=end_iso,
            tz_name=self._tz_name,
            needs_confirmation=True,
            ambiguity_reason=reason,
        )

    def _resolve_relative_day(self, text: str, now: datetime):
        """Resolve ``today`` / ``tomorrow`` (UA + EN) to a calendar date."""
        base = now.astimezone(self._tz).date()
        if "завтра" in text or "tomorrow" in text:
            return base + timedelta(days=1)
        return base

    # ------------------------------------------------------------------
    # Handler 1: UA / EN day keyword + range or single endpoint
    # ------------------------------------------------------------------
    def _parse_ukrainian_tomorrow_today(
        self, text: str, now: datetime
    ) -> ParsedOutage | None:
        if not (
            ("завтра" in text)
            or ("сьогодні" in text)
            or ("tomorrow" in text)
            or ("today" in text)
        ):
            return None
        # Range form: ``з 9:00 до 18:00`` / ``from 9:00 to 18:00``
        m = re.search(
            rf"(?:з|from)\s+{_HHMM}\s*(?:до|to)\s*{_HHMM}",
            text,
        )
        if m:
            return self._build_day_range(m, text, now)
        # Single endpoint form: ``з 14:00`` / ``at 14:00``
        m = re.search(rf"(?:з|at)\s+{_HHMM}", text)
        if m:
            return self._build_single_endpoint(m, text, now)
        return None

    def _build_day_range(
        self,
        m: re.Match[str],
        text: str,
        now: datetime,
    ) -> ParsedOutage:
        sh_h, sh_m = int(m.group(1)), int(m.group(2))
        eh_h, eh_m = int(m.group(3)), int(m.group(4))
        day = self._resolve_relative_day(text, now)
        start = self._local(day.year, day.month, day.day, sh_h, sh_m)
        end = self._local(day.year, day.month, day.day, eh_h, eh_m)
        if end <= start:
            return self._needs_confirm(
                start_iso=start.isoformat(),
                end_iso=end.isoformat(),
                reason="parser: end must be after start",
            )
        return self._ok(start, end)

    def _build_single_endpoint(
        self,
        m: re.Match[str],
        text: str,
        now: datetime,
    ) -> ParsedOutage:
        sh_h, sh_m = int(m.group(1)), int(m.group(2))
        day = self._resolve_relative_day(text, now)
        start = self._local(day.year, day.month, day.day, sh_h, sh_m)
        return self._needs_confirm(
            start_iso=start.isoformat(),
            end_iso=None,
            reason="parser: missing end time",
        )

    # ------------------------------------------------------------------
    # Handler 2: ``dd.mm.yyyy hh:mm-hh:mm``
    # ------------------------------------------------------------------
    def _parse_dd_mm_yyyy_range(
        self, text: str, now: datetime
    ) -> ParsedOutage | None:
        m = re.search(rf"{_DDMMYYYY}\s+{_HHMM}\s*-\s*{_HHMM}", text)
        if not m:
            return None
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        sh_h, sh_m = int(m.group(4)), int(m.group(5))
        eh_h, eh_m = int(m.group(6)), int(m.group(7))
        try:
            start = self._local(y, mo, d, sh_h, sh_m)
            end = self._local(y, mo, d, eh_h, eh_m)
        except ValueError:
            return self._needs_confirm(
                start_iso=None,
                end_iso=None,
                reason="parser: invalid date in dd.mm.yyyy range",
            )
        if end <= start:
            return self._needs_confirm(
                start_iso=start.isoformat(),
                end_iso=end.isoformat(),
                reason="parser: end must be after start",
            )
        return self._ok(start, end)

    # ------------------------------------------------------------------
    # Handler 3: ``yyyy-mm-dd hh:mm to hh:mm``
    # ------------------------------------------------------------------
    def _parse_iso_date_range(
        self, text: str, now: datetime
    ) -> ParsedOutage | None:
        m = re.search(
            rf"{_ISOISH}\s+{_HHMM}\s*(?:to|—|–)\s*{_HHMM}",
            text,
        )
        if not m:
            return None
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        sh_h, sh_m = int(m.group(4)), int(m.group(5))
        eh_h, eh_m = int(m.group(6)), int(m.group(7))
        try:
            start = self._local(y, mo, d, sh_h, sh_m)
            end = self._local(y, mo, d, eh_h, eh_m)
        except ValueError:
            return self._needs_confirm(
                start_iso=None,
                end_iso=None,
                reason="parser: invalid date in ISO range",
            )
        if end <= start:
            return self._needs_confirm(
                start_iso=start.isoformat(),
                end_iso=end.isoformat(),
                reason="parser: end must be after start",
            )
        return self._ok(start, end)

    # ------------------------------------------------------------------
    # Handler 4: relative phrasing
    # ------------------------------------------------------------------
    def _parse_relative(
        self, text: str, now: datetime
    ) -> ParsedOutage | None:
        ua = re.search(r"через\s+\d+\s*(хвилин|годин|хв|год)", text)
        en = re.search(r"in\s+\d+\s*(minute|minutes|hour|hours)", text)
        if not (ua or en):
            return None
        return self._needs_confirm(
            start_iso=None,
            end_iso=None,
            reason="parser: relative phrasing not auto-decided",
        )

    # ------------------------------------------------------------------
    # Catch-all single endpoint (no range / no relative keyword)
    # ------------------------------------------------------------------
    def _parse_single_endpoint(
        self, text: str, now: datetime
    ) -> ParsedOutage | None:
        if not (
            ("сьогодні" in text)
            or ("завтра" in text)
            or ("today" in text)
            or ("tomorrow" in text)
        ):
            return None
        m = re.search(_HHMM, text)
        if not m:
            return None
        return self._build_single_endpoint(m, text, now)


# ---------------------------------------------------------------------------
# Service registration
# ---------------------------------------------------------------------------


def _stores(hass: HomeAssistant) -> dict[str, EventStore]:
    """Return (or lazily create) the per-entry ``EventStore`` map.

    Stored under ``hass.data[DOMAIN][entry_id]["outage_store"]``. The
    coordinator's data bundle already exists by the time this is
    called (``async_setup_entry`` registers services after platform
    setup), but we use ``setdefault`` defensively.
    """
    data = hass.data.setdefault(DOMAIN, {})
    # ``data`` is a dict keyed by entry_id whose values are bundles;
    # we use a separate slot at the top level to avoid coupling to
    # the coordinator bundle shape.
    stores_map = hass.data.setdefault(f"{DOMAIN}_outage_stores", {})
    return stores_map  # type: ignore[return-value]


def _get_store(hass: HomeAssistant, entry_id: str | None = None) -> EventStore:
    """Return the ``EventStore`` for ``entry_id``.

    When ``entry_id`` is ``None``, require exactly one loaded entry
    (mirroring the resolver contract used by the existing services).
    """
    loaded = hass.data.get(f"{DOMAIN}_outage_stores", {})
    if entry_id is None:
        loaded_entry_ids = list(loaded.keys())
        if not loaded_entry_ids:
            raise ValueError(
                "No Inverter config entry is loaded; cannot resolve outage store"
            )
        if len(loaded_entry_ids) > 1:
            raise ValueError(
                "Multiple Inverter config entries loaded; specify entry_id "
                "to choose one"
            )
        return loaded[loaded_entry_ids[0]]
    if entry_id not in loaded:
        raise ValueError(f"Unknown inverter config entry {entry_id!r}")
    return loaded[entry_id]


def _ensure_store(
    hass: HomeAssistant,
    *,
    entry_id: str,
) -> EventStore:
    """Create the store on demand; used by the test path and the bootstrap."""
    stores_map = hass.data.setdefault(f"{DOMAIN}_outage_stores", {})
    if entry_id not in stores_map:
        if not _HA_AVAILABLE:
            raise RuntimeError(
                "EventStore construction requires the Home Assistant runtime; "
                "no HA ``Store`` available in the current process"
            )
        storage = Store(
            hass,
            STORE_VERSION,
            f"{STORE_KEY_BASE}_{entry_id}",
        )
        stores_map[entry_id] = EventStore(storage, entry_id)
    return stores_map[entry_id]


async def _build_event_from_call(
    hass: HomeAssistant,
    call: ServiceCall,
) -> OutageEvent:
    """Build (but do not insert) an ``OutageEvent`` from a service call.

    Shared by ``add_outage_event`` (text + structured) and the
    internal ``_insert_event`` helper. Always defaults
    ``status="pending"``; auto-confirm is handled in the service
    entry point when the parser produced an unambiguous result.
    """
    text = call.data.get("text")
    start = call.data.get("start")
    end = call.data.get("end")
    tz = call.data.get("tz")
    source = _validate_source(call.data.get("source", "manual:service"))
    notes = call.data.get("notes")

    # Time-zone resolution.
    if tz is None:
        tz_name = _default_tz_name(hass)
    else:
        tz_name = _validate_tz_name(tz)

    parsed: ParsedOutage | None = None
    raw_text: str | None = None

    if text is not None:
        raw_text = str(text)
        parsed = OutageParser(tz_name).parse(raw_text)

    if start is not None or end is not None:
        # Structured insert wins over text when both are supplied;
        # the parser's output is discarded. This keeps the
        # service contract simple: a structured insert is
        # authoritative.
        if start is None or end is None:
            raise ServiceValidationError(
                "structured insert requires both 'start' and 'end'"
            )
        _require_offset_aware_iso("start", start)
        _require_offset_aware_iso("end", end)
        # Build a ParsedOutage that overrides the parser result.
        parsed = ParsedOutage(
            start_iso=str(start),
            end_iso=str(end),
            tz_name=tz_name,
            needs_confirmation=False,
            ambiguity_reason=None,
        )
    elif parsed is None:
        # Neither text nor structured input -- reject.
        raise ServiceValidationError(
            "add_outage_event requires 'text' or both 'start' and 'end'"
        )

    assert parsed is not None  # for type-checkers
    if not parsed.start_iso or not parsed.end_iso:
        raise ServiceValidationError(
            "parser could not determine start/end; "
            f"reason: {parsed.ambiguity_reason or 'unknown'}"
        )
    return parsed.to_event(source=source, raw_text=raw_text, notes=notes)


async def async_register_outage_services(hass: HomeAssistant) -> None:
    """Register the add / update / confirm / cancel / list services.

    Phase 2B is expected to add a single line in
    ``__init__.py::async_setup_entry`` immediately after the
    existing ``async_register_services`` call -- see the report
    for the exact snippet.
    """

    async def handle_add_outage_event(call: ServiceCall) -> dict[str, Any]:
        event = await _build_event_from_call(hass, call)

        # Auto-confirm when the event is fully unambiguous.
        if not event.needs_confirmation:
            event = replace(event, status=STATUS_CONFIRMED)
        # else: leave as pending so the operator confirms explicitly.

        # Duplicate guard: same source + same time window as an
        # active event -> reject unless force=True.
        force = bool(call.data.get("force", False))
        if not force:
            entry_id = call.data.get("entry_id")
            store = _get_store(hass, entry_id)
            existing = find_duplicate(
                store,
                start_iso=event.start_iso,
                end_iso=event.end_iso,
                source=event.source,
            )
            if existing is not None:
                raise ServiceValidationError(
                    f"duplicate outage event {existing.id!r} "
                    f"already overlaps [{existing.start_iso}, {existing.end_iso}) "
                    "in the same source; pass force=true to override"
                )

        # Persist via the store.
        entry_id = call.data.get("entry_id")
        store = _ensure_store(hass, entry_id=entry_id or _resolve_single_entry_id(hass))
        if store.get(event.id) is not None:
            raise ServiceValidationError(
                f"event id {event.id!r} already exists (uuid collision)"
            )
        await store.add(event)

        return {
            "id": event.id,
            "status": event.status,
            "needs_confirmation": event.needs_confirmation,
            "ambiguity_reason": event.ambiguity_reason,
        }

    async def handle_update_outage_event(call: ServiceCall) -> dict[str, Any]:
        event_id = call.data["id"]
        passed_version = int(call.data["version"])
        entry_id = call.data.get("entry_id")
        store = _get_store(hass, entry_id)

        existing = store.get(event_id)
        if existing is None:
            raise ServiceValidationError(f"outage event {event_id!r} not found")
        if existing.version != passed_version:
            raise ServiceValidationError(
                f"version mismatch for outage event {event_id!r}: "
                f"storage has version {existing.version}, "
                f"call passed version {passed_version}"
            )

        # Apply optional updates. Optimistic concurrency is checked
        # above; the store-side update() primitive is version-agnostic.
        new_start = call.data.get("start")
        new_end = call.data.get("end")
        new_tz = call.data.get("tz")
        new_notes = call.data.get("notes")

        start_iso = existing.start_iso
        end_iso = existing.end_iso
        tz_name = existing.tz_name

        if new_start is not None or new_end is not None:
            if new_start is None or new_end is None:
                raise ServiceValidationError(
                    "updating the time window requires both 'start' and 'end'"
                )
            _require_offset_aware_iso("start", new_start)
            _require_offset_aware_iso("end", new_end)
            start_iso = str(new_start)
            end_iso = str(new_end)
        if new_tz is not None:
            tz_name = _validate_tz_name(new_tz)

        updated = replace(
            existing,
            start_iso=start_iso,
            end_iso=end_iso,
            tz_name=tz_name,
            notes=new_notes if new_notes is not None else existing.notes,
            version=existing.version + 1,
            updated_at=_now_iso_utc(),
        )
        await store.update(updated)
        return updated.to_dict()

    async def handle_confirm_outage_event(call: ServiceCall) -> dict[str, Any]:
        event_id = call.data["id"]
        entry_id = call.data.get("entry_id")
        store = _get_store(hass, entry_id)
        updated = await confirm_event(store, event_id)
        return updated.to_dict()

    async def handle_cancel_outage_event(call: ServiceCall) -> dict[str, Any]:
        event_id = call.data["id"]
        reason = call.data.get("reason")
        entry_id = call.data.get("entry_id")
        store = _get_store(hass, entry_id)
        updated = await cancel_event(store, event_id, reason=reason)
        return updated.to_dict()

    async def handle_list_outage_events(call: ServiceCall) -> dict[str, Any]:
        status_filter = call.data.get("status")
        entry_id = call.data.get("entry_id")
        store = _get_store(hass, entry_id)
        events = store.list_events(status_filter=status_filter)
        return {"events": [e.to_dict() for e in events]}

    hass.services.async_register(
        DOMAIN,
        "add_outage_event",
        handle_add_outage_event,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Optional("text"): str,
                vol.Optional("start"): str,
                vol.Optional("end"): str,
                vol.Optional("tz"): str,
                vol.Optional("source"): str,
                vol.Optional("notes"): str,
                vol.Optional("force", default=False): bool,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "update_outage_event",
        handle_update_outage_event,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Required("id"): str,
                vol.Required("version"): int,
                vol.Optional("start"): str,
                vol.Optional("end"): str,
                vol.Optional("tz"): str,
                vol.Optional("notes"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "confirm_outage_event",
        handle_confirm_outage_event,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Required("id"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "cancel_outage_event",
        handle_cancel_outage_event,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Required("id"): str,
                vol.Optional("reason"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "list_outage_events",
        handle_list_outage_events,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Optional("status"): str,
            }
        ),
    )


def _resolve_single_entry_id(hass: HomeAssistant) -> str:
    """Return the only loaded entry's id; raise on ambiguity or absence."""
    stores_map = hass.data.get(f"{DOMAIN}_outage_stores", {})
    ids = list(stores_map.keys())
    if not ids:
        raise ValueError("No Inverter config entry is loaded")
    if len(ids) > 1:
        raise ValueError(
            "Multiple Inverter config entries loaded; specify entry_id"
        )
    return ids[0]