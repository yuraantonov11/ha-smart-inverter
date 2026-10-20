"""Contract tests for adapting fetched cloud data to canonical telemetry."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from telemetry_sources.cloud import cloud_payload_to_frame
from telemetry_sources.models import SourceKind, ValueQuality


NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone(timedelta(hours=3)))


class CloudTelemetryAdapterTests(unittest.TestCase):
    def test_maps_confirmed_cloud_keys_and_normalizes_receipt_time(self):
        frame = cloud_payload_to_frame(
            {
                "pvPower": "120", "loadPower": 450, "gridVoltage": 231.2,
                "batteryVoltage": 53.1, "batterySoc": 82,
            },
            device_id="serial-123", received_at=NOW,
        )
        self.assertEqual(frame.identity.provider_id, "powmr_cloud")
        self.assertEqual(frame.identity.device_id, "serial-123")
        self.assertEqual(frame.identity.kind, SourceKind.CLOUD)
        self.assertEqual(frame.received_at, datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(frame.fields["pv_power"].value, 120.0)
        self.assertEqual(frame.fields["load_power"].unit, "W")
        self.assertEqual(frame.fields["grid_voltage"].unit, "V")
        self.assertIsNone(frame.fields["battery_soc"].measured_at)
        self.assertEqual(frame.profile_id, "powmr-cloud-response-v1")

    def test_missing_invalid_and_out_of_range_values_remain_unknown(self):
        frame = cloud_payload_to_frame(
            {
                "pvPower": None, "loadPower": "unavailable", "gridVoltage": 999,
                "batteryVoltage": True, "batterySoc": 101,
            },
            device_id="serial-123", received_at=NOW,
        )
        self.assertEqual(set(frame.fields), {
            "pv_power", "load_power", "grid_voltage", "battery_voltage", "battery_soc",
        })
        for field in frame.fields.values():
            with self.subTest(unit=field.unit):
                self.assertIsNone(field.value)
                self.assertEqual(field.quality, ValueQuality.UNKNOWN)

    def test_rejects_naive_receipt_time(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            cloud_payload_to_frame({}, device_id="serial-123", received_at=datetime(2026, 10, 19))


if __name__ == "__main__":
    unittest.main(verbosity=2)
