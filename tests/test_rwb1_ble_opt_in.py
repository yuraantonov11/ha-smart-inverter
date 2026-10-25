"""Tests for ``SourceMode.BLE`` opt-in and the integration with the
:mod:`telemetry_sources.selection` per-field allow-list.

These tests intentionally avoid every byte of live BLE/I/O: there are no real
MAC addresses, no real DTU identifiers and no real keys. The provider
identities are plain strings.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from telemetry_sources import ble_policies
from telemetry_sources.ble_policies import ble_policies as ble_policies_fn
from telemetry_sources.models import (
    FieldObservation,
    SourceFrame,
    SourceIdentity,
    SourceKind,
    ValueQuality,
)
from telemetry_sources.router import TelemetrySourceRouter
from telemetry_sources.selection import FieldPolicy, select_fields
from telemetry_sources.settings import (
    BLE_PROVIDER_ID,
    CLOUD_PROVIDER_ID,
    EXPERIMENTAL_BLE_CONSENT_FLAG,
    SourceMode,
    TelemetrySettings,
    validate_telemetry_settings,
)


NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
DEVICE_ID = "inverter-1"


def make_identity(provider_id: str) -> SourceIdentity:
    return SourceIdentity(provider_id, DEVICE_ID, SourceKind.BLE if provider_id == BLE_PROVIDER_ID else SourceKind.CLOUD)


def obs(value: float) -> FieldObservation:
    return FieldObservation(value=value, unit="W", received_at=NOW, measured_at=None, quality=ValueQuality.MEASURED)


def frame(provider_id: str, fields: dict[str, float]) -> SourceFrame:
    return SourceFrame(make_identity(provider_id), NOW, {
        name: obs(value) for name, value in fields.items()
    })


class SourceModeBleValidationTests(unittest.TestCase):
    def test_ble_mode_value_exists(self):
        self.assertEqual(SourceMode.BLE.value, "ble")

    def test_ble_mode_requires_consent(self):
        with self.assertRaisesRegex(ValueError, "consent"):
            validate_telemetry_settings({
                "mode": "ble", "collector_id": BLE_PROVIDER_ID,
            })

    def test_ble_mode_requires_distinct_provider_id(self):
        for bad in (None, "", "   ", " powmr_cloud ", BLE_PROVIDER_ID + " ", CLOUD_PROVIDER_ID):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_telemetry_settings({
                    "mode": "ble",
                    "collector_id": bad,
                    EXPERIMENTAL_BLE_CONSENT_FLAG: True,
                })

    def test_ble_mode_provider_id_cannot_be_cloud(self):
        with self.assertRaises(ValueError):
            validate_telemetry_settings({
                "mode": "ble",
                "collector_id": CLOUD_PROVIDER_ID,
                EXPERIMENTAL_BLE_CONSENT_FLAG: True,
            })

    def test_ble_mode_consent_must_be_truthy(self):
        with self.assertRaises(ValueError):
            validate_telemetry_settings({
                "mode": "ble",
                "collector_id": BLE_PROVIDER_ID,
                EXPERIMENTAL_BLE_CONSENT_FLAG: False,
            })
        # Strings like "true" must not silently coerce: the data type is bool.
        with self.assertRaises((ValueError, TypeError)):
            validate_telemetry_settings({
                "mode": "ble",
                "collector_id": BLE_PROVIDER_ID,
                EXPERIMENTAL_BLE_CONSENT_FLAG: "true",
            })

    def test_ble_mode_accepts_consented_opt_in(self):
        settings = validate_telemetry_settings({
            "mode": "ble",
            "collector_id": BLE_PROVIDER_ID,
            EXPERIMENTAL_BLE_CONSENT_FLAG: True,
        })
        self.assertEqual(settings.mode, SourceMode.BLE)
        self.assertIs(settings.experimental_ble_consent, True)

    def test_ble_mode_rejects_unknown_consent_key_via_dataclass(self):
        # Pass a non-boolean via *constructor* path (dataclass __post_init__).
        with self.assertRaises(ValueError):
            TelemetrySettings(
                mode=SourceMode.BLE,
                collector_id=BLE_PROVIDER_ID,
                experimental_ble_consent="yes",
            )


class SourceModeBleProviderOrderTests(unittest.TestCase):
    def test_cloud_is_always_primary_in_ble_mode(self):
        """Security guarantee: BLE never reads first by default in any config."""
        settings = validate_telemetry_settings({
            "mode": "ble",
            "collector_id": BLE_PROVIDER_ID,
            EXPERIMENTAL_BLE_CONSENT_FLAG: True,
        })
        order = settings.provider_order
        self.assertEqual(order[0], CLOUD_PROVIDER_ID)
        self.assertEqual(order[1], BLE_PROVIDER_ID)
        self.assertNotEqual(order[0], BLE_PROVIDER_ID)

    def test_ble_provider_order_remains_cloud_first_with_cloud_fallback_false(self):
        """Even when cloud_fallback is disabled, BLE mode keeps cloud first.

        The whole point of BLE mode is that BLE can never silently win; the
        only way to opt out of cloud here is to use SourceMode.UNAVAILABLE.
        """
        settings = validate_telemetry_settings({
            "mode": "ble",
            "collector_id": BLE_PROVIDER_ID,
            EXPERIMENTAL_BLE_CONSENT_FLAG: True,
            "cloud_fallback": False,
        })
        order = settings.provider_order
        self.assertEqual(order, (CLOUD_PROVIDER_ID, BLE_PROVIDER_ID))

    def test_unavailable_returns_empty_provider_order(self):
        settings = TelemetrySettings(mode=SourceMode.UNAVAILABLE, cloud_fallback=False)
        self.assertEqual(settings.provider_order, ())

    def test_cloud_only_mode_keeps_cloud_alone(self):
        settings = TelemetrySettings()
        self.assertEqual(settings.provider_order, (CLOUD_PROVIDER_ID,))

    def test_router_mode_unchanged(self):
        settings = validate_telemetry_settings({
            "mode": "router", "collector_id": "router-a",
        })
        self.assertEqual(settings.provider_order, ("router-a", CLOUD_PROVIDER_ID))


class SourceModeBleIntegrationTests(unittest.TestCase):
    def test_ble_provider_cannot_contribute_disallowed_field_via_select_fields(self):
        """End-to-end: even if BLE comes second in priority order, the
        allowed_providers gate independently blocks SOC/load/pv_power."""
        policies = ble_policies_fn()
        frames = [
            frame("powmr_cloud", {
                "grid_voltage": 231.8, "battery_voltage": 57.8,
                "battery_soc": 95.0, "load_power": 398.0, "pv_power": 0.0,
            }),
            frame(BLE_PROVIDER_ID, {
                "grid_voltage": 232.0, "battery_voltage": 58.1,
                "battery_soc": 100.0, "load_power": 301.0, "pv_power": 0.0,
            }),
        ]
        result = select_fields(
            frames, device_id=DEVICE_ID, now=NOW,
            priority={CLOUD_PROVIDER_ID: 0, BLE_PROVIDER_ID: 1},
            policies=policies,
        )
        # Allowed fields take the (closest to cloud) cloud values; BLE excluded.
        self.assertEqual(result.fields["grid_voltage"].value, 231.8)
        self.assertEqual(result.fields["battery_voltage"].value, 57.8)
        self.assertEqual(result.fields["battery_soc"].value, 95.0)
        self.assertEqual(result.fields["load_power"].value, 398.0)
        self.assertEqual(result.fields["pv_power"].value, 0.0)
        self.assertEqual(result.sources["battery_soc"].provider_id, CLOUD_PROVIDER_ID)
        self.assertEqual(result.sources["load_power"].provider_id, CLOUD_PROVIDER_ID)
        # Excluded log proves the gate fired for each blocked field.
        for blocked in ("battery_soc", "load_power", "pv_power"):
            self.assertIn(blocked, result.excluded, msg=f"{blocked} must be excluded")
            joined = " ".join(result.excluded[blocked])
            self.assertIn(BLE_PROVIDER_ID, joined)
            self.assertIn("not allowed", joined)

    def test_ble_loses_to_cloud_by_priority_even_when_field_is_open_to_both(self):
        """Priority alone (not allowed_providers) still keeps cloud primary.

        Demonstrates that BLE-in-First position would only be reachable by
        toggling provider_order in code; we have not.
        """
        frames = [
            frame("powmr_cloud", {"grid_voltage": 231.8}),
            frame(BLE_PROVIDER_ID, {"grid_voltage": 232.0}),
        ]
        result = select_fields(
            frames, device_id=DEVICE_ID, now=NOW,
            priority={CLOUD_PROVIDER_ID: 0, BLE_PROVIDER_ID: 1},
            policies={"grid_voltage": ble_policies_fn()["grid_voltage"]},
        )
        self.assertEqual(result.fields["grid_voltage"].value, 231.8)
        self.assertEqual(result.sources["grid_voltage"].provider_id, CLOUD_PROVIDER_ID)

    def test_router_collects_two_providers_and_does_not_poll_unconfigured_ble(self):
        """Router composition: BLE is *not* a provider_order member in ROUTER mode,
        therefore even if injected into the router, BLE should not be polled.
        """
        settings = validate_telemetry_settings({
            "mode": "router", "collector_id": "router-a",
        })
        # Pretend two providers are wired up: an opted-in router and the cloud.
        router = TelemetrySourceRouter.from_settings(
            [
                _FakeProvider(CLOUD_PROVIDER_ID, frame_payload={
                    "pv_power": 100.0,
                }),
                _FakeProvider(BLE_PROVIDER_ID, frame_payload={
                    "pv_power": 999.0,
                }),
                _FakeProvider("router-a", frame_payload={
                    "pv_power": 200.0,
                }),
            ],
            settings=settings,
            policies={"pv_power": FieldPolicy(timedelta(seconds=10))},
        )
        result = _run(router.collect(device_id=DEVICE_ID, now=NOW))
        self.assertEqual(result.selection.fields["pv_power"].value, 200.0)
        # BLE was never in provider_order, so it must have been skipped.
        self.assertEqual(router.enabled_provider_ids, ("router-a", CLOUD_PROVIDER_ID))


class _FakeProvider:
    """Adapter exposing only what TelemetrySourceRouter needs."""

    def __init__(self, provider_id: str, frame_payload: dict[str, float] | None = None, error: Exception | None = None):
        self._provider_id = provider_id
        self._frame_payload = frame_payload
        self._error = error
        self.reads = 0

    @property
    def identity(self) -> SourceIdentity:
        return make_identity(self._provider_id)

    async def read(self) -> SourceFrame:
        self.reads += 1
        if self._error is not None:
            raise self._error
        assert self._frame_payload is not None
        return frame(self._provider_id, self._frame_payload)

    async def close(self) -> None:
        return None


def _run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("coroutine did not finish")


# Patch TelemetrySourceRouter to expose enabled provider ids for the
# integration test without leaking the property across the whole module.
_orig_from_settings = TelemetrySourceRouter.from_settings


@classmethod  # type: ignore[arg-type]
def _from_settings_with_exposed_order(cls, providers, *, settings, policies, required_fields=frozenset()):
    supplied = tuple(providers)
    by_id = {provider.identity.provider_id: provider for provider in supplied}
    enabled = tuple(by_id[key] for key in settings.provider_order if key in by_id)
    instance = cls(
        enabled,
        priority={provider.identity.provider_id: index for index, provider in enumerate(enabled)},
        policies=policies,
        required_fields=required_fields,
    )
    instance.enabled_provider_ids = tuple(provider.identity.provider_id for provider in enabled)  # type: ignore[attr-defined]
    return instance


TelemetrySourceRouter.from_settings = _from_settings_with_exposed_order  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main(verbosity=2)
