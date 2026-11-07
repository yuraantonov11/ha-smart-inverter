"""Lossless redesign contract, without HA imports or live commands."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest


MODULE = Path(__file__).resolve().parents[1] / "services" / "dashboard_redesign.py"
spec = importlib.util.spec_from_file_location("dashboard_redesign_standalone", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
build = module.build_redesigned_dashboard


def fixture():
    return {
        "title": "User station", "background": "var(--lovelace-background)",
        "views": [
            {"title": "Огляд", "path": "powmr-overview", "type": "sections",
             "sections": [{"type": "grid", "cards": [
                 {"type": "custom:k-flow-card", "pv_total_power": "sensor.renamed_pv",
                  "sun": "sun.sun", "battery_soc": "sensor.control_soc",
                  "custom_user_setting": {"label": "Keep me"}},
                 {"type": "custom:user-notes", "content": "Keep every original card"},
             ]}]},
            {"title": "Керування", "path": "powmr-control", "cards": [
                {"type": "entities", "entities": ["select.renamed_output"]},
            ]},
            {"title": "AI · HEMS", "path": "powmr-ai", "cards": [
                {"type": "markdown", "content": "Original AI and forecast diagnostics"},
            ]},
            {"title": "Відключення", "path": "powmr-outages", "cards": [
                {"type": "custom:k-outage-scenarios-card", "entry_id": "entry-a"},
            ]},
            {"title": "Власні нотатки", "path": "user-notes", "cards": [
                {"type": "markdown", "content": "User added view"},
            ]},
        ],
    }


ENTITIES = {
    "pv_power": "sensor.renamed_pv", "load_power": "sensor.house",
    "battery_soc": "sensor.raw_soc", "battery_soc_corrected": "sensor.control_soc",
    "ble_read_status": "sensor.renamed_read_status", "ble_pv_power": "sensor.renamed_ble_pv",
    "ble_load_power": "sensor.renamed_ble_load", "ble_battery_soc": "sensor.renamed_ble_soc",
    "predictive_decision_state": "sensor.renamed_decision",
}


class DashboardRedesignTests(unittest.TestCase):
    def test_preserves_original_custom_content_and_does_not_mutate_inputs(self):
        source, entities = fixture(), deepcopy(ENTITIES)
        before, keys_before = deepcopy(source), deepcopy(entities)
        output, summary = build(source, entities)
        self.assertEqual(source, before)
        self.assertEqual(entities, keys_before)
        self.assertEqual(output["background"], before["background"])
        self.assertTrue(summary["changed"])
        originals = {v["path"]: v for v in output["views"] if v.get("subview")}
        for original in before["views"][:-1]:
            path = summary["renamed_paths"].get(original["path"], original["path"])
            restored = deepcopy(originals[path])
            for key in ("path", "subview", "back_path"):
                restored.pop(key, None)
            expected = deepcopy(original)
            expected.pop("path", None)
            self.assertEqual(restored, expected)
        self.assertIn(before["views"][-1], output["views"])

    def test_four_text_routes_and_animation_is_first(self):
        output, _ = build(fixture(), ENTITIES)
        visible = [v for v in output["views"] if not v.get("subview")]
        self.assertEqual([v["title"] for v in visible[:4]],
                         ["Огляд", "Аналітика", "Автоматика", "Система"])
        self.assertTrue(all("icon" not in v for v in visible[:4]))
        flow = output["views"][0]["sections"][0]["cards"][1]
        original_flow = fixture()["views"][0]["sections"][0]["cards"][0]
        for key, value in original_flow.items():
            self.assertEqual(flow[key], value)
        self.assertTrue(flow["compact"])
        self.assertEqual(len(output["views"][0]["sections"]), 4)

    def test_old_control_ai_and_outage_links_still_work(self):
        output, _ = build(fixture(), ENTITIES, dashboard_path="station-sidecar")
        paths = [v["path"] for v in output["views"]]
        self.assertEqual(len(paths), len(set(paths)))
        for path in ("powmr-control", "powmr-ai", "powmr-outages"):
            self.assertIn(path, paths)
        text = repr(output)
        self.assertIn("/station-sidecar/powmr-outages", text)
        self.assertIn("/station-sidecar/powmr-control", text)
        self.assertNotIn("/powmr-energy/", text)

    def test_second_migration_is_noop_and_preserves_user_edits(self):
        output, _ = build(fixture(), ENTITIES)
        output["views"][0]["sections"].append({"type": "grid", "cards": [
            {"type": "markdown", "content": "Added after migration"},
        ]})
        again, summary = build(output, ENTITIES)
        self.assertFalse(summary["changed"])
        self.assertEqual(again, output)
        self.assertIsNot(again, output)

    def test_renamed_ble_bindings_and_control_source_are_not_swapped(self):
        output, _ = build(fixture(), ENTITIES)
        text = repr(output["views"][0])
        self.assertIn("sensor.renamed_ble_pv", text)
        self.assertIn("sensor.renamed_read_status", text)
        self.assertIn("last_success", text)
        self.assertIn("застаріли", text)
        self.assertIn("керування використовує", text)
        self.assertIn("sensor.control_soc", text)
        flow = output["views"][0]["sections"][0]["cards"][1]
        self.assertEqual(flow["battery_soc"], "sensor.control_soc")
        self.assertIn("Джерело керування — хмара", text)

    def test_no_ble_means_cloud_summary_without_empty_ble_fields(self):
        cloud_only = {k: v for k, v in ENTITIES.items() if not k.startswith("ble_")}
        output, _ = build(fixture(), cloud_only)
        text = repr(output["views"][0])
        self.assertIn("Сонце · хмара", text)
        self.assertNotIn("Показники інвертора · лише перегляд", text)
        self.assertNotIn("'entity': ''", text)

    def test_navigation_is_short_full_width_and_does_not_duplicate_overview_heading(self):
        output, _ = build(fixture(), ENTITIES)
        for card in module._cards(output):
            if card.get("type") == "button" and card.get("tap_action", {}).get("action") == "navigate":
                self.assertFalse(card["show_icon"])
                self.assertTrue(card["show_name"])
                self.assertEqual(card["grid_options"], {"columns": 12, "rows": 1})
                self.assertIn("icon", card)
        overview_entities = [card for card in module._cards(output["views"][0]["sections"][1])
                             if card.get("type") == "entities"]
        self.assertTrue(overview_entities)
        self.assertTrue(all("title" not in card for card in overview_entities))

    def test_system_connection_has_one_source_disclaimer_with_ble(self):
        output, _ = build(fixture(), ENTITIES)
        system = next(view for view in output["views"] if view["path"] == "powmr-system")
        markdown = [card["content"] for card in module._cards(system["sections"][0])
                    if card.get("type") == "markdown"]
        self.assertEqual(len(markdown), 1)
        self.assertIn("Джерело керування — хмара", markdown[0])
        no_ble, _ = build(fixture(), {k: v for k, v in ENTITIES.items() if not k.startswith("ble_")})
        system = next(view for view in no_ble["views"] if view["path"] == "powmr-system")
        self.assertIn("Керування", repr(system["sections"][0]))

    def test_refuses_missing_animation_or_invalid_config_without_mutation(self):
        for config in ({}, {"views": []}, {"views": [{"path": "user-only"}]}):
            before = deepcopy(config)
            with self.assertRaises(ValueError):
                build(config, ENTITIES)
            self.assertEqual(config, before)
        with self.assertRaises(ValueError):
            build(fixture(), ENTITIES, dashboard_path="/wrong/path")

    def test_existing_details_path_collision_is_resolved(self):
        source = fixture()
        source["views"].append({"path": "powmr-overview-details", "title": "User page"})
        output, summary = build(source, ENTITIES)
        self.assertEqual(summary["renamed_paths"]["powmr-overview"], "powmr-overview-details-2")
        self.assertIn(source["views"][-1], output["views"])

    def test_diagnostic_presence_does_not_add_control_actions(self):
        output, _ = build(fixture(), ENTITIES)
        for card in module._cards(output["views"][0]):
            action = card.get("tap_action", {})
            self.assertNotIn(action.get("action"), ("call-service", "perform-action", "toggle"))
        # Original controls and outage actions remain reachable, not deleted.
        self.assertIn("select.renamed_output", repr(output))
        self.assertIn("custom:k-outage-scenarios-card", repr(output))

    def test_daily_forecast_route_is_curated_instead_of_original_ai_dump(self):
        output, _ = build(fixture(), ENTITIES)
        automation = next(v for v in output["views"] if v["path"] == "powmr-automation")
        text = repr(automation)
        self.assertIn("/powmr-energy/powmr-forecasts", text)
        self.assertNotIn("/powmr-energy/powmr-ai", text)
        forecasts = next(v for v in output["views"] if v["path"] == "powmr-forecasts")
        self.assertTrue(forecasts["subview"])
        forecast_text = repr(forecasts)
        self.assertIn("real_pairs", forecast_text)
        self.assertIn("completed_windows", forecast_text)
        self.assertIn("readiness", text)
        self.assertNotIn("Original AI and forecast diagnostics", forecast_text)
        self.assertNotIn("confidence =", forecast_text)

    def test_manual_editor_receives_renamed_entities_without_duplicate_sbu_toggle(self):
        entities = dict(ENTITIES, output_priority="select.renamed_output",
                        charger_priority="select.renamed_charge",
                        max_charging_current="number.charge_limit",
                        grid_feed_in="switch.grid_export", backup_mode="switch.duplicate_sbu")
        output, _ = build(fixture(), entities)
        automation = next(v for v in output["views"] if v["path"] == "powmr-automation")
        editors = [c for c in module._cards(automation)
                   if c.get("type") == "custom:smart-solar-controls-card"]
        self.assertEqual(len(editors), 1)
        bound = {row["entity"] for row in editors[0]["entities"]}
        self.assertEqual(bound, {"select.renamed_output", "select.renamed_charge",
                                 "number.charge_limit", "switch.grid_export"})
        self.assertNotIn("switch.duplicate_sbu", repr(automation))
        self.assertEqual(next(row["name"] for row in editors[0]["entities"]
                              if row["entity"] == "number.charge_limit"), "Загальний струм заряду")

    def test_summary_templates_render_missing_data_without_raw_keys_or_false_success(self):
        from jinja2 import Environment
        environment = Environment()
        environment.globals.update(
            states=lambda entity: "unknown", state_attr=lambda entity, key: None,
            as_timestamp=lambda value, default=None: default, now=lambda: None,
        )
        output, _ = build(fixture(), ENTITIES)
        curated = [v for v in output["views"] if not v.get("subview")
                   or v.get("path") == "powmr-forecasts"]
        rendered = []
        for card in module._cards(curated):
            if card.get("type") == "markdown":
                rendered.append(environment.from_string(card["content"]).render())
        text = "\n".join(rendered)
        self.assertIn("ще немає успішного читання", text)
        self.assertIn("Точність поточної моделі ще не виміряна", text)
        self.assertNotIn("Прогнозне керування готове", text)
        self.assertNotIn("{{", text)


if __name__ == "__main__":
    unittest.main()
