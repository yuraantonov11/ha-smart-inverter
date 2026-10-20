"""Tests for the bounded diagnostic field capture.

Юра's constraints, each pinned here:
  * only an explicit allow-list of CONCRETE raw field names;
  * numeric only, bounded strings;
  * no credentials / device SN / session / token / MAC / broker address;
  * bounded by record count AND byte count, then stops (no rotation, no
    deletion);
  * polling and control untouched (observer is called after parsing).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import importlib.util
import sys

_REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "field_diagnostics_under_test", _REPO / "field_diagnostics.py"
)
fd = importlib.util.module_from_spec(_spec)
sys.modules["field_diagnostics_under_test"] = fd
_spec.loader.exec_module(fd)


class AllowListTests(unittest.TestCase):
    def setUp(self):
        fd.reset()

    def test_known_numeric_field_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            fd._state["path"] = str(Path(d) / "s.jsonl")
            ok = fd.record({"batteryVoltage": 52.8, "batterySoc": 88.0})
            self.assertTrue(ok)
            rec = json.loads((Path(d) / "s.jsonl").read_text().strip())
            self.assertEqual(rec["f"]["batteryVoltage"], 52.8)
            self.assertEqual(rec["f"]["batterySoc"], 88.0)

    def test_unknown_field_is_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            fd._state["path"] = str(Path(d) / "s.jsonl")
            fd.record({
                "batteryVoltage": 52.8,
                "totallyUnknownField": 1.0,
                "anotherNewRawField": 2.0,
            })
            rec = json.loads((Path(d) / "s.jsonl").read_text().strip())
            self.assertEqual(set(rec["f"]), {"batteryVoltage"})

    def test_identifier_like_fields_are_never_written(self):
        """Any key that smells like an identifier must be rejected even if
        it appears in raw_fields. This is the leak guard.
        """
        forbidden = [
            "deviceSn", "deviceSN", "sn", "serialNumber", "token", "accessToken",
            "password", "username", "userName", "accountId", "sessionId",
            "mac", "macAddress", "broker", "brokerIp", "host", "uuid",
            "email", "phone", "deviceId", "imei",
        ]
        with tempfile.TemporaryDirectory() as d:
            fd._state["path"] = str(Path(d) / "s.jsonl")
            raw = {k: "SECRET-VALUE" for k in forbidden}
            raw["batteryVoltage"] = 52.8
            fd.record(raw)
            text = (Path(d) / "s.jsonl").read_text()
            self.assertNotIn("SECRET-VALUE", text)
            rec = json.loads(text.strip())
            self.assertEqual(set(rec["f"]), {"batteryVoltage"})

    def test_booleans_and_nonfinite_are_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            fd._state["path"] = str(Path(d) / "s.jsonl")
            fd.record({
                "batteryVoltage": 52.8,
                "gridPower": float("nan"),
                "loadPower": float("inf"),
                "ratedActivePower": True,
            })
            rec = json.loads((Path(d) / "s.jsonl").read_text().strip())
            self.assertEqual(set(rec["f"]), {"batteryVoltage"})

    def test_none_is_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            fd._state["path"] = str(Path(d) / "s.jsonl")
            fd.record({"batteryVoltage": None, "batterySoc": 50.0})
            rec = json.loads((Path(d) / "s.jsonl").read_text().strip())
            self.assertEqual(set(rec["f"]), {"batterySoc"})

    def test_long_strings_are_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            fd._state["path"] = str(Path(d) / "s.jsonl")
            fd.record({"batteryVoltage": 52.8, "workingMode": "x" * 100})
            rec = json.loads((Path(d) / "s.jsonl").read_text().strip())
            self.assertEqual(set(rec["f"]), {"batteryVoltage"})

    def test_short_mode_string_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            fd._state["path"] = str(Path(d) / "s.jsonl")
            fd.record({"batteryVoltage": 52.8, "workingMode": "LineMode"})
            rec = json.loads((Path(d) / "s.jsonl").read_text().strip())
            self.assertEqual(rec["f"]["workingMode"], "LineMode")


class BoundTests(unittest.TestCase):
    def setUp(self):
        fd.reset()

    def test_record_cap_is_enforced(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.jsonl"
            fd._state["path"] = str(p)
            saved = fd.MAX_RECORDS
            fd.MAX_RECORDS = 5
            try:
                written = sum(fd.record({"batteryVoltage": 52.0}) for _ in range(20))
            finally:
                fd.MAX_RECORDS = saved
            self.assertEqual(written, 5)
            self.assertEqual(len(p.read_text().strip().splitlines()), 5)
            self.assertTrue(fd.status()["stopped"])

    def test_byte_cap_is_enforced(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.jsonl"
            fd._state["path"] = str(p)
            saved = fd.MAX_BYTES
            fd.MAX_BYTES = 200
            try:
                written = sum(fd.record({"batteryVoltage": 52.0}) for _ in range(50))
            finally:
                fd.MAX_BYTES = saved
            self.assertLessEqual(p.stat().st_size, 200)
            self.assertLess(written, 50)
            self.assertTrue(fd.status()["stopped"])

    def test_writer_stops_after_cap_and_keeps_data(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.jsonl"
            fd._state["path"] = str(p)
            saved = fd.MAX_RECORDS
            fd.MAX_RECORDS = 3
            try:
                for _ in range(10):
                    fd.record({"batteryVoltage": 52.0})
            finally:
                fd.MAX_RECORDS = saved
            # No rotation, no deletion: the file still holds exactly 3.
            self.assertEqual(len(p.read_text().strip().splitlines()), 3)


class RecordShapeTests(unittest.TestCase):
    def setUp(self):
        fd.reset()

    def test_record_has_timestamp_and_only_f_key(self):
        with tempfile.TemporaryDirectory() as d:
            fd._state["path"] = str(Path(d) / "s.jsonl")
            ts = datetime(2026, 10, 20, 5, 0, 0, tzinfo=timezone.utc)
            fd.record({"batteryVoltage": 52.8}, received_at=ts)
            rec = json.loads((Path(d) / "s.jsonl").read_text().strip())
            self.assertEqual(set(rec), {"t", "f"})
            self.assertEqual(rec["t"], ts.isoformat())

    def test_empty_raw_is_noop(self):
        self.assertFalse(fd.record({}))

    def test_nested_value_shape_is_unwrapped(self):
        """The cloud returns raw fields as {"value": X}. api.py reads
        item.get("value") when the item is a dict. The diagnostic must
        unwrap that or it records nothing at all.
        """
        with tempfile.TemporaryDirectory() as d:
            fd._state["path"] = str(Path(d) / "s.jsonl")
            ok = fd.record({
                "batteryVoltage": {"value": 52.8},
                "batterySoc": {"value": 88.0},
                "gridVoltage": 231.3,
            })
            self.assertTrue(ok)
            rec = json.loads((Path(d) / "s.jsonl").read_text().strip())
            self.assertEqual(rec["f"]["batteryVoltage"], 52.8)
            self.assertEqual(rec["f"]["batterySoc"], 88.0)
            self.assertEqual(rec["f"]["gridVoltage"], 231.3)


class UnitHintTests(unittest.TestCase):
    def test_every_allowed_numeric_field_has_a_unit_hint(self):
        numeric = {k for k in fd.ALLOWED_FIELDS if not k.endswith(
            ("workingMode", "deviceMode", "outputSourcePriority", "chargerSourcePriority"))}
        missing = numeric - set(fd.UNIT_HINTS)
        self.assertEqual(missing, set(),
                         f"fields without a unit hint: {sorted(missing)}")

    def test_unit_hints_cover_only_allowed_fields(self):
        self.assertEqual(set(fd.UNIT_HINTS) - set(fd.ALLOWED_FIELDS), set())


if __name__ == "__main__":
    unittest.main()