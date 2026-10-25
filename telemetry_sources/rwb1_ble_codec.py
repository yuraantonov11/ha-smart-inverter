"""AES envelope and fragmentation codec for the RWB1 BLE link.

Read-only by construction: this module can build exactly one Modbus read
request (:func:`telemetry_sources.rwb1_ble_profile.build_allowlisted_request`)
and can never emit a control or configuration command.

Protocol recap (verified on hardware 2026-10-20, 2 successful reads):

* Key: ``MD5((DTU_ID.strip() + "SEC_").encode()).digest()`` -> 16 bytes.
* Cipher: AES-128-CBC with the IV equal to the key.
* Payload: compact JSON (``separators=(",", ":")``), zero padded to a multiple
  of 16 bytes, then base64.
* Request envelope: ``{"CID": 30024, "PL": {"Req": <hex>, "Uart": {...},
  "CmdType": "gatherSingleDevProps", "CmdNo": <4 alnum>}}``.
* Transport framing: a private three byte header ``index`` (1-based),
  ``total``, ``length``, followed by the payload chunk.

Padding note: the reference implementation always appends at least one zero
byte, even when the JSON already is a multiple of 16, and the receiver strips
trailing zeros. That behaviour is reproduced here verbatim for wire
compatibility rather than "fixed" to PKCS#7.

Secret handling: the DTU identifier is used to derive the key, held in memory
only, and never returned in ``repr``, logged or written to disk. Only the
derived key object travels between these functions.

MIT-licensed protocol reference used to cross-check this implementation:
https://github.com/Jpedrotas/easun-rwb1-esphome (Copyright (c) 2026 EASUN RWB1
ESPHome contributors) - ``tools/rwb1_ble_protocol.py`` and
``tools/ble_modbus_read.py``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import string
from dataclasses import dataclass
from typing import Mapping

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .rwb1_ble_profile import Rwb1BleError, Rwb1BleProfileError


CID_REQUEST = 30024
CID_RESPONSE = 30025
RC_OK = 0

KEY_BYTES = 16
BLOCK_BYTES = 16
MAX_FRAGMENT_PAYLOAD = 227
MAX_FRAGMENT_COUNT = 255
MTU_OVERHEAD = 6

UART_PROFILE: Mapping[str, int | str] = {
    "BaudRate": 2400,
    "DataBit": 8,
    "ParityBit": "NONE",
    "StopBit": 1,
}
CMD_TYPE = "gatherSingleDevProps"
CMD_NO_LENGTH = 4
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


class Rwb1BleCodecError(Rwb1BleError):
    """Envelope, cipher or framing failure on the RWB1 BLE link."""


class Rwb1BleFragmentError(Rwb1BleCodecError):
    """A received fragment violates the three byte framing contract."""


def derive_key(dtu_id: str) -> bytes:
    """Derive the 16-byte application key from the DTU identifier.

    The identifier itself is not retained, not logged and not returned.
    """
    if not isinstance(dtu_id, str) or not dtu_id.strip():
        raise Rwb1BleCodecError("a non-empty DTU identifier is required")
    return hashlib.md5((dtu_id.strip() + "SEC_").encode("utf-8")).digest()


def new_command_no() -> str:
    """Four random alphanumeric correlation characters."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(CMD_NO_LENGTH))


def _require_key(key: bytes) -> bytes:
    if not isinstance(key, (bytes, bytearray, memoryview)) or len(key) != KEY_BYTES:
        raise Rwb1BleCodecError("key must be exactly 16 bytes")
    return bytes(key)


def max_fragment_payload(mtu_size: int | None) -> int:
    """Usable payload per fragment for a negotiated MTU (never above 227)."""
    if mtu_size is None:
        return MAX_FRAGMENT_PAYLOAD
    if mtu_size <= MTU_OVERHEAD:
        raise Rwb1BleCodecError("negotiated MTU leaves no room for a payload")
    return max(1, min(MAX_FRAGMENT_PAYLOAD, int(mtu_size) - MTU_OVERHEAD))


def build_envelope(modbus_frame: bytes, *, cmd_no: str | None = None) -> dict:
    """Wrap the allow-listed read frame in the vendor request envelope."""
    from .rwb1_ble_profile import validate_allowed_request

    validate_allowed_request(modbus_frame)
    command = new_command_no() if cmd_no is None else cmd_no
    if (
        not isinstance(command, str)
        or not command
        or len(command) > 16
        or not all(char in string.ascii_letters + string.digits for char in command)
    ):
        raise Rwb1BleCodecError("CmdNo must be short alphanumeric ASCII")
    return {
        "CID": CID_REQUEST,
        "PL": {
            "Req": bytes(modbus_frame).hex().upper(),
            "Uart": dict(UART_PROFILE),
            "CmdType": CMD_TYPE,
            "CmdNo": command,
        },
    }


