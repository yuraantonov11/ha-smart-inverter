"""Contract and strict RWB1 decoder tests; no transport is opened."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from telemetry_sources.models import (
    FieldObservation, SourceFrame, SourceIdentity, SourceKind, ValueQuality,
)
from telemetry_sources.selection import FieldPolicy, select_fields
from telemetry_sources.rwb1 import (
    Rwb1FrameError, build_read_request, decode_correlated_response,
    decode_passive_observed_response, modbus_crc16,
)


NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def identity(provider: str = "local", device: str = "inverter-1") -> SourceIdentity:
    return SourceIdentity(provider, device, SourceKind.GATEWAY)


def obs(value, *, at=NOW, measured=None, quality=ValueQuality.MEASURED, unit="W"):
    return FieldObservation(value, unit, at, measured, quality)


def frame(provider, fields, *, device="inverter-1", at=NOW):
    return SourceFrame(identity(provider, device), at, fields)


def response_for(words: list[int], *, address: int = 5, function: int = 3) -> bytes:
    data = b"".join(word.to_bytes(2, "little") for word in words)
    body = bytes((address, function, len(data))) + data
    return body + modbus_crc16(body).to_bytes(2, "little")


class ObservationContractTests(unittest.TestCase):
    def test_zero_is_data_and_none_remains_unknown(self):
        zero = obs(0)
        missing = obs(None)
        self.assertEqual(zero.value, 0.0)
        self.assertIsNone(missing.value)
        self.assertEqual(missing.quality, ValueQuality.UNKNOWN)

    def test_rejects_nonfinite_boolean_and_naive_timestamps(self):
        for value in (float("nan"), float("inf"), float("-inf"), True):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                obs(value)
        with self.assertRaises(ValueError):
            obs(1, at=datetime(2026, 10, 19, 12, 0))
        with self.assertRaises(ValueError):
            obs(1, measured=datetime(2026, 10, 19, 12, 0))

    def test_aware_times_normalize_to_utc_and_frame_copies_mapping(self):
        plus_three = timezone(timedelta(hours=3))
        received = datetime(2026, 10, 19, 15, 0, tzinfo=plus_three)
        source_fields = {"load_power": obs(0, at=received)}
        result = frame("local", source_fields, at=received)
        source_fields["pv_power"] = obs(40)
        self.assertEqual(result.received_at, NOW)
        self.assertEqual(set(result.fields), {"load_power"})
        with self.assertRaises(TypeError):
            result.fields["pv_power"] = obs(40)

    def test_selection_falls_back_per_field_and_keeps_provenance_times(self):
        old = NOW - timedelta(seconds=20)
        measured = NOW - timedelta(seconds=10)
        frames = [
            frame("primary", {"pv_power": obs(900, at=old, unit="W"),
                              "load_power": obs(0, at=NOW, measured=measured)}),
            frame("secondary", {"pv_power": obs(120, at=NOW, unit="W"),
                                 "load_power": obs(500, at=NOW)}),
        ]
        policies = {
            name: FieldPolicy(timedelta(seconds=5), timedelta(seconds=30))
            for name in ("pv_power", "load_power")
        }
        result = select_fields(
            frames, device_id="inverter-1", now=NOW,
            priority={"primary": 0, "secondary": 1}, policies=policies,
            required_fields=frozenset({"pv_power", "load_power", "battery_soc"}),
        )
        self.assertEqual(result.fields["pv_power"].value, 120)
        self.assertEqual(result.fields["load_power"].value, 0)
        self.assertEqual(result.sources["pv_power"].provider_id, "secondary")
        self.assertEqual(result.sources["load_power"].provider_id, "primary")
        self.assertEqual(result.fields["load_power"].measured_at, measured)
        self.assertEqual(result.missing_required, frozenset({"battery_soc"}))
        self.assertIn("receipt data is stale", " ".join(result.excluded["pv_power"]))

    def test_unknown_measurement_time_is_allowed_only_by_policy(self):
        source = frame("local", {"load_power": obs(300)})
        allowed = FieldPolicy(timedelta(seconds=5), timedelta(seconds=30))
        strict = FieldPolicy(timedelta(seconds=5), timedelta(seconds=30), True)
        permissive_result = select_fields(
            [source], device_id="inverter-1", now=NOW,
            priority={"local": 0}, policies={"load_power": allowed},
        )
        strict_result = select_fields(
            [source], device_id="inverter-1", now=NOW,
            priority={"local": 0}, policies={"load_power": strict},
        )
        self.assertIn("load_power", permissive_result.fields)
        self.assertNotIn("load_power", strict_result.fields)
        self.assertIn("measurement timestamp is unknown", strict_result.excluded["load_power"][0])

    def test_device_mismatch_stale_measurement_quality_and_future_are_excluded(self):
        frames = [
            frame("wrong-device", {"pv_power": obs(10)}, device="inverter-2"),
            frame("old-measurement", {"pv_power": obs(
                20, measured=NOW - timedelta(minutes=1))}),
            frame("estimated", {"pv_power": obs(30, quality=ValueQuality.ESTIMATED)}),
            frame("future", {"pv_power": obs(40, at=NOW + timedelta(seconds=1))}),
        ]
        policy = FieldPolicy(timedelta(minutes=2), timedelta(seconds=10))
        result = select_fields(
            frames, device_id="inverter-1", now=NOW,
            priority={}, policies={"pv_power": policy}, required_fields=frozenset({"pv_power"}),
        )
        self.assertEqual(result.fields, {})
        self.assertEqual(result.missing_required, frozenset({"pv_power"}))
        self.assertIn("device", result.excluded)
        self.assertEqual(len(result.excluded["pv_power"]), 3)


class Rwb1DecoderTests(unittest.TestCase):
    def setUp(self):
        self.identity = identity()
        self.request = build_read_request()
        words = [0] * 21
        words[1] = 2339
        words[4] = 0
        words[5] = 529
        words[6] = 86
        words[12] = 1073
        self.response = response_for(words)

    def decode(self, request=None, response=None, identity_arg=None):
        return decode_correlated_response(
            self.request if request is None else request,
            self.response if response is None else response,
            identity=self.identity if identity_arg is None else identity_arg,
            received_at=NOW,
        )

    def test_valid_correlated_frame_decodes_only_corroborated_fields(self):
        decoded = self.decode()
        self.assertEqual(set(decoded.fields), {
            "grid_voltage", "pv_power", "battery_voltage", "battery_soc", "load_power",
        })
        self.assertEqual(decoded.fields["grid_voltage"].value, 233.9)
        self.assertEqual(decoded.fields["pv_power"].value, 0.0)
        self.assertEqual(decoded.fields["battery_voltage"].value, 52.9)
        self.assertEqual(decoded.fields["battery_soc"].value, 86.0)
        self.assertEqual(decoded.fields["load_power"].value, 1073.0)
        self.assertIsNone(decoded.fields["load_power"].measured_at)
        self.assertEqual(decoded.profile_id, "rwb1-04-pow-hvm6.2m-48v-n-experimental-v1")

    def test_rejects_wrong_request_profile_and_source_kind(self):
        for params in ((5, 3, 0x1194, 21), (5, 3, 0x1195, 20), (4, 3, 0x1195, 21)):
            addr, fn, register, count = params
            body = bytes((addr, fn)) + register.to_bytes(2, "big") + count.to_bytes(2, "big")
            request = body + modbus_crc16(body).to_bytes(2, "little")
            with self.subTest(params=params), self.assertRaises(Rwb1FrameError):
                self.decode(request=request)
        serial_identity = SourceIdentity("serial", "inverter-1", SourceKind.SERIAL)
        with self.assertRaises(Rwb1FrameError):
            self.decode(identity_arg=serial_identity)

    def test_rejects_corruption_address_function_length_and_uncorrelated_21_word_block(self):
        bad_crc = self.response[:-1] + bytes((self.response[-1] ^ 0x01,))
        with self.assertRaisesRegex(Rwb1FrameError, "CRC"):
            self.decode(response=bad_crc)
        for response in (
            response_for([0] * 21, address=4),
            response_for([0] * 21, function=4),
            self.response[:-1],
        ):
            with self.assertRaises(Rwb1FrameError):
                self.decode(response=response)
        wrong_register = build_read_request(register=0x1000, count=21)
        with self.assertRaises(Rwb1FrameError):
            self.decode(request=wrong_register)

    def test_invalid_soc_becomes_unknown_and_inputs_are_not_mutated(self):
        words = [0] * 21
        words[6] = 101
        response = response_for(words)
        request_before = bytes(self.request)
        response_before = bytes(response)
        decoded = self.decode(response=response)
        self.assertIsNone(decoded.fields["battery_soc"].value)
        self.assertEqual(decoded.fields["battery_soc"].quality, ValueQuality.UNKNOWN)
        self.assertEqual(self.request, request_before)
        self.assertEqual(response, response_before)

    def test_passive_candidate_decodes_only_corroborated_fields(self):
        decoded = decode_passive_observed_response(
            self.response, identity=self.identity, received_at=NOW,
        )
        self.assertEqual(decoded.profile_id, "rwb1-04-pow-hvm6.2m-48v-n-passive-candidate-v1")
        self.assertEqual(decoded.fields["grid_voltage"].value, 233.9)
        self.assertEqual(decoded.fields["pv_power"].value, 0.0)
        self.assertEqual(decoded.fields["battery_voltage"].value, 52.9)
        self.assertEqual(decoded.fields["battery_soc"].value, 86.0)
        self.assertEqual(decoded.fields["load_power"].value, 1073.0)
        self.assertIsNone(decoded.fields["load_power"].measured_at)

    def test_passive_candidate_still_requires_exact_frame_shape_crc_and_source(self):
        with self.assertRaisesRegex(Rwb1FrameError, "CRC"):
            decode_passive_observed_response(
                self.response[:-1] + bytes((self.response[-1] ^ 1,)),
                identity=self.identity, received_at=NOW,
            )
        for response in (
            response_for([0] * 21, address=4),
            response_for([0] * 21, function=4),
            response_for([0] * 20),
        ):
            with self.subTest(response_length=len(response)), self.assertRaises(Rwb1FrameError):
                decode_passive_observed_response(response, identity=self.identity, received_at=NOW)
        serial_identity = SourceIdentity("serial", "inverter-1", SourceKind.SERIAL)
        with self.assertRaises(Rwb1FrameError):
            decode_passive_observed_response(self.response, identity=serial_identity, received_at=NOW)

    def test_out_of_range_candidate_values_become_unknown(self):
        words = [0] * 21
        words[1] = 3001  # 300.1 V exceeds the candidate grid limit.
        words[4] = 50_001
        words[5] = 1001  # 100.1 V exceeds the candidate battery limit.
        words[6] = 101
        words[12] = 50_001
        decoded = decode_passive_observed_response(
            response_for(words), identity=self.identity, received_at=NOW,
        )
        for field in ("grid_voltage", "pv_power", "battery_voltage", "battery_soc", "load_power"):
            with self.subTest(field=field):
                self.assertIsNone(decoded.fields[field].value)
                self.assertEqual(decoded.fields[field].quality, ValueQuality.UNKNOWN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
