"""Regression tests for the verified RWB1 PS4Z 8-field mapping.

Юра: «Приймаю mapping восьми полів як перевірений для дослідженого
пристрою й профілю. Зафіксуй його regression-тестами на санітизованих
кадрах.»

Fixtures: ``fixtures/rwb1_ps4z_mapping_sanitized.json`` — three PS4Z frames
(cap_ts) with the cloud fields that matched them. No device SN, session id,
topic, broker address, MAC or account data.

Scope of the claim, stated exactly:
  * byte order LITTLE-ENDIAN for the PS4Z block
  * the eight fields below, with these scales
  * verified on ONE device (PowMr HVM6.2M-48V) and ONE profile
  * verified on three independent cycles with natural variation

These tests pin THAT claim. They do not claim the device model, the
remaining words, or the other blocks (``hIg6`` / ``Sgx0`` / ``zsui``),
which are separate and only partially mapped.
"""
from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "rwb1_ps4z_mapping_sanitized.json"

# word index -> (cloud raw key, multiply cloud by, multiply word by)
MAPPING: dict[int, tuple[str, float, float]] = {
    1:  ("acInputVoltage",      1.0,   0.1),
    3:  ("pvInputVoltage",      1.0,   0.1),
    4:  ("pvInputPower",        1.0,   1.0),
    5:  ("batteryVoltage",      1.0,   0.1),
    6:  ("batteryCapacity",     1.0,   1.0),
    11: ("outputApparentPower", 1.0,   1.0),
    12: ("acOutputActivePower", 1000.0, 1.0),
    13: ("loadPercentage",      1.0,   1.0),
}
TOL = 0.051


def decode_words(b64: str) -> list[int]:
    raw = base64.b64decode(b64 + "==")
    nb = raw[2]
    data = raw[3:3 + nb]
    assert len(data) == nb and nb % 2 == 0
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, nb, 2)]


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.fx = json.loads(FIXTURE.read_text())

    def test_fixture_has_no_identifiers(self):
        """Check the DATA only. The ``_note`` prose deliberately names
        what is excluded, so scanning it would self-trigger."""
        cases_blob = json.dumps(self.fx["cases"])
        for forbidden in ("deviceSn", "session", "mac", "broker", "8.212",
                          "192.168.1.139", "token", "password", "account",
                          "01M3XWJ8", "61161"):
            self.assertNotIn(forbidden, cases_blob,
                             f"fixture data must not contain {forbidden!r}")

    def test_fixture_keys_are_only_the_expected_ones(self):
        """Belt and braces: every case key is allow-listed, so a future
        edit cannot smuggle an identifier in under a new name."""
        allowed_case = {"cap_utc", "cloud_delta_s", "ps4z_b64", "words", "cloud"}
        allowed_word = {str(i) for i in MAPPING}
        allowed_cloud = {k for k, _m, _w in MAPPING.values()}
        for case in self.fx["cases"]:
            self.assertLessEqual(set(case), allowed_case)
            self.assertLessEqual(set(case["words"]), allowed_word)
            self.assertEqual(set(case["cloud"]), allowed_cloud)

    def test_at_least_three_independent_cases(self):
        self.assertGreaterEqual(len(self.fx["cases"]), 3)

    def test_cases_are_chronologically_distinct(self):
        caps = [c["cap_utc"] for c in self.fx["cases"]]
        self.assertEqual(caps, sorted(caps))
        self.assertEqual(len(set(caps)), len(caps))

    def test_fixture_values_actually_vary(self):
        """A constant fixture would prove nothing. Each mapped word must
        take at least two distinct values across the cases.
        """
        for wi in MAPPING:
            vals = {c["words"][str(wi)] for c in self.fx["cases"]}
            self.assertGreaterEqual(len(vals), 2,
                                    f"word {wi} is constant in the fixture")


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.fx = json.loads(FIXTURE.read_text())

    def test_every_case_matches_all_eight_fields(self):
        for case in self.fx["cases"]:
            words = decode_words(case["ps4z_b64"])
            for wi, (key, cloud_mult, word_mult) in MAPPING.items():
                local = words[wi] * word_mult
                cloud = case["cloud"][key]
                self.assertIsNotNone(cloud, f"{key} missing in fixture")
                self.assertAlmostEqual(
                    local, float(cloud) * cloud_mult, delta=TOL,
                    msg=(f"case {case['cap_utc']} word {wi} ({key}): "
                         f"local={local} cloud={cloud}*x{cloud_mult}"),
                )

    def test_all_cases_fully_match(self):
        n_ok = 0
        for case in self.fx["cases"]:
            words = decode_words(case["ps4z_b64"])
            ok = sum(1 for wi, (k, cm, wm) in MAPPING.items()
                     if abs(words[wi] * wm - float(case["cloud"][k]) * cm) < TOL)
            self.assertEqual(ok, len(MAPPING))
            n_ok += 1
        self.assertGreaterEqual(n_ok, 3)

    def test_cloud_delta_is_reported_and_positive(self):
        """The cloud reported the RWB1 sample AFTER the packet was
        captured. The offset varies, so it is recorded per case and must
        not be hard-coded.
        """
        deltas = [c["cloud_delta_s"] for c in self.fx["cases"]]
        for d in deltas:
            self.assertGreater(d, 0.0)
        self.assertGreater(len(set(deltas)), 1, "delta must not be constant")


class ScaleGuardTests(unittest.TestCase):
    """The scales are pinned. A scale change must break the tests."""

    def setUp(self):
        self.fx = json.loads(FIXTURE.read_text())

    def test_tenth_volt_fields_are_not_read_as_whole_volts(self):
        """w5 holds 531 for 53.1 V. Reading it as 531 V is wrong for a 48 V
        pack; the x0.1 scale is what makes it physical.
        """
        for case in self.fx["cases"]:
            words = decode_words(case["ps4z_b64"])
            self.assertGreater(words[5] / 10.0, 45.0)
            self.assertLess(words[5] / 10.0, 65.0)
            self.assertGreater(words[5], 200.0)

    def test_kw_field_is_normalised_to_watts(self):
        """acOutputActivePower arrives in kW; w12 carries watts."""
        for case in self.fx["cases"]:
            self.assertLess(case["cloud"]["acOutputActivePower"], 2.0)
            self.assertGreater(case["cloud"]["acOutputActivePower"], 0.05)
            words = decode_words(case["ps4z_b64"])
            self.assertGreater(words[12], 100.0)

    def test_big_endian_would_fail(self):
        """Documents why little-endian is the pinned order: reading the
        same bytes big-endian must NOT reproduce the mapping.
        """
        case = self.fx["cases"][0]
        raw = base64.b64decode(case["ps4z_b64"] + "==")
        data = raw[3:3 + raw[2]]
        be = [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data), 2)]
        ok_be = sum(1 for wi, (k, cm, wm) in MAPPING.items()
                    if abs(be[wi] * wm - float(case["cloud"][k]) * cm) < TOL)
        ok_le = sum(1 for wi, (k, cm, wm) in MAPPING.items()
                    if abs(decode_words(case["ps4z_b64"])[wi] * wm
                           - float(case["cloud"][k]) * cm) < TOL)
        self.assertEqual(ok_le, len(MAPPING))
        self.assertLess(ok_be, len(MAPPING))


if __name__ == "__main__":
    unittest.main()