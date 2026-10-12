"""Presentation-only contracts for the existing default dashboard card."""
from __future__ import annotations

from pathlib import Path
import unittest

import yaml
from jinja2 import Environment

REPO = Path(__file__).resolve().parents[1]
ENTITY = "sensor.smart_solar_inverter_predictive_decision_state"
SELECT_ENTITY = "select.smart_solar_inverter_predictive_target_soc"


def _learning_card_template() -> str:
    dashboard = yaml.safe_load((REPO / "dashboard.yaml").read_text(encoding="utf-8"))
    for view in dashboard["views"]:
        for section in view.get("sections", []):
            for card in section.get("cards", []):
                if card.get("title") == "AI · навчання і точність":
                    return card["content"]
    raise AssertionError("existing AI learning markdown card not found")


def _soc_card_template() -> str:
    dashboard = yaml.safe_load((REPO / "dashboard.yaml").read_text(encoding="utf-8"))
    for view in dashboard["views"]:
        for section in view.get("sections", []):
            for card in section.get("cards", []):
                if card.get("title") == "AI · SOC-стан":
                    return card["content"]
    raise AssertionError("existing AI SOC-status markdown card not found")


def _render_learning_card(attrs: dict, *, available: bool = True) -> str:
    env = Environment(autoescape=False)
    # Default missing calibration keys so Jinja2 strict-undefined lookup of optional
    # attributes (mae_kwh, bias_kwh, recent_pairs, pending) yields None / [] instead
    # of raising. The dashboard sets |default filters explicitly; the test fixture
    # mirrors that by always carrying the relevant keys.
    cal = dict(attrs.get("forecast_calibration") or {})
    cal.setdefault("recent_pairs", [])
    cal.setdefault("pending", [])
    cal.setdefault("mae_kwh", None)
    cal.setdefault("bias_kwh", None)
    attrs_copy = dict(attrs)
    attrs_copy["forecast_calibration"] = cal

    def _state_attr(entity, key):
        if entity != ENTITY:
            return None
        if key == "forecast_calibration":
            return cal
        return attrs_copy.get(key)

    env.globals["state_attr"] = _state_attr
    env.globals["has_value"] = lambda entity: entity == ENTITY and available
    # states() not used by the AI · навчання і точність card, kept for completeness.
    env.globals["states"] = lambda entity: attrs_copy.get("_state") if entity == ENTITY else None
    return env.from_string(_learning_card_template()).render()


