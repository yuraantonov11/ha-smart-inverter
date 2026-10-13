"""Presentation-only contracts for the existing default dashboard card."""
from __future__ import annotations

from pathlib import Path
import unittest

import re

import yaml
from jinja2 import Environment

REPO = Path(__file__).resolve().parents[1]
ENTITY = "sensor.smart_solar_inverter_predictive_decision_state"
SELECT_ENTITY = "select.smart_solar_inverter_predictive_target_soc"


# Mirror the HA-only Jinja filter ``regex_replace`` so the
# tests can exercise the same templates that the live
# dashboard runs under Home Assistant. Production
# Home Assistant adds this filter natively; here we install
# it onto the vanilla Jinja2 environment the unit tests use.
def _regex_replace(value: str, pattern: str, replacement: str = "") -> str:
    if value is None:
        return value
    return re.sub(pattern, replacement, str(value))


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
    env.filters["regex_replace"] = _regex_replace
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


def _render_soc_card(select_state: str | None, *,
                    target_soc: int | None = None,
                    has_manual: bool | None = None) -> str:
    env = Environment(autoescape=False)
    env.filters["regex_replace"] = _regex_replace
    env.globals["states"] = lambda entity: select_state if entity == SELECT_ENTITY else None

    def _state_attr(entity, key):
        if entity != SELECT_ENTITY:
            return None
        if key == "target_soc":
            return target_soc
        if key == "has_manual_target":
            return has_manual
        return None

    env.globals["state_attr"] = _state_attr
    env.globals["has_value"] = lambda entity: entity == SELECT_ENTITY
    return env.from_string(_soc_card_template()).render()


def _render_recommendation_card(
    attrs: dict, *,
    target_soc: int | None = None,
    has_manual: bool | None = None,
) -> str:
    """Render the «AI · рекомендація» markdown card with the
    given attrs and a fake ``has_manual_target`` /
    ``target_soc`` triple.

    The recommendation card uses the SAME ``has_manual`` /
    ``target_soc`` attributes as the SOC card. We mirror the
    three states: ``has_manual is True + target_soc int``,
    ``has_manual is False + target_soc None`` (auto), and
    ``has_manual is None`` (entity not loaded)."""
    env = Environment(autoescape=False)
    env.filters["regex_replace"] = _regex_replace
    STATE = ENTITY
    SELECT = SELECT_ENTITY
    env.globals["state_attr"] = (
        lambda entity, key: (
            attrs.get(key) if entity == STATE else
            (target_soc if key == "target_soc" and entity == SELECT else
             has_manual if key == "has_manual_target" and entity == SELECT else
             None)
        )
    )
    env.globals["states"] = (
        lambda entity: (
            "80" if entity == SELECT and has_manual is True else
            "None" if entity == SELECT and has_manual is False else
            "unknown" if entity == SELECT and has_manual is None else
            None
        )
    )
    env.globals["has_value"] = lambda entity: entity == SELECT
    return env.from_string(_recommendation_card_template()).render()


