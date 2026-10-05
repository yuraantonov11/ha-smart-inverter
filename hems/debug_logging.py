"""HEMS debug logging utility.

Writes a structured log of every HEMS evaluation cycle to a file the
user can inspect after the day is over. The production path uses the
existing INFO/DEBUG loggers as before, but the frequent per-cycle
chatter goes here to keep the main log clean.

The log file lives in ``HEMS_DEBUG_LOG_DIR`` (``/config`` on a real HA
install) and rotates daily at midnight. When the directory does not
exist — e.g. in unit tests running outside HA — we silently disable
file writes so the test thread does not crash on ``FileNotFoundError``.
Tests that need to assert log content should use ``set_log_path`` /
``reset_log_path`` to redirect the file to a tmpdir.

T18 audit: this module no longer spawns a fresh ``threading.Thread``
for every evaluation cycle, no longer opens files on the calling
thread, and no longer blocks the event loop on ``read_recent`` or
``daily_summary``. All disk I/O is funnelled through
``_DebugLogWorker`` — a small, bounded worker pool with a queue
overflow policy. Each config entry has its own log file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue as _queue
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Public seam so tests can monkeypatch the log directory without
# touching ``/config``. Default matches Home Assistant's config dir.
_LOG_PATH: Path = Path(
    os.environ.get(
        "POWMR_DEBUG_LOG_DIR",
        "/config",
    )
) / "powmr_hems_debug.log"
_ROTATE_AT: dict[str, str] = {}  # path -> date string we last rotated

_LOGGER = logging.getLogger(__name__)


def set_log_path(path: Path | str) -> None:
    """Override the log file path. Used by tests via monkeypatch."""
    global _LOG_PATH
    _LOG_PATH = Path(path)


def reset_log_path() -> None:
    """Restore the default log path (``/config/powmr_hems_debug.log``)."""
    global _LOG_PATH
    _LOG_PATH = Path("/config") / "powmr_hems_debug.log"


# ---------------------------------------------------------------------------
# Per-entry binding
# ---------------------------------------------------------------------------
#
# When the integration has more than one config entry, each one must
# write to its own log file. ``bind_entry`` records the entry_id ->
# Path mapping; ``log_evaluation`` resolves the entry's path before
# enqueuing the record. Tests can call ``bind_entry`` to register a
# tmpdir path. ``unbind_entry`` removes the binding.

_ENTRY_PATHS: dict[str, Path] = {}
_ENTRY_LOCK = threading.Lock()


def bind_entry(entry_id: str, path: Path | str) -> None:
    """Register a log file path for a config entry."""
    with _ENTRY_LOCK:
        _ENTRY_PATHS[entry_id] = Path(path)


def unbind_entry(entry_id: str) -> None:
    """Remove the binding for a config entry."""
    with _ENTRY_LOCK:
        _ENTRY_PATHS.pop(entry_id, None)


def _resolve_log_path(entry_id: str | None) -> Path:
    """Return the log path for ``entry_id`` or the default."""
    if entry_id is not None:
        with _ENTRY_LOCK:
            bound = _ENTRY_PATHS.get(entry_id)
        if bound is not None:
            return bound
    return _LOG_PATH


# ---------------------------------------------------------------------------
# Bounded worker
# ---------------------------------------------------------------------------
#
# A small fixed-size pool of writer threads serves all log writes. The
# queue is bounded; if the queue fills, additional enqueues raise
# ``queue.Full`` and the caller (the engine path) records a single
# dropped record in the ``dropped`` counter. The worker exposes
# ``drain(timeout)`` (block until the queue empties) and
# ``shutdown(timeout)`` (block until the queue empties, then stop the
# threads). All real I/O happens here, never on the event loop.

_MAX_QUEUE = 1024
_WORKER_COUNT = 1
_WORKER_LOCK = threading.Lock()
_worker: "_DebugLogWorker | None" = None


class _DebugLogWorker:
    """Bounded worker pool for HEMS debug-log writes.

    Public attributes:
        * ``qsize()`` — current queue depth.
        * ``dropped`` — number of records dropped because the queue
          was full.
        * ``enqueue(payload, path, priority=5)`` — non-blocking; the
          path is resolved at enqueue time so per-entry writes go to
          the right file. ``priority`` follows the ``logging``
          convention; ``0`` is a real value, not a sentinel.
        * ``drain(timeout)`` — block until queue is empty.
        * ``shutdown(timeout)`` — drain and stop the worker threads.
    """

    def __init__(self, max_queue: int = _MAX_QUEUE) -> None:
        self._queue: _queue.PriorityQueue[tuple[int, int, str, Path]] = (
            _queue.PriorityQueue(maxsize=max_queue)
        )
        self._max_queue = max_queue
        self.dropped = 0
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        for i in range(_WORKER_COUNT):
            t = threading.Thread(
                target=self._serve,
                name=f"powmr-debug-log-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)
        # Monotonic counter so that equal
        # priorities still order by
        # enqueue time.
        self._seq = 0
        self._seq_lock = threading.Lock()

    def qsize(self) -> int:
        return self._queue.qsize()

    def enqueue(
        self, payload: str, path: Path, priority: int = 5
    ) -> None:
        """Non-blocking enqueue. ``priority``
        follows the ``logging`` convention:
        lower number = more important. A
        priority of 0 is honoured just
        like any other number — it is not
        a sentinel.
        """
        if self._stop.is_set():
            return
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        try:
            self._queue.put_nowait((priority, seq, payload, path))
        except _queue.Full:
            self.dropped += 1

    def drain(self, timeout: float | None = None) -> bool:
        """Block until every queued item
        has been written to disk and
        ``task_done`` has been called.
        Returns True if the queue drained.
        """
        deadline = (
            time.monotonic() + timeout if timeout is not None else None
        )
        while True:
            # ``Queue.join`` blocks until
            # every enqueued item has been
            # ``task_done``-ed, which only
            # happens after ``_write_line``
            # returns.
            if self._queue.empty() and self._queue.unfinished_tasks == 0:
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            self._queue.join() if False else time.sleep(0.01)

    def shutdown(self, timeout: float | None = None) -> bool:
        """Drain the queue, then signal the
        worker threads to exit.
        """
        drained = self.drain(timeout)
        self._stop.set()
        for t in self._threads:
            t.join(timeout=1.0)
        return drained

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                priority, seq, payload, path = self._queue.get(
                    timeout=0.1
                )
            except _queue.Empty:
                continue
            try:
                # Rotation sentinel —
                # run the rotation helper
                # on the worker thread so
                # the event loop stays
                # free. The rotation day
                # is embedded in the
                # sentinel payload
                # (``__ROTATE__:<last>``)
                # because ``_ROTATE_AT``
                # is already bumped to
                # today by the time the
                # worker picks up the
                # entry.
                if isinstance(payload, str) and payload.startswith("__ROTATE__:"):
                    last = payload[len("__ROTATE__:"):]
                    if last:
                        _rotate_paths(path, last)
                else:
                    _write_line(path, payload)
            except Exception as exc:  # pragma: no cover
                _LOGGER.debug("debug-log worker write failed: %s", exc)
            finally:
                self._queue.task_done()


def _get_worker() -> _DebugLogWorker:
    """Lazy-initialise the global worker pool."""
    global _worker
    if _worker is None:
        with _WORKER_LOCK:
            if _worker is None:
                _worker = _DebugLogWorker()
    return _worker


def shutdown_drain() -> bool:
    """Tear down the global worker pool.
    Used by reload + reload_drain hooks.
    """
    global _worker
    with _WORKER_LOCK:
        worker = _worker
        _worker = None
    if worker is None:
        return True
    return worker.shutdown(timeout=10.0)


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------
#
# ``_maybe_rotate`` is called on every ``log_evaluation`` cycle but the
# actual file-system work runs in the worker thread, so the event loop
# is never blocked.


def _rotate_paths(target: Path, last: str) -> None:
    """Synchronous rotation work —
    only call from the worker
    thread. Splits off the
    ``os.rename`` and
    ``Path.unlink`` calls so they
    never run on the event loop.
    """
    try:
        old = target.with_name(
            f"powmr_hems_debug.{last}.log"
        )
        if target.exists() and not old.exists():
            target.rename(old)
    except OSError as exc:
        _LOGGER.debug(
            "Could not rotate HEMS debug log: %s",
            exc,
        )
    try:
        cutoff = (datetime.now() - timedelta(days=3)).strftime(
            "%Y-%m-%d"
        )
        for p in target.parent.glob(
            "powmr_hems_debug.*.log"
        ):
            tag = (
                p.name.replace("powmr_hems_debug.", "")
                .replace(".log", "")
            )
            if tag < cutoff:
                p.unlink()
    except OSError:
        pass


def _maybe_rotate() -> None:
    """Roll the log file at midnight (HA local time).

    Audit T18 follow-up: only the
    metadata update (``_ROTATE_AT``)
    runs on the event loop. The
    rename + unlink are pushed to
    the worker via
    ``_rotate_paths`` so a slow
    filesystem cannot stall HA.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    last = _ROTATE_AT.get(str(_LOG_PATH))
    if last == today:
        return
    if last is None:
        _ROTATE_AT[str(_LOG_PATH)] = today
        return
    # Update the metadata first so
    # concurrent calls do not
    # double-enqueue rotation work.
    _ROTATE_AT[str(_LOG_PATH)] = today
    # Push the actual rotation
    # onto the worker queue. Use a
    # high priority so it runs
    # before user writes that target
    # the *new* file. The rotation
    # day is embedded in the
    # payload so the worker does
    # not have to re-read
    # ``_ROTATE_AT`` (which has
    # already been bumped to today).
    try:
        sentinel = "__ROTATE__:" + last
        _get_worker()._queue.put_nowait(
            (-1, 0, sentinel, _LOG_PATH)
        )
    except Exception:
        # Queue full or worker not
        # running — fall back to a
        # synchronous rotation on the
        # caller thread. We swallow
        # every error here; the worst
        # outcome is a stale file
        # name.
        try:
            _rotate_paths(_LOG_PATH, last)
        except Exception:
            pass


