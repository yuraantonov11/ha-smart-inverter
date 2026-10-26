"""Reliable termination and recovery of the BLE session.

Three contracts are pinned here, all of them from the failure that was
measured on 2026-10-26:

1. **Shutdown is ordered.** No new read may start, the read in flight gets
   a moment to finish, ``provider.close()`` is awaited to the end, and the
   teardown result is written to disk so it survives the restart.
2. **The fault is decided by the reads.** The age of the last successful
   read together with a run of consecutive failures. The proxy's allocation
   list is not evidence: it also lists a perfectly healthy session.
3. **A healthy session is never torn down.** The release only runs when the
   runner holds no session at all, and the number of attempts is bounded.

Offline: no radio, no network. The proxy is a fake, the clock is a fake.
"""
import asyncio
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/opt/data/powmr-ai-work/powmr_inverter")

# The real ``homeassistant`` package is used on purpose: stubbing it would
# poison ``sys.modules`` for every other test module in the same process.
if "powmr_inverter" not in sys.modules:
    _pkg = types.ModuleType("powmr_inverter")
    _pkg.__path__ = [str(ROOT)]
    sys.modules["powmr_inverter"] = _pkg

import powmr_inverter.ble_diagnostic as bd  # noqa: E402
from powmr_inverter.telemetry_sources import ble_recovery  # noqa: E402

NOW = [1000.0]
NOW_UTC = datetime(2026, 10, 26, 7, 0, 0, tzinfo=timezone.utc)


def make_runner(tmpdir=None):
    """A runner with the production state, wired like the real one."""
    if tmpdir is None:
        # A directory of its own: the recovery budget is persisted, and
        # tests must not share that file.
        tmpdir = tempfile.mkdtemp(prefix="ble-recovery-test-")
    runner = object.__new__(bd.BleDiagnosticRunner)
    runner.hass = types.SimpleNamespace(
        config=types.SimpleNamespace(config_dir=str(tmpdir)),
    )
    runner.entry = types.SimpleNamespace(options={}, data={}, entry_id="t")
    runner.address = "D4:27:87:E4:F9:4F"
    runner.dtu_id = "50659432846709074818"
    runner.interval = 10.0
    runner.response_timeout = 12.0
    runner.enabled = True
    runner._provider = None
    runner._dropped_sessions = 0
    runner._lock = asyncio.Lock()
    runner._task = None
    runner._stop = asyncio.Event()
    runner._stopping = False
    runner._paused = False
    runner._consecutive_failures = 0
    runner._last_success_at = None
    runner._release_budget = ble_recovery.ReleaseBudget()
    runner._recovery_enabled = True
    runner._recovery_max_success_age_s = 90.0
    runner._recovery_min_failures = 5
    runner._recovery_max_attempts = 3
    runner._recovery_cooldown_s = 300.0
    runner._shutdown_read_grace_s = 2.0
    runner._clock = lambda: NOW[0]
    runner._now = lambda: NOW_UTC
    runner._entities = []
    runner.state = {
        "cycle": 0,
        "counters_started_at": "2026-10-26T00:00:00+00:00",
        "reads_ok": 0,
        "reads_failed": 0,
        "reconnects": 0,
        "sessions_dropped": 0,
        "session_built": 0,
        "session_reused": 0,
        "skipped_cycles": 0,
        "consecutive_failures": 0,
        "last_fault": None,
        "last_disconnect": None,
        "last_success": None,
        "adapter_for_device": "54:EF:44:CF:32:16",
        "recovery": {
            "enabled": True,
            "state": "idle",
            "attempts": 0,
            "max_attempts": 3,
            "last_result": None,
            "at": None,
        },
    }
    return runner


class FakeProvider:
    """A provider whose teardown did not complete."""

    def __init__(self, reason="disconnect: RuntimeError: boom"):
        self.teardown_incomplete = reason
        self.closed = False

    async def close(self):
        self.closed = True

    async def read(self):
        raise RuntimeError("no")


