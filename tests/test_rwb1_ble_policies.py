"""Frozen-content tests for :func:`telemetry_sources.ble_policies.ble_policies`.

Policy position (Юра, 2026-10): every BLE field stays **diagnostic only**.
Proximity of two voltage readings is not yet sufficient validation for
control, so no field may be *selected* from BLE - not even the ones that
happened to corroborate against the cloud. BLE frames must stay *visible* for
diagnostics, which is what ``diagnostics`` on the selection result and
:func:`ble_diagnostic_values` provide.

These assert the exact mapping production wiring depends on, so an accidental
re-promotion of a field to BLE fails CI.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from telemetry_sources.ble_policies import (
    BLE_DIAGNOSTIC_FIELDS,
    ble_diagnostic_candidates,
    ble_diagnostic_values,
    ble_policies,
)
from telemetry_sources.models import (
    FieldObservation,
    SourceFrame,
    SourceIdentity,
    SourceKind,
    ValueQuality,
)
from telemetry_sources.rwb1_ble_profile import FIELD_BY_NAME
from telemetry_sources.selection import FieldPolicy, select_fields
from telemetry_sources.settings import BLE_PROVIDER_ID, CLOUD_PROVIDER_ID


CLOUD_AND_BLE = frozenset({CLOUD_PROVIDER_ID, BLE_PROVIDER_ID})
CLOUD_ONLY = frozenset({CLOUD_PROVIDER_ID})

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
DEVICE_ID = "inverter-1"


def frame(provider_id: str, fields: dict[str, float]) -> SourceFrame:
    identity = SourceIdentity(
        provider_id, DEVICE_ID, SourceKind.BLE if provider_id == BLE_PROVIDER_ID else SourceKind.CLOUD,
    )
    return SourceFrame(identity, NOW, {
        name: FieldObservation(
            value=value, unit="W", received_at=NOW, measured_at=None,
            quality=ValueQuality.MEASURED,
        )
        for name, value in fields.items()
    })


class BlePolicyContentTests(unittest.TestCase):
    def test_returns_a_mapping(self):
        policies = ble_policies()
        self.assertIsInstance(policies, dict)
        # Defensive: don't expose an accidentally-mutable shared instance.
        self.assertIsNot(policies, ble_policies())

    def test_grid_voltage_is_diagnostic_only(self):
        """Corroborated once against the cloud - not enough to drive control."""
        policies = ble_policies()
        self.assertIn("grid_voltage", policies)
        self.assertEqual(policies["grid_voltage"].allowed_providers, CLOUD_ONLY)
        self.assertNotIn(BLE_PROVIDER_ID, policies["grid_voltage"].allowed_providers)

    def test_battery_voltage_is_diagnostic_only(self):
        policies = ble_policies()
        self.assertIn("battery_voltage", policies)
        self.assertEqual(policies["battery_voltage"].allowed_providers, CLOUD_ONLY)
        self.assertNotIn(BLE_PROVIDER_ID, policies["battery_voltage"].allowed_providers)

    def test_battery_soc_is_blocked_for_ble(self):
        policies = ble_policies()
        self.assertIn("battery_soc", policies)
        self.assertEqual(policies["battery_soc"].allowed_providers, CLOUD_ONLY)
        self.assertNotIn(BLE_PROVIDER_ID, policies["battery_soc"].allowed_providers)

    def test_load_power_is_blocked_for_ble(self):
        policies = ble_policies()
        self.assertIn("load_power", policies)
        self.assertEqual(policies["load_power"].allowed_providers, CLOUD_ONLY)
        self.assertNotIn(BLE_PROVIDER_ID, policies["load_power"].allowed_providers)

    def test_pv_power_is_blocked_for_ble(self):
        """Same evidence class as load_power / battery_soc - diagnostic only."""
        policies = ble_policies()
        self.assertIn("pv_power", policies)
        self.assertEqual(policies["pv_power"].allowed_providers, CLOUD_ONLY)
        self.assertNotIn(BLE_PROVIDER_ID, policies["pv_power"].allowed_providers)

    def test_no_policy_can_ever_select_a_ble_value(self):
        """The gate itself refuses to hand out a BLE-allowing policy."""
        for name, policy in ble_policies().items():
            with self.subTest(field=name):
                self.assertNotIn(
                    BLE_PROVIDER_ID, policy.allowed_providers or frozenset(),
                    f"{name} would let BLE drive a decision",
                )

    def test_every_mapped_ble_field_has_a_policy(self):
        """Each field the transport can emit is governed explicitly."""
        self.assertEqual(set(ble_policies()), set(FIELD_BY_NAME))

    def test_battery_current_is_not_listed(self):
        """The field is unmapped in rwb1_ble_profile: decision deferred.

        Adding a default policy here would silently lock BLE out forever (or,
        worse, let unconfirmed data through). The omission is intentional.
        """
        policies = ble_policies()
        self.assertNotIn("battery_current", policies)

    def test_freshness_and_measurement_windows_are_uniform(self):
        """All declared policies use the same freshness baseline.

        Promoted fields keep the same gates without re-tuning.
        """
        for name, policy in ble_policies().items():
            with self.subTest(field=name):
                self.assertEqual(policy.max_receipt_age.total_seconds(), 10.0)
                self.assertEqual(policy.max_measurement_age.total_seconds(), 30.0)
                self.assertFalse(policy.require_measurement_time)

    def test_no_policy_advertises_an_unknown_provider(self):
        allowed_seen: set[str] = set()
        for policy in ble_policies().values():
            for entry in policy.allowed_providers or frozenset():
                allowed_seen.add(entry)
        self.assertTrue(allowed_seen.issubset({CLOUD_PROVIDER_ID, BLE_PROVIDER_ID}))


class DiagnosticVisibilityTests(unittest.TestCase):
    """BLE stays *visible* for diagnostics even though it is never selected."""

    BLE_VALUES = {
        "grid_voltage": 232.0,
        "battery_voltage": 58.1,
        "battery_soc": 100.0,
        "load_power": 301.0,
        "pv_power": 12.0,
    }
    CLOUD_VALUES = {
        "grid_voltage": 231.8,
        "battery_voltage": 57.8,
        "battery_soc": 95.0,
        "load_power": 398.0,
        "pv_power": 10.0,
    }

    def test_diagnostic_candidates_match_the_mapped_fields(self):
        self.assertEqual(BLE_DIAGNOSTIC_FIELDS, frozenset(FIELD_BY_NAME))
        self.assertEqual(ble_diagnostic_candidates(), frozenset(FIELD_BY_NAME))

    def test_no_field_is_ever_selected_from_ble(self):
        frames = [frame(CLOUD_PROVIDER_ID, self.CLOUD_VALUES), frame(BLE_PROVIDER_ID, self.BLE_VALUES)]
        result = select_fields(
            frames, device_id=DEVICE_ID, now=NOW,
            priority={CLOUD_PROVIDER_ID: 1, BLE_PROVIDER_ID: 0},
            policies=ble_policies(),
        )
        for name in FIELD_BY_NAME:
            with self.subTest(field=name):
                self.assertEqual(result.sources[name].provider_id, CLOUD_PROVIDER_ID)
                self.assertEqual(result.fields[name].value, self.CLOUD_VALUES[name])
                self.assertTrue(
                    any(BLE_PROVIDER_ID in reason for reason in result.excluded[name]),
                    f"{name}: the BLE exclusion reason must stay actionable",
                )

    def test_ble_only_capture_is_never_selected_but_stays_visible(self):
        result = select_fields(
            [frame(BLE_PROVIDER_ID, self.BLE_VALUES)], device_id=DEVICE_ID, now=NOW,
            priority={BLE_PROVIDER_ID: 0}, policies=ble_policies(),
            required_fields=frozenset(FIELD_BY_NAME),
        )
        self.assertEqual(result.fields, {})
        self.assertEqual(result.missing_required, frozenset(FIELD_BY_NAME))
        self.assertEqual(
            result.diagnostic_values,
            {},
            "diagnostics are opt-in: without the flag nothing is reported",
        )

    def test_diagnostic_values_are_reported_when_asked_for(self):
        result = select_fields(
            [frame(BLE_PROVIDER_ID, self.BLE_VALUES)], device_id=DEVICE_ID, now=NOW,
            priority={BLE_PROVIDER_ID: 0}, policies=ble_policies(),
            diagnostic_providers=(BLE_PROVIDER_ID,),
        )
        self.assertEqual(result.fields, {}, "diagnostics must never feed selection")
        self.assertEqual(set(result.diagnostic_values), set(FIELD_BY_NAME))
        self.assertEqual(result.diagnostic_values["grid_voltage"][0].provider_id, BLE_PROVIDER_ID)
        self.assertEqual(result.diagnostic_values["grid_voltage"][0].observation.value, 232.0)

    def test_diagnostic_values_helper_reads_ble_frames_directly(self):
        values = ble_diagnostic_values(
            [frame(BLE_PROVIDER_ID, self.BLE_VALUES)], device_id=DEVICE_ID,
        )
        self.assertEqual(set(values), set(FIELD_BY_NAME))
        self.assertEqual(values["battery_soc"].value, 100.0)

    def test_diagnostic_values_helper_ignores_other_devices_and_providers(self):
        self.assertEqual(ble_diagnostic_values([], device_id=DEVICE_ID), {})
        self.assertEqual(
            ble_diagnostic_values(
                [frame(CLOUD_PROVIDER_ID, self.CLOUD_VALUES)], device_id=DEVICE_ID,
            ),
            {},
        )
        other = SourceIdentity(BLE_PROVIDER_ID, "another-inverter", SourceKind.BLE)
        alien = SourceFrame(other, NOW, {})
        self.assertEqual(ble_diagnostic_values([alien], device_id=DEVICE_ID), {})


class SelectionGateMechanicsTests(unittest.TestCase):
    """The generic gate still supports other provider combinations.

    ``ble_policies`` is what pins BLE out; the gate itself remains a general
    mechanism so a future, genuinely validated field can be promoted by
    editing one table.
    """

    def test_a_policy_may_admit_both_providers_for_other_sources(self):
        policy = FieldPolicy(
            max_receipt_age=timedelta(seconds=10), allowed_providers=CLOUD_AND_BLE,
        )
        result = select_fields(
            [frame(CLOUD_PROVIDER_ID, {"grid_voltage": 231.8}),
             frame(BLE_PROVIDER_ID, {"grid_voltage": 232.0})],
            device_id=DEVICE_ID, now=NOW,
            priority={CLOUD_PROVIDER_ID: 0, BLE_PROVIDER_ID: 1},
            policies={"grid_voltage": policy},
        )
        self.assertEqual(result.sources["grid_voltage"].provider_id, CLOUD_PROVIDER_ID)
        self.assertNotIn("grid_voltage", result.excluded)

    def test_diagnostic_providers_do_not_bypass_a_provider_allow_list(self):
        """Asking for diagnostics never re-opens the selection gate."""
        policy = FieldPolicy(
            max_receipt_age=timedelta(seconds=10), allowed_providers=CLOUD_ONLY,
        )
        result = select_fields(
            [frame(BLE_PROVIDER_ID, {"load_power": 301.0})], device_id=DEVICE_ID, now=NOW,
            priority={BLE_PROVIDER_ID: 0}, policies={"load_power": policy},
            diagnostic_providers=(BLE_PROVIDER_ID,),
        )
        self.assertEqual(result.fields, {})
        self.assertEqual(
            result.diagnostic_values["load_power"][0].observation.value, 301.0,
        )

    def test_stale_observations_are_never_reported_as_diagnostics(self):
        stale = FieldObservation(
            value=301.0, unit="W", received_at=NOW - timedelta(seconds=30), measured_at=None,
            quality=ValueQuality.MEASURED,
        )
        identity = SourceIdentity(BLE_PROVIDER_ID, DEVICE_ID, SourceKind.BLE)
        result = select_fields(
            [SourceFrame(identity, stale.received_at, {"load_power": stale})],
            device_id=DEVICE_ID, now=NOW,
            priority={BLE_PROVIDER_ID: 0}, policies=ble_policies(),
            diagnostic_providers=(BLE_PROVIDER_ID,),
        )
        self.assertEqual(result.fields, {})
        self.assertEqual(result.diagnostic_values, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)