# Sentinel string that tells the
# worker to call ``_rotate_paths``
# instead of writing a line. The
# string is intentionally odd so
# no real payload can collide with
# it.
_ROTATE_SENTINEL = "__ROTATE__"


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------
#
# The writer is small and safe to call from any thread — it never
# raises. Production code only invokes it via the worker.


def _write_line(path: Path, line: str) -> None:
    """Append one line to ``path``. Never raises."""
    try:
        if not path.parent.exists():
            return
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        # Directory disappeared mid-test,
        # perms issue, slow filesystem,
        # etc. Skip — never block the HEMS
        # loop on logging.
        pass


# ---------------------------------------------------------------------------
# Sync API surface
# ---------------------------------------------------------------------------


def log_evaluation(
    *,
    timestamp: datetime,
    inputs: dict[str, Any],
    decision: Any,
    applied: dict[str, Any] | None,
    skip_reason: str | None = None,
    entry_id: str | None = None,
    priority: int = 5,
) -> None:
    """Append one evaluation to the debug log.

    The file write runs through the
    bounded ``_DebugLogWorker`` queue —
    the event loop is never blocked, and
    no fresh thread is spawned per
    call. ``entry_id`` routes the write
    to a per-entry log file registered
    via ``bind_entry``.
    """
    try:
        # Rotation metadata update is
        # cheap and safe to do on the
        # calling thread; the actual rename
        # + delete runs in the worker via
        # ``_write_line``.
        _maybe_rotate()
        payload = {
            "ts": timestamp.isoformat(timespec="seconds"),
            "smart_mode": inputs.get("smart_mode"),
            "soc": inputs.get("soc"),
            "pv_w": inputs.get("pv_power"),
            "load_w": inputs.get("load_power"),
            "grid_w": inputs.get("grid_power"),
            "batt_w": inputs.get("battery_power"),
            "grid_v": inputs.get("grid_voltage"),
            "grid_ok": inputs.get("grid_available"),
            "hour": timestamp.hour,
            "current_output": inputs.get("current_output"),
            "current_charger": inputs.get("current_charger"),
            "reserve_soc": inputs.get("reserve_soc"),
            "forecast_today_kwh": inputs.get("forecast_today_kwh"),
            "forecast_tomorrow_kwh": inputs.get("forecast_tomorrow_kwh"),
            "decision": {
                "reason": getattr(decision, "reason", None),
                "output_priority": getattr(decision, "output_priority", None),
                "charger_priority": getattr(decision, "charger_priority", None),
                "buzzer_off": getattr(decision, "buzzer_off", None),
                "skip": getattr(decision, "skip", None),
            },
            "applied": applied,
            "skip_reason": skip_reason,
        }
        line = json.dumps(payload, ensure_ascii=False, default=str)
        # Enqueue; the worker resolves
        # ``path`` at dequeue time so per
        # writes go to the right file.
        _get_worker().enqueue(
            line, _resolve_log_path(entry_id), priority=priority
        )
    except Exception as exc:
        # Never let logging break the HEMS
        # loop.
        _LOGGER.debug("Failed to write HEMS debug log: %s", exc)


