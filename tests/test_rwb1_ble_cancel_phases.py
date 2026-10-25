"""Cancel-safety coverage for every phase of a BLE transaction.

Deterministic by construction: where a test needs the backend to still be
releasing, it holds the release open with an explicit event the test controls.
No test encodes progress as a wall-clock sleep, and no test's outcome depends on
a sleep fitting inside a deadline. A release that must finish is awaited on an
event the backend sets after it actually released -- never inferred from elapsed
time.

The one place a deadline is genuinely exercised, it runs against a call that
never returns, so there is no timer to race.

Every test asserts the observable outcome -- the backend was actually released
-- never just that a flag was cleared.
"""
import asyncio
import unittest

from telemetry_sources import rwb1_ble
from telemetry_sources.rwb1_ble import Rwb1BleTelemetryProvider, Rwb1BleTransportError
from tests.rwb1_ble_fixtures import TEST_ADDRESS, TEST_DTU_ID
from tests.test_rwb1_ble_transport import (
    FakeBleakenector,
    identity,
    make_provider,
    request_is_complete,
)

# The step deadline is generous because nothing in this file races it: releases
# are event-driven, so a step either completes when the test says so or never.
STEP = {"cleanup_step_timeout": 5.0, "cleanup_deadline": 10.0}


class Gate:
    """A backend ``disconnect`` the test opens and closes by hand.

    ``started``   -- the teardown reached the backend call.
    ``release``   -- the test lets the backend finish releasing.
    ``completed`` -- the backend finished and recorded the call.

    No sleeps: how long a step takes is whatever the event loop happens to do,
    so the step deadline does not have to be tuned against a timer.
    """

    def __init__(self, client):
        self._client = client
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.completed = asyncio.Event()
        self._original = client.disconnect
        # A gate stands for a backend that currently holds the slot, whether or
        # not the test went through connect(). Set it here so "still holding" is
        # a property of the fake, not an accident of test setup order.
        client.connected = True
        client.disconnect = self._disconnect

    async def _disconnect(self):
        self.started.set()
        await self.release.wait()
        await self._original()
        self.completed.set()

    @property
    def disconnect_recorded(self):
        return ("disconnect",) in self._client.calls


def open_gate(client):
    """A gate that completes as soon as the teardown reaches it."""
    gate = Gate(client)
    gate.release.set()
    return gate


