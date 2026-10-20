"""Behavioral tests for transport-neutral source routing."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from telemetry_sources.models import (
    FieldObservation, SourceFrame, SourceIdentity, SourceKind, ValueQuality,
)
from telemetry_sources.router import TelemetrySourceRouter
from telemetry_sources.selection import FieldPolicy


NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("fake providers must complete without external I/O")


def identity(provider_id: str) -> SourceIdentity:
    return SourceIdentity(provider_id, "inverter-1", SourceKind.GATEWAY)


def frame(provider_id: str, values: dict[str, float], *, at=NOW) -> SourceFrame:
    source = identity(provider_id)
    fields = {
        name: FieldObservation(value, "W", at, None, ValueQuality.MEASURED)
        for name, value in values.items()
    }
    return SourceFrame(source, at, fields)


class FakeProvider:
    def __init__(self, provider_id: str, result=None, error=None, close_error=None, identity_override=None):
        self.identity = identity_override or identity(provider_id)
        self.result = result
        self.error = error
        self.close_error = close_error
        self.close_count = 0

    async def read(self):
        if self.error:
            raise self.error
        return self.result

    async def close(self):
        self.close_count += 1
        if self.close_error:
            raise self.close_error


class TelemetrySourceRouterTests(unittest.TestCase):
    def router(self, providers):
        return TelemetrySourceRouter(
            providers,
            priority={provider.identity.provider_id: index for index, provider in enumerate(providers)},
            policies={
                "pv_power": FieldPolicy(timedelta(seconds=10)),
                "load_power": FieldPolicy(timedelta(seconds=10)),
                "battery_soc": FieldPolicy(timedelta(seconds=10)),
            },
            required_fields=frozenset({"pv_power", "load_power", "battery_soc"}),
        )

    def test_freshness_failover_is_per_field_and_preserves_zero(self):
        stale = frame("primary", {"pv_power": 800, "load_power": 600}, at=NOW - timedelta(seconds=30))
        secondary = frame("backup", {"pv_power": 0, "load_power": 420, "battery_soc": 85})
        result = run(self.router([
            FakeProvider("primary", stale), FakeProvider("backup", secondary),
        ]).collect(device_id="inverter-1", now=NOW))

        self.assertEqual(result.selection.fields["pv_power"].value, 0.0)
        self.assertEqual(result.selection.fields["load_power"].value, 420.0)
        self.assertEqual(result.selection.sources["pv_power"].provider_id, "backup")
        self.assertEqual(result.selection.missing_required, frozenset())
        self.assertEqual(result.providers_read, 2)

    def test_one_provider_error_does_not_hide_another_provider(self):
        backup = FakeProvider("backup", frame("backup", {"pv_power": 150}))
        result = run(self.router([
            FakeProvider("offline", error=TimeoutError("secret endpoint detail")), backup,
        ]).collect(device_id="inverter-1", now=NOW))

        self.assertEqual(result.selection.fields["pv_power"].value, 150.0)
        self.assertEqual(result.selection.missing_required, frozenset({"load_power", "battery_soc"}))
        self.assertEqual(result.provider_failures[0].provider_id, "offline")
        self.assertEqual(result.provider_failures[0].error_type, "TimeoutError")
        self.assertNotIn("secret", repr(result.provider_failures))

    def test_identity_mismatch_is_excluded_and_reported_safely(self):
        provider = FakeProvider(
            "gateway", frame("other", {"pv_power": 100}),
        )
        result = run(self.router([provider]).collect(device_id="inverter-1", now=NOW))
        self.assertEqual(result.providers_read, 0)
        self.assertEqual(result.provider_failures[0].error_type, "IdentityMismatch")
        self.assertNotIn("pv_power", result.selection.fields)

    def test_close_attempts_all_providers_once_and_is_idempotent(self):
        first = FakeProvider("first", close_error=RuntimeError("detail"))
        second = FakeProvider("second")
        router = self.router([first, second])
        failures = run(router.close())
        self.assertEqual(first.close_count, 1)
        self.assertEqual(second.close_count, 1)
        self.assertEqual(failures[0].error_type, "RuntimeError")
        self.assertEqual(run(router.close()), ())
        with self.assertRaisesRegex(RuntimeError, "closed"):
            run(router.collect(device_id="inverter-1", now=NOW))

    def test_duplicate_provider_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            self.router([FakeProvider("same"), FakeProvider("same")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
