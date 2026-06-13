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
from .const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    DEFAULT_POLL_INTERVAL_SEC,
    DEFAULT_PV_SURPLUS_ENTER_W,
    DEFAULT_RESERVE_SOC,
    DOMAIN,
    MAX_POLL_INTERVAL_SEC,
    MIN_POLL_INTERVAL_SEC,
)

_LOGGER = logging.getLogger(__name__)


class InverterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Inverter Inverter."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

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
                if not ok or not api.device_sn:
                    errors["base"] = "no_device"
                else:
                    await api.close()
                    await self.async_set_unique_id(api.device_sn)
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=f"Solar Inverter ({api.device_sn})",
                        data={
                            CONF_EMAIL: email,
                            CONF_PASSWORD: password,
                        },
                    )
            finally:
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
                }
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
            poll = user_input.get("poll_interval", DEFAULT_POLL_INTERVAL_SEC)
            if poll < MIN_POLL_INTERVAL_SEC or poll > MAX_POLL_INTERVAL_SEC:
                errors["poll_interval"] = "invalid_poll_interval"
            else:
                return self.async_create_entry(data=user_input)

        current = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
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
                        default=current.get("site_latitude", 49.0),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=-90.0, max=90.0),
                    ),
                    vol.Optional(
                        "site_longitude",
                        default=current.get("site_longitude", 31.0),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=-180.0, max=180.0),
                    ),
                }
            ),
            errors=errors,
        )
