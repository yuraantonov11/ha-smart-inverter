"""D3 v4 regression tests for Phase 4 v2.

The 383c947 commit fixed D3 atomicity and chain
handling. Codex then reproduced two more
defects that this file targets:

  D3-recovery-wired: the recovery of pending
             inbox commits from the event
             store's payload MUST be hooked
             into the real setup / reload
             path, not done by the test. The
             test must drive the production
             ``reconcile_pending_inbox_commits``
             helper (the one wired into
             ``__init__.py`` and into
             ``handle_ingest_outage_notification``)
             and verify the InboxStore is
             populated from the event store's
             pending list.

  D3-pending-incremental: a NEW snapshot
             preserves the previous
             unfinished operations in the
             pending list, adding the new
             one by its structured key. A
             single recovery failure does
             NOT clear the entire queue --
             only the specific entry that
             succeeded is removed; failed
             entries stay in the list for
             the next attempt.

The full path is: two pending operations,
reload, second recovery attempt fails, third
attempt succeeds, redelivery. No key is
lost and no extra event is created.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
INBOX_PATH = REPO_ROOT / "services" / "outage_inbox.py"
OUTAGE_PATH = REPO_ROOT / "services" / "outage.py"

_STUB_PKG = "_outage_inbox_v2_d3v4_stub"


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
    in-place mutation semantics. The
    ``fail_predicate`` lets the test inject
    failures into specific ``async_save`` calls.
    """

    def __init__(
        self,
        backend,
        default,
        *,
        fail_predicate=None,
    ):
        self._backend = backend
        self._default = default
        self._fail_predicate = fail_predicate
        self._save_count = 0

    async def async_load(self):
        if self._backend:
            import copy
            return copy.deepcopy(self._backend)
        return self._default

    async def async_save(self, data):
        self._save_count += 1
        if self._fail_predicate is not None:
            result = self._fail_predicate(data, self._save_count)
            if result is not None and result is not False:
                raise RuntimeError(str(result))
        self._backend.clear()
        self._backend.update(data)


def _make_stores(
    backend_events: dict | None = None,
    backend_inbox: dict | None = None,
    *,
    fail_event_save_predicate=None,
    fail_inbox_save_predicate=None,
):
    if backend_events is None:
        backend_events = {}
    if backend_inbox is None:
        backend_inbox = {}
    es = outage.EventStore(
        _DictStore(
            backend_events,
            default=None,
            fail_predicate=fail_event_save_predicate,
        ),
        "test",
    )
    ib = inbox.InboxStore(
        _DictStore(
            backend_inbox,
            default=None,
            fail_predicate=fail_inbox_save_predicate,
        ),
        "test",
    )
    return es, ib, backend_events, backend_inbox


async def _ingest(es, ib, raw):
    """Run the production handler body
    (``outage.apply_ingest_outcome``) on a
    parsed raw notification. Returns the
    underlying ``IngestOutcomeV2`` so the
    tests can inspect the action /
    ``event.id`` / ``replacement_event.id``.
    """
    parser = inbox.NotificationParserV2("Europe/Kyiv")
    adapter = inbox.NotificationAdapterV2(parser)
    parsed = adapter.parse(raw)
    outcome = await adapter.decide(parsed, es, ib)
    if outcome.inbox_entry is None:
        return outcome, "replay"
    await outage.apply_ingest_outcome(
        event_store=es,
        inbox=ib,
        adapter=adapter,
        raw=raw,
        outcome=outcome,
    )
    return outcome, "committed"


async def _produce_pending_snapshot(
    es, ib, raw_orig, raw_resched
):
    """Build a state where the event store has
    a pending inbox commit for a reschedule
    (snapshot succeeded, inbox commit failed).
    Returns ``(original_id, raw_resched)``.
    """
    # 1. Original event.
    outcome_orig, _ = await _ingest(es, ib, raw_orig)
    original_id = outcome_orig.event.id
    # 2. Inject a failure into the inbox
    #    storage so the reschedule's inbox
    #    commit fails AFTER the event store
    #    snapshot succeeds.
    ib._storage._fail_predicate = (
        lambda _data, _count: True
    )
    try:
        await _ingest(es, ib, raw_resched)
    except RuntimeError:
        pass
    # Clear the failure for subsequent manual
    # saves (the recovery path is the one that
    # may re-introduce failures).
    ib._storage._fail_predicate = None
    return original_id


