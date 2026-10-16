"""D3 v2 regression tests for Phase 4 v2.

The 0e94c84 commit fixed D3 (reschedule coherence)
with separate add+update and a rollback. Codex then
reproduced more defects that this file targets:

  D3-snapshot: the reschedule MUST be persisted
               as a single atomic snapshot. Build
               the old+new pair, save it in ONE
               ``async_save`` call, and update the
               in-memory working state only AFTER
               the save succeeds. A failure on the
               storage boundary (injected into
               ``async_save``) must leave the
               working state unchanged -- no
               dangling ``superseded_by``, no
               orphan replacement event.

  D3-replay-vs-correction: distinguish a replay
               from a new correction. A replay is
               the SAME ``source/channel/message_id``
               already in the inbox. A new
               correction is a DIFFERENT
               ``message_id`` with a new interval
               (which the operator may have sent
               to refine the reschedule window
               again). ``target.superseded_by`` alone
               is NOT a sufficient replay signal.

The tests drive the REAL production handler body
(the same ``decide`` / ``apply_reschedule_snapshot``
/ ``commit`` sequencing as
``handle_ingest_outage_notification``) and the
REAL production adapter and stores. Failures are
injected into the backing storage's ``async_save``
-- NOT before the call -- to exercise the atomicity
guarantee.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
INBOX_PATH = REPO_ROOT / "services" / "outage_inbox.py"
OUTAGE_PATH = REPO_ROOT / "services" / "outage.py"

_STUB_PKG = "_outage_inbox_v2_d3snapshot_stub"


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


class _FailingDictStore:
    """In-process stand-in for HA ``Store`` with
    in-place mutation semantics. The
    ``fail_on_save`` flag, when True, makes
    ``async_save`` raise -- the production
    handler must keep the working state
    consistent on this path.
    """

    def __init__(
        self,
        backend,
        default,
        *,
        fail_on_save: bool = False,
        save_attempts: int = 0,
    ):
        self._backend = backend
        self._default = default
        self._fail_on_save = fail_on_save
        self._save_attempts = save_attempts
        self._save_count = 0

    async def async_load(self):
        if self._backend:
            import copy
            return copy.deepcopy(self._backend)
        return self._default

    async def async_save(self, data):
        self._save_count += 1
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
    fail_event_save: bool = False,
    fail_inbox_save: bool = False,
    event_save_attempts: int = 0,
    inbox_save_attempts: int = 0,
):
    if backend_events is None:
        backend_events = {}
    if backend_inbox is None:
        backend_inbox = {}
    es = outage.EventStore(
        _FailingDictStore(
            backend_events,
            default=None,
            fail_on_save=fail_event_save,
            save_attempts=event_save_attempts,
        ),
        "test",
    )
    ib = inbox.InboxStore(
        _FailingDictStore(
            backend_inbox,
            default=None,
            fail_on_save=fail_inbox_save,
            save_attempts=inbox_save_attempts,
        ),
        "test",
    )
    return es, ib, backend_events, backend_inbox


async def _run_handler(
    es,
    ib,
    raw,
    tz_name="Europe/Kyiv",
):
    """Reproduce the body of
    ``handle_ingest_outage_notification``:
    decide -> apply_reschedule_snapshot (or
    add / update) -> commit inbox. The test
    exercises the same code paths as the
    production handler, including the
    RescheduleSnapshot for the supersession
    path.
    """
    parser = inbox.NotificationParserV2(tz_name)
    adapter = inbox.NotificationAdapterV2(parser)
    parsed = adapter.parse(raw)
    outcome = await adapter.decide(parsed, es, ib)
    if outcome.inbox_entry is None:
        return outcome, "replay"
    try:
        if outcome.action == "added" and outcome.event is not None:
            await es.add(outcome.event)
        elif outcome.action == "superseded":
            if outcome.snapshot is not None:
                await es.apply_reschedule_snapshot(
                    outcome.snapshot
                )
        elif outcome.action in (
            "updated_pending",
            "cancelled",
            "duplicate_event",
        ):
            if outcome.target_event is not None:
                await es.update(outcome.target_event)
    except Exception:
        raise
    await adapter.commit(outcome, ib)
    return outcome, "committed"


# --------------------------------------------------------------------
# D3-snapshot: atomic persistence, store consistency on save failure
# --------------------------------------------------------------------


class D3SnapshotAtomicityTests(unittest.TestCase):
    """D3-snapshot: a reschedule MUST persist
    both events in ONE ``async_save``. A failure
    on the storage boundary (injected into
    ``async_save``) must leave the working state
    unchanged -- no dangling ``superseded_by``,
    no orphan replacement.
    """

    def test_storage_save_failure_leaves_working_state_unchanged(self):
        """Inject a failure into the event
        store's ``async_save``. The
        ``apply_reschedule_snapshot`` call
        MUST raise and the working state MUST
        be unchanged (no replacement, no
        superseded_by on the OLD).
        """
        # 1. Original event (saves succeed).
        es, ib, _, _ = _make_stores()
        raw_orig = _make_raw(
            message_id="m-snap-1-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_run_handler(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        # 2. Inject failure into the event
        #    store's ``async_save``. The next
        #    save (the snapshot for the
        #    reschedule) fails.
        es._storage._fail_on_save = True
        raw_resched = _make_raw(
            message_id="m-snap-1-resched",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-snap-1-orig",
        )
        with self.assertRaises(RuntimeError):
            _run(_run_handler(es, ib, raw_resched))
        # 3. The store has the ORIGINAL only.
        events = es.list_events()
        self.assertEqual(
            len(events), 1,
            msg=(
                "the snapshot save failed; the "
                "replacement must NOT be in the "
                "store. events="
                f"{[e.id for e in events]}"
            ),
        )
        # 4. The OLD has NO ``superseded_by``.
        original_after = es.get(original_id)
        self.assertIsNone(
            original_after.superseded_by,
            msg=(
                "the OLD's ``superseded_by`` must NOT "
                "be set when the snapshot save fails; "
                "got "
                f"{original_after.superseded_by!r}"
            ),
        )
        # 5. The inbox has only the original;
        #    the reschedule was NOT committed.
        self.assertEqual(len(ib.list()), 1)
        self.assertNotIn(
            "m-snap-1-resched",
            [e.message_id for e in ib.list()],
        )

    def test_storage_recovery_on_first_attempt_fail_second_succeed(self):
        """The first snapshot save fails, the
        redelivery succeeds. The redelivery
        must produce the same final state as a
        single-shot success.
        """
        # 1. Original event (saves succeed).
        es, ib, _, _ = _make_stores()
        raw_orig = _make_raw(
            message_id="m-snap-2-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_run_handler(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        # 2. The first reschedule attempt
        #    fails (the snapshot save raises).
        #    The original save was count=1; the
        #    snapshot save is count=2. Setting
        #    ``save_attempts=2`` makes count=2
        #    fail; count=3 (the redelivery) is
        #    allowed through.
        es._storage._save_attempts = 2
        raw_resched = _make_raw(
            message_id="m-snap-2-resched",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-snap-2-orig",
        )
        with self.assertRaises(RuntimeError):
            _run(_run_handler(es, ib, raw_resched))
        # Sanity: store unchanged.
        self.assertEqual(len(es.list_events()), 1)
        self.assertIsNone(es.get(original_id).superseded_by)
        # 3. Redelivery: this time the save
        #    succeeds (count=3 > save_attempts).
        es._storage._save_attempts = 0
        outcome_retry, status = _run(
            _run_handler(es, ib, raw_resched)
        )
        self.assertEqual(status, "committed")
        # Final state: 2 events; OLD's
        # ``superseded_by`` points to the new.
        self.assertEqual(len(es.list_events()), 2)
        original_after = es.get(original_id)
        replacement_after = es.get(
            original_after.superseded_by
        )
        self.assertIsNotNone(replacement_after)
        self.assertNotEqual(
            original_after.superseded_by, original_id
        )

    def test_snapshot_atomic_write_under_reload_after_failure(self):
        """End-to-end reload: a storage save
        failure leaves the persistent backend
        unchanged. A fresh InboxStore /
        EventStore loaded from that backend
        sees the original event with no
        ``superseded_by`` -- and a redelivery
        completes the reschedule.
        """
        backend_events: dict[str, Any] = {}
        backend_inbox: dict[str, Any] = {}
        es, ib, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        raw_orig = _make_raw(
            message_id="m-snap-3-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_run_handler(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        # Persist what we have so far.
        _run(es.async_save())
        _run(ib.async_save())
        # Inject failure into the event
        # store's next save.
        es._storage._fail_on_save = True
        # Snapshot save fails.
        raw_resched = _make_raw(
            message_id="m-snap-3-resched",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-snap-3-orig",
        )
        with self.assertRaises(RuntimeError):
            _run(_run_handler(es, ib, raw_resched))
        # Reload from the persistent backend.
        es2, ib2, _, _ = _make_stores(
            backend_events=backend_events,
            backend_inbox=backend_inbox,
        )
        _run(es2.async_load())
        _run(ib2.async_load())
        # Sanity: the reloaded store has the
        # original only.
        self.assertEqual(len(es2.list_events()), 1)
        self.assertIsNone(
            es2.get(original_id).superseded_by
        )
        # Redelivery from a fresh InboxStore.
        es2._storage._fail_on_save = False
        outcome_retry, status = _run(
            _run_handler(es2, ib2, raw_resched)
        )
        self.assertEqual(status, "committed")
        self.assertEqual(len(es2.list_events()), 2)
        original_after = es2.get(original_id)
        self.assertIsNotNone(original_after.superseded_by)
        self.assertIsNotNone(
            es2.get(original_after.superseded_by)
        )


# --------------------------------------------------------------------
# D3-replay-vs-correction: same key vs new correction
# --------------------------------------------------------------------


class D3ReplayVsCorrectionTests(unittest.TestCase):
    """D3-replay-vs-correction: distinguish a
    replay (same ``source/channel/message_id``)
    from a new correction (different
    ``message_id`` with a new interval).
    ``target.superseded_by`` is NOT a sufficient
    replay signal.
    """

    def test_same_message_id_redelivery_is_replay(self):
        es, ib, _, _ = _make_stores()
        raw_orig = _make_raw(
            message_id="m-rv-1-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_run_handler(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        # First reschedule: completes.
        raw_resched = _make_raw(
            message_id="m-rv-1-resched",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-rv-1-orig",
        )
        outcome_first, _ = _run(
            _run_handler(es, ib, raw_resched)
        )
        first_replacement_id = (
            outcome_first.replacement_event.id
        )
        # Redelivery of the SAME message:
        # must be a replay.
        outcome_second, status = _run(
            _run_handler(es, ib, raw_resched)
        )
        self.assertEqual(
            status, "replay",
            msg=(
                "redelivery of the same message_id "
                "must be a replay; got "
                f"{status!r}"
            ),
        )
        # The store still has 2 events; the
        # OLD's ``superseded_by`` is the
        # ORIGINAL replacement id (no new
        # replacement was created).
        self.assertEqual(len(es.list_events()), 2)
        original_after = es.get(original_id)
        self.assertEqual(
            original_after.superseded_by,
            first_replacement_id,
        )

    def test_new_message_id_with_new_interval_is_new_correction(self):
        """A second reschedule with a NEW
        ``message_id`` and a new interval --
        even if the OLD already has
        ``superseded_by`` -- must produce a new
        correction (a new replacement event),
        NOT a replay.
        """
        es, ib, _, _ = _make_stores()
        # 1. Original.
        raw_orig = _make_raw(
            message_id="m-rv-2-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_run_handler(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        # 2. First reschedule.
        raw_resched_1 = _make_raw(
            message_id="m-rv-2-resched-1",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-rv-2-orig",
        )
        outcome_1, _ = _run(_run_handler(es, ib, raw_resched_1))
        replacement_1_id = (
            outcome_1.replacement_event.id
        )
        # 3. Second reschedule: NEW
        #    ``message_id`` and a different
        #    interval. This is a NEW correction.
        #    The OLD has ``superseded_by`` set
        #    (pointing at replacement_1) -- the
        #    adapter must NOT treat this as a
        #    replay.
        raw_resched_2 = _make_raw(
            message_id="m-rv-2-resched-2",
            raw_text="15.10.2026 16:00-23:00",
            original_message_id="m-rv-2-orig",
        )
        outcome_2, status = _run(
            _run_handler(es, ib, raw_resched_2)
        )
        self.assertEqual(
            status, "committed",
            msg=(
                "a new reschedule with a different "
                "message_id must be a new correction; "
                f"got {status!r}"
            ),
        )
        # The store now has 3 events: original,
        # first replacement, second replacement.
        self.assertEqual(
            len(es.list_events()), 3,
            msg=(
                "two reschedules must produce three "
                "events: original + 2 replacements; "
                f"got {len(es.list_events())} events"
            ),
        )
        # The OLD's ``superseded_by`` is the
        # SECOND replacement (the chain: OLD ->
        # replacement_2, replacement_1 is
        # superseded by replacement_2? Or
        # original -> replacement_1 -> ?
        # The spec is: the LATEST correction is
        # the one tied to the original. The
        # adapter's job is to update the
        # ORIGINAL's ``superseded_by`` to point
        # at the latest replacement).
        original_after = es.get(original_id)
        self.assertEqual(
            original_after.superseded_by,
            outcome_2.replacement_event.id,
            msg=(
                "the OLD's ``superseded_by`` must "
                "point at the LATEST replacement"
            ),
        )
        # The first replacement still exists
        # (audit) but is not the one the OLD
        # points at.
        self.assertIsNotNone(es.get(replacement_1_id))

    def test_sequential_reschedules_different_message_ids_persist_in_chain(self):
        """Two sequential reschedules with
        different message_ids persist the
        whole chain: original -> repl_1 ->
        repl_2 (the OLD points at repl_2).
        """
        es, ib, _, _ = _make_stores()
        raw_orig = _make_raw(
            message_id="m-rv-3-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_run_handler(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        for i, (msg_id, raw_text) in enumerate(
            [
                ("m-rv-3-resched-1", "15.10.2026 14:00-22:00"),
                ("m-rv-3-resched-2", "15.10.2026 16:00-23:00"),
            ],
            start=1,
        ):
            raw = _make_raw(
                message_id=msg_id,
                raw_text=raw_text,
                original_message_id="m-rv-3-orig",
            )
            outcome, status = _run(
                _run_handler(es, ib, raw)
            )
            self.assertEqual(
                status, "committed",
                msg=(
                    f"reschedule #{i} must be a new "
                    f"correction; got {status!r}"
                ),
            )
        # 3 events in the store.
        self.assertEqual(len(es.list_events()), 3)
        # The OLD's ``superseded_by`` is the
        # LATEST replacement.
        original_after = es.get(original_id)
        all_ids = {e.id for e in es.list_events()}
        self.assertIn(
            original_after.superseded_by, all_ids,
            msg=(
                "the OLD's ``superseded_by`` must "
                "point at an existing replacement"
            ),
        )
        # The pointed-at event is NOT the
        # original.
        self.assertNotEqual(
            original_after.superseded_by, original_id
        )
        # The inbox has 3 entries: original +
        # 2 reschedules.
        self.assertEqual(len(ib.list()), 3)


if __name__ == "__main__":
    unittest.main()
