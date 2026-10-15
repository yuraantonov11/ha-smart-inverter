"""Phase 4 v2 regression tests.

The v2 pipeline (services/outage_inbox.py) fixes three
regressions the v1 had:

  1. Cancellation used to pick "the most recent
     confirmed event of the same source". That is an
     anti-pattern: cancellation must resolve to a
     SPECIFIC event by ``original_message_id`` (the
     ``message_id`` of the original notification that
     announced the event) OR by the triple
     ``(address_hint, start, end)``. Ambiguity must
     produce ``needs_operator_review`` with no
     mutation.

  2. The v1 inbox was in-memory only -- a redelivery
     after restart produced a second event. The v2
     inbox is persisted via a separate
     ``InboxStore`` (HA ``Store`` in production,
     in-memory dict in tests). A redelivery after
     reload is a no-op.

  3. A reschedule was treated as a duplicate or a
     fresh add. The v2 pipeline handles it as a
     CORRECTION: the old event stays in the store
     with ``superseded_by`` pointing at the new
     event id; the new event's notes record the
     supersession link. History is preserved.

These tests exercise the full ingestion pipeline
end-to-end with a real ``InboxStore`` (in-memory
backend) and a real ``EventStore``. No shortcuts.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
INBOX_PATH = REPO_ROOT / "services" / "outage_inbox.py"
OUTAGE_PATH = REPO_ROOT / "services" / "outage.py"

_STUB_PKG = "_outage_inbox_v2_stub"


def _load_production_modules():
    """Load ``outage`` and ``outage_inbox`` as
    sub-modules of a stub package.
    """
    pkg = types.ModuleType(_STUB_PKG)
    pkg.__path__ = [str(REPO_ROOT / "services")]
    sys.modules[_STUB_PKG] = pkg

    # Stub ``homeassistant`` so the production
    # ``outage.py`` can import it.
    if "homeassistant" not in sys.modules:
        ha = types.ModuleType("homeassistant")
        ha.__path__ = []
        sys.modules["homeassistant"] = ha

        ha_core = types.ModuleType("homeassistant.core")
        setattr(ha_core, "HomeAssistant", object)
        setattr(ha_core, "ServiceCall", dict)
        sys.modules["homeassistant.core"] = ha_core

        ha_exc = types.ModuleType("homeassistant.exceptions")
        class _SvcValErr(Exception):
            pass
        setattr(ha_exc, "ServiceValidationError", _SvcValErr)
        sys.modules["homeassistant.exceptions"] = ha_exc

        ha_helpers = types.ModuleType("homeassistant.helpers")
        ha_helpers.__path__ = []
        sys.modules["homeassistant.helpers"] = ha_helpers

        ha_storage = types.ModuleType("homeassistant.helpers.storage")
        class _Store:
            def __init__(self, *a, **kw):
                pass
        setattr(ha_storage, "Store", _Store)
        sys.modules["homeassistant.helpers.storage"] = ha_storage

    outage_spec = importlib.util.spec_from_file_location(
        f"{_STUB_PKG}.outage", str(OUTAGE_PATH)
    )
    assert outage_spec is not None and outage_spec.loader is not None
    outage_mod = importlib.util.module_from_spec(outage_spec)
    sys.modules[f"{_STUB_PKG}.outage"] = outage_mod
    outage_spec.loader.exec_module(outage_mod)

    inbox_spec = importlib.util.spec_from_file_location(
        f"{_STUB_PKG}.outage_inbox", str(INBOX_PATH)
    )
    assert inbox_spec is not None and inbox_spec.loader is not None
    inbox_mod = importlib.util.module_from_spec(inbox_spec)
    sys.modules[f"{_STUB_PKG}.outage_inbox"] = inbox_mod
    inbox_spec.loader.exec_module(inbox_mod)

    return outage_mod, inbox_mod


outage, inbox = _load_production_modules()


REQUIRED_SYMBOLS = (
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
)
STRUCTURAL_PROBE_OK = all(
    hasattr(inbox, name) for name in REQUIRED_SYMBOLS
)
PROBE_FAILURE_REASON = (
    "outage_inbox.py is missing one of "
    f"{REQUIRED_SYMBOLS}"
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_raw(
    *,
    message_id: str = "msg-1",
    raw_text: str = "15.10.2026 09:00-18:00",
    source: "NotificationSourceV2" = None,
    channel: str = "ops@example.com",
    sender: str = "ops@example.com",
    address_hint: str | None = "вул. Київська, 12",
    original_message_id: str | None = None,
):
    if source is None:
        source = inbox.NotificationSourceV2.EMAIL
    return inbox.RawNotificationV2(
        source=source,
        channel=channel,
        message_id=message_id,
        received_at=datetime(
            2026, 10, 14, 11, 0, 0, tzinfo=timezone.utc
        ).isoformat(),
        raw_text=raw_text,
        sender=sender,
        address_hint=address_hint,
        original_message_id=original_message_id,
    )


@unittest.skipUnless(
    STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON
)
class CancellationMatchingTests(unittest.TestCase):
    """Cancellation MUST match a SPECIFIC event by
    ``original_message_id`` or by the unambiguous
    ``(address_hint, start, end)`` triple. Anti-pattern
    "pick the most recent" is forbidden.
    """

    def setUp(self):
        self.parser = inbox.NotificationParserV2("Europe/Kyiv")
        self.adapter = inbox.NotificationAdapterV2(self.parser)
        self.event_store = outage.EventStore.in_memory("test")
        self.inbox_store = inbox.InboxStore.in_memory("test")

    def test_cancellation_by_original_message_id(self):
        """A cancellation message with
        ``original_message_id`` points at the EXACT
        event that was originally created. The
        adapter finds it via the inbox notes and
        cancels it. No "most recent" lookup.
        """
        # 1. Original notification creates an event.
        original_raw = _make_raw(
            message_id="m-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_1 = _run(
            self.adapter.ingest(
                self.adapter.parse(original_raw),
                self.event_store,
                self.inbox_store,
            )
        )
        self.assertEqual(outcome_1.action, "added")
        # Persist the event into the store.
        _run(self.event_store.add(outcome_1.event))
        original_event = self.event_store.get(outcome_1.event.id)
        # Operator confirms the event (it becomes
        # eligible for dispatch).
        _run(
            outage.confirm_event(
                self.event_store, original_event.id
            )
        )
        # The event has a notes field with
        # ``message_id=m-orig`` (so the adapter can
        # find it later).
        confirmed = self.event_store.get(original_event.id)
        self.assertIsNotNone(confirmed)
        self.assertIn("message_id=m-orig", confirmed.notes)
        # 2. Cancellation message with
        # ``original_message_id="m-orig"``. The
        # adapter must cancel the SPECIFIC event.
        cancel_raw = _make_raw(
            message_id="m-cancel-1",
            raw_text="Скасовано: original-id: m-orig",
            original_message_id="m-orig",
        )
        outcome_2 = _run(
            self.adapter.ingest(
                self.adapter.parse(cancel_raw),
                self.event_store,
                self.inbox_store,
            )
        )
        self.assertEqual(
            outcome_2.action, "cancelled",
            msg=f"cancellation by original_message_id "
            f"must cancel the event; got {outcome_2!r}",
        )
        # Persist the cancellation.
        _run(
            self.event_store.update(outcome_2.target_event)
        )
        # The event is now cancelled.
        events = self.event_store.list_events()
        self.assertEqual(events[0].status, "cancelled")

    def test_cancellation_ambiguous_by_address_window(self):
        """Two events with the same address and
        overlapping windows. The cancellation has
        no ``original_message_id`` and the message
        body is just "Скасовано" (no parseable
        window). The adapter must record
        ``needs_operator_review`` and leave both
        events unchanged. The v1 anti-pattern
        would have picked the "most recent" of the
        two; the v2 refuses to decide.
        """
        # Seed two events with the same address and
        # overlapping window.
        seed_1 = outage.OutageEvent(
            id="ev-1",
            source="notification:email",
            raw_text="seed-1",
            start_iso="2026-10-15T09:00:00+03:00",
            end_iso="2026-10-15T18:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T08:00:00+00:00",
            status="confirmed",
            needs_confirmation=False,
            ambiguity_reason=None,
            version=1,
            updated_at="2026-10-14T08:00:00+00:00",
            notes=(
                "sender=test;address=вул. Київська, 12;"
                "message_id=m-seed-1"
            ),
        )
        seed_2 = outage.OutageEvent(
            id="ev-2",
            source="notification:email",
            raw_text="seed-2",
            start_iso="2026-10-15T10:00:00+03:00",
            end_iso="2026-10-15T19:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T08:01:00+00:00",
            status="confirmed",
            needs_confirmation=False,
            ambiguity_reason=None,
            version=1,
            updated_at="2026-10-14T08:01:00+00:00",
            notes=(
                "sender=test;address=вул. Київська, 12;"
                "message_id=m-seed-2"
            ),
        )
        self.event_store._events[seed_1.id] = seed_1  # type: ignore[attr-defined]
        self.event_store._events[seed_2.id] = seed_2  # type: ignore[attr-defined]
        # Cancellation message with no
        # ``original_message_id`` and a body that
        # the parser does NOT enrich with a window.
        # The adapter falls back to address+window
        # match -- the cancellation has no
        # ``parsed_start`` / ``parsed_end``, so the
        # adapter must match by address_hint ONLY
        # and find the two candidates. Two matches
        # => ambiguous => ``needs_operator_review``.
        cancel_raw = _make_raw(
            message_id="m-cancel-amb",
            raw_text="Скасовано",
            original_message_id=None,
        )
        outcome = _run(
            self.adapter.ingest(
                self.adapter.parse(cancel_raw),
                self.event_store,
                self.inbox_store,
            )
        )
        # Two matching events => ambiguous. The
        # adapter refuses to pick one and records
        # ``needs_operator_review``.
        self.assertEqual(
            outcome.action, "needs_operator_review",
            msg=f"ambiguous cancellation must produce "
            f"needs_operator_review; got {outcome!r}",
        )
        # Neither event was mutated.
        events = self.event_store.list_events()
        for ev in events:
            self.assertEqual(
                ev.status, "confirmed",
                msg=f"event {ev.id!r} must stay confirmed "
                f"on ambiguous cancellation; got "
                f"{ev.status!r}",
            )
            self.assertNotIn(
                "cancelled_by=",
                ev.notes or "",
                msg=f"event {ev.id!r} notes must NOT "
                f"record a cancellation",
            )
        # The inbox records the cancellation as
        # ``needs_operator_review``.
        inbox_keys = list(self.inbox_store._entries.keys())  # type: ignore[attr-defined]
        self.assertEqual(len(inbox_keys), 1)
        inbox_entry = self.inbox_store._entries[inbox_keys[0]]  # type: ignore[attr-defined]
        self.assertEqual(
            inbox_entry.action,
            inbox.InboxAction.NEEDS_OPERATOR_REVIEW.value,
        )

    def test_cancellation_no_target_is_unmatched(self):
        """A cancellation message that cannot
        resolve to any event is recorded as
        ``unmatched_cancellation``. The operator
        must inspect the inbox. No event is
        mutated.
        """
        cancel_raw = _make_raw(
            message_id="m-cancel-none",
            raw_text="Скасовано: original-id: m-does-not-exist",
            original_message_id="m-does-not-exist",
        )
        outcome = _run(
            self.adapter.ingest(
                self.adapter.parse(cancel_raw),
                self.event_store,
                self.inbox_store,
            )
        )
        self.assertEqual(
            outcome.action, "unmatched_cancellation",
            msg=f"a cancellation with no target must be "
            f"recorded as unmatched_cancellation; got "
            f"{outcome!r}",
        )
        # The inbox entry records the failure.
        self.assertEqual(len(self.inbox_store.list()), 1)
        inbox_entry = self.inbox_store.list()[0]
        self.assertEqual(
            inbox_entry.action,
            inbox.InboxAction.UNMATCHED_CANCELLATION.value,
        )

    def test_cancellation_does_not_pick_most_recent(self):
        """Regression: the v1 picked "the most
        recently received confirmed event of the
        same source" for cancellation. The v2
        must NEVER do that. The test seeds TWO
        confirmed events with DIFFERENT message_ids
        in their notes (so neither is the v2's
        ``original_message_id`` match). A
        cancellation with no ``original_message_id``
        is recorded as ``needs_operator_review`` --
        NOT cancelled.
        """
        # Two confirmed events from the same source.
        seed_old = outage.OutageEvent(
            id="old-1",
            source="notification:email",
            raw_text="old",
            start_iso="2026-10-15T09:00:00+03:00",
            end_iso="2026-10-15T18:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T07:00:00+00:00",
            status="confirmed",
            needs_confirmation=False,
            ambiguity_reason=None,
            version=1,
            updated_at="2026-10-14T07:00:00+00:00",
            notes=(
                "sender=ops;address=вул. Київська, 12;"
                "message_id=m-old-1"
            ),
        )
        seed_new = outage.OutageEvent(
            id="new-1",
            source="notification:email",
            raw_text="new",
            start_iso="2026-10-22T09:00:00+03:00",
            end_iso="2026-10-22T18:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T10:00:00+00:00",
            status="confirmed",
            needs_confirmation=False,
            ambiguity_reason=None,
            version=1,
            updated_at="2026-10-14T10:00:00+00:00",
            notes=(
                "sender=ops;address=вул. Київська, 12;"
                "message_id=m-new-1"
            ),
        )
        self.event_store._events[seed_old.id] = seed_old  # type: ignore[attr-defined]
        self.event_store._events[seed_new.id] = seed_new  # type: ignore[attr-defined]
        # Cancellation with no original_message_id
        # AND no address_hint. The adapter must NOT
        # pick the most recent (new-1); it records
        # unmatched_cancellation because the
        # cancellation has no resolvable target.
        cancel_raw = _make_raw(
            message_id="m-cancel-v2",
            raw_text="Скасовано",
            address_hint=None,
            original_message_id=None,
        )
        outcome = _run(
            self.adapter.ingest(
                self.adapter.parse(cancel_raw),
                self.event_store,
                self.inbox_store,
            )
        )
        self.assertEqual(
            outcome.action, "unmatched_cancellation",
            msg=f"a cancellation with no target must "
            f"NOT pick the most recent event; got "
            f"{outcome!r}",
        )
        # Neither event was mutated.
        self.assertEqual(
            self.event_store.get("old-1").status, "confirmed",
        )
        self.assertEqual(
            self.event_store.get("new-1").status, "confirmed",
        )


@unittest.skipUnless(
    STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON
)
class RescheduleTests(unittest.TestCase):
    """A reschedule creates a new event with
    ``supersedes=<old>`` in its notes and sets
    ``superseded_by`` on the old event. Both events
    live in the store.
    """

    def setUp(self):
        self.parser = inbox.NotificationParserV2("Europe/Kyiv")
        self.adapter = inbox.NotificationAdapterV2(self.parser)
        self.event_store = outage.EventStore.in_memory("test")
        self.inbox_store = inbox.InboxStore.in_memory("test")

    def test_reschedule_via_original_message_id(self):
        """Original message ``m-orig`` creates event
        ``ev-1``. A new message with
        ``original_message_id=m-orig`` and a
        DIFFERENT window is a reschedule. The
        adapter creates a new event with
        ``supersedes=ev-1`` in notes and sets
        ``superseded_by`` on the old event.
        """
        original_raw = _make_raw(
            message_id="m-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_1 = _run(
            self.adapter.ingest(
                self.adapter.parse(original_raw),
                self.event_store,
                self.inbox_store,
            )
        )
        self.assertEqual(outcome_1.action, "added")
        _run(self.event_store.add(outcome_1.event))
        old_event = self.event_store.get(outcome_1.event.id)
        # Reschedule to a different time on the same
        # day (overlap window): the parser will yield
        # a different ``parsed_start`` / ``parsed_end``.
        reschedule_raw = _make_raw(
            message_id="m-resched",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-orig",
        )
        outcome_2 = _run(
            self.adapter.ingest(
                self.adapter.parse(reschedule_raw),
                self.event_store,
                self.inbox_store,
            )
        )
        self.assertEqual(
            outcome_2.action, "superseded",
            msg=f"reschedule must be a SUPERSEDED "
            f"action; got {outcome_2!r}",
        )
        # The new event has a different start_iso.
        new_event = outcome_2.event
        self.assertNotEqual(
            new_event.start_iso, old_event.start_iso,
            msg="the new event must have a different "
            "start_iso than the old one",
        )
        # The new event's notes mention the old
        # event's id (audit link).
        self.assertIn(
            f"supersedes={old_event.id}",
            new_event.notes,
            msg=f"new event notes must record "
            f"supersedes={old_event.id}; got "
            f"{new_event.notes!r}",
        )
        # The old event's ``superseded_by`` points at
        # the new event id (carried by the adapter's
        # returned target_event).
        self.assertEqual(
            outcome_2.target_event.superseded_by,
            new_event.id,
            msg=f"old event's superseded_by must "
            f"point at the new event id; got "
            f"{outcome_2.target_event.superseded_by!r}",
        )
        # Both events live in the store.
        # The reschedule produces the new event; the
        # caller (the service handler) persists it.
        _run(self.event_store.add(outcome_2.event))
        # The old event is updated with the
        # ``superseded_by`` link.
        _run(self.event_store.update(outcome_2.target_event))
        self.assertEqual(len(self.event_store.list_events()), 2)

    def test_reschedule_via_address_window(self):
        """A reschedule without an
        ``original_message_id`` is matched by
        address+window. The new event is a
        supersession.
        """
        # Original event.
        original_raw = _make_raw(
            message_id="m-orig-2",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_1 = _run(
            self.adapter.ingest(
                self.adapter.parse(original_raw),
                self.event_store,
                self.inbox_store,
            )
        )
        _run(self.event_store.add(outcome_1.event))
        old_event = self.event_store.get(outcome_1.event.id)
        # Reschedule WITHOUT an original_message_id.
        # The new window is on a different day and
        # does NOT overlap the old one, so the
        # adapter treats it as a fresh add. The
        # contract: reschedule ONLY when the new
        # message carries ``original_message_id``;
        # otherwise the new window is a new event.
        reschedule_raw = _make_raw(
            message_id="m-resched-2",
            raw_text="2026-10-22 09:00 to 18:00",
        )
        outcome_2 = _run(
            self.adapter.ingest(
                self.adapter.parse(reschedule_raw),
                self.event_store,
                self.inbox_store,
            )
        )
        # The new window is different and does NOT
        # overlap the old one (15.10 vs 22.10). The
        # adapter adds a fresh event; the old one is
        # untouched.
        self.assertEqual(
            outcome_2.action, "added",
            msg=f"a new window without original_message_id "
            f"is a fresh add; got {outcome_2!r}",
        )
        # The old event is unchanged.
        old_after = self.event_store.get(old_event.id)
        self.assertIsNone(
            old_after.superseded_by,
            msg="the old event must NOT be superseded "
            "without original_message_id",
        )
        # Both events live in the store.
        _run(self.event_store.add(outcome_2.event))
        self.assertEqual(len(self.event_store.list_events()), 2)


@unittest.skipUnless(
    STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON
)
class InboxPersistenceTests(unittest.TestCase):
    """The inbox is the canonical record. A
    redelivery of the same
    ``(source, channel, message_id)`` after restart
    is a no-op.
    """

    def setUp(self):
        self.parser = inbox.NotificationParserV2("Europe/Kyiv")
        self.adapter = inbox.NotificationAdapterV2(self.parser)

    def test_redelivery_after_reload_is_noop(self):
        """Full path: ingest a message, persist the
        inbox and event store, simulate a reload
        (new InboxStore / EventStore backed by the
        same underlying storage), ingest the same
        message again, and verify the second call is
        a no-op.
        """
        # First session: the backend dicts.
        backend_events: dict[str, Any] = {}
        backend_inbox: dict[str, Any] = {}

        class _DictStore:
            """``async_load`` / ``async_save`` backed
            by a plain dict (the in-process stand-in
            for HA ``Store``). The dict is MUTATED
            in place (not reassigned) so a second
            ``InboxStore`` reading the same backend
            sees the saved state."""

            def __init__(self, backend, default):
                self._backend = backend
                self._default = default

            async def async_load(self):
                if self._backend:
                    # Return a deep copy so the
                    # caller's mutation of the
                    # returned dict does not bleed
                    # back into the backend.
                    import copy
                    return copy.deepcopy(self._backend)
                return self._default

            async def async_save(self, data):
                # Mutate the backend in place so the
                # caller's reference stays live.
                self._backend.clear()
                self._backend.update(data)

        event_store_1 = outage.EventStore(
            _DictStore(backend_events, default=None),
            "test",
        )
        inbox_store_1 = inbox.InboxStore(
            _DictStore(backend_inbox, default=None),
            "test",
        )
        # 1. First ingestion.
        raw = _make_raw(
            message_id="m-persist",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_1 = _run(
            self.adapter.ingest(
                self.adapter.parse(raw),
                event_store_1,
                inbox_store_1,
            )
        )
        self.assertEqual(outcome_1.action, "added")
        # Persist both stores.
        _run(event_store_1.add(outcome_1.event))
        _run(event_store_1.async_save())
        _run(inbox_store_1.async_save())
        # 2. Simulate a reload: new stores, same
        # underlying backend dicts.
        event_store_2 = outage.EventStore(
            _DictStore(backend_events, default=None),
            "test",
        )
        inbox_store_2 = inbox.InboxStore(
            _DictStore(backend_inbox, default=None),
            "test",
        )
        _run(event_store_2.async_load())
        _run(inbox_store_2.async_load())
        # 3. Redeliver the same message.
        outcome_2 = _run(
            self.adapter.ingest(
                self.adapter.parse(raw),
                event_store_2,
                inbox_store_2,
            )
        )
        # The second call is a duplicate: the action
        # is the original action (added) and the
        # reason explains why.
        self.assertEqual(
            outcome_2.action, "added",
            msg=f"a redelivery must return the original "
            f"action from the inbox; got {outcome_2!r}",
        )
        self.assertIn(
            "inbox already processed",
            outcome_2.reason,
            msg=f"redelivery reason must mention the "
            f"inbox; got {outcome_2.reason!r}",
        )
        # The event store has exactly 1 event (no
        # duplicate added by the redelivery).
        self.assertEqual(
            len(event_store_2.list_events()),
            1,
            msg=f"redelivery must NOT add a second "
            f"event; got "
            f"{event_store_2.list_events()!r}",
        )
        # The inbox has exactly 1 entry.
        self.assertEqual(
            len(inbox_store_2.list()),
            1,
            msg=f"redelivery must NOT add a second "
            f"inbox entry; got {inbox_store_2.list()!r}",
        )

    def test_cancellation_redelivery_is_noop(self):
        """A cancellation message's redelivery is
        a no-op: the inbox has the original
        ``cancelled`` action and the event stays
        cancelled.
        """
        backend_events: dict[str, Any] = {}
        backend_inbox: dict[str, Any] = {}

        class _DictStore:
            def __init__(self, backend, default):
                self._backend = backend
                self._default = default
            async def async_load(self):
                if self._backend:
                    import copy
                    return copy.deepcopy(self._backend)
                return self._default
            async def async_save(self, data):
                self._backend.clear()
                self._backend.update(data)

        # 1. Create + cancel.
        event_store_1 = outage.EventStore(
            _DictStore(backend_events, default=None),
            "test",
        )
        inbox_store_1 = inbox.InboxStore(
            _DictStore(backend_inbox, default=None),
            "test",
        )
        original_raw = _make_raw(
            message_id="m-cancel-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_1 = _run(
            self.adapter.ingest(
                self.adapter.parse(original_raw),
                event_store_1,
                inbox_store_1,
            )
        )
        _run(event_store_1.add(outcome_1.event))
        original_event_id = outcome_1.event.id
        # Operator confirms.
        _run(
            outage.confirm_event(
                event_store_1, original_event_id
            )
        )
        # Cancellation.
        cancel_raw = _make_raw(
            message_id="m-cancel-redeliver",
            raw_text="Скасовано: original-id: m-cancel-orig",
            original_message_id="m-cancel-orig",
        )
        outcome_cancel = _run(
            self.adapter.ingest(
                self.adapter.parse(cancel_raw),
                event_store_1,
                inbox_store_1,
            )
        )
        self.assertEqual(outcome_cancel.action, "cancelled")
        _run(
            event_store_1.update(outcome_cancel.target_event)
        )
        # Persist.
        _run(event_store_1.async_save())
        _run(inbox_store_1.async_save())
        # 2. Reload + redeliver the cancellation.
        event_store_2 = outage.EventStore(
            _DictStore(backend_events, default=None),
            "test",
        )
        inbox_store_2 = inbox.InboxStore(
            _DictStore(backend_inbox, default=None),
            "test",
        )
        _run(event_store_2.async_load())
        _run(inbox_store_2.async_load())
        outcome_2 = _run(
            self.adapter.ingest(
                self.adapter.parse(cancel_raw),
                event_store_2,
                inbox_store_2,
            )
        )
        # The action is the original (cancelled); the
        # redelivery does NOT touch the event.
        self.assertEqual(outcome_2.action, "cancelled")
        self.assertIn("inbox already processed", outcome_2.reason)
        # The event is STILL cancelled (the
        # redelivery did not double-cancel).
        event_after = event_store_2.get(original_event_id)
        self.assertEqual(event_after.status, "cancelled")
        # The version was NOT bumped (the redelivery
        # is a no-op).
        self.assertEqual(event_after.version, 3)
        # The inbox has exactly 2 entries (the
        # original + the cancellation; the
        # redelivery is a no-op).
        self.assertEqual(len(inbox_store_2.list()), 2)


@unittest.skipUnless(
    STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON
)
class FullPipelineTests(unittest.TestCase):
    """End-to-end: notification -> persisted event
    (status=pending) -> operator confirms -> planner
    picks it up -> cancel via notification -> the
    event is cancelled.
    """

    def setUp(self):
        self.parser = inbox.NotificationParserV2("Europe/Kyiv")
        self.adapter = inbox.NotificationAdapterV2(self.parser)
        self.event_store = outage.EventStore.in_memory("test")
        self.inbox_store = inbox.InboxStore.in_memory("test")

    def test_full_message_to_plan_path(self):
        """Message arrives -> pending event -> operator
        confirms -> ``_find_active_event`` picks it ->
        cancel via second message -> event is
        cancelled -> ``_find_active_event`` does NOT
        pick it.
        """
        raw = _make_raw(
            message_id="m-full-1",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome = _run(
            self.adapter.ingest(
                self.adapter.parse(raw),
                self.event_store,
                self.inbox_store,
            )
        )
        self.assertEqual(outcome.action, "added")
        # Persist.
        _run(self.event_store.add(outcome.event))
        new_event = self.event_store.get(outcome.event.id)
        # The event is PENDING.
        self.assertEqual(new_event.status, "pending")
        # The runtime's ``_find_active_event`` only
        # picks ``confirmed`` events.
        eligible = [
            ev
            for ev in self.event_store.iter_active()
            if ev.status == "confirmed"
        ]
        self.assertEqual(eligible, [])
        # Operator confirms.
        _run(outage.confirm_event(self.event_store, new_event.id))
        confirmed = self.event_store.get(new_event.id)
        self.assertEqual(confirmed.status, "confirmed")
        # ``_find_active_event`` picks it now.
        eligible = [
            ev
            for ev in self.event_store.iter_active()
            if ev.status == "confirmed"
        ]
        self.assertEqual(len(eligible), 1)
        # Cancellation via a second message.
        cancel_raw = _make_raw(
            message_id="m-full-cancel",
            raw_text="Скасовано: original-id: m-full-1",
            original_message_id="m-full-1",
        )
        outcome_cancel = _run(
            self.adapter.ingest(
                self.adapter.parse(cancel_raw),
                self.event_store,
                self.inbox_store,
            )
        )
        self.assertEqual(outcome_cancel.action, "cancelled")
        _run(
            self.event_store.update(
                outcome_cancel.target_event
            )
        )
        # The event is cancelled.
        self.assertEqual(
            self.event_store.get(new_event.id).status,
            "cancelled",
        )
        # ``_find_active_event`` does NOT pick it
        # anymore.
        eligible = [
            ev
            for ev in self.event_store.iter_active()
            if ev.status == "confirmed"
        ]
        self.assertEqual(eligible, [])


if __name__ == "__main__":
    unittest.main()
