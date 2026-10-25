"""Strict experimental decoder for the observed RWB1/Modbus profile.

The device does not include the starting register in a Modbus read response,
so an active read requires the matching request. A separate passive decoder is
available for the one historically corroborated 21-word response shape, but it
must not be used to attribute arbitrary unsolicited frames. This module only
decodes data; it opens no sockets and sends no inverter commands.
"""

from __future__ import annotations

from datetime import datetime

from .models import FieldObservation, SourceFrame, SourceIdentity, SourceKind, ValueQuality


class Rwb1FrameError(ValueError):
    """Malformed, unexpected, or uncorrelated RWB1 Modbus frame."""


def modbus_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


def _check_crc(frame: bytes, label: str) -> None:
    if len(frame) < 4:
        raise Rwb1FrameError(f"{label} frame is too short")
    expected = modbus_crc16(frame[:-2]).to_bytes(2, "little")
    if frame[-2:] != expected:
        raise Rwb1FrameError(f"{label} CRC mismatch")


def build_read_request(*, address: int = 5, register: int = 0x1195, count: int = 21) -> bytes:
    """Build the experimental read request; caller chooses when/if to send it."""
    if not (0 <= address <= 247 and 0 <= register <= 0xFFFF and 1 <= count <= 125):
        raise ValueError("invalid Modbus read parameters")
    body = bytes((address, 3)) + register.to_bytes(2, "big") + count.to_bytes(2, "big")
    return body + modbus_crc16(body).to_bytes(2, "little")


def decode_correlated_response(
    request: bytes,
    response: bytes,
    *,
    identity: SourceIdentity,
    received_at: datetime,
    profile_id: str = "rwb1-04-pow-hvm6.2m-48v-n-experimental-v1",
) -> SourceFrame:
    """Decode only a CRC-valid response matching the expected read request."""
    if len(request) != 8:
        raise Rwb1FrameError("request must be one complete 8-byte Modbus read frame")
    _check_crc(request, "request")
    address, function = request[0], request[1]
    register = int.from_bytes(request[2:4], "big")
    count = int.from_bytes(request[4:6], "big")
    if function != 3 or address != 5 or register != 0x1195 or count != 21:
        raise Rwb1FrameError("request does not match the experimental RWB1 profile")
    if identity.kind not in (SourceKind.GATEWAY, SourceKind.LAN):
        raise Rwb1FrameError("source identity kind must be gateway or lan")

    return _decode_21_word_response(
        response,
        identity=identity,
        received_at=received_at,
        profile_id=profile_id,
        expected_address=address,
        expected_function=function,
    )


def decode_passive_observed_response(
    response: bytes,
    *,
    identity: SourceIdentity,
    received_at: datetime,
) -> SourceFrame:
    """Decode the passively observed RWB1 21-word shape as experimental.

    The frame is CRC/shape checked and uses the field map corroborated against
    five HA history values on 2026-10-19. Because the response omits its start
    register, this does not establish request correlation or authorize polling.
    """
    if identity.kind not in (SourceKind.GATEWAY, SourceKind.LAN):
        raise Rwb1FrameError("source identity kind must be gateway or lan")
    return _decode_21_word_response(
        response,
        identity=identity,
        received_at=received_at,
        profile_id="rwb1-04-pow-hvm6.2m-48v-n-passive-candidate-v1",
        expected_address=5,
        expected_function=3,
    )


def _decode_21_word_response(
    response: bytes,
    *,
    identity: SourceIdentity,
    received_at: datetime,
    profile_id: str,
    expected_address: int,
    expected_function: int,
) -> SourceFrame:
    _check_crc(response, "response")
    if len(response) != 47:
        raise Rwb1FrameError("response must contain exactly 21 register words")
    if response[0] != expected_address or response[1] != expected_function:
        raise Rwb1FrameError("response address/function does not match profile")
    if response[2] != 42:
        raise Rwb1FrameError("response byte count does not match requested register count")

    # The passively observed RWB1 payload stores each Modbus word byte-swapped.
    words = [int.from_bytes(response[3 + i * 2:5 + i * 2], "little") for i in range(21)]
    grid_voltage = words[1] / 10.0
    pv_power = float(words[4])
    battery_voltage = words[5] / 10.0
    battery_soc = float(words[6])
    load_power = float(words[12])
    values: dict[str, tuple[float | None, str]] = {
        "grid_voltage": (grid_voltage if 0.0 <= grid_voltage <= 300.0 else None, "V"),
        "pv_power": (pv_power if 0.0 <= pv_power <= 50_000.0 else None, "W"),
        "battery_voltage": (battery_voltage if 0.0 <= battery_voltage <= 100.0 else None, "V"),
        "battery_soc": (battery_soc if 0.0 <= battery_soc <= 100.0 else None, "%"),
        "load_power": (load_power if 0.0 <= load_power <= 50_000.0 else None, "W"),
    }
    fields = {
        name: FieldObservation(
            value=value,
            unit=unit,
            received_at=received_at,
            measured_at=None,
            quality=ValueQuality.MEASURED if value is not None else ValueQuality.UNKNOWN,
        )
        for name, (value, unit) in values.items()
    }
    return SourceFrame(
        identity=identity,
        received_at=received_at,
        fields=fields,
        profile_id=profile_id,
    )
