"""Synthetic fixtures for the experimental RWB1 BLE path.

Every value here is invented for tests. There are no real MAC addresses, no
real IP addresses, no real DTU identifiers, no real AES keys and no captured
envelopes. The DTU identifier is the literal placeholder ``TEST-DTU-0001`` and
the BLE address is an obviously fake locally-administered address. Nothing in
this module reaches a radio or a network.
"""

from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from telemetry_sources.rwb1_ble_codec import UART_PROFILE, derive_key
from telemetry_sources.rwb1_ble_profile import (
    ALLOWED_REQUESTS,
    REGISTER_COUNT,
    modbus_crc16,
)


TEST_DTU_ID = "TEST-DTU-0001"
TEST_KEY = derive_key(TEST_DTU_ID)
TEST_ADDRESS = "AA:BB:CC:DD:EE:F0"  # locally administered, synthetic
TEST_DEVICE_ID = "rwb1-test-device"
TEST_PROVIDER_ID = "rwb1_ble_experimental"

# A plausible, entirely invented register block. Values are chosen so that a
# big-endian decode would produce visibly absurd magnitudes.
TEST_WORDS: tuple[int, ...] = (
    0x0000,  # 0
    2339,    # 1  grid voltage 233.9 V
    0x0000,  # 2
    0x0000,  # 3
    0,        # 4  pv power 0 W
    529,      # 5  battery voltage 52.9 V
    86,       # 6  battery SOC 86 %
    0x0000,  # 7
    0x0000,  # 8
    0x0000,  # 9
    0x0000,  # 10
    0x0000,  # 11
    1073,     # 12 load power 1073 W
    0x0000,  # 13
    0x0000,  # 14
    0x0000,  # 15
    0x0000,  # 16
    0x0000,  # 17
    0x0000,  # 18
    0x0000,  # 19
    0x0000,  # 20
)
assert len(TEST_WORDS) == REGISTER_COUNT

EXPECTED_VALUES = {
    "grid_voltage": 233.9,
    "pv_power": 0.0,
    "battery_voltage": 52.9,
    "battery_soc": 86.0,
    "load_power": 1073.0,
}


def response_for(words=TEST_WORDS, *, address: int = 5, function: int = 3) -> bytes:
    """Build a synthetic little-endian 21-word Modbus response."""
    data = b"".join(word.to_bytes(2, "little") for word in words)
    body = bytes((address, function, len(data))) + data
    return body + modbus_crc16(body).to_bytes(2, "little")


def words_with(overrides: dict[int, int]) -> tuple[int, ...]:
    """Return ``TEST_WORDS`` with the given word indexes replaced."""
    words = list(TEST_WORDS)
    for index, value in overrides.items():
        if not 0 <= index < REGISTER_COUNT:
            raise ValueError("word index out of range")
        words[index] = value
    return tuple(words)


def encrypt_envelope(payload: dict, key: bytes = TEST_KEY) -> bytes:
    """Encrypt an arbitrary envelope the same way the device does."""
    plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    plaintext += b"\0" * (16 - len(plaintext) % 16)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key)).encryptor()
    return base64.b64encode(encryptor.update(plaintext) + encryptor.finalize())


def response_envelope(
    modbus: bytes = None,
    *,
    cid: int = 30025,
    rc: int = 0,
    cmd_no: str | None = None,
    omit_rsp: bool = False,
    key: bytes = TEST_KEY,
) -> bytes:
    """Build a synthetic encrypted device response envelope."""
    payload: dict = {"Uart": dict(UART_PROFILE), "CmdType": "gatherSingleDevProps"}
    if cmd_no is not None:
        payload["CmdNo"] = cmd_no
    if not omit_rsp:
        payload["Rsp"] = (modbus if modbus is not None else response_for()).hex().upper()
    return encrypt_envelope({"CID": cid, "RC": rc, "PL": payload}, key)


