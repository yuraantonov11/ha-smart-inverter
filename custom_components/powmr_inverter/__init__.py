"""The Smart Solar Inverter integration."""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .api import InverterApiClient
from .const import DOMAIN
from .coordinator import InverterCoordinator, HistoryCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
]

_FRONTEND_REGISTERED = False

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Solar Inverter from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    api = InverterApiClient(
        email=entry.data["email"],
        password=entry.data["password"],
    )

    try:
        # Authenticate
        if not await api.authenticate():
            _LOGGER.error("Failed to authenticate with inverter API")
            return False

        # Create coordinator
        coordinator = InverterCoordinator(
            hass=hass,
            api=api,
            entry=entry,
            update_interval=timedelta(seconds=5),
        )

        # Create history coordinator (15-min polling)
        history_coordinator = HistoryCoordinator(
            hass=hass,
            api=api,
            entry=entry,
        )

        # First refresh
        await coordinator.async_config_entry_first_refresh()

        # First refresh history coordinator (background, non-blocking)
        await history_coordinator.async_config_entry_first_refresh()

        # Store coordinator
        hass.data[DOMAIN][entry.entry_id] = {
            "api": api,
            "coordinator": coordinator,
            "history_coordinator": history_coordinator,
        }

        # Register device
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, api.device_sn or entry.entry_id)},
            manufacturer="Solar Inverter",
            model="Smart Inverter",
            name="Smart Solar Inverter",
            sw_version="1.0.0",
        )

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Register services
        from .services import async_register_services
        await async_register_services(hass)

        # ── Bundle flow card (once per HA start) ─────────────────
        global _FRONTEND_REGISTERED
        if not _FRONTEND_REGISTERED:
            await _install_flow_card(hass)
            _FRONTEND_REGISTERED = True

        # ── Auto-install dashboard on first setup ─────────────────
        await _auto_install_dashboard(hass, entry)

        return True

    except Exception as exc:
        _LOGGER.error("Failed to set up inverter integration: %s", exc)
        await api.close()
        return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        if entry_data is not None:
            api: InverterApiClient | None = entry_data.get("api")
            if api is not None:
                await api.close()
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def _install_flow_card(hass: HomeAssistant) -> None:
    """Bundle k-flow-card JS + icons into www/ and register for auto-load."""
    import shutil

    src_dir = os.path.join(os.path.dirname(__file__), "frontend")
    www_dir = os.path.join(hass.config.config_dir, "www", "community", "powmr-inverter")
    # Also copy icons to the legacy HACS path so a cached (unpatched) JS
    # that still looks at /local/community/k-flow-card/ finds them.
    legacy_dir = os.path.join(hass.config.config_dir, "www", "community", "k-flow-card")
    resource_url = "/local/community/powmr-inverter/k-flow-card.js"

    def _copy_files() -> bool:
        """Copy bundled frontend files to www/ — always overwrite so updates stick."""
        js_src = os.path.join(src_dir, "k-flow-card.js")
        if not os.path.exists(js_src):
            return False
        # Primary destination
        os.makedirs(www_dir, exist_ok=True)
        shutil.copy2(js_src, os.path.join(www_dir, "k-flow-card.js"))
        _LOGGER.info("Installed k-flow-card.js → www/")
        # Forecast sparkline card
        fc_src = os.path.join(src_dir, "forecast-card.js")
        if os.path.exists(fc_src):
            shutil.copy2(fc_src, os.path.join(www_dir, "forecast-card.js"))
            _LOGGER.info("Installed forecast-card.js → www/")
        # Power history chart card (renders API data from attribute arrays)
        ph_src = os.path.join(src_dir, "power-history-card.js")
        if os.path.exists(ph_src):
            shutil.copy2(ph_src, os.path.join(www_dir, "power-history-card.js"))
            _LOGGER.info("Installed power-history-card.js → www/")
        # Icon PNGs → both primary AND legacy path (safety net for cached JS)
        for fname in ("grid-icon.png", "home-icon.png", "ev-charger-icon.png"):
            src = os.path.join(src_dir, fname)
            if not os.path.exists(src):
                continue
            shutil.copy2(src, os.path.join(www_dir, fname))
            os.makedirs(legacy_dir, exist_ok=True)
            shutil.copy2(src, os.path.join(legacy_dir, fname))
        _LOGGER.info("Installed icons → www/ (both primary & legacy paths)")
        return True

    copied = await hass.async_add_executor_job(_copy_files)
    if not copied:
        _LOGGER.warning("k-flow-card.js not found; flow card unavailable")
        return

    try:
        from homeassistant.components.frontend import add_extra_js_url
        # Bump the cache-buster on every release so browsers pick up the new JS
        add_extra_js_url(hass, f"{resource_url}?v=1.8.2")
        # Forecast sparkline card
        fc_url = "/local/community/powmr-inverter/forecast-card.js"
        add_extra_js_url(hass, f"{fc_url}?v=1.8.2")
        # Power history chart card
        ph_url = "/local/community/powmr-inverter/power-history-card.js"
        add_extra_js_url(hass, f"{ph_url}?v=1.8.11")
        _LOGGER.info("Flow card + forecast card + power-history card modules registered")
    except Exception as exc:
        _LOGGER.warning("Could not register flow card: %s", exc)