def encrypt_request(
    modbus_frame: bytes,
    key: bytes,
    *,
    cmd_no: str | None = None,
) -> tuple[bytes, str]:
    """Encrypt one allow-listed read; return ``(base64 payload, CmdNo)``.

    The returned ``CmdNo`` is the correlation token to expect in the response.
    """
    secret = _require_key(key)
    envelope = build_envelope(modbus_frame, cmd_no=cmd_no)
    plaintext = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    plaintext += b"\0" * (BLOCK_BYTES - len(plaintext) % BLOCK_BYTES)
    encryptor = Cipher(algorithms.AES(secret), modes.CBC(secret)).encryptor()
    return base64.b64encode(encryptor.update(plaintext) + encryptor.finalize()), envelope["PL"]["CmdNo"]


def fragment_payload(payload: bytes, *, max_payload: int = MAX_FRAGMENT_PAYLOAD) -> list[bytes]:
    """Split ``payload`` into framed chunks of at most ``max_payload`` bytes."""
    if not payload:
        raise Rwb1BleCodecError("cannot fragment an empty payload")
    if not 1 <= max_payload <= MAX_FRAGMENT_PAYLOAD:
        raise Rwb1BleCodecError("fragment payload size out of range")
    if len(payload) > max_payload * MAX_FRAGMENT_COUNT:
        raise Rwb1BleCodecError("payload is too large to fragment")
    chunks = [
        payload[offset : offset + max_payload] for offset in range(0, len(payload), max_payload)
    ]
    total = len(chunks)
    return [bytes((index, total, len(chunk))) + chunk for index, chunk in enumerate(chunks, 1)]


@dataclass(frozen=True, slots=True)
class FragmentState:
    """Immutable reassembly progress after feeding one fragment."""

    complete: bytes | None
    received: int
    total: int


class FragmentReassembler:
    """Strict reassembly of a three byte framed RWB1 BLE message.

    Unlike a permissive parser, every contract violation raises
    :class:`Rwb1BleFragmentError`: zero/oversized indices, a length field that
    disagrees with the chunk, an oversize chunk, a total beyond the addressable
    range, and a repeated index carrying a *different* payload - index 1
    included. An identical repeat is accepted as a retransmission and keeps the
    parts already collected. A genuinely new message must be announced through
    :meth:`reset` (or a fresh reassembler, as the transport does per
    transaction). Incomplete messages simply report ``complete=None`` until the
    deadline expires.
    """

    def __init__(self, *, max_payload: int = MAX_FRAGMENT_PAYLOAD) -> None:
        if not 1 <= max_payload <= MAX_FRAGMENT_PAYLOAD:
            raise Rwb1BleFragmentError("fragment payload size out of range")
        self._max_payload = max_payload
        self._chunks: dict[int, bytes] = {}
        self._total: int | None = None

    @property
    def total(self) -> int | None:
        return self._total

    @property
    def pending(self) -> int:
        if self._total is None:
            return 0
        return self._total - len(self._chunks)

    def reset(self) -> None:
        self._chunks.clear()
        self._total = None

    def feed(self, fragment: bytes) -> FragmentState:
        """Consume one fragment; return progress, raising on any violation."""
        if not isinstance(fragment, (bytes, bytearray, memoryview)):
            raise Rwb1BleFragmentError("fragment must be a bytes-like object")
        raw = bytes(fragment)
        if len(raw) < 3:
            raise Rwb1BleFragmentError("fragment is shorter than its header")
        index, total, length = raw[0], raw[1], raw[2]
        data = raw[3:]
        if index == 0:
            raise Rwb1BleFragmentError("fragment index is zero-based")
        if total == 0:
            raise Rwb1BleFragmentError("fragment total is zero")
        if index > total:
            raise Rwb1BleFragmentError("fragment index exceeds the announced total")
        if total > MAX_FRAGMENT_COUNT:
            raise Rwb1BleFragmentError("fragment total exceeds the addressable range")
        if length != len(data):
            raise Rwb1BleFragmentError("fragment length field disagrees with the chunk")
        if length > self._max_payload:
            raise Rwb1BleFragmentError("fragment payload exceeds the negotiated size")
        previous = self._chunks.get(index)
        if previous is not None:
            # Duplicate index. Validate *before* touching any state: a
            # conflicting payload is corruption, and an identical payload is a
            # retransmission that must leave everything already collected
            # untouched (including for index 1, the head of the message).
            if previous != data:
                raise Rwb1BleFragmentError("conflicting duplicate fragment index")
            if total != self._total:
                raise Rwb1BleFragmentError("fragment total changed mid-message")
        elif index == 1:
            # A head that was never seen starts the message: drop any partial
            # state from an earlier message and re-bind the announced total, so
            # a different total is legitimate. Out-of-order arrival (parts
            # already held for this same total) must *not* discard them.
            if self._chunks and total != self._total:
                self._chunks.clear()
            self._total = total
        elif self._total is None:
            self._total = total
        elif total != self._total:
            raise Rwb1BleFragmentError("fragment total changed mid-message")
        self._chunks[index] = data
        if len(self._chunks) != total or any(part not in self._chunks for part in range(1, total + 1)):
            return FragmentState(complete=None, received=len(self._chunks), total=total)
        complete = b"".join(self._chunks[part] for part in range(1, total + 1))
        self._chunks.clear()
        self._total = None
        return FragmentState(complete=complete, received=total, total=total)


