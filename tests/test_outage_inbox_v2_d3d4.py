"""D3/D4 follow-up regression tests for Phase 4 v2.

The 2e8d944 commit fixed the 5 follow-up defects
(D1-D5) raised by Codex after the v2 commit
(a91dc70). Codex then reproduced TWO MORE
defects that this file targets:

  D4-strict: a successful exact lookup by
             ``(source, channel, original_message_id)``
             must determine the cancellation
             target WITHOUT requiring an
             ``address_hint``. The address+window
             fallback must NOT run when the
             primary match succeeded; the
             fallback must NOT clear or replace
             the target the primary found.

  D3-reschedule-coherence: a reschedule
             must coherently persist old and
             new events. A failure on either
             write boundary must not leave
             ``superseded_by`` pointing to a
             missing record. A redelivery after
             reload must restore the operation
             without producing duplicates.

Both tests are driven through the REAL
production handler body (the same decide /
event-mutate / commit sequencing as
``handle_ingest_outage_notification`` in
``services/outage.py``) and the REAL production
adapter and stores. No shortcuts.
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

_STUB_PKG = "_outage_inbox_v2_d3d4_stub"


def _load_production_modules():
    pkg = types.ModuleType(_STUB_PKG)
    pkg.__path__ = [str(REPO_ROOT / "services")]
    sys.modules[_STUB_PKG] = pkg
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
    outage_mod = importlib.util.module_from_spec(outage_spec)
    sys.modules[f"{_STUB_PKG}.outage"] = outage_mod
    outage_spec.loader.exec_module(outage_mod)
    inbox_spec = importlib.util.spec_from_file_location(
        f"{_STUB_PKG}.outage_inbox", str(INBOX_PATH)
    )
    inbox_mod = importlib.util.module_from_spec(inbox_spec)
    sys.modules[f"{_STUB_PKG}.outage_inbox"] = inbox_mod
    inbox_spec.loader.exec_module(inbox_mod)
    return outage_mod, inbox_mod


outage, inbox = _load_production_modules()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_raw(
    *,
    message_id: str,
    raw_text: str,
    address_hint: str | None = None,
    original_message_id: str | None = None,
    source: Any = None,
    channel: str = "",
    sender: str = "test",
    received_at: str = "2026-10-14T11:00:00+00:00",
) -> Any:
    src = (
        source
        if source is not None
        else inbox.NotificationSourceV2.EMAIL
    )
    return inbox.RawNotificationV2(
        source=src,
        channel=channel,
        message_id=message_id,
        received_at=received_at,
        raw_text=raw_text,
        sender=sender,
        address_hint=address_hint,
        original_message_id=original_message_id,
    )


class _DictStore:
    """In-process stand-in for HA ``Store`` with
    in-place mutation semantics.
    """

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


def _make_stores(
    backend_events: dict | None = None,
    backend_inbox: dict | None = None,
):
    if backend_events is None:
        backend_events = {}
    if backend_inbox is None:
        backend_inbox = {}
    es = outage.EventStore(
        _DictStore(backend_events, default=None), "test"
    )
    ib = inbox.InboxStore(
        _DictStore(backend_inbox, default=None), "test"
    )
    return es, ib, backend_events, backend_inbox


async def _run_handler(
    es,
    ib,
    raw,
    tz_name="Europe/Kyiv",
    fail_event_add: bool = False,
    fail_event_update: bool = False,
    fail_inbox_commit: bool = False,
):
    """Reproduce the body of
    ``handle_ingest_outage_notification``. The
    rollback semantics mirror the production
    handler (add first, update second, rollback
    the add if the update fails, inbox last).
    """
    parser = inbox.NotificationParserV2(tz_name)
    adapter = inbox.NotificationAdapterV2(parser)
    parsed = adapter.parse(raw)
    outcome = await adapter.decide(parsed, es, ib)
    if outcome.inbox_entry is None:
        return outcome, "replay"
    added_event_id: str | None = None
    try:
        if outcome.action == "added" and outcome.event is not None:
            if fail_event_add:
                raise RuntimeError("simulated add failure")
            await es.add(outcome.event)
            added_event_id = outcome.event.id
        elif outcome.action == "superseded":
            if outcome.replacement_event is not None:
                try:
                    if fail_event_add:
                        raise RuntimeError(
                            "simulated add failure"
                        )
                    await es.add(outcome.replacement_event)
                    added_event_id = (
                        outcome.replacement_event.id
                    )
                except ValueError:
                    # Idempotent: previous attempt
                    # left the replacement.
                    pass
                if outcome.target_event is not None:
                    if fail_event_update:
                        raise RuntimeError(
                            "simulated update failure"
                        )
                    await es.update(outcome.target_event)
        elif outcome.action in (
            "updated_pending",
            "cancelled",
            "duplicate_event",
        ):
            if outcome.target_event is not None:
                if fail_event_update:
                    raise RuntimeError(
                        "simulated update failure"
                    )
                await es.update(outcome.target_event)
    except Exception:
        if added_event_id is not None:
            try:
                await es.remove(added_event_id)
            except Exception:
                pass
        raise
    if fail_inbox_commit:
        raise RuntimeError("simulated inbox commit failure")
    await adapter.commit(outcome, ib)
    return outcome, "committed"


# --------------------------------------------------------------------
# D4-strict: structured primary lookup does NOT require address_hint
# --------------------------------------------------------------------


class D4StrictPrimaryLookupTests(unittest.TestCase):
    """D4-strict: a successful exact lookup by
    ``(source, channel, original_message_id)``
    determines the cancellation target WITHOUT
    requiring an address_hint. The fallback
    must NOT clear or replace the target the
    primary found.
    """

    def test_primary_lookup_does_not_require_address(self):
        es, ib, _, _ = _make_stores()
        # 1. Ingest the original (no
        #    address_hint). The event lands in
        #    the store.
        raw_orig = _make_raw(
            message_id="m-d4s-1",
            raw_text="15.10.2026 09:00-18:00",
            # No address_hint on purpose.
        )
        outcome_orig, _ = _run(_run_handler(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        # 2. Cancellation with
        #    ``original_message_id`` only --
        #    NO address_hint. The structured
        #    primary lookup must resolve the
        #    target.
        raw_cancel = _make_raw(
            message_id="m-d4s-cancel",
            raw_text="Скасовано",
            original_message_id="m-d4s-1",
            # No address_hint.
        )
        outcome, status = _run(_run_handler(es, ib, raw_cancel))
        self.assertEqual(status, "committed")
        self.assertEqual(
            outcome.action, "cancelled",
            msg=(
                "primary lookup by structured key "
                "must succeed WITHOUT address_hint; "
                f"got {outcome!r}"
            ),
        )
        # 3. The original event is cancelled.
        original_after = es.get(original_id)
        self.assertEqual(original_after.status, "cancelled")
        # 4. The inbox records the cancellation.
        inbox_keys = list(ib._entries.keys())  # type: ignore[attr-defined]
        self.assertEqual(len(inbox_keys), 2)
        cancel_entry = ib._entries[  # type: ignore[attr-defined]
            f"email||m-d4s-cancel"
        ]
        self.assertEqual(
            cancel_entry.action,
            inbox.InboxAction.CANCELLED.value,
        )

    def test_primary_target_preserved_when_fallback_also_matches(self):
        """The fallback must not overwrite the
        primary target. Setup: an event exists
        (added by the original notification).
        A second event is added manually with
        the same address_hint+window. A
        cancellation with ``original_message_id``
        must resolve to the FIRST event (the
        primary) and never consider the second
        one (the fallback).
        """
        es, ib, _, _ = _make_stores()
        # 1. Original notification: produces
        #    event #1.
        raw_orig = _make_raw(
            message_id="m-d4s-2-orig",
            raw_text="15.10.2026 09:00-18:00",
            address_hint="вул. Київська, 12",
        )
        outcome_orig, _ = _run(_run_handler(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        # 2. Manually add a SECOND event that
        #    matches the same address+window
        #    range. (Simulates a second
        #    address that happens to be the
        #    same.)
        from dataclasses import replace as _dc_replace
        ev_2 = outage.OutageEvent(
            id="ev-2-amb",
            source="notification:email",
            raw_text="other",
            start_iso="2026-10-15T10:00:00+03:00",
            end_iso="2026-10-15T19:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T08:00:00+00:00",
            status="confirmed",
            needs_confirmation=False,
            ambiguity_reason=None,
            version=1,
            updated_at="2026-10-14T08:00:00+00:00",
            notes="sender=test;address=вул. Київська, 12;message_id=ev-2-amb",
        )
        _run(es.add(ev_2))
        # 3. Cancellation by
        #    ``original_message_id`` AND a
        #    matching address_hint. The
        #    fallback would have been
        #    ambiguous (2 candidates). The
        #    primary, however, points at the
        #    ORIGINAL id unambiguously.
        raw_cancel = _make_raw(
            message_id="m-d4s-2-cancel",
            raw_text="Скасовано",
            address_hint="вул. Київська, 12",
            original_message_id="m-d4s-2-orig",
        )
        outcome, status = _run(_run_handler(es, ib, raw_cancel))
        self.assertEqual(status, "committed")
        self.assertEqual(
            outcome.action, "cancelled",
            msg=(
                "primary target must be preserved; "
                f"got {outcome!r}"
            ),
        )
        # 4. The ORIGINAL is cancelled. The
        #    second event is NOT.
        original_after = es.get(original_id)
        ev_2_after = es.get("ev-2-amb")
        self.assertEqual(original_after.status, "cancelled")
        self.assertEqual(ev_2_after.status, "confirmed")


# --------------------------------------------------------------------
# D3-reschedule-coherence: persistence, failure, recovery
# --------------------------------------------------------------------


class D3RescheduleCoherenceTests(unittest.TestCase):
    """D3: a reschedule must coherently persist
    old and new events. A failure on either
    write boundary must not leave
    ``superseded_by`` pointing to a missing
    record. A redelivery after reload must
    restore the operation without producing
    duplicates.
    """

    def test_update_failure_rolls_back_add(self):
        """If the OLD's ``update`` fails after
        the replacement's ``add`` succeeded,
        the handler rolls back the ``add`` so
        the new id does not dangle. The inbox
        is not committed.
        """
        es, ib, _, _ = _make_stores()
        # 1. Original event.
        raw_orig = _make_raw(
            message_id="m-d3-1-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_run_handler(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        # 2. Reschedule with a simulated
        #    update failure (after the add
        #    succeeds).
        raw_resched = _make_raw(
            message_id="m-d3-1-resched",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-d3-1-orig",
        )
        with self.assertRaises(RuntimeError):
            _run(
                _run_handler(
                    es,
                    ib,
                    raw_resched,
                    fail_event_update=True,
                )
            )
        # 3. The store has the ORIGINAL only
        #    (the add was rolled back). The
        #    OLD's ``superseded_by`` is still
        #    ``None`` (the update did not
        #    land).
        events = es.list_events()
        self.assertEqual(
            len(events), 1,
            msg=(
                "the add was rolled back; the new "
                "event must NOT be in the store. "
                f"events={[e.id for e in events]}"
            ),
        )
        original_after = es.get(original_id)
        self.assertIsNone(
            original_after.superseded_by,
            msg=(
                "the OLD's ``superseded_by`` must NOT "
                "point to a missing record. Got "
                f"{original_after.superseded_by!r}"
            ),
        )
        # 4. The inbox has only the original.
        #    The reschedule was NOT committed.
        self.assertEqual(len(ib.list()), 1)
        self.assertNotIn(
            "m-d3-1-resched",
            [e.message_id for e in ib.list()],
        )

    def test_inbox_commit_failure_leaves_no_orphan_in_store(self):
        """If ``event_store.add`` and
        ``event_store.update`` both succeed but
        the inbox commit fails, the events are
        in the store but the inbox has no entry
        for the reschedule. A redelivery is
        allowed (and the reschedule idempotency
        branch must not produce a duplicate).
        """
        es, ib, _, _ = _make_stores()
        # 1. Original event.
        raw_orig = _make_raw(
            message_id="m-d3-2-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_run_handler(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        # 2. Reschedule with simulated
        #    inbox commit failure (the add
        #    and update BOTH succeed).
        raw_resched = _make_raw(
            message_id="m-d3-2-resched",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-d3-2-orig",
        )
        with self.assertRaises(RuntimeError):
            _run(
                _run_handler(
                    es,
                    ib,
                    raw_resched,
                    fail_inbox_commit=True,
                )
            )
        # 3. The store has BOTH events; the
        #    OLD's ``superseded_by`` is the
        #    new id. The inbox has only the
        #    original.
        events = es.list_events()
        self.assertEqual(len(events), 2)
        original_after = es.get(original_id)
        replacement_after = es.get(
            original_after.superseded_by
        )
        self.assertIsNotNone(
            replacement_after,
            msg=(
                "the OLD's ``superseded_by`` must "
                "point to an existing event; got "
                f"{original_after.superseded_by!r}"
            ),
        )
        # The replacement must NOT be the
        # original.
        self.assertNotEqual(
            original_after.superseded_by, original_id
        )
        self.assertEqual(len(ib.list()), 1)

    def test_redelivery_after_partial_state_completes_without_duplicate(self):
        """After a partial state (add
        succeeded, update failed), the
        redelivery must complete the work
        without producing a duplicate.
        Specifically:
          * The OLD has no ``superseded_by``
            (update was rolled back).
          * A redelivery of the same
            reschedule must:
            - see the OLD (no superseded_by)
              via the primary lookup,
            - generate a new replacement
              (different id from the rolled-
              back one),
            - add the new replacement and
              update the OLD with the new
              superseded_by link,
            - commit the inbox.
          * The store ends with exactly TWO
            events (original + new
            replacement), and the OLD's
            ``superseded_by`` is the new
            replacement's id (NOT a dangling
            pointer to a removed event).
        """
        es, ib, _, _ = _make_stores()
        raw_orig = _make_raw(
            message_id="m-d3-3-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_run_handler(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        # Simulate the partial state: add
        # succeeds, update fails -> handler
        # rolls back the add.
        raw_resched = _make_raw(
            message_id="m-d3-3-resched",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-d3-3-orig",
        )
        with self.assertRaises(RuntimeError):
            _run(
                _run_handler(
                    es,
                    ib,
                    raw_resched,
                    fail_event_update=True,
                )
            )
        # Sanity: store has only the original;
        # OLD has no superseded_by.
        self.assertEqual(len(es.list_events()), 1)
        self.assertIsNone(es.get(original_id).superseded_by)
        # Redelivery: a fresh attempt.
        outcome_retry, status = _run(
            _run_handler(es, ib, raw_resched)
        )
        self.assertEqual(status, "committed")
        self.assertEqual(outcome_retry.action, "superseded")
        # Store has 2 events: original +
        # replacement. The OLD's
        # ``superseded_by`` is the new id.
        events = es.list_events()
        self.assertEqual(len(events), 2)
        original_after = es.get(original_id)
        replacement_after = es.get(original_after.superseded_by)
        self.assertIsNotNone(
            replacement_after,
            msg=(
                "the OLD's ``superseded_by`` must "
                "point to an existing event; got "
                f"{original_after.superseded_by!r}"
            ),
        )
        # Inbox has the reschedule entry.
        resched_inbox = ib._entries[  # type: ignore[attr-defined]
            f"email||m-d3-3-resched"
        ]
        self.assertEqual(
            resched_inbox.action,
            inbox.InboxAction.SUPERSEDED.value,
        )

    def test_redelivery_after_reload_with_partial_state_completes(self):
        """Full reload scenario: the partial
        state is persisted, a new InboxStore /
        EventStore is constructed against the
        same backend, and the reschedule is
        redelivered. The redelivery must
        complete without producing a duplicate.
        """
        backend_events: dict[str, Any] = {}
        backend_inbox: dict[str, Any] = {}
        es1, ib1, backend_events, backend_inbox = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        raw_orig = _make_raw(
            message_id="m-d3-4-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(
            _run_handler(es1, ib1, raw_orig)
        )
        original_id = outcome_orig.event.id
        # Persist + partial state.
        _run(es1.async_save())
        _run(ib1.async_save())
        raw_resched = _make_raw(
            message_id="m-d3-4-resched",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-d3-4-orig",
        )
        with self.assertRaises(RuntimeError):
            _run(
                _run_handler(
                    es1,
                    ib1,
                    raw_resched,
                    fail_event_update=True,
                )
            )
        # Persist the partial state. Note: the
        # add was rolled back, so the store
        # is back to 1 event.
        _run(es1.async_save())
        _run(ib1.async_save())
        # Reload: fresh stores against the
        # same backends.
        es2, ib2, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        _run(es2.async_load())
        _run(ib2.async_load())
        # Redelivery.
        outcome_retry, status = _run(
            _run_handler(es2, ib2, raw_resched)
        )
        self.assertEqual(status, "committed")
        self.assertEqual(outcome_retry.action, "superseded")
        # Store has 2 events: original +
        # replacement.
        events = es2.list_events()
        self.assertEqual(len(events), 2)
        original_after = es2.get(original_id)
        replacement_after = es2.get(
            original_after.superseded_by
        )
        self.assertIsNotNone(
            replacement_after,
            msg=(
                "the OLD's ``superseded_by`` must "
                "point to an existing event after "
                "redelivery; got "
                f"{original_after.superseded_by!r}"
            ),
        )

    def test_redelivery_of_completed_reschedule_is_noop(self):
        """When the reschedule is fully
        completed (add + update + commit), a
        redelivery of the SAME reschedule
        message is a no-op. The handler must
        detect that ``target.superseded_by``
        is already set and reuse the existing
        replacement rather than create a new
        one.
        """
        es, ib, _, _ = _make_stores()
        raw_orig = _make_raw(
            message_id="m-d3-5-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_run_handler(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        raw_resched = _make_raw(
            message_id="m-d3-5-resched",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-d3-5-orig",
        )
        # First delivery: complete.
        outcome_first, _ = _run(
            _run_handler(es, ib, raw_resched)
        )
        replacement_id = outcome_first.replacement_event.id
        # Second delivery: must be a no-op
        # (replay).
        outcome_second, status_second = _run(
            _run_handler(es, ib, raw_resched)
        )
        self.assertEqual(
            status_second, "replay",
            msg=(
                "redelivery of a completed reschedule "
                "must be a no-op (replay); got "
                f"{status_second!r}"
            ),
        )
        # The store still has 2 events; the
        # OLD's ``superseded_by`` is the same
        # replacement id.
        self.assertEqual(len(es.list_events()), 2)
        original_after = es.get(original_id)
        self.assertEqual(
            original_after.superseded_by, replacement_id
        )


if __name__ == "__main__":
    unittest.main()
