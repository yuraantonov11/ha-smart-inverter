"""Tests for the per-field ``allowed_providers`` gate.

These cover the new :attr:`FieldPolicy.allowed_providers` attribute and prove
that a BLE observation cannot contribute a field the policy forbids, while
all existing test (allowed_providers=None) keeps its prior behaviour.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from telemetry_sources.models import (
    FieldObservation,
    SourceFrame,
    SourceIdentity,
    SourceKind,
    ValueQuality,
)
from telemetry_sources.selection import FieldPolicy, select_fields


NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
DEVICE_ID = "inverter-1"

ALLOWED = frozenset({"powmr_cloud"})
ONLY_BLE = frozenset({"rwb1_ble"})


def make_identity(provider_id: str) -> SourceIdentity:
    return SourceIdentity(provider_id, DEVICE_ID, SourceKind.GATEWAY)


def obs(value: float, *, quality: ValueQuality = ValueQuality.MEASURED) -> FieldObservation:
    return FieldObservation(value=value, unit="W", received_at=NOW, measured_at=None, quality=quality)


def frame(provider_id: str, fields: dict[str, FieldObservation]) -> SourceFrame:
    return SourceFrame(make_identity(provider_id), NOW, fields)


class FieldPolicyConstructorTests(unittest.TestCase):
    def test_default_allows_any_provider(self):
        policy = FieldPolicy(max_receipt_age=timedelta(seconds=5))
        self.assertIsNone(policy.allowed_providers)

    def test_allowed_providers_must_be_well_formed(self):
        for bad in ({}, frozenset({""}), frozenset({"  "}), frozenset({1}), None):
            with self.subTest(bad=bad):
                if bad is None:
                    # None is the documented "no restriction" value.
                    policy = FieldPolicy(
                        max_receipt_age=timedelta(seconds=5), allowed_providers=bad,
                    )
                    self.assertIsNone(policy.allowed_providers)
                else:
                    with self.assertRaises(ValueError):
                        FieldPolicy(
                            max_receipt_age=timedelta(seconds=5), allowed_providers=bad,
                        )


class FieldPolicyAllowedProvidersGateTests(unittest.TestCase):
    """Per-field source restriction enforced as the final gate in select_fields."""

    def test_ble_blocked_from_battery_soc_does_not_overwrite_cloud_value(self):
        """The original vulnerability: BLE-SOC must not win against cloud-SOC.

        Cloud policy says ``battery_soc`` may come from any provider; we now
        narrow that to ``{powmr_cloud}`` and assert BLE is excluded.
        """
        policy = FieldPolicy(
            max_receipt_age=timedelta(seconds=10),
            max_measurement_age=timedelta(seconds=30),
            allowed_providers=ALLOWED,
        )
        frames = [
            frame("powmr_cloud", {"battery_soc": obs(95.0, quality=ValueQuality.MEASURED)}),
            frame("rwb1_ble", {"battery_soc": obs(100.0, quality=ValueQuality.MEASURED)}),
        ]
        result = select_fields(
            frames, device_id=DEVICE_ID, now=NOW,
            priority={"powmr_cloud": 0, "rwb1_ble": 0},
            policies={"battery_soc": policy},
        )
        self.assertEqual(result.fields["battery_soc"].value, 95.0)
        self.assertEqual(result.sources["battery_soc"].provider_id, "powmr_cloud")
        self.assertIn("battery_soc", result.excluded)
        reasons = " ".join(result.excluded["battery_soc"])
        self.assertIn("rwb1_ble", reasons)
        self.assertIn("not allowed", reasons)

    def test_ble_allowed_field_uses_normal_priority_and_excluded_is_clean(self):
        """When BLE is on the allow-list it must contribute, with no exclusion."""
        policy = FieldPolicy(
            max_receipt_age=timedelta(seconds=10),
            max_measurement_age=timedelta(seconds=30),
            allowed_providers=frozenset({"powmr_cloud", "rwb1_ble"}),
        )
        frames = [
            frame("powmr_cloud", {"grid_voltage": obs(231.8)}),
            frame("rwb1_ble", {"grid_voltage": obs(232.0)}),
        ]
        result = select_fields(
            frames, device_id=DEVICE_ID, now=NOW,
            priority={"powmr_cloud": 0, "rwb1_ble": 1},
            policies={"grid_voltage": policy},
        )
        self.assertEqual(result.fields["grid_voltage"].value, 231.8)
        self.assertEqual(result.sources["grid_voltage"].provider_id, "powmr_cloud")
        self.assertNotIn("grid_voltage", result.excluded)

    def test_cloud_only_field_with_only_ble_frame_is_reported_as_missing(self):
        """No candidate passes -> missing_required is populated, excluded has the reason."""
        policy = FieldPolicy(
            max_receipt_age=timedelta(seconds=10),
            max_measurement_age=timedelta(seconds=30),
            allowed_providers=ALLOWED,
        )
        frames = [frame("rwb1_ble", {"load_power": obs(301.0)})]
        result = select_fields(
            frames, device_id=DEVICE_ID, now=NOW,
            priority={"rwb1_ble": 0},
            policies={"load_power": policy},
            required_fields=frozenset({"load_power"}),
        )
        self.assertEqual(result.missing_required, frozenset({"load_power"}))
        self.assertNotIn("load_power", result.fields)
        self.assertEqual(len(result.excluded.get("load_power", ())), 1)
        self.assertIn("rwb1_ble: provider is not allowed for this field",
                      result.excluded["load_power"][0])

    def test_ble_only_field_with_only_cloud_frame_keeps_ble_available(self):
        """Inverse test: BLE-only allow-list still blocks cloud contributions."""
        policy = FieldPolicy(
            max_receipt_age=timedelta(seconds=10),
            max_measurement_age=timedelta(seconds=30),
            allowed_providers=ONLY_BLE,
        )
        frames = [
            frame("powmr_cloud", {"grid_voltage": obs(231.8)}),
            frame("rwb1_ble", {"grid_voltage": obs(232.0)}),
        ]
        result = select_fields(
            frames, device_id=DEVICE_ID, now=NOW,
            priority={"powmr_cloud": 0, "rwb1_ble": 1},
            policies={"grid_voltage": policy},
        )
        self.assertEqual(result.fields["grid_voltage"].value, 232.0)
        self.assertEqual(result.sources["grid_voltage"].provider_id, "rwb1_ble")
        self.assertIn("powmr_cloud: provider is not allowed for this field",
                      result.excluded["grid_voltage"][0])

    def test_default_policy_without_allowed_providers_does_not_restrict(self):
        """Regression: existing tests set ``allowed_providers`` to None implicitly."""
        policy = FieldPolicy(max_receipt_age=timedelta(seconds=10))
        frames = [
            frame("powmr_cloud", {"grid_voltage": obs(231.8)}),
            frame("rwb1_ble", {"grid_voltage": obs(232.0)}),
        ]
        result = select_fields(
            frames, device_id=DEVICE_ID, now=NOW,
            priority={"powmr_cloud": 0, "rwb1_ble": 0},
            policies={"grid_voltage": policy},
        )
        # No restriction; lower priority number wins, so a deterministic tie
        # resolution by received_at goes to rwb1_ble (same received_at).
        self.assertIn("grid_voltage", result.fields)
        self.assertNotIn("grid_voltage", result.excluded)

    def test_blocked_provider_still_appears_in_excluded_with_provider_prefix(self):
        """Exclusion log keeps the provider_id so the diagnostics stay actionable."""
        policy = FieldPolicy(
            max_receipt_age=timedelta(seconds=10),
            max_measurement_age=timedelta(seconds=30),
            allowed_providers=ALLOWED,
        )
        frames = [frame("rwb1_ble", {"battery_soc": obs(86.0, quality=ValueQuality.MEASURED)})]
        result = select_fields(
            frames, device_id=DEVICE_ID, now=NOW,
            priority={"rwb1_ble": 0},
            policies={"battery_soc": policy},
        )
        # Exclusion reason must reference the blocked provider so operators
        # understand why the value was dropped.
        self.assertEqual(len(result.excluded.get("battery_soc", ())), 1)
        self.assertTrue(result.excluded["battery_soc"][0].startswith("rwb1_ble:"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
