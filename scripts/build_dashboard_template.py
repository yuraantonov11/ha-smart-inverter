"""Regenerate the manual-install template with the production layout builder."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import re
import yaml

class DashboardDumper(yaml.SafeDumper):
    """Keep Jinja/Markdown readable and editable in the manual template."""

def _string(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)

DashboardDumper.add_representer(str, _string)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dashboard_redesign", ROOT / "services/dashboard_redesign.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def main():
    path = ROOT / "dashboard.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if config.pop("smart_solar_redesign_version", None):
        originals = [view for view in config["views"] if view.get("subview") and view.get("path") in module._KNOWN_PATHS]
        if not originals:
            raise ValueError("Cannot regenerate template without preserved source views")
        for view in originals:
            view.pop("subview", None)
            view.pop("back_path", None)
        config["views"] = originals
    mapping = {}
    for domain, key in re.findall(r"\b(sensor|binary_sensor|number|select|switch)\.smart_solar_inverter_([a-z0-9_]+)", path.read_text(encoding="utf-8")):
        mapping[key] = f"{domain}.smart_solar_inverter_{key}"
    if not any(c.get("type") == "custom:k-flow-card" for c in module._cards(config)):
        flow = {"type": "custom:k-flow-card", "inverter_name": "Інвертор", "sun": "sun.sun"}
        for config_key, entity_key in {"pv_total_power":"pv_power", "consump":"load_power", "grid_active_power":"grid_power", "battery_soc":"battery_soc_corrected", "battery_voltage":"battery_voltage", "battery_current":"battery_current", "battery_power":"battery_power"}.items():
            if entity_key in mapping:
                flow[config_key] = mapping[entity_key]
        config["views"][0].setdefault("sections", []).insert(0, {"type":"grid", "cards":[flow]})
    result, summary = module.build_redesigned_dashboard(config, mapping)
    path.write_text(yaml.dump(result, Dumper=DashboardDumper, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(summary)

if __name__ == "__main__":
    main()