class CancelPhaseTests(unittest.IsolatedAsyncioTestCase):
    """One test per await a caller can cancel at."""

    def _provider(self, **kwargs):
        client = FakeBleakenector()
        options = dict(STEP)
        options.update(kwargs)
        return client, make_provider(client, **options)

    # ---------------------------------------------------------------- connect
    async def test_cancel_during_connect_releases_partial_slot(self):
        """The backend slot is acquired before discovery finishes; cancel there."""
        client, provider = self._provider()
        gate = open_gate(client)
        acquired = asyncio.Event()

        async def stalled_connect():
            client.connected = True   # slot taken, discovery still running
            acquired.set()
            await asyncio.Event().wait()

        client.connect = stalled_connect
        task = asyncio.create_task(provider.read())
        await asyncio.wait_for(acquired.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(gate.completed.wait(), 5)
        self.assertFalse(client.connected, "partial connect left the slot held")
        self.assertTrue(gate.disconnect_recorded)
        self.assertIsNone(provider.teardown_incomplete)
        await provider.close()

    # ------------------------------------------------------------- start_notify
    async def test_cancel_during_start_notify_releases_slot(self):
        client, provider = self._provider()
        gate = open_gate(client)
        started = asyncio.Event()

        async def stalled_notify(characteristic, callback):
            client.calls.append(("start_notify", characteristic))
            client.connected = True
            client.notifying = True
            started.set()
            await asyncio.Event().wait()

        client.start_notify = stalled_notify
        task = asyncio.create_task(provider.read())
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(gate.completed.wait(), 5)
        self.assertFalse(client.connected)
        self.assertFalse(client.notifying)
        self.assertTrue(gate.disconnect_recorded)
        await provider.close()

    # -------------------------------------------------------------------- write
    async def test_cancel_during_write_releases_slot(self):
        client, provider = self._provider()
        gate = open_gate(client)
        writing = asyncio.Event()

        async def stalled_write(*args, **kwargs):
            writing.set()
            await asyncio.Event().wait()

        client.write_gatt_char = stalled_write
        task = asyncio.create_task(provider.read())
        await asyncio.wait_for(writing.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(gate.completed.wait(), 5)
        self.assertFalse(client.connected)
        self.assertTrue(gate.disconnect_recorded)
        await provider.close()

    # ------------------------------------------------------------ response wait
    async def test_cancel_during_response_wait_releases_slot(self):
        client, provider = self._provider()
        gate = open_gate(client)
        original_write = client.write_gatt_char
        sent = asyncio.Event()

        async def write_no_answer(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            if request_is_complete(b"".join(client.written)):
                sent.set()   # request complete, device will never answer

        client.write_gatt_char = write_no_answer
        task = asyncio.create_task(provider.read())
        await asyncio.wait_for(sent.wait(), 5)
        # Hand control back to the loop so the read reaches its response wait
        # before it is cancelled. Loop turns, not a timer.
        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(gate.completed.wait(), 5)
        self.assertFalse(client.connected)
        self.assertTrue(gate.disconnect_recorded)
        await provider.close()

    # ------------------------------------------------------------- stop_notify
    async def test_cancel_during_stop_notify_still_disconnects(self):
        """The teardown step that stalls is stop_notify; disconnect must follow.

        ``stop_notify`` below never returns, so this test *is* a step-deadline
        test: the short budget is the point, and there is no timer for it to race
        against. The disconnect that follows is event-driven.
        """
        client = FakeBleakenector()
        provider = make_provider(client, cleanup_step_timeout=0.05,
                                 cleanup_deadline=5.0)
        gate = open_gate(client)
        active = asyncio.Event()
        stopping = asyncio.Event()

        async def stalled_write(*args, **kwargs):
            active.set()
            await asyncio.Event().wait()

        async def stalled_stop(characteristic):
            stopping.set()
            await asyncio.Event().wait()   # never returns; its step gives up

        client.write_gatt_char = stalled_write
        client.stop_notify = stalled_stop
        task = asyncio.create_task(provider.read())
        await asyncio.wait_for(active.wait(), 5)
        task.cancel()
        await asyncio.wait_for(stopping.wait(), 5)
        task.cancel()   # second cancel lands inside teardown
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(gate.completed.wait(), 5)
        self.assertFalse(client.connected, "second cancel skipped disconnect")
        self.assertTrue(gate.disconnect_recorded)
        await provider.close()

    # --------------------------------------------------------------- disconnect
    async def test_second_cancel_inside_disconnect_still_releases_slot(self):
        """The second cancel lands inside disconnect, not stop_notify."""
        client, provider = self._provider()
        gate = Gate(client)          # release stays CLOSED until the test opens it
        active = asyncio.Event()

        async def stalled_write(*args, **kwargs):
            active.set()
            await asyncio.Event().wait()

        client.write_gatt_char = stalled_write
        task = asyncio.create_task(provider.read())
        await asyncio.wait_for(active.wait(), 5)
        task.cancel()
        await asyncio.wait_for(gate.started.wait(), 5)
        task.cancel()   # lands inside disconnect
        # Let the cancellation be delivered and give the provider its chance to
        # give up on the backend -- loop turns, never a timer.
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertFalse(task.done(),
                         "cancelled caller completed while the release was outstanding")
        self.assertTrue(client.connected, "backend should still hold the slot")
        self.assertFalse(gate.disconnect_recorded)
        gate.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(gate.completed.wait(), 5)
        await provider.close()
        self.assertFalse(client.connected)
        self.assertTrue(gate.disconnect_recorded)
        self.assertIsNone(provider.teardown_incomplete)

    # ------------------------------------------------------------------- close
    async def test_cancel_during_close_still_waits_for_cleanup(self):
        """close() must not abandon teardown when the caller cancels it."""
        client, provider = self._provider()
        gate = Gate(client)
        provider._client = client
        provider._notifying = True
        task = asyncio.create_task(provider.close())
        await asyncio.wait_for(gate.started.wait(), 5)
        task.cancel()
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertFalse(task.done(),
                         "cancelled close() returned while the release was outstanding")
        self.assertTrue(client.connected, "backend should still hold the slot")
        self.assertFalse(gate.disconnect_recorded)
        gate.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(gate.completed.wait(), 5)
        self.assertFalse(client.connected, "cancel made close abandon the release")
        self.assertTrue(gate.disconnect_recorded)
        self.assertIsNone(provider.teardown_incomplete)

    async def test_close_joins_an_in_flight_teardown_instead_of_orphaning_it(self):
        """close() must await the tracked task, not start a second teardown."""
        client, provider = self._provider()
        gate = Gate(client)
        active = asyncio.Event()

        async def stalled_write(*args, **kwargs):
            active.set()
            await asyncio.Event().wait()

        client.write_gatt_char = stalled_write
        read_task = asyncio.create_task(provider.read())
        await asyncio.wait_for(active.wait(), 5)
        read_task.cancel()
        await asyncio.wait_for(gate.started.wait(), 5)
        read_task.cancel()
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertFalse(read_task.done(),
                         "cancelled reader completed while the release was outstanding")
        self.assertFalse(gate.disconnect_recorded,
                         "backend should still hold the slot")
        gate.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await read_task
        # close() joins the in-flight teardown, so once the backend call has
        # finished there can have been no second one.
        await provider.close()
        await asyncio.wait_for(gate.completed.wait(), 5)
        disconnects = [c for c in client.calls if c == ("disconnect",)]
        self.assertEqual(len(disconnects), 1,
                         "close started a second teardown against the same client")
        self.assertFalse(client.connected)
        self.assertIsNone(provider.teardown_incomplete)


class TeardownAccountingTests(unittest.IsolatedAsyncioTestCase):
    """Requirement: a deadline expiry must be visible, not disguised."""

    def _provider(self, **kwargs):
        client = FakeBleakenector()
        options = dict(STEP)
        options.update(kwargs)
        return client, make_provider(client, **options)

    async def test_expired_deadline_is_reported_not_hidden(self):
        """The backend never returns, so nothing here races a timer."""
        client = FakeBleakenector()
        provider = make_provider(client, cleanup_step_timeout=0.05,
                                 cleanup_deadline=5.0)

        async def never_disconnects():
            await asyncio.Event().wait()   # backend never releases the slot

        client.connected = True          # the backend holds the slot and never lets go
        client.disconnect = never_disconnects
        provider._client = client
        provider._notifying = False
        released = await provider._disconnect()
        self.assertFalse(released, "an expired deadline must not report success")
        self.assertIsNotNone(provider.teardown_incomplete)
        self.assertIn("disconnect", provider.teardown_incomplete)
        self.assertIn("may still be held", provider.teardown_incomplete)
        self.assertTrue(client.connected, "the fake never released")

    async def test_completed_teardown_reports_no_incomplete(self):
        client, provider = self._provider()
        provider._client = client
        provider._notifying = True
        self.assertTrue(await provider._disconnect())
        self.assertFalse(client.connected)
        self.assertIsNone(provider.teardown_incomplete)

    async def test_cleanup_task_is_tracked_while_it_runs(self):
        """No untracked background task: the provider holds a reference."""
        client, provider = self._provider()
        gate = Gate(client)
        provider._client = client
        provider._notifying = False
        task = asyncio.create_task(provider._disconnect())
        await asyncio.wait_for(gate.started.wait(), 5)
        self.assertIsNotNone(provider._cleanup_task, "teardown task was untracked")
        self.assertFalse(provider._cleanup_task.done())
        gate.release.set()
        await asyncio.wait_for(gate.completed.wait(), 5)
        self.assertTrue(await task)
        self.assertIsNone(provider._cleanup_task)

    async def test_client_reference_is_retained_until_teardown_ends(self):
        """close() must still have something to join while teardown runs."""
        client, provider = self._provider()
        gate = Gate(client)
        provider._client = client
        provider._notifying = False
        task = asyncio.create_task(provider._disconnect())
        await asyncio.wait_for(gate.started.wait(), 5)
        self.assertIsNone(provider._client,
                          "_client must be cleared so _connect does not reuse it")
        self.assertIs(provider._cleanup_client, client,
                      "the teardown lost its client reference too early")
        gate.release.set()
        await asyncio.wait_for(gate.completed.wait(), 5)
        self.assertTrue(await task)
        self.assertIsNone(provider._cleanup_client)

    async def test_repeated_cancel_does_not_multiply_teardowns(self):
        client, provider = self._provider()
        gate = Gate(client)
        active = asyncio.Event()

        async def stalled_write(*args, **kwargs):
            active.set()
            await asyncio.Event().wait()

        client.write_gatt_char = stalled_write
        task = asyncio.create_task(provider.read())
        await asyncio.wait_for(active.wait(), 5)
        # The first cancel starts teardown; the rest land while the backend
        # release is still outstanding.
        task.cancel()
        await asyncio.wait_for(gate.started.wait(), 5)
        for _ in range(4):
            task.cancel()
            await asyncio.sleep(0)
        self.assertFalse(task.done(),
                         "cancelled caller completed while the release was outstanding")
        gate.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(gate.completed.wait(), 5)
        disconnects = [c for c in client.calls if c == ("disconnect",)]
        self.assertEqual(len(disconnects), 1,
                         "repeated cancellation started more than one teardown")
        self.assertFalse(client.connected)
        await provider.close()


class TeardownContractTests(unittest.TestCase):
    """The teardown coroutine itself must never raise."""

    def test_teardown_client_survives_a_missing_disconnect(self):
        class Bare:
            pass

        asyncio.run(
            rwb1_ble._teardown_client(Bare(), notifying=True, step_timeout=0.01)
        )

    def test_start_cleanup_rejects_a_missing_client(self):
        provider = Rwb1BleTelemetryProvider(
            identity=identity(), dtu_id=TEST_DTU_ID, ble_device=None,
            device_address=TEST_ADDRESS,
        )
        with self.assertRaises(Rwb1BleTransportError):
            provider._start_cleanup(None, notifying=False)

    def test_defaults_are_the_shipping_budgets(self):
        provider = Rwb1BleTelemetryProvider(
            identity=identity(), dtu_id=TEST_DTU_ID, ble_device=None,
            device_address=TEST_ADDRESS,
        )
        self.assertEqual(provider._cleanup_step_timeout,
                         rwb1_ble.CLEANUP_STEP_TIMEOUT)
        self.assertEqual(provider._cleanup_deadline, rwb1_ble.CLEANUP_DEADLINE)

    def test_non_positive_budget_is_rejected(self):
        with self.assertRaises(ValueError):
            Rwb1BleTelemetryProvider(
                identity=identity(), dtu_id=TEST_DTU_ID, ble_device=None,
                device_address=TEST_ADDRESS, cleanup_deadline=0.0,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)