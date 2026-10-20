"""Cloud-default and explicit source/fallback consent contracts."""

from __future__ import annotations

import unittest
from datetime import timedelta

from telemetry_sources.router import TelemetrySourceRouter
from telemetry_sources.selection import FieldPolicy
from telemetry_sources.settings import (
    SourceMode, TelemetrySettings, load_telemetry_settings, validate_telemetry_settings,
)
from tests.test_telemetry_source_router import FakeProvider, NOW, frame, run


class CountingProvider(FakeProvider):
    reads = 0

    async def read(self):
        self.reads += 1
        return await super().read()


class TelemetrySettingsTests(unittest.TestCase):
    def test_existing_options_remain_cloud_only_without_migration_or_mutation(self):
        options = {"poll_interval": 5, "hems_enabled": True}
        loaded = load_telemetry_settings(options)
        self.assertEqual(loaded.settings.provider_order, ("powmr_cloud",))
        self.assertIsNone(loaded.issue)
        self.assertNotIn("telemetry", options)

    def test_cloud_choice_ignores_disabled_collector_leftovers(self):
        settings = validate_telemetry_settings({
            "mode": "cloud", "collector_id": None, "cloud_fallback": "stale-invalid-value",
        })
        self.assertEqual(settings, TelemetrySettings())

    def test_invalid_submission_rejected_but_saved_optional_config_does_not_crash(self):
        for data in ({"mode": "typo"}, {"mode": "router"}, {"mode": "unavailable"}):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    validate_telemetry_settings(data)
                loaded = load_telemetry_settings({"telemetry": data})
                self.assertEqual(loaded.issue, "invalid_telemetry_settings")
                self.assertEqual(loaded.settings.mode, SourceMode.CLOUD)
        loaded = load_telemetry_settings({"telemetry": ["broken"]})
        self.assertEqual(loaded.issue, "invalid_telemetry_settings")

    def test_router_requires_distinct_id_and_boolean_fallback(self):
        for collector in (None, "", " ", " bad ", "powmr_cloud"):
            with self.subTest(collector=collector), self.assertRaises(ValueError):
                validate_telemetry_settings({"mode": "router", "collector_id": collector})
        with self.assertRaises(ValueError):
            validate_telemetry_settings({
                "mode": "router", "collector_id": "router-a", "cloud_fallback": "false",
            })

    def test_corrupt_saved_local_options_preserve_explicit_cloud_opt_out(self):
        loaded = load_telemetry_settings({"telemetry": {
            "mode": "router", "collector_id": None, "cloud_fallback": False,
        }})
        self.assertEqual(loaded.issue, "invalid_telemetry_settings")
        self.assertEqual(loaded.settings.provider_order, ())


class SourceOptInBehaviorTests(unittest.TestCase):
    def collect(self, settings, providers):
        router = TelemetrySourceRouter.from_settings(
            providers,
            settings=settings,
            policies={"pv_power": FieldPolicy(timedelta(seconds=10))},
            required_fields=frozenset({"pv_power"}),
        )
        return run(router.collect(device_id="inverter-1", now=NOW))

    def test_default_never_reads_router_even_when_cloud_fails(self):
        cloud = CountingProvider("powmr_cloud", error=TimeoutError("offline"))
        local = CountingProvider("router-a", frame("router-a", {"pv_power": 999}))
        result = self.collect(TelemetrySettings(), [cloud, local])
        self.assertEqual(cloud.reads, 1)
        self.assertEqual(local.reads, 0)
        self.assertEqual(result.selection.missing_required, frozenset({"pv_power"}))

    def test_opted_in_router_has_priority_and_ignores_unconfigured_collector(self):
        settings = validate_telemetry_settings({"mode": "router", "collector_id": "router-a"})
        cloud = CountingProvider("powmr_cloud", frame("powmr_cloud", {"pv_power": 100}))
        local = CountingProvider("router-a", frame("router-a", {"pv_power": 200}))
        other = CountingProvider("router-other", frame("router-other", {"pv_power": 999}))
        result = self.collect(settings, [cloud, other, local])
        self.assertEqual(result.selection.fields["pv_power"].value, 200)
        self.assertEqual(result.selection.sources["pv_power"].provider_id, "router-a")
        self.assertEqual(other.reads, 0)

    def test_permitted_fallback_uses_cloud_after_local_failure(self):
        settings = validate_telemetry_settings({"mode": "router", "collector_id": "router-a"})
        cloud = CountingProvider("powmr_cloud", frame("powmr_cloud", {"pv_power": 100}))
        local = CountingProvider("router-a", error=TimeoutError("offline"))
        result = self.collect(settings, [cloud, local])
        self.assertEqual(result.selection.sources["pv_power"].provider_id, "powmr_cloud")
        self.assertEqual(result.provider_failures[0].provider_id, "router-a")

    def test_cloud_opt_out_does_not_poll_cloud_on_local_failure(self):
        settings = validate_telemetry_settings({
            "mode": "router", "collector_id": "router-a", "cloud_fallback": False,
        })
        cloud = CountingProvider("powmr_cloud", frame("powmr_cloud", {"pv_power": 100}))
        local = CountingProvider("router-a", error=TimeoutError("offline"))
        result = self.collect(settings, [cloud, local])
        self.assertEqual(cloud.reads, 0)
        self.assertEqual(result.selection.missing_required, frozenset({"pv_power"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
