"""Versioned RWB1 BLE profile: endianness, field map, allow-list, CRC."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from telemetry_sources.models import SourceIdentity, SourceKind, ValueQuality
from telemetry_sources.rwb1_ble_profile import (
    ALLOWED_REQUESTS,
    FIELD_MAP,
    PROFILE_ID,
    REGISTER_COUNT,
    RESPONSE_LENGTH,
    WORD_ORDER,
    Rwb1BleProfileError,
    build_allowlisted_request,
    check_crc,
    decode_read_response,
    decode_words,
    expected_fields,
    modbus_crc16,
    parse_allowed_request,
    validate_allowed_request,
    validate_allowed_response,
    words_from_response,
)

from tests.rwb1_ble_fixtures import (
    EXPECTED_VALUES, TEST_DEVICE_ID, TEST_PROVIDER_ID, TEST_WORDS, response_for, words_with,
)


NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)


def identity(kind=SourceKind.BLE):
    return SourceIdentity(TEST_PROVIDER_ID, TEST_DEVICE_ID, kind)


class ProfileVersionTests(unittest.TestCase):
    def test_profile_id_is_explicit_versioned_and_distinct_from_existing(self):
        self.assertEqual(PROFILE_ID, "rwb1-ble-fee7-21w-le-v1")
        self.assertEqual(WORD_ORDER, "little")
        self.assertNotIn("ble", "")
        existing = (
            "rwb1-04-pow-hvm6.2m-48v-n-experimental-v1",
            "rwb1-04-pow-hvm6.2m-48v-n-passive-candidate-v1",
        )
        for profile in existing:
            self.assertNotEqual(PROFILE_ID, profile)

    def test_field_map_indexes_the_documented_words_with_units(self):
        by_name = {spec.name: spec for spec in FIELD_MAP}
        self.assertEqual(set(by_name), set(expected_fields()))
        self.assertEqual(by_name["grid_voltage"].word_index, 1)
        self.assertEqual(by_name["grid_voltage"].scale, 0.1)
        self.assertEqual(by_name["pv_power"].word_index, 4)
        self.assertEqual(by_name["battery_voltage"].word_index, 5)
        self.assertEqual(by_name["battery_soc"].word_index, 6)
        self.assertEqual(by_name["load_power"].word_index, 12)
        for spec in FIELD_MAP:
            with self.subTest(field=spec.name):
                self.assertLess(spec.word_index, REGISTER_COUNT)
                self.assertTrue(spec.unit)
                self.assertLessEqual(spec.minimum, spec.maximum)
                self.assertTrue(spec.evidence)

    def test_unconfirmed_fields_are_labelled_as_such(self):
        by_name = {spec.name: spec for spec in FIELD_MAP}
        self.assertEqual(by_name["battery_soc"].evidence, "two-ble-reads-unconfirmed")
        self.assertEqual(by_name["load_power"].evidence, "two-ble-reads-unconfirmed")


class EndianRegressionTests(unittest.TestCase):
    """The 21-word block is little-endian; big-endian yields absurd values."""

    def test_words_are_decoded_little_endian(self):
        words = words_from_response(response_for())
        self.assertEqual(words, TEST_WORDS)

    def test_byte_level_little_endian_layout_is_what_decoders_read(self):
        response = response_for()
        for index, expected in enumerate(TEST_WORDS):
            chunk = response[3 + index * 2 : 5 + index * 2]
            with self.subTest(word=index):
                self.assertEqual(chunk, expected.to_bytes(2, "little"))
                self.assertEqual(int.from_bytes(chunk, "little"), expected)

    def test_endianness_regression_known_words(self):
        response = response_for(words_with({1: 2339, 5: 529, 6: 86, 12: 1073}))
        words = words_from_response(response)
        self.assertEqual(words[1], 2339)
        self.assertEqual(words[5], 529)
        self.assertEqual(words[6], 86)
        self.assertEqual(words[12], 1073)

    def test_big_endian_reading_of_the_same_block_would_be_absurd(self):
        """Guard against a silent swap back to big-endian."""
        response = response_for()
        big = [
            int.from_bytes(response[3 + i * 2 : 5 + i * 2], "big") for i in range(21)
        ]
        # Same wire bytes, wrong byte order: every voltage/SOC word explodes.
        self.assertEqual(big[1], 8969)   # was 2339
        self.assertEqual(big[6], 22_016)  # was 86
        self.assertGreater(big[1] * 0.1, 300.0)
        self.assertGreater(big[6], 100.0)
        frame = decode_words(tuple(big), identity=identity(), received_at=NOW)
        for name in ("grid_voltage", "battery_voltage", "battery_soc"):
            with self.subTest(field=name):
                self.assertIsNone(frame.fields[name].value)
                self.assertEqual(frame.fields[name].quality, ValueQuality.UNKNOWN)
        # load_power stays in range but is ~12x off, which is the other tell.
        self.assertGreater(frame.fields["load_power"].value, 10_000.0)

    def test_profile_decode_matches_expected_values_and_units(self):
        frame = decode_read_response(
            response_for(), identity=identity(), received_at=NOW,
        )
        self.assertEqual(frame.profile_id, PROFILE_ID)
        for name, expected in EXPECTED_VALUES.items():
            with self.subTest(field=name):
                self.assertAlmostEqual(frame.fields[name].value, expected, places=6)

    def test_received_and_measured_times_are_separate(self):
        frame = decode_read_response(
            response_for(), identity=identity(), received_at=NOW,
        )
        self.assertEqual(frame.received_at, NOW)
        for name, observation in frame.fields.items():
            with self.subTest(field=name):
                self.assertEqual(observation.received_at, NOW)
                self.assertIsNone(observation.measured_at)
                self.assertEqual(observation.quality, ValueQuality.MEASURED)


class OutOfRangeTests(unittest.TestCase):
    def test_implausible_values_become_unknown_never_zero(self):
        response = response_for(
            words_with({1: 3001, 4: 50_001, 5: 1001, 6: 101, 12: 50_001})
        )
        frame = decode_read_response(response, identity=identity(), received_at=NOW)
        for name in ("grid_voltage", "pv_power", "battery_voltage", "battery_soc", "load_power"):
            with self.subTest(field=name):
                self.assertIsNone(frame.fields[name].value)
                self.assertEqual(frame.fields[name].quality, ValueQuality.UNKNOWN)
        self.assertIsNone(frame.fields["pv_power"].value)

    def test_zero_is_kept_as_data(self):
        frame = decode_read_response(response_for(), identity=identity(), received_at=NOW)
        self.assertEqual(frame.fields["pv_power"].value, 0.0)
        self.assertEqual(frame.fields["pv_power"].quality, ValueQuality.MEASURED)

    def test_wrong_word_count_is_rejected(self):
        with self.assertRaises(Rwb1BleProfileError):
            decode_words(TEST_WORDS[:20], identity=identity(), received_at=NOW)

    def test_non_ble_identity_is_rejected(self):
        lan = identity(SourceKind.LAN)
        with self.assertRaisesRegex(Rwb1BleProfileError, "kind must be ble"):
            decode_read_response(response_for(), identity=lan, received_at=NOW)

    def test_inputs_are_not_mutated(self):
        response = response_for()
        before = bytes(response)
        decode_read_response(response, identity=identity(), received_at=NOW)
        self.assertEqual(response, before)


class CrcTests(unittest.TestCase):
    def test_known_modbus_crc_vector(self):
        # Canonical check value for the Modbus CRC-16 of the ASCII digits.
        self.assertEqual(modbus_crc16(b"123456789"), 0x4B37)

    def test_request_crc_matches_the_allow_list(self):
        request = build_allowlisted_request()
        self.assertEqual(request[-2:], bytes((0x90, 0x91)))
        check_crc(request, "request")
        self.assertIn(request, ALLOWED_REQUESTS)

    def test_valid_and_invalid_response_crc(self):
        good = response_for()
        check_crc(good, "response")
        bad = good[:-1] + bytes((good[-1] ^ 0x01,))
        with self.assertRaisesRegex(Rwb1BleProfileError, "CRC"):
            check_crc(bad, "response")

    def test_short_frame_is_rejected(self):
        with self.assertRaisesRegex(Rwb1BleProfileError, "too short"):
            check_crc(b"\x05\x03", "response")


class AllowListTests(unittest.TestCase):
    def test_only_one_request_exists_and_its_parameters_are_exact(self):
        request = build_allowlisted_request()
        self.assertEqual(request, bytes.fromhex("05 03 11 95 00 15 90 91"))
        self.assertEqual(len(ALLOWED_REQUESTS), 1)
        self.assertEqual(parse_allowed_request(request), (5, 3, 0x1195, 21))

    def test_other_register_function_slave_or_count_are_rejected(self):
        def frame(address=5, function=3, register=0x1195, count=21) -> bytes:
            body = (
                bytes((address, function))
                + register.to_bytes(2, "big")
                + count.to_bytes(2, "big")
            )
            return body + modbus_crc16(body).to_bytes(2, "little")

        cases = {
            "other-register": frame(register=0x1194),
            "config-register": frame(register=0x0000),
            "write-function": frame(function=6),
            "coils-function": frame(function=1),
            "other-slave": frame(address=4),
            "broadcast-slave": frame(address=0),
            "other-count": frame(count=20),
            "sweep-count": frame(count=125),
            "truncated": frame()[:6],
        }
        for label, request in cases.items():
            with self.subTest(case=label), self.assertRaises(Rwb1BleProfileError):
                validate_allowed_request(request)

    def test_non_bytes_request_is_rejected(self):
        for value in ("0503119500159091", None, 5):
            with self.subTest(value=value), self.assertRaises(Rwb1BleProfileError):
                validate_allowed_request(value)

    def test_build_allowlisted_request_takes_no_arguments(self):
        with self.assertRaises(TypeError):
            build_allowlisted_request(register=0x1194)  # type: ignore[call-arg]


class ResponseShapeTests(unittest.TestCase):
    def test_response_must_be_exactly_47_bytes(self):
        self.assertEqual(RESPONSE_LENGTH, 47)
        for response in (response_for()[:-1], response_for() + b"\x00"):
            with self.subTest(length=len(response)), self.assertRaises(Rwb1BleProfileError):
                validate_allowed_response(response)
        # A CRC-valid but short block still fails on length, not on CRC.
        short = response_for(TEST_WORDS[:20])
        with self.assertRaisesRegex(Rwb1BleProfileError, "exactly 21"):
            validate_allowed_response(short)

    def test_byte_count_slave_and_function_are_checked(self):
        for label, response in {
            "wrong-slave": response_for(address=4),
            "wrong-function": response_for(function=4),
        }.items():
            with self.subTest(case=label), self.assertRaises(Rwb1BleProfileError):
                validate_allowed_response(response)
        bad_count = bytearray(response_for())
        bad_count[2] = 40
        body = bytes(bad_count[:-2])
        rebuilt = body + modbus_crc16(body).to_bytes(2, "little")
        with self.assertRaisesRegex(Rwb1BleProfileError, "byte count"):
            validate_allowed_response(rebuilt)

    def test_crc_mismatch_on_response_is_rejected_before_decoding(self):
        response = response_for()
        broken = response[:-1] + bytes((response[-1] ^ 0xFF,))
        with self.assertRaisesRegex(Rwb1BleProfileError, "CRC"):
            decode_read_response(broken, identity=identity(), received_at=NOW)

    def test_non_bytes_response_is_rejected(self):
        with self.assertRaises(Rwb1BleProfileError):
            validate_allowed_response("05032A")


if __name__ == "__main__":
    unittest.main(verbosity=2)