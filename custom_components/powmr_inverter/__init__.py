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
    """Create and register the Smart Solar dashboard on first setup.

    Writes the YAML definition and registers it with lovelace so the
    dashboard appears in the sidebar automatically — no manual config needed.
    """
    import os

    dash_dir = os.path.join(hass.config.config_dir, "dashboards")
    dash_file = os.path.join(dash_dir, "powmr_dashboard.yaml")

    # Read the bundled dashboard YAML (async-safe via executor)
    src = os.path.join(os.path.dirname(__file__), "dashboard.yaml")

    def _read_dashboard() -> str | None:
        if not os.path.exists(src):
            return None
        with open(src, "r", encoding="utf-8") as f:
            return f.read()

    dashboard_yaml = await hass.async_add_executor_job(_read_dashboard)
    if dashboard_yaml is None:
        _LOGGER.warning("Dashboard YAML not found at %s, skipping", src)
        return

    # Write dashboard file (async-safe via executor)
    def _write_dashboard() -> bool:
        if os.path.exists(dash_file):
            return False  # already exists
        os.makedirs(dash_dir, exist_ok=True)
        with open(dash_file, "w", encoding="utf-8") as f:
            f.write(dashboard_yaml)
        return True

    written = await hass.async_add_executor_job(_write_dashboard)
    if written:
        _LOGGER.info("Dashboard written to %s", dash_file)

    # Register dashboard in lovelace storage so it appears in the sidebar
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
