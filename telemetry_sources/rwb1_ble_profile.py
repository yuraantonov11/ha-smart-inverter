"""Versioned, read-only BLE register profile for the PowMr RWB1.

The wire facts encoded here (allow-listed read, 21-word response shape,
byte order) were established on the user's own hardware against the
public reference implementation:

    Jpedrotas/easun-rwb1-esphome — MIT License,
    Copyright (c) 2026 EASUN RWB1 ESPHome contributors.
    https://github.com/Jpedrotas/easun-rwb1-esphome
    (see tools/rwb1_ble_protocol.py, tools/ble_modbus_read.py,
    COMPATIBILITY.md)

This module is a *separate, explicitly versioned* profile. It does not change,
wrap or override the semantics of :mod:`telemetry_sources.rwb1`; the passive
gateway/LAN candidate decoder there keeps its own profile IDs. Any future
correction to this mapping must ship as a new ``PROFILE_ID`` so that recorded
frames stay interpretable.

Wire facts used here (all read-only, all allow-listed):

* One single Modbus read is permitted: ``05 03 11 95 00 15 90 91``
  (slave 5, function 03, start register 0x1195, 21 registers).
* The response is exactly 47 bytes: ``05 03 2A <42 data bytes> <crc16-lo> <crc16-hi>``.
* ``CRC`` is Modbus CRC-16 (polynomial 0xA001, init 0xFFFF), transmitted low byte first.

**Byte order.** Every 16-bit register word is read LITTLE-endian:
``int.from_bytes(response[3 + i * 2 : 5 + i * 2], "little")``. This is the
order already used by ``telemetry_sources.rwb1`` and it reproduces plausible
values; the big-endian reading yields absurd magnitudes. Word index therefore
counts Modbus words, not bytes.

Field map (``word index`` -> meaning), evidence level per field is recorded in
:data:`FIELD_MAP` and is deliberately *not* uniform:

===========  =========================  ======  ===============================
Word index   Field                      Scale   Evidence
===========  =========================  ======  ===============================
1            grid_voltage               /10     corroborated against cloud
6            battery_soc                x1      two BLE reads only, unconfirmed
4            pv_power                   x1      two BLE reads only, unconfirmed
5            battery_voltage            /10     corroborated against cloud
12           load_power                 x1      two BLE reads only, unconfirmed
===========  =========================  ======  ===============================

Known gaps, kept explicit rather than guessed:

* ``battery current`` is **not** mapped. No BLE capture corroborated it.
* ``measured_at`` is always ``None``: the inverter does not transmit a
  measurement timestamp, only the arrival time of the frame.
* SOC and load disagree with the cloud API (100 % vs 95 %, 301 W vs 398 W) and
  are diagnostic candidates, not validated telemetry. See
  ``docs/rwb1-ble-experimental.md``.

The DTU identifier and derived AES key are deliberately absent from this
module: they belong to the codec, are held in memory only and are never
logged or persisted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from .models import FieldObservation, SourceFrame, SourceIdentity, SourceKind, ValueQuality


PROFILE_ID = "rwb1-ble-fee7-21w-le-v1"
"""Stable identifier stored on every frame decoded with this map."""

WORD_ORDER = "little"
"""Byte order of each 16-bit Modbus word inside the response payload."""

SLAVE_ADDRESS = 5
FUNCTION_READ_HOLDING = 3
START_REGISTER = 0x1195
REGISTER_COUNT = 21
RESPONSE_LENGTH = 3 + REGISTER_COUNT * 2 + 2
RESPONSE_BYTE_COUNT = REGISTER_COUNT * 2

ALLOWED_REQUESTS: frozenset[bytes] = frozenset({bytes.fromhex("05 03 11 95 00 15 90 91")})
"""The complete allow-list: one read frame, and nothing else.

No control command, no configuration write, no pairing, no register sweep is
implemented anywhere in this package.
"""


class Rwb1BleError(Exception):
    """Base class for every RWB1 BLE failure raised by this package."""


class Rwb1BleProfileError(Rwb1BleError):
    """Frame outside the allow-list, or malformed against this profile."""


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One decoded field: where it comes from and what "sane" means."""

    name: str
    word_index: int
    unit: str
    scale: float
    minimum: float
    maximum: float
    evidence: str


FIELD_MAP: tuple[FieldSpec, ...] = (
    FieldSpec("grid_voltage", 1, "V", 0.1, 0.0, 300.0, "corroborated-against-cloud"),
    FieldSpec("pv_power", 4, "W", 1.0, 0.0, 50_000.0, "two-ble-reads-unconfirmed"),
    FieldSpec("battery_voltage", 5, "V", 0.1, 0.0, 100.0, "corroborated-against-cloud"),
    FieldSpec("battery_soc", 6, "%", 1.0, 0.0, 100.0, "two-ble-reads-unconfirmed"),
    FieldSpec("load_power", 12, "W", 1.0, 0.0, 50_000.0, "two-ble-reads-unconfirmed"),
)

FIELD_BY_NAME = {spec.name: spec for spec in FIELD_MAP}

UNMAPPED_FIELDS: tuple[str, ...] = ("battery_current",)
"""Fields the captures never corroborated; left unmapped on purpose."""


