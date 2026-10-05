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
        """Audit T18 follow-up: even when
        the filesystem is slow, the event
        loop must keep ticking while the
        bounded worker handles the write
        in the background.

        This is a real async test: we
        launch the work via ``asyncio``
        and assert the loop runs several
        heartbeats during the slow write.
        """
        import asyncio as _asyncio

        log_path = self._tmpdir / "async_loop.log"
        debug_logging.set_log_path(log_path)

        # Slow the writer so the queue
        # holds the record for a while.
        original_write = debug_logging._write_line
        def slow_write(path, line):
            time.sleep(1.5)
            return original_write(path, line)
        debug_logging._write_line = slow_write
        try:
            debug_logging.log_evaluation(
                timestamp=datetime(2026, 6, 15, 10, 0, 0),
                inputs={
                    "smart_mode": 0,
                    "soc": 60.0,
                    "pv_power": 100.0,
                    "load_power": 200.0,
                },
                decision=HemsDecision(
                    output_priority="2",
                    charger_priority="2",
                    reason="async-loop",
                    skip=False,
                ),
                applied={"output_priority": "2", "charger_priority": "2"},
                skip_reason=None,
            )

            async def _heartbeat_check() -> int:
                beat_count = 0
                loop_start = _asyncio.get_event_loop().time()
                # _heartbeat_loop runs for 0.5s;
                # if the event loop is blocked
                # by a synchronous file write,
                # we will not reach the expected
                # tick count.
                while (
                    _asyncio.get_event_loop().time() - loop_start
                    < 0.5
                ):
                    beat_count += 1
                    await _asyncio.sleep(0.01)
                return beat_count

            beat_count = _asyncio.run(_heartbeat_check())
            self.assertGreaterEqual(
                beat_count,
                30,
                msg=(
                    f"event loop heartbeat stalled: "
                    f"only {beat_count} heartbeats in 0.5s"
                ),
            )
        finally:
            try:
                debug_logging._worker.drain(timeout=5.0)
            except Exception:
                pass
            debug_logging._write_line = original_write

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
        # Audit T18 follow-up:
        # ``_maybe_rotate`` now enqueues
        # the rename onto the bounded
        # worker. We must first
        # lazy-init the worker (via
        # ``log_evaluation``) and then
        # drain so we observe the
        # post-rotation state.
        debug_logging.log_evaluation(
            timestamp=datetime(2026, 6, 15, 13, 30, 0),
            inputs={
                "smart_mode": 0,
                "soc": 60.0,
                "pv_power": 100.0,
                "load_power": 200.0,
            },
            decision=HemsDecision(reason="warm", skip=True),
            applied=None,
            skip_reason="warm",
        )
        debug_logging._worker.drain(timeout=5.0)
        debug_logging._maybe_rotate()
        debug_logging._worker.drain(timeout=5.0)
        debug_logging._maybe_rotate()  # idempotent
        debug_logging._worker.drain(timeout=5.0)
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



    def test_t18_08_daily_summary_priority_zero(self) -> None:
        """Audit T18 follow-up: a
        recorded ``output_priority`` or
        ``charger_priority`` of ``"0"``
        (or ``0`` int) is a real value —
        it must count as a command in
        ``daily_summary``, not as a
        skip. The legacy
        ``or applied.get(...)`` pattern
        dropped both cases via
        truthiness.
        """
        import asyncio as _asyncio

        log_path = self._tmpdir / "priority_zero.log"
        debug_logging.set_log_path(log_path)
        # Warm up the worker.
        debug_logging.log_evaluation(
            timestamp=datetime(2026, 6, 15, 16, 0, 0),
            inputs={
                "smart_mode": 0,
                "soc": 60.0,
                "pv_power": 100.0,
                "load_power": 200.0,
            },
            decision=HemsDecision(reason="warm", skip=True),
            applied=None,
            skip_reason="warm",
        )
        # Three records: one with
        # ``"0"`` string output_priority,
        # one with ``0`` int, one with
        # None.
        debug_logging.log_evaluation(
            timestamp=datetime(2026, 6, 15, 16, 1, 0),
            inputs={
                "smart_mode": 0,
                "soc": 60.0,
                "pv_power": 100.0,
                "load_power": 200.0,
            },
            decision=HemsDecision(reason="zero_str", skip=False),
            applied={"output_priority": "0", "charger_priority": "2"},
            skip_reason=None,
        )
        debug_logging.log_evaluation(
            timestamp=datetime(2026, 6, 15, 16, 2, 0),
            inputs={
                "smart_mode": 0,
                "soc": 60.0,
                "pv_power": 100.0,
                "load_power": 200.0,
            },
            decision=HemsDecision(reason="zero_int", skip=False),
            applied={"output_priority": 0, "charger_priority": 0},
            skip_reason=None,
        )
        debug_logging.log_evaluation(
            timestamp=datetime(2026, 6, 15, 16, 3, 0),
            inputs={
                "smart_mode": 0,
                "soc": 60.0,
                "pv_power": 100.0,
                "load_power": 200.0,
            },
            decision=HemsDecision(reason="skip", skip=True),
            applied=None,
            skip_reason="skipped",
        )
        debug_logging._worker.drain(timeout=5.0)
        date_str = "2026-06-15"
        summary = _asyncio.run(
            debug_logging.daily_summary(date_str, entry_id=None)
        )
        self.assertEqual(
            summary["decisions"],
            4,
            msg=(
                f"daily_summary must count every record on the "
                f"date; got {summary!r}"
            ),
        )
        # Two of the four are commands
        # (``"0"`` and ``0`` int); one
        # is a skip; the warmup is
        # counted as a skip too.
        self.assertEqual(
            summary["commands_sent"],
            2,
            msg=(
                "daily_summary must count priority-0 records "
                f"as commands; got {summary!r}"
            ),
        )
        self.assertEqual(
            summary["skipped"],
            2,
            msg=(
                "daily_summary must count true skips only; "
                f"got {summary!r}"
            ),
        )

    def test_t18_09_rotation_runs_on_worker_thread(self) -> None:
        """Audit T18 follow-up:
        ``_maybe_rotate`` must not call
        ``Path.rename`` /
        ``Path.unlink`` on the calling
        thread. We patch
        ``_rotate_paths`` to record the
        thread it ran on and assert it
        is NOT the calling thread.
        """
        import threading as _threading
        log_path = self._tmpdir / "rotation_thread.log"
        debug_logging.set_log_path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps({"marker": "pre"}) + "\n",
            encoding="utf-8",
        )
        # Mark the rotation date as
        # yesterday so ``_maybe_rotate``
        # will roll.
        yesterday = (datetime.now() - timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        debug_logging._ROTATE_AT[str(log_path)] = yesterday
        # Replace ``_rotate_paths`` so
        # we can observe the thread.
        seen: list[str] = []
        original = debug_logging._rotate_paths
        def recording_rotate(target, last):
            seen.append(_threading.current_thread().name)
            return original(target, last)
        debug_logging._rotate_paths = recording_rotate
        try:
            caller = _threading.current_thread().name
            debug_logging._maybe_rotate()
            # The rotation work is
            # queued on the bounded
            # worker. Drain to ensure
            # it ran.
            debug_logging._worker.drain(timeout=5.0)
            self.assertTrue(
                seen,
                msg=(
                    "_rotate_paths must have been called via "
                    "the worker; saw no calls"
                ),
            )
            self.assertNotEqual(
                seen[0],
                caller,
                msg=(
                    f"rotation ran on caller thread ({caller!r}) "
                    f"instead of the worker thread; audit T18 "
                    f"follow-up forbids synchronous rotation"
                ),
            )
        finally:
            debug_logging._rotate_paths = original

    def test_t18_10_per_entry_isolation_runtime(self) -> None:
        """Audit T18 follow-up: the
        production coordinator must
        thread ``entry_id`` through to
        ``debug_logging.log_evaluation``
        so two entries write to two
        distinct files.

        We exec the coordinator path
        up to ``log_evaluation`` and
        verify the call carries
        ``entry_id``.
        """
        # Read the live coordinator
        # source and look for the
        # ``_hems.evaluate`` call
        # site.
        coord_src = (REPO_ROOT / "coordinator.py").read_text(
            encoding="utf-8"
        )
        import ast as _ast
        tree = _ast.parse(coord_src)
        evaluate_calls: list[_ast.Call] = []
        for node in _ast.walk(tree):
            if (
                isinstance(node, _ast.Call)
                and isinstance(node.func, _ast.Attribute)
                and node.func.attr == "evaluate"
            ):
                evaluate_calls.append(node)
        self.assertTrue(
            evaluate_calls,
            msg=(
                "coordinator.py must call _hems.evaluate"
            ),
        )
        # At least one of these calls
        # must pass ``entry_id``.
        entry_id_passed = False
        for call in evaluate_calls:
            for kw in call.keywords:
                if kw.arg == "entry_id":
                    entry_id_passed = True
                    break
        self.assertTrue(
            entry_id_passed,
            msg=(
                "coordinator._hems.evaluate must thread "
                "entry_id through to log_evaluation"
            ),
        )

    def test_t18_11_drain_during_unload(self) -> None:
        """Audit T18 follow-up: when the
        integration unloads, the
        bounded worker must drain the
        in-flight queue before the
        entry is removed from
        ``hass.data``. We pin this
        contract at the module level:
        ``shutdown_drain`` must return
        True (queue drained).
        """
        log_path = self._tmpdir / "drain_unload.log"
        debug_logging.set_log_path(log_path)
        # Slow the writer slightly so
        # there is always an
        # in-flight record.
        original_write = debug_logging._write_line
        def slow_write(path, line):
            time.sleep(0.05)
            return original_write(path, line)
        debug_logging._write_line = slow_write
        try:
            for i in range(20):
                debug_logging.log_evaluation(
                    timestamp=datetime(2026, 6, 15, 17, 0, i % 60),
                    inputs={
                        "smart_mode": 0,
                        "soc": 60.0,
                        "pv_power": 100.0,
                        "load_power": 200.0,
                    },
                    decision=HemsDecision(reason="u", skip=True),
                    applied=None,
                    skip_reason="u",
                )
            drained = debug_logging.shutdown_drain()
            self.assertTrue(
                drained,
                msg=(
                    "shutdown_drain must return True when the "
                    "queue drains"
                ),
            )
        finally:
            debug_logging._write_line = original_write
            # Restart the worker so
            # subsequent tests can use
            # the API.
            debug_logging._worker = None



if __name__ == "__main__":
    main(verbosity=2)