class FakeClient:
    """Stands in for the bleak client of the provider."""

    def __init__(self, delay=0.0, fail=None):
        self.delay = delay
        self.fail = fail
        self.closed = False
        self.disconnect_calls = 0

    async def read(self):
        if self.fail is not None:
            raise self.fail
        if self.delay:
            await asyncio.sleep(self.delay)
        return types.SimpleNamespace(
            profile_id="rwb1-ble-fee7-21w-le-v1",
            fields={},
            received_at=None,
        )

    async def close(self):
        self.disconnect_calls += 1
        self.closed = True


class FaultDetectionTests(unittest.TestCase):
    """The fault is decided by the reads, never by the proxy's bookkeeping."""

    def test_a_recent_success_means_no_fault(self):
        signal = ble_recovery.detect_fault(
            last_success_at=NOW_UTC - timedelta(seconds=5),
            consecutive_failures=1,
            now=NOW_UTC,
        )
        self.assertIsNone(signal)

    def test_an_old_success_plus_failures_is_a_fault(self):
        signal = ble_recovery.detect_fault(
            last_success_at=NOW_UTC - timedelta(seconds=200),
            consecutive_failures=5,
            now=NOW_UTC,
        )
        self.assertIsNotNone(signal)
        self.assertIn("no successful read", signal.reason)
        self.assertEqual(signal.success_age_s, 200.0)

    def test_failures_alone_without_an_old_success_are_not_a_fault(self):
        """A handful of failures after a good read is not a broken link."""
        signal = ble_recovery.detect_fault(
            last_success_at=NOW_UTC - timedelta(seconds=10),
            consecutive_failures=4,
            now=NOW_UTC,
        )
        self.assertIsNone(signal)

    def test_no_success_at_all_is_a_fault(self):
        signal = ble_recovery.detect_fault(
            last_success_at=None,
            consecutive_failures=5,
            now=NOW_UTC,
        )
        self.assertIsNotNone(signal)
        self.assertIsNone(signal.success_age_s)
        self.assertIn("no successful read", signal.reason)

    def test_the_detection_has_no_allocation_input(self):
        """The proxy's allocation list cannot influence the verdict."""
        import inspect

        params = set(inspect.signature(ble_recovery.detect_fault).parameters)
        self.assertNotIn("allocated", params)
        self.assertNotIn("state", params)


class ReleaseBudgetTests(unittest.TestCase):
    """The number of attempts is bounded and the cooldown is respected."""

    def test_the_attempt_limit_is_enforced(self):
        budget = ble_recovery.ReleaseBudget(max_attempts=2, cooldown_s=0.0)
        now = datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc)
        self.assertTrue(budget.may_attempt(now))
        budget.record(now)
        self.assertTrue(budget.may_attempt(now + timedelta(seconds=1)))
        budget.record(now + timedelta(seconds=1))
        self.assertFalse(budget.may_attempt(now + timedelta(seconds=2)))

    def test_the_cooldown_blocks_an_early_attempt(self):
        budget = ble_recovery.ReleaseBudget(max_attempts=5, cooldown_s=300.0)
        now = datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc)
        budget.record(now)
        self.assertFalse(budget.may_attempt(now + timedelta(seconds=299)))
        self.assertTrue(budget.may_attempt(now + timedelta(seconds=300)))

    def test_a_reset_clears_the_budget(self):
        budget = ble_recovery.ReleaseBudget(max_attempts=1, cooldown_s=0.0)
        now = datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc)
        budget.record(now)
        self.assertFalse(budget.may_attempt(now + timedelta(seconds=1)))
        budget.reset()
        self.assertTrue(budget.may_attempt(now + timedelta(seconds=1)))

    def test_a_persisted_budget_keeps_its_attempts(self):
        """A restart must not hand out a fresh series of attempts."""
        now = datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc)
        budget = ble_recovery.ReleaseBudget(max_attempts=3, cooldown_s=300.0)
        budget.record(now)

        restored = ble_recovery.ReleaseBudget.from_dict(budget.as_dict())

        self.assertEqual(restored.attempts, 1)
        self.assertEqual(restored.max_attempts, 3)
        self.assertEqual(restored.last_attempt_at, now)
        self.assertFalse(
            restored.may_attempt(now + timedelta(seconds=10)),
            "the restored budget must still count the attempt",
        )

    def test_a_corrupt_budget_record_does_not_grant_attempts(self):
        restored = ble_recovery.ReleaseBudget.from_dict({"attempts": "not a number"})
        self.assertEqual(restored.attempts, 0)
        restored = ble_recovery.ReleaseBudget.from_dict(None)
        self.assertEqual(restored.attempts, 0)


