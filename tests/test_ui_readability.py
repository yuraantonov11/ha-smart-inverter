"""Presentation-only contracts for the existing default dashboard card."""
from __future__ import annotations

from pathlib import Path
import unittest

import yaml
from jinja2 import Environment

REPO = Path(__file__).resolve().parents[1]
ENTITY = "sensor.smart_solar_inverter_predictive_decision_state"


def _learning_card_template() -> str:
    dashboard = yaml.safe_load((REPO / "dashboard.yaml").read_text(encoding="utf-8"))
    for view in dashboard["views"]:
        for section in view.get("sections", []):
            for card in section.get("cards", []):
                if card.get("title") == "AI · навчання і точність":
                    return card["content"]
    raise AssertionError("existing AI learning markdown card not found")


def _render_learning_card(attrs: dict, *, available: bool = True) -> str:
    env = Environment(autoescape=False)
    env.globals["state_attr"] = lambda entity, key: attrs.get(key) if entity == ENTITY else None
    env.globals["has_value"] = lambda entity: entity == ENTITY and available
    return env.from_string(_learning_card_template()).render()


class UiReadabilityContracts(unittest.TestCase):
    def test_v2_model_names_and_kwh_values_are_formatted_only_for_display(self) -> None:
        calibration = {
            "forecast_model": "hourly_response_v2",
            "samples": 1,
            "pending_count": 1,
            "mae_kwh": 1.23456,
            "bias_kwh": -0.006,
            "recent_pairs": [
                {"date": "2026-10-10", "forecast_kwh": 3.4567, "actual_kwh": 0.0,
                    "used_for_current_model": True},
            ],
            "pending": [
                {"date": "2026-10-11", "forecast_kwh": 2.5, "actual_kwh": None,
                    "awaiting": "completed_day"},
            ],
        }
        attrs = {"forecast_calibration": calibration, "override_pending_until": None}

        rendered = _render_learning_card(attrs)

        self.assertIn("Погодинна модель станції (v2)", rendered)
        self.assertIn("1.23 кВт·год", rendered)
        self.assertIn("-0.01 кВт·год", rendered)
        self.assertIn("3.46", rendered)
        self.assertIn("0.00", rendered)
        self.assertIn("Дані недоступні", rendered)
        self.assertIn("| 2026-10-11 | 2.50 | Дані недоступні |", rendered)
        self.assertEqual(calibration["mae_kwh"], 1.23456)
        self.assertEqual(calibration["bias_kwh"], -0.006)
        self.assertEqual(calibration["recent_pairs"][0]["forecast_kwh"], 3.4567)
        self.assertEqual(calibration["recent_pairs"][0]["actual_kwh"], 0.0)

    def test_station_model_pause_and_unknown_actual_measurement_are_honest(self) -> None:
        calibration = {
            "forecast_model": "station_gain_v2",
            "samples": 1,
            "pending_count": 1,
            "mae_kwh": None,
            "bias_kwh": None,
            "recent_pairs": [],
            "pending": [
                {"date": "2026-10-12", "forecast_kwh": None, "actual_kwh": None,
                    "awaiting": "api_recorder"},
            ],
        }
        attrs = {"forecast_calibration": calibration, "override_pending_until": None}

        rendered = _render_learning_card(attrs)

        self.assertIn("Добова модель станції (v2)", rendered)
        self.assertIn("**Пауза:** Пауза не активна", rendered)
        self.assertIn("**MAE:** Дані недоступні кВт·год", rendered)
        self.assertIn("| 2026-10-12 | Дані недоступні | Дані недоступні |", rendered)
        self.assertNotIn("0.00 кВт·год", rendered)

        attrs["override_pending_until"] = "2026-10-12T12:30:00+00:00"
        active = _render_learning_card(attrs)
        self.assertIn("Пауза активна до 2026-10-12T12:30:00+00:00", active)
        self.assertNotIn("Пауза не активна", active)

        unavailable = _render_learning_card(attrs, available=False)
        self.assertIn("**Пауза:** Дані недоступні", unavailable)
        self.assertNotIn("Пауза не активна", unavailable)


if __name__ == "__main__":
    unittest.main()
