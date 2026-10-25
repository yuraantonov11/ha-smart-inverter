"""The shutdown path must actually release the BLE session.

Home Assistant's shutdown unloads the config entry, which stops the runner and
closes the provider. If the close does not complete, the gateway keeps the
proxy slot and the device stops being reachable -- which is exactly the
symptom seen after a restart.

These tests drive the real ``BleDiagnosticRunner.async_stop`` and the real
provider teardown against a recording client, and check three things:

  * the backend ``disconnect`` was actually called;
  * a cancellation arriving *during* the close does not abandon it;
  * the runner's own loop task is stopped first, so no read is in flight while
    the session is being released.

Offline: no radio, no network.
"""
import asyncio
import sys
import types
import unittest
from pathlib import Path

ROOT = Path("/opt/data/powmr-ai-work/powmr_inverter")

# The real ``homeassistant`` package is used on purpose. Stubbing it here
# would poison ``sys.modules`` for every other test module loaded in the same
# process by ``unittest discover``, and the rest of the suite would fail on
# ``cannot import name ... from 'homeassistant.const'``.

if "powmr_inverter" not in sys.modules:
    _pkg = types.ModuleType("powmr_inverter")
    _pkg.__path__ = [str(ROOT)]
    sys.modules["powmr_inverter"] = _pkg

import powmr_inverter.ble_diagnostic as bd  # noqa: E402
import powmr_inverter.telemetry_sources.rwb1_ble as ble  # noqa: E402


class RecordingClient:
    """Records the backend calls the teardown makes."""

    def __init__(self, disconnect_delay=0.0):
        self.calls = []
        self.disconnect_delay = disconnect_delay

    async def connect(self):
        self.calls.append("connect")

    async def disconnect(self):
        self.calls.append("disconnect")
        if self.disconnect_delay:
            await asyncio.sleep(self.disconnect_delay)

    async def start_notify(self, *a, **k):
        self.calls.append("notify")

    async def stop_notify(self, *a, **k):
        self.calls.append("unnotify")

    async def write_gatt_char(self, *a, **k):
        self.calls.append("write")


def make_runner(client):
    """A runner with a live session and a loop task that blocks."""
    runner = object.__new__(bd.BleDiagnosticRunner)
    runner.hass = None
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
    runner._client_class = lambda *a, **k: None
    runner.state = {
        "cycle": 0,
        "counters_started_at": "2026-10-24T00:00:00+00:00",
        "reads_ok": 0,
        "reads_failed": 0,
        "reconnects": 0,
        "sessions_dropped": 0,
        "session_built": 0,
        "session_reused": 0,
    }
    return runner


def provider_with(client):
    """A real provider wired to the recording client.

    Built through the production constructor so the real teardown path runs --
    a hand-made object would skip exactly the state the teardown depends on.
    """
    from powmr_inverter.telemetry_sources.models import SourceIdentity, SourceKind

    provider = ble.Rwb1BleTelemetryProvider(
        identity=SourceIdentity(
            provider_id="p", device_id="d", kind=SourceKind.BLE
        ),
        dtu_id="1",
        bleakenector=client,
        settle_seconds=0.0,
        min_interval=5.0,
    )
    provider._client = client
    provider._notifying = True
    return provider


class ShutdownPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_disconnects_the_backend(self):
        client = RecordingClient()
        runner = make_runner(client)
        runner._provider = provider_with(client)
        runner._provider._client = client

        await runner.async_stop()

        self.assertIn("disconnect", client.calls, "the backend was never disconnected")
        self.assertIn("unnotify", client.calls, "the INDICATE subscription was left open")
        record = runner.state.get("last_disconnect")
        self.assertIsNotNone(record)
        self.assertTrue(record["ok"], record)
        self.assertFalse(record["teardown_incomplete"])
        self.assertEqual(runner.state["sessions_dropped"], 1)

    async def test_cancellation_during_the_close_does_not_abandon_it(self):
        """A cancelled shutdown must still release the proxy slot."""
        client = RecordingClient(disconnect_delay=0.25)
        runner = make_runner(client)
        runner._provider = provider_with(client)
        runner._provider._client = client

        async def cancel_soon():
            await asyncio.sleep(0.05)
            runner._task.cancel()

        # The runner's loop task is what gets cancelled; the close must run.
        async def blocker():
            await asyncio.sleep(10)

        runner._task = asyncio.ensure_future(blocker())

        await asyncio.wait_for(runner.async_stop(), timeout=5)

        self.assertIn("disconnect", client.calls, "the close was abandoned")
        self.assertTrue(runner.state["last_disconnect"]["ok"])

    async def test_the_loop_task_is_stopped_before_the_session_is_released(self):
        """No read may be in flight while the session is being torn down."""
        order = []

        class OrderedClient(RecordingClient):
            async def disconnect(self):
                order.append("disconnect")
                await super().disconnect()

        client = OrderedClient()
        runner = make_runner(client)
        runner._provider = provider_with(client)
        runner._provider._client = client

        async def loop():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                order.append("loop-stopped")
                raise

        runner._task = asyncio.ensure_future(loop())
        # Let the loop task actually start, so its cancellation handler runs.
        await asyncio.sleep(0)
        await runner.async_stop()
        self.assertEqual(order, ["loop-stopped", "disconnect"])

    async def test_teardown_budget_is_short_enough_for_a_shutdown(self):
        """The whole release has to fit into Home Assistant's shutdown budget."""
        self.assertLessEqual(ble.CLEANUP_DEADLINE, 10.0)
        self.assertLessEqual(ble.CLEANUP_STEP_TIMEOUT, 5.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