# ---------------------------------------------------------------------------
# Async read API
# ---------------------------------------------------------------------------


def _read_recent_sync(path: Path, limit: int) -> list[dict[str, Any]]:
    """Synchronous reader — only call from a thread."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - 65536))
            tail = fh.read().decode("utf-8", errors="replace")
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        out: list[dict[str, Any]] = []
        for ln in reversed(lines[-limit:]):
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out
    except OSError:
        return []


async def read_recent(
    limit: int = 200, entry_id: str | None = None
) -> list[dict[str, Any]]:
    """Return the last ``limit`` entries
    (newest first) from the debug log.

    Async API: the file I/O runs in a
    worker thread so the HA event loop
    is never blocked, even when the
    log file is large or the filesystem
    is slow.
    """
    path = _resolve_log_path(entry_id)
    if not path.exists():
        return []
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _read_recent_sync, path, limit),
            timeout=1.0,
        )
    except (asyncio.TimeoutError, Exception):
        return []


def read_recent_sync(
    limit: int = 200, entry_id: str | None = None
) -> list[dict[str, Any]]:
    """Synchronous read — only call from a worker thread, never from
    the event loop. Kept for tests and one-shot CLI tooling."""
    path = _resolve_log_path(entry_id)
    if not path.exists():
        return []
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_read_recent_sync, path, limit)
            return fut.result(timeout=1.0)
    except Exception:
        return []


async def daily_summary(
    date_str: str | None = None,
    entry_id: str | None = None,
) -> dict[str, int]:
    """Count decisions, commands sent, and skips for the given day.

    Async API: file I/O runs in a
    worker thread; the event loop is
    never blocked.
    """
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    out = {"decisions": 0, "commands_sent": 0, "skipped": 0}
    path = _resolve_log_path(entry_id)
    if not path.exists():
        return out

    def _summarise() -> dict[str, int]:
        local = {
            "decisions": 0,
            "commands_sent": 0,
            "skipped": 0,
        }
        try:
            with path.open("r", encoding="utf-8") as fh:
                for raw_line in fh:
                    if date_str not in raw_line:
                        continue
                    try:
                        row = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    local["decisions"] += 1
                    applied = row.get("applied") or {}
                    # T18 audit follow-up:
                    # a recorded priority of ``0``
                    # is a real value — it is
                    # not None and it is not a
                    # skip. We must check ``is
                    # not None`` instead of
                    # relying on truthiness, so
                    # ``"0"`` (string) and ``0``
                    # (int) both count as a
                    # command. The legacy
                    # ``or applied.get(...)``
                    # pattern dropped these.
                    out_p = applied.get("output_priority")
                    chg_p = applied.get("charger_priority")
                    if (
                        out_p is not None
                        and out_p != ""
                    ) or (
                        chg_p is not None
                        and chg_p != ""
                    ):
                        local["commands_sent"] += 1
                    else:
                        local["skipped"] += 1
        except OSError:
            return local
        return local

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _summarise),
            timeout=2.0,
        )
    except (asyncio.TimeoutError, Exception):
        return out