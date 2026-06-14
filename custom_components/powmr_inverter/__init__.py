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
        await _auto_install_dashboard(hass)

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


async def _auto_install_dashboard(hass: HomeAssistant) -> None:
    """Write the bundled dashboard YAML to the config dir on first setup.

    Does NOT modify configuration.yaml — the user still needs to add
    the ``lovelace.dashboards`` block manually (one-time), or the
    dashboard shows up under Settings → Dashboards → Add Dashboard.
    """
    import os, pathlib

    dash_dir = os.path.join(hass.config.config_dir, "dashboards")
    dash_file = os.path.join(dash_dir, "powmr_dashboard.yaml")

    # Try to copy from the component directory first (bundled with HACS)
    src = os.path.join(os.path.dirname(__file__), "dashboard.yaml")
    if not os.path.exists(src):
        # Fallback: look in the repo root (dev mode / manual install)
        repo_root = os.path.join(os.path.dirname(__file__), "..", "..", "..")
        src = os.path.join(repo_root, "homeassistant", "powmr_dashboard.yaml")
        if not os.path.exists(src):
            _LOGGER.warning("Dashboard YAML not found, skipping auto-install")
            return

    os.makedirs(dash_dir, exist_ok=True)
    if not os.path.exists(dash_file):
        try:
            with open(src, "r", encoding="utf-8") as inf:
                dashboard_yaml = inf.read()
            with open(dash_file, "w", encoding="utf-8") as outf:
                outf.write(dashboard_yaml)
            _LOGGER.info(
                "Dashboard written to %s — add to configuration.yaml or "
                "Settings → Dashboards → Add Dashboard to activate",
                dash_file,
            )
        except OSError as exc:
            _LOGGER.error("Failed to write dashboard YAML: %s", exc)