# ═══════════════════════════════════════════════════════════════════════
# Dashboard builder — returns a plain Python dict (stored as JSON)
# ═══════════════════════════════════════════════════════════════════════

def _tile(entity: str, name: str, icon: str) -> dict:
    return {"type": "tile", "entity": entity, "name": name, "icon": icon}

def _stats(title: str, chart_type: str, period: str, days: int,
           stat_types: list[str], entities: list[str]) -> dict:
    return {
        "type": "statistics-graph",
        "title": title,
        "chart_type": chart_type,
        "period": period,
        "days_to_show": days,
        "stat_types": stat_types,
        "entities": entities,
    }

def _history(title: str, hours: int, refresh: int, entities: list[str]) -> dict:
    return {
        "type": "history-graph",
        "title": title,
        "hours_to_show": hours,
        "refresh_interval": refresh,
        "entities": entities,
    }

def _entities_card(title: str, entity_list: list[dict]) -> dict:
    return {
        "type": "entities",
        "title": title,
        "show_header_toggle": False,
        "entities": entity_list,
    }

def _entity_row(eid: str, name: str | None = None) -> dict:
    row: dict = {"entity": eid}
    if name:
        row["name"] = name
    return row


async def _auto_install_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Generate and register the Smart Solar dashboard with real entity IDs.

    Builds the dashboard as a Python dict (NOT a YAML string) so that
    HA stores it as a JSON object in .storage — avoids the well-known
    "Cannot use 'in' operator to search for 'strategy'" frontend crash.
    """
    import hashlib

    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)

    # Collect all entities belonging to this config entry
    entry_entities = [
        ent for ent in registry.entities.values()
        if ent.config_entry_id == entry.entry_id
    ]
    if not entry_entities:
        _LOGGER.warning("No entities found for dashboard, deferring")
        return

    # Build translation_key → full entity_id mapping
    eid: dict[str, str] = {}
    for ent in entry_entities:
        tk = ent.translation_key
        if tk:
            eid[tk] = ent.entity_id

    # Shorthand: entity by translation_key, empty string if missing
    def _e(key: str) -> str:
        return eid.get(key, "")

    # Helper: only include entity card rows where entity exists
    def _rows(*pairs: tuple[str, str]) -> list[dict]:
        return [_entity_row(_e(k), n) for k, n in pairs if _e(k)]

    # ── Build the dashboard config dict ──────────────────────────
    views: list[dict] = []

    # ═══════════════════════════════════════════════════════════════
    # View 1: ОГЛЯД
    # ═══════════════════════════════════════════════════════════════
    overview_cards: list[dict] = []

    # ── Status tiles ──
    status_grid_cards: list[dict] = []
    for tk, nm, ic in [
        ("grid_available", "Мережа", "mdi:transmission-tower"),
        ("working_mode", "Режим", "mdi:state-machine"),
        ("battery_soc_corrected", "SOC", "mdi:battery-heart-variant"),
        ("daily_savings", "Економія", "mdi:cash-check"),
        ("forecast_tomorrow", "Прогноз PV", "mdi:solar-power"),
    ]:
        if _e(tk):
            status_grid_cards.append(_tile(_e(tk), nm, ic))
    if status_grid_cards:
        overview_cards.append({"type": "grid", "cards": status_grid_cards})

    # ── k-flow-card (must be inside a grid section!) ──
    flow_cfg: dict = {"type": "custom:k-flow-card", "inverter_name": "PowMr"}
    for cfg_key, entity_key in [
        ("pv_total_power", "pv_power"),
        ("grid_active_power", "grid_power"),
        ("consump", "load_power"),
        ("battery_soc", "battery_soc_corrected"),
        ("battery_power", "battery_power"),
        ("battery_voltage", "battery_voltage"),
        ("today_pv", "daily_energy"),
        ("daily_savings", "daily_savings"),
    ]:
        val = _e(entity_key)
        if val:
            flow_cfg[cfg_key] = val
    # Fallback: if battery_soc_corrected missing, try battery_soc
    if "battery_soc" not in flow_cfg and _e("battery_soc"):
        flow_cfg["battery_soc"] = _e("battery_soc")
    flow_cfg["sun"] = "sun.sun"
    flow_cfg["_show_battery"] = True
    overview_cards.append({"type": "grid", "cards": [flow_cfg]})

    # ── Combined energy flow + voltage + SOC graphs ──
    graph_cards: list[dict] = []
    pv = _e("pv_power")
    load = _e("load_power")
    grid = _e("grid_power")
    batt = _e("battery_power")
    if any([pv, load, grid, batt]):
        ents = [x for x in [pv, load, grid, batt] if x]
        graph_cards.append(_stats("Енергопотік (24г)", "line", "hour", 1, ["mean"], ents))
    pv_v = _e("pv_voltage")
    grid_v = _e("grid_voltage")
    batt_v = _e("battery_voltage")
    if any([pv_v, grid_v, batt_v]):
        ents = [x for x in [pv_v, grid_v, batt_v] if x]
        graph_cards.append(_stats("Напруги (24г)", "line", "hour", 1, ["mean"], ents))
    batt_soc = _e("battery_soc")
    batt_soc_c = _e("battery_soc_corrected")
    if any([batt_soc, batt_soc_c]):
        ents = [x for x in [batt_soc, batt_soc_c] if x]
        graph_cards.append(_history("SOC (24г)", 24, 60, ents))
    if graph_cards:
        overview_cards.append({"type": "grid", "cards": graph_cards})

    # ── HEMS diagnostics ──
    hems_rows = _rows(
        ("hems_last_reason", "Причина рішення"),
        ("hems_last_output_cmd", "Команда виходу"),
        ("hems_last_charger_cmd", "Команда заряду"),
        ("hems_auto_mode", "HEMS авто-режим"),
    )
    if hems_rows:
        overview_cards.append({"type": "grid", "cards": [_entities_card("HEMS Стан", hems_rows)]})

    # ── Forecast graph (replaces broken forecast-card) ──
    # Forecast moved to combined generation+forecast chart in History view

    views.append({
        "title": "Огляд",
        "path": "powmr-overview",
        "icon": "mdi:home-lightning-bolt",
        "type": "sections",
        "max_columns": 3,
        "sections": overview_cards,
    })

    # ═══════════════════════════════════════════════════════════════
    # View 2: КЕРУВАННЯ
    # ═══════════════════════════════════════════════════════════════
    control_cards: list[dict] = []

    mode_rows = _rows(
        ("output_priority", "Пріоритет виходу"),
        ("charger_priority", "Пріоритет заряджання"),
        ("smart_mode", "Режим HEMS"),
        ("hems_auto_mode", "Авто-режим HEMS"),
        ("backup_mode", "Резервний режим"),
        ("eco_mode", "ECO режим"),
    )
    if mode_rows:
        control_cards.append({"type": "grid", "cards": [_entities_card("Режими інвертора", mode_rows)]})

    grid_ctrl_rows = _rows(
        ("grid_charging", "Заряд від мережі"),
        ("grid_feed_in", "Віддача в мережу"),
    )
    if grid_ctrl_rows:
        control_cards.append({"type": "grid", "cards": [_entities_card("Керування мережею", grid_ctrl_rows)]})

    limit_rows = _rows(
        ("max_charging_current", "Макс. струм заряджання"),
        ("max_utility_charging_current", "Макс. струм заряду (мережа)"),
        ("battery_charge_limit_percent", "Ліміт заряду АКБ"),
        ("battery_discharge_limit_percent", "Ліміт розряду АКБ"),
        ("grid_charge_power_limit", "Макс. потужність заряду (мережа)"),
    )
    if limit_rows:
        control_cards.append({"type": "grid", "cards": [_entities_card("Струми та ліміти", limit_rows)]})

    state_rows = _rows(
        ("grid_voltage", "Напруга мережі (V)"),
        ("pv_voltage", "Напруга PV (V)"),
        ("battery_voltage", "Напруга АКБ (V)"),
        ("battery_current", "Струм батареї (A)"),
        ("pv_surplus", "Надлишок PV (W)"),
        ("ac_output_power", "Вихід AC (W)"),
        ("feed_in_power", "Віддача в мережу (W)"),
        ("grid_import_power", "Споживання з мережі (W)"),
        ("inverter_temperature", "Температура (°C)"),
    )
    if state_rows:
        control_cards.append({"type": "grid", "cards": [_entities_card("Поточні показники", state_rows)]})

    views.append({
        "title": "Керування",
        "path": "powmr-control",
        "icon": "mdi:tune",
        "type": "sections",
        "max_columns": 2,
        "sections": control_cards,
    })

    # ═══════════════════════════════════════════════════════════════
    # View 3: ІСТОРІЯ
    # ═══════════════════════════════════════════════════════════════
    history_cards: list[dict] = []

    # PV генерація (7 днів) removed — covered by power-history-card monthly
    # PV power (48h) removed — covered by power-history-card daily
    if pv and load:
        history_cards.append({"type": "grid", "cards": [
            _stats("PV + Навантаження (7 днів)", "line", "hour", 7, ["mean"], [pv, load])
        ]})
    soc_ents = [x for x in [_e("battery_soc_corrected"), batt_v] if x]
    if soc_ents:
        history_cards.append({"type": "grid", "cards": [
            _stats("SOC + Напруга АКБ (7 днів)", "line", "hour", 7, ["mean"], soc_ents)
        ]})

    # ── History chart sensors (fetched from API every 15 min) ──
    daily_power_eid = _e("history_daily_power")
    forecast_eid = _e("forecast_tomorrow")
    if daily_power_eid:
        series = [{
            "entity": daily_power_eid,
            "attribute": "hourly_power_kw",
            "labels_attribute": "hourly_labels",
            "color": "#f5b06a",
            "name": "Генерація",
            "unit_divisor": 1,
        }]
        if forecast_eid:
            series.append({
                "entity": forecast_eid,
                "attribute": "hourly_forecast_w",
                "labels_attribute": "",
                "color": "#e74c3c",
                "name": "Прогноз",
                "unit_divisor": 1000,
            })
        history_cards.append({"type": "grid", "cards": [{
            "type": "custom:power-history-card",
            "series": series,
            "title": "Генерація + Прогноз (сьогодні)",
            "unit": "kW",
        }]})

    monthly_energy_eid = _e("history_monthly_energy")
    if monthly_energy_eid:
        history_cards.append({"type": "grid", "cards": [{
            "type": "custom:power-history-card",
            "entity": monthly_energy_eid,
            "attribute": "daily_energy_kwh",
            "labels_attribute": "daily_labels",
            "title": "Енергія PV за місяць",
            "chart_type": "bar",
            "bar_color": "#2ecc71",
        }]})

    yearly_energy_eid = _e("history_yearly_energy")
    if yearly_energy_eid:
        history_cards.append({"type": "grid", "cards": [{
            "type": "custom:power-history-card",
            "entity": yearly_energy_eid,
            "attribute": "monthly_energy_kwh",
            "labels_attribute": "monthly_labels",
            "title": "Енергія PV за рік",
            "chart_type": "bar",
            "bar_color": "#3498db",
        }]})

    total_energy_eid = _e("history_total_energy")
    if total_energy_eid:
        history_cards.append({"type": "grid", "cards": [_tile(
            total_energy_eid, "Загальна енергія", "mdi:solar-power-variant"
        )]})

    views.append({
        "title": "Історія",
        "path": "powmr-history",
        "icon": "mdi:chart-line",
        "type": "sections",
        "max_columns": 2,
        "sections": history_cards,
    })

    # ═══════════════════════════════════════════════════════════════
    # View 4: ЕКОНОМІКА
    # ═══════════════════════════════════════════════════════════════
    econ_cards: list[dict] = []

    econ_tiles: list[dict] = [
        {"type": "markdown", "content": "# Економіка сонячної енергії\nРозрахунок економії на основі тарифів та генерації."}
    ]
    for tk, nm, ic in [
        ("daily_savings", "Економія сьогодні", "mdi:cash-check"),
        ("monthly_savings", "Економія за місяць", "mdi:cash-multiple"),
        ("forecast_tomorrow", "Прогноз на завтра", "mdi:solar-power"),
        ("forecast_day_after", "Прогноз на післязавтра", "mdi:solar-power-variant"),
        ("learned_ratio", "Коефіцієнт PV", "mdi:brain"),
    ]:
        if _e(tk):
            econ_tiles.append(_tile(_e(tk), nm, ic))
    econ_cards.append({"type": "grid", "cards": econ_tiles})

    econ_graphs: list[dict] = []
    ds = _e("daily_savings")
    if ds:
        econ_graphs.append(_stats("Економія (30 днів)", "bar", "day", 30, ["sum"], [ds]))
    if econ_graphs:
        econ_cards.append({"type": "grid", "cards": econ_graphs})

    views.append({
        "title": "Економіка",
        "path": "powmr-economics",
        "icon": "mdi:cash",
        "type": "sections",
        "max_columns": 2,
        "sections": econ_cards,
    })

    # ── Assemble final config dict ───────────────────────────────
    dashboard_config: dict = {
        "title": "Smart Solar Енергопанель",
        "views": views,
    }

    # Hash for change detection
    import json as _json
    config_hash = hashlib.md5(_json.dumps(dashboard_config, sort_keys=True).encode()).hexdigest()[:8]
    old_hash = hass.data[DOMAIN][entry.entry_id].get("dash_hash", "")

    if config_hash != old_hash:
        hass.data[DOMAIN][entry.entry_id]["dash_hash"] = config_hash
        _LOGGER.info("Dashboard config regenerated (%d entities, hash=%s)", len(eid), config_hash)

    # Register in lovelace storage — writes a JSON dict (not YAML string!)
    await _register_lovelace_dashboard(hass, dashboard_config)


async def _register_lovelace_dashboard(hass: HomeAssistant, dashboard_config: dict) -> None:
    """Auto-register the dashboard in storage mode.

    Writes dashboard metadata to .storage/lovelace_dashboards
    and the actual dashboard config to .storage/lovelace.powmr_energy.
    The config is stored as a JSON object — NOT a YAML string — which
    avoids the "Cannot use 'in' operator to search for 'strategy'" crash.
    """
    import json
    import os

    DASHBOARD_URL = "powmr-energy"
    DASHBOARD_TITLE = "Smart Solar Енергопанель"
    DASHBOARD_ID = "powmr_energy"
    config_dir = hass.config.config_dir
    dashboards_storage = os.path.join(config_dir, ".storage", "lovelace_dashboards")
    dashboard_content_storage = os.path.join(config_dir, ".storage", f"lovelace.{DASHBOARD_ID}")

    # Step 1: Check if already registered
    try:
        def _check():
            if not os.path.exists(dashboards_storage):
                return False
            with open(dashboards_storage, "r") as f:
                data = json.loads(f.read())
            for item in data.get("data", {}).get("items", []):
                if item.get("url_path") == DASHBOARD_URL:
                    return True
            return False
        if await hass.async_add_executor_job(_check):
            # Already registered — just update the content
            await _update_dashboard_content(hass, dashboard_content_storage, dashboard_config)
            _LOGGER.debug("Dashboard %s already registered, content updated", DASHBOARD_URL)
            return
    except Exception as exc:
        _LOGGER.debug("Dashboard check failed: %s", exc)

    # Step 2: Register metadata
    try:
        def _register():
            with open(dashboards_storage, "r") as f:
                data = json.loads(f.read())

            items = data.get("data", {}).get("items", [])
            for item in items:
                if item.get("url_path") == DASHBOARD_URL:
                    return True  # concurrent registration

            items.append({
                "id": DASHBOARD_ID,
                "icon": "mdi:solar-power",
                "title": DASHBOARD_TITLE,
                "show_in_sidebar": True,
                "require_admin": False,
                "mode": "storage",
                "url_path": DASHBOARD_URL,
            })
            data["data"]["items"] = items

            with open(dashboards_storage, "w") as f:
                json.dump(data, f, indent=2)
            return True

        result = await hass.async_add_executor_job(_register)
        if result:
            # Step 3: Write dashboard config content
            await _update_dashboard_content(hass, dashboard_content_storage, dashboard_config)
            _LOGGER.info("✅ Dashboard '%s' auto-registered (storage mode, no YAML)", DASHBOARD_TITLE)
        else:
            _LOGGER.debug("Dashboard already registered (concurrent)")

    except Exception as exc:
        _LOGGER.warning("Dashboard auto-register failed: %s", exc)


async def _update_dashboard_content(
    hass: HomeAssistant, storage_path: str, dashboard_config: dict, dashboard_id: str = "powmr_energy"
) -> None:
    """Write the dashboard config to storage as a JSON object.

    CRITICAL: data.config MUST be a dict (JSON object), NOT a YAML string.
    Storing a string causes "Cannot use 'in' operator to search for 'strategy'"
    in the HA frontend because JS receives a string where it expects an object.
    """
    import json

    def _write():
        data = {
            "key": f"lovelace.{dashboard_id}",
            "version": 1,
            "minor_version": 1,
            "key_version": 1,
            "data": {
                "config": dashboard_config,  # ← JSON dict, NOT YAML string!
            },
        }
        with open(storage_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    await hass.async_add_executor_job(_write)
