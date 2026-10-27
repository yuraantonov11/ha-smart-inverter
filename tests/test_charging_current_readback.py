"""Charging controls must show device readback, not fabricated defaults."""
import asyncio
import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
import hems.battery_limits

@pytest.fixture
def number_module(monkeypatch):
    names = ["homeassistant", "homeassistant.components", "homeassistant.components.number",
             "homeassistant.config_entries", "homeassistant.const", "homeassistant.core",
             "homeassistant.helpers", "homeassistant.helpers.entity_platform",
             "homeassistant.helpers.update_coordinator", "fake_powmr", "fake_powmr.hems",
             "fake_powmr.const", "fake_powmr.coordinator"]
    modules = {name: types.ModuleType(name) for name in names}
    modules["fake_powmr"].__path__ = []
    modules["fake_powmr.hems"].__path__ = []
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setitem(sys.modules, "fake_powmr.hems.battery_limits", hems.battery_limits)
    class CoordinatorEntity:
        def __init__(self, coordinator): self.coordinator = coordinator
        def async_write_ha_state(self): pass
    class NumberEntity: pass
    number = modules["homeassistant.components.number"]
    number.NumberEntity = NumberEntity
    number.NumberEntityDescription = lambda **kw: types.SimpleNamespace(**kw)
    number.NumberMode = types.SimpleNamespace(BOX="box")
    modules["homeassistant.config_entries"].ConfigEntry = object
    modules["homeassistant.const"].UnitOfElectricCurrent = types.SimpleNamespace(AMPERE="A")
    modules["homeassistant.const"].UnitOfElectricPotential = types.SimpleNamespace(VOLT="V")
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.helpers.entity_platform"].AddEntitiesCallback = object
    modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = CoordinatorEntity
    modules["fake_powmr.const"].DOMAIN = "powmr_inverter"
    modules["fake_powmr.coordinator"].InverterCoordinator = object
    spec = importlib.util.spec_from_file_location("fake_powmr.number", Path(__file__).resolve().parents[1] / "number.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

@pytest.mark.parametrize("klass,key", [("InverterMaxChargingCurrent", "setMaxChargingCurrent"),
                                     ("InverterMaxUtilityChargingCurrent", "setUtilityMaxChargingCurrent")])
def test_entities_only_display_recent_device_values(number_module, klass, key):
    api = types.SimpleNamespace(device_sn="device", last_device_configs_received_at=None)
    coord = types.SimpleNamespace(api=api, data={"deviceSettings": {}}, _device_settings_snapshot={}, _device_settings_received_at=None)
    entity = getattr(number_module, klass)(coord)
    assert entity.native_value is None
    coord._device_settings_snapshot[key] = {"value": 40, "unit": "A"}
    coord._device_settings_received_at = datetime.now(timezone.utc)
    assert entity.native_value == 40
    coord._device_settings_received_at -= timedelta(minutes=4)
    assert entity.native_value is None

def test_successful_write_requests_readback_instead_of_assuming_it(number_module):
    api = types.SimpleNamespace(device_sn="device", last_device_configs_received_at=None,
                                set_max_charging_current=AsyncMock(return_value=True))
    coord = types.SimpleNamespace(api=api, data={"deviceSettings": {}}, _device_settings_snapshot={}, _device_settings_received_at=None, async_request_refresh=AsyncMock())
    entity = number_module.InverterMaxChargingCurrent(coord)
    asyncio.run(entity.async_set_native_value(40))
    api.set_max_charging_current.assert_awaited_once_with(40)
    coord.async_request_refresh.assert_awaited_once()
    assert entity.native_value is None
