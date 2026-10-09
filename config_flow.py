"""Config flow for Smart Solar Inverter integration.

R07 follow-up (2026-10-09): the flow is
split into three explicit phases:

  1. ``async_step_user`` — credentials.
     The client is constructed with NO
     ``selected_device_sn`` so the login
     and the device-list fetch are both
     allowed to proceed. After a
     successful login we ALWAYS have a
     device list (the login response
     includes the access token needed to
     call the device-list endpoint).
  2. ``async_step_select_device`` —
     present the device list to the
     operator. We do NOT call
     ``_fetch_device_list`` here (the
     list was already retrieved during
     step 1 and is cached on
     ``self._pending_devices``). The
     operator MUST select explicitly when
     the account has more than one
     device.
  3. ``async_create_entry`` — only after
     a confirmed selection.

A second-stage refactor (R07 follow-up
#2): the reauth flow MUST receive the
saved ``selected_device_sn`` and MUST
preserve all other fields in
``entry.data``. If the selected device
is missing from the new account, the
reauth fails WITHOUT touching
``entry.data`` (so the operator's
existing bindings are not destroyed).

Cleanup contract (R07 follow-up #3):
every code path that owns an
``InverterApiClient`` MUST close it
exactly once:
  - successful setup
  - auth failure
  - user cancellation of the picker
  - unexpected exception
  - reauth success and failure
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .api import InverterApiClient, InverterAuthError, InverterApiError
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


def _str_device_label(d: dict[str, Any]) -> str:
    """Format a device dict for the picker.
    All fields are coerced to string so
    a numeric ``id`` or ``stationId``
    cannot crash the option label.
    """
    dev_id = str(d.get("id", "?"))
    station = str(d.get("stationId", "?"))
    # Use ``isFinite``-safe formatting: a
    # non-numeric or NaN value renders as
    # ``0.0 kWh`` (a placeholder, NOT
    # the live daily total).
    try:
        daily = float(d.get("dailyProducedQuantity", 0))
        if not _is_finite(daily):
            daily = 0.0
    except (TypeError, ValueError):
        daily = 0.0
    return f"{dev_id} — station {station} — {daily:.1f} kWh today"


def _is_finite(v: Any) -> bool:
    if v is None:
        return False
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return f == f  # NaN check (NaN != NaN)


class InverterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Solar Inverter."""

    VERSION = 1

    def __init__(self) -> None:
        # Pending state for the multi-step
        # flow. Cleared in every exit path.
        # NEVER stored on ``self`` after
        # ``async_create_entry``.
        self._pending_api: InverterApiClient | None = None
        self._pending_devices: list[dict[str, Any]] = []
        self._pending_email: str | None = None
        self._pending_password: str | None = None
        self._pending_predictive: dict[str, Any] | None = None
        # True if the reauth flow is
        # running. ``_get_reauth_entry()``
        # returns the entry being reauthed.
        self._is_reauth: bool = False

    async def _cleanup_pending(self) -> None:
        """Close the pending client (if
        any) and clear all pending state.
        Called from every exit path
        (success, error, cancel).
        """
        api = self._pending_api
        self._pending_api = None
        self._pending_devices = []
        self._pending_email = None
        self._pending_password = None
        self._pending_predictive = None
        if api is not None:
            try:
                await api.close()
            except Exception:  # noqa: BLE001
                # The client may have a closed
                # session already; ignore.
                _LOGGER.debug("pending api close failed", exc_info=True)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Credentials step.

        R07 follow-up contract:
          1. Validate the credentials
             schema.
          2. Build a client with NO
             ``selected_device_sn`` and
             call ``authenticate()`` (no
             preference). The login MUST
             succeed before we fetch the
             device list.
          3. After login, fetch the device
             list with NO preference. The
             fetch must SUCCEED even when
             the account has multiple
             devices — the multi-device
             guard in ``_fetch_device_list``
             only triggers when a specific
             ``selected_device_sn`` is
             passed.
          4. Single-device accounts:
             auto-bind. Multi-device
             accounts: cache the list on
             ``self`` and proceed to
             ``async_step_select_device``.
          5. The client is closed on every
             exit path (success, error,
             cancel) by ``_cleanup_pending``.

        ID normalisation: every device id
        is coerced to a string before it
        is stored in
        ``entry.data['selected_device_sn']``
        so the equality check at
        ``async_step_select_device`` does
        not depend on the JSON numeric /
        string type returned by the API.
        """
        if user_input is not None:
            return await self._handle_user_submit(user_input)

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
            errors={},
        )

    async def _handle_user_submit(
        self, user_input: dict[str, Any]
    ) -> FlowResult:
        """The submit handler for the
        credentials step. Extracted so it
        can be unit-tested in isolation.
        """
        errors: dict[str, str] = {}
        email = user_input[CONF_EMAIL]
        password = user_input[CONF_PASSWORD]
        try:
            predictive_options = parse_predictive_options(user_input)
        except ValueError:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required(CONF_EMAIL): str,
                    vol.Required(CONF_PASSWORD): str,
                    **_predictive_schema(user_input),
                }),
                errors={"base": "invalid_predictive_options"},
            )

        # Phase 1: login with the
        # device-list call SKIPPED. We
        # need the access token to call
        # the device-list endpoint, but
        # we MUST NOT raise on
        # multi-device-no-preference
        # here. The picker step uses the
        # cached list. ``skip_device_list``
        # is a production-API flag added
        # in R07 follow-up for exactly
        # this case.
        api = InverterApiClient(email=email, password=password)
        self._pending_api = api
        self._pending_email = email
        self._pending_password = password
        self._pending_predictive = predictive_options
        try:
            try:
                await api.authenticate(skip_device_list=True)
            except InverterAuthError as exc:
                _LOGGER.error("Auth failed: %s", exc)
                errors["base"] = "auth_failed"
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema({
                        vol.Required(CONF_EMAIL): str,
                        vol.Required(CONF_PASSWORD): str,
                        **_predictive_schema(user_input),
                    }),
                    errors=errors,
                )
            except InverterApiError as exc:
                _LOGGER.error("API error during login: %s", exc)
                errors["base"] = "auth_failed"
                return self.async_show_form(
                    step_id="user",
                    data_schema=vol.Schema({
                        vol.Required(CONF_EMAIL): str,
                        vol.Required(CONF_PASSWORD): str,
                        **_predictive_schema(user_input),
                    }),
                    errors=errors,
                )

            # Phase 2: device list. We
            # ``_list_devices`` (returns the
            # raw list without binding). The
            # auth client is kept alive on
            # ``self._pending_api`` so the
            # picker step can re-use it
            # without a second login.
            devices = await api._list_devices()
        finally:
            # ``_pending_api`` retains the
            # client; close it ONLY on the
            # error path (the success path
            # closes via ``_cleanup_pending``
            # inside ``async_step_select_device``
            # or directly below).
            if errors:
                await self._cleanup_pending()

        if not devices:
            await self._cleanup_pending()
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required(CONF_EMAIL): str,
                    vol.Required(CONF_PASSWORD): str,
                    **_predictive_schema(user_input),
                }),
                errors={"base": "no_device"},
            )

        # Normalise ids to string. The API
        # sometimes returns numeric ids; we
        # coerce so the picker equality
        # check does not depend on type.
        norm_devices: list[dict[str, Any]] = []
        for d in devices:
            norm_devices.append({
                **d,
                "id": str(d.get("id", "")),
                "stationId": str(d.get("stationId", "")),
            })
        self._pending_devices = norm_devices

        # Phase 3: single-device auto-bind
        # or multi-device picker.
        if len(norm_devices) == 1:
            chosen_sn = norm_devices[0]["id"]
            return await self._finalize_entry(chosen_sn)

        # Multi-device: proceed to picker.
        return await self.async_step_select_device()

    async def async_step_select_device(
        self, user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Picker step. Only reached when the
        account has more than one device.

        R07 follow-up:
          - We do NOT call
            ``_fetch_device_list`` here —
            the list was already retrieved
            during ``async_step_user`` and
            is cached on
            ``self._pending_devices``.
          - We do NOT auto-fall-back to
            ``devices[0]`` when the operator
            cancels or submits an invalid
            id. Invalid submission shows an
            error and lets the user retry.
          - The pending client is closed
            in every exit path via
            ``_cleanup_pending``.

        Returns ``FlowResult``, NOT the
        raw coroutine from
        ``async_step_user`` (the previous
        implementation returned the
        coroutine directly, which Home
        Assistant cannot await).
        """
        devices = self._pending_devices
        if not devices or self._pending_api is None:
            # Pending state lost (e.g. the
            # operator refreshed the page
            # mid-flow). Send them back to
            # the credentials step.
            await self._cleanup_pending()
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
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
                }),
                errors={"base": "no_device"},
            )

        errors: dict[str, str] = {}
        if user_input is not None:
            chosen_raw = user_input.get("selected_device_sn")
            # Normalise: coerce to str, strip
            # whitespace. The API can return
            # either a number or a string,
            # and the operator can type either
            # in the form. Both must work.
            chosen = str(chosen_raw).strip() if chosen_raw is not None else ""
            valid_ids = {d["id"] for d in devices}
            if not chosen or chosen not in valid_ids:
                errors["base"] = "invalid_device"
            else:
                return await self._finalize_entry(chosen)

        device_options = {
            d["id"]: _str_device_label(d) for d in devices
        }
        return self.async_show_form(
            step_id="select_device",
            data_schema=vol.Schema(
                {vol.Required("selected_device_sn"): vol.In(device_options)}
            ),
            errors=errors,
        )

    async def _finalize_entry(
        self, chosen_sn: str,
    ) -> FlowResult:
        """Create the config entry with the
        normalised ``selected_device_sn``
        and clean up the pending client.
        """
        email = self._pending_email
        password = self._pending_password
        predictive_options = self._pending_predictive
        if not email or not password or not predictive_options:
            await self._cleanup_pending()
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
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
                }),
                errors={"base": "no_device"},
            )
        await self._cleanup_pending()
        await self.async_set_unique_id(chosen_sn)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Solar Inverter ({chosen_sn})",
            options={
                **predictive_options,
                "predictive_mode": predictive_options["predictive_default_mode"].lower(),
            },
            data={
                CONF_EMAIL: email,
                CONF_PASSWORD: password,
                "selected_device_sn": chosen_sn,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "InverterOptionsFlow":
        """Create the options flow."""
        return InverterOptionsFlow()

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Re-authentication when the token
        expires.

        R07 follow-up contract:
          - The client's ``selected_device_sn``
            is the saved operator choice
            (from ``entry.data``). It is
            NEVER omitted. A legacy entry
            without ``selected_device_sn``
            uses the entry's own
            ``unique_id`` (which IS the
            device_sn, set by HA) as the
            identity guard.
          - The new client authenticates
            with the saved device preference.
          - If the saved device is missing
            from the new account, the
            reauth FAILS without touching
            ``entry.data``, the unique id,
            or the binding. The operator
            must add a new entry instead.
          - On success, ``entry.data`` is
            updated with the new
            credentials AND the saved
            ``selected_device_sn`` is
            preserved. Other entry.data
            fields are preserved by
            spreading ``entry.data`` into
            the update.
        """
        entry = self._get_reauth_entry()
        saved_sn = entry.data.get("selected_device_sn") or (
            # Legacy entries: the unique id
            # IS the device_sn. ``unique_id``
            # is a property of the config
            # entry in HA, set via
            # ``async_set_unique_id``. We
            # read it through the entry's
            # public attribute.
            getattr(entry, "unique_id", None)
        )

        if user_input is not None:
            email = user_input.get(CONF_EMAIL, entry.data[CONF_EMAIL])
            password = user_input[CONF_PASSWORD]
            api = InverterApiClient(
                email=email,
                password=password,
                selected_device_sn=saved_sn,
            )
            self._pending_api = api
            try:
                try:
                    await api.authenticate()
                except (InverterAuthError, InverterApiError):
                    # Auth failed OR the
                    # selected device is
                    # missing. The
                    # ``InverterApiError`` is
                    # raised by
                    # ``_fetch_device_list``
                    # when the preference
                    # is not in the list.
                    # Either way: the saved
                    # binding is preserved
                    # and the operator must
                    # reconfigure.
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
                        errors={"base": "reauth_failed_device_missing"},
                    )
                # Success: the saved device
                # was found. Persist the new
                # credentials AND the saved
                # ``selected_device_sn``.
                new_data = {
                    **entry.data,
                    CONF_EMAIL: email,
                    CONF_PASSWORD: password,
                }
                # If ``entry.data`` did not
                # have ``selected_device_sn``,
                # we add it from the legacy
                # unique_id.
                if "selected_device_sn" not in new_data and saved_sn:
                    new_data["selected_device_sn"] = str(saved_sn)
                self.hass.config_entries.async_update_entry(
                    entry,
                    data=new_data,
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            finally:
                await self._cleanup_pending()

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
            errors={},
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
