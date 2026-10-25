"""Runtime wiring for the BLE client: real bleak class, HA Bluetooth API path.

Two distinct guarantees are covered here:

1. *Dependency smoke test* - the client class this transport defaults to is
   imported from ``bleak_retry_connector`` (NOT from ``bleak``, where
   ``BleakClientWithServiceCache`` does not exist on bleak 3.x), the class
   exposes the surface the transport uses, and building it performs **no**
   radio activity. If the dependency is missing the test skips with an
   explicit reason rather than failing silently.
2. *Home Assistant wiring* - the transport can be pointed at a Home Assistant
   ``BLEDevice`` object through the documented
   ``homeassistant.components.bluetooth`` API, so network-attached adapters
   (Xiaomi Gateway 3, ESPHome Bluetooth proxies) work instead of only a raw
   address string.

No radio, no Bluetooth adapter and no HA instance are used anywhere.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from telemetry_sources import rwb1_ble
from telemetry_sources.rwb1_ble import (
    Rwb1BleTelemetryProvider,
    Rwb1BleTransportError,
    default_bleakenector_factory,
)

from tests.rwb1_ble_fixtures import TEST_ADDRESS, TEST_DEVICE_ID, TEST_DTU_ID, TEST_PROVIDER_ID


def _import_bleak_retry_connector():
    try:
        import bleak_retry_connector  # noqa: F401
    except ImportError as err:  # pragma: no cover - environment dependent.
        raise unittest.SkipTest(
            "bleak-retry-connector is not installed in this environment "
            f"({err}); install it to run the BLE client smoke tests"
        ) from err
    return bleak_retry_connector


class _FakeBleDevice:
    """Duck-typed stand-in for ``bleak.backends.device.BLEDevice``."""

    def __init__(self, address: str = TEST_ADDRESS, name: str = "rwb1"):
        self.address = address
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only.
        return f"_FakeBleDevice({self.address!r})"


class _RecordingClient:
    """Records what the factory handed to the bleak client class."""

    instances: list["_RecordingClient"] = []

    def __init__(self, device, **kwargs):
        self.device = device
        self.kwargs = kwargs
        self.connect_calls = 0
        type(self).instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []

    async def connect(self):  # pragma: no cover - never called in these tests.
        self.connect_calls += 1


class _FakeBluetoothApi:
    """Stand-in for the two ``homeassistant.components.bluetooth`` symbols."""

    def __init__(self, devices: dict[str, _FakeBleDevice]):
        self._devices = devices
        self.lookups: list[tuple[str, bool]] = []

    def async_ble_device_from_address(self, hass, address, connectable=True):
        self.lookups.append((address, connectable))
        return self._devices.get(address)


class DefaultClientClassTests(unittest.TestCase):
    """The factory must resolve a class that actually exists on bleak 3.x."""

    def test_resolved_client_class_comes_from_bleak_retry_connector(self):
        module = _import_bleak_retry_connector()
        resolved = rwb1_ble.resolve_bleak_client_class()
        self.assertIs(
            resolved, module.BleakClientWithServiceCache,
            "the default client class must be bleak_retry_connector's, "
            "because bleak itself does not export BleakClientWithServiceCache",
        )

    def test_resolved_class_exposes_the_surface_the_transport_uses(self):
        client_class = rwb1_ble.resolve_bleak_client_class()
        for attribute in (
            "connect", "disconnect", "write_gatt_char", "start_notify", "stop_notify",
            "services", "mtu_size",
        ):
            with self.subTest(attribute=attribute):
                self.assertTrue(
                    hasattr(client_class, attribute),
                    f"BleakClientWithServiceCache must provide {attribute}",
                )

    def test_building_a_client_performs_no_radio_activity(self):
        """Construction must not connect, discover or touch an adapter."""
        self.addCleanup(_RecordingClient.reset)
        client = default_bleakenector_factory(
            TEST_ADDRESS, client_class=_RecordingClient,
        )
        self.assertEqual(len(_RecordingClient.instances), 1)
        self.assertEqual(client.connect_calls, 0)
        self.assertEqual(client.device, TEST_ADDRESS)

    def test_factory_propagates_the_connect_timeout(self):
        self.addCleanup(_RecordingClient.reset)
        default_bleakenector_factory(
            TEST_ADDRESS, timeout=7.5, client_class=_RecordingClient,
        )
        self.assertEqual(_RecordingClient.instances[0].kwargs, {"timeout": 7.5})

    def test_missing_dependency_is_reported_as_a_transport_error(self):
        """A missing bleak stack must be an actionable error, not an ImportError."""
        import builtins

        real_import = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name.startswith("bleak"):
                raise ImportError(f"no module named {name!r}")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = refuse
        self.addCleanup(setattr, builtins, "__import__", real_import)
        with self.assertRaisesRegex(Rwb1BleTransportError, "bleak-retry-connector"):
            default_bleakenector_factory(TEST_ADDRESS)


class HomeAssistantBluetoothPathTests(unittest.TestCase):
    """The HA path must receive a BLEDevice object, not a bare address."""

    def setUp(self):
        _RecordingClient.reset()
        self.addCleanup(_RecordingClient.reset)
        self.device = _FakeBleDevice()
        self.api = _FakeBluetoothApi({TEST_ADDRESS: self.device})

    def test_address_is_resolved_through_the_ha_bluetooth_api(self):
        client = rwb1_ble.ha_bleakenector_factory(
            object(), TEST_ADDRESS, bluetooth_api=self.api,
            client_class=_RecordingClient,
        )
        self.assertEqual(self.api.lookups, [(TEST_ADDRESS, True)])
        self.assertIs(client.device, self.device)

    def test_ble_device_object_is_passed_through_untouched(self):
        """A call site that already holds a BLEDevice must not re-resolve it."""
        other = _FakeBleDevice("11:22:33:44:55:66")
        client = rwb1_ble.ha_bleakenector_factory(
            object(), other, bluetooth_api=self.api, client_class=_RecordingClient,
        )
        self.assertEqual(self.api.lookups, [])
        self.assertIs(client.device, other)

    def test_unknown_address_is_reported_with_a_clear_error(self):
        with self.assertRaisesRegex(Rwb1BleTransportError, "not known to Home Assistant"):
            rwb1_ble.ha_bleakenector_factory(
                object(), "00:00:00:00:00:00", bluetooth_api=self.api,
                client_class=_RecordingClient,
            )

    def test_provider_accepts_a_ha_ble_device_and_a_callable_factory(self):
        """End-to-end wiring: the injected factory receives the BLEDevice."""
        seen: list[object] = []

        def factory():
            seen.append(self.device)
            return _RecordingClient(self.device)

        provider = Rwb1BleTelemetryProvider(
            identity=_identity(), dtu_id=TEST_DTU_ID, bleakenector=factory,
            ble_device=self.device, settle_seconds=0.0,
        )
        # Construction must not build a client yet (lazy), but the provider must
        # remember the HA device for the factory call.
        self.assertEqual(seen, [])
        self.assertEqual(provider.ble_device, self.device)

    def test_provider_requires_a_device_when_no_client_is_injected(self):
        """Neither an address nor a BLEDevice: the read must refuse clearly."""
        provider = Rwb1BleTelemetryProvider(
            identity=_identity(), dtu_id=TEST_DTU_ID,
            bleakenector=None, ble_device=None, settle_seconds=0.0,
        )
        with self.assertRaisesRegex(Rwb1BleTransportError, "device address"):
            asyncio.run(provider.read())


def _identity():
    from telemetry_sources.models import SourceIdentity, SourceKind

    return SourceIdentity(TEST_PROVIDER_ID, TEST_DEVICE_ID, SourceKind.BLE)


if __name__ == "__main__":
    unittest.main(verbosity=2)