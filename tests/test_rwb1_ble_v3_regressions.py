"""Regressions for the two defects found in review v2 of the BLE increment.

D1 -- ``ble_device=`` was routed through ``default_bleakenector_factory``, which
      accepts only a non-empty ``str`` address. A ``BLEDevice`` was therefore
      rejected before any radio work happened. The fix builds the client from
      the ``BLEDevice`` itself, with no address conversion and no local
      discovery. These tests build the real client path with no injected
      factory and no radio connection.

D2 -- a second cancellation delivered while teardown was awaiting
      ``stop_notify`` left the BLE link allocated: the code never reached
      ``disconnect``. Clearing the flags is not enough; teardown must survive
      the second cancel and still release the connection.

Every test here runs against the production module with an injected clock and a
fake adapter. No radio, no network, no HA.
"""

from __future__ import annotations

import asyncio
import unittest

from tests.rwb1_ble_fixtures import TEST_ADDRESS, TEST_DTU_ID, response_for
from tests.test_rwb1_ble_transport import (
    FakeBleakenector,
    identity,
    make_provider,
    request_is_complete,
)
from telemetry_sources import rwb1_ble
from telemetry_sources.rwb1_ble import (
    Rwb1BleTelemetryProvider,
    Rwb1BleTransportError,
    ble_device_to_bleakenector,
)
from tests.rwb1_ble_fixtures import response_envelope


def _synthetic_ble_device():
    """Build a synthetic BLEDevice without importing bleak's backend.

    The provider must not require a real adapter, so the object only has to
    look like the one HA passes in: address, name, details.
    """
    try:
        from bleak.backends.device import BLEDevice
    except Exception:  # pragma: no cover - bleak always present in dev env
        class BLEDevice:  # type: ignore[no-redef]
            def __init__(self, address, name, details, rssi=0):
                self.address = address
                self.name = name
                self.details = details
                self.rssi = rssi

        return BLEDevice(TEST_ADDRESS, "synthetic", {})
    return BLEDevice(TEST_ADDRESS, "synthetic", {})


class BleDeviceClientPathTests(unittest.TestCase):
    """D1: a BLEDevice must produce a client with no address conversion."""

    def test_provider_builds_client_from_ble_device_without_injected_factory(self):
        device = _synthetic_ble_device()
        provider = Rwb1BleTelemetryProvider(
            identity=identity(), dtu_id=TEST_DTU_ID, ble_device=device
        )
        client = provider._build_client()  # construction only: no radio work
        self.assertIsNotNone(client)
        self.assertIsNot(client, device, "a raw BLEDevice is not a Bleak client")

    def test_build_client_does_not_call_default_bleakenector_factory(self):
        """The v2 defect routed BLEDevice into the str-only address factory."""
        device = _synthetic_ble_device()
        calls = []

        def exploding_factory(*args, **kwargs):
            calls.append(args)
            raise AssertionError(
                "default_bleakenector_factory must not receive a BLEDevice"
            )

        original = rwb1_ble.default_bleakenector_factory
        rwb1_ble.default_bleakenector_factory = exploding_factory
        try:
            provider = Rwb1BleTelemetryProvider(
                identity=identity(), dtu_id=TEST_DTU_ID, ble_device=device
            )
            client = provider._build_client()
        finally:
            rwb1_ble.default_bleakenector_factory = original
        self.assertIsNotNone(client)
        self.assertEqual(calls, [], "address-only factory was used for a BLEDevice")

    def test_ble_device_to_bleakenector_returns_a_wrapper(self):
        device = _synthetic_ble_device()
        connector = ble_device_to_bleakenector(device)
        self.assertIsNotNone(connector)
        self.assertIsNot(connector, device)
        for attribute in ("connect", "disconnect", "start_notify", "stop_notify"):
            self.assertTrue(callable(getattr(connector, attribute)), attribute)

    def test_ble_device_to_bleakenector_rejects_none(self):
        with self.assertRaises(Rwb1BleTransportError):
            ble_device_to_bleakenector(None)

    def test_lazy_connector_defers_real_client_class_until_connect(self):
        """No adapter is touched at construction; the class runs at connect()."""
        device = _synthetic_ble_device()
        built = []

        class RecordingClient:
            def __init__(self, dev, timeout=None):
                built.append(dev)

            async def connect(self):
                return "connected"

            async def disconnect(self):
                return "disconnected"

            @property
            def services(self):
                return "services"

        connector = ble_device_to_bleakenector(device, client_class=RecordingClient)
        self.assertEqual(built, [], "client class ran at construction time")
        self.assertEqual(connector.services if False else "deferred", "deferred")
        asyncio.get_event_loop_policy()
        with self.assertRaises(Rwb1BleTransportError):
            _ = connector.services  # must refuse before connect()
        connected = asyncio.run(connector.connect())
        self.assertEqual(connected, "connected")
        self.assertEqual(built, [device])
        asyncio.run(connector.disconnect())


