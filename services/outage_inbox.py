"""Phase 4 (continued): notification inbox + adapter.

The Phase 4 v1 (services/outage_notifications.py) shipped
an adapter that picked "the most recent confirmed event
of the same source" for cancellation. Codex / the
operator flagged that as an anti-pattern: cancellation
must be UNAMBIGUOUSLY matched to a specific event by
``original_message_id`` or by the triple
``(address_hint, start, end)`` -- never by recency.

This module ships the Phase 4 v2 pipeline:

  1. ``InboxEntry`` + ``InboxStore``: a persistent
     audit log of every received message, keyed by
     the structured key ``source:channel:message_id``.
     Survives across restart (a redelivery of the same
     message after reload is a no-op).

  2. ``NotificationAdapterV2`` (and the underlying
     ``NotificationParserV2`` which adds the
     ``original_message_id`` field for cancellation
     links): the v2 ingestion pipeline. Cancellation
     MUST resolve to exactly one event; ambiguity
     produces ``needs_operator_review`` with no
     mutation.

  3. Reschedule handling: a fresh parse with the
     same ``original_message_id`` as a stored event
     but a different window is a CORRECTION. The old
     event is kept in the store with
     ``superseded_by`` pointing at the new event id;
     the new event's notes record the supersession
     link. Both events live in the store (history is
     preserved).

  4. Idempotency: every ``ingest`` writes the inbox
     entry FIRST (upsert by ``source:channel:message_id``).
     A redelivery returns the original outcome. A
     reload followed by an ingest of the same key is
     a no-op.

The module is pure-Python. The surrounding service
handler (``async_ingest_outage_notification`` in
services/outage.py) is responsible for awaiting
``store.async_save`` and ``InboxStore.async_save``.

A real notification source (IMAP / SMS / webhook) is
NOT wired here -- Phase 4 v2 only ships the ingestion
contract. The operator calls
``powmr_inverter.ingest_outage_notification`` with a
``RawNotification`` and the adapter handles the rest.
A specific transport (IMAP IDLE, Telegram bot, etc.)
is a separate round.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, List

from zoneinfo import ZoneInfo

from .outage import (
    EventStore,
    OutageEvent,
    OutageParser,
    ParsedOutage,
    RescheduleSnapshot,
    STATUS_CANCELLED,
    STATUS_CONFIRMED,
    STATUS_PENDING,
    _bump,
    _now_iso_utc,
    _validate_tz_name,
    find_duplicate,
)


# ---------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------


INBOX_STORE_VERSION = 1


class InboxAction(str, Enum):
    """The structured action the adapter took for a
    given inbox entry. Recorded in the inbox so a
    reload can replay the audit log.
    """

    ADDED = "added"
    UPDATED_PENDING = "updated_pending"
    DUPLICATE_MESSAGE = "duplicate_message"
    DUPLICATE_EVENT = "duplicate_event"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    NEEDS_OPERATOR_REVIEW = "needs_operator_review"
    IGNORED = "ignored"
    UNMATCHED_CANCELLATION = "unmatched_cancellation"


@dataclass(frozen=True)
class InboxKey:
    """Structured key for the inbox.

    ``source`` is the transport family (email, sms,
    webhook, telegram, manual:import). ``channel`` is
    the optional logical channel within a source
    (the operator's email address, the SMS gateway
    number, the webhook URL). The combination
    ``(source, channel, message_id)`` is unique.
    """

    source: str
    channel: str
    message_id: str

    def as_storage_key(self) -> str:
        return f"{self.source}|{self.channel}|{self.message_id}"

    @classmethod
    def from_storage_key(cls, key: str) -> "InboxKey":
        parts = key.split("|", 2)
        if len(parts) != 3:
            raise ValueError(f"invalid inbox key {key!r}")
        return cls(source=parts[0], channel=parts[1], message_id=parts[2])


@dataclass
class InboxEntry:
    """Audit log of a single received notification.

    The inbox is the canonical record of every message
    the adapter saw. The fields are stable across
    restarts and are the source of truth for the
    operator's audit.
    """

    key: str  # serialised InboxKey
    source: str
    channel: str
    message_id: str
    received_at: str  # offset-aware ISO
    raw_text: str
    sender: str
    address_hint: str | None
    action: str
    event_id: str | None
    reason: str | None
    # Free-form dict from the parser (e.g. parsed_start
    # / parsed_end / parsed_tz / confidence /
    # ambiguity_reason / is_cancellation). Persisted as
    # ``parsed`` so the operator can replay the parse
    # result without re-running the parser.
    parsed: dict[str, Any] = field(default_factory=dict)
    # For cancellation / supersession: the id of the
    # event the action targeted. ``None`` if the
    # action was a no-op or a fresh add.
    target_event_id: str | None = None
    # For supersession: the id of the new event that
    # replaces the target. ``None`` otherwise.
    replacement_event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InboxEntry":
        return cls(**data)


class _InMemoryInboxStorage:
    """In-memory stand-in for ``homeassistant.helpers.storage.Store``.

    Mirrors the production ``Store.async_load`` /
    ``async_save`` contract so the inbox tests run
    without HA.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] | None = None

    async def async_load(self) -> dict[str, Any] | None:
        return self._data

    async def async_save(self, data: dict[str, Any]) -> None:
        # Deep copy on save so the test's in-memory
        # mutation does not bleed into the persisted
        # snapshot mid-test.
        import copy
        self._data = copy.deepcopy(data)


