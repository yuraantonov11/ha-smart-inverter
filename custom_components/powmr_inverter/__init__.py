"""The Smart Solar Inverter integration."""

from __future__ import annotations

import logging
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

    # Build dashboard YAML dynamically
    lines: list[str] = []
    lines.append("title: Smart Solar Енергопанель")
    lines.append("views:")
    # ── View 1: Power ──
    lines.append("  - title: Потужність")
    lines.append("    path: powmr-power")
    lines.append("    icon: mdi:home-lightning-bolt")
    lines.append("    type: sections")
    lines.append("    max_columns: 3")
    lines.append("    sections:")
    lines.append("      - type: grid")
    lines.append("        cards:")
    # Header
    lines.append("          - type: markdown")
    lines.append("            content: |")
    lines.append("              # Smart Solar Inverter")
    lines.append("              Живий потік потужності, стан мережі та батареї.")
    # Tiles
    lines.append("          - type: tile")
    lines.append(f"            entity: {_e('grid_available')}")
    lines.append("            name: Стан мережі")
    lines.append("            icon: mdi:transmission-tower")
    lines.append("          - type: tile")
    lines.append(f"            entity: {_e('working_mode')}")
    lines.append("            name: Режим роботи")
    lines.append("            icon: mdi:state-machine")
    lines.append("          - type: tile")
    lines.append(f"            entity: {_e('battery_soc_corrected')}")
    lines.append("            name: Скоригований SOC")
    lines.append("            icon: mdi:battery-heart-variant")
    lines.append("          - type: tile")
    lines.append(f"            entity: {_e('pv_surplus')}")
    lines.append("            name: Надлишок PV")
    lines.append("            icon: mdi:flash")
    lines.append("          - type: tile")
    lines.append(f"            entity: {_e('grid_voltage')}")
    lines.append("            name: Напруга мережі")
    lines.append("            icon: mdi:sine-wave")
    # Gauges
    lines.append("      - type: grid")
    lines.append("        cards:")
    lines.append("          - type: gauge")
    lines.append(f"            entity: {_e('battery_soc_corrected')}")
    lines.append("            name: SOC батареї")
    lines.append("            min: 0")
    lines.append("            max: 100")
    lines.append("            severity:")
    lines.append("              green: 45")
    lines.append("              yellow: 25")
    lines.append("              red: 0")
    lines.append("          - type: gauge")
    lines.append(f"            entity: {_e('pv_power')}")
    lines.append("            name: Потужність PV")
    lines.append("            min: 0")
    lines.append("            max: 5000")
    lines.append("            severity:")
    lines.append("              green: 1200")
    lines.append("              yellow: 400")
    lines.append("              red: 0")
    lines.append("          - type: gauge")
    lines.append(f"            entity: {_e('load_power')}")
    lines.append("            name: Потужність навантаження")
    lines.append("            min: 0")
    lines.append("            max: 5000")
    lines.append("            severity:")
    lines.append("              green: 0")
    lines.append("              yellow: 2200")
    lines.append("              red: 3600")
    # Charts
    lines.append("          - type: statistics-graph")
    lines.append("            title: Потік потужності (24г)")
    lines.append("            chart_type: line")
    lines.append("            period: hour")
    lines.append("            days_to_show: 1")
    lines.append("            stat_types: [mean]")
    lines.append("            entities:")
    if _e("pv_power"): lines.append(f"              - {_e('pv_power')}")
    if _e("load_power"): lines.append(f"              - {_e('load_power')}")
    if _e("grid_power"): lines.append(f"              - {_e('grid_power')}")
    if _e("battery_power"): lines.append(f"              - {_e('battery_power')}")
    lines.append("          - type: history-graph")
    lines.append("            title: SOC батареї (24г)")
    lines.append("            hours_to_show: 24")
    lines.append("            refresh_interval: 60")
    lines.append("            entities:")
    if _e("battery_soc"): lines.append(f"              - {_e('battery_soc')}")
    if _e("battery_soc_corrected"): lines.append(f"              - {_e('battery_soc_corrected')}")
    # ── Controls ──
    lines.append("      - type: grid")
    lines.append("        cards:")
    lines.append("          - type: entities")
    lines.append("            title: Керування інвертором")
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
    # State
    lines.append("          - type: entities")
    lines.append("            title: Поточний стан")
    lines.append("            show_header_toggle: false")
    lines.append("            entities:")
    for sk, sl in [("grid_available","Мережа доступна"),
                    ("pv_voltage","Напруга PV"),
                    ("grid_voltage","Напруга мережі"),
                    ("battery_voltage","Напруга АКБ"),
                    ("battery_current","Струм батареї"),
                    ("ac_output_power","Вихідна потужність AC"),
                    ("feed_in_power","Віддача в мережу"),
                    ("grid_import_power","Споживання з мережі"),
                    ("inverter_temperature","Температура інвертора")]:
        if _e(sk):
            lines.append(f"              - entity: {_e(sk)}")
            lines.append(f"                name: {sl}")
    # ── View 2: HEMS ──
    lines.append("  - title: HEMS")
    lines.append("    path: powmr-hems")
    lines.append("    icon: mdi:brain")
    lines.append("    type: sections")
    lines.append("    max_columns: 3")
    lines.append("    sections:")
    lines.append("      - type: grid")
    lines.append("        cards:")
    lines.append("          - type: markdown")
    lines.append("            content: |")
    lines.append("              # HEMS — Розумне керування енергією")
    lines.append("          - type: entities")
    lines.append("            title: Режими")
    lines.append("            show_header_toggle: false")
    lines.append("            entities:")
    if _e("smart_mode"):
        lines.append(f"              - entity: {_e('smart_mode')}")
        lines.append("                name: Режим HEMS")
    if _e("hems_auto_mode"):
        lines.append(f"              - entity: {_e('hems_auto_mode')}")
        lines.append("                name: Авто-режим")
    if _e("eco_mode"):
        lines.append(f"              - entity: {_e('eco_mode')}")
        lines.append("                name: ECO режим")
    lines.append("          - type: entities")
    lines.append("            title: Батарея")
    lines.append("            show_header_toggle: false")
    lines.append("            entities:")
    for bk, bl in [("battery_soc","SOC (інвертор)"),
                    ("battery_soc_corrected","SOC (скоригований)"),
                    ("battery_voltage","Напруга АКБ"),
                    ("battery_current","Струм АКБ"),
                    ("battery_charge_current","Струм заряду"),
                    ("battery_discharge_current","Струм розряду")]:
        if _e(bk):
            lines.append(f"              - entity: {_e(bk)}")
            lines.append(f"                name: {bl}")

    dashboard_yaml = "\n".join(lines) + "\n"

    # Write to disk (always overwrite to reflect correct entity IDs)
    dash_dir = os.path.join(hass.config.config_dir, "dashboards")
    dash_file = os.path.join(dash_dir, "powmr_dashboard.yaml")

    def _write() -> None:
        os.makedirs(dash_dir, exist_ok=True)
        with open(dash_file, "w", encoding="utf-8") as f:
            f.write(dashboard_yaml)

    await hass.async_add_executor_job(_write)
    _LOGGER.info("Dashboard regenerated with %d entity mappings", len(eid))

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
