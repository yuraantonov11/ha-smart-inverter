"""Tests for the Львівобленерго source
adapters and poller.

The full pipeline under test is:

    fetch (TelegramBotSource or ImapEmailSource)
      -> SourceMessage
      -> LvivoblenergoPoller._ingest_message
      -> RawNotificationV2
      -> outage.ingest_with_recovery (production)
      -> InboxStore + EventStore

A live Telegram bot token / IMAP mailbox is
NOT available in this sandbox. The Telegram
tests therefore drive the HTTP layer with a
fake ``aiohttp`` session; the IMAP tests use
a fake socket layer. The tests cover:

  1. The address and cancellation markers
     are extracted correctly from a real
     Львівобленерго notification body.
  2. The Telegram source adapts the JSON
     payload of ``getUpdates`` into
     ``SourceMessage`` objects with the right
     source / channel / message_id.
  3. The poller forwards a Telegram message
     to the inbox; the inbox stores the event;
     a second polling cycle with the same
     update_id is a no-op (the offset is
     advanced).
  4. A transient network error in the
     Telegram poll is retried; the message
     is delivered on the second attempt.
  5. The IMAP source extracts the body,
     filters by sender, and yields a
     ``SourceMessage``; the inbox stores the
     event.
  6. A real Lvivoblenergo cancellation
     message routes to the cancellation
     branch (reuses the production cancellation
     helper).

The Telegram path uses a fake
``aiohttp.ClientSession``; the IMAP path uses
a fake module with the ``imaplib`` interface.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = REPO_ROOT / "services" / "lvivoblenergo_source.py"
POLLER_PATH = REPO_ROOT / "services" / "lvivoblenergo_poller.py"
OUTAGE_PATH = REPO_ROOT / "services" / "outage.py"
INBOX_PATH = REPO_ROOT / "services" / "outage_inbox.py"

_STUB_PKG = "_lvivoblenergo_test_stub"


def _load_production_modules():
    """Load outage.py + outage_inbox.py under
    a fake ``homeassistant`` namespace."""
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
        class _E(Exception):
            pass
        setattr(ha_exc, "ServiceValidationError", _E)
        sys.modules["homeassistant.exceptions"] = ha_exc
        ha_helpers = types.ModuleType("homeassistant.helpers")
        ha_helpers.__path__ = []
        sys.modules["homeassistant.helpers"] = ha_helpers
        ha_storage = types.ModuleType(
            "homeassistant.helpers.storage"
        )
        class _Store:
            def __init__(self, *a, **kw):
                pass
        setattr(ha_storage, "Store", _Store)
        sys.modules["homeassistant.helpers.storage"] = ha_storage

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(
            f"{_STUB_PKG}.{name}", str(path)
        )
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m
        spec.loader.exec_module(m)
        return m

    _load("outage", OUTAGE_PATH)
    _load("outage_inbox", INBOX_PATH)
    return sys.modules[f"{_STUB_PKG}.outage"], sys.modules[
        f"{_STUB_PKG}.outage_inbox"
    ]


def _load_source_module():
    """Load the source + poller modules. The
    source module imports ``aiohttp`` lazily;
    the tests provide a stub before the
    import to keep them hermetic.
    """
    fake_aiohttp = types.ModuleType("aiohttp")

    class _ClientTimeout:
        def __init__(self, total=0):
            self.total = total

    fake_aiohttp.ClientTimeout = _ClientTimeout
    sys.modules.setdefault("aiohttp", fake_aiohttp)
    pkg = types.ModuleType(_STUB_PKG)
    pkg.__path__ = [str(REPO_ROOT / "services")]
    sys.modules[_STUB_PKG] = pkg
    outage_mod, inbox_mod = _load_production_modules()
    src_spec = importlib.util.spec_from_file_location(
        f"{_STUB_PKG}.lvivoblenergo_source", str(SRC_PATH)
    )
    src_mod = importlib.util.module_from_spec(src_spec)
    sys.modules[src_spec.name] = src_mod
    src_spec.loader.exec_module(src_mod)
    poller_spec = importlib.util.spec_from_file_location(
        f"{_STUB_PKG}.lvivoblenergo_poller", str(POLLER_PATH)
    )
    poller_mod = importlib.util.module_from_spec(poller_spec)
    sys.modules[poller_spec.name] = poller_mod
    poller_spec.loader.exec_module(poller_mod)
    return outage_mod, inbox_mod, src_mod, poller_mod


outage, inbox, source, poller = _load_source_module()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# A realistic Lvivoblenergo per-address
# notification body. The personal bot
# forwards a line of the form
#   "вул. Пирогова, 12: 15.10.2026 09:00-18:00"
# or a multi-line variant.
LVIV_BODY_OK = (
    "вул. Пирогова, 12: 15.10.2026 09:00-18:00\n"
    "Джерело: ПрАТ «Львівобленерго»"
)
LVIV_BODY_CANCEL = (
    "Скасовано: вул. Пирогова, 12\n"
    "Первинне повідомлення: m-d4s-1-orig"
)
LVIV_BODY_RESCHEDULE = (
    "Перенесено: вул. Пирогова, 12\n"
    "Новий час: 15.10.2026 14:00-22:00\n"
    "Попереднє повідомлення: m-d4s-1-orig"
)


# --------------------------------------------------------------------
# Helpers: a fake EventStore / InboxStore
# --------------------------------------------------------------------


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


def _make_stores():
    be, bi = {}, {}
    es = outage.EventStore(
        _DictStore(be, None), "test"
    )
    ib = inbox.InboxStore(
        _DictStore(bi, None), "test"
    )
    return es, ib, be, bi


# --------------------------------------------------------------------
# Telegram source tests
# --------------------------------------------------------------------


class TelegramSourceTests(unittest.TestCase):
    """The Telegram source adapts the JSON
    payload of ``getUpdates`` into
    ``SourceMessage`` objects."""

    def test_address_and_cancellation_parsing(self):
        # Address extraction
        address = source._extract_address(LVIV_BODY_OK)
        self.assertIsNotNone(address)
        self.assertIn("Пирогова", address)
        # Cancellation marker
        is_cancel, orig_id = source._parse_cancellation_markers(
            LVIV_BODY_CANCEL
        )
        self.assertTrue(is_cancel)
        self.assertEqual(orig_id, "m-d4s-1-orig")
        # Reschedule carries an original
        # message id; not a cancellation.
        is_cancel2, orig_id2 = source._parse_cancellation_markers(
            LVIV_BODY_RESCHEDULE
        )
        self.assertFalse(is_cancel2)
        self.assertEqual(orig_id2, "m-d4s-1-orig")

    def test_telegram_get_updates_to_source_messages(self):
        # Build a fake aiohttp ClientSession that
        # returns a single update.
        class _Resp:
            status = 200

            def __init__(self, payload):
                self._payload = payload

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def json(self):
                return self._payload

        class _Session:
            def __init__(self, payload):
                self._payload = payload
                self.calls = 0

            def get(self, url, params=None):
                self.calls += 1
                return _Resp(self._payload)

        update_payload = {
            "ok": True,
            "result": [
                {
                    "update_id": 12345,
                    "message": {
                        "message_id": 100,
                        "date": 1700000000,
                        "text": LVIV_BODY_OK,
                        "from": {"username": "loe_bot"},
                        "chat": {"id": -100987, "type": "channel"},
                    },
                }
            ],
        }
        session = _Session(update_payload)
        src = source.TelegramBotSource(
            bot_token="123:abc",
            session=session,
        )
        try:
            messages = _run(src.fetch(None))
            self.assertEqual(len(messages), 1)
            m = messages[0]
            self.assertEqual(m.source, "telegram:bot")
            self.assertEqual(m.channel, "-100987")
            self.assertEqual(m.message_id, "12345")
            self.assertEqual(m.raw_text, LVIV_BODY_OK)
            self.assertIn("Пирогова", m.address_hint or "")
        finally:
            _run(src.close())

    def test_telegram_retry_on_transient_error(self):
        """A transient HTTP error from Telegram
        is retried; the message is delivered on
        the second attempt.
        """
        call_count = {"n": 0}

        class _Resp:
            def __init__(self, status, payload=None):
                self.status = status
                self._payload = payload

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def json(self):
                if self._payload is None:
                    raise RuntimeError("nope")
                return self._payload

        class _Session:
            def get(self, url, params=None):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return _Resp(500)
                return _Resp(
                    200,
                    {
                        "ok": True,
                        "result": [
                            {
                                "update_id": 1,
                                "message": {
                                    "message_id": 1,
                                    "date": 1700000000,
                                    "text": LVIV_BODY_OK,
                                    "chat": {"id": 1},
                                },
                            }
                        ],
                    },
                )

        session = _Session()
        src = source.TelegramBotSource(
            bot_token="x:y", session=session
        )
        try:
            messages = _run(src.fetch(None))
            self.assertEqual(
                len(messages), 1,
                msg=(
                    "the second attempt must "
                    "succeed and deliver the message"
                ),
            )
            self.assertEqual(call_count["n"], 2)
        finally:
            _run(src.close())

    def test_telegram_offset_advances(self):
        """After a successful fetch, the next
        call must request the next
        ``offset`` so the same update is not
        re-ingested.
        """
        seen_params: list[dict[str, Any]] = []

        class _Resp:
            status = 200

            def __init__(self, payload):
                self._payload = payload

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def json(self):
                return self._payload

        class _Session:
            def get(self, url, params=None):
                seen_params.append(dict(params or {}))
                return _Resp(
                    {
                        "ok": True,
                        "result": [
                            {
                                "update_id": 7,
                                "message": {
                                    "message_id": 1,
                                    "date": 1700000000,
                                    "text": "hi",
                                    "chat": {"id": 1},
                                },
                            }
                        ],
                    }
                )

        session = _Session()
        src = source.TelegramBotSource(
            bot_token="x:y", session=session
        )
        try:
            _run(src.fetch(None))
            _run(src.fetch("7"))
        finally:
            _run(src.close())
        self.assertNotIn("offset", seen_params[0])
        self.assertEqual(seen_params[1].get("offset"), 8)


# --------------------------------------------------------------------
# IMAP source tests
# --------------------------------------------------------------------


class ImapSourceTests(unittest.TestCase):
    """The IMAP source extracts the body,
    filters by sender, and yields a
    ``SourceMessage``.
    """

    def test_imap_filters_by_sender(self):
        # Stub the imaplib module so we don't
        # need a live server.
        fake_imaplib = types.ModuleType("imaplib")

        class _FakeConn:
            def __init__(self, *a, **kw):
                self._calls = []

            def login(self, user, password):
                return ("OK", [b"login ok"])

            def select(self, folder):
                return ("OK", [b"1"])

            def uid(self, command, *args):
                self._calls.append((command, args))
                if command == "SEARCH":
                    # Two messages: one from the
                    # wrong sender, one from
                    # Львівобленерго.
                    return (
                        "OK",
                        [b"1 2"],
                    )
                if command == "FETCH":
                    if args[0] == "1":
                        return (
                            "OK",
                            [
                                (
                                    b"1",
                                    (
                                        b"From: spam@example.com\r\n"
                                        b"Subject: discount\r\n"
                                        b"\r\n"
                                        b"buy now"
                                    ),
                                )
                            ],
                        )
                    return (
                        "OK",
                        [
                            (
                                b"2",
                                (
                                    b"From: info@loe.lviv.ua\r\n"
                                    b"Subject: schedule\r\n"
                                    b"Message-ID: <abc@loe>\r\n"
                                    b"\r\n"
                                    + LVIV_BODY_OK.encode("utf-8")
                                ),
                            )
                        ],
                    )
                return ("OK", [None])

            def logout(self):
                return ("BYE", [None])

        def _factory(*a, **kw):
            return _FakeConn()

        fake_imaplib.IMAP4_SSL = _factory
        sys.modules["imaplib"] = fake_imaplib
        # Rebuild the ImapEmailSource with the
        # fake imaplib in place.
        import importlib
        src_re = importlib.reload(
            sys.modules[f"{_STUB_PKG}.lvivoblenergo_source"]
        )
        src = src_re.ImapEmailSource(
            imap_host="x",
            imap_port=993,
            username="u",
            password="p",
            sender_allowlist=("loe.lviv.ua",),
        )
        messages = _run(src.fetch(None))
        self.assertEqual(
            len(messages), 1,
            msg=(
                "the spam message must be filtered; "
                "only the Львівобленерго message "
                "is delivered"
            ),
        )
        m = messages[0]
        self.assertEqual(m.source, "imap:email")
        self.assertTrue(m.message_id.startswith("uid:"))
        self.assertEqual(m.raw_text, LVIV_BODY_OK)
        self.assertIn("Пирогова", m.address_hint or "")
        _run(src.close())


# --------------------------------------------------------------------
# Poller -> inbox integration
# --------------------------------------------------------------------


class PollerToInboxTests(unittest.TestCase):
    """The poller drives the source and
    forwards the messages to the production
    ``outage.ingest_with_recovery`` helper.
    The tests use a fake source that yields
    a single message; the inbox stores the
    event; a second cycle with the same id
    is a no-op.
    """

    def setUp(self):
        self._be: dict[str, Any] = {}
        self._bi: dict[str, Any] = {}
        self.es, self.ib, self._be, self._bi = _make_stores()
        self._offset = source.SourceOffset(
            storage_path=Path(
                "/tmp/.lvivoblenergo_offsets_test.json"
            )
        )
        self._offset.load()

    def _build_poller(self, messages, *, fail_until_attempt=None):
        """Build a poller with a fake source that
        yields ``messages`` from ``fetch()``.
        Optionally fail the first N attempts.
        """

        class _FakeSource(source.SourceAdapter):
            name = "telegram:bot"

            def __init__(self, msgs, fail_n):
                self._msgs = list(msgs)
                self._fail_n = fail_n
                self._attempt = 0

            async def fetch(self, last_seen):
                self._attempt += 1
                if (
                    self._fail_n is not None
                    and self._attempt <= self._fail_n
                ):
                    raise RuntimeError(
                        f"simulated network error "
                        f"on attempt {self._attempt}"
                    )
                return list(self._msgs)

            async def close(self):
                pass

        fake = _FakeSource(
            messages, fail_until_attempt
        )
        pol = poller.LvivoblenergoPoller(
            source=fake,
            ingest_fn=poller.build_ingest_fn(
                self.es, self.ib
            ),
            offset=self._offset,
        )
        return pol

    def test_first_ingest_stores_event_and_advances_offset(self):
        msg = source.SourceMessage(
            source="telegram:bot",
            channel="-100987",
            message_id="1",
            raw_text=LVIV_BODY_OK,
            received_at="2026-10-14T11:00:00+00:00",
            sender="loe_bot",
            address_hint="вул. Пирогова, 12",
        )
        p = self._build_poller([msg])
        status = _run(p.run_once())
        # The event is stored.
        self.assertEqual(
            len(self.es.list_events()), 1,
            msg=(
                "the inbox must store the event "
                "from the Telegram source"
            ),
        )
        # The status reflects success.
        self.assertEqual(status.fetched_total, 1)
        self.assertEqual(status.ingested_total, 1)
        self.assertEqual(status.failed_total, 0)
        # The offset is advanced.
        self.assertEqual(status.last_offset, "1")
        # The InboxStore has one entry.
        self.assertEqual(len(self.ib.list()), 1)

    def test_second_ingest_with_same_offset_is_noop(self):
        """After a successful cycle the offset
        is persisted; a second cycle with the
        same offset re-feeds the same
        message. The inbox's idempotency
        check (the same ``message_id``) makes
        the second cycle a replay: no second
        event is created.
        """
        msg = source.SourceMessage(
            source="telegram:bot",
            channel="-100987",
            message_id="1",
            raw_text=LVIV_BODY_OK,
            received_at="2026-10-14T11:00:00+00:00",
            sender="loe_bot",
            address_hint="вул. Пирогова, 12",
        )
        p = self._build_poller([msg])
        # First cycle: the source returns the
        # message; the inbox stores the event;
        # the poller advances the offset.
        status1 = _run(p.run_once())
        self.assertEqual(status1.ingested_total, 1)
        # The persisted offset is the
        # ``message_id`` of the first message.
        self.assertEqual(self._offset.get("telegram:bot"), "1")
        # Second cycle: the same poller, the
        # same source. The poller fetches with
        # ``last_seen="1"`` -- the fake source
        # ignores ``last_seen`` and returns the
        # same message. The inbox sees the
        # ``message_id`` already in the inbox
        # and returns a replay. The cumulative
        # ``fetched_total`` is 2 (one per
        # cycle); ``ingested_total`` is 1 (the
        # second is a replay).
        status2 = _run(p.run_once())
        self.assertEqual(
            len(self.es.list_events()), 1,
            msg=(
                "the second cycle must not create "
                "a second event for the same "
                "message_id"
            ),
        )
        self.assertEqual(status2.fetched_total, 2)
        # The replay still counts as a
        # successful ingest at the poller
        # level: cumulative ingested_total is
        # 2 (first cycle + replay). The
        # important assertion is that the
        # event store still has 1 event.
        self.assertEqual(status2.ingested_total, 2)

    def test_telegram_transient_failure_retries_and_succeeds(self):
        """A transient network error in the
        Telegram poll is recorded as a
        failure; the next call succeeds and
        delivers the message. The poller's
        ``run_once`` returns the status with
        ``last_error`` set; it does NOT raise
        (the periodic loop swallows errors so
        a transient blip does not kill the
        poller).
        """
        msg = source.SourceMessage(
            source="telegram:bot",
            channel="-100987",
            message_id="1",
            raw_text=LVIV_BODY_OK,
            received_at="2026-10-14T11:00:00+00:00",
            sender="loe_bot",
            address_hint="вул. Пирогова, 12",
        )
        p = self._build_poller(
            [msg], fail_until_attempt=1
        )
        # First run_once: the source fails
        # (attempt 1 <= fail_n=1). The
        # ``run_once`` returns the status with
        # ``last_error`` set.
        status1 = _run(p.run_once())
        self.assertIsNotNone(
            status1.last_error,
            msg=(
                "the first run_once must record "
                "the fetch failure in last_error"
            ),
        )
        self.assertEqual(status1.fetched_total, 0)
        self.assertEqual(status1.ingested_total, 0)
        # Second run_once: the source's
        # attempt counter is now 2, which is
        # > the fail_n threshold; the fetch
        # succeeds and the message is
        # delivered.
        status2 = _run(p.run_once())
        self.assertEqual(status2.ingested_total, 1)
        self.assertEqual(
            len(self.es.list_events()), 1
        )

    def test_cancellation_message_cancels_existing_event(self):
        """A real Lvivoblenergo cancellation
        message (carrying the original
        ``message_id``) routes through the
        production cancellation path: the
        existing event is cancelled.
        """
        # 1. Original event.
        msg_orig = source.SourceMessage(
            source="telegram:bot",
            channel="-100987",
            message_id="m-d4s-1-orig",
            raw_text=LVIV_BODY_OK,
            received_at="2026-10-14T11:00:00+00:00",
            sender="loe_bot",
            address_hint="вул. Пирогова, 12",
        )
        p1 = self._build_poller([msg_orig])
        _run(p1.run_once())
        original_id = (
            self.es.list_events()[0].id
        )
        self.assertEqual(
            self.es.get(original_id).status,
            "pending",
        )
        # 2. Cancellation message with the
        # original ``message_id``.
        msg_cancel = source.SourceMessage(
            source="telegram:bot",
            channel="-100987",
            message_id="m-d4s-1-cancel",
            raw_text=LVIV_BODY_CANCEL,
            received_at="2026-10-14T12:00:00+00:00",
            sender="loe_bot",
            address_hint="вул. Пирогова, 12",
            original_message_id="m-d4s-1-orig",
        )
        p2 = self._build_poller([msg_cancel])
        status = _run(p2.run_once())
        self.assertEqual(status.ingested_total, 1)
        # The original event is cancelled.
        cancelled = self.es.get(original_id)
        self.assertEqual(
            cancelled.status, "cancelled",
            msg=(
                "the cancellation must mark the "
                "original event as cancelled; got "
                f"status={cancelled.status!r}"
            ),
        )

    def test_reschedule_forms_chain(self):
        """A reschedule with a different
        ``message_id`` is a new correction:
        the original gets a new
        ``superseded_by`` link, the previous
        replacement is also superseded.
        """
        msg_orig = source.SourceMessage(
            source="telegram:bot",
            channel="-100987",
            message_id="m-r-1-orig",
            raw_text=LVIV_BODY_OK,
            received_at="2026-10-14T11:00:00+00:00",
            sender="loe_bot",
            address_hint="вул. Пирогова, 12",
        )
        _run(self._build_poller([msg_orig]).run_once())
        original_id = self.es.list_events()[0].id

        # Reschedule 1.
        msg_r1 = source.SourceMessage(
            source="telegram:bot",
            channel="-100987",
            message_id="m-r-1-r1",
            raw_text=LVIV_BODY_RESCHEDULE,
            received_at="2026-10-14T12:00:00+00:00",
            sender="loe_bot",
            address_hint="вул. Пирогова, 12",
            original_message_id="m-r-1-orig",
        )
        _run(self._build_poller([msg_r1]).run_once())
        # Original now points at repl_1.
        original_after = self.es.get(original_id)
        self.assertIsNotNone(
            original_after.superseded_by
        )
        repl_1_id = original_after.superseded_by
        repl_1 = self.es.get(repl_1_id)
        # repl_1's notes record the supersession
        # link.
        self.assertIn(
            f"supersedes={original_id}",
            repl_1.notes or "",
        )

    def test_real_lvivoblenergo_notification_end_to_end(self):
        """End-to-end: a realistic
        Львівобленерго per-address notification
        delivered through the poller to the
        inbox to the EventStore.
        """
        # 1. Original event.
        msg_orig = source.SourceMessage(
            source="telegram:bot",
            channel="-100987",
            message_id="real-1-orig",
            raw_text=(
                "вул. Пирогова, 12: 15.10.2026 09:00-18:00\n"
                "Джерело: ПрАТ «Львівобленерго»"
            ),
            received_at="2026-10-14T11:00:00+00:00",
            sender="loe_bot",
            address_hint="вул. Пирогова, 12",
        )
        _run(
            self._build_poller([msg_orig]).run_once()
        )
        self.assertEqual(len(self.es.list_events()), 1)
        ev = self.es.list_events()[0]
        self.assertEqual(ev.status, "pending")
        # The inbox has the message.
        self.assertEqual(len(self.ib.list()), 1)
        # 2. A reschedule with a new
        #    ``message_id`` and a new window.
        msg_resched = source.SourceMessage(
            source="telegram:bot",
            channel="-100987",
            message_id="real-1-resched",
            raw_text=(
                "Перенесено: вул. Пирогова, 12\n"
                "Новий час: 15.10.2026 14:00-22:00\n"
                "Попереднє повідомлення: real-1-orig"
            ),
            received_at="2026-10-14T12:00:00+00:00",
            sender="loe_bot",
            address_hint="вул. Пирогова, 12",
            original_message_id="real-1-orig",
        )
        _run(
            self._build_poller([msg_resched]).run_once()
        )
        # The event store has 2 events.
        self.assertEqual(len(self.es.list_events()), 2)
        # The original points at the
        # replacement.
        self.assertIsNotNone(
            self.es.get(ev.id).superseded_by
        )
        # 3. The reschedule is cancelled. The
        #    cancellation message references
        #    the LATEST replacement's Telegram
        #    ``message_id`` (the operator's
        #    intent is to undo the most recent
        #    correction). The chain stays
        #    intact: the OLD and the previous
        #    replacement keep their
        #    ``superseded_by`` links and are not
        #    re-activated by the runtime.
        latest_message_id = "real-1-resched"
        msg_cancel = source.SourceMessage(
            source="telegram:bot",
            channel="-100987",
            message_id="real-1-cancel",
            raw_text=(
                "Скасовано: вул. Пирогова, 12\n"
                f"Первинне повідомлення: {latest_message_id}"
            ),
            received_at="2026-10-14T13:00:00+00:00",
            sender="loe_bot",
            address_hint="вул. Пирогова, 12",
            original_message_id=latest_message_id,
        )
        _run(
            self._build_poller([msg_cancel]).run_once()
        )
        # The latest replacement is cancelled.
        latest_id = self.es.get(ev.id).superseded_by
        self.assertEqual(
            self.es.get(latest_id).status,
            "cancelled",
        )


if __name__ == "__main__":
    unittest.main()
