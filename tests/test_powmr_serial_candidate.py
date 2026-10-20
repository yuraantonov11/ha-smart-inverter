import unittest
from datetime import datetime, timezone

from telemetry_sources.models import SourceIdentity, SourceKind, ValueQuality
from telemetry_sources.powmr_serial import (
    PROFILE_ID,
    PowMrSerialFrameError,
    build_read_request,
    decode_read_response,
    modbus_crc16,
)


NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def make_response(words, address=5, function=3):
    data = b"".join(word.to_bytes(2, "little") for word in words)
    body = bytes((address, function, len(data))) + data
    return body + modbus_crc16(body).to_bytes(2, "little")


class PowMrSerialCandidateProfileTests(unittest.TestCase):
    def setUp(self):
        self.identity = SourceIdentity("serial", "inverter-1", SourceKind.SERIAL)
        self.request = build_read_request()

    def decode(self, response, request=None, identity=None):
        return decode_read_response(
            self.request if request is None else request,
            response,
            identity=self.identity if identity is None else identity,
            received_at=NOW,
        )

    def test_read_request_is_fixed_read_only_4501_45(self):
        request = build_read_request()
        self.assertEqual(request[:6], bytes.fromhex("05031195002d"))
        self.assertEqual(len(request), 8)
        self.assertEqual(modbus_crc16(request[:-2]).to_bytes(2, "little"), request[-2:])

    def test_candidate_register_map_and_little_endian_values(self):
        words = [0] * 45
        words[1] = 2300
        words[3] = 401
        words[4] = 1250
        words[5] = 531
        words[6] = 87
        words[7] = 12
        words[8] = 3
        words[11] = 942
        frame = self.decode(make_response(words))
        self.assertEqual(frame.profile_id, PROFILE_ID)
        self.assertEqual(frame.fields["grid_voltage"].value, 230.0)
        self.assertEqual(frame.fields["pv_voltage"].value, 40.1)
        self.assertEqual(frame.fields["pv_power"].value, 1250.0)
        self.assertEqual(frame.fields["battery_voltage"].value, 53.1)
        self.assertEqual(frame.fields["battery_soc"].value, 87.0)
        self.assertEqual(frame.fields["load_power"].value, 942.0)
        self.assertEqual(frame.fields["load_power"].unit, "W")
        self.assertIsNone(frame.fields["load_power"].measured_at)

    def test_zero_is_measured_but_invalid_soc_is_unknown(self):
        words = [0] * 45
        words[6] = 101
        frame = self.decode(make_response(words))
        self.assertEqual(frame.fields["pv_power"].value, 0.0)
        self.assertIsNone(frame.fields["battery_soc"].value)
        self.assertEqual(frame.fields["battery_soc"].quality, ValueQuality.UNKNOWN)

    def test_rejects_corrupt_wrong_size_and_wrong_identity_frames(self):
        words = [0] * 45
        response = make_response(words)
        corrupt = response[:-1] + bytes((response[-1] ^ 1,))
        with self.assertRaisesRegex(PowMrSerialFrameError, "CRC"):
            self.decode(corrupt)
        with self.assertRaises(PowMrSerialFrameError):
            self.decode(response[:-1])
        with self.assertRaises(PowMrSerialFrameError):
            self.decode(make_response(words, address=4))
        other_source = SourceIdentity("gateway", "inverter-1", SourceKind.GATEWAY)
        with self.assertRaises(PowMrSerialFrameError):
            self.decode(response, identity=other_source)

    def test_rejects_different_request_and_other_device(self):
        words = [0] * 45
        bad_request = bytearray(self.request)
        bad_request[5] = 44
        bad_request = bytes(bad_request[:-2]) + modbus_crc16(bytes(bad_request[:-2])).to_bytes(2, "little")
        with self.assertRaises(PowMrSerialFrameError):
            self.decode(make_response(words), request=bad_request)
        other_device = SourceIdentity("serial", "inverter-2", SourceKind.SERIAL)
        frame = self.decode(make_response(words), identity=other_device)
        self.assertEqual(frame.identity.device_id, "inverter-2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