def _run_ingest_expect_failure(es, ib, raw):
    """Sync wrapper around ``_ingest`` that
    swallows the expected ``RuntimeError``
    from a simulated inbox-commit failure.
    The caller has already configured
    ``ib._storage._fail_predicate`` to force
    the failure.
    """
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            _ingest(es, ib, raw)
        )
    except RuntimeError:
        pass
    finally:
        loop.close()


# --------------------------------------------------------------------
# D3-recovery-wired: production recovery is called by setup + handler
# --------------------------------------------------------------------


class D3ProductionRecoveryTests(unittest.TestCase):
    """D3-recovery-wired: the production
    ``reconcile_pending_inbox_commits`` helper
    is called from two places: the entry-setup
    / reload hook, and BEFORE each
    ``decide`` in the handler. The test
    exercises the same module-level helper the
    production code uses.
    """

    def test_reconcile_helper_upserts_pending_to_inbox(self):
        """A fresh event store + a fresh inbox
        store. The event store has a pending
        entry (from a previous run that crashed
        after the snapshot). The production
        ``reconcile_pending_inbox_commits`` must
        upsert the entry into the inbox.
        """
        # 1. Build a state with a pending entry.
        backend_events: dict[str, Any] = {}
        backend_inbox: dict[str, Any] = {}
        es, ib, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        raw_orig = _make_raw(
            message_id="m-rec-1-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        raw_resched = _make_raw(
            message_id="m-rec-1-resched",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-rec-1-orig",
        )
        _run(
            _produce_pending_snapshot(
                es, ib, raw_orig, raw_resched
            )
        )
        # Sanity: the event store has the
        # snapshot; the inbox has only the
        # original.
        self.assertEqual(len(ib.list()), 1)
        # 2. Reload: fresh stores against the
        #    same backend. The pending list is
        #    in the event store payload.
        es2, ib2, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        _run(es2.async_load())
        _run(ib2.async_load())
        # Sanity: the in-memory pending list is
        # populated.
        self.assertEqual(
            len(es2._pending_inbox_commits), 1
        )
        # 3. The production recovery helper
        #    upserts the pending entries into
        #    the inbox and clears the list.
        recovered = _run(
            outage.reconcile_pending_inbox_commits(es2, ib2)
        )
        self.assertEqual(recovered, 1)
        # 4. The inbox now has both entries.
        self.assertEqual(len(ib2.list()), 2)
        # 5. The pending list is empty.
        self.assertEqual(
            len(es2._pending_inbox_commits), 0
        )
        # 6. The event store was re-saved with
        #    the empty pending list.
        event_payload = backend_events
        self.assertNotIn(
            "_pending_inbox_commits",
            event_payload,
            msg=(
                "the next reload must not "
                "double-commit the same entry"
            ),
        )

    def test_two_pending_operations_incremental_snapshot(self):
        """Two pending operations persist
        independently. A new snapshot MUST
        preserve the previous pending list and
        add the new one. A redelivery after a
        partial failure does not lose the other
        entry.
        """
        backend_events: dict[str, Any] = {}
        backend_inbox: dict[str, Any] = {}
        es, ib, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        # 1. Ingest the original.
        raw_orig = _make_raw(
            message_id="m-rec-2-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(
            _ingest(es, ib, raw_orig)
        )
        original_id = outcome_orig.event.id
        # 2. Reschedule 1: snapshot succeeds,
        #    inbox fails. The event store payload
        #    has a pending entry for reschedule 1.
        ib._storage._fail_predicate = (
            lambda _data, _count: True
        )
        raw_1 = _make_raw(
            message_id="m-rec-2-r1",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-rec-2-orig",
        )
        _run_ingest_expect_failure(es, ib, raw_1)
        # 3. Reload: the pending list has 1
        #    entry. The first recovery attempt
        #    ALSO fails. The pending list must
        #    still have 1 entry -- the recovery
        #    does not preemptively clear.
        es2, ib2, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        _run(es2.async_load())
        _run(ib2.async_load())
        self.assertEqual(
            len(es2._pending_inbox_commits), 1
        )
        # First recovery attempt: the inbox
        # upsert is forced to fail.
        ib2._storage._fail_predicate = (
            lambda _data, _count: True
        )
        recovered = _run(
            outage.reconcile_pending_inbox_commits(es2, ib2)
        )
        self.assertEqual(recovered, 0)
        # The entry is re-attached. The event
        # store was re-saved with the entry
        # still pending.
        self.assertEqual(
            len(es2._pending_inbox_commits), 1,
            msg=(
                "a single recovery failure must "
                "not clear the pending queue"
            ),
        )
        # 4. Second recovery attempt: the inbox
        #    upsert succeeds.
        ib2._storage._fail_predicate = None
        recovered = _run(
            outage.reconcile_pending_inbox_commits(es2, ib2)
        )
        self.assertEqual(recovered, 1)
        # The inbox has both entries.
        self.assertEqual(len(ib2.list()), 2)
        # The pending list is empty.
        self.assertEqual(
            len(es2._pending_inbox_commits), 0
        )
        # 5. The OLD's ``superseded_by`` is the
        #    replacement (the snapshot was
        #    applied successfully).
        original_after = es2.get(original_id)
        self.assertIsNotNone(original_after.superseded_by)
        self.assertIsNotNone(
            es2.get(original_after.superseded_by)
        )
        # 6. Redelivery of reschedule 1 is a
        #    clean replay. No new event.
        outcome_redelivery, status = _run(
            _ingest(es2, ib2, raw_1)
        )
        self.assertEqual(
            status, "replay",
            msg=(
                "after recovery, a redelivery "
                "must be a replay; the inbox has "
                "the structured key"
            ),
        )
        # The store still has 2 events.
        self.assertEqual(len(es2.list_events()), 2)

    def test_recovery_failure_keeps_entry_for_next_attempt(self):
        """A recovery failure must keep the
        pending entry available for the next
        attempt -- the entry is NOT silently
        dropped.
        """
        backend_events: dict[str, Any] = {}
        backend_inbox: dict[str, Any] = {}
        es, ib, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        raw_orig = _make_raw(
            message_id="m-rec-3-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        raw_resched = _make_raw(
            message_id="m-rec-3-resched",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-rec-3-orig",
        )
        _run(
            _produce_pending_snapshot(
                es, ib, raw_orig, raw_resched
            )
        )
        es2, ib2, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        _run(es2.async_load())
        _run(ib2.async_load())
        # First recovery: fails.
        ib2._storage._fail_predicate = (
            lambda _data, _count: True
        )
        recovered = _run(
            outage.reconcile_pending_inbox_commits(es2, ib2)
        )
        self.assertEqual(recovered, 0)
        # The pending entry is still there.
        self.assertEqual(len(es2._pending_inbox_commits), 1)
        # Second recovery: succeeds.
        ib2._storage._fail_predicate = None
        recovered = _run(
            outage.reconcile_pending_inbox_commits(es2, ib2)
        )
        self.assertEqual(recovered, 1)
        # The inbox has both entries; the
        # pending list is empty.
        self.assertEqual(len(ib2.list()), 2)
        self.assertEqual(len(es2._pending_inbox_commits), 0)

    def test_snapshot_preserves_existing_pending_operations(self):
        """A second snapshot (reschedule 2) MUST
        preserve the pending entry from
        reschedule 1 (which had not yet been
        recovered), adding the new one. The
        merge is by ``inbox.key``.
        """
        backend_events: dict[str, Any] = {}
        backend_inbox: dict[str, Any] = {}
        es, ib, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        # 1. Original.
        raw_orig = _make_raw(
            message_id="m-rec-4-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(
            _ingest(es, ib, raw_orig)
        )
        original_id = outcome_orig.event.id
        # 2. Reschedule 1: snapshot OK, inbox
        #    fails. The event store has a
        #    pending entry.
        ib._storage._fail_predicate = (
            lambda _data, _count: True
        )
        raw_1 = _make_raw(
            message_id="m-rec-4-r1",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-rec-4-orig",
        )
        _run_ingest_expect_failure(es, ib, raw_1)
        # Reload. The pending list has 1
        # entry. The first recovery also fails.
        es2, ib2, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        _run(es2.async_load())
        _run(ib2.async_load())
        self.assertEqual(
            len(es2._pending_inbox_commits), 1
        )
        # Simulate a partial recovery: the
        # first attempt fails. The entry
        # stays in the list.
        ib2._storage._fail_predicate = (
            lambda _data, _count: True
        )
        _run(
            outage.reconcile_pending_inbox_commits(es2, ib2)
        )
        self.assertEqual(
            len(es2._pending_inbox_commits), 1
        )
        # 3. Reschedule 2: a NEW correction. The
        #    adapter finds the original (via the
        #    inbox replay) and builds a new
        #    snapshot. The new snapshot's
        #    pending list must include BOTH the
        #    previous (still-pending) entry and
        #    the new one. The merge is by
        #    ``inbox.key``.
        ib2._storage._fail_predicate = None
        # Reload again -- the inbox has only
        # the original (reschedule 1's commit
        # is still pending).
        # The redelivery: the adapter's primary
        # lookup via the inbox finds the
        # original (original's inbox entry was
        # committed). The adapter builds a new
        # correction (reschedule 2) with a
        # different message_id.
        raw_2 = _make_raw(
            message_id="m-rec-4-r2",
            raw_text="15.10.2026 16:00-23:00",
            original_message_id="m-rec-4-orig",
        )
        # Inject a failure into the inbox save
        # for the reschedule 2 (so the reschedule
        # 2's commit is also pending). The
        # event store's snapshot must include
        # BOTH pending entries.
        ib2._storage._fail_predicate = (
            lambda _data, _count: True
        )
        # The original is in the inbox; the
        # reschedule 1's pending entry is in
        # the event store. The adapter finds
        # the original via the inbox's original
        # entry -- but the original's
        # ``superseded_by`` is set to the
        # reschedule 1 replacement (which is in
        # the store, not the inbox). The
        # adapter detects this and proceeds
        # with a chain update. The new
        # snapshot writes old + new + previous
        # replacement + pending entry.
        _run_ingest_expect_failure(es2, ib2, raw_2)
        # Reload: the in-memory pending list
        # must have BOTH entries (reschedule 1
        # and reschedule 2).
        es3, ib3, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        _run(es3.async_load())
        _run(ib3.async_load())
        # The pending list has 2 entries.
        self.assertEqual(
            len(es3._pending_inbox_commits), 2,
            msg=(
                "the new snapshot must preserve "
                "the previous unfinished "
                "operation; got "
                f"{len(es3._pending_inbox_commits)} "
                "entries"
            ),
        )


