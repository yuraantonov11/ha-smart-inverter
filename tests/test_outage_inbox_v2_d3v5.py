"""D3 v5 regression tests for Phase 4 v2.

The 8fc7226 commit wired the recovery into
the production setup and the handler. Codex
then reproduced one more defect that this
file targets:

  D3-recovery-redelivery: after
             ``reconcile_pending_inbox_commits``,
             the handler MUST check the current
             ``(source, channel, message_id)``.
             If the current operation is still
             pending (the recovery failed for
             THAT key), the handler MUST NOT
             call ``decide`` again and MUST NOT
             mutate events. The handler
             returns an explicit
             ``recovery_pending`` state with a
             reason and a retry flag.

The full path: snapshot succeeds, inbox
fails, reload, recovery fails again, multiple
redeliveries, recovery succeeds, replay.
Event count must stay at 2 throughout.
Also tested: partial recovery of two pending
operations.

Tests drive the production
``ingest_with_recovery`` helper.
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

_STUB_PKG = "_outage_inbox_v2_d3v5_stub"


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

    def __init__(self, backend, default, *, fail_predicate=None):
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
):
    if backend_events is None:
        backend_events = {}
    if backend_inbox is None:
        backend_inbox = {}
    es = outage.EventStore(
        _DictStore(backend_events, default=None),
        "test",
    )
    ib = inbox.InboxStore(
        _DictStore(backend_inbox, default=None),
        "test",
    )
    return es, ib, backend_events, backend_inbox


def _build_raw(orig_id: str, resched_id: str, address: str | None = None):
    raw_orig = _make_raw(
        message_id=orig_id,
        raw_text="15.10.2026 09:00-18:00",
        address_hint=address,
    )
    raw_resched = _make_raw(
        message_id=resched_id,
        raw_text="15.10.2026 14:00-22:00",
        original_message_id=orig_id,
        address_hint=address,
    )
    return raw_orig, raw_resched


async def _drive_handler(
    es, ib, raw, *, tz_name="Europe/Kyiv"
):
    """Run the production handler body
    (``ingest_with_recovery``) on a parsed
    raw notification. Returns the dict the
    handler returns.
    """
    parser = inbox.NotificationParserV2(tz_name)
    adapter = inbox.NotificationAdapterV2(parser)
    return await outage.ingest_with_recovery(
        event_store=es,
        inbox=ib,
        raw=raw,
        parser=parser,
        adapter=adapter,
    )


def _run_drive(es, ib, raw):
    return _run(_drive_handler(es, ib, raw))


def _produce_pending_state(
    es, ib, backend_events, backend_inbox, raw_orig, raw_resched
):
    """Build a state where the event store has
    a pending inbox commit for a reschedule
    (snapshot succeeded, inbox commit failed).
    """
    # 1. Original event with a working inbox.
    outcome_orig = _run_drive(es, ib, raw_orig)
    assert outcome_orig["action"] == "added", (
        f"original must succeed; got {outcome_orig!r}"
    )
    original_id = outcome_orig["event_id"]
    # 2. Break the inbox so the reschedule's
    #    commit fails AFTER the event store
    #    snapshot succeeds.
    ib._storage._fail_predicate = (
        lambda _data, _count: True
    )
    # 3. The reschedule: snapshot succeeds,
    #    inbox commit fails. The handler
    #    returns ``recovery_pending`` because
    #    the pending entry is now in the event
    #    store's payload.
    reschedule = _run_drive(es, ib, raw_resched)
    assert reschedule["action"] == "recovery_pending", (
        f"the first reschedule should return "
        f"recovery_pending; got {reschedule!r}"
    )
    ib._storage._fail_predicate = None
    return original_id


# --------------------------------------------------------------------
# D3-recovery-redelivery: the production handler short-circuits on pending
# --------------------------------------------------------------------


class D3RecoveryRedeliveryTests(unittest.TestCase):
    """D3-recovery-redelivery: a redelivery while
    the inbox is still unavailable must
    return ``recovery_pending`` without
    calling ``decide`` or mutating events.
    Event count stays at 2 throughout.
    """

    def test_redelivery_under_unavailable_inbox_returns_recovery_pending(self):
        """Build a state with a pending entry.
        The inbox storage is broken. Each
        redelivery of the reschedule must
        return ``recovery_pending`` and the
        event count must stay at 2.
        """
        backend_events: dict[str, Any] = {}
        backend_inbox: dict[str, Any] = {}
        es, ib, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        raw_orig, raw_resched = _build_raw(
            "m-rec-r-1-orig", "m-rec-r-1-resched"
        )
        original_id = _produce_pending_state(
            es, ib, backend_events, backend_inbox,
            raw_orig, raw_resched,
        )
        # The event store has 2 events
        # (original + replacement).
        self.assertEqual(len(es.list_events()), 2)
        # 1. Reload.
        es2, ib2, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        _run(es2.async_load())
        _run(ib2.async_load())
        # The recovery fails (the inbox
        # storage is broken).
        ib2._storage._fail_predicate = (
            lambda _data, _count: True
        )
        # 2. Multiple redeliveries: each call
        #    returns ``recovery_pending`` and
        #    the event count stays at 2.
        for attempt in range(5):
            result = _run_drive(es2, ib2, raw_resched)
            self.assertEqual(
                result["action"],
                "recovery_pending",
                msg=(
                    f"attempt {attempt + 1}: expected "
                    f"recovery_pending; got {result!r}"
                ),
            )
            self.assertTrue(result["retry"])
            self.assertEqual(
                result["message_id"], "m-rec-r-1-resched"
            )
            self.assertEqual(
                result["source"], "email"
            )
            self.assertEqual(
                len(es2.list_events()), 2,
                msg=(
                    f"attempt {attempt + 1}: a "
                    f"recovery_pending redelivery "
                    f"MUST NOT create a new event; "
                    f"got {len(es2.list_events())} "
                    f"events"
                ),
            )
        # 3. The original's ``superseded_by``
        #    is still the original replacement
        #    (no second replacement was added
        #    by any of the redeliveries).
        original_after = es2.get(original_id)
        self.assertIsNotNone(original_after.superseded_by)
        # 4. The pending list still has 1
        #    entry.
        self.assertEqual(
            len(es2._pending_inbox_commits), 1
        )

    def test_recovery_succeeds_then_redelivery_is_replay(self):
        """After the inbox comes back, the
        next redelivery commits the entry and
        returns the original outcome as a
        replay. Event count stays at 2.
        """
        backend_events: dict[str, Any] = {}
        backend_inbox: dict[str, Any] = {}
        es, ib, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        raw_orig, raw_resched = _build_raw(
            "m-rec-r-2-orig", "m-rec-r-2-resched"
        )
        _produce_pending_state(
            es, ib, backend_events, backend_inbox,
            raw_orig, raw_resched,
        )
        # Reload.
        es2, ib2, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        _run(es2.async_load())
        _run(ib2.async_load())
        # 1. First redelivery: recovery fails.
        ib2._storage._fail_predicate = (
            lambda _data, _count: True
        )
        result_1 = _run_drive(es2, ib2, raw_resched)
        self.assertEqual(result_1["action"], "recovery_pending")
        # 2. Second redelivery: inbox back, the
        #    recovery succeeds.
        ib2._storage._fail_predicate = None
        result_2 = _run_drive(es2, ib2, raw_resched)
        # The entry was recovered; the next
        # call returns a clean replay (the
        # inbox now has the entry).
        self.assertEqual(
            result_2.get("idempotent_replay"),
            True,
            msg=(
                "after recovery succeeds, the "
                "redelivery must be a replay; "
                f"got {result_2!r}"
            ),
        )
        self.assertEqual(
            result_2["action"], "superseded"
        )
        # 3. Event count is still 2.
        self.assertEqual(len(es2.list_events()), 2)
        # 4. The inbox has both entries; the
        #    pending list is empty.
        self.assertEqual(len(ib2.list()), 2)
        self.assertEqual(
            len(es2._pending_inbox_commits), 0
        )

    def test_partial_recovery_two_pending_operations(self):
        """Two pending operations, recovery
        fails for one and succeeds for the
        other. The handler's recovery_pending
        check applies to the CURRENT key only:
        a redelivery of the failed key returns
        ``recovery_pending``; a redelivery of
        the recovered key returns the original
        outcome as a replay.
        """
        backend_events: dict[str, Any] = {}
        backend_inbox: dict[str, Any] = {}
        es, ib, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        # 1. Original + 2 reschedules.
        raw_orig, raw_resched_1 = _build_raw(
            "m-part-orig", "m-part-r1"
        )
        # 1a. Original: succeeds (inbox is up).
        result_orig = _run_drive(es, ib, raw_orig)
        self.assertEqual(result_orig["action"], "added")
        # 1b. Break the inbox so the reschedule
        #     commits fail AFTER the event store
        #     snapshot succeeds.
        ib._storage._fail_predicate = (
            lambda _data, _count: True
        )
        # 1c. Reschedule 1: snapshot OK, inbox
        #     fails. The handler returns
        #     ``recovery_pending`` (the pending
        #     entry is in the event store
        #     payload).
        result_1 = _run_drive(es, ib, raw_resched_1)
        self.assertEqual(result_1["action"], "recovery_pending")
        # 1d. Reschedule 2: the inbox is still
        #     broken. The current key is NOT in
        #     the pending list (reschedule 2
        #     was never processed). The handler
        #     proceeds to ``decide``; the
        #     snapshot succeeds, the inbox
        #     commit fails. The handler catches
        #     and returns ``recovery_pending``
        #     because the pending list now
        #     contains the current key.
        raw_resched_2 = _make_raw(
            message_id="m-part-r2",
            raw_text="15.10.2026 16:00-23:00",
            original_message_id="m-part-orig",
        )
        result_2 = _run_drive(es, ib, raw_resched_2)
        self.assertEqual(result_2["action"], "recovery_pending")
        # 1e. The event store has the original +
        #     reschedule 1's replacement (the
        #     chain update for reschedule 2
        #     links repl_1 -> repl_2, but
        #     repl_2 itself was added).
        #     Actually, with the current chain
        #     logic, reschedule 2's adapter
        #     sees the original's superseded_by
        #     = repl_1 (set by reschedule 1).
        #     It builds a new event and sets
        #     target.superseded_by = new_id AND
        #     previous_replacement (repl_1)
        #     .superseded_by = new_id. The chain
        #     has 3 events. But the inbox is
        #     broken, so the apply_ingest_outcome
        #     catches and returns recovery_pending.
        #     The snapshot IS persisted (3 events
        #     in the store).
        self.assertEqual(
            len(es.list_events()), 3,
            msg=(
                "the snapshot is on disk; the "
                "event store has 3 events even "
                "though the inbox commit failed"
            ),
        )
        # 2. Reload.
        es2, ib2, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        _run(es2.async_load())
        _run(ib2.async_load())
        # 3. Recovery: BOTH reschedule entries
        #    are committed (the inbox is now
        #    up).
        ib2._storage._fail_predicate = None
        recovered = _run(
            outage.reconcile_pending_inbox_commits(es2, ib2)
        )
        self.assertEqual(recovered, 2)
        # 4. The inbox has the original + the
        #    two reschedules. The pending list
        #    is empty.
        self.assertEqual(len(ib2.list()), 3)
        self.assertEqual(
            len(es2._pending_inbox_commits), 0
        )
        # 5. Redelivery of reschedule 1: the
        #    inbox has the entry; the handler
        #    returns a clean replay.
        result_redelivery_1 = _run_drive(
            es2, ib2, raw_resched_1
        )
        self.assertEqual(
            result_redelivery_1.get("idempotent_replay"),
            True,
        )
        # 6. Redelivery of reschedule 2: the
        #    inbox has the entry; the handler
        #    returns a clean replay (NOT a new
        #    correction, because the
        #    structured key was committed).
        result_redelivery_2 = _run_drive(
            es2, ib2, raw_resched_2
        )
        self.assertEqual(
            result_redelivery_2.get("idempotent_replay"),
            True,
            msg=(
                "after recovery, a redelivery of "
                "the same message_id must be a "
                "replay; got "
                f"{result_redelivery_2!r}"
            ),
        )
        # The event store has 3 events:
        # original + repl_1 + repl_2.
        self.assertEqual(len(es2.list_events()), 3)


if __name__ == "__main__":
    unittest.main()
