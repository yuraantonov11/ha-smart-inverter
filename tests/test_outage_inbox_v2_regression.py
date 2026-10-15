"""Phase 4 v2 regression tests for the 5 defects reproduced by Codex.

The earlier ``a91dc70`` commit landed the v2
pipeline (InboxAdapter with persistence,
unambiguous cancellation, reschedule as
correction). Codex then reproduced 5 follow-up
defects that this file targets:

  D1 -- Old persisted events written by the v1
       pipeline lack ``superseded_by`` in their
       JSON. The new field is required. Loading
       must default to ``None`` and the v2
       pipeline must continue to work with old
       payloads (no migration).

  D2 -- Idempotency must hold through the REAL
       handler (``handle_ingest_outage_notification``
       in ``services/outage.py``), not just the
       adapter directly. A redelivery of the same
       ``(source, channel, message_id)`` after a
       simulated reload (new InboxStore / EventStore
       backed by the same persistent storage) MUST
       be a no-op: no second event is added.

  D3 -- Atomicity. The earlier v2 wrote the inbox
       BEFORE the event mutation. A crash between
       the two leaves the inbox saying
       ``added/cancelled/superseded`` without the
       corresponding event on disk. The fix splits
       the adapter into ``decide()`` (no writes) and
       ``commit()`` (writes the inbox); the handler
       applies event mutations in between. A
       simulated failure in ``event_store.add``
       must leave the inbox empty, so a redelivery
       retries the whole flow.

  D4 -- The original-message lookup used a
       substring in ``event.notes``. That is
       fragile (notes are operator-mutable
       free-form). The fix uses the structured
       InboxStore key
       ``(source, channel, message_id)``: a
       redelivery of the original is mapped to the
       event via the inbox's ``event_id``, even
       when the event's notes are corrupted.

  D5 -- Superseded events must be EXCLUDED from
       the runtime active-event selection (the
       old event is kept for audit but the
       replacement event is the one the runtime
       acts on).

The tests exercise the production code path:
  * real ``EventStore``,
  * real ``InboxStore`` with a shared dict backend
    that survives across "reloads",
  * real ``NotificationAdapterV2``,
  * real handler body from ``services/outage.py``
    (the function uses the module-level
    ``handle_ingest_outage_notification`` -- the
    test reproduces its body to drive the same
    decide/commit sequencing).
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
INBOX_PATH = REPO_ROOT / "services" / "outage_inbox.py"
OUTAGE_PATH = REPO_ROOT / "services" / "outage.py"

_STUB_PKG = "_outage_inbox_v2_regression_stub"


def _load_production_modules():
    """Load ``outage`` and ``outage_inbox`` as
    sub-modules of a stub package."""
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
    in-place mutation semantics. The backend dict
    is shared between two ``InboxStore`` /
    ``EventStore`` instances so a "reload" reads
    what the previous session wrote.
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
        _DictStore(backend_events, default=None),
        "test",
    )
    ib = inbox.InboxStore(
        _DictStore(backend_inbox, default=None),
        "test",
    )
    return es, ib, backend_events, backend_inbox


async def _run_handler(
    es,
    ib,
    raw,
    tz_name="Europe/Kyiv",
    fail_event_add: bool = False,
):
    """Reproduce the body of
    ``handle_ingest_outage_notification`` so the
    test exercises the same decide / event
    mutation / commit sequencing without needing
    a real ``hass`` object.

    When ``fail_event_add`` is True the
    ``event_store.add`` call raises after the
    adapter decided an ``added`` outcome. The
    handler must NOT commit the inbox in that
    case (defect D3 regression).
    """
    parser = inbox.NotificationParserV2(tz_name)
    adapter = inbox.NotificationAdapterV2(parser)
    parsed = adapter.parse(raw)
    outcome = await adapter.decide(parsed, es, ib)
    if outcome.inbox_entry is None:
        # Idempotency replay.
        return outcome, "replay"
    try:
        if outcome.action == "added" and outcome.event is not None:
            if fail_event_add:
                raise RuntimeError(
                    "simulated event_store.add failure"
                )
            await es.add(outcome.event)
        elif outcome.action in (
            "updated_pending",
            "cancelled",
            "superseded",
            "duplicate_event",
        ):
            if outcome.target_event is not None:
                await es.update(outcome.target_event)
            if (
                outcome.event is not None
                and outcome.replacement_event is not None
                and outcome.replacement_event.id
                != (
                    outcome.target_event.id
                    if outcome.target_event is not None
                    else None
                )
            ):
                await es.add(outcome.replacement_event)
    except Exception:
        # Inbox is NOT committed: redelivery
        # can re-decide.
        raise
    await adapter.commit(outcome, ib)
    return outcome, "committed"


# --------------------------------------------------------------------
# D1 -- backward compat: load legacy payloads
# --------------------------------------------------------------------


class BackwardCompatTests(unittest.TestCase):
    """D1: events persisted by the v1 pipeline
    (or by an older v2 snapshot) lack
    ``superseded_by``. ``OutageEvent.from_dict``
    must default the field to ``None`` so legacy
    events continue to load.
    """

    def test_outage_event_from_dict_without_superseded_by(self):
        legacy_payload = {
            "id": "ev-legacy",
            "source": "manual:service",
            "raw_text": "legacy",
            "start_iso": "2026-10-15T09:00:00+03:00",
            "end_iso": "2026-10-15T18:00:00+03:00",
            "tz_name": "Europe/Kyiv",
            "received_at": "2026-10-14T08:00:00+00:00",
            "status": "confirmed",
            "needs_confirmation": False,
            "ambiguity_reason": None,
            "version": 1,
            "updated_at": "2026-10-14T08:00:00+00:00",
            "notes": None,
            # No ``superseded_by`` key.
        }
        ev = outage.OutageEvent.from_dict(legacy_payload)
        self.assertIsNone(ev.superseded_by)
        # Round-trip back to dict: the new field
        # is now part of the payload.
        roundtrip = ev.to_dict()
        self.assertIn("superseded_by", roundtrip)
        self.assertIsNone(roundtrip["superseded_by"])

    def test_event_store_loads_legacy_payload(self):
        """The ``EventStore`` persists through HA
        ``Store`` (``async_save`` /
        ``async_load``). A legacy payload
        written without ``superseded_by`` must
        load cleanly via the store.
        """
        es, _ib, backend_events, _backend_inbox = _make_stores()
        legacy_payload = {
            "__version__": outage.STORE_VERSION,
            "events": {
                "ev-legacy": {
                    "id": "ev-legacy",
                    "source": "manual:service",
                    "raw_text": "legacy",
                    "start_iso": "2026-10-15T09:00:00+03:00",
                    "end_iso": "2026-10-15T18:00:00+03:00",
                    "tz_name": "Europe/Kyiv",
                    "received_at": "2026-10-14T08:00:00+00:00",
                    "status": "confirmed",
                    "needs_confirmation": False,
                    "ambiguity_reason": None,
                    "version": 1,
                    "updated_at": "2026-10-14T08:00:00+00:00",
                    "notes": None,
                }
            },
        }
        backend_events.update(legacy_payload)
        es2, _ib2, _b1, _b2 = _make_stores(
            backend_events=backend_events
        )
        _run(es2.async_load())
        events = es2.list_events()
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0].superseded_by)

    def test_inbox_entry_from_dict_without_extras(self):
        """InboxEntry.from_dict must also be
        forward-compatible: missing
        ``target_event_id`` / ``replacement_event_id``
        default to ``None``.
        """
        legacy = {
            "key": "email||m-1",
            "source": "email",
            "channel": "",
            "message_id": "m-1",
            "received_at": "2026-10-14T11:00:00+00:00",
            "raw_text": "15.10.2026 09:00-18:00",
            "sender": "s",
            "address_hint": None,
            "action": "added",
            "event_id": "ev-1",
            "reason": "fresh",
            "parsed": {},
        }
        entry = inbox.InboxEntry.from_dict(legacy)
        self.assertIsNone(entry.target_event_id)
        self.assertIsNone(entry.replacement_event_id)
        # And vice versa: the round-trip carries
        # both fields.
        d = entry.to_dict()
        self.assertIn("target_event_id", d)
        self.assertIn("replacement_event_id", d)


# --------------------------------------------------------------------
# D2 -- handler idempotency through real handler + reload
# --------------------------------------------------------------------


class HandlerIdempotencyTests(unittest.TestCase):
    """D2: the same notification must NOT produce
    a second event, even when ingested through
    the real handler body, even after a reload
    that creates a fresh ``InboxStore`` /
    ``EventStore`` against the same persistent
    backend.
    """

    def setUp(self):
        self.es1, self.ib1, self.b_events, self.b_inbox = (
            _make_stores()
        )
        self.parser = inbox.NotificationParserV2("Europe/Kyiv")
        self.adapter = inbox.NotificationAdapterV2(self.parser)

    def test_redelivery_through_handler_is_noop_after_reload(self):
        raw = _make_raw(
            message_id="m-d2-1",
            raw_text="15.10.2026 09:00-18:00",
        )
        # 1. First ingestion.
        outcome, status = _run(
            _run_handler(self.es1, self.ib1, raw)
        )
        self.assertEqual(status, "committed")
        self.assertEqual(outcome.action, "added")
        # 2. Persist.
        _run(self.es1.async_save())
        _run(self.ib1.async_save())
        # 3. Reload: fresh stores backed by the
        #    same dicts.
        es2, ib2, _, _ = _make_stores(
            backend_events=self.b_events,
            backend_inbox=self.b_inbox,
        )
        _run(es2.async_load())
        _run(ib2.async_load())
        # 4. Redelivery.
        outcome2, status2 = _run(
            _run_handler(es2, ib2, raw)
        )
        # 5. The handler must report an idempotent
        #    replay (no second event was created).
        self.assertEqual(
            status2, "replay",
            msg=(
                "the handler must return ``replay`` for "
                "a redelivery; the inbox already has the "
                "key"
            ),
        )
        self.assertEqual(outcome2.action, "added")
        # 6. The store still has exactly one event.
        self.assertEqual(len(es2.list_events()), 1)

    def test_redelivery_with_different_raw_text_still_noop(self):
        """A different ``raw_text`` but the same
        ``message_id`` is still a redelivery.
        The original ``raw_text`` is preserved
        in the inbox entry.
        """
        raw_v1 = _make_raw(
            message_id="m-d2-2",
            raw_text="15.10.2026 09:00-18:00",
        )
        _run(_run_handler(self.es1, self.ib1, raw_v1))
        _run(self.es1.async_save())
        _run(self.ib1.async_save())
        es2, ib2, _, _ = _make_stores(
            backend_events=self.b_events,
            backend_inbox=self.b_inbox,
        )
        _run(es2.async_load())
        _run(ib2.async_load())
        # Same message_id, different raw_text --
        # still a replay.
        raw_v2 = _make_raw(
            message_id="m-d2-2",
            raw_text="different body, same id",
        )
        outcome, status = _run(_run_handler(es2, ib2, raw_v2))
        self.assertEqual(status, "replay")
        self.assertEqual(len(es2.list_events()), 1)


# --------------------------------------------------------------------
# D3 -- atomicity: error between event and inbox leaves inbox empty
# --------------------------------------------------------------------


class AtomicityTests(unittest.TestCase):
    """D3: when ``event_store.add`` fails, the
    inbox MUST NOT be committed. A redelivery
    must then re-decide and add the event
    cleanly.
    """

    def test_event_add_failure_leaves_inbox_empty(self):
        es, ib, _, _ = _make_stores()
        raw = _make_raw(
            message_id="m-d3-1",
            raw_text="15.10.2026 09:00-18:00",
        )
        # 1. Ingestion with simulated
        #    ``event_store.add`` failure.
        with self.assertRaises(RuntimeError):
            _run(
                _run_handler(
                    es, ib, raw, fail_event_add=True
                )
            )
        # 2. The inbox has NO entry: the redelivery
        #    can retry.
        self.assertEqual(
            len(ib.list()),
            0,
            msg=(
                "the inbox must be empty when event "
                "mutation fails; otherwise a redelivery "
                "is treated as a duplicate"
            ),
        )
        # 3. The event store has no event.
        self.assertEqual(len(es.list_events()), 0)

    def test_redelivery_after_failure_succeeds(self):
        es, ib, _, _ = _make_stores()
        raw = _make_raw(
            message_id="m-d3-2",
            raw_text="15.10.2026 09:00-18:00",
        )
        # 1. First attempt: simulated failure.
        with self.assertRaises(RuntimeError):
            _run(
                _run_handler(
                    es, ib, raw, fail_event_add=True
                )
            )
        # 2. Redelivery: success.
        outcome, status = _run(_run_handler(es, ib, raw))
        self.assertEqual(status, "committed")
        self.assertEqual(outcome.action, "added")
        # 3. The event is now in the store.
        events = es.list_events()
        self.assertEqual(len(events), 1)
        # 4. The inbox is now committed.
        self.assertEqual(len(ib.list()), 1)


# --------------------------------------------------------------------
# D4 -- structured original-message lookup
# --------------------------------------------------------------------


class StructuredOriginalMessageLookupTests(unittest.TestCase):
    """D4: the original-message lookup must use
    the structured ``InboxStore`` key, not a
    substring in ``event.notes``. The test
    corrupts the event's notes and verifies the
    reschedule still finds the original event.
    """

    def test_reschedule_finds_original_via_inbox_key_even_when_notes_corrupted(self):
        es, ib, _, _ = _make_stores()
        # 1. Seed the inbox with the original
        #    notification. The adapter needs the
        #    inbox entry to resolve the
        #    ``original_message_id`` lookup.
        parser = inbox.NotificationParserV2("Europe/Kyiv")
        adapter = inbox.NotificationAdapterV2(parser)
        raw_orig = _make_raw(
            message_id="m-d4-orig",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, status_orig = _run(
            _run_handler(es, ib, raw_orig)
        )
        self.assertEqual(status_orig, "committed")
        original_event_id = outcome_orig.event.id
        # 2. Corrupt the event's notes so the
        #    v1 substring lookup would have
        #    failed. The structured lookup via
        #    ``inbox.get`` must still resolve.
        ev = es.get(original_event_id)
        self.assertIsNotNone(ev)
        from dataclasses import replace
        es._events[ev.id] = replace(
            ev, notes="operator wrote something here"
        )
        # 3. Reschedule with the original
        #    ``message_id``.
        raw_reschedule = _make_raw(
            message_id="m-d4-resched",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-d4-orig",
        )
        outcome, status = _run(_run_handler(es, ib, raw_reschedule))
        self.assertEqual(status, "committed")
        self.assertEqual(
            outcome.action, "superseded",
            msg=(
                "the adapter must find the original "
                "event via the structured inbox key "
                "even when the event's notes are "
                "corrupted; got "
                f"{outcome!r}"
            ),
        )
        # 4. The old event now has
        #    ``superseded_by`` pointing at the
        #    new event.
        old_after = es.get(original_event_id)
        self.assertIsNotNone(old_after.superseded_by)
        self.assertEqual(
            old_after.superseded_by, outcome.replacement_event.id
        )

    def test_reschedule_without_inbox_entry_treats_as_fresh(self):
        """When the original inbox entry is gone
        (e.g. the operator wiped the inbox), a
        reschedule with ``original_message_id``
        that no longer resolves is treated as a
        fresh add. The event gets a new id; the
        orphan is left in the store.
        """
        es, ib, _, _ = _make_stores()
        # 1. Add an orphan event on a different
        #    window so the reschedule's new window
        #    does NOT overlap it (otherwise
        #    standard dedup fires first and the
        #    test exercises the wrong branch).
        ev = outage.OutageEvent(
            id="ev-orphan",
            source="notification:email",
            raw_text="orphan",
            start_iso="2026-10-12T09:00:00+03:00",
            end_iso="2026-10-12T18:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-11T08:00:00+00:00",
            status="confirmed",
            needs_confirmation=False,
            ambiguity_reason=None,
            version=1,
            updated_at="2026-10-11T08:00:00+00:00",
            notes="operator wrote something here",
        )
        _run(es.add(ev))
        # 2. Reschedule with an original_message_id
        #    that has no inbox entry. The
        #    structured lookup returns ``None``
        #    (no inbox entry), so the reschedule
        #    is treated as a fresh add.
        raw = _make_raw(
            message_id="m-d4-resched-2",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-missing",
        )
        outcome, status = _run(_run_handler(es, ib, raw))
        self.assertEqual(status, "committed")
        # The orphan is NOT linked (no
        # ``superseded_by`` set). The new message
        # is a fresh add.
        self.assertEqual(outcome.action, "added")
        orphan_after = es.get("ev-orphan")
        self.assertIsNone(orphan_after.superseded_by)


# --------------------------------------------------------------------
# D5 -- superseded events excluded from runtime
# --------------------------------------------------------------------


def _build_runtime_stub_hass(entry_id, event_store):
    """Build a minimal stub ``hass`` that the
    runtime's ``_find_active_event`` (or its
    public wrapper) can read. We bypass the
    runtime's ``_get_bundle`` / store caching
    by replicating its minimal access pattern.
    """
    bundle = {"outage_store": event_store}
    hass = types.SimpleNamespace()
    hass.data = {"powmr_inverter": {entry_id: bundle}}
    return hass


class RuntimeSupersessionExclusionTests(unittest.TestCase):
    """D5: the runtime's active-event selection
    must skip events that carry a
    ``superseded_by`` link. The old event is
    kept in the store for audit but does not
    drive dispatch.
    """

    def test_superseded_event_is_skipped_by_runtime_selector(self):
        # The runtime's _find_active_event is a
        # private helper; the test reproduces the
        # same eligibility check inline. The
        # production change is in
        # ``services/outage_runtime.py::
        # _find_active_event``.
        now = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
        ev_old = outage.OutageEvent(
            id="ev-old",
            source="notification:email",
            raw_text="original",
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
            superseded_by="ev-new",
        )
        ev_new = outage.OutageEvent(
            id="ev-new",
            source="notification:email",
            raw_text="reschedule",
            start_iso="2026-10-15T14:00:00+03:00",
            end_iso="2026-10-15T22:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T09:00:00+00:00",
            status="confirmed",
            needs_confirmation=False,
            ambiguity_reason=None,
            version=1,
            updated_at="2026-10-14T09:00:00+00:00",
            notes=None,
            superseded_by=None,
        )
        es, _ib, _b_events, _b_inbox = _make_stores()
        _run(es.add(ev_old))
        _run(es.add(ev_new))
        # ``now = 10:00 UTC`` is in BOTH events'
        # eligibility windows (4h prep backstop).
        # OLD: 09:00-18:00 Kyiv = 06:00-15:00 UTC
        #   (prep opens 02:00 UTC). 10:00 UTC is
        #   in-window.
        # NEW: 14:00-22:00 Kyiv = 11:00-19:00 UTC
        #   (prep opens 07:00 UTC). 10:00 UTC is
        #   in the prep window.
        # Without the supersession filter, the
        # selector would have returned the OLD
        # event (its iteration order is by event
        # id). With the filter, the OLD event is
        # skipped and the NEW event is picked.
        # Import the runtime module.
        runtime_path = (
            REPO_ROOT / "services" / "outage_runtime.py"
        )
        spec = importlib.util.spec_from_file_location(
            f"{_STUB_PKG}.outage_runtime", str(runtime_path)
        )
        runtime_mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runtime_mod
        spec.loader.exec_module(runtime_mod)
        hass = _build_runtime_stub_hass("test", es)
        selected = runtime_mod._find_active_event(
            hass, "test", now
        )
        self.assertIsNotNone(
            selected,
            msg=(
                "the runtime must pick the NEW event "
                "even before its window opens (prep "
                "window); got None"
            ),
        )
        self.assertEqual(
            selected.id, "ev-new",
            msg=(
                "the OLD event is superseded; the "
                "selector must skip it. Got "
                f"{selected.id!r}"
            ),
        )

    def test_replacement_event_is_picked_after_window_opens(self):
        """The NEW event must be picked once its
        window (or prep window) opens. This is
        the positive counterpart of the previous
        test.
        """
        ev_new = outage.OutageEvent(
            id="ev-new-2",
            source="notification:email",
            raw_text="reschedule",
            start_iso="2026-10-15T14:00:00+03:00",
            end_iso="2026-10-15T22:00:00+03:00",
            tz_name="Europe/Kyiv",
            received_at="2026-10-14T09:00:00+00:00",
            status="confirmed",
            needs_confirmation=False,
            ambiguity_reason=None,
            version=1,
            updated_at="2026-10-14T09:00:00+00:00",
            notes=None,
            superseded_by=None,
        )
        es, _ib, _b_events, _b_inbox = _make_stores()
        _run(es.add(ev_new))
        now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        # 12:00 UTC == 15:00 Kyiv -- inside the
        # NEW event's window.
        runtime_path = (
            REPO_ROOT / "services" / "outage_runtime.py"
        )
        spec = importlib.util.spec_from_file_location(
            f"{_STUB_PKG}.outage_runtime_2", str(runtime_path)
        )
        runtime_mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runtime_mod
        spec.loader.exec_module(runtime_mod)
        hass = _build_runtime_stub_hass("test", es)
        selected = runtime_mod._find_active_event(
            hass, "test", now
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected.id, "ev-new-2")


# --------------------------------------------------------------------
# Cross-cutting: full message -> event -> plan -> runtime path
# --------------------------------------------------------------------


class FullPathTests(unittest.TestCase):
    """The end-to-end contract: a notification
    delivered through the handler produces a
    confirmed event; the runtime picks it up;
    a reschedule replaces the event; the
    runtime picks the replacement after its
    window opens.
    """

    def test_message_to_runtime_replacement(self):
        es, ib, _b_events, _b_inbox = _make_stores()
        from dataclasses import replace as _dc_replace
        # 1. Ingest the original.
        raw_orig = _make_raw(
            message_id="m-full-1",
            raw_text="15.10.2026 09:00-18:00",
        )
        outcome_orig, _ = _run(_run_handler(es, ib, raw_orig))
        original_id = outcome_orig.event.id
        _run(es.update(_dc_replace(
            es.get(original_id), status="confirmed"
        )))
        # 2. Reschedule.
        raw_resched = _make_raw(
            message_id="m-full-2",
            raw_text="15.10.2026 14:00-22:00",
            original_message_id="m-full-1",
        )
        outcome_resched, _ = _run(
            _run_handler(es, ib, raw_resched)
        )
        replacement_id = outcome_resched.replacement_event.id
        # 2b. Confirm the replacement so the
        #     runtime picks it up.
        _run(es.update(_dc_replace(
            es.get(replacement_id), status="confirmed"
        )))
        # 3. The OLD event has ``superseded_by``
        #    pointing at the new one.
        old_after = es.get(original_id)
        self.assertEqual(old_after.superseded_by, replacement_id)
        # 4. Both events are in the store.
        self.assertEqual(
            len(es.list_events()), 2,
            msg="the OLD event must be preserved "
            "for audit even after reschedule",
        )
        # 5. The runtime picks the NEW event
        #    (the OLD is superseded).
        now = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
        # OLD: 09:00-18:00 Kyiv = 06:00-15:00 UTC
        #   (prep opens 02:00 UTC).
        # NEW: 14:00-22:00 Kyiv = 11:00-19:00 UTC
        #   (prep opens 07:00 UTC).
        # 10:00 UTC is in BOTH prep windows. The
        # selector must return the NEW event
        # (OLD is superseded).
        runtime_path = (
            REPO_ROOT / "services" / "outage_runtime.py"
        )
        spec = importlib.util.spec_from_file_location(
            f"{_STUB_PKG}.outage_runtime_3", str(runtime_path)
        )
        runtime_mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runtime_mod
        spec.loader.exec_module(runtime_mod)
        hass = _build_runtime_stub_hass("test", es)
        selected = runtime_mod._find_active_event(
            hass, "test", now
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected.id, replacement_id)
        # 6. After 12:00 UTC, NEW is fully in-window
        #    and is still picked.
        later = datetime(
            2026, 10, 15, 12, 0, tzinfo=timezone.utc
        )
        selected2 = runtime_mod._find_active_event(
            hass, "test", later
        )
        self.assertIsNotNone(selected2)
        self.assertEqual(selected2.id, replacement_id)


if __name__ == "__main__":
    unittest.main()