def _render_soc_card(select_state: str | None) -> str:
    env = Environment(autoescape=False)
    env.globals["states"] = lambda entity: select_state if entity == SELECT_ENTITY else None
    return env.from_string(_soc_card_template()).render()


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
        # Original data is untouched by display formatting.
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
        # No fake-zero artefacts in this branch.
        self.assertNotIn("0.00 кВт·год", rendered)

        attrs["override_pending_until"] = "2026-10-12T12:30:00+00:00"
        active = _render_learning_card(attrs)
        self.assertIn("Пауза активна до 2026-10-12T12:30:00+00:00", active)
        self.assertNotIn("Пауза не активна", active)

        unavailable = _render_learning_card(attrs, available=False)
        self.assertIn("**Пауза:** Дані недоступні", unavailable)
        self.assertNotIn("Пауза не активна", unavailable)

    def test_v1_model_fallback_is_listed(self) -> None:
        """Both v1 and v2 model keys must be present in the rendered translation map."""
        calibration = {
            "forecast_model": "hourly_response_v1",
            "samples": 1, "pending_count": 0, "mae_kwh": 0.1, "bias_kwh": 0.0,
            "recent_pairs": [], "pending": [],
        }
        attrs = {"forecast_calibration": calibration, "override_pending_until": None}
        rendered = _render_learning_card(attrs)
        self.assertIn("Погодинна модель станції (v1)", rendered)

        calibration["forecast_model"] = "station_gain_v1"
        attrs["forecast_calibration"] = calibration
        rendered = _render_learning_card(attrs)
        self.assertIn("Добова модель станції (v1)", rendered)

    def test_reason_codes_translate_to_ukrainian_with_machine_codes_preserved(self) -> None:
        """Each known reason / execution_reason must surface a Ukrainian explanation
        while keeping the machine-readable code in parentheses for diagnostics."""
        calibration = {
            "forecast_model": "hourly_response_v2",
            "samples": 0, "pending_count": 0,
            "recent_pairs": [], "pending": [],
        }
        # Sweep every reason code documented in the engine and predictive modules.
        cases = {
            "already_in_target": "Поточна конфігурація вже відповідає цілі",
            "battery_drives_load": "Батарея живить навантаження",
            "day_pv_low": "Прогнозована PV-потужність нижча за споживання",
            "hysteresis_recovery": "Повернення до попереднього режиму",
            "night_window_usb": "Нічна USB-стратегія",
            "critical_recovery": "Критичний SOC",
            "surplus_enter_sbu": "Надлишок PV",
            "reserve_soc_protection": "SOC нижче резерву",
            "evening_reserve_protection": "Вечірній резерв",
            "emergency_low_soc_daytime": "Аварійно низький SOC удень",
            "storm_mode": "Штормовий режим",
            "predictive_assist_evening": "Прогнозна підтримка",
            "no_hint": "Планувальник ще не сформував рекомендацію",
            "hems_auto_off": "HEMS авто-режим вимкнено",
            "unknown_mode": "Невідомий режим",
            "invalid_telemetry": "Невалідні дані телеметрії",
            "soc_unknown": "SOC невідомий",
            "day_default": "Стандартна денна стратегія",
            "arbitrage_night": "Арбітраж вночі",
            "arbitrage_day_sbu": "Арбітраж удень: SBU",
            "arbitrage_day_usb": "Арбітраж удень: USB",
            "forecast_good_use_battery": "Прогноз сприятливий",
            "circuit_breaker": "Захисне блокування HEMS",
            "reserve_floor": "SOC досяг резервної межі",
        }
        for code, ua_prefix in cases.items():
            attrs = {
                "forecast_calibration": dict(calibration),
                "override_pending_until": None,
                "mode": "Assist", "confidence": 0.5, "applied": True,
                "reason": code, "execution_reason": code,
            }
            rendered = _render_learning_card(attrs)
            self.assertIn(ua_prefix, rendered, msg=f"Ukrainian explanation missing for reason={code}")
            # machine-readable code is preserved in parens
            self.assertIn(f"({code})", rendered, msg=f"machine-readable code missing for reason={code}")

    def test_mode_applied_and_confidence_have_ukrainian_translations(self) -> None:
        calibration = {
            "forecast_model": "hourly_response_v2",
            "samples": 1, "pending_count": 0,
            "recent_pairs": [], "pending": [],
        }
        # Off + applied=False branch
        attrs_false = {
            "forecast_calibration": calibration,
            "override_pending_until": None,
            "mode": "Off", "confidence": 0.0, "applied": False,
            "reason": "hems_auto_off", "execution_reason": "hems_auto_off",
        }
        rendered = _render_learning_card(attrs_false)
        self.assertIn("Вимкнено (Off)", rendered)
        self.assertIn("0 %", rendered)
        self.assertIn("Ні (AI рекомендація не прийнята)", rendered)

        # Shadow branch
        attrs_shadow = dict(attrs_false, mode="Shadow", applied=True, reason="day_default",
                            execution_reason="day_default", confidence=0.4123)
        rendered_shadow = _render_learning_card(attrs_shadow)
        self.assertIn("Спостереження (Shadow)", rendered_shadow)
        self.assertIn("41 %", rendered_shadow)

        # Assist branch with applied=true
        attrs_assist = dict(attrs_false, mode="Assist", applied=True, reason="day_default",
                            execution_reason="day_default", confidence=0.999)
        rendered_assist = _render_learning_card(attrs_assist)
        self.assertIn("Підтримка (Assist)", rendered_assist)
        self.assertIn("100 %", rendered_assist)
        self.assertIn("Так (AI рекомендація прийнята HEMS)", rendered_assist)

    def test_learning_card_carries_honest_disclaimer_about_ai_vs_hems(self) -> None:
        """The card must not over-claim physical inverter action — only the
        AI recommendation being accepted by HEMS."""
        calibration = {
            "forecast_model": "hourly_response_v2",
            "samples": 0, "pending_count": 0,
            "recent_pairs": [], "pending": [],
        }
        attrs = {
            "forecast_calibration": calibration, "override_pending_until": None,
            "mode": "Assist", "confidence": 0.5, "applied": True,
            "reason": "day_default", "execution_reason": "day_default",
        }
        rendered = _render_learning_card(attrs)
        self.assertIn("AI лише рекомендує", rendered)
        self.assertIn("HEMS вирішує", rendered)
        self.assertIn("прийняття AI-рекомендації HEMS", rendered)
        self.assertIn("фізичну дію інвертора", rendered)

    def test_soc_status_card_states_when_target_is_unknown_or_empty(self) -> None:
        """When the predictive_target_soc select has no manual target,
        show the Ukrainian "Автоматично" status, not the raw unknown."""
        for empty in (None, "", "None", "none", "unknown", "unavailable"):
            rendered = _render_soc_card(empty)
            self.assertIn("Автоматично — ручну ціль не задано", rendered,
                          msg=f"SOC card did not render Автоматично for select_state={empty!r}")
            self.assertNotIn("Ручна ціль", rendered,
                             msg=f"SOC card wrongly showed manual target for empty={empty!r}")

    def test_soc_status_card_shows_manual_target_when_set(self) -> None:
        rendered = _render_soc_card("80")
        self.assertIn("Ручна ціль: 80 %", rendered)
        self.assertIn("HEMS на паузі 30 хв", rendered)
        # And the inverse: it must not claim Автоматично when user picked a value.
        self.assertNotIn("Автоматично", rendered)


if __name__ == "__main__":
    unittest.main()
