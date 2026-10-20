"""Guard the in-memory capture parser against TCP segmentation and leakage."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from telemetry_sources.rwb1 import modbus_crc16


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inspect_rwb1_mqtt_capture.py"
SPEC = importlib.util.spec_from_file_location("rwb1_capture_sanitizer", SCRIPT)
SANITIZER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(SANITIZER)


def make_frame() -> bytes:
    words = [0] * 21
    words[1] = 2343
    words[4] = 15
    words[5] = 532
    words[6] = 90
    words[12] = 982
    data = b"".join(word.to_bytes(2, "little") for word in words)
    body = bytes((5, 3, len(data))) + data
    return body + modbus_crc16(body).to_bytes(2, "little")


def mqtt_publish(topic: str, payload: bytes) -> bytes:
    body = len(topic).to_bytes(2, "big") + topic.encode() + payload
    remaining = len(body)
    encoded = bytearray()
    while True:
        digit = remaining % 128
        remaining //= 128
        encoded.append(digit | (0x80 if remaining else 0))
        if not remaining:
            break
    return bytes((0x30,)) + bytes(encoded) + body


class CaptureSanitizerTests(unittest.TestCase):
    def test_read_requests_report_register_and_count_without_source_values(self):
        body = bytes.fromhex("050311950001")
        request = body + modbus_crc16(body).to_bytes(2, "little")
        payload = json.dumps({"b": {"rs": base64.b64encode(request).decode()}}).encode()
        self.assertEqual(SANITIZER.find_read_requests(payload), [(4501, 1)])
        self.assertEqual(SANITIZER.find_frames(payload), [])
        message = SANITIZER.mqtt_packets(mqtt_publish("private/topic", payload))[0]
        self.assertIn("read_requests=[(4501, 1)]", message[3])
        self.assertNotIn("private/topic", message[3])
        corrupt = request[:-1] + bytes((request[-1] ^ 1,))
        self.assertEqual(SANITIZER.find_read_requests(corrupt), [])

    def test_short_base64_rpc_reply_is_detected_without_assigning_units(self):
        # Synthetic raw value; the response carries no starting register.
        body = bytes((5, 3, 2, 0xB8, 0x01))
        frame = body + modbus_crc16(body).to_bytes(2, "little")
        payload = b"\x00" + json.dumps({"b": {"co": base64.b64encode(frame).decode()}}).encode()
        messages = SANITIZER.mqtt_packets(mqtt_publish("private/topic", payload))
        self.assertEqual(messages[0][2], [frame])
        summary = SANITIZER.candidate_summary(frame)
        self.assertIn("units=unknown", summary)
        self.assertNotIn("pv_W", summary)
        self.assertNotIn("load_W", summary)
        self.assertNotIn(str(0xB801), summary)

    def test_short_frame_bad_crc_and_invalid_byte_count_are_rejected(self):
        body = bytes((5, 3, 2, 0, 50))
        frame = body + modbus_crc16(body).to_bytes(2, "little")
        self.assertEqual(SANITIZER.find_frames(frame), [frame])
        corrupt = frame[:-1] + bytes((frame[-1] ^ 1,))
        self.assertEqual(SANITIZER.find_frames(corrupt), [])
        for count in (0, 1, 3, 252):
            invalid = bytes((5, 3, count)) + bytes(count)
            invalid += modbus_crc16(invalid).to_bytes(2, "little")
            self.assertEqual(SANITIZER.find_frames(invalid), [])

    def test_short_hex_rpc_reply_is_detected(self):
        body = bytes((5, 3, 2, 0, 50))
        frame = body + modbus_crc16(body).to_bytes(2, "little")
        self.assertEqual(SANITIZER.find_frames(json.dumps({"co": frame.hex()}).encode()), [frame])

    def test_fragmented_mqtt_packet_reassembles_and_extracts_frame(self):
        frame = make_frame()
        payload = b"\x00" + json.dumps({"data": base64.b64encode(frame).decode()}).encode()
        packet = mqtt_publish("private/topic/that/must/not/escape", payload)
        split = 23
        chunks = [
            (1000 + split, packet[split:], 20.0),
            (1000, packet[:split], 10.0),
        ]
        stream, byte_times = SANITIZER.reassemble_with_timestamps(chunks)
        messages = SANITIZER.mqtt_packets(stream)
        self.assertEqual(len(messages), 1)
        kind, payload_size, frames, shape, start, end = messages[0]
        self.assertEqual(kind, 3)
        self.assertEqual(payload_size, len(payload))
        self.assertEqual(frames, [frame])
        self.assertEqual(byte_times[end - 1], 20.0)
        self.assertNotIn("private/topic", repr(messages))
        self.assertNotIn(frame.hex(), repr(messages))
        self.assertIn("data", shape)

    def test_candidate_summary_never_prints_raw_frame(self):
        frame = make_frame()
        summary = SANITIZER.candidate_summary(frame)
        self.assertIn("little=", summary)
        self.assertNotIn(frame.hex(), summary)
        self.assertEqual(SANITIZER.find_frames(frame), [frame])

    def test_json_shape_reports_types_without_values(self):
        payload = b"\x00" + json.dumps({
            "code": 3,
            "device": "private-device-id",
            "reading": [1, 2],
            "nested": {"value": "secret"},
        }).encode()
        shape = SANITIZER.json_shape(payload)
        self.assertIn("code': 'number'", shape)
        self.assertIn("device': 'string(len=17)'", shape)
        self.assertIn("reading': 'array(len=2)'", shape)
        self.assertIn("nested': 'object(keys=1)'", shape)
        self.assertNotIn("private-device-id", shape)
        self.assertNotIn("secret", shape)


if __name__ == "__main__":
    unittest.main(verbosity=2)
