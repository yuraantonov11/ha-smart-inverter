"""Exercise the production options flow without importing the HA runtime."""
from __future__ import annotations

import ast
import asyncio
import math
import runpy
import sys
import types
from pathlib import Path
from unittest.mock import patch

import voluptuous as vol

ROOT = Path(__file__).resolve().parent.parent


class FlowBase:
    def async_show_menu(self, **kwargs):
        return {"type": "menu", **kwargs}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}


def make_flow(options=None):
    tree = ast.parse((ROOT / "config_flow.py").read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if getattr(n, "name", None) in ("_predictive_schema", "InverterOptionsFlow")]
    validator_tree = ast.parse((ROOT / "hems/predictive_control.py").read_text(encoding="utf-8"))
    validator_nodes = [n for n in validator_tree.body
                       if getattr(n, "name", None) == "parse_predictive_options"
                       or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "DEFAULT_OPTIONS" for t in n.targets)]
    validator_ns = {"math": math}
    exec(compile(ast.Module(body=validator_nodes, type_ignores=[]), "<production-validator>", "exec"), validator_ns)
    namespace = runpy.run_path(str(ROOT / "const.py"))
    namespace.update(__name__="options_categories.flow", __package__="options_categories",
                     config_entries=types.SimpleNamespace(OptionsFlow=FlowBase),
                     vol=vol, Any=object, FlowResult=dict, math=math,
                     parse_predictive_options=validator_ns["parse_predictive_options"])
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<production-options>", "exec"), namespace)
    flow = namespace["InverterOptionsFlow"]()
    flow.config_entry = types.SimpleNamespace(options=dict(options or {}))
    flow.hass = types.SimpleNamespace(config_entries=types.SimpleNamespace(
        async_update_entry=lambda entry, options: setattr(entry, "options", options)))
    return flow


def submit(flow, group, data):
    helpers = types.ModuleType("options_categories.hems.options_helpers")
    helpers.requires_reload = runpy.run_path(str(ROOT / "hems/options_helpers.py"))["requires_reload"]
    with patch.dict(sys.modules, {"options_categories.hems.options_helpers": helpers}):
        return asyncio.run(getattr(flow, "async_step_" + group)(data))


def test_menu_and_six_forms_cover_all_26_options_once():
    flow = make_flow()
    menu = asyncio.run(flow.async_step_init())
    assert menu["type"] == "menu"
    keys = []
    for group in menu["menu_options"]:
        flow._group = group
        form = asyncio.run(flow.async_step_init())
        keys += [k.schema for k in form["data_schema"].schema]
        assert form["step_id"] == group
    assert len(keys) == len(set(keys)) == 26
    assert "predictive_mode" in keys
    assert "max_discharge_power_w" in keys


def test_category_save_preserves_other_categories_and_internal_state():
    flow = make_flow({"tariff_day": 4.32, "reserve_soc": 20, "ble_dtu_id": "kept", "_energy_state": {"day": 3}})
    result = submit(flow, "tariffs", {"tariff_day": 5.0, "ble_dtu_id": "not a tariff field"})
    assert result["type"] == "abort"
    assert flow.config_entry.options == {"tariff_day": 5.0, "reserve_soc": 20, "ble_dtu_id": "kept", "_energy_state": {"day": 3}}


def test_validation_uses_saved_other_half_of_predictive_window():
    flow = make_flow({"predictive_night_window_start_hour": 22, "predictive_night_window_end_hour": 6})
    result = submit(flow, "predictive", {"predictive_night_window_start_hour": 6})
    assert result["type"] == "form"
    assert result["step_id"] == "predictive"
    assert result["errors"] == {"base": "invalid_predictive_options"}
    assert flow.config_entry.options["predictive_night_window_start_hour"] == 22


def test_unchanged_conf_defaults_do_not_become_options_overrides():
    flow = make_flow({"reserve_soc": 20})
    flow._connection_defaults = {"ble_diagnostic": True, "ble_dtu_id": "from-conf", "ble_read_interval": 15}
    result = submit(flow, "connection", dict(flow._connection_defaults))
    assert result["type"] == "abort"
    assert flow.config_entry.options == {"reserve_soc": 20}


def test_reload_sensitive_save_preserves_other_options():
    flow = make_flow({"poll_interval": 5, "reserve_soc": 20, "ble_dtu_id": "kept"})
    result = submit(flow, "station", {"site_latitude": 49.7, "site_longitude": 23.9})
    assert result["type"] == "create_entry"
    assert result["data"]["poll_interval"] == 5
    assert result["data"]["ble_dtu_id"] == "kept"
    assert result["data"]["site_latitude"] == 49.7


def test_connection_form_shows_effective_conf_values_and_preserves_source_on_save():
    flow = make_flow({"reserve_soc": 20})
    ble = types.ModuleType("options_categories.ble_diagnostic")
    async def read_conf(_hass):
        return {"enabled": "1", "dtu_id": "working-dtu", "interval": "15"}
    ble.read_ble_conf_async = read_conf
    ble.ble_is_enabled = lambda hass, entry: False
    ble.entry_option = lambda entry, key: entry.options.get(key)
    ble.ble_configured_interval = lambda entry, default: entry.options.get("ble_read_interval", default)
    with patch.dict(sys.modules, {"options_categories.ble_diagnostic": ble}):
        result = asyncio.run(flow.async_step_connection())
    defaults = {key.schema: key.default() for key in result["data_schema"].schema}
    assert defaults["ble_diagnostic"] is True
    assert defaults["ble_dtu_id"] == "working-dtu"
    assert defaults["ble_read_interval"] == 15
    # Editing cloud cadence does not silently migrate the displayed BLE fallback.
    submitted = dict(defaults, poll_interval=8)
    result = submit(flow, "connection", submitted)
    assert result["type"] == "create_entry"
    assert result["data"] == {"reserve_soc": 20, "poll_interval": 8}


def test_battery_form_surfaces_derived_capacity_without_new_option():
    flow = make_flow({"battery_capacity_ah": 100, "nominal_voltage_v": 51.2})
    flow._group = "battery"
    result = asyncio.run(flow.async_step_init())
    assert result["description_placeholders"]["battery_energy_kwh"] == "5.12"
    assert len(result["data_schema"].schema) == 5