@dataclass(frozen=True, slots=True)
class DecodedResponse:
    """A verified device response envelope."""

    modbus: bytes
    cmd_no: str | None
    cmd_no_present: bool


def _parse_hex_field(value: object, label: str) -> bytes:
    if not isinstance(value, str):
        raise Rwb1BleCodecError(f"{label} must be a hex string")
    compact = value.replace(" ", "")
    if not compact:
        raise Rwb1BleCodecError(f"{label} is empty")
    if len(compact) % 2:
        raise Rwb1BleCodecError(f"{label} has an odd number of hex digits")
    if not all(char in _HEX_DIGITS for char in compact):
        raise Rwb1BleCodecError(f"{label} contains non-hexadecimal characters")
    return bytes.fromhex(compact)


def decrypt_response(
    message: bytes,
    key: bytes,
    *,
    expected_cmd_no: str | None = None,
) -> DecodedResponse:
    """Base64 -> AES-CBC -> JSON -> Modbus payload, with strict validation.

    ``expected_cmd_no`` is enforced only when the device actually echoed a
    ``CmdNo``. A response without ``CmdNo`` is accepted but reported through
    :attr:`DecodedResponse.cmd_no_present` so callers can record the gap
    instead of silently assuming correlation.
    """
    secret = _require_key(key)
    if not isinstance(message, (bytes, bytearray, memoryview, str)):
        raise Rwb1BleCodecError("response must be base64 text or bytes")
    try:
        ciphertext = base64.b64decode(message, validate=True)
    except (binascii.Error, ValueError) as err:
        raise Rwb1BleCodecError("response is not valid base64") from err
    if not ciphertext:
        raise Rwb1BleCodecError("response payload is empty")
    if len(ciphertext) % BLOCK_BYTES:
        raise Rwb1BleCodecError("response payload is not a multiple of 16 bytes")
    decryptor = Cipher(algorithms.AES(secret), modes.CBC(secret)).decryptor()
    plaintext = (decryptor.update(ciphertext) + decryptor.finalize()).rstrip(b"\0")
    try:
        envelope = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise Rwb1BleCodecError("response payload is not the expected JSON") from err
    if not isinstance(envelope, dict):
        raise Rwb1BleCodecError("response envelope must be a JSON object")
    if envelope.get("CID") != CID_RESPONSE:
        raise Rwb1BleCodecError("response CID is not the expected device answer")
    if envelope.get("RC") != RC_OK:
        raise Rwb1BleCodecError("response RC reports a non-zero device status")
    payload = envelope.get("PL")
    if not isinstance(payload, dict) or "Rsp" not in payload:
        raise Rwb1BleCodecError("response payload has no PL.Rsp field")
    modbus = _parse_hex_field(payload["Rsp"], "PL.Rsp")
    if len(modbus) < 5:
        raise Rwb1BleCodecError("Modbus response is too short to be a reply")
    raw_cmd_no = payload.get("CmdNo")
    cmd_no_present = raw_cmd_no is not None
    if cmd_no_present and not isinstance(raw_cmd_no, str):
        raise Rwb1BleCodecError("response CmdNo must be a string")
    if cmd_no_present and expected_cmd_no is not None and raw_cmd_no != expected_cmd_no:
        raise Rwb1BleCodecError("response CmdNo does not match the request")
    return DecodedResponse(modbus=modbus, cmd_no=raw_cmd_no, cmd_no_present=cmd_no_present)


def decrypt_response_to_frame(
    message: bytes,
    key: bytes,
    *,
    identity,
    received_at,
    expected_cmd_no: str | None = None,
):
    """Decrypt, then decode the Modbus payload with the versioned profile."""
    from .rwb1_ble_profile import decode_read_response

    decoded = decrypt_response(message, key, expected_cmd_no=expected_cmd_no)
    return decode_read_response(
        decoded.modbus, identity=identity, received_at=received_at
    )


__all__ = [
    "CID_REQUEST",
    "CID_RESPONSE",
    "DecodedResponse",
    "FragmentReassembler",
    "FragmentState",
    "MAX_FRAGMENT_PAYLOAD",
    "Rwb1BleCodecError",
    "Rwb1BleFragmentError",
    "Rwb1BleProfileError",
    "build_envelope",
    "decrypt_response",
    "decrypt_response_to_frame",
    "derive_key",
    "encrypt_request",
    "fragment_payload",
    "max_fragment_payload",
    "new_command_no",
]