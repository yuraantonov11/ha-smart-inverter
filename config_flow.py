"""Config flow for Inverter Smart Inverter integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .api import InverterApiClient, InverterAuthError
from .hems.predictive_control import parse_predictive_options
from .const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    DEFAULT_HEMS_ENABLED,
    DEFAULT_POLL_INTERVAL_SEC,
    DEFAULT_PV_SURPLUS_ENTER_W,
    DEFAULT_RESERVE_SOC,
    DEFAULT_SITE_LATITUDE,
    DEFAULT_SITE_LONGITUDE,
    DOMAIN,
    MAX_POLL_INTERVAL_SEC,
    MIN_POLL_INTERVAL_SEC,
    PREDICTIVE_MODES,
    PREDICTIVE_MODE_DEFAULT,
)

_LOGGER = logging.getLogger(__name__)


def _predictive_schema(current):
    return {
        vol.Optional("predictive_default_mode", default=current.get("predictive_default_mode", "Shadow")): vol.In(["Off", "Shadow", "Assist"]),
        vol.Optional("predictive_night_window_start_hour", default=current.get("predictive_night_window_start_hour", 23)): vol.All(int, vol.Range(min=0, max=23)),
        vol.Optional("predictive_night_window_end_hour", default=current.get("predictive_night_window_end_hour", 7)): vol.All(int, vol.Range(min=0, max=23)),
        vol.Optional("predictive_min_confidence_for_assist", default=current.get("predictive_min_confidence_for_assist", 0.2)): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
    }


class InverterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Solar Inverter."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step.

        R07 multi-device flow:
          1. credentials step (existing)
          2. authenticate + fetch device list
          3. if account has >1 device, present
             a "select_device" step and require
             an explicit choice
          4. selected device is persisted in
             ``entry.data['selected_device_sn']``
             and used by every subsequent
             auth / re-auth / re-load cycle.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]
            try:
                predictive_options = parse_predictive_options(user_input)
            except ValueError:
                return self.async_show_form(step_id="user", data_schema=vol.Schema({
                    vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str,
                    **_predictive_schema(user_input),
                }), errors={"base": "invalid_predictive_options"})

            # Validate credentials
            api = InverterApiClient(email=email, password=password)
            try:
                ok = await api.authenticate()
            except InverterAuthError as exc:
                errors["base"] = "auth_failed"
                _LOGGER.error("Auth failed: %s", exc)
            except Exception as exc:
                errors["base"] = "auth_failed"
                _LOGGER.exception("Unexpected auth error: %s", exc)
            else:
                if not ok:
                    errors["base"] = "no_device"
                else:
                    # R07: if the account has more
                    # than one device, the user
                    # MUST pick one. We stash the
                    # authenticated client on
                    # ``self`` so the next step
                    # can re-use it without a
                    # second login.
                    if api._account_device_count > 1:
                        self._pending_api = api
                        self._pending_email = email
                        self._pending_password = password
                        self._pending_predictive = (
                            predictive_options
                        )
                        return await self.async_step_select_device()
                    # Single device: bind to it
                    # automatically. We still
                    # persist the choice in
                    # ``data`` so the integration
                    # re-binds to the same device
                    # across reloads.
                    chosen_sn = api.device_sn
                    if not chosen_sn:
                        errors["base"] = "no_device"
                    else:
                        await api.close()
                        await self.async_set_unique_id(chosen_sn)
                        self._abort_if_unique_id_configured()

                        return self.async_create_entry(
                            title=f"Solar Inverter ({chosen_sn})",
                            options={**predictive_options, "predictive_mode": predictive_options["predictive_default_mode"].lower()},
                            data={
                                CONF_EMAIL: email,
                                CONF_PASSWORD: password,
                                "selected_device_sn": chosen_sn,
                            },
                        )
            finally:
                if "api" in dir(self) and getattr(self, "_pending_api", None) is not api:
                    await api.close()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.EMAIL,
                        )
                    ),
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD,
                        )
                    ),
                    **_predictive_schema({}),
                }
            ),
            errors=errors,
        )

    async def async_step_select_device(
        self, user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """R07: present the operator with the
        list of devices on the account and
        require an explicit choice.
        """
        api = getattr(self, "_pending_api", None)
        if api is None:
            # No authenticated client — send
            # the user back to the credentials
            # step.
            return self.async_step_user()
        # Re-fetch the device list to make
        # sure the choices are fresh.
        try:
            await api._fetch_device_list(None)
        except Exception as exc:
            _LOGGER.error("Device list refresh failed: %s", exc)
            await api.close()
            return self.async_step_user()

        devices = await api._list_devices()
        if not devices:
            await api.close()
            return self.async_step_user()

        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = user_input.get("selected_device_sn")
            if not chosen or chosen not in [
                d["id"] for d in devices
            ]:
                errors["base"] = "invalid_device"
            else:
                email = self._pending_email
                password = self._pending_password
                predictive_options = self._pending_predictive
                await api.close()
                await self.async_set_unique_id(chosen)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Solar Inverter ({chosen})",
                    options={**predictive_options, "predictive_mode": predictive_options["predictive_default_mode"].lower()},
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                        "selected_device_sn": chosen,
                    },
                )

        # Build a Select selector with one row
        # per device.
        device_options = {
            d["id"]: (
                f"{d['id']} — station {d.get('stationId', '?')}"
                f" — {d.get('dailyProducedQuantity', 0):.1f} kWh today"
            )
            for d in devices
        }
        return self.async_show_form(
            step_id="select_device",
            data_schema=vol.Schema(
                {vol.Required("selected_device_sn"): vol.In(device_options)}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> InverterOptionsFlow:
        """Create the options flow."""
        return InverterOptionsFlow()

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle re-authentication when the token expires."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            email = user_input.get(CONF_EMAIL, entry.data[CONF_EMAIL])
            password = user_input[CONF_PASSWORD]
            api = InverterApiClient(email=email, password=password)
            try:
                ok = await api.authenticate()
            except InverterAuthError:
                errors["base"] = "auth_failed"
            else:
                if ok and api.device_sn:
                    await api.close()
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            CONF_EMAIL: email,
                            CONF_PASSWORD: password,
                        },
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reauth_successful")
                errors["base"] = "auth_failed"
            finally:
                await api.close()

        return self.async_show_form(
            step_id="reauth",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_EMAIL,
                        default=entry.data.get(CONF_EMAIL, ""),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.EMAIL,
                        )
                    ),
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD,
                        )
                    ),
                }
            ),
            errors=errors,
        )


class InverterOptionsFlow(config_entries.OptionsFlow):
    """Handle options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                parse_predictive_options(user_input)
            except ValueError:
                errors["base"] = "invalid_predictive_options"
            poll = user_input.get("poll_interval", DEFAULT_POLL_INTERVAL_SEC)
            if poll < MIN_POLL_INTERVAL_SEC or poll > MAX_POLL_INTERVAL_SEC:
                errors["poll_interval"] = "invalid_poll_interval"
            elif not errors:
                # T16 audit: the reload-vs-apply
                # decision lives in a pure helper
                # (``hems.options_helpers.requires_reload``)
                # so the test suite can exercise
                # it without importing homeassistant.
                from .hems.options_helpers import (
                    requires_reload,
                )

                new_data = {
                    **self.config_entry.options,
                    **user_input,
                }
                if requires_reload(
                    new_data, self.config_entry.options
                ):
                    # A reload-required key changed.
                    # ``async_create_entry`` triggers
                    # HA's automatic reload, which
                    # recreates the coordinator with
                    # the new ``update_interval`` /
                    # API client.
                    return self.async_create_entry(data=new_data)
                # No reload-required key changed —
                # apply the change in-place via
                # ``async_update_entry``. The
                # coordinator re-reads
                # ``entry.options`` on every cycle,
                # so the change takes effect on
                # the next update. The internal
                # persistence keys (feedback,
                # night_window, _energy_state) are
                # not surfaced here, so this path
                # cannot create a reload loop.
                self.hass.config_entries.async_update_entry(
                    self.config_entry, options=new_data
                )
                return self.async_abort(reason="options_updated")

        current = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "hems_enabled",
                        default=current.get("hems_enabled", DEFAULT_HEMS_ENABLED),
                    ): bool,
                    vol.Optional(
                        "poll_interval",
                        default=current.get(
                            "poll_interval", DEFAULT_POLL_INTERVAL_SEC
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_POLL_INTERVAL_SEC, max=MAX_POLL_INTERVAL_SEC),
                    ),
                    vol.Optional(
                        "reserve_soc",
                        default=current.get("reserve_soc", DEFAULT_RESERVE_SOC),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=10.0, max=40.0),
                    ),
                    vol.Optional(
                        "pv_surplus_threshold_w",
                        default=current.get(
                            "pv_surplus_threshold_w", DEFAULT_PV_SURPLUS_ENTER_W
                        ),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=50.0, max=1000.0),
                    ),
                    vol.Optional(
                        "tariff_day",
                        default=current.get("tariff_day", 4.32),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=0.5, max=20.0),
                    ),
                    vol.Optional(
                        "tariff_night",
                        default=current.get("tariff_night", 2.16),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=0.5, max=20.0),
                    ),
                    vol.Optional(
                        "site_latitude",
                        default=current.get(
                            "site_latitude", DEFAULT_SITE_LATITUDE
                        ),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=-90.0, max=90.0),
                    ),
                    vol.Optional(
                        "site_longitude",
                        default=current.get(
                            "site_longitude", DEFAULT_SITE_LONGITUDE
                        ),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=-180.0, max=180.0),
                    ),
                    vol.Optional(
                        "auto_storm_by_forecast",
                        default=current.get("auto_storm_by_forecast", False),
                    ): bool,
                    vol.Optional(
                        "battery_capacity_ah",
                        default=current.get("battery_capacity_ah", 230.0),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=10.0, max=2000.0),
                    ),
                    vol.Optional(
                        "nominal_voltage_v",
                        default=current.get("nominal_voltage_v", 51.2),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=10.0, max=100.0),
                    ),
                    vol.Optional(
                        "pv_total_capacity_w",
                        default=current.get("pv_total_capacity_w", 3000.0),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=100.0, max=50000.0),
                    ),
                    vol.Optional(
                        "inverter_max_power_w",
                        default=current.get("inverter_max_power_w", 5000.0),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=500.0, max=50000.0),
                    ),
                    vol.Optional(
                        "night_start_hour",
                        default=current.get("night_start_hour", 23),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=0, max=23),
                    ),
                    vol.Optional(
                        "day_start_hour",
                        default=current.get("day_start_hour", 7),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=0, max=23),
                    ),
                    # ── Predictive ML mode (persistent across reload) ──
                    **_predictive_schema(current),
                    vol.Optional(
                        "predictive_mode",
                        default=current.get("predictive_mode", PREDICTIVE_MODE_DEFAULT),
                    ): vol.In(list(PREDICTIVE_MODES)),
                }
            ),
            errors=errors,
        )