class AddressEncodingTests(unittest.TestCase):
    """ESPHome encodes the BLE address big-endian."""

    def test_the_address_is_encoded_big_endian(self):
        value = ble_recovery.address_to_esphome_int("D4:27:87:E4:F9:4F")
        self.assertEqual(value, 0xD42787E4F94F)

    def test_a_bad_address_is_rejected(self):
        with self.assertRaises(ValueError):
            ble_recovery.address_to_esphome_int("D4:27:87")


class FakeAPIClient:
    def __init__(self):
        self.calls = []
        self.fail = None

    async def bluetooth_device_disconnect(self, address):
        self.calls.append(address)
        if self.fail is not None:
            raise self.fail


def fake_hass(esphome_entries, bluetooth_entries=()):
    """A hass with just enough of the config-entry API."""
    return types.SimpleNamespace(
        config_entries=types.SimpleNamespace(
            async_entries=lambda domain: (
                esphome_entries
                if domain == "esphome"
                else list(bluetooth_entries)
                if domain == "bluetooth"
                else []
            ),
            async_get_entry=lambda entry_id: next(
                (e for e in esphome_entries if e.entry_id == entry_id), None
            ),
        )
    )


def esphome_entry(entry_id, host="192.168.1.146", mac="54:EF:44:CF:32:16", client=None):
    entry = types.SimpleNamespace(
        entry_id=entry_id,
        title="Gateway BT Proxy",
        data={
            "host": host,
            "port": 6053,
            "password": "",
            "bluetooth_mac_address": mac,
        },
    )
    entry.runtime_data = types.SimpleNamespace(client=client)
    return entry


class ReleaserTests(unittest.IsolatedAsyncioTestCase):
    """The release is address-targeted and goes through the right proxy."""

    async def test_it_releases_the_address_on_the_matching_proxy(self):
        client = FakeAPIClient()
        hass = fake_hass(
            [esphome_entry("e1", mac="00:11:22:33:44:55"),
             esphome_entry("e2", client=client)]
        )
        releaser = ble_recovery.GatewayConnectionReleaser(hass, "54:EF:44:CF:32:16")

        outcome = await releaser.release("D4:27:87:E4:F9:4F")

        self.assertTrue(outcome.ok, outcome.detail)
        self.assertEqual(client.calls, [0xD42787E4F94F])

    async def test_without_a_scanner_source_nothing_is_attempted(self):
        releaser = ble_recovery.GatewayConnectionReleaser(fake_hass([]), None)
        outcome = await releaser.release("D4:27:87:E4:F9:4F")
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.attempted)

    async def test_without_a_matching_proxy_nothing_is_attempted(self):
        hass = fake_hass([esphome_entry("e1", mac="00:11:22:33:44:55")])
        releaser = ble_recovery.GatewayConnectionReleaser(hass, "54:EF:44:CF:32:16")
        outcome = await releaser.release("D4:27:87:E4:F9:4F")
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.attempted)

    async def test_a_failing_proxy_is_reported_with_the_reason(self):
        client = FakeAPIClient()
        client.fail = RuntimeError("proxy said no")
        hass = fake_hass([esphome_entry("e1", client=client)])
        releaser = ble_recovery.GatewayConnectionReleaser(hass, "54:EF:44:CF:32:16")
        outcome = await releaser.release("D4:27:87:E4:F9:4F")
        self.assertFalse(outcome.ok)
        self.assertIn("proxy said no", outcome.detail)

    async def test_the_only_proxy_is_found_without_a_known_source(self):
        """The stranded case: the device is unknown, so there is no source."""
        client = FakeAPIClient()
        hass = fake_hass([esphome_entry("e2", client=client)])
        releaser = ble_recovery.GatewayConnectionReleaser(hass, None)
        outcome = await releaser.release("D4:27:87:E4:F9:4F")
        self.assertTrue(outcome.ok, outcome.detail)
        self.assertEqual(client.calls, [0xD42787E4F94F])

    async def test_several_proxies_without_a_source_are_ambiguous(self):
        client = FakeAPIClient()
        hass = fake_hass(
            [
                esphome_entry("e1", mac="00:11:22:33:44:55", client=client),
                esphome_entry("e2", client=client),
            ]
        )
        releaser = ble_recovery.GatewayConnectionReleaser(hass, None)
        outcome = await releaser.release("D4:27:87:E4:F9:4F")
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.attempted, "an ambiguous proxy must not be touched")

    async def test_the_bluetooth_entry_points_at_the_esphome_entry(self):
        client = FakeAPIClient()
        esphome = esphome_entry("e2", mac="00:11:22:33:44:55", client=client)
        bluetooth = types.SimpleNamespace(
            entry_id="b1",
            unique_id="54:EF:44:CF:32:16",
            data={"source": "54:EF:44:CF:32:16", "source_config_entry_id": "e2"},
        )
        hass = fake_hass([esphome], [bluetooth])
        releaser = ble_recovery.GatewayConnectionReleaser(hass, "54:EF:44:CF:32:16")
        outcome = await releaser.release("D4:27:87:E4:F9:4F")
        self.assertTrue(outcome.ok, outcome.detail)
        self.assertEqual(client.calls, [0xD42787E4F94F])


