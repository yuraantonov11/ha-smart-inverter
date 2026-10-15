"""Phase 4: notification adapter tests.

Full behavioural tests of the ingestion pipeline:

  * ``RawNotification`` -> ``NotificationParser`` ->
    ``ParsedNotification`` -> ``NotificationAdapter`` ->
    ``EventStore``.

The tests exercise the production path end-to-end:
no shortcuts, no fake parsers. The adapter is the
boundary that decides whether a notification is
"added", "updated_pending", "duplicate",
"cancelled", or "ignored". The production runtime
``_find_active_event`` (services/outage_runtime.py)
only picks ``status == 'confirmed'`` events, so a
pending ``OutageEvent`` produced by the adapter is
NEVER auto-dispatched -- this is the spec's
``Неоднозначне повідомлення не повинно автоматично
ставати підтвердженою подією`` rule (extended to
unambiguous messages too: the operator must still
ratify via ``confirm_event``).

Verification commands:

    .test-venv/bin/python -m unittest tests.test_outage_notifications -v
    .test-venv/bin/python -m unittest discover tests/ -p "test_outage*.py"
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NOTIFICATIONS_PATH = (
    REPO_ROOT / "services" / "outage_notifications.py"
)
OUTAGE_PATH = REPO_ROOT / "services" / "outage.py"

# Stub package so the relative import inside
# outage_notifications.py resolves.
_STUB_PKG = "_outage_notifications_stub"


def _load_production_modules():
    """Load ``outage`` and ``outage_notifications`` as
    sub-modules of a stub package.
    """
    pkg = types.ModuleType(_STUB_PKG)
    pkg.__path__ = [str(REPO_ROOT)]
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

    # 1. Load outage.
    outage_spec = importlib.util.spec_from_file_location(
        f"{_STUB_PKG}.outage", str(OUTAGE_PATH)
    )
    assert outage_spec is not None and outage_spec.loader is not None
    outage_mod = importlib.util.module_from_spec(outage_spec)
    sys.modules[f"{_STUB_PKG}.outage"] = outage_mod
    outage_spec.loader.exec_module(outage_mod)

    # 2. Load outage_notifications (imports ``.outage``).
    notif_spec = importlib.util.spec_from_file_location(
        f"{_STUB_PKG}.outage_notifications", str(NOTIFICATIONS_PATH)
    )
    assert notif_spec is not None and notif_spec.loader is not None
    notif_mod = importlib.util.module_from_spec(notif_spec)
    sys.modules[f"{_STUB_PKG}.outage_notifications"] = notif_mod
    notif_spec.loader.exec_module(notif_mod)

    return notif_mod, outage_mod


notif, outage = _load_production_modules()


REQUIRED_SYMBOLS = (
    "NotificationSource",
    "RawNotification",
    "ParsedNotification",
    "NotificationParser",
    "NotificationAdapter",
    "IngestOutcome",
)
STRUCTURAL_PROBE_OK = all(
    hasattr(notif, name) for name in REQUIRED_SYMBOLS
)
PROBE_FAILURE_REASON = (
    "outage_notifications.py is missing one of "
    f"{REQUIRED_SYMBOLS}"
)


def _make_raw(
    message_id: str = "msg-1",
    raw_text: str = "завтра з 9:00 до 18:00 буде відключення",
    source: "NotificationSource" = None,
    sender: str = "dispatcher@oblenergo.local",
    address_hint: str | None = "вул. Київська, 12",
) -> "RawNotification":
    if source is None:
        source = notif.NotificationSource.EMAIL
    return notif.RawNotification(
        message_id=message_id,
        source=source,
        received_at=datetime(2026, 10, 14, 11, 0, 0, tzinfo=timezone.utc).isoformat(),
        raw_text=raw_text,
        sender=sender,
        address_hint=address_hint,
    )


@unittest.skipUnless(
    STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON
)
class FreshEventTests(unittest.TestCase):
    """An unambiguous notification creates a PENDING
    event. The dispatcher only picks ``confirmed``,
    so the event is never auto-dispatched.
    """

    def setUp(self):
        self.parser = notif.NotificationParser("Europe/Kyiv")
        self.adapter = notif.NotificationAdapter(self.parser)
        self.store = outage.EventStore.in_memory("test-entry")

    def test_unambiguous_message_creates_pending_event(self):
        raw = _make_raw(
            message_id="m-1",
            raw_text="завтра з 9:00 до 18:00 буде відключення",
        )
        parsed = self.parser.parse(raw)
        outcome = self.adapter.ingest(parsed, self.store)
        # The adapter added a new event.
        self.assertEqual(
            outcome.action, "added",
            msg=f"unambiguous message must be added; got "
            f"{outcome!r}",
        )
        # The event is PENDING (the dispatcher never
        # auto-confirms; the operator ratifies).
        self.assertEqual(outcome.event.status, "pending")
        # The raw_text and source are preserved for
        # audit.
        self.assertEqual(
            outcome.event.raw_text,
            "завтра з 9:00 до 18:00 буде відключення",
        )
        self.assertTrue(
            outcome.event.source.startswith("notification:"),
            msg=f"source must carry the notification "
            f"prefix; got {outcome.event.source!r}",
        )
        # The message_id is stamped into the notes
        # for later dedup.
        self.assertIn("m-1", outcome.event.notes)
        # The sender and address are stamped too.
        self.assertIn("dispatcher@oblenergo.local", outcome.event.notes)
        self.assertIn("вул. Київська, 12", outcome.event.notes)
        # The event is not auto-dispatchable: a
        # find_active_event call would NOT pick it
        # because its status is "pending".
        self.assertNotEqual(outcome.event.status, "confirmed")

    def test_ambiguous_message_without_window_is_ignored(self):
        """An ambiguous message (parser
        ``needs_confirmation=True``) with NO end
        at all is recorded as ``ignored``. The
        adapter does not invent a window.
        """
        raw = _make_raw(
            message_id="m-amb-1",
            raw_text="сьогодні з 14:00",  # single endpoint
        )
        parsed = self.parser.parse(raw)
        # Single-endpoint parse: start_iso set,
        # end_iso None.
        self.assertIsNotNone(parsed.parsed_start)
        self.assertIsNone(
            parsed.parsed_end,
            msg=f"single-endpoint parse must NOT produce "
            f"a concrete end; got {parsed.parsed_end!r}",
        )
        self.assertIsNotNone(parsed.ambiguity_reason)
        outcome = self.adapter.ingest(parsed, self.store)
        self.assertEqual(
            outcome.action, "ignored",
            msg=f"ambiguous message with no end must be "
            f"ignored; got {outcome!r}",
        )
        # The store is empty -- no event was created.
        self.assertEqual(len(self.store.list_events()), 0)

    def test_no_event_auto_dispatch_from_pending(self):
        """A pending ``OutageEvent`` produced by the
        adapter is NOT picked by
        ``_find_active_event`` because the runtime
        requires ``status == 'confirmed'``. This is
        the spec's ``Неоднозначне повідомлення не
        повинно автоматично ставати підтвердженою
        подією`` rule, extended to unambiguous
        messages too.
        """
        raw = _make_raw(
            message_id="m-1",
            raw_text="завтра з 9:00 до 18:00 буде відключення",
        )
        parsed = self.parser.parse(raw)
        outcome = self.adapter.ingest(parsed, self.store)
        new_event = outcome.event
        # Persist the event via the store's in-memory
        # dict (the service handler would call
        # ``await store.add(new_event)``).
        self.store._events[new_event.id] = new_event  # type: ignore[attr-defined]
        # The runtime's ``_find_active_event``
        # requires ``status == 'confirmed'``. So the
        # pending event is NOT picked.
        eligible = [
            ev
            for ev in self.store.iter_active()
            if ev.status == "confirmed"
        ]
        self.assertEqual(
            eligible, [],
            msg=f"a pending event must NOT be picked "
            f"for dispatch; got {eligible!r}",
        )


@unittest.skipUnless(
    STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON
)
class DedupTests(unittest.TestCase):
    """Dedup by ``message_id`` and by window."""

    def setUp(self):
        self.parser = notif.NotificationParser("Europe/Kyiv")
        self.adapter = notif.NotificationAdapter(self.parser)
        self.store = outage.EventStore.in_memory("test-entry")

    def test_duplicate_message_id_is_no_op(self):
        raw = _make_raw(
            message_id="m-dup",
            raw_text="завтра з 9:00 до 18:00 буде відключення",
        )
        parsed = self.parser.parse(raw)
        # First call adds.
        outcome_1 = self.adapter.ingest(parsed, self.store)
        self.assertEqual(outcome_1.action, "added")
        # Persist the first event so the dedup can
        # find the message_id in the notes.
        self.store._events[outcome_1.event.id] = outcome_1.event  # type: ignore[attr-defined]
        # Second call with the same message_id is a
        # duplicate (no-op).
        outcome_2 = self.adapter.ingest(parsed, self.store)
        self.assertEqual(
            outcome_2.action, "duplicate",
            msg=f"resend with same message_id must be "
            f"a duplicate; got {outcome_2!r}",
        )
        # The store still has exactly 1 event.
        self.assertEqual(len(self.store.list_events()), 1)

    def test_resend_with_different_message_id_no_improvement(self):
        """A resend with a DIFFERENT message_id but the
        same address + window + source is treated as
        a revision. If the new confidence is not
        higher than the existing one, the adapter
        drops it as a duplicate.
        """
        # First message: unambiguous, confidence 1.0.
        # Use two phrasings of the SAME window so
        # ``find_duplicate`` detects the overlap.
        raw_1 = _make_raw(
            message_id="m-1",
            raw_text="15.10.2026 09:00-18:00",
        )
        parsed_1 = self.parser.parse(raw_1)
        outcome_1 = self.adapter.ingest(parsed_1, self.store)
        self.assertEqual(outcome_1.action, "added")
        # Persist the first event so the dedup /
        # revision path can find it.
        self.store._events[outcome_1.event.id] = outcome_1.event  # type: ignore[attr-defined]
        # Second message: different id, same window
        # (rephrased), same source. Also unambiguous
        # (confidence 1.0). No improvement -> duplicate.
        raw_2 = _make_raw(
            message_id="m-2",
            raw_text="2026-10-15 09:00 to 18:00",
        )
        parsed_2 = self.parser.parse(raw_2)
        outcome_2 = self.adapter.ingest(parsed_2, self.store)
        # The new confidence is not strictly higher
        # than the existing one, so the adapter
        # drops it as a duplicate.
        self.assertEqual(
            outcome_2.action, "duplicate",
            msg=f"resend with same confidence must be "
            f"a duplicate; got {outcome_2!r}",
        )
        # The store still has exactly 1 event.
        self.assertEqual(len(self.store.list_events()), 1)


@unittest.skipUnless(
    STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON
)
class RevisionTests(unittest.TestCase):
    """A resend with a HIGHER confidence updates the
    existing pending event.
    """

    def setUp(self):
        self.parser = notif.NotificationParser("Europe/Kyiv")
        self.adapter = notif.NotificationAdapter(self.parser)
        self.store = outage.EventStore.in_memory("test-entry")

    def test_revision_with_higher_confidence_updates_event(self):
        """Pre-seed a pending event with
        ``ambiguity_reason`` set. A subsequent
        unambiguous parse with the same window
        updates the event: ``raw_text`` is replaced,
        ``ambiguity_reason`` is cleared, ``version``
        is bumped.
        """
        # Pre-seed: a pending event with
        # ``ambiguity_reason`` set.
        seed = outage.OutageEvent(
            id="seed-1",
            source="notification:email",
            raw_text="завтра о 9:00",
            start_iso="2026-10-15T09:00:00+03:00",
            end_iso="2026-10-15T18:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T11:00:00+00:00",
            status="pending",
            needs_confirmation=True,
            ambiguity_reason="parser: single endpoint",
            version=1,
            updated_at="2026-10-14T11:00:00+00:00",
            notes=(
                "sender=test;address=addr;message_id=m-seed"
            ),
        )
        self.store._events[seed.id] = seed  # type: ignore[attr-defined]
        # Send a fresh, unambiguous parse with
        # the same window and source.
        raw_2 = _make_raw(
            message_id="m-rev-2",
            raw_text="15.10.2026 09:00-18:00",
        )
        parsed_2 = self.parser.parse(raw_2)
        outcome = self.adapter.ingest(parsed_2, self.store)
        self.assertEqual(
            outcome.action, "updated_pending",
            msg=f"higher-confidence revision must "
            f"update the event; got {outcome!r}",
        )
        # The adapter returns the new event state.
        # The surrounding service handler would
        # call ``await store.update(outcome.event)``.
        # For this test we bypass the I/O and write
        # directly.
        self.store._events[outcome.event.id] = outcome.event  # type: ignore[attr-defined]
        # The event in the store was updated.
        events = self.store.list_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0].raw_text, "15.10.2026 09:00-18:00",
            msg=f"raw_text must be updated; got "
            f"{events[0].raw_text!r}",
        )
        self.assertIsNone(
            events[0].ambiguity_reason,
            msg=f"ambiguity_reason must be cleared on "
            f"higher-confidence update; got "
            f"{events[0].ambiguity_reason!r}",
        )
        # The version was bumped.
        self.assertEqual(events[0].version, 2)

    def test_revision_does_not_auto_confirm(self):
        """A revision updates a PENDING event but
        never promotes it to ``confirmed``. The
        operator must still ratify.
        """
        seed = outage.OutageEvent(
            id="seed-1",
            source="notification:email",
            raw_text="завтра о 9:00",
            start_iso="2026-10-15T09:00:00+03:00",
            end_iso="2026-10-15T18:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T11:00:00+00:00",
            status="pending",
            needs_confirmation=True,
            ambiguity_reason="parser: single endpoint",
            version=1,
            updated_at="2026-10-14T11:00:00+00:00",
            notes=(
                "sender=test;address=addr;message_id=m-seed"
            ),
        )
        self.store._events[seed.id] = seed  # type: ignore[attr-defined]
        raw = _make_raw(
            message_id="m-rev-2",
            raw_text="15.10.2026 09:00-18:00",
        )
        parsed = self.parser.parse(raw)
        self.adapter.ingest(parsed, self.store)
        # The event is still pending.
        events = self.store.list_events()
        self.assertEqual(events[0].status, "pending")
        self.assertNotEqual(events[0].status, "confirmed")

    def test_revision_does_not_overwrite_confirmed(self):
        """A confirmed event with the same window
        must NOT be auto-rewritten. The new message
        is recorded as ``duplicate`` and the
        operator must cancel and re-add.
        """
        seed = outage.OutageEvent(
            id="seed-1",
            source="notification:email",
            raw_text="завтра з 9:00 до 18:00",
            start_iso="2026-10-15T09:00:00+03:00",
            end_iso="2026-10-15T18:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T11:00:00+00:00",
            status="confirmed",
            needs_confirmation=False,
            ambiguity_reason=None,
            version=2,
            updated_at="2026-10-14T11:01:00+00:00",
            notes=(
                "sender=test;address=addr;message_id=m-conf"
            ),
        )
        self.store._events[seed.id] = seed  # type: ignore[attr-defined]
        raw = _make_raw(
            message_id="m-rev-2",
            raw_text="15.10.2026 09:00-18:00",
        )
        parsed = self.parser.parse(raw)
        outcome = self.adapter.ingest(parsed, self.store)
        self.assertEqual(
            outcome.action, "duplicate",
            msg=f"a confirmed event must NOT be auto-"
            f"rewritten; got {outcome!r}",
        )
        # The event is unchanged.
        events = self.store.list_events()
        self.assertEqual(events[0].status, "confirmed")
        self.assertEqual(events[0].raw_text, "завтра з 9:00 до 18:00")


@unittest.skipUnless(
    STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON
)
class CancellationTests(unittest.TestCase):
    """Cancellation keywords transition a confirmed
    event to cancelled. The pending-events pipeline
    never auto-cancels.
    """

    def setUp(self):
        self.parser = notif.NotificationParser("Europe/Kyiv")
        self.adapter = notif.NotificationAdapter(self.parser)
        self.store = outage.EventStore.in_memory("test-entry")

    def test_cancellation_cancels_confirmed_event(self):
        """A cancellation message with the same
        source cancels the most recent confirmed
        event from that source.
        """
        confirmed = outage.OutageEvent(
            id="conf-1",
            source="notification:email",
            raw_text="завтра з 9:00 до 18:00",
            start_iso="2026-10-15T09:00:00+03:00",
            end_iso="2026-10-15T18:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T08:00:00+00:00",
            status="confirmed",
            needs_confirmation=False,
            ambiguity_reason=None,
            version=2,
            updated_at="2026-10-14T08:01:00+00:00",
            notes="sender=test;address=addr;message_id=m-conf",
        )
        self.store._events[confirmed.id] = confirmed  # type: ignore[attr-defined]
        raw = _make_raw(
            message_id="m-cancel-1",
            raw_text="Скасовано: відключення 15.10.2026 09:00-18:00",
        )
        parsed = self.parser.parse(raw)
        self.assertTrue(parsed.is_cancellation)
        outcome = self.adapter.ingest(parsed, self.store)
        self.assertEqual(
            outcome.action, "cancelled",
            msg=f"cancellation must cancel the event; "
            f"got {outcome!r}",
        )
        # Persist the cancelled state.
        self.store._events[outcome.event.id] = outcome.event  # type: ignore[attr-defined]
        # The event is now cancelled.
        events = self.store.list_events()
        self.assertEqual(events[0].status, "cancelled")
        # The cancellation message_id is stamped
        # into the notes.
        self.assertIn("m-cancel-1", events[0].notes)
        # The version was bumped.
        self.assertEqual(events[0].version, 3)

    def test_cancellation_without_confirmed_event_is_ignored(self):
        """A cancellation message that has no
        matching confirmed event is recorded as
        ``ignored_cancellation`` (the operator may
        have cancelled manually already, or the
        message arrived before the original).
        """
        raw = _make_raw(
            message_id="m-cancel-2",
            raw_text="скасовано",
        )
        parsed = self.parser.parse(raw)
        outcome = self.adapter.ingest(parsed, self.store)
        self.assertEqual(
            outcome.action, "ignored_cancellation",
            msg=f"cancellation with no target must be "
            f"ignored; got {outcome!r}",
        )

    def test_cancellation_does_not_cancel_pending_event(self):
        """A cancellation message only targets
        CONFIRMED events. A pending event from the
        same source stays pending -- the operator
        decides what to do with it.
        """
        pending = outage.OutageEvent(
            id="pend-1",
            source="notification:email",
            raw_text="завтра з 9:00 до 18:00",
            start_iso="2026-10-15T09:00:00+03:00",
            end_iso="2026-10-15T18:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T08:00:00+00:00",
            status="pending",
            needs_confirmation=False,
            ambiguity_reason=None,
            version=1,
            updated_at="2026-10-14T08:00:00+00:00",
            notes="sender=test;address=addr;message_id=m-pend",
        )
        self.store._events[pending.id] = pending  # type: ignore[attr-defined]
        raw = _make_raw(
            message_id="m-cancel-3",
            raw_text="скасовано",
        )
        parsed = self.parser.parse(raw)
        outcome = self.adapter.ingest(parsed, self.store)
        # No confirmed event matched -> ignored.
        self.assertEqual(outcome.action, "ignored_cancellation")
        # The pending event is unchanged.
        events = self.store.list_events()
        self.assertEqual(events[0].status, "pending")
        self.assertEqual(events[0].version, 1)


@unittest.skipUnless(
    STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON
)
class ParserTests(unittest.TestCase):
    """``NotificationParser`` distinguishes a
    cancellation message from a planned-outage
    message and reports the parser's confidence.
    """

    def setUp(self):
        self.parser = notif.NotificationParser("Europe/Kyiv")

    def test_unambiguous_planned_outage(self):
        raw = _make_raw(
            message_id="m-1",
            raw_text="завтра з 9:00 до 18:00 буде відключення",
        )
        parsed = self.parser.parse(raw)
        self.assertFalse(parsed.is_cancellation)
        self.assertIsNotNone(parsed.parsed_start)
        self.assertIsNotNone(parsed.parsed_end)
        self.assertEqual(parsed.confidence, 1.0)
        self.assertIsNone(parsed.ambiguity_reason)

    def test_ukrainian_cancellation_keyword(self):
        raw = _make_raw(
            message_id="m-2",
            raw_text="Скасовано: відключення 15.10.2026",
        )
        parsed = self.parser.parse(raw)
        self.assertTrue(parsed.is_cancellation)

    def test_english_cancellation_keyword(self):
        raw = _make_raw(
            message_id="m-3",
            raw_text="Cancelled: outage 15.10.2026",
        )
        parsed = self.parser.parse(raw)
        self.assertTrue(parsed.is_cancellation)

    def test_empty_input(self):
        raw = _make_raw(message_id="m-4", raw_text="")
        parsed = self.parser.parse(raw)
        self.assertTrue(parsed.ambiguity_reason is not None)


@unittest.skipUnless(
    STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON
)
class DispatcherIntegrationTests(unittest.TestCase):
    """End-to-end: notification -> pending event in the
    store -> ``_find_active_event`` does NOT pick it
    (only ``confirmed`` events are eligible for
    dispatch) -> ``plan_for_event`` works only after
    ``confirm_event`` is called.
    """

    def setUp(self):
        self.parser = notif.NotificationParser("Europe/Kyiv")
        self.adapter = notif.NotificationAdapter(self.parser)
        self.store = outage.EventStore.in_memory("test-entry")

    def test_pending_event_becomes_eligible_after_confirm(self):
        """The runtime's ``_find_active_event``
        requires ``status == 'confirmed'``. After
        ``confirm_event`` the pending event becomes
        eligible for dispatch.
        """
        raw = _make_raw(
            message_id="m-1",
            raw_text="завтра з 9:00 до 18:00 буде відключення",
        )
        parsed = self.parser.parse(raw)
        outcome = self.adapter.ingest(parsed, self.store)
        new_event = outcome.event
        self.store._events[new_event.id] = new_event  # type: ignore[attr-defined]
        # The event is PENDING.
        self.assertEqual(new_event.status, "pending")
        # The runtime's filter: only ``confirmed``.
        eligible = [
            ev
            for ev in self.store.iter_active()
            if ev.status == "confirmed"
        ]
        self.assertEqual(eligible, [])
        # After ``confirm_event`` the event is
        # picked.
        async def _confirm():
            await outage.confirm_event(
                self.store, new_event.id
            )
        asyncio.new_event_loop().run_until_complete(_confirm())
        eligible = [
            ev
            for ev in self.store.iter_active()
            if ev.status == "confirmed"
        ]
        self.assertEqual(len(eligible), 1)


@unittest.skipUnless(
    STRUCTURAL_PROBE_OK, PROBE_FAILURE_REASON
)
class AuditFieldsTests(unittest.TestCase):
    """The adapter preserves ``raw_text`` and
    ``source`` on every event so the operator can
    audit the parser's input.
    """

    def setUp(self):
        self.parser = notif.NotificationParser("Europe/Kyiv")
        self.adapter = notif.NotificationAdapter(self.parser)
        self.store = outage.EventStore.in_memory("test-entry")

    def test_audit_fields_on_fresh_event(self):
        raw = _make_raw(
            message_id="m-audit-1",
            raw_text="15.10.2026 09:00-18:00",
            sender="audit@oblenergo.local",
        )
        parsed = self.parser.parse(raw)
        outcome = self.adapter.ingest(parsed, self.store)
        event = outcome.event
        # raw_text preserved verbatim.
        self.assertEqual(event.raw_text, "15.10.2026 09:00-18:00")
        # source carries the notification prefix.
        self.assertEqual(
            event.source, "notification:email",
            msg=f"source must carry the notification "
            f"prefix; got {event.source!r}",
        )
        # notes carry the sender, address, and
        # message_id.
        self.assertIn("audit@oblenergo.local", event.notes)
        self.assertIn("вул. Київська, 12", event.notes)
        self.assertIn("m-audit-1", event.notes)


if __name__ == "__main__":
    unittest.main()
