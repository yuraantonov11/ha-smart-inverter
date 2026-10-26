"""A teardown that did not complete must say WHICH step was left unfinished.

The live evidence after the Home Assistant restart on 2026-10-26 was::

    RWB1 BLE session disconnected: ok=False teardown_incomplete=True in 0.000s

``in 0.000s`` does not mean "the step timed out" -- a clean teardown also
finishes in 0.0 s. It means the step *raised immediately*. The report only
said "something did not complete", so the operator could not tell whether the
backend slot had been released.

These tests pin the two things that make the next occurrence diagnosable:

* the provider's verdict names the step and the exception;
* the runner publishes that reason, so it survives in the entity state and
  in the log line.

The failing client raises the way a broken bleak client does -- the
connection is already broken, so ``disconnect()`` raises instead of closing
the link.
"""
import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path("/opt/data/powmr-ai-work/powmr_inverter")

# The real ``homeassistant`` package is used on purpose: stubbing it would
# poison ``sys.modules`` for every other test module in the same process.
if "powmr_inverter" not in sys.modules:
    _pkg = types.ModuleType("powmr_inverter")
    _pkg.__path__ = [str(ROOT)]
    sys.modules["powmr_inverter"] = _pkg

import powmr_inverter.ble_diagnostic as bd  # noqa: E402
try:  # the tests directory may or may not be on sys.path
    from tests.ble_test_hass import FakeHass
except ImportError:  # pragma: no cover - direct execution
    from ble_test_hass import FakeHass

import powmr_inverter.telemetry_sources.rwb1_ble as ble  # noqa: E402


class BrokenClient:
    """A client whose link is already broken: disconnect() raises at once."""

    def __init__(self, error=None):
        self.calls = []
        self.error = error or RuntimeError("Backend already closed")

    async def connect(self):
        self.calls.append("connect")

    async def disconnect(self):
        self.calls.append("disconnect")
        raise self.error

    async def stop_notify(self, *a, **k):
        self.calls.append("unnotify")


def make_runner():
    runner = object.__new__(bd.BleDiagnosticRunner)
    runner.hass = FakeHass(tempfile.mkdtemp(prefix="ble-test-"))
    runner.entry = types.SimpleNamespace(options={}, data={}, entry_id="t")
    runner.address = "D4:27:87:E4:F9:4F"
    runner.dtu_id = "1"
    runner.interval = 10.0
    runner.response_timeout = 12.0
    runner.enabled = True
    runner._provider = None
    runner._dropped_sessions = 0
    runner._lock = asyncio.Lock()
    runner._task = None
    runner._stop = asyncio.Event()
    runner.state = {
        "cycle": 0,
        "counters_started_at": "2026-10-26T00:00:00+00:00",
        "reads_ok": 0,
        "reads_failed": 0,
        "reconnects": 0,
        "sessions_dropped": 0,
        "session_built": 0,
        "session_reused": 0,
    }
    return runner


def provider_with(client):
    from powmr_inverter.telemetry_sources.models import SourceIdentity, SourceKind

    provider = ble.Rwb1BleTelemetryProvider(
        identity=SourceIdentity(provider_id="p", device_id="d", kind=SourceKind.BLE),
        dtu_id="1",
        bleakenector=client,
        settle_seconds=0.0,
        min_interval=5.0,
    )
    provider._client = client
    provider._notifying = False
    return provider


class UnfinishedStepTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_verdict_names_the_step_that_did_not_complete(self):
        client = BrokenClient()
        provider = provider_with(client)

        failed = await ble._teardown_client(
            client, notifying=False, step_timeout=1.0
        )

        self.assertEqual(len(failed), 1, failed)
        self.assertTrue(
            failed[0].startswith("disconnect"),
            f"the unfinished step is not named: {failed[0]!r}",
        )
        self.assertIn("RuntimeError", failed[0])
        self.assertIn("Backend already closed", failed[0])

    async def test_a_step_that_times_out_says_deadline(self):
        class SlowClient(BrokenClient):
            async def disconnect(self):
                self.calls.append("disconnect")
                await asyncio.sleep(5)

        client = SlowClient()
        failed = await ble._teardown_client(
            client, notifying=False, step_timeout=0.05
        )

        self.assertEqual(len(failed), 1, failed)
        self.assertIn("deadline", failed[0])

    async def test_the_runner_publishes_the_reason(self):
        client = BrokenClient()
        runner = make_runner()
        runner._provider = provider_with(client)

        await runner.async_stop()

        record = runner.state.get("last_disconnect")
        self.assertIsNotNone(record)
        self.assertFalse(record["ok"], record)
        self.assertTrue(record["teardown_incomplete"], record)
        reason = record.get("teardown_reason")
        self.assertIsNotNone(reason, "the reason must be published")
        self.assertIn("disconnect", reason)
        self.assertIn("RuntimeError", reason)

    async def test_a_clean_close_publishes_no_reason(self):
        class GoodClient(BrokenClient):
            async def disconnect(self):
                self.calls.append("disconnect")

        client = GoodClient()
        runner = make_runner()
        runner._provider = provider_with(client)

        await runner.async_stop()

        record = runner.state.get("last_disconnect")
        self.assertTrue(record["ok"], record)
        self.assertIsNone(record.get("teardown_reason"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
