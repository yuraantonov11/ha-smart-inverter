"""RWB1 BLE transport against an injected fake bleakenector.

No Bluetooth stack, radio or network is touched: the fake connector answers
indications from synthetic in-memory fixtures only.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from telemetry_sources.models import SourceIdentity, SourceKind
from telemetry_sources.rwb1_ble import (
    CCCD_UUID, INDICATE_UUID, SERVICE_UUID, WRITE_UUID,
    Rwb1BleRateLimited, Rwb1BleTelemetryProvider, Rwb1BleTimeout,
    Rwb1BleTransportError,
)
from telemetry_sources.rwb1_ble_codec import (
    FragmentReassembler, Rwb1BleFragmentError, decrypt_response, fragment_payload,
)
from telemetry_sources.rwb1_ble_profile import Rwb1BleProfileError

from tests.rwb1_ble_fixtures import (
    EXPECTED_VALUES, TEST_ADDRESS, TEST_DEVICE_ID, TEST_DTU_ID, TEST_KEY, TEST_PROVIDER_ID,
    RealisticBleakenector, decrypt_request_envelope, fragmented, response_envelope,
    response_for, words_with,
)


def identity(kind=SourceKind.BLE):
    return SourceIdentity(TEST_PROVIDER_ID, TEST_DEVICE_ID, kind)


class FakeService:
    def __init__(self, uuid=SERVICE_UUID):
        self.uuid = uuid
        self.characteristics = {}


class FakeServices:
    def __init__(self, present=True):
        self._service = FakeService() if present else None

    def get_service(self, uuid):
        if uuid != SERVICE_UUID:
            return None
        return self._service


class FakeBleakenector:
    """BleakClient-shaped double recording every call it receives."""

    def __init__(
        self,
        *,
        mtu_size=185,
        messages=(),
        service_present=True,
        connect_error=None,
        write_error=None,
        start_notify_error=None,
        emit_unsolicited=False,
    ):
        self.mtu_size = mtu_size
        self.messages = list(messages)
        self.services = FakeServices(service_present)
        self.connect_error = connect_error
        self.write_error = write_error
        self.start_notify_error = start_notify_error
        self.emit_unsolicited = emit_unsolicited
        self.calls: list[tuple] = []
        self.connected = False
        self.notifying = False
        self.written: list[bytes] = []
        self._callback = None
        self._reassembler = FragmentReassembler(max_payload=min(227, mtu_size - 6))

    # -- connection lifecycle -------------------------------------------
    async def connect(self):
        self.calls.append(("connect",))
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def disconnect(self):
        self.calls.append(("disconnect",))
        self.connected = False
        self.notifying = False

    async def start_notify(self, characteristic, callback):
        self.calls.append(("start_notify", characteristic))
        if self.start_notify_error is not None:
            raise self.start_notify_error
        self._callback = callback
        self.notifying = True

    async def stop_notify(self, characteristic):
        self.calls.append(("stop_notify", characteristic))
        self.notifying = False
        self._callback = None

    # -- data path -------------------------------------------------------
    async def write_gatt_char(self, characteristic, data, response=True):
        self.calls.append(("write", characteristic, response))
        if self.write_error is not None:
            raise self.write_error
        self.written.append(bytes(data))
        if self.emit_unsolicited and self.messages:
            self._deliver(self.messages[0])

    def deliver(self, message):
        """Push a complete message to the provider as device fragments."""
        for fragment in fragmented(message, max_payload=min(227, self.mtu_size - 6)):
            self._callback(None, bytearray(fragment))

    def deliver_raw(self, fragment):
        self._callback(None, bytearray(fragment))

    def _deliver(self, message):
        self.deliver(message)


class FakeWallClock:
    """A settable wall clock, so ``received_at`` can be asserted exactly.

    The provider stamps ``received_at`` from ``wall_clock()``. Comparing that
    stamp against a second ``datetime.now()`` sampled in the test compares two
    independent clocks and drifts by however long the assertions take, which
    made ``test_received_at_is_the_last_complete_message_in_the_window`` fail
    intermittently by ~1 us. Driving both sides from one injected clock removes
    the race: the test decides the timestamp, the transport must use exactly it.
    """

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


def make_provider(client, **kwargs):
    options = {
        "identity": identity(),
        "dtu_id": TEST_DTU_ID,
        "bleakenector": client,
        "settle_seconds": 0.0,
    }
    options.update(kwargs)
    return Rwb1BleTelemetryProvider(**options)


def request_command_no(written: bytes, key: bytes = TEST_KEY) -> str:
    """Reassemble and decrypt written fragments; return the sent CmdNo.

    Test-only inverse of :func:`encrypt_request`, so a fake device can echo the
    correlation token it was actually given.
    """
    assembler = FragmentReassembler()
    complete = None
    offset = 0
    for _ in range(written[1]):
        length = written[offset + 2]
        complete = assembler.feed(written[offset : offset + 3 + length]).complete
        offset += 3 + length
    assert complete is not None
    return decrypt_request_envelope(complete, key)["PL"]["CmdNo"]


def request_is_complete(written: bytes) -> bool:
    """True once every announced request fragment has been written."""
    expected = written[1]
    offset = 0
    for _ in range(expected):
        if offset + 3 > len(written):
            return False
        offset += 3 + written[offset + 2]
    return offset == len(written)


class TransportContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.message = response_envelope(response_for(), cmd_no=None)
        self.client = FakeBleakenector(messages=[self.message])
        self.provider = make_provider(
            self.client, min_interval=15.0,
        )

    async def _read_with_response(self, *, cmd_no=None, words=None, message=None):
        """Drive one read: start the provider task, answer its first write."""
        payload = message if message is not None else response_envelope(
            words or response_for(), cmd_no=cmd_no,
        )
        self.client.messages = [payload]
        original_write = self.client.write_gatt_char

        async def write_and_answer(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            self.client.deliver(payload)

        self.client.write_gatt_char = write_and_answer
        return await self.provider.read()

    async def test_successful_read_returns_the_profile_frame(self):
        frame = await self._read_with_response()
        self.assertEqual(frame.profile_id, "rwb1-ble-fee7-21w-le-v1")
        self.assertEqual(frame.identity, identity())
        for name, expected in EXPECTED_VALUES.items():
            with self.subTest(field=name):
                self.assertAlmostEqual(frame.fields[name].value, expected, places=6)
                self.assertIsNone(frame.fields[name].measured_at)
        self.assertIsInstance(frame.received_at, datetime)
        self.assertEqual(frame.received_at.tzinfo, timezone.utc)

    async def test_service_discovery_uses_documented_uuids(self):
        await self._read_with_response()
        characteristics = {call[1] for call in self.client.calls if call[0] == "write"}
        self.assertEqual(characteristics, {WRITE_UUID})
        started = [call[1] for call in self.client.calls if call[0] == "start_notify"]
        self.assertEqual(started, [INDICATE_UUID])
        self.assertEqual(CCCD_UUID, "00002902-0000-1000-8000-00805f9b34fb")

    async def test_request_written_is_the_single_allowlisted_read(self):
        await self._read_with_response()
        # Reassemble the written fragments and decrypt with the fixture key.
        merged = b"".join(self.client.written)
        assembler = FragmentReassembler()
        complete = None
        offset = 0
        for _ in range(merged[1]):
            length = merged[offset + 2]
            complete = assembler.feed(merged[offset : offset + 3 + length]).complete
            offset += 3 + length
        self.assertIsNotNone(complete)
        envelope = decrypt_request_envelope(complete, TEST_KEY)
        self.assertEqual(envelope["CID"], 30024)
        self.assertEqual(envelope["PL"]["Req"], "0503119500159091")
        self.assertEqual(envelope["PL"]["CmdType"], "gatherSingleDevProps")
        self.assertEqual(envelope["PL"]["Uart"]["BaudRate"], 2400)

    async def test_only_one_read_is_performed_per_call(self):
        await self._read_with_response()
        writes = [call for call in self.client.calls if call[0] == "write"]
        self.assertGreaterEqual(len(writes), 1)
        # All written fragments belong to a single request payload.
        self.assertLessEqual(len(writes), 3)

    async def test_cmd_no_present_in_the_answer_is_correlated(self):
        client = FakeBleakenector()
        provider = make_provider(client)
        original_write = client.write_gatt_char
        seen: list[str] = []

        async def write_and_echo(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            written = b"".join(client.written)
            if not request_is_complete(written):
                return  # wait for the final fragment of the request payload
            seen.append(request_command_no(written))
            client.deliver(response_envelope(response_for(), cmd_no=seen[0]))

        client.write_gatt_char = write_and_echo
        frame = await provider.read()
        self.assertEqual(frame.profile_id, "rwb1-ble-fee7-21w-le-v1")
        self.assertEqual(len(seen[0]), 4)

    async def test_mismatched_cmd_no_in_the_answer_is_rejected(self):
        client = FakeBleakenector()
        provider = make_provider(client)
        original_write = client.write_gatt_char

        async def write_and_wrong_answer(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            client.deliver(response_envelope(response_for(), cmd_no="ZZZZ"))

        client.write_gatt_char = write_and_wrong_answer
        with self.assertRaisesRegex(Exception, "CmdNo"):
            await provider.read()

    async def test_missing_cmd_no_in_the_answer_is_accepted(self):
        frame = await self._read_with_response(cmd_no=None)
        self.assertEqual(frame.fields["battery_soc"].value, 86.0)

    async def test_timeout_disconnects_and_backs_off(self):
        client = FakeBleakenector(messages=[])
        provider = make_provider(client, response_timeout=0.05)
        with self.assertRaises(Rwb1BleTimeout):
            await provider.read()
        self.assertIn(("disconnect",), client.calls)
        self.assertEqual(provider.consecutive_failures, 1)

    async def test_cleanup_unsubscribes_and_disconnects(self):
        await self._read_with_response()
        await provider_close(self.provider)
        self.assertIn(("stop_notify", INDICATE_UUID), self.client.calls)
        self.assertIn(("disconnect",), self.client.calls)
        self.assertFalse(self.provider.is_connected)
        self.assertFalse(self.client.notifying)

    async def test_close_is_idempotent(self):
        await provider_close(self.provider)
        disconnects = [c for c in self.client.calls if c[0] == "disconnect"]
        await provider_close(self.provider)
        self.assertEqual(len([c for c in self.client.calls if c[0] == "disconnect"]), len(disconnects))

    async def test_read_after_close_is_refused(self):
        await provider_close(self.provider)
        with self.assertRaisesRegex(Rwb1BleTransportError, "closed"):
            await self.provider.read()

    async def test_reconnect_after_a_failed_attempt(self):
        client = FakeBleakenector(messages=[], connect_error=OSError("adapter busy"))
        now = [1000.0]
        provider = make_provider(
            client, response_timeout=0.05, clock=lambda: now[0],
            min_interval=5.0, initial_backoff=30.0,
        )
        with self.assertRaises(OSError):
            await provider.read()
        self.assertIn(("disconnect",), client.calls)
        # Immediately afterwards the *backoff* window blocks a second attempt.
        # The min_interval is stepped over first, so this exercises the backoff
        # rather than the rate limit -- previously both raised the same type and
        # the test passed without ever reaching the backoff check.
        now[0] += 5.0
        with self.assertRaisesRegex(Rwb1BleTimeout, "backoff"):
            await provider.read()
        now[0] += 60.0  # clears the backoff
        client.connect_error = None
        original_write = client.write_gatt_char

        async def write_and_answer(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            client.deliver(self.message)

        client.write_gatt_char = write_and_answer
        frame = await provider.read()
        self.assertEqual(frame.profile_id, "rwb1-ble-fee7-21w-le-v1")
        self.assertEqual(provider.consecutive_failures, 0)

    async def test_backoff_grows_and_is_capped(self):
        now = [0.0]
        client = FakeBleakenector(messages=[], connect_error=OSError("down"))
        provider = make_provider(
            client, response_timeout=0.05, clock=lambda: now[0],
            initial_backoff=2.0, max_backoff=8.0,
        )
        observed = []
        for _ in range(4):
            now[0] += 15.0  # step past the minimum read interval
            with self.assertRaises(OSError):
                await provider.read()
            observed.append(provider._next_attempt_at - now[0])
            now[0] = provider._next_attempt_at
        self.assertEqual(observed, [2.0, 4.0, 8.0, 8.0])

    async def test_missing_service_is_reported_after_connecting(self):
        """Real bleak only discovers services *after* connect().

        The old assertion (``connect`` never called) pinned an impossible
        order: ``client.services`` raises ``BleakError("Service Discovery has
        not been performed yet")`` before a connection exists, verified
        against bleak 3.0.2.
        """
        client = RealisticBleakenector(service_present=False)
        provider = make_provider(client)
        with self.assertRaisesRegex(Rwb1BleTransportError, "FEE7 service"):
            await provider.read()
        # Discovery happened, so connect must have run first - and the failed
        # attempt must still tear the link down.
        self.assertIn(("connect",), client.calls)
        self.assertIn(("disconnect",), client.calls)
        self.assertNotIn(("start_notify", INDICATE_UUID), client.calls)

    async def test_service_discovery_precedes_notification_on_a_realistic_client(self):
        """The whole happy path on a double that forbids pre-connect access."""
        client = RealisticBleakenector(messages=[])
        provider = make_provider(client)
        payload = response_envelope(response_for())
        original_write = client.write_gatt_char

        async def write_and_answer(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            client.deliver(payload)

        client.write_gatt_char = write_and_answer
        frame = await provider.read()
        self.assertEqual(frame.profile_id, "rwb1-ble-fee7-21w-le-v1")
        order = [call[0] for call in client.calls]
        self.assertLess(order.index("connect"), order.index("start_notify"))

    async def test_notify_failure_cleans_up(self):
        client = FakeBleakenector(start_notify_error=OSError("notify refused"))
        provider = make_provider(client)
        with self.assertRaises(OSError):
            await provider.read()
        self.assertIn(("disconnect",), client.calls)

    async def test_write_failure_cleans_up(self):
        client = FakeBleakenector(write_error=OSError("write refused"))
        provider = make_provider(client)
        with self.assertRaises(OSError):
            await provider.read()
        self.assertIn(("disconnect",), client.calls)

    async def test_corrupt_fragment_is_reported_and_disconnects(self):
        client = FakeBleakenector()
        provider = make_provider(client)
        original_write = client.write_gatt_char

        async def write_and_corrupt(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            client.deliver_raw(bytes((0, 1, 4)) + b"junk")  # zero index

        client.write_gatt_char = write_and_corrupt
        with self.assertRaises(Rwb1BleFragmentError):
            await provider.read()
        self.assertIn(("disconnect",), client.calls)

    async def test_unexpected_modbus_payload_is_rejected(self):
        client = FakeBleakenector()
        provider = make_provider(client)
        original_write = client.write_gatt_char

        async def write_and_wrong_payload(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            client.deliver(response_envelope(response_for(address=4)))

        client.write_gatt_char = write_and_wrong_payload
        with self.assertRaises(Rwb1BleProfileError):
            await provider.read()

    async def test_only_one_transaction_at_a_time(self):
        client = FakeBleakenector()
        # Each clock read advances 300 s so the minimum-interval guard passes and
        # the lock, not the rate limiter, is what serializes the two reads.
        tick = [1000.0]

        def clock() -> float:
            tick[0] += 15.0
            return tick[0]

        provider = make_provider(client, clock=clock)
        entered = []
        original_write = client.write_gatt_char

        async def write_and_answer(characteristic, data, response=True):
            entered.append("start")
            await original_write(characteristic, data, response=response)
            # Yield to the loop twice instead of sleeping: the assertions below
            # are about ordering, so a wall-clock delay would be noise.
            for _ in range(2):
                await asyncio.sleep(0)
            client.deliver(response_envelope(response_for()))
            entered.append("end")

        client.write_gatt_char = write_and_answer
        results = await asyncio.gather(provider.read(), provider.read())
        self.assertEqual(len(results), 2)
        # Serialized: every window closes before the next one opens, even though
        # each read writes more than one fragment.
        self.assertTrue(len(entered) % 2 == 0 and len(entered) >= 4)
        self.assertEqual(entered, ["start" if i % 2 == 0 else "end" for i in range(len(entered))])

    async def test_minimum_interval_blocks_back_to_back_reads(self):
        now = [500.0]
        client = FakeBleakenector()
        provider = make_provider(client, clock=lambda: now[0])
        original_write = client.write_gatt_char

        async def write_and_answer(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            client.deliver(response_envelope(response_for()))

        client.write_gatt_char = write_and_answer
        await provider.read()
        now[0] += 1.0  # still inside the floor
        with self.assertRaisesRegex(Rwb1BleRateLimited, "minimum interval"):
            await provider.read()
        now[0] += 300.0  # clears the floor
        self.assertEqual((await provider.read()).profile_id, "rwb1-ble-fee7-21w-le-v1")

    async def test_unsolicited_frames_before_the_answer_are_ignored(self):
        client = FakeBleakenector()
        provider = make_provider(client)
        original_write = client.write_gatt_char

        async def write_with_noise(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            client.deliver(response_envelope(response_for(), cid=1))
            client.deliver(response_envelope(response_for()))

        client.write_gatt_char = write_with_noise
        frame = await provider.read()
        self.assertEqual(frame.fields["load_power"].value, 1073.0)

    async def test_out_of_range_values_reach_the_caller_as_unknown(self):
        client = FakeBleakenector()
        provider = make_provider(client)
        original_write = client.write_gatt_char

        async def write_and_answer(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            client.deliver(
                response_envelope(response_for(words_with({6: 101, 12: 60_000})))
            )

        client.write_gatt_char = write_and_answer
        frame = await provider.read()
        self.assertIsNone(frame.fields["battery_soc"].value)
        self.assertIsNone(frame.fields["load_power"].value)
        self.assertEqual(frame.fields["pv_power"].value, 0.0)

    async def test_large_mtu_still_respects_the_protocol_fragment_cap(self):
        client = FakeBleakenector(mtu_size=517)
        provider = make_provider(client)
        original_write = client.write_gatt_char

        async def write_and_answer(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            client.deliver(response_envelope(response_for()))

        client.write_gatt_char = write_and_answer
        frame = await provider.read()
        self.assertEqual(frame.profile_id, "rwb1-ble-fee7-21w-le-v1")
        self.assertTrue(all(len(chunk) - 3 <= 227 for chunk in client.written))

    async def test_received_at_is_the_moment_the_answer_arrived(self):
        """F5: ``received_at`` must stamp the receipt, not the request start.

        The transport writes several fragments and waits for a notification;
        a frame stamped at transaction start reports a value as fresh when it
        may be seconds stale. The clock is injected and only ever moves when the
        test moves it, so the expected stamp is an exact constant rather than a
        ``datetime.now()`` comparison that drifts with assertion time.
        """
        client = FakeBleakenector()
        clock = FakeWallClock()
        provider = make_provider(client, wall_clock=clock)
        original_write = client.write_gatt_char
        stamps = {}

        async def write_and_answer(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            written = b"".join(client.written)
            if not request_is_complete(written):
                return  # answer only once the whole request is on the wire
            # The device takes a measurable amount of time to answer.
            stamps["request_sent"] = clock.now
            clock.advance(0.25)
            stamps["arrival"] = clock.now
            client.deliver(response_envelope(response_for()))

        client.write_gatt_char = write_and_answer
        frame = await provider.read()
        self.assertEqual(frame.received_at,
                         datetime.fromtimestamp(stamps["arrival"], tz=timezone.utc))
        # The stamp is the arrival moment, strictly later than the request start.
        self.assertGreater(frame.received_at,
                           datetime.fromtimestamp(stamps["request_sent"], tz=timezone.utc))
        self.assertIsNone(frame.fields["grid_voltage"].measured_at)

    async def test_received_at_is_the_last_complete_message_in_the_window(self):
        """With noise plus a real answer, the stamp follows the used message.

        Deterministic: an unsolicited message lands first and the real answer
        second. Both arrival instants are recorded on the injected clock, so
        the assertions are exact arithmetic -- this was the test that failed
        intermittently in review v2.
        """
        client = FakeBleakenector()
        clock = FakeWallClock()
        provider = make_provider(client, wall_clock=clock)
        original_write = client.write_gatt_char
        stamps = {}

        async def write_with_noise_then_answer(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            written = b"".join(client.written)
            if not request_is_complete(written):
                return  # answer only once the whole request is on the wire
            clock.advance(0.05)
            stamps["noise"] = clock.now
            client.deliver(response_envelope(response_for(), cid=1))  # unsolicited
            clock.advance(0.2)
            stamps["real"] = clock.now
            client.deliver(response_envelope(response_for()))  # the real answer

        client.write_gatt_char = write_with_noise_then_answer
        frame = await provider.read()
        self.assertEqual(frame.fields["load_power"].value, 1073.0)
        # The stamp must be the arrival of the *used* message (the real answer),
        # not the unsolicited noise and not the transaction start.
        self.assertEqual(frame.received_at,
                         datetime.fromtimestamp(stamps["real"], tz=timezone.utc))
        self.assertGreater(frame.received_at,
                           datetime.fromtimestamp(stamps["noise"], tz=timezone.utc),
                           "received_at must follow the arrival of the used message, "
                           "not the noise")
        self.assertEqual(
            (frame.received_at
             - datetime.fromtimestamp(stamps["noise"], tz=timezone.utc)).total_seconds(),
            0.2,
        )


class TransportCancellationTests(unittest.IsolatedAsyncioTestCase):
    """F4: cancelling a read must release the link and refuse late work."""

    def _provider_with_stalled_device(self, **kwargs):
        client = FakeBleakenector()
        provider = make_provider(client, response_timeout=30.0, **kwargs)
        # Keep the pristine double: the stall hook replaces the instance
        # attribute, so a later "answer" hook must chain to *this*, not to the
        # stall wrapper.
        original_write = client.write_gatt_char
        # Set the moment the device holds the whole request and will not answer.
        # Tests wait on this instead of sleeping: "the read reached its response
        # wait" is an ordering fact, not a duration.
        waiting = asyncio.Event()

        async def stall(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            # Only the final fragment stalls: the provider still has to be able
            # to write the earlier ones before the request is complete.
            if request_is_complete(b"".join(client.written)):
                waiting.set()
                await asyncio.Event().wait()  # device never answers

        client.write_gatt_char = stall
        return client, provider, original_write, waiting

    async def test_cancelled_read_disconnects_and_releases_the_slot(self):
        client, provider, _original, waiting = self._provider_with_stalled_device()
        task = asyncio.create_task(provider.read())
        await asyncio.wait_for(waiting.wait(), 5)  # read is on its response wait
        self.assertTrue(provider.is_connected)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIn(("stop_notify", INDICATE_UUID), client.calls)
        self.assertIn(("disconnect",), client.calls)
        self.assertFalse(provider.is_connected)
        self.assertFalse(client.notifying)

    async def test_cancelled_read_does_not_count_as_a_device_failure(self):
        """A cancellation is a caller decision, not a BLE failure."""
        client, provider, _original, waiting = self._provider_with_stalled_device()
        task = asyncio.create_task(provider.read())
        await asyncio.wait_for(waiting.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(provider.consecutive_failures, 0)
        self.assertEqual(provider._next_attempt_at, 0.0)

    async def test_read_refused_when_closed_while_waiting_for_the_lock(self):
        """A read queued behind the lock must re-check ``closed`` on entry.

        ``close()`` takes the same lock, so a read that already passed its
        ``closed`` check can end up acquiring the lock *after* teardown.
        """
        client = FakeBleakenector()
        provider = make_provider(client)
        # Hold the lock so the read below queues on it, exactly as it would
        # behind another in-flight read.
        await provider._lock.acquire()
        released = False
        try:
            queued = asyncio.create_task(provider.read())
            await asyncio.sleep(0)  # let the task reach ``async with self._lock``
            self.assertFalse(queued.done())
            provider._closed = True  # teardown happened while the read waited
            provider._lock.release()
            released = True
            with self.assertRaisesRegex(Rwb1BleTransportError, "closed"):
                await queued
        finally:
            if not released:
                provider._lock.release()
        self.assertNotIn(("connect",), client.calls)
        self.assertEqual(
            [c for c in client.calls if c[0] == "write"], [],
            "a closed provider must not issue GATT writes",
        )

    async def test_a_late_notification_after_cancel_is_dropped(self):
        """Unsolicited frames must not leak into a later transaction."""
        client, provider, original_write, waiting = self._provider_with_stalled_device(
            min_interval=15.0)
        now = [1000.0]
        provider._clock = lambda: now[0]
        task = asyncio.create_task(provider.read())
        await asyncio.wait_for(waiting.wait(), 5)
        callback = client._callback
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        # Device pushes an unsolicited frame into the closed window.
        callback(None, bytearray(fragmented(response_envelope(response_for()))[0]))
        now[0] += 15.0  # clears the floor

        async def write_and_answer(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            client.deliver(response_envelope(response_for()))

        client.write_gatt_char = write_and_answer
        frame = await provider.read()
        self.assertEqual(frame.profile_id, "rwb1-ble-fee7-21w-le-v1")


class TransportConstructionTests(unittest.IsolatedAsyncioTestCase):
    def test_identity_kind_must_be_ble(self):
        with self.assertRaises(ValueError):
            make_provider(FakeBleakenector(), identity=identity(SourceKind.LAN))

    def test_minimum_interval_cannot_go_below_the_floor(self):
        # The floor is a guard, not a measured hardware limit; assert against
        # the constant so a deliberate floor change does not read as a defect.
        from telemetry_sources.rwb1_ble import DEFAULT_MIN_INTERVAL

        self.assertEqual(DEFAULT_MIN_INTERVAL, 5.0)
        with self.assertRaisesRegex(ValueError, "at least 5"):
            make_provider(FakeBleakenector(), min_interval=1.0)

    async def test_rate_limit_skip_is_not_a_device_error(self):
        """The local rate limit must not look like a transport failure.

        It used to raise Rwb1BleTimeout, so a live interval change produced a
        spurious device error in the diagnostics and invited the recovery path.
        """
        from telemetry_sources.rwb1_ble import (
            Rwb1BleError,
            Rwb1BleRateLimited,
        )

        self.assertFalse(
            issubclass(Rwb1BleRateLimited, Rwb1BleTransportError),
            "a skipped cycle must not be a transport error",
        )
        self.assertFalse(
            issubclass(Rwb1BleRateLimited, Rwb1BleTimeout),
            "a skipped cycle must not be a device timeout",
        )
        self.assertTrue(issubclass(Rwb1BleRateLimited, Rwb1BleError))

        now = [500.0]
        client = FakeBleakenector(messages=[])
        payload = response_envelope(response_for(), cmd_no=None)
        client.messages = [payload]
        original_write = client.write_gatt_char

        async def write_and_answer(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            client.deliver(payload)

        client.write_gatt_char = write_and_answer
        provider = make_provider(
            client, min_interval=60.0, clock=lambda: now[0], settle_seconds=0.0
        )
        frame = await provider.read()
        self.assertEqual(frame.profile_id, "rwb1-ble-fee7-21w-le-v1")
        with self.assertRaisesRegex(Rwb1BleRateLimited, "minimum interval"):
            await provider.read()
        # The skip left the live session intact.
        self.assertIn(("connect",), client.calls)

    def test_intervals_used_in_production_are_accepted(self):
        """5 s and 10 s must both survive the provider's own validation.

        Regression: the runner's floor and the provider's floor disagreed once,
        and every cycle ended in a ValueError instead of a read.
        """
        from telemetry_sources.rwb1_ble import DEFAULT_MIN_INTERVAL

        for interval in (DEFAULT_MIN_INTERVAL, 10.0):
            provider = make_provider(FakeBleakenector(), min_interval=interval)
            self.assertEqual(provider._min_interval, interval)

    def test_timeout_and_backoff_arguments_are_validated(self):
        with self.assertRaises(ValueError):
            make_provider(FakeBleakenector(), response_timeout=0)
        with self.assertRaises(ValueError):
            make_provider(FakeBleakenector(), connect_timeout=-1)
        with self.assertRaises(ValueError):
            make_provider(FakeBleakenector(), initial_backoff=10.0, max_backoff=1.0)

    def test_repr_hides_secrets_and_address(self):
        provider = make_provider(FakeBleakenector())
        text = repr(provider)
        self.assertIn(TEST_PROVIDER_ID, text)
        self.assertNotIn(TEST_DTU_ID, text)
        self.assertNotIn(TEST_KEY.hex(), text)
        self.assertNotIn(TEST_ADDRESS, text)

    def test_client_factory_is_used_lazily(self):
        calls = []

        def factory():
            calls.append("built")
            return FakeBleakenector()

        provider = Rwb1BleTelemetryProvider(
            identity=identity(), dtu_id=TEST_DTU_ID, bleakenector=factory,
            device_address=TEST_ADDRESS, settle_seconds=0.0,
        )
        self.assertEqual(calls, [])

    async def test_injected_factory_is_the_client_that_actually_gets_used(self):
        """F3: the injected callable must build the client, not the default.

        Previously a callable ``bleakenector`` was replaced by
        ``default_bleakenector_factory(...)``, so an HA-provided factory could
        never run.
        """
        client = RealisticBleakenector(messages=[])
        payload = response_envelope(response_for())
        calls = []
        original_write = client.write_gatt_char

        def factory():
            calls.append("built")
            return client

        provider = Rwb1BleTelemetryProvider(
            identity=identity(), dtu_id=TEST_DTU_ID, bleakenector=factory,
            device_address=TEST_ADDRESS, settle_seconds=0.0,
        )

        async def write_and_answer(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            client.deliver(payload)

        client.write_gatt_char = write_and_answer
        frame = await provider.read()
        self.assertEqual(calls, ["built"])
        self.assertEqual(frame.profile_id, "rwb1-ble-fee7-21w-le-v1")
        self.assertIn(("connect",), client.calls)

    async def test_injected_factory_is_used_even_without_a_device_address(self):
        """An injected factory needs no address: it owns device resolution."""
        client = FakeBleakenector()
        provider = Rwb1BleTelemetryProvider(
            identity=identity(), dtu_id=TEST_DTU_ID,
            bleakenector=lambda: client, settle_seconds=0.0,
        )
        original_write = client.write_gatt_char

        async def write_and_answer(characteristic, data, response=True):
            await original_write(characteristic, data, response=response)
            client.deliver(response_envelope(response_for()))

        client.write_gatt_char = write_and_answer
        frame = await provider.read()
        self.assertEqual(frame.profile_id, "rwb1-ble-fee7-21w-le-v1")

    async def test_factory_returning_a_non_client_is_refused(self):
        provider = Rwb1BleTelemetryProvider(
            identity=identity(), dtu_id=TEST_DTU_ID,
            bleakenector=lambda: object(), device_address=TEST_ADDRESS,
            settle_seconds=0.0,
        )
        with self.assertRaisesRegex(Rwb1BleTransportError, "must be a client or a factory"):
            await provider.read()

    async def test_bad_connector_object_is_refused_on_first_read(self):
        provider = make_provider(object())
        with self.assertRaisesRegex(Rwb1BleTransportError, "must be a client or a factory"):
            await provider.read()

    async def test_default_factory_without_any_device_is_refused(self):
        """No injected client and no device: the address requirement stands.

        This replaces the old ``test_factory_without_device_address_is_refused``,
        which pinned the F3 bug by demanding an address even when a factory
        that does not need one was supplied.
        """
        provider = Rwb1BleTelemetryProvider(
            identity=identity(), dtu_id=TEST_DTU_ID, bleakenector=None,
            settle_seconds=0.0,
        )
        with self.assertRaisesRegex(Rwb1BleTransportError, "device"):
            await provider.read()

    def test_key_length_matches_the_documented_cipher(self):
        from telemetry_sources.rwb1_ble_codec import derive_key

        self.assertEqual(len(derive_key(TEST_DTU_ID)), 16)


async def provider_close(provider):
    await provider.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)