class RecoveryCoordinationTests(unittest.IsolatedAsyncioTestCase):
    """Pause -> release -> resume, with the guards in place."""

    def _patch_releaser(self, outcome=None, record=None):
        calls = []

        class FakeReleaser:
            def __init__(self, hass, source):
                self.hass = hass
                self.source = source

            async def release(self, address):
                calls.append(address)
                if record is not None:
                    record.append(self)
                return outcome or ble_recovery.ReleaseOutcome(
                    ok=True, detail="released"
                )

        original = ble_recovery.GatewayConnectionReleaser
        ble_recovery.GatewayConnectionReleaser = FakeReleaser
        self.addCleanup(setattr, ble_recovery, "GatewayConnectionReleaser", original)
        return calls

    async def test_a_fault_triggers_exactly_one_release(self):
        runner = make_runner()
        runner._consecutive_failures = 5
        runner._last_success_at = NOW_UTC - timedelta(seconds=300)
        calls = self._patch_releaser()

        await runner._maybe_recover()

        self.assertEqual(calls, ["D4:27:87:E4:F9:4F"])
        self.assertEqual(runner.state["recovery"]["state"], "released")
        self.assertFalse(runner._paused, "the reads must resume")

    async def test_a_recent_success_keeps_the_session_even_with_a_provider(self):
        """A live session is protected by the reads, not by the provider.

        The provider's presence alone must not decide anything -- a stuck
        client is exactly what leaves a slot stranded.
        """
        runner = make_runner()
        runner._consecutive_failures = 5
        runner._last_success_at = NOW_UTC - timedelta(seconds=5)
        runner._provider = FakeClient()
        calls = self._patch_releaser()

        await runner._maybe_recover()

        self.assertEqual(calls, [])
        self.assertFalse(runner._provider.closed, "a working session was closed")

    async def test_the_provider_is_closed_before_the_release(self):
        """The order is: pause, close the provider, then release by address."""
        runner = make_runner()
        runner._consecutive_failures = 5
        runner._last_success_at = NOW_UTC - timedelta(seconds=300)
        provider = FakeClient()
        runner._provider = provider
        order = []
        provider.close = lambda: order.append("close")

        async def _close():
            order.append("close")

        provider.close = _close

        class FakeReleaser:
            def __init__(self, hass, source):
                pass

            async def release(self, address):
                order.append("release")
                return ble_recovery.ReleaseOutcome(ok=True, detail="released")

        original = ble_recovery.GatewayConnectionReleaser
        ble_recovery.GatewayConnectionReleaser = FakeReleaser
        self.addCleanup(setattr, ble_recovery, "GatewayConnectionReleaser", original)

        await runner._maybe_recover()

        self.assertEqual(order, ["close", "release"])

    async def test_a_recent_success_prevents_any_release(self):
        runner = make_runner()
        runner._consecutive_failures = 5
        runner._last_success_at = NOW_UTC - timedelta(seconds=5)
        calls = self._patch_releaser()

        await runner._maybe_recover()

        self.assertEqual(calls, [])
        self.assertIsNone(runner.state["last_fault"])

    async def test_the_number_of_attempts_is_bounded(self):
        global NOW_UTC
        runner = make_runner()
        runner._consecutive_failures = 5
        runner._last_success_at = NOW_UTC - timedelta(seconds=300)
        calls = self._patch_releaser()

        for _ in range(5):
            await runner._maybe_recover()
            # past the cooldown
            NOW_UTC = NOW_UTC + timedelta(seconds=400)

        self.assertEqual(len(calls), 3, "the attempt limit must hold")
        self.assertEqual(runner.state["recovery"]["state"], "budget_exhausted")

    async def test_a_failed_release_is_recorded(self):
        runner = make_runner()
        runner._consecutive_failures = 5
        runner._last_success_at = NOW_UTC - timedelta(seconds=300)
        self._patch_releaser(
            outcome=ble_recovery.ReleaseOutcome(ok=False, detail="proxy said no")
        )

        await runner._maybe_recover()

        self.assertEqual(runner.state["recovery"]["state"], "failed")
        self.assertFalse(runner.state["recovery"]["last_result"]["ok"])


class RecoveryBudgetPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """The attempt budget must survive a reload and a restart."""

    async def test_the_budget_is_restored_from_disk(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / "powmr_ble_diagnostic.conf"
            conf.write_text("enabled=1\ninterval=10\nresponse_timeout=12\n")

            first = make_runner(td)
            first._recovery_enabled = True
            first._release_budget = ble_recovery.ReleaseBudget(
                max_attempts=3, cooldown_s=300.0
            )
            first._release_budget.record(NOW_UTC)
            first._persist_recovery_budget()

            # A fresh runner -- as after a restart -- restores the budget.
            second = make_runner(td)
            second._load_recovery_budget()

            self.assertEqual(second._release_budget.attempts, 1)
            self.assertEqual(second._release_budget.max_attempts, 3)

    async def test_a_reload_does_not_reset_the_attempts(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / "powmr_ble_diagnostic.conf"
            conf.write_text(
                "enabled=1\ninterval=10\nresponse_timeout=12\n"
                "recovery_max_attempts=3\nrecovery_cooldown_s=300\n"
            )
            runner = make_runner(td)
            runner._release_budget = ble_recovery.ReleaseBudget(
                max_attempts=3, cooldown_s=300.0
            )
            runner._release_budget.record(NOW_UTC)

            runner._apply_recovery_settings(
                {
                    "recovery_enabled": "1",
                    "recovery_max_attempts": "3",
                    "recovery_cooldown_s": "300",
                }
            )

            self.assertEqual(
                runner._release_budget.attempts,
                1,
                "a reload must not reset the attempt counter",
            )

    async def test_the_automatic_release_is_off_by_default(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / "powmr_ble_diagnostic.conf"
            conf.write_text("enabled=1\ninterval=10\nresponse_timeout=12\n")
            runner = make_runner(td)
            runner._apply_recovery_settings({})
            self.assertFalse(
                runner._recovery_enabled,
                "the automatic release must be opt-in",
            )


class ShutdownSequenceTests(unittest.IsolatedAsyncioTestCase):
    """The shutdown order: no new reads, finish or cancel, close, persist."""

    def test_the_shutdown_budget_stays_bounded(self):
        """Grace plus teardown must fit into Home Assistant's budget.

        The grace is deliberately short: Home Assistant's shutdown gives an
        integration a few seconds, and a long grace would starve the close
        and leave the proxy slot stranded.
        """
        import powmr_inverter.telemetry_sources.rwb1_ble as ble

        self.assertLessEqual(
            bd.SHUTDOWN_READ_GRACE_MAX_S + ble.CLEANUP_DEADLINE, 15.0
        )
        self.assertLessEqual(bd.SHUTDOWN_READ_GRACE_DEFAULT_S, 3.0)

    def test_the_default_grace_is_short(self):
        """The production constructor must produce a short grace."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / "powmr_ble_diagnostic.conf"
            conf.write_text("enabled=1\ninterval=10\nresponse_timeout=12\n")
            hass = types.SimpleNamespace(
                config=types.SimpleNamespace(config_dir=str(td)),
            )
            entry = types.SimpleNamespace(options={}, data={}, entry_id="t")
            runner = bd.BleDiagnosticRunner(hass, entry)
            self.assertLessEqual(runner._shutdown_read_grace_s, 3.0)
            self.assertGreaterEqual(runner._shutdown_read_grace_s, 0.5)

    async def test_no_new_read_starts_after_the_stop(self):
        runner = make_runner()
        runner._stopping = True
        state = await runner.read_once()
        self.assertEqual(state["status"], bd.STATUS_STOPPING)
        self.assertEqual(state["cycle"], 0, "a read must not have been started")

    async def test_the_read_in_flight_is_allowed_to_finish(self):
        runner = make_runner()
        started = asyncio.Event()
        finished = []

        async def slow_read():
            started.set()
            await asyncio.sleep(0.05)
            finished.append(True)

        runner._task = asyncio.ensure_future(slow_read())
        await started.wait()

        await runner.async_stop()

        self.assertEqual(finished, [True], "the read must not be cancelled")

    async def test_a_hung_read_is_cancelled(self):
        runner = make_runner()
        started = asyncio.Event()

        async def hung_read():
            started.set()
            await asyncio.sleep(30)

        runner._task = asyncio.ensure_future(hung_read())
        await started.wait()
        runner._shutdown_read_grace_s = 0.05

        await asyncio.wait_for(runner.async_stop(), timeout=5)

        self.assertTrue(runner._task is None)

    async def test_the_close_is_awaited_after_a_cancellation(self):
        """The shield alone does not finish the cleanup.

        The caller is cancelled while the close is in flight. The close task
        must be kept and awaited to the end, and the teardown result must be
        written anyway.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            runner = make_runner(td)
            client = FakeClient()
            runner._provider = client
            close_started = asyncio.Event()

            async def slow_close():
                close_started.set()
                await asyncio.sleep(0.2)
                client.disconnect_calls += 1
                client.closed = True

            runner._drop_session = slow_close

            stop = asyncio.ensure_future(runner.async_stop())
            await close_started.wait()
            stop.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await stop
            # the cancellation is honoured, but only after the release

            # The close ran to the end even though the shutdown was cancelled.
            self.assertEqual(client.disconnect_calls, 1)
            path = Path(td) / bd.TEARDOWN_FILENAME
            self.assertTrue(
                path.exists(), "the teardown record must be written on cancellation"
            )

    async def test_the_teardown_record_is_written_when_the_close_fails(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            runner = make_runner(td)

            async def failing_close():
                raise RuntimeError("close exploded")

            runner._drop_session = failing_close

            with self.assertRaises(RuntimeError):
                await runner.async_stop()

            path = Path(td) / bd.TEARDOWN_FILENAME
            self.assertTrue(
                path.exists(), "the teardown record must be written on failure"
            )
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsNone(record["last_disconnect"])

    async def test_the_teardown_result_is_persisted(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            runner = make_runner(td)
            provider = FakeProvider()
            runner._provider = provider

            await runner.async_stop()

            path = Path(td) / bd.TEARDOWN_FILENAME
            self.assertTrue(path.exists(), "the teardown record was not written")
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(record["last_disconnect"]["ok"])
            self.assertTrue(record["last_disconnect"]["teardown_incomplete"])
            self.assertIn("disconnect", record["last_disconnect"]["teardown_reason"])
            self.assertTrue(provider.closed, "close must be awaited")
            self.assertEqual(record["address"], "D4:27:87:E4:F9:4F")


if __name__ == "__main__":
    unittest.main(verbosity=2)
