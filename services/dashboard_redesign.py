"""Pure, lossless composition of the Smart Solar dashboard.

The four daily-use views contain summaries. Original views remain available as
detail subviews, preserving user cards, entity bindings and old deep links.
This module does not import Home Assistant or select telemetry for control.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


REDESIGN_VERSION = 1
_MARKER = "smart_solar_redesign_version"
_KNOWN_PATHS = {
    "powmr-overview", "powmr-power", "powmr-control", "powmr-history",
    "powmr-economics", "powmr-hems", "powmr-ai", "powmr-outages",
}
_KNOWN_TITLES = {
    "Огляд", "Потужність", "Керування", "Історія", "Економіка",
    "HEMS", "AI · HEMS", "ШІ", "Відключення",
}
_ENTITY_RE = re.compile(r"\b(?:sensor|binary_sensor|select|switch|number)\.[a-z0-9_]+\b")


def _cards(value: Any):
    if isinstance(value, dict):
        if "type" in value:
            yield value
        for child in value.values():
            yield from _cards(child)
    elif isinstance(value, list):
        for child in value:
            yield from _cards(child)


def _section(title: str, *cards: dict, wide: bool = False) -> dict:
    result = {"type": "grid", "cards": [
        {"type": "heading", "heading": title, "heading_style": "title"},
        *[card for card in cards if card],
    ]}
    if wide:
        result["column_span"] = 2
    return result


def _entities(title: str, rows: list[tuple[str, str]]) -> dict:
    entities = [{"entity": entity, "name": name} for entity, name in rows if entity]
    return {"type": "entities", "title": title, "show_header_toggle": False,
            "entities": entities} if entities else {}


def _link(title: str, path: str, icon: str = "mdi:arrow-right") -> dict:
    return {"type": "button", "name": title, "icon": icon,
            "show_state": False, "tap_action": {
                "action": "navigate", "navigation_path": path,
            }}


def _markdown(content: str) -> dict:
    return {"type": "markdown", "content": content.strip()}


def _view(title: str, path: str, sections: list[dict]) -> dict:
    # Omitting view icons deliberately keeps meaningful text in HA's tab bar.
    return {"title": title, "path": path, "type": "sections",
            "max_columns": 3, "dense_section_placement": False,
            "sections": sections}


def _discover_entities(config: dict, entities: dict[str, str]) -> dict[str, str]:
    result = {key: value for key, value in entities.items()
              if isinstance(value, str) and _ENTITY_RE.fullmatch(value)}
    referenced = set()

    def walk(value):
        if isinstance(value, str):
            referenced.update(_ENTITY_RE.findall(value))
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(config)
    # Existing cards may contain entities that were renamed independently from
    # their usual object id. Explicit registry bindings always take precedence.
    for key in ("read_status", "link_state", "proxy", "grid_voltage",
                "battery_voltage", "battery_soc", "pv_power", "load_power"):
        matches = sorted(entity for entity in referenced
                         if entity.split(".", 1)[1].endswith(f"rwb1_ble_{key}"))
        if len(matches) == 1:
            result.setdefault(f"ble_{key}", matches[0])
    return result


def _soc_note(entities: dict[str, str]) -> dict:
    reported = entities.get("battery_soc") or entities.get("ble_battery_soc")
    control = entities.get("battery_soc_corrected")
    if not (reported and control):
        return {}
    return _markdown(
        "{% set reported = states('" + reported + "') | float(none) %}\n"
        "{% set control = states('" + control + "') | float(none) %}\n"
        "{% if reported is not none and control is not none and reported != control %}\n"
        "**SOC різниться:** інвертор {{ reported | round(1) }}%, "
        "керування використовує {{ control | round(1) }}%.\n"
        "{% elif control is not none %}\n"
        "SOC керування: {{ control | round(1) }}%.\n"
        "{% else %}\nЩе немає показника SOC керування.\n{% endif %}"
    )


def _freshness(status: str) -> dict:
    return _markdown(
        "{% set ts = as_timestamp(state_attr('" + status + "', 'last_success'), none) %}\n"
        "{% set iv = state_attr('" + status + "', 'interval_s') | float(10) %}\n"
        "{% set age = (as_timestamp(now()) - ts) if ts is not none else none %}\n"
        "{% if age is none %}\n**Bluetooth:** ще немає успішного читання.\n"
        "{% elif age > ([iv * 3, 30] | max) %}\n"
        "**Останні отримані дані Bluetooth · застаріли:** {{ age | int }} с тому.\n"
        "{% else %}\n**Bluetooth:** отримано {{ age | int }} с тому.\n{% endif %}\n"
        "Лише перегляд. Джерело керування — хмара."
    )


def _decision(entities: dict[str, str]) -> dict:
    entity = entities.get("predictive_decision_state")
    if not entity:
        auto = entities.get("hems_auto_mode")
        return _markdown("Автоматичне керування: {{ states('" + auto + "') }}." if auto else "Очікуємо стану автоматики.")
    return _markdown(
        "{% set mode = state_attr('" + entity + "', 'mode') %}\n"
        "**Прогнозна допомога:** {{ {'Off':'Вимкнено', 'off':'Вимкнено', "
        "'Shadow':'Спостереження', 'shadow':'Спостереження', 'Assist':'Допомога', "
        "'assist':'Допомога'}.get(mode, 'Очікуємо дані') }}.\n\n"
        "{% set applied = state_attr('" + entity + "', 'applied') %}\n"
        "{% if applied is sameas true %}Рекомендацію прийнято HEMS."
        "{% elif applied is sameas false %}Рекомендацію не застосовано."
        "{% else %}Стан застосування ще не отримано.{% endif %}\n\n"
        "{% set reason = state_attr('" + entity + "', 'execution_reason') %}\n"
        "{{ {'battery_drives_load':'Батарея живить будинок — підзарядка не потрібна.', "
        "'already_in_target':'Поточний заряд відповідає цілі.', "
        "'evening_reserve_protection':'Зберігаємо вечірній резерв.', "
        "'forecast_good_use_battery':'За прогнозом можна використовувати батарею.'"
        "}.get(reason, 'Причина та історія рішення — у подробицях автоматики.') }}\n\n"
        "Поточні параметри не є підтвердженням виконання команди інвертором."
    )


def _readiness(entities: dict[str, str]) -> dict:
    entity = entities.get("predictive_decision_state")
    if not entity:
        return _markdown("Очікуємо першого розрахунку прогнозної допомоги.")
    return _markdown(
        "{% set ready = state_attr('" + entity + "', 'readiness') %}\n"
        "{% set limits = state_attr('" + entity + "', 'battery_limits_known') %}\n"
        "{% if ready is sameas true %}\n**Прогнозне керування готове.**\n"
        "{% elif limits is sameas false %}\n"
        "**Потрібні обмеження батареї.** Підтвердьте допустимі потужності "
        "заряджання й розряду в налаштуваннях.\n"
        "{% elif ready is sameas false %}\n"
        "**Поки що лише прогноз.** Очікуємо достатньої якості даних та "
        "завершених пар прогноз–факт.\n"
        "{% else %}\nОчікуємо стану готовності прогнозного керування.\n{% endif %}"
    )


def _forecast_quality(entities: dict[str, str]) -> dict:
    entity = entities.get("predictive_decision_state")
    if not entity:
        return _markdown("Точність ще не виміряна: очікуємо даних поточної моделі.")
    return _markdown(
        "{% set pairs = state_attr('" + entity + "', 'real_pairs') | int(0) %}\n"
        "{% set c = state_attr('" + entity + "', 'forecast_calibration') or {} %}\n"
        "{% if pairs == 0 %}\n**Точність поточної моделі ще не виміряна.** "
        "Очікуємо завершених порівнюваних діб.\n"
        "{% else %}\nЗавершених пар поточної моделі: **{{ pairs }}**.\n{% endif %}\n"
        "{% for days in ['7', '30'] %}\n"
        "{% set w = c.get('completed_windows', {}).get(days, {}) %}\n"
        "{% if w.get('samples', 0) > 0 %}\n"
        "За {{ days }} днів: {{ w.samples }} діб; середня абсолютна помилка "
        "**{{ w.mae_kwh }} кВт·год/добу**.\n{% endif %}\n{% endfor %}\n\n"
        "Якість вимірюється за зафіксованими прогнозами та фактом після "
        "завершення доби. Внутрішня впевненість не є відсотком точності."
    )


def _load_forecast(entities: dict[str, str]) -> dict:
    entity = entities.get("predictive_decision_state")
    if not entity:
        return _markdown("Прогноз споживання з’явиться після накопичення історії.")
    return _markdown(
        "{% set d = state_attr('" + entity + "', 'load_forecast') or {} %}\n"
        "{% if d.get('hourly_w') %}\n"
        "**{{ d.target_date }}:** {{ '%.2f' | format((d.hourly_w | sum) / 1000) }} "
        "кВт·год.\n"
        "{% if d.get('daily_error_range_kwh') %}\n"
        "Діапазон за минулими помилками: "
        "{{ '%.2f' | format(d.daily_error_range_kwh[0]) }}–"
        "{{ '%.2f' | format(d.daily_error_range_kwh[1]) }} кВт·год.\n{% endif %}\n"
        "{% else %}\nЩе немає достатньої історії завершених виміряних діб.\n{% endif %}\n"
        "{% set w = d.get('validation_windows', {}).get('30', {}) %}\n"
        "{% if w.get('samples', 0) > 0 %}\n"
        "Перевірка на історії: {{ w.samples }} діб, середня добова помилка "
        "{{ w.daily_mae_kwh }} кВт·год.\n{% endif %}\n\n"
        "Історична перевірка — окрема оцінка, не журнал раніше виданих прогнозів."
    )


def build_redesigned_dashboard(
    existing_config: dict,
    entity_map: dict[str, str],
    *,
    dashboard_path: str = "powmr-energy",
) -> tuple[dict, dict]:
    """Return a new HA config plus an auditable migration summary.

    Inputs are never mutated. Original integration views are kept as subviews;
    unrelated user views remain visible and unchanged. Running twice is a no-op.
    """
    if not isinstance(existing_config, dict) or not isinstance(existing_config.get("views"), list):
        raise ValueError("Expected a Lovelace config with a views list")
    if existing_config.get(_MARKER) == REDESIGN_VERSION:
        return deepcopy(existing_config), {"changed": False, "version": REDESIGN_VERSION}
    if not dashboard_path or "/" in dashboard_path or not re.fullmatch(r"[a-z0-9_-]+", dashboard_path):
        raise ValueError("Expected a dashboard URL path, without slash")
    config = deepcopy(existing_config)
    sources = config["views"]
    entities = _discover_entities(config, entity_map)
    base = f"/{dashboard_path}/"
    route = lambda path: base + path
    all_cards = list(_cards(sources))
    flows = [card for card in all_cards if card.get("type") == "custom:k-flow-card"]
    if not flows:
        raise ValueError("No existing animated energy-flow card; refusing to replace the dashboard")
    flow = deepcopy(flows[0])
    flow["compact"] = True
    flow["grid_options"] = {"columns": "full", "rows": "auto"}

    # Preserve every original card and view. Only navigation metadata changes on
    # recognized integration views; unknown user views are not hidden or moved.
    new_paths = {"powmr-overview", "powmr-analytics", "powmr-automation", "powmr-system"}
    used_paths = {view.get("path") for view in sources if isinstance(view, dict)} | new_paths
    forecast_path = "powmr-forecasts"
    forecast_number = 2
    while forecast_path in used_paths:
        forecast_path = f"powmr-forecasts-{forecast_number}"
        forecast_number += 1
    used_paths.add(forecast_path)
    details, custom_views, detail_links, renamed = [], [], [], {}
    for index, view in enumerate(sources):
        if not isinstance(view, dict):
            custom_views.append(view)
            continue
        path = view.get("path", "")
        if path not in _KNOWN_PATHS and (path or view.get("title") not in _KNOWN_TITLES):
            custom_views.append(view)
            continue
        old_path = path
        if path in new_paths or not path:
            stem = f"{path or 'powmr-detail'}-details"
            path = stem
            count = 2
            while path in used_paths:
                path = f"{stem}-{count}"
                count += 1
            used_paths.add(path)
            view["path"] = path
            renamed[old_path or str(index)] = path
        view["subview"] = True
        view["back_path"] = route("powmr-overview")
        details.append(view)
        detail_links.append((view.get("title") or "Подробиці", path, old_path))

    def detail_for(*paths: str) -> str | None:
        return next((new for _, new, old in detail_links if old in paths), None)

    live_status = entities.get("ble_read_status", "")
    live_rows = [(entities.get("ble_pv_power", ""), "Сонце · Bluetooth"),
                 (entities.get("ble_load_power", ""), "Будинок · Bluetooth, діагностика"),
                 (entities.get("ble_battery_soc", ""), "SOC за інвертором · Bluetooth")]
    cloud_rows = [(entities.get("pv_power", ""), "Сонце · хмара"),
                  (entities.get("load_power", ""), "Будинок · хмара"),
                  (entities.get("battery_soc", ""), "SOC за інвертором · хмара"),
                  (entities.get("grid_available", ""), "Мережа · хмара")]
    live_cards = []
    if live_status and any(entity for entity, _ in live_rows):
        live_cards.append({"type": "conditional", "conditions": [
            {"condition": "state", "entity": live_status, "state_not": "disabled"},
            {"condition": "state", "entity": live_status, "state_not": "unavailable"},
            {"condition": "state", "entity": live_status, "state_not": "unknown"},
        ], "card": {"type": "vertical-stack", "cards": [
            _entities("Показники інвертора · лише перегляд", live_rows),
            _freshness(live_status),
        ]}})
        live_cards.append({"type": "conditional", "conditions": [
            {"condition": "or", "conditions": [
                {"condition": "state", "entity": live_status, "state": value}
                for value in ("disabled", "unavailable", "unknown")
            ]},
        ], "card": _entities("Показники інвертора · хмара", cloud_rows)})
    else:
        live_cards.append(_entities("Показники інвертора · хмара", cloud_rows))
    live_cards.append(_soc_note(entities))
    auto_cards = [_decision(entities), _link("Автоматика →", route("powmr-automation"), "mdi:robot")]
    overview_sections = [
        _section("Потік енергії", flow, wide=True),
        _section("Показники інвертора", *live_cards),
        _section("Автоматика зараз", *auto_cards),
        _section("Сьогодні та завтра", _entities("Підсумки", [
            (entities.get("daily_energy", ""), "Сонце сьогодні"),
            (entities.get("daily_savings", ""), "Валова оцінка сьогодні"),
            (entities.get("forecast_tomorrow", ""), "Сонце завтра · прогноз"),
        ]), _link("Аналітика →", route("powmr-analytics"), "mdi:chart-line")),
    ]

    analytics_sections = []
    seen_charts = set()
    for card in all_cards:
        if card.get("type") not in {"custom:power-history-card", "custom:solar-energy-chart", "custom:total-energy-card"}:
            continue
        key = (card["type"], card.get("entity"), card.get("attribute"))
        if key in seen_charts:
            continue
        seen_charts.add(key)
        analytics_sections.append(_section(card.get("title") or "Енергія", deepcopy(card), wide=True))
    if not analytics_sections:
        analytics_sections.append(_section("Енергія", _entities("Генерація", [
            (entities.get("daily_energy", ""), "За сьогодні"),
            (entities.get("history_monthly_energy", ""), "За місяць"),
            (entities.get("history_yearly_energy", ""), "За рік"),
        ])))
    analytics_sections.append(_section("Вартість", _entities("Валова оцінка", [
        (entities.get("daily_savings", ""), "Сьогодні"),
        (entities.get("monthly_savings", ""), "За місяць"),
    ]), _markdown("Оцінка вартості енергії розряду батареї. Витрати заряджання та втрати не відняті.")))
    history_path = detail_for("powmr-history", "powmr-power")
    if history_path:
        analytics_sections.append(_section("Графіки та періоди", _link(
            "Потужність, SOC та історія", route(history_path), "mdi:chart-timeline-variant")))

    automation_sections = [_section("Рішення та стан", _decision(entities)),
                           _section("Готовність прогнозного керування", _readiness(entities),
                                    _link("Налаштування батареї", "/config/integrations/integration/powmr_inverter", "mdi:battery-cog"))]
    mode_card = _entities("Автоматичне керування", [
        (entities.get("hems_auto_mode", ""), "Автоматичне керування"),
        (entities.get("smart_mode", ""), "Стратегія"),
        (entities.get("predictive_mode", ""), "Прогнозна допомога"),
    ])
    if mode_card:
        automation_sections.append(_section("Режим", mode_card))
    manual_fields = [
        {"entity": entities[key], "name": label}
        for key, label in [
            ("output_priority", "Живлення будинку"),
            ("charger_priority", "Джерело заряду"),
            ("max_charging_current", "Загальний струм заряду, А"),
            ("max_utility_charging_current", "Струм заряду від мережі, А"),
            ("grid_feed_in", "Віддача в мережу"),
        ] if entities.get(key)
    ]
    if manual_fields:
        automation_sections.append(_section("Ручне керування", {
            "type": "custom:smart-solar-controls-card", "title": "Пріоритети та ліміти",
            "entities": manual_fields,
        }))
    auto_links = [_link("Прогнози та їхня якість", route(forecast_path), "mdi:weather-partly-cloudy")]
    for label, paths, icon in [
        ("Відключення та підготовка", ("powmr-outages",), "mdi:calendar-alert"),
    ]:
        found = detail_for(*paths)
        if found:
            auto_links.append(_link(label, route(found), icon))
    if auto_links:
        automation_sections.append(_section("Дії та подробиці", *auto_links))
    automation_sections.append(_section("Прогноз", _entities("Сонячна генерація", [
        (entities.get("forecast_tomorrow", ""), "Завтра"),
        (entities.get("forecast_day_after", ""), "Післязавтра"),
    ])))

    forecast_sections = [
        _section("Сонячна генерація", _entities("Оновлений прогноз", [
            (entities.get("forecast_tomorrow", ""), "Завтра"),
            (entities.get("forecast_day_after", ""), "Післязавтра"),
        ]), _markdown("Прогноз, не фактична генерація. Дата та дані моделі — у властивостях показника.")),
        _section("Споживання", _load_forecast(entities)),
        _section("Якість сонячного прогнозу", _forecast_quality(entities)),
    ]
    comparison = next((card for card in all_cards if card.get("type") == "custom:pv-comparison-card"), None)
    if comparison:
        forecast_sections.append(_section("Погодинний прогноз і факт", deepcopy(comparison), wide=True))
    forecast_view = _view("Прогнози", forecast_path, forecast_sections)
    forecast_view.update(subview=True, back_path=route("powmr-automation"))

    system_sections = [_section("Підключення", _entities("Джерела даних", [
        (live_status, "Стан читання Bluetooth"),
        (entities.get("ble_proxy", ""), "Bluetooth-проксі"),
    ]), _freshness(live_status) if live_status else {},
        _markdown("**Керування:** хмарні дані. Bluetooth використовується для перегляду.")),
        _section("Налаштування", _link("Параметри інтеграції", "/config/integrations/integration/powmr_inverter", "mdi:cog")),
        _section("Батарея та обладнання", _entities("Поточні виміри · хмара", [
            (entities.get("battery_voltage", ""), "Напруга батареї"),
            (entities.get("battery_current", ""), "Струм батареї"),
            (entities.get("inverter_temperature", ""), "Температура інвертора"),
            (entities.get("grid_voltage", ""), "Напруга мережі"),
        ]))]
    if detail_links:
        system_sections.append(_section("Діагностика та збережені сторінки", *[
            _link(title, route(path), "mdi:information-outline")
            for title, path, _ in detail_links
        ]))
    config["views"] = [
        _view("Огляд", "powmr-overview", overview_sections),
        _view("Аналітика", "powmr-analytics", analytics_sections),
        _view("Автоматика", "powmr-automation", automation_sections),
        _view("Система", "powmr-system", system_sections),
        *custom_views, forecast_view, *details,
    ]
    config[_MARKER] = REDESIGN_VERSION
    return config, {
        "changed": True, "version": REDESIGN_VERSION,
        "visible_views_added": 4, "original_views_preserved": len(sources),
        "detail_views": len(details), "unrelated_views_preserved": len(custom_views),
        "renamed_paths": renamed, "flow_bindings_preserved": True,
        "ble_diagnostic_only": True,
    }
