"""Lvivoblenergo source adapters (Phase 4 v3).

Two concrete source adapters for ingesting
official Львівобленерго outage notifications:

  * ``TelegramBotSource`` — long-poll
    ``getUpdates`` on a Telegram bot the
    operator registers via BotFather and
    stores in HA ``secrets.yaml``. The
    operator asks the Львівобленерго
    personal bot (@loeserviceBot) to forward
    per-address schedules to our bot.

  * ``ImapEmailSource`` — IMAP IDLE on the
    email address the operator registers in
    the Львівобленерго personal cabinet at
    ``info.loe.lviv.ua``. IMAP credentials
    live in HA ``secrets.yaml`` (operator-side).

Both adapters return ``RawNotificationV2``
objects, validated by
``services.outage_inbox.NotificationParserV2``.
The pipeline ``fetch -> validate -> inbox ->
event -> plan`` is shared.

Why these two:
  * The Львівобленерго official
    ``poweroff.loe.lviv.ua`` site is a
    Cloudflare bot-protected React SPA whose
    per-address schedule API is not
    unauthenticated. Personal-cabinet
    registration is required.
  * The public Telegram channel
    ``@lvivoblenergo`` does not post the
    per-address schedule; it points users to
    the personal bot and the cabinet.
  * The ``bezsvitla.com.ua`` aggregator is
    unofficial and is therefore not a
    primary source. Its scraping contract is
    unstable.

Operator configuration (no secrets in
chat)::
  * TelegramBotSource:
      telegram_bot_token: secret in
        ``secrets.yaml`` under
        ``powmr_inverter.lvivoblenergo_bot_token``.
      telegram_chat_id: optional allowed
        sender (empty = any sender).
  * ImapEmailSource:
      imap_host: ``imap.gmail.com`` (default
        port 993) or the operator's IMAP
        server.
      imap_user / imap_password: secret in
        ``secrets.yaml``.
      imap_sender_allowlist: optional
        (default = the Львівобленерго sender
        domain).

Both adapters:
  * Are stateful: a persistent offset file
    records the last fetched message id so
    a restart does not re-ingest old
    messages.
  * Have retry with exponential backoff on
    transient network failures.
  * Convert the source-specific payload
    (Telegram message text, IMAP email body)
    into ``RawNotificationV2`` with
    ``address_hint`` extracted from the
    body (or left ``None`` if the source
    does not carry the operator's address).
  * Call the existing
    ``services/outage_inbox.py`` adapter to
    finish the ``inbox -> event -> plan``
    pipeline via the registered service
    handler.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

_LOGGER = logging.getLogger(__name__)

# Львівобленерго's official sender domain
# (the personal cabinet sends from
# ``info@loe.lviv.ua`` or similar). The
# IMAP adapter filters on this domain when
# no explicit allowlist is set.
DEFAULT_SENDER_ALLOWLIST: tuple[str, ...] = (
    "loe.lviv.ua",
    "lvivoblenergo.com.ua",
)

# Telegram message length cap (per
# getUpdates). Львівобленерго personal-bot
# messages are short (one schedule line per
# outage). 4096 is the historical cap; 64 KB
# is the current Telegram limit.
_TG_MAX_TEXT = 65536


@dataclass(frozen=True)
class SourceMessage:
    """A single message produced by a source
    adapter. The source produces a
    ``SourceMessage``; the bridge in
    ``lvivoblenergo_poller`` converts it to
    the ``RawNotificationV2`` shape the
    inbox adapter expects.
    """

    source: str  # "telegram:bot" | "imap:email"
    channel: str  # bot username | email address
    message_id: str  # Telegram update_id | IMAP UID
    raw_text: str
    received_at: str  # offset-aware ISO 8601
    sender: str = ""
    address_hint: str | None = None
    # When set, the bridge treats the
    # notification as a cancellation of the
    # prior message (the Львівобленерго
    # personal bot appends "Скасовано" on
    # cancellations).
    is_cancellation: bool = False
    # When the operator's personal bot
    # forwards a per-address reschedule, the
    # body carries the prior message id as
    # ``original: <id>``. The bridge uses this
    # to link the new event to the prior
    # one.
    original_message_id: str | None = None


class SourceAdapter(ABC):
    """Abstract base for a Львівобленерго source."""

    name: str = "abstract"

    @abstractmethod
    async def fetch(
        self, last_seen: str | None
    ) -> Iterable[SourceMessage]:
        """Yield messages newer than
        ``last_seen``. Each adapter is
        responsible for its own offset
        bookkeeping.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release network resources."""


# --------------------------------------------------------------------
# Telegram bot source
# --------------------------------------------------------------------


_TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramBotSource(SourceAdapter):
    """Long-poll ``getUpdates`` on a Telegram
    bot the operator registers via BotFather.

    Configuration::

        source = TelegramBotSource(
            bot_token=...,
            allowed_chat_ids=None,  # any chat
            session=aiohttp.ClientSession(),
        )

    The source is stateless across calls;
    the poller keeps the last
    ``update_id`` and passes it as
    ``last_seen``.
    """

    name = "telegram:bot"

    def __init__(
        self,
        bot_token: str,
        *,
        allowed_chat_ids: Iterable[int] | None = None,
        session: Any | None = None,
        address_hint_resolver: Any | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._allowed_chat_ids = (
            set(allowed_chat_ids)
            if allowed_chat_ids is not None
            else None
        )
        self._session = session
        self._owns_session = session is None
        # When the operator supplies an explicit
        # ``address_hint_resolver`` (e.g. to
        # bind a Telegram chat id to a specific
        # personal-cabinet address), the source
        # uses it. When it is ``None``, the
        # source falls back to the default
        # ``_extract_address`` heuristic so the
        # address appears in the inbox key even
        # without operator-side configuration.
        self._address_hint_resolver = (
            address_hint_resolver
        )

    async def _ensure_session(self) -> Any:
        if self._session is None:
            try:
                import aiohttp  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "TelegramBotSource requires "
                    "aiohttp; install it via "
                    "homeassistant/aiohttp or "
                    "add it to the integration "
                    f"requirements: {exc}"
                )
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def fetch(
        self, last_seen: str | None
    ) -> list[SourceMessage]:
        """Poll ``getUpdates`` once. Return
        the messages newer than ``last_seen``
        (an ``update_id`` string).
        """
        session = await self._ensure_session()
        params: dict[str, Any] = {
            "timeout": 25,  # long-poll
            "allowed_updates": ["message"],
        }
        if last_seen is not None:
            try:
                params["offset"] = int(last_seen) + 1
            except ValueError:
                # Operator gave us a non-int
                # last_seen. Skip the offset to
                # avoid skipping messages.
                _LOGGER.warning(
                    "TelegramBotSource: invalid "
                    "last_seen=%r; ignoring", last_seen
                )
        url = (
            f"{_TELEGRAM_API_BASE}/bot"
            f"{self._bot_token}/getUpdates"
        )
        # Retry on transient network errors.
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                async with session.get(
                    url, params=params
                ) as resp:
                    if resp.status == 429:
                        # Rate-limited. Honour
                        # ``retry_after`` if
                        # present.
                        body = await resp.json()
                        retry_after = (
                            body.get("parameters", {}).get(
                                "retry_after", 5
                            )
                        )
                        _LOGGER.warning(
                            "TelegramBotSource: "
                            "rate-limited; sleeping "
                            "%s s",
                            retry_after,
                        )
                        await asyncio.sleep(
                            float(retry_after)
                        )
                        continue
                    if resp.status >= 500:
                        last_exc = RuntimeError(
                            f"Telegram API "
                            f"HTTP {resp.status}"
                        )
                        await asyncio.sleep(
                            2 ** attempt
                        )
                        continue
                    payload = await resp.json()
                if not payload.get("ok"):
                    raise RuntimeError(
                        f"Telegram API error: "
                        f"{payload!r}"
                    )
                break
            except (
                asyncio.TimeoutError,
                ConnectionError,
            ) as exc:
                last_exc = exc
                await asyncio.sleep(2 ** attempt)
        else:  # pragma: no cover -- exhausted
            raise RuntimeError(
                f"TelegramBotSource: getUpdates "
                f"failed after retries: {last_exc!r}"
            )
        results: list[SourceMessage] = []
        for update in payload.get("result", []) or []:
            update_id = str(update.get("update_id", ""))
            message = update.get("message") or update.get(
                "edited_message"
            )
            if message is None:
                continue
            chat = message.get("chat", {}) or {}
            chat_id = chat.get("id")
            if (
                self._allowed_chat_ids is not None
                and chat_id not in self._allowed_chat_ids
            ):
                continue
            raw_text = message.get("text") or message.get(
                "caption"
            ) or ""
            if not raw_text or len(raw_text) > _TG_MAX_TEXT:
                continue
            text = _strip_reschedule_prefix(raw_text)
            sender = (
                (message.get("from", {}) or {}).get(
                    "username"
                )
                or ""
            )
            date = message.get("date")
            received_at = (
                _epoch_to_iso(date)
                if date
                else _now_iso_utc()
            )
            address_hint = None
            if self._address_hint_resolver is not None:
                address_hint = (
                    self._address_hint_resolver(text)
                )
            else:
                # Fall back to the body-text
                # heuristic so a single-personal-
                # bot operator gets an
                # ``address_hint`` without extra
                # config.
                address_hint = _extract_address(text)
            (
                is_cancellation,
                original_message_id,
            ) = _parse_cancellation_markers(text)
            results.append(
                SourceMessage(
                    source=self.name,
                    channel=str(chat_id),
                    message_id=update_id,
                    raw_text=text,
                    received_at=received_at,
                    sender=sender,
                    address_hint=address_hint,
                    is_cancellation=is_cancellation,
                    original_message_id=(
                        original_message_id
                    ),
                )
            )
        return results


class TelegramPublicSource(SourceAdapter):
    """Scrape the public Telegram preview at
    ``https://t.me/s/<channel>``. This is a
    NO-AUTHENTICATION path: Telegram renders
    the public channel's recent messages as
    plain HTML. The Bezsvitla aggregator
    channel
    (``https://t.me/s/lvivska_bezsvitla``)
    republishes Львівобленерго per-group
    schedules (``Черга 1.1: 🕒 09:00-11:00``)
    with ``Джерело: Львівобленерго``. This is
    the only unauthenticated structured feed
    of Львівобленерго data we have. The
    per-group format is informational only --
    the operator's personal cabinet knows
    which group the operator's address
    belongs to.
    """

    name = "telegram:public"

    def __init__(
        self,
        channel: str,
        *,
        session: Any | None = None,
        max_messages: int = 5,
    ) -> None:
        self._channel = channel
        self._session = session
        self._owns_session = session is None
        self._max = max_messages

    async def _ensure_session(self) -> Any:
        if self._session is None:
            try:
                import aiohttp  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "TelegramPublicSource requires "
                    "aiohttp: " + str(exc)
                )
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def fetch(
        self, last_seen: str | None
    ) -> list[SourceMessage]:
        """Fetch the most recent messages from
        the public channel preview. The
        ``last_seen`` filter is a substring
        match against the message text (e.g.
        the last-seen message id's text). This
        is a best-effort filter; the public
        preview does not expose a stable
        update id.
        """
        url = f"https://t.me/s/{self._channel}"
        # The public preview is a static
        # HTML page. We use ``urllib`` (always
        # available) instead of ``aiohttp`` so
        # the source works in any environment
        # without an extra dependency.
        last_exc: Exception | None = None
        html = ""
        for attempt in range(3):
            try:
                html = await asyncio.to_thread(
                    _urllib_fetch, url
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                await asyncio.sleep(2 ** attempt)
        if not html:
            _LOGGER.warning(
                "TelegramPublicSource: fetch "
                "failed for %s: %r",
                url,
                last_exc,
            )
            return []
        # Extract the most recent N messages.
        # The page layout is::
        #   <div class="tgme_widget_message_text ...">
        #     <b>...</b> text ...
        #   </div>
        #   <time datetime="2026-04-09T...">
        import re as _re
        # Match the data-post id + the body +
        # the time.
        items = _re.findall(
            r'data-post="[^/]+/(\d+)"[^>]*>.*?'
            r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>.*?'
            r'<time[^>]+datetime="([^"]+)"',
            html,
            flags=_re.DOTALL,
        )
        results: list[SourceMessage] = []
        for post_id, raw_html, iso_ts in items[
            : self._max
        ]:
            text = _re.sub(r"<[^>]+>", "", raw_html)
            text = _re.sub(r"\s+", " ", text).strip()
            if not text:
                continue
            if last_seen is not None and last_seen in text:
                # Already processed.
                continue
            (
                is_cancellation,
                original_message_id,
            ) = _parse_cancellation_markers(text)
            address_hint = _extract_address(text)
            results.append(
                SourceMessage(
                    source=self.name,
                    channel=self._channel,
                    message_id=str(post_id),
                    raw_text=text,
                    received_at=iso_ts,
                    sender=f"@{self._channel}",
                    address_hint=address_hint,
                    is_cancellation=is_cancellation,
                    original_message_id=(
                        original_message_id
                    ),
                )
            )
        return results


def _urllib_fetch(url: str, *, timeout: float = 15.0) -> str:
    """Synchronous ``urllib`` GET. Returns the
    body as text. Raises on non-2xx.
    """
    import urllib.request
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status >= 400:
            raise RuntimeError(
                f"HTTP {resp.status} for {url}"
            )
        data = resp.read()
    # ``read()`` returns bytes; the public
    # preview is UTF-8.
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------
# IMAP email source
# --------------------------------------------------------------------


class ImapEmailSource(SourceAdapter):
    """IMAP IDLE poller for the operator's
    personal Львівобленерго cabinet email
    subscription.

    Configuration::

        source = ImapEmailSource(
            imap_host="imap.gmail.com",
            imap_port=993,
            username=...,
            password=...,
            sender_allowlist=("loe.lviv.ua",),
            loop=asyncio.get_running_loop(),
        )

    The source is stateless; the poller
    persists the last-seen ``UID`` (or
    ``Message-ID``) to a small file.
    """

    name = "imap:email"

    def __init__(
        self,
        imap_host: str,
        imap_port: int,
        username: str,
        password: str,
        *,
        sender_allowlist: Iterable[str] | None = None,
        use_ssl: bool = True,
        folder: str = "INBOX",
    ) -> None:
        self._host = imap_host
        self._port = imap_port
        self._user = username
        self._password = password
        self._ssl = use_ssl
        self._folder = folder
        self._allowlist = (
            tuple(s.lower() for s in sender_allowlist)
            if sender_allowlist
            else DEFAULT_SENDER_ALLOWLIST
        )
        # The IMAP connection is a blocking
        # socket. The poller runs it in a
        # thread to keep the event loop free.
        self._conn: Any = None

    @staticmethod
    def _run_in_thread(coro_factory: Any) -> Any:
        """Helper: run a blocking IMAP call
        in a thread. Returns the coroutine
        result.
        """
        import asyncio as _asyncio

        async def _wrapper() -> Any:
            loop = _asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, coro_factory
            )

        return _wrapper()

    def _connect(self) -> Any:
        import imaplib  # type: ignore[import-not-found]
        if self._ssl:
            conn = imaplib.IMAP4_SSL(
                self._host, self._port
            )
        else:
            conn = imaplib.IMAP4(self._host, self._port)
        conn.login(self._user, self._password)
        conn.select(self._folder)
        return conn

    def _fetch(
        self, last_seen_uid: int
    ) -> list[tuple[int, str, str, str]]:
        """Blocking IMAP fetch. Returns
        ``[(uid, message_id, sender, body), ...]``
        for UIDs strictly greater than
        ``last_seen_uid``.
        """
        import re as _re
        if self._conn is None:
            self._conn = self._connect()
        typ, data = self._conn.uid(
            "SEARCH", None, f"UID {last_seen_uid + 1}:*"
        )
        if typ != "OK" or not data or not data[0]:
            return []
        uids = [
            int(x) for x in data[0].split() if x.isdigit()
        ]
        if not uids:
            return []
        out: list[tuple[int, str, str, str]] = []
        for uid in uids:
            typ, msg_data = self._conn.uid(
                "FETCH", str(uid), "(RFC822)"
            )
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = message_from_bytes(raw)
            sender = _decode_header_value(
                msg.get("From", "")
            )
            if not any(
                sender.lower().endswith(f"@{d}")
                for d in self._allowlist
            ):
                continue
            message_id = (
                _decode_header_value(
                    msg.get("Message-ID", "")
                )
                or f"uid:{uid}"
            )
            body = _extract_text_body(msg)
            if not body:
                continue
            body = _strip_reschedule_prefix(body)
            out.append(
                (uid, message_id, sender, body)
            )
        return out

    async def fetch(
        self, last_seen: str | None
    ) -> list[SourceMessage]:
        last_uid = 0
        if last_seen is not None:
            try:
                last_uid = int(last_seen)
            except ValueError:
                _LOGGER.warning(
                    "ImapEmailSource: invalid "
                    "last_seen=%r; starting from 0",
                    last_seen,
                )
        # Run the blocking IMAP fetch in a
        # thread. Retry with exponential
        # backoff on transient errors.
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                rows = await self._run_in_thread(
                    lambda: self._fetch(last_uid)
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                _LOGGER.warning(
                    "ImapEmailSource: fetch "
                    "attempt %s failed: %r",
                    attempt + 1,
                    exc,
                )
                # Reconnect on next attempt.
                try:
                    if self._conn is not None:
                        self._conn.logout()
                except Exception:  # noqa: BLE001
                    pass
                self._conn = None
                await asyncio.sleep(2 ** attempt)
        else:  # pragma: no cover
            raise RuntimeError(
                f"ImapEmailSource: fetch failed "
                f"after retries: {last_exc!r}"
            )
        out: list[SourceMessage] = []
        for uid, message_id, sender, body in rows:
            (
                is_cancellation,
                original_message_id,
            ) = _parse_cancellation_markers(body)
            address_hint = _extract_address(body)
            out.append(
                SourceMessage(
                    source=self.name,
                    channel=self._user,
                    message_id=(
                        # Use the IMAP UID as the
                        # inbox key; the email
                        # Message-ID is logged but
                        # not used (IMAP UIDs are
                        # monotonic and unique).
                        f"uid:{uid}"
                    ),
                    raw_text=body,
                    received_at=_now_iso_utc(),
                    sender=sender,
                    address_hint=address_hint,
                    is_cancellation=is_cancellation,
                    original_message_id=(
                        original_message_id
                    ),
                )
            )
        return out

    async def close(self) -> None:
        if self._conn is None:
            return
        try:
            await self._run_in_thread(self._conn.logout)
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "ImapEmailSource: close error",
                exc_info=True,
            )
        self._conn = None


# --------------------------------------------------------------------
# Helpers shared by the two adapters
# --------------------------------------------------------------------


_CANCEL_KEYWORDS = (
    "скасовано",
    "відмінено",
    "скасування",
    "відновлено",
)

# When the Львівобленерго personal bot forwards
# a *reschedule* (a new time for the same
# outage), the body starts with
# "Перенесено:" or "Postponed:" / "Rescheduled:".
# The v2 inbox parser treats these as
# cancellation keywords (it expects the
# reschedule to come as a fresh message with
# a new time window, not as a marked
# reschedule). The source adapter strips the
# reschedule prefix so the parser sees a
# regular schedule body; the reschedule
# semantics travel via the
# ``original_message_id`` field set from the
# "Попереднє повідомлення: <id>" line.
_RESCHEDULE_PREFIX_RE = re.compile(
    r"(?im)^\s*(?:перенесено|postponed|rescheduled)\s*:\s*"
)


_RESCHEDULE_LINK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?im)^[^\n]*\boriginal(?:[_-]?(?:id|message))?\s*:\s*([A-Za-z0-9._-]+)"
    ),
    # Ukrainian: "Первинне повідомлення: <id>"
    re.compile(
        r"(?im)^[^\n]*\b(?:первинне|первинний|попередн\w*)\s*(?:повідомлення|id|message)\s*:\s*([A-Za-z0-9._\-]+)"
    ),
    re.compile(
        r"(?im)^[^\n]*\bprevious(?:[_-]?(?:id|message))?\s*:\s*([A-Za-z0-9._-]+)"
    ),
)


def _parse_cancellation_markers(
    text: str,
) -> tuple[bool, str | None]:
    """Detect the Львівобленерго personal-bot
    cancellation markers and the optional
    ``original: <id>`` link. The Львівобленерго
    personal bot uses Ukrainian markers
    ("Скасовано", "Первинне повідомлення") AND
    falls back to English ("original:",
    "previous:") when forwarding to a
    third-party bot.
    """
    lowered = text.lower()
    is_cancellation = any(
        kw in lowered for kw in _CANCEL_KEYWORDS
    )
    original_message_id: str | None = None
    for pattern in _RESCHEDULE_LINK_PATTERNS:
        m = pattern.search(text)
        if m:
            original_message_id = m.group(1)
            break
    return is_cancellation, original_message_id


_ADDRESS_PATTERNS = (
    # "вул. Пирогова, 12", "вул. Пирогова 12"
    re.compile(
        r"(?i)\b(?:вул(?:иця|\.)?\.?|вулиця)\s+"
        r"([А-ЯҐЄІЇґєіїʼA-Za-z][А-ЯҐЄІЇґєіїʼа-яҐЄІЇґєіїa-z'’\.\- ]+?)"
        r"[,\s]+(\d+[А-Яа-яA-Za-z]?)"
    ),
    # "м. Львів"
    re.compile(
        r"(?i)\b(?:м\.|місто)\s+"
        r"([А-ЯҐЄІЇґєії][а-яҐЄІЇґєії\-]+)"
    ),
)


_strip_reschedule_prefix = staticmethod(
    lambda text: _RESCHEDULE_PREFIX_RE.sub(
        "", text or "", count=1
    ).strip()
)


def _extract_address(text: str) -> str | None:
    """Best-effort address extraction from a
    Львівобленерго notification body. The
    address is informational only -- the
    inbox adapter's structured
    ``(address_hint, window)`` match is the
    authoritative path.
    """
    for pattern in _ADDRESS_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        if pattern.pattern.startswith(
            "(?i)\\b(?:вул"
        ):
            street, number = m.group(1), m.group(2)
            return f"вул. {street.strip().rstrip('.')}, {number}"
        return f"м. {m.group(1).strip()}"
    return None


def _decode_header_value(value: str) -> str:
    """Decode an RFC 2047-encoded header."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def _extract_text_body(msg: Any) -> str:
    """Extract a plain-text body from an
    email Message. Walks the parts and
    prefers ``text/plain``.
    """
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = part.get("Content-Disposition", "")
            if (
                ctype == "text/plain"
                and "attachment" not in disp.lower()
            ):
                payload = part.get_payload(
                    decode=True
                )
                if isinstance(payload, bytes):
                    return payload.decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace",
                    )
                return str(payload)
        # Fall back: first text part.
        for part in msg.walk():
            if part.get_content_type().startswith(
                "text/"
            ):
                payload = part.get_payload(
                    decode=True
                )
                if isinstance(payload, bytes):
                    return payload.decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace",
                    )
                return str(payload)
        return ""
    payload = msg.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode(
            msg.get_content_charset() or "utf-8",
            errors="replace",
        )
    return str(payload)


def _epoch_to_iso(epoch: int) -> str:
    return (
        datetime.fromtimestamp(
            int(epoch), tz=timezone.utc
        ).isoformat()
    )


def _now_iso_utc() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# --------------------------------------------------------------------
# Offset bookkeeping
# --------------------------------------------------------------------


@dataclass
class SourceOffset:
    """Persists the last seen ``message_id``
    per source so a restart does not
    re-ingest old messages.
    """

    storage_path: Path
    _offsets: dict[str, str] = field(default_factory=dict)

    def load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            self._offsets = json.loads(
                self.storage_path.read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError):
            _LOGGER.warning(
                "SourceOffset: could not read "
                "%s; starting empty",
                self.storage_path,
            )
            self._offsets = {}

    def get(self, key: str) -> str | None:
        return self._offsets.get(key)

    def set(self, key: str, value: str) -> None:
        self._offsets[key] = value
        try:
            self.storage_path.parent.mkdir(
                parents=True, exist_ok=True
            )
            self.storage_path.write_text(
                json.dumps(self._offsets),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover
            _LOGGER.warning(
                "SourceOffset: persist failed: %r",
                exc,
            )