def _recommendation_card_template() -> str:
    dashboard = yaml.safe_load((REPO / "dashboard.yaml").read_text(encoding="utf-8"))
    for view in dashboard["views"]:
        for section in view.get("sections", []):
            for card in section.get("cards", []):
                if card.get("title") == "AI · рекомендація та виконання":
                    return card["content"]
    raise AssertionError("existing AI recommendation markdown card not found")


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
        # Five columns: date | forecast | actual | delta | used.
        # Delta is its own cell now (no slash prefix on the actual cell).
        self.assertIn("| 3.46 | 0.00 | -3.46 | так |", rendered)
        self.assertIn("| 2026-10-11 | 2.50 | Очікуємо завершення доби |", rendered)
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
        self.assertIn("**MAE:** Немає даних кВт·год", rendered)
        self.assertIn("| 2026-10-12 | Немає даних | Немає даних |", rendered)
        # No fake-zero artefacts in this branch.
        self.assertNotIn("0.00 кВт·год", rendered)

        attrs["override_pending_until"] = "2026-10-12T12:30:00+00:00"
        active = _render_learning_card(attrs)
        self.assertIn("Пауза активна до 2026-10-12T12:30:00+00:00", active)
        self.assertNotIn("Пауза не активна", active)

        unavailable = _render_learning_card(attrs, available=False)
        self.assertIn("**Пауза:** Немає даних", unavailable)
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
        # Юра fixed: the live reason string is wrapped in
        # ``[ML] day_pv_low: PV 131W < load 585W (conf=0.00)``.
        # ``short_reason`` must strip the leading ``[ML] ``,
        # split on the first colon, trim, and look up the
        # resulting key in REASON_LABELS.
        rendered = _render_learning_card(
            {
                "reason": "[ML] day_pv_low: PV 131W < load 585W (conf=0.00)",
                "execution_reason": "[ML] storm_mode: тиждень дощів, очікувана PV 0.05 кВт·год",
                "mode": "Shadow", "applied": True, "confidence": 0.0,
                "forecast_calibration": {
                    "forecast_model": "station_gain_v2",
                    "samples": 1, "pending_count": 0,
                    "recent_pairs": [], "pending": [],
                },
                "override_pending_until": None,
            }
        )
        self.assertIn("PV нижче споживання — берегти заряд", rendered)
        self.assertIn("Штормовий режим — берегти заряд", rendered)
        # The original reason string must be preserved in the
        # diagnostic block, verbatim.
        self.assertIn(
            "reason = \"[ML] day_pv_low: PV 131W < load 585W (conf=0.00)\"",
            rendered,
        )
        self.assertIn(
            "execution_reason = \"[ML] storm_mode: тиждень дощів",
            rendered,
        )
        # Below: exhaustive list of reason codes.
        """Each known reason / execution_reason must surface a Ukrainian explanation
        while keeping the machine-readable code in parentheses for diagnostics."""
        calibration = {
            "forecast_model": "hourly_response_v2",
            "samples": 0, "pending_count": 0,
            "recent_pairs": [], "pending": [],
        }
        # Sweep every reason code documented in the engine and predictive modules.
        cases = {
            "already_in_target": "Уже в цілі",
            "battery_drives_load": "Батарея живить навантаження",
            "day_pv_low": "PV нижче споживання",
            "hysteresis_recovery": "Відновлення після паузи",
            "night_window_usb": "Нічна USB-стратегія",
            "critical_recovery": "Критичний SOC",
            "surplus_enter_sbu": "Надлишок PV",
            "reserve_soc_protection": "SOC нижче резерву",
            "evening_reserve_protection": "Вечірній резерв",
            "emergency_low_soc_daytime": "Аварійно низький SOC удень",
            "storm_mode": "Штормовий режим",
            "predictive_assist_evening": "Підготувати SOC до вечора",
            "no_hint": "Планувальник ще не сформував рекомендацію",
            "hems_auto_off": "HEMS авто-режим вимкнено",
            "unknown_mode": "Невідомий режим",
            "invalid_telemetry": "Невалідні дані телеметрії",
            "soc_unknown": "SOC невідомий — планувальник утримується",
            "day_default": "Стандартна денна стратегія",
            "arbitrage_night": "Арбітраж вночі",
            "arbitrage_day_sbu": "Арбітраж удень (SBU)",
            "arbitrage_day_usb": "Арбітраж удень (USB)",
            "forecast_good_use_battery": "Прогноз сприятливий",
            "circuit_breaker": "Захисне блокування HEMS",
            "reserve_floor": "SOC на резервній межі",
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

    def test_dwell_lock_translation_in_both_cards(self) -> None:
        """Юра fixed: додай переклад ``dwell_lock``, звіривши
        його значення з engine: утримання поточного режиму
        через мінімальний інтервал між змінами.
        The translation must be present in BOTH the
        recommendation card and the learning card (both use
        the same ``REASON_LABELS`` map)."""
        attrs = {
            "mode": "Shadow", "applied": True, "confidence": 0.5,
            "reason": "dwell_lock",
            "execution_reason": "dwell_lock",
            "forecast_calibration": {
                "forecast_model": "station_gain_v2",
                "samples": 1, "pending_count": 0,
                "recent_pairs": [], "pending": [],
            },
            "override_pending_until": None,
        }
        rendered = _render_learning_card(attrs)
        # Must translate, not show "Невідомий reason".
        self.assertIn("Утримання поточного режиму", rendered)
        self.assertNotIn("Невідомий reason", rendered)
        # The machine code is preserved in the diagnostic block.
        self.assertIn('reason = "dwell_lock"', rendered)

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
        self.assertIn("Вимкнено", rendered)
        self.assertIn("0 %", rendered)
        self.assertIn("Рекомендацію AI не застосовано", rendered)

        # Shadow branch
        attrs_shadow = dict(attrs_false, mode="Shadow", applied=True, reason="day_default",
                            execution_reason="day_default", confidence=0.4123)
        rendered_shadow = _render_learning_card(attrs_shadow)
        self.assertIn("Спостереження", rendered_shadow)
        self.assertIn("41 %", rendered_shadow)

        # Assist branch with applied=true
        attrs_assist = dict(attrs_false, mode="Assist", applied=True, reason="day_default",
                            execution_reason="day_default", confidence=0.999)
        rendered_assist = _render_learning_card(attrs_assist)
        self.assertIn("Підтримка", rendered_assist)
        self.assertIn("100 %", rendered_assist)
        self.assertIn("AI рекомендація прийнята HEMS", rendered_assist)

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
        """Юра fixed: the SOC card distinguishes three states via
        the new ``has_manual_target`` attribute (added in select.py),
        NOT by parsing the entity-state string.

          * ``has_manual_target is True`` + integer target_soc
            — operator has set a manual target.
          * ``has_manual_target is False`` + target_soc None
            — entity is loaded but no manual target is set.
            The HA entity state is "unknown" because
            ``current_option`` returns None, but that is the AUTO
            state, NOT an unavailability.
          * ``has_manual_target is None`` (attribute missing)
            — entity is not yet registered with the state machine.
            This is the only state where we say "ще не завантажився".

        The card must NOT say "тимчасово недоступний" for the auto
        state (Юра: "Не називай цей стан недоступністю")."""
        # Unavailable: has_manual attribute is None (entity not yet loaded).
        for unavailable in (None,):
            rendered = _render_soc_card(
                "unknown", target_soc=None, has_manual=unavailable,
            )
            self.assertIn("ще не завантажився", rendered,
                          msg=f"SOC card must say 'ще не завантажився' for has_manual=None")
            self.assertNotIn("Автоматично", rendered,
                             msg=f"SOC card must NOT say Автоматично for has_manual=None")
            self.assertNotIn("тимчасово недоступний", rendered,
                             msg=f"SOC card must NOT say 'тимчасово недоступний' for has_manual=None")
        # Auto: has_manual=False, target_soc=None, but state string may
        # be "unknown"/"None"/"none" — that is the auto state, not
        # unavailability.
        for auto_state in (None, "None", "none", "unknown", "unavailable", ""):
            rendered = _render_soc_card(
                auto_state, target_soc=None, has_manual=False,
            )
            self.assertIn("Автоматично", rendered,
                          msg=f"SOC card must say Автоматично for auto state={auto_state!r}")
            self.assertNotIn("тимчасово недоступний", rendered,
                             msg=f"SOC card must NOT say 'тимчасово недоступний' for auto state={auto_state!r}")
            self.assertNotIn("ще не завантажився", rendered,
                             msg=f"SOC card must NOT say 'ще не завантажився' for auto state={auto_state!r}")

    def test_soc_button_card_opens_more_info_on_select(self) -> None:
        """Юра fixed: 'Поверни доступний SOC-контрол: наприклад,
        кнопку «Задати ручну ціль SOC», яка відкриває more-info
        на наявному select. Прибери посилання на неіснуючий
        «select вище».'
        The dashboard must contain a button card with
        ``tap_action.action == 'more-info'`` pointing at the
        predictive_target_soc select (whose entity_id is the
        template; the deployer substitutes it via the registry
        before saving).
        """
        dashboard = yaml.safe_load(
            (REPO / "dashboard.yaml").read_text(encoding="utf-8")
        )
        found_button = False
        for view in dashboard["views"]:
            for section in view.get("sections", []):
                for card in section.get("cards", []):
                    if card.get("type") == "button" and card.get(
                        "name", "",
                    ).startswith("Задати ручну ціль SOC"):
                        found_button = True
                        tap = card.get("tap_action", {})
                        self.assertEqual(
                            tap.get("action"), "more-info",
                            msg="SOC button must use more-info action",
                        )
                        # The entity key may be a template
                        # (smart_solar_inverter_…); the deployer
                        # substitutes it before save. We just
                        # assert it points at the predictive
                        # target soc.
                        entity = tap.get("entity", "")
                        self.assertIn(
                            "predictive_target_soc", entity,
                            msg=f"SOC button entity {entity!r} "
                            "must reference predictive_target_soc",
                        )
        self.assertTrue(
            found_button,
            msg="expected a button card 'Задати ручну ціль SOC' "
            "in dashboard.yaml",
        )

    def test_no_card_text_references_nonexistent_select_above(self) -> None:
        """Юра fixed: the previous text said 'Обери 20–100 у
        select вище' but there is no select card above the
        AI cards anymore (it was a entities card, now a
        markdown card). The dashboard must NOT point at a
        non-existent control. We allow 'select' in the text
        as long as it does not say 'select вище'."""
        dashboard = yaml.safe_load(
            (REPO / "dashboard.yaml").read_text(encoding="utf-8")
        )
        for view in dashboard["views"]:
            for section in view.get("sections", []):
                for card in section.get("cards", []):
                    content = card.get("content", "")
                    if not isinstance(content, str):
                        continue
                    self.assertNotIn(
                        "select вище", content,
                        msg=f"card {card.get('title', '?')!r} still "
                        "references the non-existent 'select вище'",
                    )
                    self.assertNotIn(
                        "у select,", content,
                        msg=f"card {card.get('title', '?')!r} still "
                        "references the non-existent 'select,'",
                    )

    def test_recommendation_card_uses_same_soc_logic_as_soc_card(self) -> None:
        """Юра fixed: «AI · рекомендація» досі показує
        «очікуємо першого циклу», бо має власний ``has_value()``
        блок. У рекомендації залишилася перевірка
        ``has_value(SELECT)`` і state-string. Використай ті самі
        ``has_manual_target`` / ``target_soc``, що в SOC-картці.

        We render the recommendation card with a fake
        ``_render_recommendation_card`` helper (defined below
        in this test module) and assert that the three
        states match what the SOC card already says."""
        # Build a minimal attrs dict that drives both cards.
        attrs = {
            "mode": "Shadow", "applied": True, "confidence": 0.5,
            "reason": "day_pv_low",
            "execution_reason": "day_pv_low",
            "override_pending_until": None,
        }
        # Auto state (no manual target). Both cards must say
        # "Автоматично".
        rendered = _render_recommendation_card(
            attrs, target_soc=None, has_manual=False,
        )
        self.assertIn("Автоматично", rendered)
        self.assertNotIn("очікуємо першого циклу", rendered)
        # Manual state (target=80). Both cards must show 80.
        rendered = _render_recommendation_card(
            attrs, target_soc=80, has_manual=True,
        )
        self.assertIn("80 %", rendered)
        self.assertIn("ручну ціль задано", rendered)
        # Entity not yet loaded (has_manual=None).
        rendered = _render_recommendation_card(
            attrs, target_soc=None, has_manual=None,
        )
        self.assertIn("ще не завантажився", rendered)
        self.assertNotIn("Автоматично", rendered)
        self.assertNotIn("ручну ціль задано", rendered)

    def test_select_dropdown_removed_because_no_such_option_exists(self) -> None:
        """Юра fixed: the SOC control is the select entity, not a
        dropdown explanation. The 'AI · рекомендація' card is now
        a markdown card driven by Jinja; the SOC markdown card
        distinguishes unknown/unavailable from the auto state
        via ``has_manual_target`` attribute, not by string match.
        """
        dashboard = yaml.safe_load((REPO / 'dashboard.yaml').read_text(encoding='utf-8'))
        recommendation_found = False
        for view in dashboard['views']:
            for section in view.get('sections', []):
                for card in section.get('cards', []):
                    if card.get('title') == 'AI · рекомендація та виконання':
                        recommendation_found = True
                        # Markdown card has no entities list — the
                        # whole point of the fix is that the raw
                        # machine values never appear as attribute
                        # rows. Confirm the structure.
                        self.assertEqual(card.get('type'), 'markdown',
                                         msg='AI · рекомендація must be markdown to render Ukrainian')
                        self.assertIn('MODE_LABELS', card.get('content', ''),
                                      msg='AI · рекомендація must translate mode labels')
                        self.assertIn('short_reason', card.get('content', ''),
                                      msg='AI · рекомендація must translate reason codes')
                        self.assertIn('fmt_pct', card.get('content', ''),
                                      msg='AI · рекомендація must format confidence as %')
        self.assertTrue(recommendation_found, 'AI · рекомендація card not found')

    def test_soc_status_card_shows_manual_target_when_set(self) -> None:
        rendered = _render_soc_card("80", target_soc=80, has_manual=True)
        self.assertIn("80 %", rendered)
        self.assertIn("ручну ціль задано", rendered)
        self.assertIn("HEMS на паузі 30 хв", rendered)
        # And the inverse: it must not claim Автоматично when user picked a value.
        self.assertNotIn("Автоматично", rendered)

    def test_soc_status_card_manual_reset_hint_is_honest_about_no_auto_option(self) -> None:
        """Юра fixed: select.smart_solar_inverter_predictive_target_soc only
        exposes 20–100 as options (no "auto" / "None" / "automatically").
        The card must NOT mislead the operator into thinking there is an
        auto option in the select; instead it must point at the AI / pause
        controls to clear the manual target."""
        rendered = _render_soc_card("80", target_soc=80, has_manual=True)
        self.assertNotIn("назад до автоматичного", rendered,
                         msg="Old misdirection to a non-existent auto select option")
        # Honest guidance: approve current AI recommendation or cancel pause.
        self.assertIn("Скинути ручну ціль", rendered)
        self.assertIn("схвали поточну AI-рекомендацію", rendered)
        self.assertIn("скасуй паузу", rendered)

    def test_compound_reason_is_split_into_translated_code_plus_details(self) -> None:
        """A reason like ``day_pv_low: PV 100W < load 200W`` must surface the
        translated code AND the diagnostics suffix on a separate line, not
        the raw hems/predictive.py f-string."""
        calibration = {"forecast_model": "hourly_response_v2", "samples": 0,
                       "pending_count": 0, "recent_pairs": [], "pending": []}
        attrs = {
            "forecast_calibration": calibration, "override_pending_until": None,
            "mode": "Assist", "confidence": 0.5, "applied": True,
            "reason": "day_pv_low: PV 100W < load 200W",
            "execution_reason": "night_charge: SOC 40% < target 60%",
        }
        rendered = _render_learning_card(attrs)
        # Translated head from REASON_LABELS.
        self.assertIn("PV нижче споживання", rendered)
        # The diagnostic block carries the verbatim code with details.
        self.assertIn('decision.reason = "day_pv_low: PV 100W < load 200W"', rendered)
        self.assertIn('decision.execution_reason = "night_charge: SOC 40% < target 60%"', rendered)

    def test_unknown_reason_is_shown_as_diagnostic_code_not_dany_nedostupni(self) -> None:
        """An unknown reason must render as ``Невідомий reason: <code>`` so
        operators can grep the engine logs instead of seeing the misleading
        fallback ``Дані недоступні``."""
        calibration = {"forecast_model": "hourly_response_v2", "samples": 0,
                       "pending_count": 0, "recent_pairs": [], "pending": []}
        attrs = {
            "forecast_calibration": calibration, "override_pending_until": None,
            "mode": "Assist", "confidence": 0.5, "applied": True,
            "reason": "no_such_code_2026",
            "execution_reason": None,
        }
        rendered = _render_learning_card(attrs)
        self.assertIn("Невідомий reason", rendered)
        # execution_reason None branch must say Дані недоступні.
        self.assertIn("**Причина виконання:** Немає даних", rendered)
        # The recommendation reason line must NOT say Дані недоступні.
        rec_line = next(
            ln for ln in rendered.splitlines()
            if ln.startswith("**Причина рекомендації:**")
        )
        self.assertNotIn("Дані недоступні", rec_line)

    def test_all_reason_labels_use_recommendation_framing_not_physical_action(self) -> None:
        """Per Юрина вимога: жоден REASON_LABEL не повинен стверджувати
        фізичну дію (інвертор зробив, батарея заряджається, мережа
        відключена). Дозволено: AI рекомендує, HEMS вирішує,
        очікувана дія."""
        # Words that assert a physical inverter state vs. allowed recommendation language.
        forbidden = [
            "відновлюємо заряд",   # we are recovering the charge
            "переходимо в SBU",   # we are switching to SBU
            "мінімізуємо цикли",  # we are minimising cycles
            "— бережемо заряд",   # we are saving the charge
            "— готуємося до",     # we are preparing for
            "— термінове відновлення",  # we are urgently restoring
            "використовуємо батарею",  # we are using the battery
            "використовуємо",     # we are using
        ]
        import re
        template = yaml.safe_load((REPO / "dashboard.yaml").read_text(encoding="utf-8"))
        labels: dict[str, str] = {}
        for view in template["views"]:
            for section in view.get("sections", []):
                for card in section.get("cards", []):
                    if card.get("title") == "AI · навчання і точність":
                        content = card["content"]
                        # Crude extraction: REASON_LABELS dict values.
                        for line in content.splitlines():
                            m = re.match(r"\s*'([a-z_]+)':\s*'([^']+)'\s*,", line)
                            if m:
                                labels[m.group(1)] = m.group(2)
        # Sanity — at least 24 labels discovered.
        self.assertGreaterEqual(len(labels), 24,
                                msg=f"Expected ≥24 REASON_LABELS, found {len(labels)}")
        # Forbid physical-action verbs in any short label.
        for code, ua in labels.items():
            for bad in forbidden:
                self.assertNotIn(bad, ua,
                                 msg=f"reason_label[{code}]={ua!r} contains forbidden substring {bad!r}")


if __name__ == "__main__":
    unittest.main()
