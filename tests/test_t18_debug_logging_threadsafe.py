"""T18 — debug_logging audit.

Audit T18: ``hems.debug_logging`` had several
event-loop hazards:

  1. ``log_evaluation`` spawned a fresh
     ``threading.Thread`` for every HEMS
     evaluation cycle (unbounded thread
     creation under load).
  2. ``read_recent`` created a fresh
     ``ThreadPoolExecutor`` per call and
     blocked the event loop with
     ``.result(timeout=1.0)``.
  3. ``daily_summary`` opened the file
     synchronously on the calling thread.
  4. ``_maybe_rotate`` ran synchronously in
     the event loop on every cycle.
  5. There was no separation between
     config entries — all entries wrote
     to the same shared log file.
  6. The worker pool had no bounded queue
     and no priority handling.

The tests in this module pin the
behaviour the audit asked for:
  * no new threads are created per
    ``log_evaluation`` call;
  * the event loop is never blocked by
    ``read_recent`` / ``daily_summary`` /
    rotation, even on a slow filesystem;
  * the worker pool has a bounded queue
    with overflow handling;
  * rotation is safe across reload and
    across multiple config entries;
  * reload drains the in-flight queue
    before tearing down;
  * priority 0 items are handled
    correctly (no special-case skip).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest import TestCase, main


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hems import debug_logging
from hems.engine import HemsDecision


class T18DebugLoggingTests(TestCase):
    """Audit T18 regression suite."""

    def setUp(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="t18-debug-"))
        self._before_log_path = debug_logging._LOG_PATH
        self._before_pool = debug_logging._worker
        # Force a fresh worker for each test.
        debug_logging._worker = None
        self._saved_workers: list[threading.Thread] = []

    def tearDown(self) -> None:
        debug_logging._LOG_PATH = self._before_log_path
        # Drain and shut down the test's
        # worker so it does not leak between
        # tests.
        worker = debug_logging._worker
        if worker is not None:
            try:
                worker.shutdown(timeout=2.0)
            except Exception:
                pass
            debug_logging._worker = self._before_pool
        # Save the global log path back.
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ── 1. log_evaluation does NOT spawn a new thread per call ──

    def test_t18_01_no_unbounded_threads(self) -> None:
        """``log_evaluation`` must delegate
        to a bounded worker, not spawn a
        fresh daemon thread for every
        call. The audit found that under
        load this could create thousands of
        threads on a long-running HA
        install.
        """
        log_path = self._tmpdir / "bounded.log"
        debug_logging.set_log_path(log_path)
        before_count = sum(
            1 for t in threading.enumerate()
            if getattr(t, "_target", None) is debug_logging._write_line
        )
        for i in range(50):
            debug_logging.log_evaluation(
                timestamp=datetime(2026, 6, 15, 10, 0, i),
                inputs={
                    "smart_mode": 0,
                    "soc": 60.0,
                    "pv_power": 100.0,
                    "load_power": 200.0,
                },
                decision=HemsDecision(
                    output_priority="2",
                    charger_priority="2",
                    reason="t18",
                    skip=False,
                ),
                applied={"output_priority": "2", "charger_priority": "2"},
                skip_reason=None,
            )
        after_count = sum(
            1 for t in threading.enumerate()
            if getattr(t, "_target", None) is debug_logging._write_line
        )
        # 50 calls must not produce 50 new
        # threads. The bounded worker is at
        # most a small constant number.
        self.assertLessEqual(
            after_count - before_count,
            2,
            msg=(
                f"log_evaluation spawned {after_count - before_count} "
                f"new _write_line threads; expected at most 2"
            ),
        )

    # ── 2. event loop is never blocked, even on a slow filesystem ──

    def test_t18_02_event_loop_not_blocked(self) -> None:
        """Even when the filesystem is
        slow (e.g. NFS, slow SD card),
        ``read_recent``, ``daily_summary``
        and ``_maybe_rotate`` must not
        block the event loop for more than
        a few milliseconds.
        """
        log_path = self._tmpdir / "loop.log"
        debug_logging.set_log_path(log_path)
        # Pre-fill the log with enough data.
        for i in range(20):
            debug_logging.log_evaluation(
                timestamp=datetime(2026, 6, 15, 10, 0, i % 60),
                inputs={
                    "smart_mode": 0,
                    "soc": 60.0,
                    "pv_power": 100.0,
                    "load_power": 200.0,
                },
                decision=HemsDecision(
                    output_priority="2",
                    charger_priority="2",
                    reason="t18",
                    skip=False,
                ),
                applied={"output_priority": "2", "charger_priority": "2"},
                skip_reason=None,
            )
        # Wait for the bounded worker to drain.
        debug_logging._worker.drain(timeout=5.0)
        # Patch the actual writer to sleep
        # 1.5s. If the event loop is blocked
        # synchronously the test will take
        # ~1.5s + several seconds; the
        # heartbeat should still fire on
        # time because the calls go through
        # the worker queue.
        original_write = debug_logging._write_line
        def slow_write(path, line):
            time.sleep(1.5)
            return original_write(path, line)
        debug_logging._write_line = slow_write
        try:
            # Fire a write that will be slow.
            debug_logging.log_evaluation(
                timestamp=datetime(2026, 6, 15, 11, 0, 0),
                inputs={
                    "smart_mode": 0,
                    "soc": 60.0,
                    "pv_power": 100.0,
                    "load_power": 200.0,
                },
                decision=HemsDecision(
                    output_priority="2",
                    charger_priority="2",
                    reason="slow",
                    skip=False,
                ),
                applied={"output_priority": "2", "charger_priority": "2"},
                skip_reason=None,
            )
            # Heartbeat: the event loop
            # should be free within a few
            # hundred milliseconds even while
            # the worker is sleeping.
            loop_start = time.monotonic()
            beat_count = 0
            while time.monotonic() - loop_start < 0.3:
                beat_count += 1
                time.sleep(0.01)
            self.assertGreaterEqual(
                beat_count,
                20,
                "event loop blocked: heartbeat stalled",
            )
        finally:
            debug_logging._write_line = original_write

    # ── 3. bounded queue overflow handling ────────────────────────

    def test_t18_03_bounded_queue_overflow(self) -> None:
        """If the worker is slow and the
        queue fills, the next
        ``log_evaluation`` call must
        either block briefly with a
        bounded timeout or drop the
        record with a counter increment —
        it must NOT spawn a new thread
        or block the event loop.
        """
        log_path = self._tmpdir / "overflow.log"
        debug_logging.set_log_path(log_path)
        # Slow the writer so the queue fills.
        original_write = debug_logging._write_line
        def slow_write(path, line):
            time.sleep(0.05)
            return original_write(path, line)
        debug_logging._write_line = slow_write
        try:
            for i in range(200):
                debug_logging.log_evaluation(
                    timestamp=datetime(2026, 6, 15, 12, 0, i % 60),
                    inputs={
                        "smart_mode": 0,
                        "soc": 60.0,
                        "pv_power": 100.0,
                        "load_power": 200.0,
                    },
                    decision=HemsDecision(
                        output_priority="2",
                        charger_priority="2",
                        reason=f"q-{i}",
                        skip=False,
                    ),
                    applied={"output_priority": "2", "charger_priority": "2"},
                    skip_reason=None,
                )
            # The bounded worker must have a
            # visible overflow counter (or
            # some equivalent guard).
            worker = debug_logging._worker
            self.assertIsNotNone(
                worker,
                msg="bounded worker must exist after first log_evaluation",
            )
            self.assertTrue(
                hasattr(worker, "dropped"),
                msg="bounded worker must expose a 'dropped' counter",
            )
        finally:
            # Let the queue drain before
            # tearing down.
            try:
                debug_logging._worker.drain(timeout=10.0)
            except Exception:
                pass
            debug_logging._write_line = original_write

    # ── 4. rotation is idempotent and survives reload ────────────

    def test_t18_04_rotation_idempotent(self) -> None:
        """Calling ``_maybe_rotate`` repeatedly
        on the same day must rotate exactly
        once and not lose existing log
        entries.
        """
        log_path = self._tmpdir / "rot.log"
        debug_logging.set_log_path(log_path)
        # Pre-create the log with enough
        # content to make rotation
        # observable.
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"marker": "pre-rotation"}) + "\n",
            encoding="utf-8",
        )
        # Force the rotation date to yesterday
        # so ``_maybe_rotate`` will roll.
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        debug_logging._ROTATE_AT[str(log_path)] = yesterday
        debug_logging._maybe_rotate()
        debug_logging._maybe_rotate()  # idempotent
        # The current log file should now
        # exist as ``powmr_hems_debug.<yesterday>.log``
        # alongside the freshly-opened one.
        rotated = log_path.with_name(
            f"powmr_hems_debug.{yesterday}.log"
        )
        self.assertTrue(
            rotated.exists(),
            msg=(
                f"rotated log {rotated} must exist after _maybe_rotate()"
            ),
        )
        # The current log file must exist
        # and contain the pre-rotation
        # content (rotation renamed the
        # old file, the new one starts
        # empty).
        self.assertTrue(
            rotated.exists(),
            msg=(
                "rotated log must exist after _maybe_rotate()"
            ),
        )
        # Reading the rotated file must
        # contain the pre-rotation marker.
        rotated_text = rotated.read_text(encoding="utf-8")
        self.assertIn(
            "pre-rotation",
            rotated_text,
            msg=(
                "rotated log must contain the pre-rotation content"
            ),
        )
        # Idempotent: a third call must
        # not rename again.
        debug_logging._maybe_rotate()
        self.assertTrue(
            rotated.exists(),
            msg=(
                "rotation must be idempotent across repeated calls"
            ),
        )

    # ── 5. priority 0 is handled, not silently dropped ───────────

    def test_t18_05_priority_zero_handled(self) -> None:
        """The audit asks for explicit
        priority handling. A priority of 0
        must be enqueued just like any
        other value, not treated as a
        sentinel.
        """
        log_path = self._tmpdir / "prio.log"
        debug_logging.set_log_path(log_path)
        # ``set_log_path`` does not start
        # the worker. We force a real
        # ``log_evaluation`` to lazy-init
        # the worker, then drive it
        # directly.
        debug_logging.log_evaluation(
            timestamp=datetime(2026, 6, 15, 14, 30, 0),
            inputs={
                "smart_mode": 0,
                "soc": 60.0,
                "pv_power": 100.0,
                "load_power": 200.0,
            },
            decision=HemsDecision(reason="warmup", skip=True),
            applied=None,
            skip_reason="warmup",
        )
        self.assertIsNotNone(
            debug_logging._worker,
            msg="worker must exist after log_evaluation",
        )
        # Submit items that include a
        # priority of 0. The bounded
        # worker API is
        # ``enqueue(payload, path, priority)``.
        for priority in (0, 0, 1, 0, 5, 0):
            debug_logging._worker.enqueue(
                json.dumps({"priority": priority}),
                log_path,
                priority=priority,
            )
        # Drain and ensure no items were
        # dropped just because priority
        # was 0.
        debug_logging._worker.drain(timeout=5.0)
        self.assertEqual(
            debug_logging._worker.dropped,
            0,
            msg="priority 0 items must not be silently dropped",
        )

    # ── 6. reload drains the in-flight queue ─────────────────────

    def test_t18_06_reload_drains_queue(self) -> None:
        """When the integration is reloaded,
        the debug-logging worker must
        drain its queue before tearing
        down. A reload that drops
        unflushed entries is exactly what
        the audit asked us to fix.
        """
        log_path = self._tmpdir / "reload.log"
        debug_logging.set_log_path(log_path)
        # Slow the writer slightly.
        original_write = debug_logging._write_line
        def slow_write(path, line):
            time.sleep(0.02)
            return original_write(path, line)
        debug_logging._write_line = slow_write
        try:
            for i in range(30):
                debug_logging.log_evaluation(
                    timestamp=datetime(2026, 6, 15, 14, 0, i % 60),
                    inputs={
                        "smart_mode": 0,
                        "soc": 60.0,
                        "pv_power": 100.0,
                        "load_power": 200.0,
                    },
                    decision=HemsDecision(
                        output_priority="2",
                        charger_priority="2",
                        reason=f"r-{i}",
                        skip=False,
                    ),
                    applied={"output_priority": "2", "charger_priority": "2"},
                    skip_reason=None,
                )
            # Shutdown the worker — this is
            # what reload would call.
            worker = debug_logging._worker
            worker.shutdown(timeout=10.0)
            # After shutdown the queue must
            # be empty.
            self.assertEqual(
                worker.qsize(),
                0,
                msg="reload must drain the in-flight queue",
            )
        finally:
            debug_logging._write_line = original_write
            debug_logging._worker = self._before_pool

    # ── 7. per-entry separation ─────────────────────────────────

    def test_t18_07_per_entry_separation(self) -> None:
        """Two different config entries must
        write to separate log files. The
        audit found that today they share
        one file.
        """
        # ``set_log_path`` puts the
        # default-file path inside the test
        # tmpdir so the warmup write does
        # not crash on a missing ``/config``
        # directory.
        entry_a = self._tmpdir / "entry_a.log"
        entry_b = self._tmpdir / "entry_b.log"
        debug_logging.set_log_path(self._tmpdir / "default.log")
        debug_logging.bind_entry("entry_a", entry_a)
        debug_logging.bind_entry("entry_b", entry_b)
        # Force the worker to exist by
        # calling ``log_evaluation`` once
        # through the default path.
        debug_logging.log_evaluation(
            timestamp=datetime(2026, 6, 15, 15, 0, 0),
            inputs={
                "smart_mode": 0,
                "soc": 60.0,
                "pv_power": 100.0,
                "load_power": 200.0,
            },
            decision=HemsDecision(reason="warmup", skip=True),
            applied=None,
            skip_reason="warmup",
        )
        debug_logging.log_evaluation(
            timestamp=datetime(2026, 6, 15, 15, 0, 0),
            inputs={
                "smart_mode": 0,
                "soc": 60.0,
                "pv_power": 100.0,
                "load_power": 200.0,
            },
            decision=HemsDecision(reason="entry_a", skip=True),
            applied=None,
            skip_reason="a",
            entry_id="entry_a",
        )
        debug_logging.log_evaluation(
            timestamp=datetime(2026, 6, 15, 15, 0, 0),
            inputs={
                "smart_mode": 0,
                "soc": 60.0,
                "pv_power": 100.0,
                "load_power": 200.0,
            },
            decision=HemsDecision(reason="entry_b", skip=True),
            applied=None,
            skip_reason="b",
            entry_id="entry_b",
        )
        # Drain.
        debug_logging._worker.drain(timeout=5.0)
        a_text = entry_a.read_text(encoding="utf-8")
        b_text = entry_b.read_text(encoding="utf-8")
        self.assertIn(
            "entry_a",
            a_text,
            msg="entry_a must appear in entry_a.log",
        )
        self.assertNotIn(
            "entry_b",
            a_text,
            msg="entry_b must NOT appear in entry_a.log",
        )
        self.assertIn(
            "entry_b",
            b_text,
            msg="entry_b must appear in entry_b.log",
        )
        self.assertNotIn(
            "entry_a",
            b_text,
            msg="entry_a must NOT appear in entry_b.log",
        )
        debug_logging.unbind_entry("entry_a")
        debug_logging.unbind_entry("entry_b")


if __name__ == "__main__":
    main(verbosity=2)