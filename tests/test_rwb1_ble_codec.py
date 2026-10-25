"""RWB1 BLE codec: key derivation, envelope cipher, fragmentation rules."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from telemetry_sources.rwb1_ble_codec import (
    CID_REQUEST,
    CID_RESPONSE,
    MAX_FRAGMENT_PAYLOAD,
    FragmentReassembler,
    Rwb1BleCodecError,
    Rwb1BleFragmentError,
    build_envelope,
    decrypt_response,
    derive_key,
    encrypt_request,
    fragment_payload,
    max_fragment_payload,
    new_command_no,
)
from telemetry_sources.rwb1_ble_profile import build_allowlisted_request

from tests.rwb1_ble_fixtures import (
    TEST_DTU_ID, TEST_KEY, encrypt_envelope, fragmented, response_envelope, response_for,
)


class KeyDerivationTests(unittest.TestCase):
    def test_key_is_md5_of_stripped_id_with_suffix(self):
        expected = hashlib.md5((TEST_DTU_ID.strip() + "SEC_").encode()).digest()
        self.assertEqual(derive_key(TEST_DTU_ID), expected)
        self.assertEqual(len(expected), 16)

    def test_whitespace_is_stripped_before_derivation(self):
        self.assertEqual(derive_key(f"  {TEST_DTU_ID} "), derive_key(TEST_DTU_ID))

    def test_empty_identifier_is_refused(self):
        for value in ("", "   ", None, 42):
            with self.subTest(value=value), self.assertRaises(Rwb1BleCodecError):
                derive_key(value)

    def test_key_is_deterministic_and_identifier_is_not_returned(self):
        key = derive_key(TEST_DTU_ID)
        self.assertEqual(key, derive_key(TEST_DTU_ID))
        self.assertNotIn(TEST_DTU_ID.encode(), key)


class EnvelopeTests(unittest.TestCase):
    def test_envelope_matches_the_documented_wire_shape(self):
        envelope = build_envelope(build_allowlisted_request(), cmd_no="AB12")
        self.assertEqual(envelope["CID"], CID_REQUEST)
        payload = envelope["PL"]
        self.assertEqual(payload["Req"], "0503119500159091")
        self.assertEqual(
            payload["Uart"],
            {"BaudRate": 2400, "DataBit": 8, "ParityBit": "NONE", "StopBit": 1},
        )
        self.assertEqual(payload["CmdType"], "gatherSingleDevProps")
        self.assertEqual(payload["CmdNo"], "AB12")

    def test_envelope_serializes_compactly(self):
        raw = json.dumps(build_envelope(build_allowlisted_request(), cmd_no="AB12"),
                         separators=(",", ":"))
        self.assertNotIn(", ", raw)
        self.assertNotIn(": ", raw)

    def test_envelope_refuses_a_non_allowlisted_frame(self):
        from telemetry_sources.rwb1_ble_profile import Rwb1BleProfileError

        with self.assertRaises(Rwb1BleProfileError):
            build_envelope(bytes.fromhex("05 03 00 00 00 01 84 0A"))

    def test_command_number_is_four_random_alphanumerics(self):
        generated = {new_command_no() for _ in range(20)}
        self.assertTrue(generated)
        for value in generated:
            self.assertEqual(len(value), 4)
            self.assertTrue(value.isalnum() and value.isascii())

    def test_bad_command_number_is_refused(self):
        for value in ("", "has space", "toolongcommandnoover16", 1234):
            with self.subTest(value=value), self.assertRaises(Rwb1BleCodecError):
                build_envelope(build_allowlisted_request(), cmd_no=value)
        # None is the documented "generate one" sentinel, not an error.
        self.assertEqual(len(build_envelope(build_allowlisted_request())["PL"]["CmdNo"]), 4)

    def test_wrong_key_length_is_refused(self):
        for key in (b"", b"\x00" * 15, b"\x00" * 17, "0123456789abcdef"):
            with self.subTest(key=len(key)), self.assertRaises(Rwb1BleCodecError):
                encrypt_request(build_allowlisted_request(), key)


class CryptoRoundTripTests(unittest.TestCase):
    def test_request_round_trips_through_the_device_style_cipher(self):
        request = build_allowlisted_request()
        payload, cmd_no = encrypt_request(request, TEST_KEY, cmd_no="ZZ99")
        self.assertEqual(len(payload) % 4, 0)  # base64 length
        # The device echoes the request envelope structure in its answer.
        answer = response_envelope(response_for(), cmd_no=cmd_no)
        decoded = decrypt_response(answer, TEST_KEY, expected_cmd_no=cmd_no)
        self.assertEqual(decoded.cmd_no, cmd_no)
        self.assertTrue(decoded.cmd_no_present)
        self.assertEqual(len(decoded.modbus), 47)

    def test_decrypting_with_the_wrong_key_fails(self):
        other = derive_key("TEST-DTU-0002")
        message = response_envelope(cmd_no="AB12")
        with self.assertRaises((Rwb1BleCodecError, UnicodeDecodeError, json.JSONDecodeError)):
            decrypt_response(message, other, expected_cmd_no="AB12")

    def test_iv_equals_the_key_as_on_the_device(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        envelope = build_envelope(build_allowlisted_request(), cmd_no="AB12")
        plaintext = json.dumps(envelope, separators=(",", ":")).encode()
        plaintext += b"\0" * (16 - len(plaintext) % 16)
        encryptor = Cipher(algorithms.AES(TEST_KEY), modes.CBC(TEST_KEY)).encryptor()
        expected = base64.b64encode(encryptor.update(plaintext) + encryptor.finalize())
        self.assertEqual(encrypt_request(build_allowlisted_request(), TEST_KEY, cmd_no="AB12")[0],
                         expected)


class DecryptValidationTests(unittest.TestCase):
    def test_broken_base64_is_rejected(self):
        for message in (b"not base64!!!", b"AAAA=BBB", b"@@@@"):
            with self.subTest(message=message), self.assertRaises(Rwb1BleCodecError):
                decrypt_response(message, TEST_KEY)

    def test_empty_payload_is_rejected(self):
        with self.assertRaises(Rwb1BleCodecError):
            decrypt_response(b"", TEST_KEY)

    def test_length_not_a_multiple_of_sixteen_is_rejected(self):
        raw = base64.b64decode(response_envelope(cmd_no="AB12"))
        for extra in (1, 15, 17):
            with self.subTest(extra=extra):
                bad = base64.b64encode(raw + b"\x00" * extra)
                with self.assertRaisesRegex(Rwb1BleCodecError, "multiple of 16"):
                    decrypt_response(bad, TEST_KEY)

    def test_wrong_cid_is_rejected(self):
        for cid in (30024, 30026, 0, None):
            with self.subTest(cid=cid), self.assertRaisesRegex(Rwb1BleCodecError, "CID"):
                decrypt_response(response_envelope(cid=cid), TEST_KEY)

    def test_nonzero_rc_is_rejected(self):
        for rc in (1, -1, "0", None):
            with self.subTest(rc=rc), self.assertRaisesRegex(Rwb1BleCodecError, "RC"):
                decrypt_response(response_envelope(rc=rc), TEST_KEY)

    def test_missing_payload_rsp_is_rejected(self):
        with self.assertRaisesRegex(Rwb1BleCodecError, "PL.Rsp"):
            decrypt_response(response_envelope(omit_rsp=True), TEST_KEY)

    def test_odd_length_and_non_hex_rsp_are_rejected(self):
        odd = encrypt_envelope({"CID": CID_RESPONSE, "RC": 0, "PL": {"Rsp": "050"}})
        with self.assertRaisesRegex(Rwb1BleCodecError, "odd"):
            decrypt_response(odd, TEST_KEY)
        junk = encrypt_envelope({"CID": CID_RESPONSE, "RC": 0, "PL": {"Rsp": "ZZ"}})
        with self.assertRaisesRegex(Rwb1BleCodecError, "hexadecimal"):
            decrypt_response(junk, TEST_KEY)
        not_text = encrypt_envelope({"CID": CID_RESPONSE, "RC": 0, "PL": {"Rsp": 5}})
        with self.assertRaisesRegex(Rwb1BleCodecError, "hex string"):
            decrypt_response(not_text, TEST_KEY)

    def test_truncated_modbus_payload_is_rejected(self):
        short = encrypt_envelope(
            {"CID": CID_RESPONSE, "RC": 0, "PL": {"Rsp": "050300"}}
        )
        with self.assertRaisesRegex(Rwb1BleCodecError, "too short"):
            decrypt_response(short, TEST_KEY)

    def test_non_json_plaintext_is_rejected(self):
        junk = base64.b64encode(b"\x00" * 64)
        with self.assertRaises(Rwb1BleCodecError):
            decrypt_response(junk, TEST_KEY)

    def test_non_object_envelope_is_rejected(self):
        array = encrypt_envelope([1, 2, 3])
        with self.assertRaises(Rwb1BleCodecError):
            decrypt_response(array, TEST_KEY)

    def test_non_string_message_is_rejected(self):
        with self.assertRaises(Rwb1BleCodecError):
            decrypt_response(12345, TEST_KEY)


class CmdNoCorrelationTests(unittest.TestCase):
    def test_matching_cmd_no_is_accepted(self):
        decoded = decrypt_response(
            response_envelope(cmd_no="Q7Z1"), TEST_KEY, expected_cmd_no="Q7Z1",
        )
        self.assertEqual(decoded.cmd_no, "Q7Z1")

    def test_mismatched_cmd_no_is_rejected_when_present(self):
        with self.assertRaisesRegex(Rwb1BleCodecError, "CmdNo"):
            decrypt_response(
                response_envelope(cmd_no="Q7Z1"), TEST_KEY, expected_cmd_no="ZZZZ",
            )

    def test_absent_cmd_no_is_accepted_but_reported_as_absent(self):
        decoded = decrypt_response(
            response_envelope(), TEST_KEY, expected_cmd_no="Q7Z1",
        )
        self.assertIsNone(decoded.cmd_no)
        self.assertFalse(decoded.cmd_no_present)
        self.assertEqual(len(decoded.modbus), 47)

    def test_non_string_cmd_no_is_rejected(self):
        bad = encrypt_envelope({
            "CID": CID_RESPONSE,
            "RC": 0,
            "PL": {"Rsp": response_for().hex().upper(), "CmdNo": 7},
        })
        with self.assertRaisesRegex(Rwb1BleCodecError, "CmdNo"):
            decrypt_response(bad, TEST_KEY)


class FragmentationTests(unittest.TestCase):
    def test_mtu_limit_is_capped_at_the_protocol_maximum(self):
        self.assertEqual(max_fragment_payload(None), MAX_FRAGMENT_PAYLOAD)
        self.assertEqual(max_fragment_payload(185), 179)
        self.assertEqual(max_fragment_payload(1000), MAX_FRAGMENT_PAYLOAD)
        self.assertEqual(max_fragment_payload(23), 17)

    def test_absurdly_small_mtu_is_refused(self):
        for mtu in (6, 1, 0, -3):
            with self.subTest(mtu=mtu), self.assertRaises(Rwb1BleCodecError):
                max_fragment_payload(mtu)

    def test_single_fragment_boundary_round_trip(self):
        payload = b"x" * MAX_FRAGMENT_PAYLOAD
        fragments = fragment_payload(payload)
        self.assertEqual(len(fragments), 1)
        self.assertEqual(fragments[0], bytes((1, 1, 227)) + payload)
        self.assertEqual(FragmentReassembler().feed(fragments[0]).complete, payload)

    def test_multi_fragment_boundary_sizes(self):
        for size in (1, MAX_FRAGMENT_PAYLOAD - 1, MAX_FRAGMENT_PAYLOAD,
                     MAX_FRAGMENT_PAYLOAD + 1, MAX_FRAGMENT_PAYLOAD * 3):
            with self.subTest(size=size):
                payload = bytes(range(256)) * (size // 256 + 1)
                payload = payload[:size]
                fragments = fragment_payload(payload)
                expected_total = -(-size // MAX_FRAGMENT_PAYLOAD)
                self.assertEqual(len(fragments), expected_total)
                self.assertEqual(len(fragments[0]) - 3, min(size, MAX_FRAGMENT_PAYLOAD))
                self.assertLessEqual(max(len(f) - 3 for f in fragments), MAX_FRAGMENT_PAYLOAD)
                assembler = FragmentReassembler()
                complete = None
                for fragment in fragments:
                    complete = assembler.feed(fragment).complete or complete
                self.assertEqual(complete, payload)

    def test_custom_fragment_size_respects_the_announced_length(self):
        fragments = fragment_payload(b"y" * 400, max_payload=100)
        self.assertEqual([f[0] for f in fragments], [1, 2, 3, 4])
        self.assertTrue(all(f[1] == 4 and f[2] == 100 for f in fragments))

    def test_out_of_order_delivery_is_reassembled(self):
        # Index 1 always opens a message, so only later indexes may arrive early.
        payload = b"z" * 600
        fragments = fragment_payload(payload)
        assembler = FragmentReassembler()
        self.assertIsNone(assembler.feed(fragments[0]).complete)
        self.assertIsNone(assembler.feed(fragments[2]).complete)
        self.assertEqual(assembler.feed(fragments[1]).complete, payload)

    def test_fragment_one_after_others_joins_the_message_in_flight(self):
        """Out-of-order arrival of the head is normal on BLE.

        This used to assert ``received == 1`` (i.e. a restart), which pinned
        the F6 defect: an arriving head wiped parts that were already
        collected. It must now *join* them.
        """
        fragments = fragment_payload(b"z" * 600)
        assembler = FragmentReassembler()
        assembler.feed(fragments[2])
        state = assembler.feed(fragments[0])
        self.assertIsNone(state.complete)
        self.assertEqual(state.received, 2)

    def test_identical_duplicate_is_accepted_as_retransmission(self):
        payload = b"q" * 500
        fragments = fragment_payload(payload)
        assembler = FragmentReassembler()
        assembler.feed(fragments[0])
        state = assembler.feed(fragments[0])
        self.assertEqual(state.received, 1)
        self.assertIsNone(state.complete)

    def test_conflicting_duplicate_index_is_rejected(self):
        fragments = fragment_payload(b"q" * 500)
        conflicting = bytes((2, 3, 3)) + b"abc"
        assembler = FragmentReassembler()
        assembler.feed(fragments[0])
        assembler.feed(fragments[1])
        with self.assertRaisesRegex(Rwb1BleFragmentError, "conflicting duplicate"):
            assembler.feed(conflicting)

    def test_conflicting_duplicate_of_the_first_fragment_is_rejected(self):
        """F6: index 1 must be validated like every other index.

        The reset of the partial state used to happen *before* the duplicate
        check, so a fragment 1 carrying different bytes was accepted silently
        and the message was rebuilt from the wrong head.
        """
        payload = b"q" * 500
        fragments = fragment_payload(payload)
        tampered = bytes((1, fragments[0][1], 5)) + b"XXXXX"
        assembler = FragmentReassembler()
        assembler.feed(fragments[0])
        with self.assertRaisesRegex(Rwb1BleFragmentError, "conflicting duplicate"):
            assembler.feed(tampered)

    def test_identical_repeat_of_the_first_fragment_keeps_assembled_parts(self):
        """A retransmitted head must not throw away the parts already held."""
        payload = b"q" * 500
        fragments = fragment_payload(payload)
        assembler = FragmentReassembler()
        assembler.feed(fragments[0])
        assembler.feed(fragments[1])
        state = assembler.feed(fragments[0])  # identical retransmission
        self.assertIsNone(state.complete)
        self.assertEqual(state.received, 2, "fragment 2 must survive a repeat of 1")
        self.assertEqual(assembler.pending, 1)
        self.assertEqual(assembler.feed(fragments[2]).complete, payload)

    def test_conflicting_total_on_a_repeated_first_fragment_is_rejected(self):
        fragments = fragment_payload(b"q" * 500)
        assembler = FragmentReassembler()
        assembler.feed(fragments[0])
        assembler.feed(fragments[1])
        with self.assertRaisesRegex(Rwb1BleFragmentError, "total changed"):
            assembler.feed(bytes((1, 4, len(fragments[0]) - 3)) + fragments[0][3:])

    def test_reset_is_the_documented_way_to_start_a_new_message(self):
        """A new head after a partial message is a *different* index-1 value.

        It is therefore reported as a conflicting duplicate; callers that
        genuinely begin a new message must call :meth:`reset` first (the BLE
        transport builds a fresh assembler per transaction).
        """
        assembler = FragmentReassembler()
        assembler.feed(fragmented(b"w" * 500)[0])
        other = fragment_payload(b"n" * 100)[0]
        with self.assertRaisesRegex(Rwb1BleFragmentError, "conflicting duplicate"):
            assembler.feed(other)
        assembler.reset()
        self.assertEqual(assembler.feed(other).complete, b"n" * 100)

    def test_out_of_order_first_fragment_does_not_discard_a_later_one(self):
        """Arriving head-last is normal on BLE; it must not clear state."""
        payload = b"z" * 600
        fragments = fragment_payload(payload)
        assembler = FragmentReassembler()
        assembler.feed(fragments[2])
        state = assembler.feed(fragments[0])
        self.assertEqual(state.received, 2)
        self.assertEqual(assembler.feed(fragments[1]).complete, payload)

    def test_invalid_headers_are_rejected(self):
        assembler = FragmentReassembler()
        cases = {
            "too-short": b"\x01\x02",
            "zero-index": bytes((0, 1, 1)) + b"a",
            "zero-total": bytes((1, 0, 1)) + b"a",
            "index-above-total": bytes((3, 2, 1)) + b"a",
            "length-mismatch": bytes((1, 2, 5)) + b"ab",
            "oversize-payload": bytes((1, 1, 228)) + b"a" * 228,
        }
        for label, fragment in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(Rwb1BleFragmentError):
                    FragmentReassembler().feed(fragment)

    def test_oversized_total_is_rejected(self):
        fragment = bytes((1, 255, 1)) + b"a"
        assembler = FragmentReassembler()
        self.assertEqual(assembler.feed(fragment).total, 255)
        # A total beyond the addressable range cannot be announced safely.
        with self.assertRaises(Rwb1BleFragmentError):
            FragmentReassembler().feed(bytes((1, 0, 1)) + b"a")

    def test_total_changing_mid_message_is_rejected(self):
        assembler = FragmentReassembler()
        assembler.feed(bytes((1, 3, 1)) + b"a")
        assembler.feed(bytes((2, 3, 1)) + b"b")
        with self.assertRaisesRegex(Rwb1BleFragmentError, "total changed"):
            assembler.feed(bytes((3, 4, 1)) + b"c")

    def test_incomplete_message_never_completes(self):
        assembler = FragmentReassembler()
        for fragment in fragmented(b"w" * 500)[:2]:
            state = assembler.feed(fragment)
            self.assertIsNone(state.complete)
        self.assertEqual(assembler.pending, 1)
        self.assertEqual(assembler.total, 3)

    def test_new_message_after_partial_state_requires_a_reset(self):
        """Replaced the old "index 1 silently restarts" expectation.

        ``test_reset_is_the_documented_way_to_start_a_new_message`` covers the
        supported sequence; this one pins that the *unsupported* sequence now
        fails loudly instead of silently discarding collected parts.
        """
        assembler = FragmentReassembler()
        assembler.feed(fragmented(b"w" * 500)[0])
        with self.assertRaises(Rwb1BleFragmentError):
            assembler.feed(fragment_payload(b"n" * 100)[0])

    def test_reset_clears_progress(self):
        assembler = FragmentReassembler()
        assembler.feed(fragmented(b"w" * 500)[0])
        assembler.reset()
        self.assertIsNone(assembler.total)
        self.assertEqual(assembler.pending, 0)

    def test_non_bytes_fragment_is_rejected(self):
        with self.assertRaises(Rwb1BleFragmentError):
            FragmentReassembler().feed("0101")

    def test_oversize_fragment_beyond_negotiated_limit_is_rejected(self):
        assembler = FragmentReassembler(max_payload=20)
        with self.assertRaisesRegex(Rwb1BleFragmentError, "exceeds the negotiated size"):
            assembler.feed(bytes((1, 1, 21)) + b"a" * 21)

    def test_fragmenting_rejects_bad_input(self):
        for payload, limit in ((b"", 10), (b"x" * 300, 0), (b"x" * 300, 999)):
            with self.subTest(limit=limit), self.assertRaises(Rwb1BleCodecError):
                fragment_payload(payload, max_payload=limit)

    def test_realistic_encrypted_response_reassembles_end_to_end(self):
        message = response_envelope(cmd_no="K3P9")
        assembler = FragmentReassembler()
        complete = None
        for fragment in fragmented(message, max_payload=64):
            complete = assembler.feed(fragment).complete or complete
        self.assertIsNotNone(complete)
        self.assertEqual(decrypt_response(complete, TEST_KEY, expected_cmd_no="K3P9").cmd_no, "K3P9")


if __name__ == "__main__":
    unittest.main(verbosity=2)