class InboxStore:
    """Persistent store for ``InboxEntry`` records.

    Keyed by ``InboxKey.as_storage_key()`` so the
    (source, channel, message_id) triple is unique. A
    redelivery of the same key after restart is a
    no-op (the existing entry is returned).

    The store accepts any object that exposes
    ``async_load() -> dict | None`` and
    ``async_save(dict) -> None``. The production path
    constructs an HA ``Store``; the unit-test path
    uses ``_InMemoryInboxStorage`` so tests do not
    need HA.
    """

    def __init__(self, storage: Any, entry_id: str) -> None:
        if not entry_id:
            raise ValueError("entry_id is required for the inbox store")
        self._storage = storage
        self._entry_id = entry_id
        self._entries: dict[str, InboxEntry] = {}

    @classmethod
    def in_memory(cls, entry_id: str = "test-entry") -> "InboxStore":
        return cls(_InMemoryInboxStorage(), entry_id)

    @property
    def entry_id(self) -> str:
        return self._entry_id

    async def async_load(self) -> None:
        data = await self._storage.async_load()
        if data is None:
            self._entries = {}
            return
        if not isinstance(data, dict):
            raise ValueError(
                f"inbox store {self._entry_id!r} payload must be a dict"
            )
        stored_version = data.get("__version__")
        if stored_version is not None and stored_version != INBOX_STORE_VERSION:
            raise ValueError(
                f"inbox store {self._entry_id!r} has version "
                f"{stored_version!r}; expected {INBOX_STORE_VERSION}. "
                "Migration is not implemented yet; refusing to load."
            )
        entries_raw = data.get("entries", {})
        if not isinstance(entries_raw, dict):
            raise ValueError(
                f"inbox store {self._entry_id!r} 'entries' must be a dict"
            )
        loaded: dict[str, InboxEntry] = {}
        for key, payload in entries_raw.items():
            loaded[key] = InboxEntry.from_dict(payload)
        self._entries = loaded

    async def async_save(self) -> None:
        payload: dict[str, Any] = {
            "__version__": INBOX_STORE_VERSION,
            "entries": {
                key: entry.to_dict()
                for key, entry in self._entries.items()
            },
        }
        await self._storage.async_save(payload)

    def get(self, key: InboxKey) -> InboxEntry | None:
        return self._entries.get(key.as_storage_key())

    def has(self, key: InboxKey) -> bool:
        return self.get(key) is not None

    async def upsert(self, entry: InboxEntry) -> InboxEntry:
        """Insert or replace an entry by its key. The
        caller is responsible for filling ``action``,
        ``event_id``, ``reason``, etc.
        """
        self._entries[entry.key] = entry
        await self.async_save()
        return entry

    def list(self) -> list[InboxEntry]:
        return list(self._entries.values())


# ---------------------------------------------------------------------
# v2 parser
# ---------------------------------------------------------------------


class NotificationSourceV2(str, Enum):
    """Source of a notification. The actual transport
    is out of scope -- the adapter accepts a
    structured ``RawNotificationV2`` from any internal
    service handler.
    """

    EMAIL = "email"
    SMS = "sms"
    WEBHOOK = "webhook"
    TELEGRAM = "telegram"
    MANUAL_IMPORT = "manual:import"