class BoundedCleanupTests(unittest.IsolatedAsyncioTestCase):
    """D2: teardown must survive a second cancel and release the link."""

    async def test_second_cancel_during_stop_notify_still_disconnects(self):
        client = FakeBleakenector()
        # stop_notify below never returns; keep the step budget short.
        provider = make_provider(
            client, cleanup_step_timeout=0.05, cleanup_deadline=5.0
        )
        waiting = asyncio.Event()
        stopping = asyncio.Event()

        async def write(*args, **kwargs):
            waiting.set()
            await asyncio.Event().wait()

        async def stop(*args):
            stopping.set()
            await asyncio.Event().wait()

        client.write_gatt_char = write
        client.stop_notify = stop
        task = asyncio.create_task(provider.read())
        await asyncio.wait_for(waiting.wait(), 5)
        task.cancel()
        await asyncio.wait_for(stopping.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        try:
            self.assertFalse(client.connected, "second cancel left the link open")
            self.assertIn(("disconnect",), client.calls)
        finally:
            await client.disconnect()
            await provider.close()

    async def test_cleanup_step_unblocks_the_next_step_after_cancel(self):
        """A cancel delivered to one cleanup step must not poison the next.

        This is the mechanism the fix relies on: after a CancelledError the
        task's pending-cancellation flag is cleared, so the following await in
        the same teardown is not short-circuited.
        """
        observed = []

        async def step_one(*args):
            observed.append("one")
            await asyncio.Event().wait()  # always cancelled

        async def step_two(*args):
            observed.append("two")

        async def run():
            await rwb1_ble._await_cleanup(step_one, timeout=5.0)
            await rwb1_ble._await_cleanup(step_two, timeout=5.0)

        task = asyncio.create_task(run())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        await task
        self.assertEqual(observed, ["one", "two"],
                         "cleanup stopped after the cancelled step")

    async def test_cleanup_swallows_timeout_and_continues(self):
        observed = []

        async def slow(*args):
            observed.append("slow")
            await asyncio.Event().wait()

        async def fast(*args):
            observed.append("fast")

        await rwb1_ble._await_cleanup(slow, timeout=0.05)
        await rwb1_ble._await_cleanup(fast, timeout=0.05)
        self.assertEqual(observed, ["slow", "fast"],
                         "a timed-out step skipped the next one")

    async def test_cleanup_is_bounded_when_a_step_never_returns(self):
        loop = asyncio.get_running_loop()
        started = loop.time()

        async def never(*args):
            await asyncio.Event().wait()

        await rwb1_ble._await_cleanup(never, timeout=0.05)
        self.assertLess(loop.time() - started, 1.0,
                        "cleanup was not bounded by its timeout")

    async def test_disconnect_tolerates_a_client_without_stop_notify(self):
        """Teardown must not abort when stop_notify is absent."""
        class NoStopClient(FakeBleakenector):
            stop_notify = None

        client = NoStopClient()
        provider = make_provider(client, cleanup_step_timeout=0.5)
        provider._client = client
        provider._notifying = True
        await provider._disconnect()
        self.assertIn(("disconnect",), client.calls)
        self.assertFalse(client.connected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
