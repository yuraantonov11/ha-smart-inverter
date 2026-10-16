"""D3 v3 regression tests for Phase 4 v2.

The 06ea9ad commit fixed D3 atomicity with a
``RescheduleSnapshot`` + a single
``async_save``. Codex then reproduced more
defects that this file targets:

  D3-persistence-key: the structured
             ``(source, channel, message_id)``
             key MUST be persisted alongside
             the snapshot. When the inbox
             commit fails after the snapshot
             succeeded, a redelivery after
             reload must find the operation
             already done -- via the
             ``EventStore._pending_inbox_commits``
             list -- and finalise the inbox
             without creating a new event.

  D3-chain: a real chain
             ``original -> repl_1 -> repl_2``
             must be formed when the operator
             sends two reschedules with
             different ``message_id``s. The
             second reschedule marks the
             previous replacement as
             superseded. Cancelling the last
             replacement does NOT re-activate
             the old intervals.

The tests drive the REAL production handler
body (``outage.apply_ingest_outcome``) so the
same decide / snapshot / inbox-commit / clear
sequencing is exercised in both paths.
Failures are injected into the backing
storage's ``async_save`` -- NOT before the
call -- to exercise the atomicity guarantee.
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

_STUB_PKG = "_outage_inbox_v2_d3v3_stub"


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
    ``fail_on_save`` flag and ``save_attempts``
    counter let the test inject failures into
    the backing storage's ``async_save`` -- the
    same boundary the production code uses.
    """

    def __init__(
        self,
        backend,
        default,
        *,
        fail_on_save: bool = False,
        save_attempts: int = 0,
        fail_predicate=None,
    ):
        self._backend = backend
        self._default = default
        self._fail_on_save = fail_on_save
        self._save_attempts = save_attempts
        self._fail_predicate = fail_predicate
        self._save_count = 0

    async def async_load(self):
        if self._backend:
            import copy
            return copy.deepcopy(self._backend)
        return self._default

    async def async_save(self, data):
        self._save_count += 1
        if (
            self._fail_predicate is not None
            and self._fail_predicate(data, self._save_count)
        ):
            raise RuntimeError(
                f"simulated save failure on attempt "
                f"{self._save_count}"
            )
        if self._save_attempts and self._save_count <= self._save_attempts:
            raise RuntimeError(
                f"simulated save failure on attempt "
                f"{self._save_count}/{self._save_attempts}"
            )
        if self._fail_on_save:
            raise RuntimeError("simulated save failure")
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


async def _decide(es, ib, raw):
    parser = inbox.NotificationParserV2("Europe/Kyiv")
    adapter = inbox.NotificationAdapterV2(parser)
    parsed = adapter.parse(raw)
    return await adapter.decide(parsed, es, ib), adapter, parsed


async def _ingest(es, ib, raw):
    """Run the production handler body
    (``outage.apply_ingest_outcome``) on a
    parsed raw notification. The handler does
    decide + event-mutate + commit + (on
    supersession success) clear pending.
    Returns the (dict, status) the handler
    returns.
    """
    outcome, adapter, parsed = await _decide(es, ib, raw)
    if outcome.inbox_entry is None:
        return outcome, "replay"
    return (
        await outage.apply_ingest_outcome(
            event_store=es,
            inbox=ib,
            adapter=adapter,
            raw=raw,
            outcome=outcome,
        ),
        "committed",
    )


async def _ingest_outcome(es, ib, raw):
    """Same as ``_ingest`` but returns the
    underlying ``IngestOutcomeV2`` so the
    tests can inspect the action /
    ``event.id`` / ``replacement_event.id``.
    """
    outcome, adapter, parsed = await _decide(es, ib, raw)
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


# --------------------------------------------------------------------
# D3-persistence-key: structured key in the snapshot, recoverable
# --------------------------------------------------------------------