@dataclass(frozen=True)
class RawNotificationV2:
    """A notification as received from a transport.

    ``key`` is the structured (source, channel,
    message_id) triple. ``original_message_id`` is
    set on a CANCELLATION message and points at the
    ``message_id`` of the original notification that
    announced the event. The adapter uses
    ``original_message_id`` to disambiguate the
    cancellation target -- never "the most recent
    confirmed event" (Codex / operator request).
    """

    source: NotificationSourceV2
    channel: str  # free-form; default "" if absent
    message_id: str
    received_at: str
    raw_text: str
    sender: str
    address_hint: str | None = None
    original_message_id: str | None = None

    def key(self) -> InboxKey:
        return InboxKey(
            source=self.source.value,
            channel=self.channel,
            message_id=self.message_id,
        )


@dataclass(frozen=True)
class ParsedNotificationV2:
    """The result of running a ``RawNotificationV2``
    through the parser. ``confidence`` is 1.0 for
    unambiguous parses and 0.5 for ambiguous parses
    (with a non-empty ``ambiguity_reason``).
    """

    raw: RawNotificationV2
    parsed_start: str | None
    parsed_end: str | None
    parsed_tz_name: str
    confidence: float
    ambiguity_reason: str | None
    is_cancellation: bool
    # ``original_message_id`` is non-None on
    # cancellation messages that carry an explicit
    # link to the original notification.
    original_message_id: str | None = None


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


class NotificationParserV2:
    """Wrap ``OutageParser`` with cancellation-keyword
    detection and ``original_message_id`` extraction.

    The cancellation message's body is expected to
    contain the original ``message_id`` (the
    transport's unique id of the original
    notification). The parser extracts it via a
    simple regex: ``original: <id>`` or
    ``message-id: <id>``. If the body has neither, the
    cancellation falls back to address+window matching
    in the adapter.
    """

    def __init__(self, tz_name: str) -> None:
        self._parser = OutageParser(tz_name)
        self._tz_name = tz_name

    @property
    def tz_name(self) -> str:
        return self._tz_name

    def parse(
        self,
        raw: RawNotificationV2,
        *,
        now: datetime | None = None,
    ) -> ParsedNotificationV2:
        cleaned = (raw.raw_text or "").strip().lower()
        is_cancellation = any(
            kw in cleaned for kw in _CANCELLATION_KEYWORDS
        )
        # The original_message_id may be supplied by
        # the caller (preferred -- the service handler
        # forwards it from the structured service
        # call) OR extracted from the body. The body's
        # extraction is best-effort: look for
        # ``original:`` / ``original-id:`` / ``message-id:``
        # tokens.
        original_id = raw.original_message_id
        if original_id is None and is_cancellation:
            original_id = self._extract_original_id(raw.raw_text or "")
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            raise ValueError("parser 'now' must be offset-aware")
        if is_cancellation:
            return ParsedNotificationV2(
                raw=raw,
                parsed_start=None,
                parsed_end=None,
                parsed_tz_name=self._tz_name,
                confidence=1.0,
                ambiguity_reason=None,
                is_cancellation=True,
                original_message_id=original_id,
            )
        parsed = self._parser.parse(raw.raw_text or "", now=now)
        confidence = 0.5 if parsed.needs_confirmation else 1.0
        return ParsedNotificationV2(
            raw=raw,
            parsed_start=parsed.start_iso,
            parsed_end=parsed.end_iso,
            parsed_tz_name=parsed.tz_name,
            confidence=confidence,
            ambiguity_reason=parsed.ambiguity_reason,
            is_cancellation=False,
            original_message_id=None,
        )

    @staticmethod
    def _extract_original_id(text: str) -> str | None:
        import re
        # Allow `original: ID`, `original-id: ID`, `message-id: ID`.
        for pattern in (
            r"original[-_ ]id\s*[:=]\s*([\w.\-]+)",
            r"message[-_ ]id\s*[:=]\s*([\w.\-]+)",
        ):
            m = re.search(pattern, text, flags=re.IGNORECASE)
            if m:
                return m.group(1)
        return None


# ---------------------------------------------------------------------
# v2 adapter
# ---------------------------------------------------------------------


@dataclass
class IngestOutcomeV2:
    """The structured result of ``ingest``. The
    ``action`` field is one of the ``InboxAction``
    values; ``needs_operator_review`` means the
    adapter could not decide autonomously and the
    operator must inspect the inbox.
    """

    action: str
    event: OutageEvent | None = None
    reason: str | None = None
    inbox_entry: InboxEntry | None = None
    # For cancellation / supersession: the original
    # event that was affected (if any). ``None`` for
    # fresh adds and pure dedup.
    target_event: OutageEvent | None = None
    # For supersession: the new event that REPLACES
    # the target.
    replacement_event: OutageEvent | None = None
    # Atomic reschedule snapshot. When the
    # adapter decides a supersession, both
    # events are bundled here so the handler
    # can persist them in ONE ``async_save``
    # call. The handler MUST call
    # ``event_store.apply_reschedule_snapshot``
    # (not separate ``add`` + ``update``).
    snapshot: Any = None


