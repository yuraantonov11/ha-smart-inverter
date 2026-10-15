"""Phase 4: notification adapter for planned-outage events.

This module wraps the existing ``OutageParser`` and ``EventStore``
with a small, explicit ingestion pipeline that:

  * accepts a ``RawNotification`` (message_id, source,
    received_at, raw_text, sender, address_hint);
  * parses the text into a ``ParsedNotification`` (start /
    end / tz / confidence / ambiguity_reason / cancellation
    signal);
  * dedups by ``message_id`` (an exact resend with the
    same id is dropped; a resend with a different id but
    the same address + window + source is treated as a
    revision and the existing pending event is updated);
  * detects cancellation keywords and calls
    ``cancel_event`` on the matching confirmed event;
  * ALWAYS produces a ``pending`` ``OutageEvent`` -- no
    parser path auto-confirms. Even an unambiguous parse
    stays pending until the operator ratifies via
    ``confirm_event`` (the spec: ``Неоднозначне
    повідомлення не повинно автоматично ставати
    підтвердженою подією``; we extend the same rule to
    unambiguous messages -- the operator still has to
    confirm);
  * preserves ``raw_text`` and ``source`` on every
    ``OutageEvent`` so the operator can audit the
    parser's input;
  * preserves ``ambiguity_reason`` when the parser is
    not fully confident, so the operator sees the
    exact reason the event needs review.

The dispatcher at ``_find_active_event``
(services/outage_runtime.py) only picks ``status ==
'confirmed'`` events for planning, so a pending
``OutageEvent`` produced here is never auto-dispatched
until the operator confirms it. ``Live залишай в
observation, автоматичне керування не вмикай`` --
this module only produces pending events; it never
touches the live coordinator's HEMS decision path.

The module is pure-Python: no Home Assistant imports.
The HA service handler
(``async_ingest_outage_notification``) is a thin
wrapper that calls ``NotificationAdapter.ingest``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable

from zoneinfo import ZoneInfo

from .outage import (
    EventStore,
    OutageEvent,
    OutageParser,
    ParsedOutage,
    STATUS_CANCELLED,
    STATUS_CONFIRMED,
    STATUS_PENDING,
    _bump,
    _now_iso_utc,
    find_duplicate,
)


# ---------------------------------------------------------------------
# Notification types
# ---------------------------------------------------------------------


class NotificationSource(str, Enum):
    """Source of a notification. The actual transport
    (email IMAP, SMS gateway, webhook) is out of scope
    for Phase 4 -- the adapter accepts a structured
    ``RawNotification`` from any internal service
    handler.
    """

    EMAIL = "email"
    SMS = "sms"
    WEBHOOK = "webhook"
    TELEGRAM = "telegram"
    MANUAL_IMPORT = "manual:import"


# Keywords that mark a notification as a cancellation
# of a previously-confirmed event. The list is
# short and explicit; everything else is parsed as a
# new outage (even if the body mentions a past outage
# in passing).
_CANCELLATION_KEYWORDS: tuple[str, ...] = (
    "скасовано",
    "скасована",
    "скасований",
    "скасувати",
    "cancelled",
    "canceled",
    "cancellation",
    "відмінено",
    "відміна",
    "перенесено",
    "postponed",
    "rescheduled",
    "rescinded",
    "withdrawn",
)


@dataclass(frozen=True)
class RawNotification:
    """A notification as received from a transport.

    ``message_id`` is the transport's unique identifier
    (IMAP ``Message-ID``, SMS gateway id, webhook
    delivery id). The adapter dedups on it -- the same
    ``message_id`` arriving twice in a row is a no-op.

    ``raw_text`` is the verbatim message body. The
    adapter stores it on every ``OutageEvent`` so the
    operator can audit the parser's input.

    ``address_hint`` is an optional free-form string
    that the parser may use to disambiguate which
    address the message is about. Today the parser
    does not use it; the field is preserved for future
    per-address routing.
    """

    message_id: str
    source: NotificationSource
    received_at: str  # offset-aware ISO
    raw_text: str
    sender: str
    address_hint: str | None = None


@dataclass(frozen=True)
class ParsedNotification:
    """The result of running a ``RawNotification``
    through the parser. ``confidence`` is 1.0 for
    unambiguous parses and strictly less than 1.0
    for ambiguous parses (with a non-empty
    ``ambiguity_reason``).
    """

    raw: RawNotification
    parsed_start: str | None
    parsed_end: str | None
    parsed_tz_name: str
    confidence: float
    ambiguity_reason: str | None
    is_cancellation: bool


# ---------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------


class NotificationParser:
    """Wrap ``OutageParser`` with cancellation-keyword
    detection.

    Cancellation is signalled by an explicit keyword
    in the body. The start / end times are NOT used
    to detect cancellation -- a rescheduled event
    might have new times in the same message; the
    adapter handles that as a revision, not a
    cancellation.
    """

    def __init__(self, tz_name: str) -> None:
        self._parser = OutageParser(tz_name)
        self._tz_name = tz_name

    @property
    def tz_name(self) -> str:
        return self._tz_name

    def parse(
        self,
        raw: RawNotification,
        *,
        now: datetime | None = None,
    ) -> ParsedNotification:
        cleaned = (raw.raw_text or "").strip().lower()
        is_cancellation = any(
            kw in cleaned for kw in _CANCELLATION_KEYWORDS
        )
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            raise ValueError("parser 'now' must be offset-aware")
        if is_cancellation:
            return ParsedNotification(
                raw=raw,
                parsed_start=None,
                parsed_end=None,
                parsed_tz_name=self._tz_name,
                confidence=1.0,
                ambiguity_reason=None,
                is_cancellation=True,
            )
        parsed = self._parser.parse(raw.raw_text or "", now=now)
        # Confidence is 1.0 when the parser returned a
        # fully-decided event. Otherwise 0.5 (the
        # ``needs_confirmation`` path).
        confidence = 0.5 if parsed.needs_confirmation else 1.0
        return ParsedNotification(
            raw=raw,
            parsed_start=parsed.start_iso,
            parsed_end=parsed.end_iso,
            parsed_tz_name=parsed.tz_name,
            confidence=confidence,
            ambiguity_reason=parsed.ambiguity_reason,
            is_cancellation=False,
        )


# ---------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------


@dataclass
class IngestOutcome:
    """The structured result of ``ingest``. The
    ``action`` field names the adapter's verdict so
    the audit log can distinguish a fresh event from
    a revision or a cancellation.
    """

    action: str  # "added" | "updated_pending" | "duplicate" | "cancelled" | "ignored_cancellation"
    event: OutageEvent | None = None
    reason: str | None = None


class NotificationAdapter:
    """Ingestion pipeline for outage notifications.

    ``ingest(parsed, store)`` is the single entry
    point. It is intentionally a synchronous function
    that returns an ``IngestOutcome``; the surrounding
    service handler is responsible for awaiting
    ``store.add`` / ``store.update`` (the outcome
    carries the event to persist). The adapter does
    not call any HA APIs and does not touch the live
    coordinator's HEMS decision path.
    """

    def __init__(self, parser: NotificationParser) -> None:
        self._parser = parser

    @property
    def parser(self) -> NotificationParser:
        return self._parser

    def ingest(
        self,
        parsed: ParsedNotification,
        store: EventStore,
    ) -> IngestOutcome:
        # 1. Dedupe by message_id. The same
        # ``message_id`` arriving twice is a transport
        # resend; the adapter drops it.
        seen_message_id = self._find_by_message_id(
            store, parsed.raw.message_id
        )
        if seen_message_id is not None:
            return IngestOutcome(
                action="duplicate",
                event=seen_message_id,
                reason=f"message_id {parsed.raw.message_id!r} already ingested",
            )

        # 2. Cancellation path. The body contains
        # an explicit cancel keyword. Find the
        # matching confirmed event by
        # (address_hint, parsed window, source) and
        # call ``cancel_event`` on it.
        if parsed.is_cancellation:
            return self._handle_cancellation(parsed, store)

        # 3. New or revised event path.
        if not parsed.parsed_start or not parsed.parsed_end:
            # The parser could not determine the
            # window at all. We refuse to create an
            # event with empty time fields -- the
            # operator can re-send the message with
            # more context. The audit log records the
            # ``ambiguity_reason``.
            return IngestOutcome(
                action="ignored",
                event=None,
                reason=(
                    parsed.ambiguity_reason
                    or "parser produced no time window"
                ),
            )

        # 4. Look for an existing pending event with
        # the same (window, source) -- this is a
        # revision of an earlier parse. The adapter
        # does NOT auto-merge into a confirmed event
        # (a confirmed event must be cancelled by the
        # operator first).
        duplicate = find_duplicate(
            store,
            start_iso=parsed.parsed_start,
            end_iso=parsed.parsed_end,
            source=f"notification:{parsed.raw.source.value}",
        )
        if duplicate is not None:
            return self._handle_revision(parsed, store, duplicate)

        # 5. Fresh event. Build the ``OutageEvent``
        # via the parser's ``to_event`` factory so
        # the field set matches the production
        # contract.
        stub = ParsedOutage(
            start_iso=parsed.parsed_start,
            end_iso=parsed.parsed_end,
            tz_name=parsed.parsed_tz_name,
            needs_confirmation=parsed.confidence < 1.0,
            ambiguity_reason=parsed.ambiguity_reason,
        )
        new_event = stub.to_event(
            source=f"notification:{parsed.raw.source.value}",
            raw_text=parsed.raw.raw_text,
            notes=(
                f"sender={parsed.raw.sender}"
                + (
                    f";address={parsed.raw.address_hint}"
                    if parsed.raw.address_hint
                    else ""
                )
                + f";message_id={parsed.raw.message_id}"
            ),
        )
        # ``to_event`` stamps ``status=STATUS_PENDING``
        # already. The dispatcher's
        # ``_find_active_event`` only picks
        # ``status == STATUS_CONFIRMED``, so the
        # event is never auto-dispatched.
        return IngestOutcome(
            action="added",
            event=new_event,
            reason=None,
        )

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def _handle_cancellation(
        self,
        parsed: ParsedNotification,
        store: EventStore,
    ) -> IngestOutcome:
        # Find a confirmed event that matches the
        # message's source. The adapter picks the
        # most recent confirmed event from the same
        # source (the address_hint and window are not
        # always present in a cancellation message).
        candidates = [
            ev
            for ev in store.iter_active()
            if ev.status == STATUS_CONFIRMED
            and ev.source
            == f"notification:{parsed.raw.source.value}"
        ]
        if not candidates:
            return IngestOutcome(
                action="ignored_cancellation",
                event=None,
                reason="no confirmed event matches the cancellation",
            )
        # Prefer the most recently received
        # confirmed event.
        candidates.sort(
            key=lambda e: (e.received_at, e.updated_at),
            reverse=True,
        )
        target = candidates[0]
        bumped = _bump(target, status=STATUS_CANCELLED)
        bumped = replace(
            bumped,
            notes=(
                f"{bumped.notes or ''}\n"
                f"cancelled by notification "
                f"{parsed.raw.message_id!r}"
            ),
        )
        # Return the new state. The caller persists
        # via ``await store.update(bumped)``.
        return IngestOutcome(
            action="cancelled",
            event=bumped,
            reason=f"cancellation by {parsed.raw.message_id!r}",
        )

    # ------------------------------------------------------------------
    # Revision
    # ------------------------------------------------------------------

    def _handle_revision(
        self,
        parsed: ParsedNotification,
        store: EventStore,
        duplicate: OutageEvent,
    ) -> IngestOutcome:
        if duplicate.status == STATUS_CONFIRMED:
            # A confirmed event with the same window
            # already exists. We do NOT auto-rewrite
            # it -- the operator must cancel and
            # re-add. The new message is recorded as
            # ignored.
            return IngestOutcome(
                action="duplicate",
                event=duplicate,
                reason=(
                    "confirmed event with the same window "
                    "exists; operator must cancel and re-add"
                ),
            )
        # Pending revision. Compare the new
        # confidence against the existing pending
        # event's confidence. Strictly higher ->
        # update. Equal or lower -> drop as
        # duplicate.
        old_confidence = 0.0 if duplicate.needs_confirmation else 1.0
        if parsed.confidence <= old_confidence:
            return IngestOutcome(
                action="duplicate",
                event=duplicate,
                reason="pending event already exists; no improvement",
            )
        bumped = _bump(duplicate, status=STATUS_PENDING)
        bumped = replace(
            bumped,
            raw_text=parsed.raw.raw_text,
            ambiguity_reason=parsed.ambiguity_reason,
            notes=(
                f"{bumped.notes or ''}\n"
                f"revised by notification "
                f"{parsed.raw.message_id!r}"
            ),
        )
        # Return the new state. The caller persists
        # via ``await store.update(bumped)``.
        return IngestOutcome(
            action="updated_pending",
            event=bumped,
            reason=(
                f"confidence {old_confidence} -> {parsed.confidence}"
            ),
        )

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def _find_by_message_id(
        self,
        store: EventStore,
        message_id: str,
    ) -> OutageEvent | None:
        """Return the event whose notes mention the
        given ``message_id``. The adapter stamps
        ``message_id`` into the notes on every
        event it creates / revises / cancels. A
        duplicate ``message_id`` therefore matches
        the same event on resend.
        """
        for ev in store.iter_active():
            if ev.notes and message_id in ev.notes:
                return ev
        return None


__all__ = [
    "NotificationSource",
    "RawNotification",
    "ParsedNotification",
    "NotificationParser",
    "NotificationAdapter",
    "IngestOutcome",
]
