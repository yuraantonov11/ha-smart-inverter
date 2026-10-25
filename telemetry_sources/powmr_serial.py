"""Read-only candidate PowMr HVM 4501-register serial profile.

This profile is based on public reverse-engineered references for the PowMr
HVM family. The POW-HVM6.2M-48V-N is listed as compatible but is not reported
as directly tested by those references, so this module is not production-ready
until a real frame from the target inverter confirms the mapping and byte order.
"""

from __future__ import annotations

from datetime import datetime

from .models import FieldObservation, SourceFrame, SourceIdentity, SourceKind, ValueQuality


SLAVE_ADDRESS = 5
FUNCTION_READ_HOLDING = 3
START_REGISTER = 4501
REGISTER_COUNT = 45
PROFILE_ID = "powmr-hvm-4501x45-little-endian-candidate-v1"


class PowMrSerialFrameError(ValueError):
    """Malformed or uncorrelated Modbus response."""


def modbus_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _check_crc(frame: bytes, label: str) -> None:
    if len(frame) < 4:
        raise PowMrSerialFrameError(f"{label} frame is too short")
    if frame[-2:] != modbus_crc16(frame[:-2]).to_bytes(2, "little"):
        raise PowMrSerialFrameError(f"{label} CRC mismatch")


def build_read_request() -> bytes:
    """Build only the documented read request; this module has no write API."""
    body = (
        bytes((SLAVE_ADDRESS, FUNCTION_READ_HOLDING))
        + START_REGISTER.to_bytes(2, "big")
        + REGISTER_COUNT.to_bytes(2, "big")
    )
    return body + modbus_crc16(body).to_bytes(2, "little")


def decode_read_response(
    request: bytes,
    response: bytes,
    *,
    identity: SourceIdentity,
    received_at: datetime,
) -> SourceFrame:
    """Decode a response only when the caller supplies its matching request."""
    if identity.kind is not SourceKind.SERIAL:
        raise PowMrSerialFrameError("source identity kind must be serial")
    expected_request = build_read_request()
    if request != expected_request:
        raise PowMrSerialFrameError("request does not match the candidate 4501/45 profile")
    _check_crc(request, "request")
    _check_crc(response, "response")
    if (
        len(response) != 95
        or response[0] != SLAVE_ADDRESS
        or response[1] != FUNCTION_READ_HOLDING
        or response[2] != REGISTER_COUNT * 2
    ):
        raise PowMrSerialFrameError("response address, function, or word count mismatch")

    # Public PowMr register references describe byte-swapped 16-bit values.
    words = [
        int.from_bytes(response[3 + index * 2:5 + index * 2], "little")
        for index in range(REGISTER_COUNT)
    ]
    # Offsets are relative to register 4501. Actual target-unit verification is
    # required before this candidate profile can feed production entities.
    fields_raw: dict[str, tuple[int, str, float, float]] = {
        "grid_voltage": (1, "V", 0.0, 300.0),
        "pv_voltage": (3, "V", 0.0, 1000.0),
        "pv_power": (4, "W", 0.0, 50000.0),
        "battery_voltage": (5, "V", 0.0, 100.0),
        "battery_soc": (6, "%", 0.0, 100.0),
        "battery_charge_current": (7, "A", 0.0, 1000.0),
        "battery_discharge_current": (8, "A", 0.0, 1000.0),
        "load_power": (11, "W", 0.0, 50000.0),
    }
    fields = {}
    for name, (offset, unit, minimum, maximum) in fields_raw.items():
        value = words[offset]
        if name in {"grid_voltage", "pv_voltage", "battery_voltage"}:
            value *= 0.1
        valid = minimum <= value <= maximum
        fields[name] = FieldObservation(
            value=float(value) if valid else None,
            unit=unit,
            received_at=received_at,
            measured_at=None,
            quality=ValueQuality.MEASURED if valid else ValueQuality.UNKNOWN,
        )
    return SourceFrame(
        identity=identity,
        received_at=received_at,
        fields=fields,
        profile_id=PROFILE_ID,
    )