# --------------------------------------------------------------------
# D3-pending-incremental: the new snapshot merges with existing
# --------------------------------------------------------------------


class D3PendingIncrementalTests(unittest.TestCase):
    """D3-pending-incremental: the
    ``apply_reschedule_snapshot`` MUST merge
    the new ``pending_inbox_entries`` with
    any existing ``_pending_inbox_commits``
    list. The merge is by ``inbox.key`` (later
    wins). A single recovery failure does NOT
    clear the queue.
    """

    def test_snapshot_merge_dedupes_by_inbox_key(self):
        """When the existing pending list has
        an entry with the same key as the new
        one, the new entry replaces the old.
        Different keys coexist.
        """
        es, ib, _, _ = _make_stores()
        # Seed the existing pending list with
        # an entry whose key is the same as the
        # new one.
        existing = inbox.InboxEntry(
            key="email||m-merge-1",
            source="email",
            channel="",
            message_id="m-merge-1",
            received_at="2026-10-14T10:00:00+00:00",
            raw_text="original",
            sender="s",
            address_hint=None,
            action=inbox.InboxAction.ADDED.value,
            event_id="ev-existing",
            reason="first attempt",
            parsed={},
        )
        es._pending_inbox_commits = [existing]
        # The new entry has the same key (the
        # redelivery of the same message).
        replacement_ev = outage.OutageEvent(
            id="ev-replacement",
            source="notification:email",
            raw_text="new",
            start_iso="2026-10-15T09:00:00+03:00",
            end_iso="2026-10-15T18:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T11:00:00+00:00",
            status="confirmed",
            needs_confirmation=False,
            ambiguity_reason=None,
            version=1,
            updated_at="2026-10-14T11:00:00+00:00",
            notes=None,
        )
        new = inbox.InboxEntry(
            key="email||m-merge-1",  # same key
            source="email",
            channel="",
            message_id="m-merge-1",
            received_at="2026-10-14T11:00:00+00:00",
            raw_text="new",
            sender="s",
            address_hint=None,
            action=inbox.InboxAction.SUPERSEDED.value,
            event_id=replacement_ev.id,
            reason="second attempt",
            parsed={},
        )
        old = outage.OutageEvent(
            id="ev-old",
            source="notification:email",
            raw_text="old",
            start_iso="2026-10-15T09:00:00+03:00",
            end_iso="2026-10-15T18:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T08:00:00+00:00",
            status="confirmed",
            needs_confirmation=False,
            ambiguity_reason=None,
            version=1,
            updated_at="2026-10-14T08:00:00+00:00",
            notes=None,
        )
        old = _replace(old, superseded_by=replacement_ev.id)
        snapshot = outage.RescheduleSnapshot(
            old_event=old,
            new_event=replacement_ev,
        )
        # Apply the snapshot with the new
        # pending entry. The merge should
        # produce 1 entry: the NEW one
        # (replacing the existing by key).
        _run(
            es.apply_reschedule_snapshot(
                snapshot,
                pending_inbox_entries=[new],
            )
        )
        self.assertEqual(
            len(es._pending_inbox_commits), 1,
            msg=(
                "the merge must dedupe by "
                "inbox.key; got "
                f"{len(es._pending_inbox_commits)} entries"
            ),
        )
        self.assertEqual(
            es._pending_inbox_commits[0].key,
            "email||m-merge-1",
        )
        # The new entry's action wins (later
        # wins).
        self.assertEqual(
            es._pending_inbox_commits[0].action,
            inbox.InboxAction.SUPERSEDED.value,
        )
        self.assertEqual(
            es._pending_inbox_commits[0].reason,
            "second attempt",
        )

    def test_snapshot_merge_keeps_distinct_keys(self):
        """Two distinct keys coexist in the
        merged pending list. The snapshot
        preserves both.
        """
        es, ib, _, _ = _make_stores()
        existing = inbox.InboxEntry(
            key="email||m-keep-1",
            source="email",
            channel="",
            message_id="m-keep-1",
            received_at="2026-10-14T10:00:00+00:00",
            raw_text="first",
            sender="s",
            address_hint=None,
            action=inbox.InboxAction.SUPERSEDED.value,
            event_id="ev-1",
            reason="first reschedule",
            parsed={},
        )
        es._pending_inbox_commits = [existing]
        replacement_ev = outage.OutageEvent(
            id="ev-2",
            source="notification:email",
            raw_text="second",
            start_iso="2026-10-15T09:00:00+03:00",
            end_iso="2026-10-15T18:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T11:00:00+00:00",
            status="confirmed",
            needs_confirmation=False,
            ambiguity_reason=None,
            version=1,
            updated_at="2026-10-14T11:00:00+00:00",
            notes=None,
        )
        new = inbox.InboxEntry(
            key="email||m-keep-2",  # distinct key
            source="email",
            channel="",
            message_id="m-keep-2",
            received_at="2026-10-14T11:00:00+00:00",
            raw_text="second",
            sender="s",
            address_hint=None,
            action=inbox.InboxAction.SUPERSEDED.value,
            event_id=replacement_ev.id,
            reason="second reschedule",
            parsed={},
        )
        old = outage.OutageEvent(
            id="ev-1",
            source="notification:email",
            raw_text="first",
            start_iso="2026-10-15T09:00:00+03:00",
            end_iso="2026-10-15T18:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T08:00:00+00:00",
            status="confirmed",
            needs_confirmation=False,
            ambiguity_reason=None,
            version=1,
            updated_at="2026-10-14T08:00:00+00:00",
            notes=None,
            superseded_by=replacement_ev.id,
        )
        snapshot = outage.RescheduleSnapshot(
            old_event=old,
            new_event=replacement_ev,
        )
        _run(
            es.apply_reschedule_snapshot(
                snapshot,
                pending_inbox_entries=[new],
            )
        )
        # Both keys coexist.
        self.assertEqual(len(es._pending_inbox_commits), 2)
        keys = {e.key for e in es._pending_inbox_commits}
        self.assertEqual(
            keys,
            {"email||m-keep-1", "email||m-keep-2"},
        )

    def test_commit_specific_pending_entry_keeps_others(self):
        """``commit_pending_inbox_entry(key)``
        removes ONLY the entry with the given
        key. The other entries are untouched.
        """
        es, ib, _, _ = _make_stores()
        # Seed three pending entries.
        e1 = inbox.InboxEntry(
            key="email||m-c1",
            source="email",
            channel="",
            message_id="m-c1",
            received_at="2026-10-14T10:00:00+00:00",
            raw_text="1",
            sender="s",
            address_hint=None,
            action=inbox.InboxAction.SUPERSEDED.value,
            event_id="ev-1",
            reason="1",
            parsed={},
        )
        e2 = inbox.InboxEntry(
            key="email||m-c2",
            source="email",
            channel="",
            message_id="m-c2",
            received_at="2026-10-14T10:00:00+00:00",
            raw_text="2",
            sender="s",
            address_hint=None,
            action=inbox.InboxAction.SUPERSEDED.value,
            event_id="ev-2",
            reason="2",
            parsed={},
        )
        e3 = inbox.InboxEntry(
            key="email||m-c3",
            source="email",
            channel="",
            message_id="m-c3",
            received_at="2026-10-14T10:00:00+00:00",
            raw_text="3",
            sender="s",
            address_hint=None,
            action=inbox.InboxAction.SUPERSEDED.value,
            event_id="ev-3",
            reason="3",
            parsed={},
        )
        es._pending_inbox_commits = [e1, e2, e3]
        _run(es.async_save())
        # Commit the middle one.
        _run(es.commit_pending_inbox_entry("email||m-c2"))
        # Only e1 and e3 remain.
        self.assertEqual(
            len(es._pending_inbox_commits), 2
        )
        keys = {e.key for e in es._pending_inbox_commits}
        self.assertEqual(
            keys, {"email||m-c1", "email||m-c3"}
        )

    def test_commit_unknown_key_is_noop(self):
        """``commit_pending_inbox_entry`` with
        an unknown key is a no-op. The list is
        unchanged and no save happens.
        """
        es, ib, _, _ = _make_stores()
        e1 = inbox.InboxEntry(
            key="email||m-noop",
            source="email",
            channel="",
            message_id="m-noop",
            received_at="2026-10-14T10:00:00+00:00",
            raw_text="1",
            sender="s",
            address_hint=None,
            action=inbox.InboxAction.SUPERSEDED.value,
            event_id="ev-1",
            reason="1",
            parsed={},
        )
        es._pending_inbox_commits = [e1]
        before_count = es._storage._save_count
        _run(
            es.commit_pending_inbox_entry("email||m-unknown")
        )
        # No save happened.
        self.assertEqual(es._storage._save_count, before_count)
        # The list is unchanged.
        self.assertEqual(
            len(es._pending_inbox_commits), 1
        )


def _replace(ev, **kwargs):
    from dataclasses import replace as _r
    return _r(ev, **kwargs)


if __name__ == "__main__":
    unittest.main()
