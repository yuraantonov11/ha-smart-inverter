"""Behavioral boundaries for the explicit live-dashboard migration request."""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_apply():
    # A package stub avoids importing the HA integration root. The real apply
    # module and real builder are loaded from their production files.
    name = "redesign_apply_test"
    package = types.ModuleType(name)
    package.__path__ = [str(ROOT / "services")]
    spec = importlib.util.spec_from_file_location(name + ".dashboard_redesign_apply", ROOT / "services/dashboard_redesign_apply.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: package, spec.name: module}):
        spec.loader.exec_module(module)
    return module


class Panel:
    def __init__(self, root, original):
        self.root = root
        self.original = copy.deepcopy(original)
        self.config = copy.deepcopy(original)
        self.saved = []
        self.loads = 0
        self.mismatch = False
        self.fail_save = False
        self.mutate_then_fail = False

    async def async_load(self, force):
        self.loads += 1
        if self.mismatch and self.loads == 2:
            return {"views": [{"path": "unexpected"}]}
        return copy.deepcopy(self.config)

    async def async_save(self, value):
        backups = [p for p in (self.root / "backups/smart_solar").glob("*.json") if not p.name.endswith(".resources.json")]
        assert len(backups) == 1, "backup must exist before the first save"
        assert json.loads(backups[0].read_text(encoding="utf-8")) == self.original
        self.saved.append(copy.deepcopy(value))
        if self.fail_save:
            raise RuntimeError("write refused")
        self.config = copy.deepcopy(value)
        if self.mutate_then_fail and len(self.saved) == 1:
            raise RuntimeError("write failed after mutation")


def setup(tmp_path, request=None):
    original = {"title": "Existing customized dashboard", "views": [{"path": "powmr-overview", "cards": [{"type": "custom:k-flow-card", "extra": "preserved"}]}]}
    candidate = {"title": original["title"], "views": [{"path": "powmr-overview", "cards": [{"type": "custom:k-flow-card", "retained": True}]}]}
    panel = Panel(tmp_path, original)
    entry = types.SimpleNamespace(entry_id="entry-a", options={"lovelace_dashboard_url_path": "powmr-energy"})
    async def executor(function, *args):
        return function(*args)
    hass = types.SimpleNamespace(config=types.SimpleNamespace(path=lambda *parts: str(tmp_path.joinpath(*parts))),
                                 data={"lovelace": types.SimpleNamespace(dashboards={"powmr-energy": panel})},
                                 async_add_executor_job=executor)
    if request is not None:
        (tmp_path / "powmr_dashboard_redesign.request").write_text(json.dumps(request), encoding="utf-8")
    return hass, entry, panel, original, candidate


VALID_REQUEST = {"entry_id": "entry-a", "dashboard_path": "powmr-energy"}


def execute(module, hass, entry):
    registry = types.ModuleType("homeassistant.helpers.entity_registry")
    registry.async_get = lambda _hass: types.SimpleNamespace(entities={
        "ours": types.SimpleNamespace(config_entry_id="entry-a", translation_key="pv_power", entity_id="sensor.ours", unique_id="pv"),
        "other": types.SimpleNamespace(config_entry_id="entry-b", translation_key="pv_power", entity_id="sensor.foreign", unique_id="foreign"),
    })
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.entity_registry = registry
    homeassistant = types.ModuleType("homeassistant")
    homeassistant.helpers = helpers
    with patch.dict(sys.modules, {"homeassistant": homeassistant, "homeassistant.helpers": helpers,
                                  "homeassistant.helpers.entity_registry": registry}):
        asyncio.run(module.async_apply_requested_redesign(hass, entry))


@pytest.mark.parametrize("requested", [None, {"entry_id": "other", "dashboard_path": "powmr-energy"},
                                      {"entry_id": "entry-a", "dashboard_path": "someone-elses-panel"}])
def test_only_exact_entry_and_dashboard_can_apply(tmp_path, requested):
    module = load_apply()
    hass, entry, panel, original, candidate = setup(tmp_path, requested)
    module.build_redesigned_dashboard = lambda *a, **k: pytest.fail("wrong owner must never build")
    execute(module, hass, entry)
    assert panel.config == original
    assert panel.saved == []
    assert (tmp_path / "powmr_dashboard_redesign.request").exists() == (requested is not None)


def test_success_backs_up_before_save_checks_mapping_and_removes_request(tmp_path):
    module = load_apply()
    hass, entry, panel, original, candidate = setup(tmp_path, VALID_REQUEST)
    def build(current, mapping, dashboard_path):
        assert current == original
        assert mapping == {"pv_power": "sensor.ours"}
        assert dashboard_path == "powmr-energy"
        return candidate, {"changed": True}
    module.build_redesigned_dashboard = build
    execute(module, hass, entry)
    assert panel.config == candidate
    assert panel.loads == 2
    assert panel.saved == [candidate]
    assert not (tmp_path / "powmr_dashboard_redesign.request").exists()
    result = json.loads((tmp_path / "powmr_dashboard_redesign.result.json").read_text(encoding="utf-8"))
    assert Path(result["backup"]).exists()
    assert result["dashboard_path"] == "powmr-energy"


def test_readback_mismatch_restores_original_and_retains_request(tmp_path):
    module = load_apply()
    hass, entry, panel, original, candidate = setup(tmp_path, VALID_REQUEST)
    panel.mismatch = True
    module.build_redesigned_dashboard = lambda *a, **k: (candidate, {"changed": True})
    execute(module, hass, entry)
    assert panel.config == original
    assert panel.saved == [candidate, original]
    assert (tmp_path / "powmr_dashboard_redesign.request").exists()
    assert not (tmp_path / "powmr_dashboard_redesign.result.json").exists()


def test_failed_backup_never_saves_dashboard_and_retains_request(tmp_path):
    module = load_apply()
    hass, entry, panel, original, candidate = setup(tmp_path, VALID_REQUEST)
    module.build_redesigned_dashboard = lambda *a, **k: (candidate, {"changed": True})
    async def executor(function, *args):
        if function.__name__ == "save_backup":
            raise OSError("disk unavailable")
        return function(*args)
    hass.async_add_executor_job = executor
    execute(module, hass, entry)
    assert panel.config == original
    assert panel.saved == []
    assert (tmp_path / "powmr_dashboard_redesign.request").exists()


def test_write_failure_leaves_original_and_retains_request(tmp_path):
    module = load_apply()
    hass, entry, panel, original, candidate = setup(tmp_path, VALID_REQUEST)
    panel.fail_save = True
    module.build_redesigned_dashboard = lambda *a, **k: (candidate, {"changed": True})
    execute(module, hass, entry)
    assert panel.config == original
    assert (tmp_path / "powmr_dashboard_redesign.request").exists()


def test_failure_after_partial_write_rolls_back_and_retains_request(tmp_path):
    module = load_apply()
    hass, entry, panel, original, candidate = setup(tmp_path, VALID_REQUEST)
    panel.mutate_then_fail = True
    module.build_redesigned_dashboard = lambda *a, **k: (candidate, {"changed": True})
    execute(module, hass, entry)
    assert panel.config == original
    assert (tmp_path / "powmr_dashboard_redesign.request").exists()


def test_result_record_failure_rolls_back_and_retains_request(tmp_path):
    module = load_apply()
    hass, entry, panel, original, candidate = setup(tmp_path, VALID_REQUEST)
    module.build_redesigned_dashboard = lambda *a, **k: (candidate, {"changed": True})
    async def executor(function, *args):
        if getattr(function, "__self__", None) == tmp_path / "powmr_dashboard_redesign.result.json":
            raise OSError("result record unavailable")
        return function(*args)
    hass.async_add_executor_job = executor
    execute(module, hass, entry)
    assert panel.config == original
    assert (tmp_path / "powmr_dashboard_redesign.request").exists()


class FakeResources:
    def __init__(self, root, items):
        self.root = root
        self.items = copy.deepcopy(items)
        self.original = copy.deepcopy(items)
        self.updates = []
        self.info_loaded = False

    async def async_get_info(self):
        self.info_loaded = True
        return {"mode": "storage"}

    def async_items(self):
        assert self.info_loaded
        return list(self.items)

    async def async_update_item(self, item_id, value):
        backup_files = list((self.root / "backups/smart_solar").glob("*.resources.json"))
        assert len(backup_files) == 1, "resource backup must exist before updates"
        assert json.loads(backup_files[0].read_text(encoding="utf-8")) == self.original
        self.updates.append((item_id, copy.deepcopy(value)))
        next(item for item in self.items if item["id"] == item_id).update(value)


def add_resources(hass, tmp_path, items):
    resources = FakeResources(tmp_path, items)
    hass.data["lovelace"].resources = resources
    return resources


def write_resource(tmp_path, name, content):
    path = tmp_path / "www/community/powmr-inverter" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    import hashlib
    return "/local/community/powmr-inverter/" + name + "?v=" + hashlib.sha256(content).hexdigest()[:12]


def test_updates_only_owned_allowlisted_resources_to_content_hashes(tmp_path):
    module = load_apply()
    hass, entry, panel, original, candidate = setup(tmp_path, VALID_REQUEST)
    new_flow = write_resource(tmp_path, "k-flow-card.js", b"new flow contents")
    new_controls = write_resource(tmp_path, "smart-solar-controls-card.js", b"new controls contents")
    items = [
        {"id": "flow", "url": "/local/community/powmr-inverter/k-flow-card.js?v=old", "type": "module"},
        {"id": "controls", "url": "/local/community/powmr-inverter/smart-solar-controls-card.js", "type": "module"},
        {"id": "unrelated", "url": "/hacsfiles/chart-card/chart-card.js?v=preserve", "type": "module"},
        {"id": "not-allowed", "url": "/local/community/powmr-inverter/custom-owner-card.js?v=preserve", "type": "module"},
        {"id": "other-dir", "url": "/local/someone-else/k-flow-card.js?v=preserve", "type": "module"},
    ]
    resources = add_resources(hass, tmp_path, items)
    module.build_redesigned_dashboard = lambda *a, **k: (candidate, {"changed": True})
    execute(module, hass, entry)
    assert resources.updates == [("flow", {"url": new_flow}), ("controls", {"url": new_controls})]
    assert resources.items[2:] == items[2:]
    assert all(item["type"] == "module" for item in resources.items)
    assert not (tmp_path / "powmr_dashboard_redesign.request").exists()


def test_panel_save_failure_rolls_back_resource_urls(tmp_path):
    module = load_apply()
    hass, entry, panel, original, candidate = setup(tmp_path, VALID_REQUEST)
    new_flow = write_resource(tmp_path, "k-flow-card.js", b"updated resource")
    items = [{"id": "flow", "url": "/local/community/powmr-inverter/k-flow-card.js?v=original", "type": "module"}]
    resources = add_resources(hass, tmp_path, items)
    panel.fail_save = True
    module.build_redesigned_dashboard = lambda *a, **k: (candidate, {"changed": True})
    execute(module, hass, entry)
    assert resources.updates == [("flow", {"url": new_flow}), ("flow", {"url": items[0]["url"]})]
    assert resources.items == items
    assert panel.config == original
    assert (tmp_path / "powmr_dashboard_redesign.request").exists()


def test_already_content_hashed_resource_is_not_updated_again(tmp_path):
    module = load_apply()
    hass, entry, panel, original, candidate = setup(tmp_path, VALID_REQUEST)
    current_url = write_resource(tmp_path, "pv-comparison-card.js", b"unchanged current source")
    resources = add_resources(hass, tmp_path, [{"id": "pv", "url": current_url, "type": "module"}])
    module.build_redesigned_dashboard = lambda *a, **k: (candidate, {"changed": True})
    execute(module, hass, entry)
    assert resources.updates == []
    assert panel.config == candidate
    assert not (tmp_path / "powmr_dashboard_redesign.request").exists()