def _audit_notes(
    raw: RawNotificationV2,
    *,
    address_hint: str | None = None,
    extra: list[str] | None = None,
) -> str:
    parts: list[str] = []
    if raw.sender:
        parts.append(f"sender={raw.sender}")
    if address_hint is not None:
        parts.append(f"address={address_hint}")
    else:
        parts.append(f"address={raw.address_hint or ''}")
    parts.append(f"message_id={raw.message_id}")
    parts.append(f"source={raw.source.value}:{raw.channel}")
    if extra:
        parts.extend(extra)
    return ";".join(parts)


class NotificationAdapterV2:
    """v2 ingestion pipeline.

    ``ingest(parsed, event_store, inbox)`` is the
    single entry point. The adapter is synchronous;
    the surrounding service handler is responsible
    for awaiting ``event_store.async_save`` and
    ``inbox.async_save`` after the outcome is
    produced. The adapter does not call any HA APIs
    and does not touch the live coordinator's HEMS
    decision path.

    Cancellation matching policy (Codex / operator
    request):

      * The cancellation message carries
        ``original_message_id`` (either supplied by
        the service handler or extracted from the
        body). The adapter finds the corresponding
        event in the store by looking up the inbox
        key for the original notification. If exactly
        one match is found, that event is cancelled.
      * If ``original_message_id`` is absent, the
        adapter falls back to address+window
        matching against the event store. Exactly
        one match -> cancel that event. Zero or more
        than one match -> ``needs_operator_review``,
        no event is mutated. The inbox records the
        cancellation as ``unmatched_cancellation``
        so the operator can inspect the audit log.

    Reschedule policy:

      * A fresh non-cancellation parse with the same
        ``original_message_id`` as a stored event
        (or, absent that, the same ``(address,
        start, end)`` triple) is a CORRECTION. The
        old event is kept; its ``superseded_by``
        field is set to the new event id. The new
        event's notes record the supersession link.
        Both events live in the store for audit.

    Idempotency:

      * The inbox is the canonical record. Every
        ``ingest`` writes the inbox entry first
        (upsert by ``InboxKey``). A redelivery of
        the same (source, channel, message_id)
        returns the original outcome from the
        inbox; no second event is created, no
        existing event is re-mutated.

    The adapter never auto-confirms. The
    ``_find_active_event`` runtime filter
    (services/outage_runtime.py) only picks
    ``status == 'confirmed'`` events, so a pending
    event produced here is never auto-dispatched.
    """

    def __init__(self, parser: NotificationParserV2) -> None:
        self._parser = parser

    @property
    def parser(self) -> NotificationParserV2:
        return self._parser

    def parse(
        self,
        raw: RawNotificationV2,
        *,
        now: datetime | None = None,
    ) -> ParsedNotificationV2:
        return self._parser.parse(raw, now=now)

    async def ingest(
        self,
        parsed: ParsedNotificationV2,
        event_store: EventStore,
        inbox: InboxStore,
    ) -> IngestOutcomeV2:
        """Decide + apply atomic commit.

        Two-phase: ``self.decide()`` computes the
        outcome WITHOUT writing the inbox; the
        caller (``services/outage.py`` service
        handler) applies the event mutations and
        then calls ``self.commit()`` to persist
        the inbox. This guarantees that the inbox
        never says ``added/cancelled/superseded``
        without the corresponding event state on
        disk. On error between event mutation and
        inbox commit the redelivery is allowed
        (the inbox has no entry yet).
        """
        outcome = await self.decide(parsed, event_store, inbox)
        if outcome.inbox_entry is not None:
            await self.commit(outcome, inbox)
        return outcome

    async def decide(
        self,
        parsed: ParsedNotificationV2,
        event_store: EventStore,
        inbox: InboxStore,
    ) -> IngestOutcomeV2:
        """Compute the outcome without writing the
        inbox. The handler is responsible for
        applying the event mutations
        (``event_store.add`` / ``event_store.update``)
        and calling ``self.commit`` as the LAST
        step.
        """
        raw = parsed.raw
        key = raw.key()

        # 1. Idempotency. If the inbox already has an
        # entry for this (source, channel, message_id)
        # key, return the original outcome. No event
        # mutation, no second event created.
        existing = inbox.get(key)
        if existing is not None:
            target = (
                event_store.get(existing.event_id)
                if existing.event_id
                else None
            )
            return IngestOutcomeV2(
                action=existing.action,
                event=target,
                reason=(
                    f"inbox already processed "
                    f"{key.as_storage_key()!r}: {existing.reason}"
                ),
                inbox_entry=None,  # already on disk
                target_event=target,
            )

        # 2. Build the inbox entry shell. The action
        # / event_id / reason are filled by the
        # branches below; the inbox is the
        # canonical record of the message.
        inbox_entry = InboxEntry(
            key=key.as_storage_key(),
            source=raw.source.value,
            channel=raw.channel,
            message_id=raw.message_id,
            received_at=raw.received_at,
            raw_text=raw.raw_text,
            sender=raw.sender,
            address_hint=raw.address_hint,
            action=InboxAction.IGNORED.value,
            event_id=None,
            reason=None,
            parsed={
                "parsed_start": parsed.parsed_start,
                "parsed_end": parsed.parsed_end,
                "parsed_tz_name": parsed.parsed_tz_name,
                "confidence": parsed.confidence,
                "ambiguity_reason": parsed.ambiguity_reason,
                "is_cancellation": parsed.is_cancellation,
                "original_message_id": parsed.original_message_id,
            },
        )

        # 3. Cancellation path.
        if parsed.is_cancellation:
            return self._decide_cancellation(
                parsed, event_store, inbox, inbox_entry
            )

        # 4. The new event path requires both start
        # and end. The parser must have produced a
        # concrete window; otherwise the message is
        # recorded as ``ignored`` for operator
        # review.
        if not parsed.parsed_start or not parsed.parsed_end:
            inbox_entry.action = InboxAction.IGNORED.value
            inbox_entry.reason = (
                parsed.ambiguity_reason
                or "parser produced no time window"
            )
            return IngestOutcomeV2(
                action=inbox_entry.action,
                event=None,
                reason=inbox_entry.reason,
                inbox_entry=inbox_entry,
            )

        # 5. Reschedule / revision path. Look for an
        # event that explicitly links to the
        # original_message_id of this notification
        # (the operator passed it through the service
        # call). If found, treat as a reschedule --
        # regardless of whether the new window
        # overlaps the old one. A reschedule
        # produces a new event and sets
        # ``superseded_by`` on the old one. (The
        # standard ``find_duplicate`` overlap check
        # is bypassed for reschedules: a reschedule
        # may move the window so it overlaps the old
        # one, but the operator still wants the
        # correction to take effect.)
        source_tag = f"notification:{raw.source.value}"
        reschedule_target: OutageEvent | None = None
        if raw.original_message_id:
            reschedule_target = self._find_event_by_original_message_id(
                event_store,
                inbox,
                raw.original_message_id,
                source=raw.source.value,
                channel=raw.channel,
            )

        # 6. Standard dedup. If an active event
        # overlaps the new window AND shares the
        # source, the adapter treats the new message
        # as a revision.
        if reschedule_target is None:
            duplicate = find_duplicate(
                event_store,
                start_iso=parsed.parsed_start,
                end_iso=parsed.parsed_end,
                source=source_tag,
            )
        else:
            duplicate = None
        if reschedule_target is not None:
            # A reschedule is a fresh event that
            # supersedes the old one. Both live in
            # the store; the old one gets
            # ``superseded_by`` set; the new one
            # records the link in notes.
            #
            # Replay vs. new correction: a
            # redelivery of the CURRENT message is
            # already filtered by the
            # ``inbox.get(current_key)`` check at
            # the top of ``decide``. If we are
            # here, the current ``message_id`` is
            # NOT in the inbox, so this is a NEW
            # correction -- even when the OLD has
            # been superseded by a prior correction
            # (chain of reschedules). The previous
            # ``target.superseded_by != None``
            # check is REMOVED: it conflated
            # "this reschedule was already
            # processed" with "the target has been
            # superseded (perhaps by a different
            # prior correction)" and incorrectly
            # suppressed new corrections.
            new_event = self._build_new_event(parsed)
            new_event = replace(
                new_event,
                notes=_audit_notes(
                    raw,
                    address_hint=raw.address_hint,
                    extra=[
                        f"supersedes={reschedule_target.id}",
                        f"original_message_id={raw.original_message_id}",
                    ],
                ),
            )
            bumped_old = _bump(
                reschedule_target, status=reschedule_target.status
            )
            bumped_old = replace(
                bumped_old,
                superseded_by=new_event.id,
            )
            # Build the atomic snapshot. The
            # handler persists old + new in a
            # single ``async_save`` so a partial
            # save never leaves a dangling
            # ``superseded_by``.
            snapshot = RescheduleSnapshot(
                old_event=bumped_old,
                new_event=new_event,
            )
            # Record the inbox as SUPERSEDED. The
            # handler applies ``target_event.update``
            # AND ``event_store.add(new_event)``
            # before the commit; the inbox is the
            # LAST write.
            inbox_entry.action = InboxAction.SUPERSEDED.value
            inbox_entry.event_id = new_event.id
            inbox_entry.reason = (
                f"reschedule of {reschedule_target.id} "
                f"(original_message_id={raw.original_message_id})"
            )
            inbox_entry.target_event_id = reschedule_target.id
            inbox_entry.replacement_event_id = new_event.id
            return IngestOutcomeV2(
                action=inbox_entry.action,
                event=new_event,
                reason=inbox_entry.reason,
                inbox_entry=inbox_entry,
                target_event=bumped_old,
                replacement_event=new_event,
                snapshot=snapshot,
            )

        if duplicate is not None:
            return self._decide_revision(
                parsed, inbox_entry, duplicate
            )

        # 7. Fresh event. The adapter returns the
        # new event; the caller persists it via
        # ``await event_store.add(new_event)`` and
        # then commits the inbox.
        new_event = self._build_new_event(parsed)
        inbox_entry.action = InboxAction.ADDED.value
        inbox_entry.event_id = new_event.id
        inbox_entry.reason = "fresh event from notification"
        inbox_entry.replacement_event_id = None
        return IngestOutcomeV2(
            action=inbox_entry.action,
            event=new_event,
            reason=inbox_entry.reason,
            inbox_entry=inbox_entry,
        )

    async def commit(
        self,
        outcome: IngestOutcomeV2,
        inbox: InboxStore,
    ) -> IngestOutcomeV2:
        """Persist the inbox entry. Called as the
        LAST step by the handler AFTER the event
        mutations have been applied and persisted.

        Idempotent: a second call with the same
        ``inbox_entry.key`` overwrites the entry
        with the same content (the action /
        event_id / reason are already final).
        """
        if outcome.inbox_entry is None:
            # ``decide`` returned an idempotency
            # replay (the entry is already on disk).
            return outcome
        await inbox.upsert(outcome.inbox_entry)
        return outcome

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def _decide_cancellation(
        self,
        parsed: ParsedNotificationV2,
        event_store: EventStore,
        inbox: InboxStore,
        inbox_entry: InboxEntry,
    ) -> IngestOutcomeV2:
        raw = parsed.raw
        source_tag = f"notification:{raw.source.value}"
        target: OutageEvent | None = None
        match_count = 0

        # 1. Primary match: ``original_message_id``.
        # The adapter looks up the original
        # notification's inbox entry and uses its
        # ``event_id`` to find the original event.
        # A successful primary match is FINAL --
        # the address+window fallback must NOT
        # run and must NOT overwrite the target
        # (the structured key is exact; the
        # fallback is for messages that do NOT
        # carry ``original_message_id`` at all).
        primary_target: OutageEvent | None = None
        if parsed.original_message_id:
            primary_target = (
                self._find_event_by_original_message_id(
                    event_store,
                    inbox,
                    parsed.original_message_id,
                    source=raw.source.value,
                    channel=raw.channel,
                )
            )
            if primary_target is not None:
                target = primary_target
                match_count = 1

        # 2. Fallback: address+window match. The
        # adapter only accepts a cancellation if
        # EXACTLY one event matches the address and
        # the time window. The fallback runs ONLY
        # when the primary lookup did not resolve
        # a target; if the primary already found
        # one, the address+window pass is skipped
        # so the existing target is preserved.
        if target is None and raw.address_hint:
            # The cancellation may or may not carry
            # a parseable window. With a window the
            # adapter narrows the candidates to
            # those that OVERLAP; without a window
            # the candidates are every event with
            # this ``address_hint``. In both cases
            # the count must be exactly 1 for the
            # cancellation to be unambiguous.
            candidates, _count = (
                self._find_event_by_address_window(
                    event_store,
                    address_hint=raw.address_hint,
                    start_iso=parsed.parsed_start or "",
                    end_iso=parsed.parsed_end or "",
                    source_tag=source_tag,
                )
            )
            if len(candidates) == 1:
                target = candidates[0]
                match_count = 1
            else:
                match_count = len(candidates)

        if target is None or match_count != 1:
            # Ambiguous cancellation. The operator
            # must inspect the inbox and resolve by
            # hand. NO event is mutated.
            # ``unmatched_cancellation`` (count == 0)
            # is different from ``needs_operator_review``
            # (count > 1): the latter means we found
            # candidates but they were too many to
            # decide; the former means there were no
            # candidates at all.
            inbox_entry.action = (
                InboxAction.NEEDS_OPERATOR_REVIEW.value
                if match_count > 0
                else InboxAction.UNMATCHED_CANCELLATION.value
            )
            if match_count == 0:
                inbox_entry.reason = (
                    f"cancellation has no resolvable target "
                    f"(original_message_id="
                    f"{parsed.original_message_id!r}, "
                    f"address_hint={raw.address_hint!r}, "
                    f"window="
                    f"({parsed.parsed_start}, "
                    f"{parsed.parsed_end}))"
                )
            else:
                inbox_entry.reason = (
                    f"cancellation matches {match_count} events; "
                    f"operator must disambiguate"
                )
            inbox_entry.target_event_id = (
                target.id if target is not None else None
            )
            return IngestOutcomeV2(
                action=inbox_entry.action,
                event=target,
                reason=inbox_entry.reason,
                inbox_entry=inbox_entry,
                target_event=target,
            )

        # Unambiguous match. Cancel the event.
        bumped = _bump(target, status=STATUS_CANCELLED)
        bumped = replace(
            bumped,
            notes=(
                f"{bumped.notes or ''};"
                f"cancelled_by={raw.message_id}"
            ),
        )
        inbox_entry.action = InboxAction.CANCELLED.value
        inbox_entry.event_id = bumped.id
        inbox_entry.reason = (
            f"cancelled by {raw.message_id!r} "
            f"(original_message_id="
            f"{parsed.original_message_id!r})"
        )
        inbox_entry.target_event_id = target.id
        return IngestOutcomeV2(
            action=inbox_entry.action,
            event=bumped,
            reason=inbox_entry.reason,
            inbox_entry=inbox_entry,
            # The caller persists ``target_event`` as
            # the new state. Return the bumped state
            # (not the original ``target``) so the
            # update actually carries the
            # ``STATUS_CANCELLED`` flip.
            target_event=bumped,
        )

    # ------------------------------------------------------------------
    # Revision (higher-confidence re-parse)
    # ------------------------------------------------------------------

    def _decide_revision(
        self,
        parsed: ParsedNotificationV2,
        inbox_entry: InboxEntry,
        duplicate: OutageEvent,
    ) -> IngestOutcomeV2:
        raw = parsed.raw
        if duplicate.status == STATUS_CONFIRMED:
            # A confirmed event with the same
            # window already exists. The adapter
            # does NOT auto-rewrite it -- the
            # operator must cancel and re-add. The
            # new message is recorded as a
            # duplicate so the operator can inspect.
            inbox_entry.action = InboxAction.DUPLICATE_EVENT.value
            inbox_entry.event_id = duplicate.id
            inbox_entry.reason = (
                "confirmed event with the same window exists; "
                "operator must cancel and re-add"
            )
            inbox_entry.target_event_id = duplicate.id
            return IngestOutcomeV2(
                action=inbox_entry.action,
                event=duplicate,
                reason=inbox_entry.reason,
                inbox_entry=inbox_entry,
                target_event=duplicate,
            )
        # Pending revision. A strictly higher
        # confidence updates the event. Equal or
        # lower confidence is a duplicate.
        old_confidence = 0.0 if duplicate.needs_confirmation else 1.0
        if parsed.confidence <= old_confidence:
            inbox_entry.action = InboxAction.DUPLICATE_EVENT.value
            inbox_entry.event_id = duplicate.id
            inbox_entry.reason = (
                "pending event already exists; no "
                "confidence improvement"
            )
            inbox_entry.target_event_id = duplicate.id
            return IngestOutcomeV2(
                action=inbox_entry.action,
                event=duplicate,
                reason=inbox_entry.reason,
                inbox_entry=inbox_entry,
                target_event=duplicate,
            )
        # Update.
        bumped = _bump(duplicate, status=STATUS_PENDING)
        bumped = replace(
            bumped,
            raw_text=raw.raw_text,
            ambiguity_reason=parsed.ambiguity_reason,
            notes=(
                f"{bumped.notes or ''};"
                f"revised_by={raw.message_id}"
            ),
        )
        inbox_entry.action = InboxAction.UPDATED_PENDING.value
        inbox_entry.event_id = bumped.id
        inbox_entry.reason = (
            f"confidence {old_confidence} -> {parsed.confidence}"
        )
        inbox_entry.target_event_id = duplicate.id
        return IngestOutcomeV2(
            action=inbox_entry.action,
            event=bumped,
            reason=inbox_entry.reason,
            inbox_entry=inbox_entry,
            # The caller persists ``target_event`` as
            # the new state. Return the bumped state
            # so the update carries the new
            # ``raw_text`` / ``ambiguity_reason``.
            target_event=bumped,
        )

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def _build_new_event(
        self,
        parsed: ParsedNotificationV2,
    ) -> OutageEvent:
        raw = parsed.raw
        stub = ParsedOutage(
            start_iso=parsed.parsed_start,
            end_iso=parsed.parsed_end,
            tz_name=parsed.parsed_tz_name,
            needs_confirmation=parsed.confidence < 1.0,
            ambiguity_reason=parsed.ambiguity_reason,
        )
        return stub.to_event(
            source=f"notification:{raw.source.value}",
            raw_text=raw.raw_text,
            notes=_audit_notes(raw, address_hint=raw.address_hint),
        )

    def _find_event_by_original_message_id(
        self,
        event_store: EventStore,
        inbox: InboxStore,
        original_message_id: str,
        *,
        source: str,
        channel: str,
    ) -> OutageEvent | None:
        """Find the event that the original
        notification (with the given
        ``message_id``) created.

        Uses the **structured** InboxStore key
        ``(source, channel, message_id)`` to look
        up the original inbox entry, then resolves
        its ``event_id`` against the event store.
        This is more reliable than scanning
        ``event.notes`` for a substring: the
        structured key is part of the inbox's
        contract; notes are operator-mutable
        free-form.
        """
        key = InboxKey(
            source=source,
            channel=channel or "",
            message_id=original_message_id,
        )
        entry = inbox.get(key)
        if entry is None or not entry.event_id:
            return None
        return event_store.get(entry.event_id)

    def _find_event_by_address_window(
        self,
        event_store: EventStore,
        *,
        address_hint: str,
        start_iso: str,
        end_iso: str,
        source_tag: str,
    ) -> tuple[List[OutageEvent], int]:
        """Find events whose notes mention the
        ``address_hint`` and whose window OVERLAPS
        ``(start_iso, end_iso)``. When ``start_iso``
        or ``end_iso`` is empty (the cancellation
        message did not carry a parseable window),
        the match is by ``address_hint`` alone --
        every event whose notes carry the needle is
        a candidate. Returns the list of matches AND
        the count so the caller can distinguish
        "no target" from "ambiguous target".
        """
        from .outage import events_overlap
        matches: List[OutageEvent] = []
        needle = f"address={address_hint}"
        # The probe only exists when we have a
        # concrete window. When the cancellation has
        # no window we match by address_hint only.
        probe: OutageEvent | None = None
        if start_iso and end_iso:
            probe = OutageEvent(
                id="__probe__",
                source=source_tag,
                raw_text=None,
                start_iso=start_iso,
                end_iso=end_iso,
                tz_name="UTC",
                received_at="1970-01-01T00:00:00+00:00",
                status=STATUS_PENDING,
                needs_confirmation=False,
                ambiguity_reason=None,
                version=1,
                updated_at="1970-01-01T00:00:00+00:00",
                notes=f"address={address_hint}",
            )
        for ev in event_store.list_events():
            if ev.source != source_tag:
                continue
            if not ev.notes or needle not in ev.notes:
                continue
            if probe is not None and not events_overlap(probe, ev):
                continue
            matches.append(ev)
        return matches, len(matches)


__all__ = [
    "NotificationSourceV2",
    "RawNotificationV2",
    "ParsedNotificationV2",
    "NotificationParserV2",
    "NotificationAdapterV2",
    "IngestOutcomeV2",
    "InboxEntry",
    "InboxKey",
    "InboxAction",
    "InboxStore",
]
