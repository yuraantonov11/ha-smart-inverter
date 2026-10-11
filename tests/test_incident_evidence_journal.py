"""Persisted incident journal contract tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT.parent))

from powmr_inverter.hems import evidence_journal as journal


class IncidentEvidenceJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="powmr-evidence-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        journal.drain(timeout=3.0)
        self._tmp.cleanup()

    def test_records_are_versioned_ordered_and_allowlisted(self) -> None:
        entry_id = "entry-one"
        journal.configure_entry(entry_id, self.root / entry_id, "1.9.0")
        first = journal.record_event(
            entry_id,
            "cycle",
            outcome="fresh_success",
            device_measurement_at=None,
            state={
                "raw_soc": 54.5,
                "battery_voltage_v": 52.1,
                "availability": "online",
                "api_message": "must not be persisted",
            },
            critical=True,
        )
        second = journal.record_event(entry_id, "cycle", outcome="offline")

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual(first["integration_version"], "1.9.0")
        self.assertEqual(first["entry_id"], entry_id)
        self.assertEqual(first["session_id"], second["session_id"])
        self.assertEqual((first["sequence"], second["sequence"]), (1, 2))
        self.assertEqual(first["device_measurement_at"], None)
        self.assertIsNotNone(datetime.fromisoformat(first["event_timestamp"]).tzinfo)
        self.assertNotIn("api_message", json.dumps(first))
        self.assertTrue(journal.drain(timeout=3.0))
        rows = journal.read_records(entry_id)
        self.assertEqual([row["sequence"] for row in rows], [1, 2])
        self.assertEqual(rows[0]["state"]["raw_soc"], 54.5)
        self.assertNotIn("api_message", rows[0]["state"])

    def test_expired_active_history_rotates_and_is_pruned_on_write(self) -> None:
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="powmr-active-retention-") as tmp:
            path = Path(tmp) / "events.jsonl"
            now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
            old = now - timedelta(days=journal.RETENTION_DAYS + 1)
            path.write_text(json.dumps({"entry_id": "aged", "sequence": 1, "event_timestamp": old.isoformat()}) + "\n", encoding="utf-8")
            os.utime(path, (now.timestamp(), now.timestamp()))
            writer = journal._JournalWriter(max_queue=2)
            with patch.object(journal, "_utc_now", return_value=now):
                writer._write(
                    path,
                    {"entry_id": "aged", "sequence": 2, "event_timestamp": now.isoformat()},
                    critical=True,
                )

            self.assertEqual(journal.read_jsonl(path), [{"entry_id": "aged", "sequence": 2, "event_timestamp": now.isoformat()}])
            self.assertEqual(list(Path(tmp).glob("events.*.jsonl")), [])

    def test_size_and_age_rotation_preserve_recent_history(self) -> None:
        entry_id = "rotate-entry"
        journal.configure_entry(entry_id, self.root / entry_id, "1.9.0")
        original_size = journal.MAX_FILE_SIZE_BYTES
        original_age = journal.RETENTION_DAYS
        journal.MAX_FILE_SIZE_BYTES = 300
        journal.RETENTION_DAYS = 30
        try:
            for index in range(5):
                journal.record_event(
                    entry_id, "cycle", outcome="fresh_success",
                    state={"raw_soc": 50 + index}, critical=True,
                )
            self.assertTrue(journal.drain(timeout=3.0))
            archives = list((self.root / entry_id).glob("events.*.jsonl"))
            self.assertGreaterEqual(len(archives), 1)
            recent_rows = [row for archive in archives for row in journal.read_jsonl(archive)]
            recent_rows += journal.read_records(entry_id)
            self.assertEqual(sorted(row["sequence"] for row in recent_rows), [1, 2, 3, 4, 5])

            old_archive = archives[0]
            old_archive.write_text('{"stale":true}\\n', encoding="utf-8")
            old_time = old_archive.stat().st_mtime - 31 * 86400
            import os
            os.utime(old_archive, (old_time, old_time))
            journal.record_event(entry_id, "cycle", outcome="offline", critical=True)
            self.assertTrue(journal.drain(timeout=3.0))
            self.assertFalse(old_archive.exists())
            self.assertEqual(len(journal.read_records(entry_id)), 1)
        finally:
            journal.MAX_FILE_SIZE_BYTES = original_size
            journal.RETENTION_DAYS = original_age


class _FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def json(self):
        return {"code": 0, "msg": "private response text"}


class _FakeSession:
    def post(self, *args, **kwargs):
        return _FakeResponse()


class RealtimeApiEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_successful_cloud_receipts_advance_last_success_anchor(self) -> None:
        from powmr_inverter.api import InverterApiClient
        from unittest.mock import AsyncMock

        class Response:
            status = 200

            def __init__(self, payload):
                self.payload = payload

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def text(self):
                return json.dumps(self.payload)

        class Session:
            def __init__(self):
                self.payloads = [
                    {"code": 0, "data": {"deviceAttributeState": {"batterySoc": {"value": "55"}}}},
                    {"code": 71000, "msg": "offline"},
                ]

            def get(self, *args, **kwargs):
                return Response(self.payloads.pop(0))

        api = InverterApiClient("unused@example.test", "not-a-real-password")
        api.device_sn = "test-device"
        api._session = Session()
        api._apply_rate_limit = AsyncMock()

        fresh = await api._try_realtime_endpoint("unit-test-endpoint")
        success_received_at = api.last_realtime_success_received_at
        self.assertIsNotNone(fresh)
        self.assertIsInstance(success_received_at, datetime)
        self.assertIsNotNone(success_received_at.tzinfo)
        self.assertEqual(api.last_realtime_response_received_at, success_received_at)
        self.assertIsNone(api.last_realtime_measurement_at)

        self.assertIsNone(await api._try_realtime_endpoint("unit-test-endpoint"))
        self.assertTrue(api.last_realtime_offline)
        self.assertEqual(api.last_realtime_success_received_at, success_received_at)


class CommandCorrelationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="powmr-command-")
        self.root = Path(self._tmp.name)
        journal.configure_entry("command-entry", self.root / "command-entry", "1.9.0")

    def tearDown(self) -> None:
        journal.drain(timeout=3.0)
        self._tmp.cleanup()

    def test_real_api_method_records_each_request_between_intent_and_result(self) -> None:
        from powmr_inverter.api import InverterApiClient
        import asyncio
        import uuid

        class Response(_FakeResponse):
            def __init__(self, code):
                self.code = code

            async def json(self):
                return {"code": self.code, "msg": "private response text"}

        class RetrySession:
            def __init__(self):
                self.calls = 0

            def post(self, *args, **kwargs):
                self.calls += 1
                return Response(1 if self.calls == 1 else 0)

        command_id = str(uuid.uuid4())
        api = InverterApiClient("unused@example.test", "not-a-real-password")
        api.device_sn = "test-device"
        api.evidence_entry_id = "command-entry"
        api._session = RetrySession()

        async def call_production_api():
            from unittest.mock import AsyncMock, patch
            api._apply_rate_limit = AsyncMock()
            with patch("powmr_inverter.api.asyncio.sleep", new=AsyncMock()):
                with journal.command_scope(command_id):
                    return await api.set_config_item("outputSourcePrioritySetting", "2")

        self.assertTrue(asyncio.run(call_production_api()))
        self.assertTrue(journal.drain(timeout=3.0))
        rows = journal.read_records("command-entry")
        correlated = [row for row in rows if row.get("command", {}).get("command_id") == command_id]
        self.assertEqual(
            [row["event_type"] for row in correlated],
            ["command_intent", "command_request", "command_request", "command_result"],
        )
        self.assertEqual([row["sequence"] for row in correlated], sorted(row["sequence"] for row in correlated))
        self.assertEqual([row["outcome"] for row in correlated], [None, "request_sent", "request_sent", "api_accepted"])
        for request in correlated[1:3]:
            self.assertEqual(request["command"]["status"], "request")
            self.assertEqual(set(request), {
                "schema_version", "integration_version", "entry_id", "session_id", "sequence",
                "event_timestamp", "event_type", "outcome", "cloud_response_received_at",
                "device_measurement_at", "ha_state_published_at", "state", "command", "error",
            })
        result = correlated[-1]
        self.assertEqual(result["command"]["command_id"], command_id)
        self.assertEqual(result["command"]["status"], "api_accepted")
        self.assertFalse(any(row["outcome"] == "observed_match" for row in rows))
        self.assertNotIn("private response text", json.dumps(rows))


class WriterFailureTests(unittest.TestCase):
    def test_rotation_syncs_old_inode_before_rename_and_new_inode_after_write(self) -> None:
        import os
        import stat
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="powmr-rotation-order-") as tmp:
            writer = journal._JournalWriter(max_queue=2)
            path = Path(tmp) / "events.jsonl"
            operations = []
            original_open = Path.open
            original_replace = Path.replace
            original_fsync = os.fsync

            class TrackedHandle:
                def __init__(self, handle):
                    self.handle = handle

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return self.handle.__exit__(*args)

                def write(self, data):
                    inode = os.fstat(self.handle.fileno()).st_ino
                    operations.append(("write", inode))
                    return self.handle.write(data)

                def fileno(self):
                    return self.handle.fileno()

            def tracked_open(target, *args, **kwargs):
                handle = original_open(target, *args, **kwargs)
                mode = args[0] if args else kwargs.get("mode", "r")
                return TrackedHandle(handle) if target == path and mode == "ab" else handle

            def tracked_replace(source, target):
                old_inode = source.stat().st_ino
                result = original_replace(source, target)
                operations.append(("rename", old_inode))
                return result

            def tracked_fsync(fd):
                result = original_fsync(fd)
                info = os.fstat(fd)
                if stat.S_ISREG(info.st_mode):
                    operations.append(("fsync", info.st_ino))
                return result

            with (
                patch.object(journal, "MAX_FILE_SIZE_BYTES", 1),
                patch.object(Path, "open", tracked_open),
                patch.object(Path, "replace", tracked_replace),
                patch.object(journal.os, "fsync", tracked_fsync),
            ):
                writer._write(path, {"entry_id": "rotate-order", "sequence": 1}, critical=False)
                writer._write(path, {"entry_id": "rotate-order", "sequence": 2}, critical=True)

            writes = [(index, inode) for index, (kind, inode) in enumerate(operations) if kind == "write"]
            self.assertEqual(len(writes), 2)
            old_inode = writes[0][1]
            new_inode = writes[1][1]
            self.assertNotEqual(old_inode, new_inode)
            old_fsync = next((index for index, item in enumerate(operations) if item == ("fsync", old_inode)), None)
            rename = next(index for index, item in enumerate(operations) if item[0] == "rename")
            new_fsync = next((index for index, item in enumerate(operations) if item == ("fsync", new_inode)), None)
            self.assertIsNotNone(old_fsync, "old active inode must be fsynced during rotation")
            self.assertIsNotNone(new_fsync, "critical record inode must be fsynced")
            self.assertLess(old_fsync if old_fsync is not None else -1, rename, "pending data must be fsynced on the old inode before rename")
            self.assertLess(writes[1][0], new_fsync if new_fsync is not None else -1, "critical record must be fsynced after its write")
            self.assertIn(writer.status("rotate-order").get("directory_fsync"), {"supported", "unsupported", "failed"})
            self.assertTrue(writer.drain(timeout=2.0))

    def test_failed_rotation_fsync_does_not_rename_or_count_pending_records(self) -> None:
        import errno
        import os
        import stat
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="powmr-rotation-fsync-failure-") as tmp:
            writer = journal._JournalWriter(max_queue=2)
            path = Path(tmp) / "events.jsonl"
            writer._write(path, {"entry_id": "rotate-failure", "sequence": 1}, critical=False)
            old_inode = path.stat().st_ino
            original_fsync = os.fsync

            def fail_old_inode(fd):
                info = os.fstat(fd)
                if stat.S_ISREG(info.st_mode) and info.st_ino == old_inode:
                    raise OSError(errno.ENOSPC, "private fsync failure")
                return original_fsync(fd)

            with patch.object(journal, "MAX_FILE_SIZE_BYTES", 1), patch.object(journal.os, "fsync", fail_old_inode):
                writer._write(path, {"entry_id": "rotate-failure", "sequence": 2}, critical=True)

            self.assertEqual(path.stat().st_ino, old_inode, "failed fsync must leave the active inode in place")
            self.assertEqual(list(Path(tmp).glob("events.*.jsonl")), [], "failed fsync must prevent rename")
            self.assertEqual([row["sequence"] for row in journal.read_jsonl(path)], [1])
            stats = writer.status("rotate-failure")
            self.assertEqual(stats["fsync_errors"], 1)
            self.assertEqual(stats["records_written"], 0, "unsynced pending data is not durable")
            self.assertFalse(writer.drain(timeout=2.0))

    def test_directory_fsync_unsupported_is_reported_explicitly(self) -> None:
        import errno
        import os
        import stat
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="powmr-dir-fsync-unsupported-") as tmp:
            writer = journal._JournalWriter(max_queue=2)
            path = Path(tmp) / "events.jsonl"
            original_fsync = os.fsync

            def unsupported_for_directories(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    raise OSError(errno.EINVAL, "directory fsync unsupported")
                return original_fsync(fd)

            with patch.object(journal.os, "fsync", unsupported_for_directories):
                writer._write(path, {"entry_id": "directory-unsupported", "sequence": 1}, critical=True)

            stats = writer.status("directory-unsupported")
            self.assertEqual(stats["directory_fsync"], "unsupported")
            self.assertEqual(stats["records_written"], 1)
            self.assertEqual(stats["fsync_errors"], 0)
            self.assertTrue(writer.drain(timeout=2.0))

    def test_active_writer_does_not_rescan_archives_per_append(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="powmr-journal-history-scan-") as tmp:
            root = Path(tmp)
            path = root / "events.jsonl"
            now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
            path.write_text(
                json.dumps({"entry_id": "scan-entry", "sequence": 1, "event_timestamp": now.isoformat()}) + chr(10),
                encoding="utf-8",
            )
            archives = [root / f"events.20261011T12000{index}Z.{index}.jsonl" for index in range(1, 5)]
            for index, archive in enumerate(archives, start=1):
                archive.write_text(
                    json.dumps({"entry_id": "scan-entry", "sequence": index, "event_timestamp": now.isoformat()}) + chr(10),
                    encoding="utf-8",
                )
            writer = journal._JournalWriter(max_queue=8)
            read_targets = []
            original_open = Path.open

            def tracked_open(target, *args, **kwargs):
                mode = args[0] if args else kwargs.get("mode", "r")
                if "r" in mode or "+" in mode:
                    read_targets.append(target)
                return original_open(target, *args, **kwargs)

            with (
                patch.object(journal, "_utc_now", return_value=now),
                patch.object(journal, "MAX_FILE_SIZE_BYTES", 1024 * 1024),
                patch.object(Path, "open", tracked_open),
            ):
                for sequence in range(2, 6):
                    writer._write(
                        path,
                        {"entry_id": "scan-entry", "sequence": sequence, "event_timestamp": now.isoformat()},
                        critical=False,
                    )

            archive_reads = [target for target in read_targets if target in archives]
            active_reads = [target for target in read_targets if target == path]
            self.assertEqual(archive_reads, [], "appends without rotation must not read archive history")
            self.assertLessEqual(len(active_reads), 1, "active history is rebuilt once per writer open, not per append")
            self.assertTrue(writer.drain(timeout=2.0))

    def test_fsync_failure_is_visible_and_drain_is_not_success(self) -> None:
        import errno
        import os
        import stat
        import time
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="powmr-fsync-") as tmp:
            writer = journal._JournalWriter(max_queue=4)
            event = {"entry_id": "fsync-entry", "sequence": 1}
            original_fsync = os.fsync

            def fail_file_fsync(fd):
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError(errno.ENOSPC, "private path text")
                return original_fsync(fd)

            with patch.object(journal.os, "fsync", side_effect=fail_file_fsync):
                self.assertTrue(writer.enqueue("fsync-entry", Path(tmp) / "events.jsonl", event, critical=True))
                deadline = time.monotonic() + 2.0
                while sum(writer.status("fsync-entry")[key] for key in ("write_errors", "fsync_errors")) == 0 and time.monotonic() < deadline:
                    time.sleep(0.005)
            stats = writer.status("fsync-entry")
            self.assertEqual(stats["fsync_errors"], 1)
            self.assertEqual(stats["records_written"], 0)
            self.assertEqual(stats["last_error"]["code"], errno.ENOSPC)
            self.assertFalse(writer.drain(timeout=1.0))
            retried = writer.status("fsync-entry")
            self.assertEqual(retried["fsyncs"], 1)
            self.assertEqual(retried["records_written"], 1)

    def test_full_queue_is_counted_without_blocking_enqueue(self) -> None:
        import threading
        import time

        with tempfile.TemporaryDirectory(prefix="powmr-overflow-") as tmp:
            writer = journal._JournalWriter(max_queue=1)
            path = Path(tmp) / "events.jsonl"
            started = threading.Event()
            release = threading.Event()
            original_write = writer._write

            def slow_write(*args, **kwargs):
                started.set()
                release.wait(2.0)
                return original_write(*args, **kwargs)

            writer._write = slow_write
            self.assertTrue(writer.enqueue("overflow", path, {"entry_id": "overflow", "sequence": 1}, critical=False))
            self.assertTrue(started.wait(1.0))
            self.assertTrue(writer.enqueue("overflow", path, {"entry_id": "overflow", "sequence": 2}, critical=False))
            started_at = time.monotonic()
            accepted = writer.enqueue("overflow", path, {"entry_id": "overflow", "sequence": 3}, critical=False)
            elapsed = time.monotonic() - started_at
            release.set()
            self.assertFalse(accepted)
            self.assertLess(elapsed, 0.1)
            drained = writer.drain(timeout=3.0)
            self.assertEqual(writer.status("overflow")["queue_full"], 1)
            self.assertEqual(writer.status("overflow")["last_error"], {"class": "QueueFull", "code": None})
            self.assertFalse(drained)

    def test_normal_fsync_respects_configured_deadline(self) -> None:
        import time

        with tempfile.TemporaryDirectory(prefix="powmr-fsync-deadline-") as tmp:
            original_interval = journal.NORMAL_FSYNC_SECONDS
            journal.NORMAL_FSYNC_SECONDS = 0.05
            writer = journal._JournalWriter(max_queue=2)
            try:
                path = Path(tmp) / "events.jsonl"
                started = time.monotonic()
                self.assertTrue(writer.enqueue("deadline", path, {"entry_id": "deadline", "sequence": 1}, critical=False))
                deadline = started + 0.3
                while writer.status("deadline")["fsyncs"] == 0 and time.monotonic() < deadline:
                    time.sleep(0.002)
                self.assertEqual(writer.status("deadline")["fsyncs"], 1)
                self.assertLess(time.monotonic() - started, 0.2)
            finally:
                journal.NORMAL_FSYNC_SECONDS = original_interval
                self.assertTrue(writer.drain(timeout=2.0))

    def test_slow_disk_does_not_block_asyncio_polling_path(self) -> None:
        import asyncio
        import threading
        import time
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="powmr-slow-disk-") as tmp:
            entry_id = "slow-disk-entry"
            journal.configure_entry(entry_id, Path(tmp) / entry_id, "1.9.0")
            writer = journal._JournalWriter(max_queue=4)
            previous_writer = journal._WRITER
            journal._WRITER = writer
            started = threading.Event()
            release = threading.Event()
            original_write = writer._write

            def slow_write(*args, **kwargs):
                started.set()
                release.wait(2.0)
                return original_write(*args, **kwargs)

            writer._write = slow_write

            async def exercise_poll_path():
                started_at = time.monotonic()
                self.assertIsNotNone(journal.record_event(entry_id, "cycle", outcome="fresh_success"))
                self.assertTrue(await asyncio.to_thread(started.wait, 1.0))
                await asyncio.wait_for(asyncio.sleep(0.02), timeout=0.1)
                self.assertLess(time.monotonic() - started_at, 0.2)

            try:
                asyncio.run(exercise_poll_path())
            finally:
                release.set()
                self.assertTrue(writer.drain(timeout=3.0))
                journal._WRITER = previous_writer

    def test_two_entries_keep_separate_files_and_sequences(self) -> None:
        with tempfile.TemporaryDirectory(prefix="powmr-entry-isolation-") as tmp:
            first_id, second_id = "isolated-a", "isolated-b"
            first_dir, second_dir = Path(tmp) / first_id, Path(tmp) / second_id
            journal.configure_entry(first_id, first_dir, "1.9.0")
            journal.configure_entry(second_id, second_dir, "1.9.0")
            first = journal.record_event(first_id, "cycle", outcome="fresh_success")
            second = journal.record_event(second_id, "cycle", outcome="offline")
            self.assertTrue(journal.drain(timeout=3.0))

            self.assertEqual(first["sequence"], 1)
            self.assertEqual(second["sequence"], 1)
            self.assertEqual([row["entry_id"] for row in journal.read_records(first_id)], [first_id])
            self.assertEqual([row["entry_id"] for row in journal.read_records(second_id)], [second_id])
            self.assertTrue((first_dir / "events.jsonl").is_file())
            self.assertTrue((second_dir / "events.jsonl").is_file())
            self.assertNotEqual(first_dir / "events.jsonl", second_dir / "events.jsonl")

    def test_write_enospc_is_counted_and_not_reported_as_saved(self) -> None:
        import errno
        import time
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="powmr-write-enospc-") as tmp:
            writer = journal._JournalWriter(max_queue=2)
            path = Path(tmp) / "events.jsonl"
            original_open = Path.open

            def full_disk(target, *args, **kwargs):
                if target == path:
                    raise OSError(errno.ENOSPC, "private disk path")
                return original_open(target, *args, **kwargs)

            with patch.object(Path, "open", full_disk):
                writer.enqueue("full-disk", path, {"entry_id": "full-disk", "sequence": 1}, critical=True)
                deadline = time.monotonic() + 2.0
                while writer.status("full-disk")["write_errors"] == 0 and time.monotonic() < deadline:
                    time.sleep(0.005)
            stats = writer.status("full-disk")
            self.assertEqual(stats["write_errors"], 1)
            self.assertEqual(stats["records_written"], 0)
            self.assertEqual(stats["last_error"]["code"], errno.ENOSPC)
            self.assertNotIn("private disk path", json.dumps(stats))
            self.assertFalse(writer.drain(timeout=1.0))

    def test_existing_hems_sensor_exposes_journal_health(self) -> None:
        import errno
        import time
        from types import SimpleNamespace
        from unittest.mock import patch
        from powmr_inverter.sensor import HemsReasonSensor

        with tempfile.TemporaryDirectory(prefix="powmr-health-surface-") as tmp:
            entry_id = "health-surface"
            path = Path(tmp) / entry_id / "events.jsonl"
            journal.configure_entry(entry_id, path.parent, "1.9.0")
            writer = journal._JournalWriter(max_queue=2)
            previous_writer = journal._WRITER
            journal._WRITER = writer
            original_open = Path.open

            def denied_open(target, *args, **kwargs):
                if target == path:
                    raise PermissionError(errno.EACCES, "private disk path", str(target))
                return original_open(target, *args, **kwargs)

            try:
                with patch.object(Path, "open", denied_open):
                    journal.record_event(entry_id, "cycle", outcome="fresh_success")
                    deadline = time.monotonic() + 2.0
                    while writer.status(entry_id)["write_errors"] == 0 and time.monotonic() < deadline:
                        time.sleep(0.005)
                coordinator = SimpleNamespace(
                    api=SimpleNamespace(device_sn="diagnostic-device"),
                    _entry=SimpleNamespace(entry_id=entry_id),
                )
                sensor = HemsReasonSensor(coordinator)
                attributes = sensor.extra_state_attributes
                self.assertIsInstance(attributes, dict)
                health = attributes["incident_evidence_journal"]
                self.assertTrue({"queue_depth", "queue_full", "write_errors", "fsync_errors", "last_error", "directory_fsync"} <= set(health))
                self.assertEqual(health["write_errors"], 1)
                self.assertEqual(health["last_error"]["class"], "PermissionError")
                self.assertNotIn("private disk path", json.dumps(health))
            finally:
                writer.drain(timeout=1.0)
                journal._WRITER = previous_writer

    def test_permission_failure_has_sanitized_status_and_false_drain(self) -> None:
        import time
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="powmr-permission-") as tmp:
            writer = journal._JournalWriter(max_queue=2)
            path = Path(tmp) / "forbidden.jsonl"
            original_open = Path.open

            def denied_open(target, *args, **kwargs):
                if target == path:
                    raise PermissionError(13, "private permission detail", str(path))
                return original_open(target, *args, **kwargs)

            with patch.object(Path, "open", denied_open):
                writer.enqueue("denied", path, {"entry_id": "denied", "sequence": 1}, critical=True)
                deadline = time.monotonic() + 2.0
                while writer.status("denied")["write_errors"] == 0 and time.monotonic() < deadline:
                    time.sleep(0.005)
            stats = writer.status("denied")
            self.assertEqual(stats["write_errors"], 1)
            self.assertEqual(stats["last_error"]["class"], "PermissionError")
            self.assertNotIn("private permission detail", json.dumps(stats))
            self.assertFalse(writer.drain(timeout=1.0))


class ExportBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="powmr-export-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        journal.drain(timeout=3.0)
        self._tmp.cleanup()

    def test_export_preserves_valid_stored_integration_version(self) -> None:
        entry_id = "version-export-entry"
        entry_dir = self.root / "powmr_inverter_incidents" / entry_id
        journal.configure_entry(entry_id, entry_dir, "2.0.0")
        journal.record_event(entry_id, "cycle", outcome="fresh_success", critical=True)
        journal.record_event(entry_id, "cycle", outcome="offline", critical=True)
        self.assertTrue(journal.drain(timeout=3.0))
        active = entry_dir / "events.jsonl"
        rows = journal.read_jsonl(active)
        rows[0]["integration_version"] = "0.8.7"
        rows[1]["integration_version"] = "untrusted version\\nprivate"
        active.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        now = datetime.now(timezone.utc)

        exported_path, count = journal.export_bundle_sync(
            self.root,
            entry_id,
            now - timedelta(minutes=1),
            now + timedelta(minutes=1),
        )

        self.assertEqual(count, 2)
        exported = journal.read_jsonl(exported_path)
        self.assertEqual([row["integration_version"] for row in exported], ["0.8.7", None])

    def test_export_is_time_bounded_and_drops_unallowlisted_fields(self) -> None:
        entry_id = "export-entry"
        entry_dir = self.root / "powmr_inverter_incidents" / entry_id
        journal.configure_entry(entry_id, entry_dir, "1.9.0")
        now = datetime.now(timezone.utc)
        journal.record_event(
            entry_id, "cycle", outcome="fresh_success",
            state={"raw_soc": 61.0, "availability": "online"}, critical=True,
        )
        self.assertTrue(journal.drain(timeout=3.0))
        active = entry_dir / "events.jsonl"
        with active.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"entry_id": entry_id, "event_timestamp": now.isoformat(), "response_body": "private"}) + chr(10))
        outside = journal.record_event(entry_id, "cycle", outcome="offline", critical=True)
        self.assertIsNotNone(outside)
        self.assertTrue(journal.drain(timeout=3.0))

        path, count = journal.export_bundle_sync(
            self.root, entry_id, now - timedelta(days=1), now + timedelta(days=1),
        )
        self.assertEqual(count, 2)
        self.assertEqual(path.parent, self.root / "powmr_inverter_incidents" / "exports")
        exported_text = path.read_text(encoding="utf-8")
        self.assertNotIn("response_body", exported_text)
        self.assertNotIn("private", exported_text)
        self.assertEqual(len(journal.read_jsonl(path)), 2)


class CrashDurabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="powmr-crash-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_fsynced_lines_survive_forced_process_kill_and_bad_tail(self) -> None:
        import subprocess
        import sys
        import time

        entry_id = "kill-entry"
        entry_dir = self.root / "powmr_inverter_incidents" / entry_id
        marker = self.root / "fsynced.marker"
        code = (
            "import sys,time; "
            f"sys.path.insert(0, {str(REPO_ROOT.parent)!r}); "
            "from pathlib import Path; "
            "from datetime import datetime,timezone; "
            "from powmr_inverter.hems import evidence_journal as j; "
            f"j.configure_entry({entry_id!r}, Path({str(entry_dir)!r}), '1.9.0'); "
            f"j.record_event({entry_id!r}, 'cycle', outcome='fresh_success', critical=True); "
            "assert j.drain(3.0); "
            f"Path({str(marker)!r}).write_text('confirmed'); "
            "time.sleep(60)"
        )
        child = subprocess.Popen([sys.executable, "-c", code])
        try:
            deadline = time.monotonic() + 5.0
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(marker.exists(), "child must confirm journal fsync before kill")
        finally:
            child.kill()
            child.wait(timeout=3.0)

        journal.configure_entry(entry_id, entry_dir, "1.9.0")
        active = entry_dir / "events.jsonl"
        with active.open("ab") as handle:
            handle.write(b'{"partial":')
        rows = journal.read_records(entry_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sequence"], 1)


    def test_reopen_preserves_complete_json_without_trailing_newline(self) -> None:
        path = self.root / "unterminated" / "events.jsonl"
        path.parent.mkdir(parents=True)
        path.write_bytes(json.dumps({"entry_id": "unterminated", "sequence": 1}).encode("utf-8"))

        writer = journal._JournalWriter(max_queue=2)
        writer._write(path, {"entry_id": "unterminated", "sequence": 2}, critical=True)

        self.assertEqual([row["sequence"] for row in journal.read_jsonl(path)], [1, 2])

    def test_reopen_recovers_json_and_utf8_tails_before_appending(self) -> None:
        import subprocess
        import sys

        tails = {
            "truncated-json": b'{"partial":',
            "partial-utf8": bytes((0xE2, 0x82)),
        }
        for case, tail in tails.items():
            with self.subTest(case=case):
                entry_dir = self.root / case
                path = entry_dir / "events.jsonl"
                child_code = (
                    "import os,sys; "
                    f"sys.path.insert(0, {str(REPO_ROOT.parent)!r}); "
                    "from pathlib import Path; "
                    "from powmr_inverter.hems import evidence_journal as j; "
                    f"path=Path({str(path)!r}); "
                    "writer=j._JournalWriter(max_queue=2); "
                    f"writer._write(path, {{'entry_id': {case!r}, 'sequence': 1}}, critical=True); "
                    f"fd=os.open(str(path), os.O_WRONLY|os.O_APPEND); os.write(fd, {tail!r}); os.close(fd); "
                    "os._exit(0)"
                )
                child = subprocess.run([sys.executable, "-c", child_code], timeout=10.0)
                self.assertEqual(child.returncode, 0)

                reopened = journal._JournalWriter(max_queue=2)
                reopened._write(path, {"entry_id": case, "sequence": 2}, critical=True)
                self.assertEqual(
                    [row["sequence"] for row in journal.read_jsonl(path)],
                    [1, 2],
                    "reopen must preserve the complete fsynced record and discard only its incomplete suffix",
                )


class CoordinatorCycleEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        import uuid
        from powmr_inverter.api import InverterApiClient
        from powmr_inverter.coordinator import InverterCoordinator
        from powmr_inverter.hems.predictive_control import PredictiveControlEngine

        self._tmp = tempfile.TemporaryDirectory(prefix="powmr-coordinator-")
        self.root = Path(self._tmp.name)
        self.entry_id = f"coordinator-{uuid.uuid4()}"
        self.entry_dir = self.root / "powmr_inverter_incidents" / self.entry_id
        journal.configure_entry(self.entry_id, self.entry_dir, "1.9.0")

        self.api = InverterApiClient.__new__(InverterApiClient)
        self.api.fetch_realtime_data = AsyncMock()
        self.api.last_realtime_outcome = "offline"
        self.api.last_realtime_source = "unavailable"
        self.api.last_realtime_response_received_at = None
        self.api.last_realtime_success_received_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        self.api.last_realtime_measurement_at = None
        self.coordinator = InverterCoordinator.__new__(InverterCoordinator)
        self.coordinator.api = self.api
        self.coordinator._entry = SimpleNamespace(
            entry_id=self.entry_id,
            async_start_reauth=lambda hass: None,
        )
        self.coordinator.hass = None
        self.coordinator._hems = PredictiveControlEngine()
        self.coordinator._consecutive_nulls = 0
        self.coordinator.data = {
            "batterySoc": 43.0,
            "correctedSoc": 41.0,
            "batteryVoltage": 51.7,
            "batteryCurrent": -7.0,
            "batteryPower": -361.9,
            "pvPower": 105.0,
            "loadPower": 510.0,
            "gridPower": 290.0,
            "lastUpdated": "2026-10-10T20:00:00+00:00",
        }

    def tearDown(self) -> None:
        journal.drain(timeout=3.0)
        self._tmp.cleanup()

    async def test_offline_fallback_is_recorded_separately_without_synthetic_sample_time(self) -> None:
        from powmr_inverter.api import InverterOfflineError

        self.api.fetch_realtime_data.side_effect = InverterOfflineError("untrusted vendor detail")
        result = await self.coordinator._async_update_data()
        self.assertFalse(result["online"])
        self.assertTrue(journal.drain(timeout=3.0))
        records = journal.read_records(self.entry_id)
        self.assertEqual(len(records), 1)
        event = records[0]
        self.assertEqual(event["outcome"], "offline")
        self.assertEqual(event["state"]["source"], "cache")
        self.assertTrue(event["state"]["cached"])
        self.assertTrue(event["state"]["fallback"])
        self.assertEqual(event["state"]["availability"], "offline")
        self.assertGreaterEqual(event["state"]["age_since_last_success_s"], 119.0)
        self.assertIsNone(event["device_measurement_at"])
        self.assertNotEqual(event["device_measurement_at"], result["lastUpdated"])

    async def test_token_expiry_has_its_own_cycle_event(self) -> None:
        from powmr_inverter.api import TokenExpiredError

        self.api.fetch_realtime_data.side_effect = TokenExpiredError("sensitive auth response")
        result = await self.coordinator._async_update_data()
        self.assertFalse(result["online"])
        self.assertTrue(journal.drain(timeout=3.0))
        self.assertEqual(journal.read_records(self.entry_id)[0]["outcome"], "reauth_required")

    async def test_timeout_emits_cycle_event_and_keeps_existing_exception_behavior(self) -> None:
        self.api.fetch_realtime_data.side_effect = TimeoutError("request details must not persist")
        with self.assertRaises(TimeoutError):
            await self.coordinator._async_update_data()
        self.assertTrue(journal.drain(timeout=3.0))
        events = journal.read_records(self.entry_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["outcome"], "timeout")
        self.assertEqual(events[0]["state"]["availability"], "unknown")
        self.assertNotIn("request details", json.dumps(events[0]))


    async def test_hems_decision_evidence_never_claims_unknown_hold(self) -> None:
        from powmr_inverter.coordinator import _evidence_active_holds

        self.assertEqual(_evidence_active_holds("dedup_skip_output"), [])
        self.assertEqual(_evidence_active_holds("circuit_breaker"), ["circuit_breaker"])
        self.assertEqual(_evidence_active_holds("ignored_reason", soc_unknown=True), ["soc_unknown"])
        self.coordinator.hems_auto_mode = True
        self.coordinator._record_hems_evidence(
            "held", "dedup_skip_output", raw=self.coordinator.data, active_holds=["unknown"],
        )
        self.assertTrue(journal.drain(timeout=3.0))
        event = next(row for row in journal.read_records(self.entry_id) if row["event_type"] == "hems_cycle")
        self.assertEqual(event["state"]["active_holds"], [])

    async def test_hems_command_and_api_ack_share_one_evidence_command_id(self) -> None:
        from powmr_inverter.api import InverterApiClient
        from powmr_inverter.coordinator import InverterCoordinator
        from powmr_inverter.hems.engine import HemsDecision
        from powmr_inverter.hems.predictive_control import PredictiveControlEngine
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        api = InverterApiClient("unused@example.test", "not-a-real-password")
        api.device_sn = "test-device"
        api.evidence_entry_id = self.entry_id
        api._session = _FakeSession()
        api._apply_rate_limit = AsyncMock()
        coordinator = InverterCoordinator.__new__(InverterCoordinator)
        coordinator.api = api
        coordinator._entry = SimpleNamespace(entry_id=self.entry_id)
        coordinator._hems = PredictiveControlEngine()
        coordinator.hems_auto_mode = True
        decision = HemsDecision(output_priority="2", reason="test_decision")

        await coordinator._execute_hems_command(decision)
        self.assertTrue(journal.drain(timeout=3.0))
        rows = journal.read_records(self.entry_id)
        hems = next(row for row in rows if row["event_type"] == "hems_cycle")
        intent = next(row for row in rows if row["event_type"] == "command_intent")
        result = next(row for row in rows if row["event_type"] == "command_result")
        self.assertEqual(hems["outcome"], "decision")
        self.assertIs(hems["state"]["effective_hems_auto"], True)
        self.assertEqual(intent["command"]["command_id"], result["command"]["command_id"])
        self.assertEqual(hems["command"]["command_id"], intent["command"]["command_id"])


class EvidenceOrderingRegressionTests(unittest.TestCase):
    def test_writer_preserves_enqueue_order_across_critical_and_normal_events(self) -> None:
        import threading
        import time

        with tempfile.TemporaryDirectory(prefix="powmr-order-") as tmp:
            writer = journal._JournalWriter(max_queue=8)
            path = Path(tmp) / "events.jsonl"
            started = threading.Event()
            release = threading.Event()
            original_write = writer._write
            first_call = True

            def gated_write(target, event, *, critical):
                nonlocal first_call
                if first_call:
                    first_call = False
                    started.set()
                    release.wait(2.0)
                original_write(target, event, critical=critical)

            writer._write = gated_write
            self.assertTrue(writer.enqueue("ordered", path, {"entry_id": "ordered", "sequence": 1}, critical=False))
            self.assertTrue(started.wait(1.0))
            self.assertTrue(writer.enqueue("ordered", path, {"entry_id": "ordered", "sequence": 2}, critical=False))
            self.assertTrue(writer.enqueue("ordered", path, {"entry_id": "ordered", "sequence": 3}, critical=True))
            release.set()
            self.assertTrue(writer.drain(timeout=3.0))
            rows = journal.read_jsonl(path)
            self.assertEqual([row["sequence"] for row in rows], [1, 2, 3])

    def test_drain_barrier_cannot_overtake_an_enqueue_in_progress(self) -> None:
        import threading

        with tempfile.TemporaryDirectory(prefix="powmr-drain-order-") as tmp:
            writer = journal._JournalWriter(max_queue=8)
            path = Path(tmp) / "events.jsonl"
            enqueue_paused = threading.Event()
            release_enqueue = threading.Event()
            barrier_added = threading.Event()
            original_put_nowait = writer._queue.put_nowait
            original_put = writer._queue.put

            def gate_event(item):
                if item[2] == "inflight":
                    enqueue_paused.set()
                    release_enqueue.wait(2.0)
                return original_put_nowait(item)

            def observe_barrier(item, *args, **kwargs):
                if item[5] is not None:
                    barrier_added.set()
                return original_put(item, *args, **kwargs)

            writer._queue.put_nowait = gate_event
            writer._queue.put = observe_barrier
            producer = threading.Thread(
                target=lambda: writer.enqueue(
                    "inflight", path, {"entry_id": "inflight", "sequence": 1}, critical=False
                )
            )
            producer.start()
            self.assertTrue(enqueue_paused.wait(1.0))
            drain_result = []
            drainer = threading.Thread(target=lambda: drain_result.append(writer.drain(timeout=3.0)))
            drainer.start()
            try:
                self.assertFalse(barrier_added.wait(0.05), "drain barrier overtook an enqueue that already held a sequence")
            finally:
                release_enqueue.set()
            producer.join(2.0)
            drainer.join(3.0)
            self.assertFalse(producer.is_alive())
            self.assertFalse(drainer.is_alive())
            self.assertEqual(drain_result, [True])
            self.assertEqual([row["sequence"] for row in journal.read_jsonl(path)], [1])

    def test_stored_untrusted_event_with_unhashable_type_is_ignored(self) -> None:
        malformed = {
            "entry_id": "safe-entry",
            "event_type": [],
            "sequence": 1,
            "session_id": "12345678-1234-1234-1234-123456789abc",
            "event_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.assertIsNone(journal._clean_stored_event(malformed, "safe-entry"))

    def test_export_refuses_symlink_escape_from_config_directory(self) -> None:
        import os

        with tempfile.TemporaryDirectory(prefix="powmr-export-root-") as tmp, tempfile.TemporaryDirectory(prefix="powmr-export-outside-") as outside:
            root = Path(tmp)
            entry_id = "safe-entry"
            journal.configure_entry(entry_id, root / "powmr_inverter_incidents" / entry_id, "1.9.0")
            export_dir = root / "powmr_inverter_incidents" / "exports"
            export_dir.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(outside, export_dir, target_is_directory=True)
            now = datetime.now(timezone.utc)
            with self.assertRaises(ValueError):
                journal.export_bundle_sync(root, entry_id, now - timedelta(hours=1), now)
            self.assertEqual(list(Path(outside).iterdir()), [])


class CommandReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        import uuid
        self._tmp = tempfile.TemporaryDirectory(prefix="powmr-readback-")
        self.entry_id = f"readback-{uuid.uuid4()}"
        journal.configure_entry(self.entry_id, Path(self._tmp.name) / "powmr_inverter_incidents" / self.entry_id, "1.9.0")

    async def asyncTearDown(self) -> None:
        journal.drain(timeout=3.0)
        self._tmp.cleanup()

    async def test_command_readback_requires_newer_fresh_observation(self) -> None:
        from powmr_inverter.api import InverterApiClient
        from unittest.mock import AsyncMock

        api = InverterApiClient("unused@example.test", "not-a-real-password")
        api.device_sn = "test-device"
        api.evidence_entry_id = self.entry_id
        api._session = _FakeSession()
        api._apply_rate_limit = AsyncMock()
        self.assertTrue(await api.set_config_item("outputSourcePrioritySetting", "2"))
        self.assertTrue(journal.drain(timeout=3.0))
        result = next(row for row in journal.read_records(self.entry_id) if row["event_type"] == "command_result")
        accepted_at = datetime.fromisoformat(result["cloud_response_received_at"])

        self.assertEqual(journal.record_readbacks(self.entry_id, {"outputSourcePrioritySetting": "2"}, accepted_at), 0)
        self.assertEqual(journal.record_readbacks(self.entry_id, {"outputSourcePrioritySetting": "2"}, accepted_at + timedelta(seconds=1)), 1)
        self.assertTrue(journal.drain(timeout=3.0))
        readback = next(row for row in journal.read_records(self.entry_id) if row["event_type"] == "command_readback")
        self.assertEqual(readback["outcome"], "observed_match")
        self.assertEqual(readback["command"]["status"], "observed_match")
        self.assertEqual(readback["command"]["command_id"], result["command"]["command_id"])
        self.assertIsNone(readback["cloud_response_received_at"])
        self.assertEqual(
            datetime.fromisoformat(readback["device_measurement_at"]),
            accepted_at + timedelta(seconds=1),
        )

    async def test_cloud_receipt_is_not_a_device_measurement_readback(self) -> None:
        from powmr_inverter.api import InverterApiClient
        from powmr_inverter.coordinator import InverterCoordinator
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        api = InverterApiClient("unused@example.test", "not-a-real-password")
        api.device_sn = "test-device"
        api.evidence_entry_id = self.entry_id
        api._session = _FakeSession()
        api._apply_rate_limit = AsyncMock()
        self.assertTrue(await api.set_config_item("outputSourcePrioritySetting", "2"))
        self.assertTrue(journal.drain(timeout=3.0))
        result = next(row for row in journal.read_records(self.entry_id) if row["event_type"] == "command_result")
        accepted_at = datetime.fromisoformat(result["cloud_response_received_at"])

        api.last_realtime_response_received_at = accepted_at + timedelta(seconds=1)
        api.last_realtime_measurement_at = None
        coordinator = InverterCoordinator.__new__(InverterCoordinator)
        coordinator.api = api
        coordinator._entry = SimpleNamespace(entry_id=self.entry_id)
        coordinator._record_command_readbacks({"outputSourcePriority": "2"})

        self.assertTrue(journal.drain(timeout=3.0))
        self.assertFalse(any(row["event_type"] == "command_readback" for row in journal.read_records(self.entry_id)))

    async def test_coordinator_keeps_readback_sample_and_cloud_receipt_times_distinct(self) -> None:
        from powmr_inverter.api import InverterApiClient
        from powmr_inverter.coordinator import InverterCoordinator
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        api = InverterApiClient("unused@example.test", "not-a-real-password")
        api.device_sn = "test-device"
        api.evidence_entry_id = self.entry_id
        api._session = _FakeSession()
        api._apply_rate_limit = AsyncMock()
        self.assertTrue(await api.set_config_item("outputSourcePrioritySetting", "2"))
        self.assertTrue(journal.drain(timeout=3.0))
        result = next(row for row in journal.read_records(self.entry_id) if row["event_type"] == "command_result")
        accepted_at = datetime.fromisoformat(result["cloud_response_received_at"])
        measurement_at = accepted_at + timedelta(seconds=1)
        receipt_at = accepted_at + timedelta(seconds=4)
        api.last_realtime_measurement_at = measurement_at
        api.last_realtime_response_received_at = receipt_at

        coordinator = InverterCoordinator.__new__(InverterCoordinator)
        coordinator.api = api
        coordinator._entry = SimpleNamespace(entry_id=self.entry_id)
        coordinator._record_command_readbacks({"outputSourcePriority": "2"})
        self.assertTrue(journal.drain(timeout=3.0))
        readback = next(row for row in journal.read_records(self.entry_id) if row["event_type"] == "command_readback")
        self.assertEqual(readback["device_measurement_at"], measurement_at.isoformat(timespec="milliseconds"))
        self.assertEqual(readback["cloud_response_received_at"], receipt_at.isoformat(timespec="milliseconds"))

    async def test_accepted_command_expires_on_poll_without_measurement_time(self) -> None:
        from powmr_inverter.api import InverterApiClient
        from powmr_inverter.coordinator import InverterCoordinator
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        api = InverterApiClient("unused@example.test", "not-a-real-password")
        api.device_sn = "test-device"
        api.evidence_entry_id = self.entry_id
        api._session = _FakeSession()
        api._apply_rate_limit = AsyncMock()
        self.assertTrue(await api.set_config_item("outputSourcePrioritySetting", "2"))
        self.assertTrue(journal.drain(timeout=3.0))
        accepted = next(row for row in journal.read_records(self.entry_id) if row["event_type"] == "command_result")
        accepted_at = datetime.fromisoformat(accepted["cloud_response_received_at"])
        detected_at = accepted_at + timedelta(seconds=journal.COMMAND_READBACK_TTL_SECONDS + 1)
        receipt_at = detected_at - timedelta(seconds=2)
        api.last_realtime_outcome = "offline"
        api.last_realtime_source = "unavailable"
        api.last_realtime_measurement_at = None
        api.last_realtime_response_received_at = receipt_at
        coordinator = InverterCoordinator.__new__(InverterCoordinator)
        coordinator.api = api
        coordinator._entry = SimpleNamespace(entry_id=self.entry_id)
        coordinator.data = None

        with patch.object(journal, "_utc_now", return_value=detected_at):
            coordinator._record_poll_evidence("offline", {})
        self.assertTrue(journal.drain(timeout=3.0))
        readback = next(row for row in journal.read_records(self.entry_id) if row["event_type"] == "command_readback")
        self.assertEqual(readback["outcome"], "unconfirmed")
        self.assertEqual(readback["event_timestamp"], detected_at.isoformat(timespec="milliseconds"))
        self.assertEqual(readback["cloud_response_received_at"], receipt_at.isoformat(timespec="milliseconds"))
        self.assertIsNone(readback["device_measurement_at"])

    async def test_stale_sample_after_readback_ttl_expires_without_match_or_mismatch(self) -> None:
        from powmr_inverter.api import InverterApiClient
        from unittest.mock import AsyncMock, patch

        api = InverterApiClient("unused@example.test", "not-a-real-password")
        api.device_sn = "test-device"
        api.evidence_entry_id = self.entry_id
        api._session = _FakeSession()
        api._apply_rate_limit = AsyncMock()
        self.assertTrue(await api.set_config_item("outputSourcePrioritySetting", "2"))
        self.assertTrue(journal.drain(timeout=3.0))
        accepted = next(row for row in journal.read_records(self.entry_id) if row["event_type"] == "command_result")
        accepted_at = datetime.fromisoformat(accepted["cloud_response_received_at"])
        detected_at = accepted_at + timedelta(seconds=journal.COMMAND_READBACK_TTL_SECONDS + 1)

        with patch.object(journal, "_utc_now", return_value=detected_at):
            self.assertEqual(
                journal.record_readbacks(
                    self.entry_id,
                    {"outputSourcePrioritySetting": "2"},
                    accepted_at,
                ),
                1,
            )
        self.assertTrue(journal.drain(timeout=3.0))
        readback = next(row for row in journal.read_records(self.entry_id) if row["event_type"] == "command_readback")
        self.assertEqual(readback["outcome"], "unconfirmed")
        self.assertEqual(readback["event_timestamp"], detected_at.isoformat(timespec="milliseconds"))
        self.assertIsNone(readback["device_measurement_at"])

    async def test_intent_is_fsynced_before_api_request_and_retry_has_one_final_result(self) -> None:
        from powmr_inverter.api import InverterApiClient
        from unittest.mock import AsyncMock, patch

        class Response(_FakeResponse):
            def __init__(self, code):
                self.code = code

            async def json(self):
                return {"code": self.code}

        class Session:
            def __init__(self):
                self.calls = 0

            def post(self, *args, **kwargs):
                self.calls += 1
                rows = journal.read_records(self_entry_id)
                self_outer.assertTrue(any(row["event_type"] == "command_intent" for row in rows))
                self_outer.assertGreaterEqual(journal.status(self_entry_id)["fsyncs"], 1)
                return Response(1 if self.calls == 1 else 0)

        self_entry_id = self.entry_id
        self_outer = self
        api = InverterApiClient("unused@example.test", "not-a-real-password")
        api.device_sn = "test-device"
        api.evidence_entry_id = self.entry_id
        api._session = Session()
        api._apply_rate_limit = AsyncMock()
        with patch("powmr_inverter.api.asyncio.sleep", new=AsyncMock()):
            self.assertTrue(await api.set_config_item("outputSourcePrioritySetting", "2"))
        self.assertTrue(journal.drain(timeout=3.0))
        results = [row for row in journal.read_records(self.entry_id) if row["event_type"] == "command_result"]
        self.assertEqual([row["outcome"] for row in results], ["api_accepted"])


class ExportServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_service_is_registered_and_runs_deployable_export(self) -> None:
        from types import SimpleNamespace
        from powmr_inverter.const import DOMAIN
        from powmr_inverter.services import async_register_services
        import asyncio

        with tempfile.TemporaryDirectory(prefix="powmr-export-service-") as tmp:
            root = Path(tmp)
            entry_id = "service-entry"
            journal.configure_entry(entry_id, root / "powmr_inverter_incidents" / entry_id, "1.9.0")
            journal.record_event(entry_id, "cycle", outcome="fresh_success", state={"raw_soc": 50.0}, critical=True)
            self.assertTrue(journal.drain(timeout=3.0))

            class Registry:
                def __init__(self):
                    self.handlers = {}

                def async_register(self, domain, name, handler, schema=None):
                    self.handlers[name] = (handler, schema)

            hass = SimpleNamespace(
                data={DOMAIN: {entry_id: {"api": object(), "coordinator": object()}}},
                config_entries=SimpleNamespace(async_entries=lambda domain: [SimpleNamespace(entry_id=entry_id)]),
                services=Registry(),
                config=SimpleNamespace(config_dir=str(root)),
                async_add_executor_job=lambda func, *args: asyncio.to_thread(func, *args),
                bus=SimpleNamespace(async_fire=lambda *args, **kwargs: None),
            )
            await async_register_services(hass)
            self.assertIn("export_incident_evidence", hass.services.handlers)
            handler, schema = hass.services.handlers["export_incident_evidence"]
            self.assertEqual(schema({"entry_id": entry_id, "hours": 24})["entry_id"], entry_id)
            await handler(SimpleNamespace(data={"entry_id": entry_id, "hours": 24}))
            exported = list((root / "powmr_inverter_incidents" / "exports").glob("*.jsonl"))
            self.assertEqual(len(exported), 1)
            self.assertEqual(len(journal.read_jsonl(exported[0])), 1)


class EvidenceLifecycleWiringTests(unittest.TestCase):
    def test_real_setup_and_unload_bind_and_unbind_entry_journal(self) -> None:
        import ast

        source = (REPO_ROOT / "__init__.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        setup = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_setup_entry")
        unload = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_unload_entry")

        def calls(node):
            return [n for n in ast.walk(node) if isinstance(n, ast.Call)]

        setup_calls = calls(setup)
        unload_calls = calls(unload)
        self.assertTrue(any(isinstance(n.func, ast.Attribute) and n.func.attr == "configure_entry" for n in setup_calls))
        self.assertTrue(any(isinstance(n.func, ast.Attribute) and n.func.attr == "unbind_entry" for n in unload_calls))


class PollEvidenceFieldCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        from types import SimpleNamespace
        from powmr_inverter.coordinator import InverterCoordinator

        self._tmp = tempfile.TemporaryDirectory(prefix="powmr-poll-fields-")
        self.root = Path(self._tmp.name)
        self.entry_id = "poll-fields-entry"
        self.entry_dir = self.root / "powmr_inverter_incidents" / self.entry_id
        journal.configure_entry(self.entry_id, self.entry_dir, "1.9.0")
        self.received_at = datetime(2026, 10, 10, 20, 1, tzinfo=timezone.utc)
        self.config_received_at = datetime(2026, 10, 10, 20, 2, tzinfo=timezone.utc)
        self.coordinator = InverterCoordinator.__new__(InverterCoordinator)
        self.coordinator.api = SimpleNamespace(
            last_realtime_source="cloud_primary",
            last_realtime_response_received_at=self.received_at,
            last_realtime_success_received_at=self.received_at,
            last_realtime_measurement_at=None,
            last_device_configs_received_at=self.config_received_at,
        )
        self.coordinator._entry = SimpleNamespace(entry_id=self.entry_id)
        self.coordinator.data = {"batterySoc": 55.0}

    def tearDown(self) -> None:
        journal.drain(timeout=3.0)
        self._tmp.cleanup()

    def test_production_poll_path_records_only_present_alarms_and_fresh_thresholds(self) -> None:
        raw = {
            "batterySoc": 55.0,
            "rawFields": {
                "lowBatteryAlarm": {"value": "1", "valueDisplay": "private display sentinel"},
                "inputVoltageTooHigh": {"value": "0"},
                "bmsAllowChargingFlag": {"value": "1"},
                "private_alarm_secret": {"value": "private alarm sentinel"},
            },
            "payload": {"token": "private payload sentinel"},
        }
        settings = {
            "LowBatteryCutOffVoltageSetting": {
                "value": "47.5", "name": "private config name sentinel",
                "valueDisplay": "private config display sentinel",
            },
            "comebackUtilityModeVolSBUPriority": {"value": 50},
            "private_threshold_secret": {"value": "private threshold sentinel"},
        }

        self.coordinator._record_poll_evidence(
            "fresh_success", raw, device_settings=settings, settings_refreshed=True,
        )
        self.assertTrue(journal.drain(timeout=3.0))
        event = journal.read_records(self.entry_id)[0]
        state = event["state"]

        self.assertEqual(state["alarms"], {"lowBatteryAlarm": True, "inputVoltageTooHigh": False})
        self.assertEqual(state["alarms_source"], "cloud_primary")
        self.assertEqual(state["alarms_fetched_at"], self.received_at.isoformat(timespec="milliseconds"))
        self.assertEqual(state["thresholds"], {
            "LowBatteryCutOffVoltageSetting": 47.5,
            "comebackUtilityModeVolSBUPriority": 50.0,
        })
        self.assertEqual(state["thresholds_source"], "device_config")
        self.assertEqual(state["thresholds_fetched_at"], self.config_received_at.isoformat(timespec="milliseconds"))
        self.assertIsNone(event["device_measurement_at"])
        event_text = json.dumps(event)
        for private in (
            "private display sentinel", "private config name sentinel",
            "private config display sentinel", "private alarm sentinel",
            "private threshold sentinel", "private payload sentinel", "private_alarm_secret",
        ):
            self.assertNotIn(private, event_text)
        self.assertNotIn("bmsAllowChargingFlag", state["alarms"])
        self.assertNotIn("payload", state)
        self.assertNotIn("rawFields", state)
        self.assertTrue(journal.drain(timeout=3.0))

        exported_path, exported_count = journal.export_bundle_sync(
            self.root,
            self.entry_id,
            datetime.now(timezone.utc) - timedelta(minutes=1),
            datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        self.assertEqual(exported_count, 1)
        self.assertNotIn("private", exported_path.read_text(encoding="utf-8"))

    def test_cached_device_settings_do_not_become_fresh_threshold_evidence(self) -> None:
        cached_settings = {
            "LowBatteryCutOffVoltageSetting": {"value": "45.0"},
        }
        self.coordinator._record_poll_evidence(
            "fresh_success",
            {"batterySoc": 55.0, "rawFields": {}},
            device_settings=cached_settings,
            settings_refreshed=False,
        )
        self.assertTrue(journal.drain(timeout=3.0))
        state = journal.read_records(self.entry_id)[0]["state"]
        self.assertNotIn("thresholds", state)
        self.assertNotIn("thresholds_source", state)
        self.assertNotIn("thresholds_fetched_at", state)
        self.assertNotIn("alarms", state)
        self.assertNotIn("alarms_source", state)
        self.assertNotIn("alarms_fetched_at", state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
