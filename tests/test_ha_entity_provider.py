"""Unit tests for the transport-neutral Home Assistant state adapter."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from telemetry_sources.ha_entities import HomeAssistantEntityProvider
from telemetry_sources.models import SourceIdentity, SourceKind, ValueQuality


NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def run_provider(provider):
    """Run this in-memory coroutine without importing asyncio/socket here.

    The provider deliberately performs no I/O or awaits. Avoiding asyncio in
    this flat integration repository also avoids its top-level ``select.py``
    shadowing the Python standard-library module during isolated test imports.
    """
    coroutine = provider.read()
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("in-memory entity provider unexpectedly yielded")


@dataclass
class FakeState:
    state: str
    attributes: dict
    last_reported: datetime | None = NOW
    last_updated: datetime | None = NOW


class HomeAssistantEntityProviderTests(unittest.TestCase):
    def make_provider(self, states, **kwargs):
        identity = SourceIdentity("ha_entities", "inverter-1", SourceKind.HOME_ASSISTANT)
        return HomeAssistantEntityProvider(
            identity,
            states.get,
            {"battery_soc": "sensor.inverter_battery_soc", "pv_power": "sensor.inverter_pv_power"},
            {"battery_soc": "%", "pv_power": "W"},
            clock=lambda: NOW,
            **kwargs,
        )

    def test_reads_only_mapped_numeric_values_and_preserves_zero(self):
        provider = self.make_provider({
            "sensor.inverter_battery_soc": FakeState("90", {"unit_of_measurement": "%"}),
            "sensor.inverter_pv_power": FakeState("0", {"unit_of_measurement": "W"}),
            "sensor.unmapped": FakeState("1234", {"unit_of_measurement": "W"}),
        })
        frame = run_provider(provider)
        self.assertEqual(set(frame.fields), {"battery_soc", "pv_power"})
        self.assertEqual(frame.fields["battery_soc"].value, 90.0)
        self.assertEqual(frame.fields["pv_power"].value, 0.0)
        self.assertEqual(frame.fields["pv_power"].quality, ValueQuality.MEASURED)
        self.assertIsNone(frame.fields["pv_power"].measured_at)

    def test_missing_unavailable_invalid_and_wrong_unit_are_unknown(self):
        provider = self.make_provider({
            "sensor.inverter_battery_soc": FakeState("unavailable", {"unit_of_measurement": "%"}),
            "sensor.inverter_pv_power": FakeState("2.4", {"unit_of_measurement": "kW"}),
        })
        frame = run_provider(provider)
        self.assertIsNone(frame.fields["battery_soc"].value)
        self.assertIsNone(frame.fields["pv_power"].value)
        self.assertEqual(frame.fields["battery_soc"].quality, ValueQuality.UNKNOWN)
        self.assertEqual(frame.fields["pv_power"].quality, ValueQuality.UNKNOWN)

    def test_receipt_freshness_uses_ha_report_time_not_poll_time(self):
        reported = NOW - timedelta(seconds=23)
        provider = self.make_provider({
            "sensor.inverter_battery_soc": FakeState("80", {"unit_of_measurement": "%"}, reported),
            "sensor.inverter_pv_power": FakeState("250", {"unit_of_measurement": "W"}, reported),
        })
        frame = run_provider(provider)
        self.assertEqual(frame.fields["pv_power"].received_at, reported)
        self.assertEqual(frame.received_at, reported)

    def test_missing_or_naive_ha_timestamps_do_not_make_values_fresh(self):
        provider = self.make_provider({
            "sensor.inverter_battery_soc": FakeState(
                "80", {"unit_of_measurement": "%"}, None, None,
            ),
            "sensor.inverter_pv_power": FakeState(
                "250", {"unit_of_measurement": "W"},
                datetime(2026, 10, 19, 12, 0), datetime(2026, 10, 19, 12, 0),
            ),
        })
        frame = run_provider(provider)
        self.assertIsNone(frame.fields["battery_soc"].value)
        self.assertIsNone(frame.fields["pv_power"].value)
        self.assertEqual(frame.fields["pv_power"].quality, ValueQuality.UNKNOWN)

    def test_rejects_wrong_identity_or_incomplete_unit_map(self):
        with self.assertRaisesRegex(ValueError, "identity kind"):
            HomeAssistantEntityProvider(
                SourceIdentity("cloud", "inverter-1", SourceKind.CLOUD),
                lambda _: None, {"pv_power": "sensor.pv"}, {"pv_power": "W"},
            )
        with self.assertRaisesRegex(ValueError, "expected unit"):
            HomeAssistantEntityProvider(
                SourceIdentity("ha_entities", "inverter-1", SourceKind.HOME_ASSISTANT),
                lambda _: None, {"pv_power": "sensor.pv"}, {},
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
