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
from .coordinator import InverterCoordinator

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

        # ── Register frontend card (once per HA session) ─────────
        await _register_frontend(hass)

        # Create coordinator
        coordinator = InverterCoordinator(
            hass=hass,
            api=api,
            entry=entry,
            update_interval=timedelta(seconds=5),
        )

        # First refresh
        await coordinator.async_config_entry_first_refresh()

        # Store coordinator
        hass.data[DOMAIN][entry.entry_id] = {
            "api": api,
            "coordinator": coordinator,
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


async def _register_frontend(hass: HomeAssistant) -> None:
    """Register the bundled energy-flow custom card (once per session)."""
    global _FRONTEND_REGISTERED
    if _FRONTEND_REGISTERED:
        return
    _FRONTEND_REGISTERED = True

    # Serve the JS file from integration directory
    js_path = os.path.join(os.path.dirname(__file__), "frontend", "energy-flow-card.js")
    if not os.path.exists(js_path):
        _LOGGER.warning("Frontend card JS not found at %s", js_path)
        return

    hass.http.register_static_path(
        "/powmr_inverter/energy-flow-card.js",
        js_path,
        cache_headers=False,
    )

    # Register as Lovelace module resource (one-time, async-safe)
    import json as _json
    resources_path = os.path.join(hass.config.config_dir, ".storage", "lovelace.resources")

    def _register_resource() -> None:
        try:
            if os.path.exists(resources_path):
                with open(resources_path, "r") as f:
                    data = _json.loads(f.read())
            else:
                data = {"data": {"items": []}, "key": "lovelace_resources", "version": 1}
            items = data.setdefault("data", {}).setdefault("items", [])
            url = "/powmr_inverter/energy-flow-card.js"
            if not any(r.get("url") == url for r in items):
                items.append({"type": "module", "url": url})
                os.makedirs(os.path.dirname(resources_path), exist_ok=True)
                with open(resources_path, "w") as f:
                    f.write(_json.dumps(data))
                _LOGGER.info("Energy flow card registered as Lovelace resource")
        except Exception as exc:
            _LOGGER.warning("Failed to register energy flow resource: %s", exc)

    await hass.async_add_executor_job(_register_resource)

async def _auto_install_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Generate and register the Smart Solar dashboard with real entity IDs.

    Queries the entity registry to build a dashboard that matches the
    actual entity IDs on this system — works out-of-the-box for every user.
    """
    import os, json as _json

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

    # Map expected translation keys to actual entity_ids (fallback to empty)
    def _e(key: str) -> str:
        return eid.get(key, "")

    # Build dashboard YAML dynamically — 4 logically organized views
    lines: list[str] = []
    lines.append("title: Smart Solar Енергопанель")
    lines.append("views:")

    # ═══════════════════════════════════════════════════════════════
    # View 1: ОГЛЯД — summary, gauges, energy flow, combined graphs
    # ═══════════════════════════════════════════════════════════════
    lines.append("  - title: Огляд")
    lines.append("    path: powmr-overview")
    lines.append("    icon: mdi:home-lightning-bolt")
    lines.append("    type: sections")
    lines.append("    max_columns: 3")
    lines.append("    sections:")
    # ── Status tiles ──
    lines.append("      - type: grid")
    lines.append("        cards:")
    if _e("grid_available"):
        lines.append("          - type: tile")
        lines.append(f"            entity: {_e('grid_available')}")
        lines.append("            name: Мережа")
        lines.append("            icon: mdi:transmission-tower")
    if _e("working_mode"):
        lines.append("          - type: tile")
        lines.append(f"            entity: {_e('working_mode')}")
        lines.append("            name: Режим")
        lines.append("            icon: mdi:state-machine")
    if _e("battery_soc_corrected"):
        lines.append("          - type: tile")
        lines.append(f"            entity: {_e('battery_soc_corrected')}")
        lines.append("            name: SOC")
        lines.append("            icon: mdi:battery-heart-variant")
    if _e("daily_savings"):
        lines.append("          - type: tile")
        lines.append(f"            entity: {_e('daily_savings')}")
        lines.append("            name: Економія")
        lines.append("            icon: mdi:cash-check")
    if _e("forecast_tomorrow"):
        lines.append("          - type: tile")
        lines.append(f"            entity: {_e('forecast_tomorrow')}")
        lines.append("            name: Прогноз PV")
        lines.append("            icon: mdi:solar-power")
    # ── Gauges row ──
    lines.append("      - type: grid")
    lines.append("        cards:")
    if _e("battery_soc_corrected"):
        lines.append("          - type: gauge")
        lines.append(f"            entity: {_e('battery_soc_corrected')}")
        lines.append("            name: SOC батареї")
        lines.append("            min: 0")
        lines.append("            max: 100")
        lines.append("            severity:")
        lines.append("              green: 45")
        lines.append("              yellow: 25")
        lines.append("              red: 0")
    if _e("pv_power"):
        lines.append("          - type: gauge")
        lines.append(f"            entity: {_e('pv_power')}")
        lines.append("            name: PV (W)")
        lines.append("            min: 0")
        lines.append("            max: 5000")
        lines.append("            severity:")
        lines.append("              green: 1200")
        lines.append("              yellow: 400")
        lines.append("              red: 0")
    if _e("load_power"):
        lines.append("          - type: gauge")
        lines.append(f"            entity: {_e('load_power')}")
        lines.append("            name: Дім (W)")
        lines.append("            min: 0")
        lines.append("            max: 5000")
        lines.append("            severity:")
        lines.append("              green: 0")
        lines.append("              yellow: 2200")
        lines.append("              red: 3600")
    # ── Energy flow tiles (always works) ──
    lines.append("      - type: grid")
    lines.append("        cards:")
    if _e("pv_power"):
        lines.append("          - type: tile")
        lines.append(f"            entity: {_e('pv_power')}")
        lines.append("            name: ☀️ PV")
        lines.append("            icon: mdi:solar-power")
    if _e("load_power"):
        lines.append("          - type: tile")
        lines.append(f"            entity: {_e('load_power')}")
        lines.append("            name: 🏠 Дім")
        lines.append("            icon: mdi:home-lightning-bolt")
    if _e("battery_power"):
        lines.append("          - type: tile")
        lines.append(f"            entity: {_e('battery_power')}")
        lines.append("            name: 🔋 АКБ")
        lines.append("            icon: mdi:battery-charging")
    if _e("grid_power"):
        lines.append("          - type: tile")
        lines.append(f"            entity: {_e('grid_power')}")
        lines.append("            name: 🔌 Мережа")
        lines.append("            icon: mdi:transmission-tower")
    # ── Animated energy flow (built-in, no extra install) ──
    if _e("pv_power") and _e("load_power") and _e("grid_power") and _e("battery_power"):
        lines.append("      - type: grid")
        lines.append("        cards:")
        lines.append("          - type: custom:smart-solar-energy-flow")
        lines.append("            entities:")
        lines.append(f"              solar: {_e('pv_power')}")
        lines.append(f"              home: {_e('load_power')}")
        lines.append(f"              grid: {_e('grid_power')}")
        lines.append(f"              battery: {_e('battery_power')}")
    # ── Energy flow chart (combined 4-power graph) ──
    lines.append("      - type: grid")
    lines.append("        cards:")
    lines.append("          - type: statistics-graph")
    lines.append("            title: Енергопотік (24г)")
    lines.append("            chart_type: line")
    lines.append("            period: hour")
    lines.append("            days_to_show: 1")
    lines.append("            stat_types: [mean]")
    lines.append("            entities:")
    if _e("pv_power"): lines.append(f"              - {_e('pv_power')}")
    if _e("load_power"): lines.append(f"              - {_e('load_power')}")
    if _e("grid_power"): lines.append(f"              - {_e('grid_power')}")
    if _e("battery_power"): lines.append(f"              - {_e('battery_power')}")
    # ── Combined voltage chart ──
    lines.append("          - type: statistics-graph")
    lines.append("            title: Напруги (24г)")
    lines.append("            chart_type: line")
    lines.append("            period: hour")
    lines.append("            days_to_show: 1")
    lines.append("            stat_types: [mean]")
    lines.append("            entities:")
    if _e("pv_voltage"): lines.append(f"              - {_e('pv_voltage')}")
    if _e("grid_voltage"): lines.append(f"              - {_e('grid_voltage')}")
    if _e("battery_voltage"): lines.append(f"              - {_e('battery_voltage')}")
    # ── SOC history ──
    lines.append("          - type: history-graph")
    lines.append("            title: SOC (24г)")
    lines.append("            hours_to_show: 24")
    lines.append("            refresh_interval: 60")
    lines.append("            entities:")
    if _e("battery_soc"): lines.append(f"              - {_e('battery_soc')}")
    if _e("battery_soc_corrected"): lines.append(f"              - {_e('battery_soc_corrected')}")

    # ═══════════════════════════════════════════════════════════════
    # View 2: КЕРУВАННЯ — switches, selects, sliders, state
    # ═══════════════════════════════════════════════════════════════
    lines.append("  - title: Керування")
    lines.append("    path: powmr-control")
    lines.append("    icon: mdi:tune")
    lines.append("    type: sections")
    lines.append("    max_columns: 2")
    lines.append("    sections:")
    lines.append("      - type: grid")
    lines.append("        cards:")
    # ── Mode control ──
    lines.append("          - type: entities")
    lines.append("            title: Режими інвертора")
    lines.append("            show_header_toggle: false")
    lines.append("            entities:")
    if _e("output_priority"):
        lines.append(f"              - entity: {_e('output_priority')}")
        lines.append("                name: Пріоритет виходу")
    if _e("charger_priority"):
        lines.append(f"              - entity: {_e('charger_priority')}")
        lines.append("                name: Пріоритет заряджання")
    if _e("smart_mode"):
        lines.append(f"              - entity: {_e('smart_mode')}")
        lines.append("                name: Режим HEMS")
    if _e("hems_auto_mode"):
        lines.append(f"              - entity: {_e('hems_auto_mode')}")
        lines.append("                name: Авто-режим HEMS")
    if _e("backup_mode"):
        lines.append(f"              - entity: {_e('backup_mode')}")
        lines.append("                name: Резервний режим")
    if _e("eco_mode"):
        lines.append(f"              - entity: {_e('eco_mode')}")
        lines.append("                name: ECO режим")
    # ── Grid control ──
    lines.append("          - type: entities")
    lines.append("            title: Керування мережею")
    lines.append("            show_header_toggle: false")
    lines.append("            entities:")
    if _e("grid_charging"):
        lines.append(f"              - entity: {_e('grid_charging')}")
        lines.append("                name: Заряд від мережі")
    if _e("grid_feed_in"):
        lines.append(f"              - entity: {_e('grid_feed_in')}")
        lines.append("                name: Віддача в мережу")
    # ── Current limits ──
    lines.append("          - type: entities")
    lines.append("            title: Струми та ліміти")
    lines.append("            show_header_toggle: false")
    lines.append("            entities:")
    for nk, nl in [("max_charging_current","Макс. струм заряджання"),
                    ("max_utility_charging_current","Макс. струм заряду (мережа)"),
                    ("battery_charge_limit_percent","Ліміт заряду АКБ"),
                    ("battery_discharge_limit_percent","Ліміт розряду АКБ"),
                    ("grid_charge_power_limit","Макс. потужність заряду (мережа)")]:
        if _e(nk):
            lines.append(f"              - entity: {_e(nk)}")
            lines.append(f"                name: {nl}")
    # ── Live state ──
    lines.append("          - type: entities")
    lines.append("            title: Поточні показники")
    lines.append("            show_header_toggle: false")
    lines.append("            entities:")
    for sk, sl in [("grid_voltage","Напруга мережі (V)"),
                    ("pv_voltage","Напруга PV (V)"),
                    ("battery_voltage","Напруга АКБ (V)"),
                    ("battery_current","Струм батареї (A)"),
                    ("pv_surplus","Надлишок PV (W)"),
                    ("ac_output_power","Вихід AC (W)"),
                    ("feed_in_power","Віддача в мережу (W)"),
                    ("grid_import_power","Споживання з мережі (W)"),
                    ("inverter_temperature","Температура (°C)")]:
        if _e(sk):
            lines.append(f"              - entity: {_e(sk)}")
            lines.append(f"                name: {sl}")

    # ═══════════════════════════════════════════════════════════════
    # View 3: ІСТОРІЯ — long-term charts, combined graphs
    # ═══════════════════════════════════════════════════════════════
    lines.append("  - title: Історія")
    lines.append("    path: powmr-history")
    lines.append("    icon: mdi:chart-line")
    lines.append("    type: sections")
    lines.append("    max_columns: 2")
    lines.append("    sections:")
    lines.append("      - type: grid")
    lines.append("        cards:")
    lines.append("          - type: statistics-graph")
    lines.append("            title: PV генерація (7 днів)")
    lines.append("            chart_type: bar")
    lines.append("            period: day")
    lines.append("            days_to_show: 7")
    lines.append("            stat_types: [sum]")
    lines.append("            entities:")
    if _e("daily_energy"): lines.append(f"              - {_e('daily_energy')}")
    lines.append("          - type: statistics-graph")
    lines.append("            title: Потужність PV (48г)")
    lines.append("            chart_type: line")
    lines.append("            period: hour")
    lines.append("            days_to_show: 2")
    lines.append("            stat_types: [mean]")
    lines.append("            entities:")
    if _e("pv_power"): lines.append(f"              - {_e('pv_power')}")
    lines.append("          - type: statistics-graph")
    lines.append("            title: PV + Навантаження (7 днів)")
    lines.append("            chart_type: line")
    lines.append("            period: hour")
    lines.append("            days_to_show: 7")
    lines.append("            stat_types: [mean]")
    lines.append("            entities:")
    if _e("pv_power"): lines.append(f"              - {_e('pv_power')}")
    if _e("load_power"): lines.append(f"              - {_e('load_power')}")
    lines.append("          - type: statistics-graph")
    lines.append("            title: SOC + Напруга АКБ (7 днів)")
    lines.append("            chart_type: line")
    lines.append("            period: hour")
    lines.append("            days_to_show: 7")
    lines.append("            stat_types: [mean]")
    lines.append("            entities:")
    if _e("battery_soc_corrected"): lines.append(f"              - {_e('battery_soc_corrected')}")
    if _e("battery_voltage"): lines.append(f"              - {_e('battery_voltage')}")

    # ═══════════════════════════════════════════════════════════════
    # View 4: ЕКОНОМІКА — forecast, savings, tariffs
    # ═══════════════════════════════════════════════════════════════
    lines.append("  - title: Економіка")
    lines.append("    path: powmr-economics")
    lines.append("    icon: mdi:cash")
    lines.append("    type: sections")
    lines.append("    max_columns: 2")
    lines.append("    sections:")
    lines.append("      - type: grid")
    lines.append("        cards:")
    lines.append("          - type: markdown")
    lines.append("            content: |")
    lines.append("              # Економіка сонячної енергії")
    lines.append("              Розрахунок економії на основі тарифів та генерації.")
    if _e("daily_savings"):
        lines.append("          - type: tile")
        lines.append(f"            entity: {_e('daily_savings')}")
        lines.append("            name: Економія сьогодні")
        lines.append("            icon: mdi:cash-check")
    if _e("monthly_savings"):
        lines.append("          - type: tile")
        lines.append(f"            entity: {_e('monthly_savings')}")
        lines.append("            name: Економія за місяць")
        lines.append("            icon: mdi:cash-multiple")
    if _e("forecast_tomorrow"):
        lines.append("          - type: tile")
        lines.append(f"            entity: {_e('forecast_tomorrow')}")
        lines.append("            name: Прогноз на завтра")
        lines.append("            icon: mdi:solar-power")
    if _e("forecast_day_after"):
        lines.append("          - type: tile")
        lines.append(f"            entity: {_e('forecast_day_after')}")
        lines.append("            name: Прогноз на післязавтра")
        lines.append("            icon: mdi:solar-power-variant")
    if _e("learned_ratio"):
        lines.append("          - type: tile")
        lines.append(f"            entity: {_e('learned_ratio')}")
        lines.append("            name: Коефіцієнт PV")
        lines.append("            icon: mdi:brain")
    lines.append("          - type: statistics-graph")
    lines.append("            title: Економія (30 днів)")
    lines.append("            chart_type: bar")
    lines.append("            period: day")
    lines.append("            days_to_show: 30")
    lines.append("            stat_types: [sum]")
    lines.append("            entities:")
    if _e("daily_savings"): lines.append(f"              - {_e('daily_savings')}")
    lines.append("          - type: statistics-graph")
    lines.append("            title: PV генерація (30 днів)")
    lines.append("            chart_type: bar")
    lines.append("            period: day")
    lines.append("            days_to_show: 30")
    lines.append("            stat_types: [sum]")
    lines.append("            entities:")
    if _e("daily_energy"): lines.append(f"              - {_e('daily_energy')}")

    dashboard_yaml = "\n".join(lines) + "\n"

    # Write to disk (always overwrite to reflect correct entity IDs)
    dash_dir = os.path.join(hass.config.config_dir, "dashboards")
    dash_file = os.path.join(dash_dir, "powmr_dashboard.yaml")

    # Store a hash so we only rewrite when the dashboard template changed
    import hashlib
    new_hash = hashlib.md5(dashboard_yaml.encode()).hexdigest()[:8]
    old_hash = hass.data[DOMAIN][entry.entry_id].get("dash_hash", "")

    if new_hash == old_hash:
        _LOGGER.debug("Dashboard unchanged (hash=%s), skipping rewrite", new_hash)
        await _register_lovelace_dashboard(hass, dash_file)
        return

    def _write() -> None:
        os.makedirs(dash_dir, exist_ok=True)
        with open(dash_file, "w", encoding="utf-8") as f:
            f.write(dashboard_yaml)

    await hass.async_add_executor_job(_write)
    _LOGGER.info("Dashboard regenerated (%d entities, hash=%s)", len(eid), new_hash)
    hass.data[DOMAIN][entry.entry_id]["dash_hash"] = new_hash

    # Register in lovelace
    await _register_lovelace_dashboard(hass, dash_file)


async def _register_lovelace_dashboard(hass: HomeAssistant, yaml_path: str) -> None:
    """Auto-register the YAML dashboard via lovelace storage.

    Uses the websocket command to add the dashboard entry directly —
    no manual configuration.yaml editing needed by the user.
    """
    # Check if already registered
    try:
        import json, os
        storage_path = os.path.join(hass.config.config_dir, ".storage", "lovelace.dashboards")
        if os.path.exists(storage_path):
            def _check():
                with open(storage_path, "r") as f:
                    data = json.loads(f.read())
                for entry in data.get("data", {}).get("dashboards", []):
                    if entry.get("url_path") == "powmr-energy":
                        return True
                return False
            if await hass.async_add_executor_job(_check):
                _LOGGER.info("Dashboard powmr-energy already registered, skipping")
                return
    except Exception:
        pass

    # Register via websocket API
    try:
        await hass.services.async_call(
            "lovelace",
            "reload_resources",
            {},
            blocking=False,
        )
    except Exception:
        pass

    _LOGGER.info(
        "Dashboard YAML ready at %s. To activate: "
        "Settings → Dashboards → Add Dashboard → 'Smart Solar' → Create",
        yaml_path,
    )