def decrypt_request_envelope(message: bytes, key: bytes = TEST_KEY) -> dict:
    """Decrypt a synthetic request envelope; test-only inverse helper."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    raw = base64.b64decode(message, validate=True)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(key)).decryptor()
    return json.loads((decryptor.update(raw) + decryptor.finalize()).rstrip(b"\0").decode())


def fragmented(message: bytes, *, max_payload: int = 227) -> list[bytes]:
    """Split a message into synthetic RWB1 transport fragments."""
    chunks = [
        message[offset : offset + max_payload] for offset in range(0, len(message), max_payload)
    ]
    total = len(chunks)
    return [bytes((index, total, len(chunk))) + chunk for index, chunk in enumerate(chunks, 1)]


class BleakLikeError(Exception):
    """Stand-in for :class:`bleak.BleakError`.

    Used by :class:`RealisticBleakenector` so the test suite keeps working in
    environments where ``bleak`` is not installed at all.
    """


class RealisticBleakenector:
    """A client double whose *properties* fail before ``connect()``.

    This mirrors the real ``bleak.BleakClient`` lifecycle, verified against
    bleak 3.0.2:

    * ``client.services`` raises ``BleakError("Service Discovery has not been
      performed yet")`` until a connection has completed service discovery.
    * ``client.mtu_size`` reads the backend, which only exists while connected.
    * ``write_gatt_char`` / ``start_notify`` fail when not connected.

    A permissive double that hands out attributes eagerly lets ordering bugs
    (reading services before connecting, caching an MTU before connect) pass
    unnoticed; this one cannot.
    """

    def __init__(
        self,
        *,
        mtu_size: int = 185,
        messages: tuple[bytes, ...] = (),
        service_present: bool = True,
        connect_error: Exception | None = None,
        write_error: Exception | None = None,
        start_notify_error: Exception | None = None,
    ) -> None:
        self._mtu_size = int(mtu_size)
        self._service_present = bool(service_present)
        self.messages = list(messages)
        self.connect_error = connect_error
        self.write_error = write_error
        self.start_notify_error = start_notify_error
        self.calls: list[tuple] = []
        self.written: list[bytes] = []
        self.connected = False
        self.notifying = False
        self._callback = None

    # -- lifecycle properties, bleak-faithful ----------------------------
    @property
    def mtu_size(self) -> int:
        if not self.connected:
            raise BleakLikeError("MTU size is only available while connected")
        return self._mtu_size

    @property
    def services(self):
        if not self.connected:
            raise BleakLikeError("Service Discovery has not been performed yet")
        return _ConnectedServices(self._service_present)

    @property
    def is_connected(self) -> bool:
        return self.connected

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
        self._callback = None

    async def start_notify(self, characteristic, callback):
        self.calls.append(("start_notify", characteristic))
        if not self.connected:
            raise BleakLikeError("not connected")
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
        if not self.connected:
            raise BleakLikeError("not connected")
        if self.write_error is not None:
            raise self.write_error
        self.written.append(bytes(data))

    def deliver(self, message):
        """Push a complete message to the provider as device fragments."""
        for fragment in fragmented(message, max_payload=min(227, self._mtu_size - 6)):
            self._callback(None, bytearray(fragment))

    def deliver_raw(self, fragment):
        self._callback(None, bytearray(fragment))


class _ConnectedServices:
    """Service collection that only exists after service discovery."""

    def __init__(self, present: bool) -> None:
        self._present = present

    def get_service(self, uuid):
        if uuid != "0000fee7-0000-1000-8000-00805f9b34fb":
            return None
        return object() if self._present else None


__all__ = [
    "ALLOWED_REQUESTS",
    "EXPECTED_VALUES",
    "BleakLikeError",
    "RealisticBleakenector",
    "TEST_ADDRESS",
    "TEST_DEVICE_ID",
    "TEST_DTU_ID",
    "TEST_KEY",
    "TEST_PROVIDER_ID",
    "TEST_WORDS",
    "decrypt_request_envelope",
    "encrypt_envelope",
    "fragmented",
    "response_envelope",
    "response_for",
    "words_with",
]