def modbus_crc16(data: bytes) -> int:
    """Modbus CRC-16 (reflected 0xA001, init 0xFFFF) over ``data``."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def check_crc(frame: bytes, label: str) -> None:
    """Raise unless ``frame`` ends with its own Modbus CRC, low byte first."""
    if len(frame) < 4:
        raise Rwb1BleProfileError(f"{label} frame is too short")
    if frame[-2:] != modbus_crc16(frame[:-2]).to_bytes(2, "little"):
        raise Rwb1BleProfileError(f"{label} CRC mismatch")


def build_allowlisted_request() -> bytes:
    """Return the one permitted Modbus read frame.

    There are no parameters: the register, count, function and slave address
    are compile-time constants precisely so that no caller can widen the
    allow-list by accident.
    """
    body = (
        bytes((SLAVE_ADDRESS, FUNCTION_READ_HOLDING))
        + START_REGISTER.to_bytes(2, "big")
        + REGISTER_COUNT.to_bytes(2, "big")
    )
    frame = body + modbus_crc16(body).to_bytes(2, "little")
    if frame not in ALLOWED_REQUESTS:  # pragma: no cover - guards constant drift.
        raise Rwb1BleProfileError("compiled request left the allow-list")
    return frame


def validate_allowed_request(request: bytes) -> None:
    """Accept only an exact byte-for-byte match of the single allowed read.

    Register, count, function code and slave address are all covered: any
    other frame, however well formed or CRC-valid, is rejected.
    """
    if not isinstance(request, (bytes, bytearray, memoryview)):
        raise Rwb1BleProfileError("request must be a bytes-like object")
    frame = bytes(request)
    if frame not in ALLOWED_REQUESTS:
        raise Rwb1BleProfileError("request is not on the RWB1 BLE allow-list")


def parse_allowed_request(request: bytes) -> tuple[int, int, int, int]:
    """Return ``(slave, function, register, count)`` after allow-list check."""
    validate_allowed_request(request)
    return (
        request[0],
        request[1],
        int.from_bytes(request[2:4], "big"),
        int.from_bytes(request[4:6], "big"),
    )


def words_from_response(response: bytes) -> tuple[int, ...]:
    """Split a validated 21-word response into little-endian integer words."""
    if len(response) != RESPONSE_LENGTH:
        raise Rwb1BleProfileError("response must contain exactly 21 register words")
    return tuple(
        int.from_bytes(response[3 + index * 2 : 5 + index * 2], WORD_ORDER)
        for index in range(REGISTER_COUNT)
    )


def validate_allowed_response(response: bytes) -> tuple[int, ...]:
    """Validate shape, CRC and correlation of the response, return its words.

    Checks length (47), CRC, slave address, function code and byte count (42)
    against the single allow-listed request.
    """
    if not isinstance(response, (bytes, bytearray, memoryview)):
        raise Rwb1BleProfileError("response must be a bytes-like object")
    frame = bytes(response)
    check_crc(frame, "response")
    if len(frame) != RESPONSE_LENGTH:
        raise Rwb1BleProfileError("response must contain exactly 21 register words")
    if frame[0] != SLAVE_ADDRESS or frame[1] != FUNCTION_READ_HOLDING:
        raise Rwb1BleProfileError("response slave/function does not match the profile")
    if frame[2] != RESPONSE_BYTE_COUNT:
        raise Rwb1BleProfileError("response byte count does not match the register count")
    return words_from_response(frame)


def decode_words(
    words: Sequence[int],
    *,
    identity: SourceIdentity,
    received_at: datetime,
    profile_id: str = PROFILE_ID,
) -> SourceFrame:
    """Map register words onto observations without inventing measurements.

    A value outside its physical range becomes ``None`` with
    ``ValueQuality.UNKNOWN``; it never becomes ``0``. ``measured_at`` is
    always ``None`` because the device transmits no measurement timestamp.
    """
    if len(words) != REGISTER_COUNT:
        raise Rwb1BleProfileError("expected exactly 21 register words")
    fields = {}
    for spec in FIELD_MAP:
        raw = words[spec.word_index]
        value = raw * spec.scale
        usable = spec.minimum <= value <= spec.maximum
        fields[spec.name] = FieldObservation(
            value=value if usable else None,
            unit=spec.unit,
            received_at=received_at,
            measured_at=None,
            quality=ValueQuality.MEASURED if usable else ValueQuality.UNKNOWN,
        )
    return SourceFrame(
        identity=identity,
        received_at=received_at,
        fields=fields,
        profile_id=profile_id,
    )


def decode_read_response(
    response: bytes,
    *,
    identity: SourceIdentity,
    received_at: datetime,
    profile_id: str = PROFILE_ID,
) -> SourceFrame:
    """Validate the single allow-listed response and decode it."""
    if identity.kind is not SourceKind.BLE:
        raise Rwb1BleProfileError("source identity kind must be ble")
    return decode_words(
        validate_allowed_response(response),
        identity=identity,
        received_at=received_at,
        profile_id=profile_id,
    )


def expected_fields() -> Iterable[str]:
    """Names this profile can produce, in stable order."""
    return tuple(spec.name for spec in FIELD_MAP)