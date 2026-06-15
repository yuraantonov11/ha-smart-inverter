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
    # ── Animated energy flow (picture-elements + SVG, zero deps) ──
    if _e("pv_power") and _e("load_power") and _e("grid_power") and _e("battery_power"):
        # Read bundled SVG and embed as data URI
        svg_path = os.path.join(os.path.dirname(__file__), "frontend", "flow-bg.svg")
        import base64
        svg_data = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA0MDAgMzAwIj48ZGVmcz48c3R5bGU+QGtleWZyYW1lcyBmbG93eyAwe3N0cm9rZS1kYXNob2Zmc2V0OjI0fSAxMDAle3N0cm9rZS1kYXNob2Zmc2V0OjB9IH1Aa2V5ZnJhbWVzIHB1bHNleyAwJSwxMDAle29wYWNpdHk6LjN9IDUwJXtvcGFjaXR5OjF9IH0ubGluZXtmaWxsOm5vbmU7c3Ryb2tlLXdpZHRoOjM7c3Ryb2tlLWRhc2hhcnJheTo4IDR9Lmwtc3tmaWxsOiNmNTllMGI7c3Ryb2tlOiNmNTllMGI7YW5pbWF0aW9uOmZsb3cgMXMgbGluZWFyIGluZmluaXRlfS5sLWd7ZmlsbDojNmI3MjgwO3N0cm9rZTojNmI3MjgwO2FuaW1hdGlvbjpmbG93IDEuMnMgbGluZWFyIGluZmluaXRlfS5sLWJ7ZmlsbDojMTBiOTgxO3N0cm9rZTojMTBiOTgxO2FuaW1hdGlvbjpmbG93IC44cyBsaW5lYXIgaW5maW5pdGV9LnR4dHtmb250LWZhbWlseTpzYW5zLXNlcmlmO3RleHQtYW5jaG9yOm1pZGRsZTtmb250LXNpemU6MTJweDtmaWxsOiM5Y2EzYWZ9PC9zdHlsZT48L2RlZnM+PHJlY3Qgd2lkdGg9IjQwMCIgaGVpZ2h0PSIzMDAiIHJ4PSIxNiIgZmlsbD0idmFyKC0tY2FyZC1iYWNrZ3JvdW5kLWNvbG9yLCMxZTFlMmUpIi8+PGNpcmNsZSBjeD0iNDAiIGN5PSI0NSIgcj0iMjAiIGZpbGw9IiNmNTllMGIiIG9wYWNpdHk9Ii4yIi8+PGNpcmNsZSBjeD0iNDAiIGN5PSI0NSIgcj0iMTIiIGZpbGw9IiNmNTllMGIiLz48cmVjdCB4PSIxMCIgeT0iNzAiIHdpZHRoPSI1NSIgaGVpZ2h0PSI2IiByeD0iMiIgZmlsbD0iIzNiODJmNiIvPjxyZWN0IHg9IjEwIiB5PSI4MCIgd2lkdGg9IjU1IiBoZWlnaHQ9IjYiIHJ4PSIyIiBmaWxsPSIjM2I4MmY2Ii8+PHJlY3QgeD0iMTAiIHk9IjkwIiB3aWR0aD0iNTUiIGhlaWdodD0iNiIgcng9IjIiIGZpbGw9IiMzYjgyZjYiLz48dGV4dCB4PSIzOCIgeT0iMTEwIiBjbGFzcz0idHh0Ij7imqDvuI8gUFY8L3RleHQ+PHBvbHlnb24gcG9pbnRzPSIxODAsOTAgMjIwLDYwIDI2MCw5MCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjNmI3MjgwIiBzdHJva2Utd2lkdGg9IjIiLz48cmVjdCB4PSIxOTUiIHk9IjkwIiB3aWR0aD0iNTAiIGhlaWdodD0iNDUiIGZpbGw9Im5vbmUiIHN0cm9rZT0iIzZiNzI4MCIgc3Ryb2tlLXdpZHRoPSIyIi8+PHJlY3QgeD0iMjEwIiB5PSIxMTAiIHdpZHRoPSIyMCIgaGVpZ2h0PSIyNSIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjNmI3MjgwIiBzdHJva2Utd2lkdGg9IjEuNSIgcng9IjIiLz48dGV4dCB4PSIyMjAiIHk9IjE1MiIgY2xhc3M9InR4dCI+8J+PoCDQlNGW0Lw8L3RleHQ+PGxpbmUgeDE9IjM0MCIgeTE9IjYwIiB4Mj0iMzQwIiB5Mj0iMTIwIiBzdHJva2U9IiM2YjcyODAiIHN0cm9rZS13aWR0aD0iMiIvPjxsaW5lIHgxPSIzMjAiIHkxPSI3MCIgeDI9IjM2MCIgeTI9IjcwIiBzdHJva2U9IiM2YjcyODAiIHN0cm9rZS13aWR0aD0iMiIvPjxsaW5lIHgxPSIzMjAiIHkxPSI5MCIgeDI9IjM2MCIgeTI9IjkwIiBzdHJva2U9IiM2YjcyODAiIHN0cm9rZS13aWR0aD0iMiIvPjx0ZXh0IHg9IjM0MCIgeT0iMTQwIiBjbGFzcz0idHh0Ij7wn5SMINCe0YDQtdC20LA8L3RleHQ+PHJlY3QgeD0iMTQ1IiB5PSIxODAiIHdpZHRoPSI1MCIgaGVpZ2h0PSIyOCIgcng9IjQiIGZpbGw9Im5vbmUiIHN0cm9rZT0iIzEwYjk4MSIgc3Ryb2tlLXdpZHRoPSIyIi8+PHJlY3QgeD0iMTU4IiB5PSIxNzIiIHdpZHRoPSIyNCIgaGVpZ2h0PSI4IiByeD0iMiIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjMTBiOTgxIiBzdHJva2Utd2lkdGg9IjEuNSIvPjxyZWN0IHg9IjE1MCIgeT0iMTg1IiB3aWR0aD0iMTgiIGhlaWdodD0iMTgiIHJ4PSIyIiBmaWxsPSIjMTBiOTgxIiBvcGFjaXR5PSIuMyIvPjx0ZXh0IHg9IjE3MCIgeT0iMjI1IiBjbGFzcz0idHh0Ij7wn5SXINCQ0JrQkTwvdGV4dD48cGF0aCBkPSJNIDY1IDg1IFEgMTMwIDcwLCAxOTUgOTAiIGNsYXNzPSJsaW5lIGwtcyIvPjxwYXRoIGQ9Ik0gMjQ1IDkwIFEgMjkwIDc1LCAzNDAgOTAiIGNsYXNzPSJsaW5lIGwtZyIvPjxwYXRoIGQ9Ik0gMjIwIDEzNSBRIDIyMCAxNTUsIDE5NSAxODAiIGNsYXNzPSJsaW5lIGwtYiIvPjxwYXRoIGQ9Ik0gMTk1IDE5NSBRIDI1MCAxODUsIDMxMCAxMjAiIGNsYXNzPSJsaW5lIGwtYiIvPjxjaXJjbGUgcj0iNCIgY2xhc3M9ImwtcyI+PGFuaW1hdGVNb3Rpb24gZHVyPSIycyIgcmVwZWF0Q291bnQ9ImluZGVmaW5pdGUiIHBhdGg9Ik0gNjUgODUgUSAxMzAgNzAsIDE5NSA5MCIvPjwvY2lyY2xlPjxjaXJjbGUgcj0iNCIgY2xhc3M9ImwtYiI+PGFuaW1hdGVNb3Rpb24gZHVyPSIxLjVzIiByZXBlYXRDb3VudD0iaW5kZWZpbml0ZSIgcGF0aD0iTSAyMjAgMTM1IFEgMjIwIDE1NSwgMTk1IDE4MCIvPjwvY2lyY2xlPjwvc3ZnPg=="
        try:
            def _read_svg() -> str | None:
                if os.path.exists(svg_path):
                    with open(svg_path, "r", encoding="utf-8") as f:
                        raw = f.read()
                    return "data:image/svg+xml;base64," + base64.b64encode(raw.encode()).decode()
                return None
            svg_data = await hass.async_add_executor_job(_read_svg) or svg_data
        except Exception:
            pass
        lines.append("      - type: grid")
        lines.append("        cards:")
        lines.append("          - type: picture-elements")
        lines.append(f"            image: '{svg_data}'")
        lines.append("            elements:")
        lines.append(f"              - type: state-label")
        lines.append(f"                entity: {_e('pv_power')}")
        lines.append("                style:")
        lines.append("                  left: 8%")
        lines.append("                  top: 8%")
        lines.append("                  font-size: 1.3em")
        lines.append("                  font-weight: bold")
        lines.append("                  color: '#f59e0b'")
        lines.append(f"              - type: state-label")
        lines.append(f"                entity: {_e('load_power')}")
        lines.append("                style:")
        lines.append("                  left: 42%")
        lines.append("                  top: 42%")
        lines.append("                  font-size: 1.3em")
        lines.append("                  font-weight: bold")
        lines.append(f"              - type: state-label")
        lines.append(f"                entity: {_e('grid_power')}")
        lines.append("                style:")
        lines.append("                  left: 76%")
        lines.append("                  top: 42%")
        lines.append("                  font-size: 1.3em")
        lines.append("                  font-weight: bold")
        lines.append(f"              - type: state-label")
        lines.append(f"                entity: {_e('battery_power')}")
        lines.append("                style:")
        lines.append("                  left: 42%")
        lines.append("                  top: 68%")
        lines.append("                  font-size: 1.1em")
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
