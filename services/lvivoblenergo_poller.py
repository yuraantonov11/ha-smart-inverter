"""Lvivoblenergo poller -- bridges the
source adapters to the Phase 4 v2 inbox.

The poller is a thin HA integration component
that:

  1. Loads the configured source adapter
     (Telegram bot or IMAP email).
  2. Maintains a per-source offset
     (``SourceOffset``) so a restart does not
     re-ingest old messages.
  3. On a fixed cadence (default 60 s), calls
     ``source.fetch(last_seen)`` and forwards
     each ``SourceMessage`` to the inbox
     adapter via the production
     ``services.outage.ingest_with_recovery``
     helper (so the recovery_pending state
     applies to source-driven traffic too).
  4. Persists the new offset only after a
     successful ingest; a transient failure
     leaves the offset untouched so the next
     poll retries.
  5. Surfaces a ``poller status`` dict for
     HA diagnostics and tests.

The poller is a regular async coroutine;
it can be driven from ``async_setup_entry``
or a dedicated ``DataUpdateCoordinator``. The
tests drive it directly via ``run_once``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .lvivoblenergo_source import (
    SourceAdapter,
    SourceMessage,
    SourceOffset,
    _strip_reschedule_prefix,
)
from .outage_inbox import (
    NotificationAdapterV2,
    NotificationParserV2,
    NotificationSourceV2,
    RawNotificationV2,
)

_LOGGER = logging.getLogger(__name__)


# TelegramBotSource exposes "telegram:bot" in
# ``SourceMessage.source``; IMAP exposes
# "imap:email". These map to the
# ``NotificationSourceV2`` enum so the inbox
# adapter's ``raw.source`` field carries the
# right transport.
_SOURCE_TO_TRANSPORT: dict[str, NotificationSourceV2] = {
    "telegram:bot": NotificationSourceV2.TELEGRAM,
    "telegram:public": NotificationSourceV2.TELEGRAM,
    "imap:email": NotificationSourceV2.EMAIL,
}


@dataclass
class PollerStatus:
    """Last poller cycle status -- surfaced
    to HA as a sensor attribute.
    """

    source: str
    last_run_at: str | None = None
    last_error: str | None = None
    fetched_total: int = 0
    ingested_total: int = 0
    failed_total: int = 0
    last_offset: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "last_run_at": self.last_run_at,
            "last_error": self.last_error,
            "fetched_total": self.fetched_total,
            "ingested_total": self.ingested_total,
            "failed_total": self.failed_total,
            "last_offset": self.last_offset,
        }


class LvivoblenergoPoller:
    """Drives a single ``SourceAdapter`` and
    forwards messages to the inbox.
    """

    def __init__(
        self,
        source: SourceAdapter,
        ingest_fn: Any,
        offset: SourceOffset,
        *,
        poll_interval_s: float = 60.0,
        tz_name: str = "Europe/Kyiv",
    ) -> None:
        self._source = source
        self._ingest_fn = ingest_fn
        self._offset = offset
        self._interval = poll_interval_s
        self._tz_name = tz_name
        self._status = PollerStatus(source=source.name)
        self._task: asyncio.Task[Any] | None = None
        self._stop = asyncio.Event()

    @property
    def status(self) -> PollerStatus:
        return self._status

    @property
    def source_name(self) -> str:
        return self._source.name

    def start(self) -> None:
        """Start the periodic poll loop. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stop the periodic poll loop."""
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(
                    self._task, timeout=5.0
                )
            except (asyncio.TimeoutError, Exception):
                if not self._task.done():
                    self._task.cancel()
        self._task = None
        await self._source.close()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "LvivoblenergoPoller: cycle "
                    "error: %r",
                    exc,
                    exc_info=True,
                )
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._interval,
                )
            except asyncio.TimeoutError:
                continue

    async def run_once(self) -> PollerStatus:
        """Run one fetch + ingest cycle. Returns
        the updated status.
        """
        self._status.last_run_at = (
            datetime.now(tz=timezone.utc).isoformat()
        )
        self._status.last_error = None
        offset_key = self._source.name
        last_seen = self._offset.get(offset_key)
        try:
            messages = await self._source.fetch(
                last_seen
            )
        except Exception as exc:  # noqa: BLE001
            self._status.last_error = repr(exc)
            _LOGGER.warning(
                "LvivoblenergoPoller: fetch "
                "failed: %r",
                exc,
            )
            return self._status
        self._status.fetched_total += len(messages)
        last_committed_offset = last_seen
        for message in messages:
            try:
                await self._ingest_message(message)
            except Exception as exc:  # noqa: BLE001
                self._status.failed_total += 1
                self._status.last_error = repr(exc)
                _LOGGER.warning(
                    "LvivoblenergoPoller: ingest "
                    "failed for %s: %r",
                    message.message_id,
                    exc,
                )
                # Stop the cycle on a hard
                # failure; the next cycle
                # retries from the last
                # committed offset.
                break
            last_committed_offset = message.message_id
        # Persist the offset only after the
        # messages were committed.
        if last_committed_offset is not None and (
            last_seen is None
            or last_committed_offset != last_seen
        ):
            self._offset.set(
                offset_key, last_committed_offset
            )
        self._status.last_offset = last_committed_offset
        return self._status

    async def _ingest_message(
        self, message: SourceMessage
    ) -> None:
        """Convert a ``SourceMessage`` into a
        ``RawNotificationV2`` and call the
        production handler body.
        """
        raw = self._to_raw(message)
        outcome = await self._ingest_fn(raw)
        if isinstance(outcome, dict):
            action = outcome.get("action")
            if action == "recovery_pending":
                # The inbox is unavailable.
                # The next cycle will retry the
                # same message (the offset was
                # not advanced).
                _LOGGER.warning(
                    "LvivoblenergoPoller: recovery "
                    "pending for %s; the next cycle "
                    "retries",
                    message.message_id,
                )
                raise RuntimeError(
                    "inbox recovery_pending: "
                    f"{outcome.get('reason', '')}"
                )
            if action in (
                "added",
                "cancelled",
                "superseded",
                "updated_pending",
                "duplicate_event",
            ):
                self._status.ingested_total += 1
                return
            if outcome.get("idempotent_replay"):
                self._status.ingested_total += 1
                return
        # Unknown outcome shape -- the test
        # will catch this; production logs.
        _LOGGER.warning(
            "LvivoblenergoPoller: unknown "
            "ingest outcome: %r",
            outcome,
        )

    def _to_raw(
        self, message: SourceMessage
    ) -> RawNotificationV2:
        transport = _SOURCE_TO_TRANSPORT.get(
            message.source,
            NotificationSourceV2.EMAIL,
        )
        # Strip the reschedule prefix so the v2
        # parser does not see "Перенесено:" /
        # "Postponed:" as a cancellation keyword.
        # The reschedule semantics travel via
        # ``original_message_id`` (set on the
        # SourceMessage by the source's
        # cancellation-marker parser).
        return RawNotificationV2(
            source=transport,
            channel=message.channel,
            message_id=message.message_id,
            received_at=message.received_at,
            raw_text=_strip_reschedule_prefix(
                message.raw_text
            ),
            sender=message.sender,
            address_hint=message.address_hint,
            original_message_id=(
                message.original_message_id
            ),
        )


def build_ingest_fn(
    event_store: Any,
    inbox: Any,
    *,
    tz_name: str = "Europe/Kyiv",
) -> Any:
    """Build an async ``ingest_fn(raw)`` that
    calls ``outage.ingest_with_recovery`` for
    the given stores. The poller is
    constructed with this so a single
    production helper does the
    fetch -> validate -> inbox -> event ->
    plan pipeline.
    """
    from . import outage as _outage

    async def _ingest(raw: RawNotificationV2) -> Any:
        parser = NotificationParserV2(tz_name)
        adapter = NotificationAdapterV2(parser)
        return await _outage.ingest_with_recovery(
            event_store=event_store,
            inbox=inbox,
            raw=raw,
            parser=parser,
            adapter=adapter,
        )

    return _ingest