class D3PersistenceKeyTests(unittest.TestCase):
    """D3-persistence-key: the structured
    ``(source, channel, message_id)`` key is
    persisted alongside the snapshot. After
    a snapshot-success / inbox-commit-failure,
    a reload must finalise the inbox from
    ``EventStore._pending_inbox_commits`` and
    a redelivery must be a clean replay.
    """

    def test_snapshot_persists_pending_inbox_commit(self):
        """The EventStore payload must include
        ``_pending_inbox_commits`` after a
        successful snapshot save. The
        ``InboxStore`` is NOT committed yet
        (the handler commits the inbox AFTER
        the snapshot); the pending list is
        the durable record.
        """
        backend_events: dict[str, Any] = {}
        backend_inbox: dict[str, Any] = {}
        es, ib, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        # 1. Original event (saves succeed).
        raw_orig = _make_raw(
            message_id="m-pk-1-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_ingest_outcome(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        # 2. Reschedule: snapshot succeeds, but
        #    the inbox commit fails. The
        #    EventStore payload MUST carry the
        #    pending inbox entry so a reload
        #    can find the operation already
        #    done.
        def fail_inbox(_data, _count):
            return True
        # Force the inbox to fail: mutate the
        # storage's predicate at the right
        # moment. The original ingestion
        # already committed. The reschedule
        # will: snapshot save (succeeds),
        # inbox save (we want this to fail).
        # The EventStore snapshot is committed
        # before the inbox commit; we need to
        # fail the inbox commit only.
        inbox_storage = ib._storage
        es_storage = es._storage
        es_storage._fail_predicate = None
        inbox_storage._fail_predicate = fail_inbox
        raw_resched = _make_raw(
            message_id="m-pk-1-resched",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-pk-1-orig",
        )
        with self.assertRaises(RuntimeError):
            _run(_ingest_outcome(es, ib, raw_resched))
        # The EventStore payload now has the
        # snapshot AND a pending inbox commit.
        # The InboxStore has only the original
        # entry (the reschedule's commit
        # failed).
        self.assertEqual(len(ib.list()), 1)
        # Reload: the EventStore's
        # ``_pending_inbox_commits`` is
        # populated with the reschedule's
        # inbox entry.
        es2, ib2, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        _run(es2.async_load())
        pending = es2.take_pending_inbox_commits()
        self.assertEqual(
            len(pending), 1,
            msg=(
                "the snapshot must persist the "
                "structured inbox key for "
                "recovery on reload"
            ),
        )
        # 3. The pending entry has the correct
        #    ``(source, channel, message_id)``
        #    triple.
        pending_entry = pending[0]
        self.assertEqual(
            pending_entry.source,
            inbox.NotificationSourceV2.EMAIL.value,
        )
        self.assertEqual(pending_entry.channel, "")
        self.assertEqual(
            pending_entry.message_id, "m-pk-1-resched"
        )
        # 4. The pending entry is a SUPERSEDED
        #    action pointing at the
        #    replacement.
        self.assertEqual(
            pending_entry.action,
            inbox.InboxAction.SUPERSEDED.value,
        )
        self.assertIsNotNone(pending_entry.event_id)
        self.assertIsNotNone(pending_entry.target_event_id)
        # 5. The replacement event is in the
        #    event store (the snapshot is on
        #    disk; the inbox commit is the
        #    only piece that failed).
        self.assertEqual(len(es2.list_events()), 2)
        original_after = es2.get(original_id)
        self.assertIsNotNone(original_after.superseded_by)
        self.assertIsNotNone(
            es2.get(original_after.superseded_by)
        )

    def test_redelivery_after_inbox_failure_finalises_inbox_no_duplicate(self):
        """The full redelivery path:
          1. Ingest original.
          2. Ingest reschedule: snapshot OK,
             inbox fails.
          3. Persist + reload.
          4. On reload, push the pending inbox
             commit to the InboxStore (the
             ``take_pending_inbox_commits``
             returns the entries; the entry
             setup upserts them into the
             InboxStore).
          5. The InboxStore now has the
             reschedule entry.
          6. Redelivery of the SAME reschedule:
             the adapter sees the inbox has the
             entry and returns a replay
             (idempotent). No new event is
             created.
        """
        backend_events: dict[str, Any] = {}
        backend_inbox: dict[str, Any] = {}
        es, ib, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        raw_orig = _make_raw(
            message_id="m-pk-2-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_ingest_outcome(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        # Force the inbox commit to fail on the
        # reschedule. The event-store snapshot
        # still succeeds.
        ib._storage._fail_predicate = (
            lambda _data, _count: True
        )
        raw_resched = _make_raw(
            message_id="m-pk-2-resched",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-pk-2-orig",
        )
        with self.assertRaises(RuntimeError):
            _run(_ingest_outcome(es, ib, raw_resched))
        # Persist and reload. Clear the
        # predicate so the manual ``async_save``
        # below succeeds.
        ib._storage._fail_predicate = None
        _run(es.async_save())
        _run(ib.async_save())
        es2, ib2, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        _run(es2.async_load())
        _run(ib2.async_load())
        # The reload hook reads the pending
        # list and upserts each entry into the
        # InboxStore.
        for entry in es2.take_pending_inbox_commits():
            _run(ib2.upsert(entry))
        # Inbox now has both entries.
        self.assertEqual(len(ib2.list()), 2)
        # Redelivery of the same reschedule:
        # must be a clean replay. No new event.
        es2._storage._fail_predicate = None
        ib2._storage._fail_predicate = None
        outcome_retry, status = _run(
            _ingest(es2, ib2, raw_resched)
        )
        self.assertEqual(
            status, "replay",
            msg=(
                "redelivery after reload must be a "
                "replay; the structured key in "
                "the inbox blocks the new "
                "correction"
            ),
        )
        # The store still has 2 events: the
        # original and the FIRST replacement
        # (no second replacement was created
        # by the redelivery).
        self.assertEqual(len(es2.list_events()), 2)
        original_after = es2.get(original_id)
        self.assertIsNotNone(original_after.superseded_by)
        # The OLD's ``superseded_by`` is the
        # same replacement id (not a new one).
        # The original replacement is still
        # there.
        self.assertIsNotNone(
            es2.get(original_after.superseded_by)
        )


# --------------------------------------------------------------------
# D3-chain: real chain original -> repl_1 -> repl_2 + cancel semantics
# --------------------------------------------------------------------


class D3ChainTests(unittest.TestCase):
    """D3-chain: a real chain
    ``original -> repl_1 -> repl_2`` must be
    formed. The second reschedule marks
    repl_1 as superseded by repl_2. Cancelling
    repl_2 does NOT re-activate repl_1 or the
    original.
    """

    def test_two_reschedules_form_real_chain(self):
        es, ib, _, _ = _make_stores()
        raw_orig = _make_raw(
            message_id="m-chain-1-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_ingest_outcome(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        # First reschedule.
        raw_1 = _make_raw(
            message_id="m-chain-1-r1",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-chain-1-orig",
        )
        outcome_1, _ = _run(_ingest_outcome(es, ib, raw_1))
        repl_1_id = outcome_1.replacement_event.id
        # The original now points at repl_1.
        self.assertEqual(
            es.get(original_id).superseded_by, repl_1_id
        )
        # Second reschedule: a new correction
        # with a different ``message_id``.
        raw_2 = _make_raw(
            message_id="m-chain-1-r2",
            raw_text="15.10.2026 16:00-23:00",
            original_message_id="m-chain-1-orig",
        )
        outcome_2, _ = _run(_ingest_outcome(es, ib, raw_2))
        repl_2_id = outcome_2.replacement_event.id
        # The original now points at repl_2
        # (the LATEST correction).
        self.assertEqual(
            es.get(original_id).superseded_by, repl_2_id
        )
        # repl_1 now has ``superseded_by =
        # repl_2.id`` (the chain link).
        repl_1_after = es.get(repl_1_id)
        self.assertIsNotNone(repl_1_after)
        self.assertEqual(
            repl_1_after.superseded_by, repl_2_id,
            msg=(
                "the chain link from repl_1 to "
                "repl_2 must be set atomically; "
                f"got {repl_1_after.superseded_by!r}"
            ),
        )
        # 3 events total: original, repl_1,
        # repl_2.
        self.assertEqual(len(es.list_events()), 3)
        # The inbox has 3 entries.
        self.assertEqual(len(ib.list()), 3)

    def test_cancelling_last_replacement_does_not_reactivate_chain(self):
        """Confirm the chain, then cancel the
        last replacement. The runtime selector
        must not pick the OLD or repl_1
        (their ``superseded_by`` links remain
        set; the chain does NOT roll back).
        """
        es, ib, _, _ = _make_stores()
        # Ingest original + 2 reschedules.
        raw_orig = _make_raw(
            message_id="m-chain-2-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_ingest_outcome(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        raw_1 = _make_raw(
            message_id="m-chain-2-r1",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-chain-2-orig",
        )
        outcome_1, _ = _run(_ingest_outcome(es, ib, raw_1))
        repl_1_id = outcome_1.replacement_event.id
        raw_2 = _make_raw(
            message_id="m-chain-2-r2",
            raw_text="15.10.2026 16:00-23:00",
            original_message_id="m-chain-2-orig",
        )
        outcome_2, _ = _run(_ingest_outcome(es, ib, raw_2))
        repl_2_id = outcome_2.replacement_event.id
        # Confirm repl_2.
        from dataclasses import replace as _dc_replace
        _run(es.update(_dc_replace(
            es.get(repl_2_id), status="confirmed"
        )))
        # Cancel repl_2.
        _run(es.update(_dc_replace(
            es.get(repl_2_id), status="cancelled"
        )))
        # The chain is intact: original ->
        # repl_2 (OLD), repl_1 -> repl_2
        # (chain link).
        self.assertEqual(
            es.get(original_id).superseded_by, repl_2_id
        )
        self.assertEqual(
            es.get(repl_1_id).superseded_by, repl_2_id
        )
        # The runtime selector must return
        # None. The original, repl_1, and
        # repl_2 are all excluded:
        #   * original: superseded
        #     (excluded by supersession filter)
        #   * repl_1: superseded
        #     (excluded by supersession filter)
        #   * repl_2: cancelled
        #     (excluded by status filter)
        # We exercise the production runtime
        # selector.
        import importlib.util as _ilu
        runtime_path = (
            REPO_ROOT / "services" / "outage_runtime.py"
        )
        spec = _ilu.spec_from_file_location(
            f"{_STUB_PKG}.runtime", str(runtime_path)
        )
        runtime_mod = _ilu.module_from_spec(spec)
        sys.modules[spec.name] = runtime_mod
        spec.loader.exec_module(runtime_mod)
        # Stub hass: bundle caches the store.
        bundle: dict[str, Any] = {"outage_store": es}
        hass = types.SimpleNamespace()
        hass.data = {"powmr_inverter": {"test": bundle}}
        now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        # 12:00 UTC = 15:00 Kyiv -- repl_2's
        # window (16:00-23:00 Kyiv = 13:00-20:00 UTC)
        # is partially in-window. But repl_2 is
        # cancelled; repl_1 is superseded; the
        # original is superseded. Result: None.
        selected = runtime_mod._find_active_event(
            hass, "test", now
        )
        self.assertIsNone(
            selected,
            msg=(
                "cancelling the last replacement "
                "must NOT re-activate the chain; "
                f"got {selected!r}"
            ),
        )
        # Even at a time when repl_2 WOULD be
        # in-window, the result is None.
        later = datetime(
            2026, 10, 15, 16, 0, tzinfo=timezone.utc
        )
        selected2 = runtime_mod._find_active_event(
            hass, "test", later
        )
        self.assertIsNone(selected2)

    def test_three_reschedules_form_extended_chain(self):
        """Three reschedules produce a chain
        ``original -> repl_2`` with repl_1 and
        repl_0 (the original) both pointing
        at the latest replacement.
        """
        es, ib, _, _ = _make_stores()
        raw_orig = _make_raw(
            message_id="m-chain-3-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_ingest_outcome(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        prev_id = original_id
        for i in range(1, 4):
            raw = _make_raw(
                message_id=f"m-chain-3-r{i}",
                raw_text=f"15.10.2026 {14 + i}:00-22:00",
                original_message_id="m-chain-3-orig",
            )
            outcome, _ = _run(_ingest_outcome(es, ib, raw))
            new_id = outcome.replacement_event.id
            # The OLD points at the latest.
            self.assertEqual(
                es.get(prev_id).superseded_by, new_id,
                msg=(
                    f"after reschedule #{i}, "
                    f"event {prev_id!r} must point "
                    f"at {new_id!r}"
                ),
            )
            prev_id = new_id
        # 4 events total.
        self.assertEqual(len(es.list_events()), 4)
        # 4 inbox entries.
        self.assertEqual(len(ib.list()), 4)


if __name__ == "__main__":
    unittest.main()
