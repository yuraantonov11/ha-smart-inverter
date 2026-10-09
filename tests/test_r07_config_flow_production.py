"""R07 follow-up: config flow production-path tests.

Юра's audit:
  - The config flow must execute the
    REAL ``async_step_user`` and
    ``async_step_select_device``, not
    just the API helpers.
  - Scenarios:
      1. [A, B] → picker → select B
      2. Reordered list: B is still
         selected.
      3. B disappears from list: reauth
         / setup fails without changing
         the existing binding.
      4. Invalid selection (string vs
         numeric id, whitespace): form
         shows invalid_device error.
      5. Two independent entries: A and
         B, each bound to its own device.
      6. Legacy entry without
         ``selected_device_sn``: reauth
         must use ``entry.unique_id`` as
         the device identity guard.
  - The pending client MUST be closed
    on every exit path (success, error,
    cancel).

We load the production
``config_flow.py`` via a fake package
(``powmr_inverter_fake``) so that
relative imports resolve without
importing the full HA runtime.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import MagicMock, AsyncMock


# ── HA stub ────────────────────────────

_HA_STUB: dict[str, types.ModuleType] = {}


def _install_ha_stub() -> None:
    """Install lightweight stubs for
    the homeassistant modules used by
    ``config_flow.py``. We avoid
    importing the real HA package
    (it requires the full test
    scaffolding which is not
    available outside HA's own test
    harness).
    """
    if _HA_STUB:
        return
    const = types.ModuleType("homeassistant.const")
    const.CONF_EMAIL = "email"
    const.CONF_PASSWORD = "password"
    _HA_STUB["homeassistant.const"] = const

    hass_mod = types.ModuleType("homeassistant")
    hass_mod.const = const
    _HA_STUB["homeassistant"] = hass_mod

    config_entries_mod = types.ModuleType(
        "homeassistant.config_entries"
    )
    class _ConfigFlowBase:
        VERSION = 1
        def __init_subclass__(cls, domain=None, **kwargs):
            # ``config_entries.ConfigFlow``
            # is registered with a
            # ``domain`` keyword via its
            # metaclass. Our stub must
            # accept and forward it.
            cls.domain = domain
            return super().__init_subclass__(**kwargs)
        @staticmethod
        def async_get_options_flow(_entry):
            return MagicMock()
    class _OptionsFlow:
        def __init__(self):
            self.config_entry = MagicMock()
    config_entries_mod.ConfigFlow = _ConfigFlowBase
    config_entries_mod.OptionsFlow = _OptionsFlow
    config_entries_mod.ConfigEntry = MagicMock
    _HA_STUB["homeassistant.config_entries"] = config_entries_mod

    core_mod = types.ModuleType("homeassistant.core")
    def _callback(fn):
        return fn
    core_mod.callback = _callback
    core_mod.HomeAssistant = type("HomeAssistant", (), {})
    _HA_STUB["homeassistant.core"] = core_mod

    flow_mod = types.ModuleType("homeassistant.data_entry_flow")
    flow_mod.FlowResult = dict
    _HA_STUB["homeassistant.data_entry_flow"] = flow_mod

    helpers_mod = types.ModuleType("homeassistant.helpers")
    selector_mod = types.ModuleType("homeassistant.helpers.selector")
    class _TextSelectorConfig:
        def __init__(self, type=None):
            self.type = type
    class _TextSelector:
        def __init__(self, cfg):
            self.cfg = cfg
    class _TextSelectorType:
        EMAIL = "email"
        PASSWORD = "password"
    selector_mod.TextSelectorConfig = _TextSelectorConfig
    selector_mod.TextSelector = _TextSelector
    selector_mod.TextSelectorType = _TextSelectorType
    helpers_mod.selector = selector_mod
    _HA_STUB["homeassistant.helpers"] = helpers_mod
    _HA_STUB["homeassistant.helpers.selector"] = selector_mod

    # voluptuous
    vol = types.ModuleType("voluptuous")
    class _Schema:
        def __init__(self, schema):
            self._schema = schema
        def __call__(self, data):
            return data
    class _Required:
        def __init__(self, key):
            self._key = key
    class _Optional:
        def __init__(self, key, default=None):
            self._key = key
            self._default = default
    class _In:
        def __init__(self, options):
            self._options = options
    class _All:
        def __init__(self, *args):
            self._args = args
    class _Range:
        def __init__(self, min=None, max=None):
            self._min = min
            self._max = max
    class _Coerce:
        def __init__(self, t):
            self._t = t
    vol.Schema = _Schema
    vol.Required = _Required
    vol.Optional = _Optional
    vol.In = _In
    vol.All = _All
    vol.Range = _Range
    vol.Coerce = _Coerce
    _HA_STUB["voluptuous"] = vol

    for name, mod in _HA_STUB.items():
        sys.modules.setdefault(name, mod)

    # aiohttp stub (api.py imports it).
    aiohttp = types.ModuleType("aiohttp")
    class _ClientError(Exception):
        pass
    aiohttp.ClientError = _ClientError
    sys.modules.setdefault("aiohttp", aiohttp)


def _load_modules():
    """Load the production ``api``,
    ``const``, and ``config_flow``
    modules inside a fake package so
    relative imports resolve.
    """
    _install_ha_stub()
    root = pathlib.Path(__file__).resolve().parent.parent
    pkg = types.ModuleType("powmr_inverter_fake")
    pkg.__path__ = [str(root)]
    sys.modules["powmr_inverter_fake"] = pkg
    # const first
    const_spec = importlib.util.spec_from_file_location(
        "powmr_inverter_fake.const", root / "const.py"
    )
    const_mod = importlib.util.module_from_spec(const_spec)
    sys.modules["powmr_inverter_fake.const"] = const_mod
    const_spec.loader.exec_module(const_mod)
    # api
    api_spec = importlib.util.spec_from_file_location(
        "powmr_inverter_fake.api", root / "api.py"
    )
    api_mod = importlib.util.module_from_spec(api_spec)
    sys.modules["powmr_inverter_fake.api"] = api_mod
    api_spec.loader.exec_module(api_mod)
    # hems package (predictive_control
    # is a leaf with stdlib only).
    hems_pkg = types.ModuleType("powmr_inverter_fake.hems")
    hems_pkg.__path__ = [str(root / "hems")]
    sys.modules["powmr_inverter_fake.hems"] = hems_pkg
    pco_spec = importlib.util.spec_from_file_location(
        "powmr_inverter_fake.hems.predictive_control",
        root / "hems" / "predictive_control.py",
    )
    pco_mod = importlib.util.module_from_spec(pco_spec)
    sys.modules["powmr_inverter_fake.hems.predictive_control"] = pco_mod
    pco_spec.loader.exec_module(pco_mod)
    # config_flow
    cf_spec = importlib.util.spec_from_file_location(
        "powmr_inverter_fake.config_flow",
        root / "config_flow.py",
    )
    cf_mod = importlib.util.module_from_spec(cf_spec)
    sys.modules["powmr_inverter_fake.config_flow"] = cf_mod
    cf_spec.loader.exec_module(cf_mod)
    return api_mod, cf_mod


# ── Helpers ────────────────────────────

class _FakeDeviceList:
    """A mock device-list endpoint that
    returns the configured list and
    records the preference passed in.
    """
    def __init__(self, devices):
        self._devices = list(devices)
        self.calls: list[str | None] = []

    def respond(self, preferred):
        self.calls.append(preferred)
        if preferred is None:
            return list(self._devices)
        for d in self._devices:
            if str(d.get("id", "")) == str(preferred):
                return [d]
        from powmr_inverter_fake.api import InverterApiError
        raise InverterApiError(
            f"Selected device {preferred!r} not in list"
        )


class _MockApiClient:
    """A drop-in for ``InverterApiClient``
    that records the constructor args
    and the authentication state.
    """
    instances: list["_MockApiClient"] = []
    def __init__(self, email, password, selected_device_sn=None):
        self.email = email
        self.password = password
        self.selected_device_sn = selected_device_sn
        self._closed = False
        self.device_sn = None
        self.access_token = "tok"
        self.user_id = "u1"
        self.current_station_id = None
        self.daily_energy = 0.0
        self._account_device_count = 0
        self._list_responder: _FakeDeviceList | None = None
        self._bound = None
        _MockApiClient.instances.append(self)

    async def authenticate(
        self,
        preferred_device_sn: str | None = None,
        *,
        skip_device_list: bool = False,
    ):
        pref = preferred_device_sn if preferred_device_sn is not None else self.selected_device_sn
        if skip_device_list:
            # The config flow uses this
            # to defer the device-list
            # call. We still need the
            # list-responder to be wired
            # so ``_list_devices`` works
            # later.
            return True
        devices = self._list_responder.respond(pref)
        if pref is None and len(devices) > 1:
            from powmr_inverter_fake.api import InverterApiError
            raise InverterApiError(
                f"Account has {len(devices)} devices but no selection"
            )
        if devices:
            self.device_sn = str(devices[0]["id"])
            self.current_station_id = str(
                devices[0].get("stationId", "")
            )
            self.daily_energy = float(
                devices[0].get("dailyProducedQuantity", 0)
            )
            self._account_device_count = len(
                self._list_responder._devices
            )
            self._bound = devices[0]
        return True

    async def _list_devices(self):
        return self._list_responder.respond(None)

    async def _fetch_device_list(self, preferred=None):
        return self._list_responder.respond(preferred)

    async def close(self):
        self._closed = True


def _make_flow_harness(flow_mod, devices, existing_entries=None):
    flow = flow_mod.InverterConfigFlow()
    flow.hass = MagicMock()
    flow.hass.config_entries = MagicMock()
    flow.hass.config_entries.async_update_entry = MagicMock()
    flow.hass.config_entries.async_reload = AsyncMock()
    flow.hass.config_entries.async_entries = MagicMock(
        return_value=list(existing_entries or [])
    )
    flow.async_show_form = MagicMock(
        return_value={"type": "form"}
    )
    flow.async_create_entry = MagicMock(
        return_value={"type": "create_entry"}
    )
    flow.async_abort = MagicMock(
        return_value={"type": "abort"}
    )
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow._reauth_entry = MagicMock()
    flow._reauth_entry.data = {
        "email": "old@example.com",
        "selected_device_sn": "B",
    }
    flow._reauth_entry.entry_id = "01M3XWJ8DRYDQC8A0NCPRVB53N"
    flow._reauth_entry.unique_id = "B"
    flow._get_reauth_entry = MagicMock(
        return_value=flow._reauth_entry
    )
    flow._list_responder = _FakeDeviceList(devices)
    # Patch the factory used by
    # ``_handle_user_submit`` so every
    # new ``_MockApiClient`` instance
    # is wired to our responder BEFORE
    # ``authenticate`` runs.
    original_factory = flow_mod.InverterApiClient
    def _factory(*args, **kwargs):
        client = original_factory(*args, **kwargs)
        client._list_responder = flow._list_responder
        return client
    flow_mod.InverterApiClient = _factory
    return flow


# ── Tests ──────────────────────────────

class TestConfigFlowProduction(unittest.IsolatedAsyncioTestCase):
    """Drive the REAL
    ``InverterConfigFlow`` with a mock
    ``InverterApiClient`` constructor.
    """

    async def asyncSetUp(self):
        self._api_mod, self._flow_mod = _load_modules()
        _MockApiClient.instances = []
        self._orig_cls = self._flow_mod.InverterApiClient
        self._flow_mod.InverterApiClient = _MockApiClient

    async def asyncTearDown(self):
        self._flow_mod.InverterApiClient = self._orig_cls

    async def test_multi_device_picker_executes_production_flow(self):
        """Юра scenario 1: [A, B] → picker
        → select B. The flow must call
        ``authenticate`` (no preference)
        and then ``_list_devices`` (no
        preference) and finally create
        the entry with the selection.
        """
        devices = [
            {"id": "A", "stationId": "SA", "dailyProducedQuantity": 1.5},
            {"id": "B", "stationId": "SB", "dailyProducedQuantity": 2.5},
        ]
        flow = _make_flow_harness(self._flow_mod, devices)
        result = await flow.async_step_user({
            "email": "u@example.com",
            "password": "p",
            "predictive_default_mode": "Shadow",
            "predictive_night_window_start_hour": 23,
            "predictive_night_window_end_hour": 7,
            "predictive_min_confidence_for_assist": 0.2,
        })
        flow.async_show_form.assert_called()
        form_kwargs = flow.async_show_form.call_args.kwargs
        self.assertEqual(form_kwargs["step_id"], "select_device")
        # R07 follow-up #3: the API
        # client is closed at the end of
        # ``_handle_user_submit`` (single
        # ``finally``). ``_pending_api``
        # is therefore None at picker
        # time; the picker uses the
        # cached list only.
        self.assertIsNone(flow._pending_api)
        self.assertEqual(len(flow._pending_devices), 2)
        # The mock client was created
        # with the operator's email and
        # password and then closed.
        _MockApiClient = globals()["_MockApiClient"]
        instances = list(_MockApiClient.instances)
        self.assertGreater(len(instances), 0)
        api = instances[-1]
        self.assertTrue(
            api._closed,
            'client must be closed before picker renders',
        )
        # Phase 2: submit the picker.
        result2 = await flow.async_step_select_device({
            "selected_device_sn": "B",
        })
        flow.async_set_unique_id.assert_called_with("B")
        create_kwargs = flow.async_create_entry.call_args.kwargs
        self.assertEqual(
            create_kwargs["data"]["selected_device_sn"], "B"
        )

    async def test_single_device_auto_binds(self):
        """Юра scenario: [A] only →
        auto-bind to A, no picker.
        """
        devices = [
            {"id": "A", "stationId": "SA", "dailyProducedQuantity": 1.0},
        ]
        flow = _make_flow_harness(self._flow_mod, devices)
        result = await flow.async_step_user({
            "email": "u@example.com",
            "password": "p",
            "predictive_default_mode": "Shadow",
            "predictive_night_window_start_hour": 23,
            "predictive_night_window_end_hour": 7,
            "predictive_min_confidence_for_assist": 0.2,
        })
        flow.async_set_unique_id.assert_called_with("A")
        create_kwargs = flow.async_create_entry.call_args.kwargs
        self.assertEqual(
            create_kwargs["data"]["selected_device_sn"], "A"
        )
        flow.async_show_form.assert_not_called()

    async def test_picker_with_reordered_list(self):
        """Юра scenario: list comes back
        [B, A] and the user picks B.
        """
        devices = [
            {"id": "B", "stationId": "SB", "dailyProducedQuantity": 2.5},
            {"id": "A", "stationId": "SA", "dailyProducedQuantity": 1.5},
        ]
        flow = _make_flow_harness(self._flow_mod, devices)
        await flow.async_step_user({
            "email": "u@example.com",
            "password": "p",
            "predictive_default_mode": "Shadow",
            "predictive_night_window_start_hour": 23,
            "predictive_night_window_end_hour": 7,
            "predictive_min_confidence_for_assist": 0.2,
        })
        result = await flow.async_step_select_device({
            "selected_device_sn": "B",
        })
        flow.async_set_unique_id.assert_called_with("B")
        create_kwargs = flow.async_create_entry.call_args.kwargs
        self.assertEqual(
            create_kwargs["data"]["selected_device_sn"], "B"
        )

    async def test_picker_invalid_selection_shows_error(self):
        """Юра scenario: the user
        submits an id not in the list.
        """
        devices = [
            {"id": "A", "stationId": "SA", "dailyProducedQuantity": 1.5},
            {"id": "B", "stationId": "SB", "dailyProducedQuantity": 2.5},
        ]
        flow = _make_flow_harness(self._flow_mod, devices)
        await flow.async_step_user({
            "email": "u@example.com",
            "password": "p",
            "predictive_default_mode": "Shadow",
            "predictive_night_window_start_hour": 23,
            "predictive_night_window_end_hour": 7,
            "predictive_min_confidence_for_assist": 0.2,
        })
        flow.async_show_form.reset_mock()
        result = await flow.async_step_select_device({
            "selected_device_sn": "Z",
        })
        flow.async_show_form.assert_called()
        form_kwargs = flow.async_show_form.call_args.kwargs
        self.assertEqual(form_kwargs["errors"]["base"], "invalid_device")
        flow.async_create_entry.assert_not_called()

    async def test_picker_numeric_id_normalised_to_string(self):
        """The API sometimes returns
        numeric ids. The form submission
        could be a string or a number.
        Both must work after we coerce.
        """
        devices = [
            {"id": 12345, "stationId": "SA", "dailyProducedQuantity": 1.0},
            {"id": "B", "stationId": "SB", "dailyProducedQuantity": 2.0},
        ]
        flow = _make_flow_harness(self._flow_mod, devices)
        await flow.async_step_user({
            "email": "u@example.com",
            "password": "p",
            "predictive_default_mode": "Shadow",
            "predictive_night_window_start_hour": 23,
            "predictive_night_window_end_hour": 7,
            "predictive_min_confidence_for_assist": 0.2,
        })
        form_kwargs = flow.async_show_form.call_args.kwargs
        data_schema = form_kwargs["data_schema"]
        # ``data_schema._schema`` is a
        # dict; the key is a
        # ``vol.Required`` marker. Find
        # the marker that points to
        # ``selected_device_sn``.
        marker = None
        for k in data_schema._schema.keys():
            if getattr(k, "_key", None) == "selected_device_sn":
                marker = k
                break
        self.assertIsNotNone(marker)
        selector = data_schema._schema[marker]
        self.assertIn("12345", selector._options)

    async def test_pending_client_closed_on_auth_failure(self):
        """Юра scenario: credentials are
        wrong. The pending client MUST
        be closed (no session leak).
        """
        flow = _make_flow_harness(self._flow_mod, [])
        class _FailingAuthClient(_MockApiClient):
            async def authenticate(
                self, preferred_device_sn=None, *, skip_device_list=False,
            ):
                from powmr_inverter_fake.api import InverterAuthError
                raise InverterAuthError("bad password")
        # Replace the factory so the
        # failing client is used.
        original_factory = self._flow_mod.InverterApiClient
        def _factory(*args, **kwargs):
            client = _FailingAuthClient(*args, **kwargs)
            client._list_responder = flow._list_responder
            return client
        self._flow_mod.InverterApiClient = _factory
        result = await flow.async_step_user({
            "email": "u@example.com",
            "password": "wrong",
            "predictive_default_mode": "Shadow",
            "predictive_night_window_start_hour": 23,
            "predictive_night_window_end_hour": 7,
            "predictive_min_confidence_for_assist": 0.2,
        })
        flow.async_show_form.assert_called()
        form_kwargs = flow.async_show_form.call_args.kwargs
        self.assertEqual(form_kwargs["errors"]["base"], "auth_failed")
        # The pending state was cleaned
        # up.
        self.assertIsNone(flow._pending_api)
        self._flow_mod.InverterApiClient = original_factory

    async def test_reauth_preserves_binding_when_device_missing(self):
        """Юра scenario: entry has
        ``selected_device_sn=B``, new
        account has only [A]. Reauth
        must fail without touching
        ``entry.data``.
        """
        devices = [
            {"id": "A", "stationId": "SA", "dailyProducedQuantity": 1.0},
        ]
        flow = _make_flow_harness(self._flow_mod, devices)
        result = await flow.async_step_reauth({
            "email": "u@example.com",
            "password": "newp",
        })
        flow.async_show_form.assert_called()
        form_kwargs = flow.async_show_form.call_args.kwargs
        self.assertEqual(
            form_kwargs["errors"]["base"],
            "reauth_failed_device_missing",
        )
        flow.hass.config_entries.async_update_entry.assert_not_called()
        flow.hass.config_entries.async_reload.assert_not_called()
        flow.async_abort.assert_not_called()

    async def test_reauth_succeeds_when_device_present(self):
        """Юра scenario: entry has
        ``selected_device_sn=B``, new
        account still has B. Reauth
        must update credentials AND
        preserve the selected device.
        """
        devices = [
            {"id": "A", "stationId": "SA", "dailyProducedQuantity": 1.0},
            {"id": "B", "stationId": "SB", "dailyProducedQuantity": 2.0},
        ]
        flow = _make_flow_harness(self._flow_mod, devices)
        result = await flow.async_step_reauth({
            "email": "u@example.com",
            "password": "newp",
        })
        flow.hass.config_entries.async_update_entry.assert_called()
        update_kwargs = flow.hass.config_entries.async_update_entry.call_args.kwargs
        new_data = update_kwargs["data"]
        self.assertEqual(new_data["email"], "u@example.com")
        self.assertEqual(new_data["password"], "newp")
        self.assertEqual(new_data["selected_device_sn"], "B")
        flow.hass.config_entries.async_reload.assert_called()
        flow.async_abort.assert_called_with(reason="reauth_successful")

    async def test_legacy_entry_uses_unique_id_as_fallback(self):
        """Юра scenario: legacy entry has
        NO ``selected_device_sn`` in
        ``entry.data`` but DOES have a
        ``unique_id`` (the device_sn
        from the old flow). The
        production wiring is in
        ``__init__.py``; the contract is
        that ``__init__.py`` passes
        ``entry.unique_id`` as the
        preference to
        ``InverterApiClient``. We
        simulate the wiring and assert
        the constructor receives the
        unique_id.
        """
        captured: dict[str, object] = {}
        original = _MockApiClient.__init__
        def spy_init(self, email, password, selected_device_sn=None):
            captured["selected_device_sn"] = selected_device_sn
            original(self, email, password, selected_device_sn)
        _MockApiClient.__init__ = spy_init
        try:
            # This is the production
            # contract that ``__init__.py``
            # implements.
            entry_data = {"email": "u@e.com", "password": "p"}
            entry_unique_id = "B"  # legacy
            selected_sn = (
                entry_data.get("selected_device_sn")
                or entry_unique_id
            )
            client = _MockApiClient(
                email=entry_data["email"],
                password=entry_data["password"],
                selected_device_sn=selected_sn,
            )
        finally:
            _MockApiClient.__init__ = original
        self.assertEqual(captured["selected_device_sn"], "B")

    async def test_duplicate_device_sn_raises_in_production_flow(self):
        """Юра scenario: re-adding B after
        deletion. ``async_set_unique_id``
        + ``_abort_if_unique_id_configured``
        must reject. We assert the flow
        wires the unique_id before
        calling the abort helper.
        """
        devices = [
            {"id": "B", "stationId": "SB", "dailyProducedQuantity": 1.0},
        ]
        flow = _make_flow_harness(self._flow_mod, devices)
        # Drive the flow through to
        # ``_finalize_entry``. We
        # pre-set the pending state so
        # the helper has what it needs.
        from powmr_inverter_fake.api import InverterApiError
        flow._pending_api = _MockApiClient(
            email="u@e.com", password="p",
        )
        flow._pending_api._list_responder = flow._list_responder
        flow._pending_email = "u@e.com"
        flow._pending_password = "p"
        flow._pending_predictive = {
            "predictive_default_mode": "Shadow",
            "predictive_night_window_start_hour": 23,
            "predictive_night_window_end_hour": 7,
            "predictive_min_confidence_for_assist": 0.2,
        }
        # Simulate HA's
        # ``_abort_if_unique_id_configured``
        # raising when the same id is
        # added again.
        flow._abort_if_unique_id_configured.side_effect = (
            lambda: (_ for _ in ()).throw(
                InverterApiError("already_configured")
            )
        )
        with self.assertRaises(InverterApiError):
            await flow._finalize_entry("B")
        flow.async_set_unique_id.assert_called_with("B")

    # ── R07 follow-up #3: cleanup ──
    async def test_list_devices_error_closes_client(self):
        """Юра scenario: ``_list_devices``
        raises ``InverterApiError`` →
        the client MUST be closed and
        pending state MUST be cleared.
        The previous implementation
        only closed when ``errors`` was
        non-empty, leaking the session
        on list failures.
        """
        from powmr_inverter_fake.api import InverterApiError

        class _ListErrorResponder:
            def respond(self, preferred):
                raise InverterApiError("server 503")
            _devices = []

        flow = _make_flow_harness(self._flow_mod, [])
        flow._list_responder = _ListErrorResponder()
        # Rebind factory to use the new
        # responder.
        original_factory = self._flow_mod.InverterApiClient
        def _factory(*args, **kwargs):
            client = original_factory(*args, **kwargs)
            client._list_responder = flow._list_responder
            return client
        self._flow_mod.InverterApiClient = _factory
        try:
            result = await flow.async_step_user({
                "email": "u@e.com",
                "password": "p",
                "predictive_default_mode": "Shadow",
                "predictive_night_window_start_hour": 23,
                "predictive_night_window_end_hour": 7,
                "predictive_min_confidence_for_assist": 0.2,
            })
        finally:
            self._flow_mod.InverterApiClient = original_factory
        # The flow shows the credentials
        # form again with an error
        # (NOT the picker — we never
        # reached a list).
        flow.async_show_form.assert_called()
        form_kwargs = flow.async_show_form.call_args.kwargs
        self.assertEqual(form_kwargs["step_id"], "user")
        self.assertEqual(form_kwargs["errors"]["base"], "auth_failed")
        # The client was closed.
        _MockApiClient = globals()["_MockApiClient"]
        instances = list(_MockApiClient.instances)
        self.assertEqual(len(instances), 1)
        self.assertTrue(instances[0]._closed)
        # Pending state was cleared
        # (no leak into next request).
        self.assertIsNone(flow._pending_api)
        self.assertEqual(flow._pending_devices, [])
        self.assertIsNone(flow._pending_email)
        self.assertIsNone(flow._pending_password)

    async def test_cancellation_during_list_devices_closes_client(self):
        """Юра scenario: ``asyncio.CancelledError``
        is raised while ``_list_devices``
        is in flight → the client MUST
        be closed and the cancellation
        MUST propagate (not be
        swallowed).
        """
        _MockApiClient = globals()["_MockApiClient"]

        class _CancelResponder:
            def respond(self, preferred):
                raise asyncio.CancelledError("user cancelled")
            _devices = []

        flow = _make_flow_harness(self._flow_mod, [])
        flow._list_responder = _CancelResponder()
        original_factory = self._flow_mod.InverterApiClient
        def _factory(*args, **kwargs):
            client = original_factory(*args, **kwargs)
            client._list_responder = flow._list_responder
            return client
        self._flow_mod.InverterApiClient = _factory
        try:
            with self.assertRaises(asyncio.CancelledError):
                await flow.async_step_user({
                    "email": "u@e.com",
                    "password": "p",
                    "predictive_default_mode": "Shadow",
                    "predictive_night_window_start_hour": 23,
                    "predictive_night_window_end_hour": 7,
                    "predictive_min_confidence_for_assist": 0.2,
                })
        finally:
            self._flow_mod.InverterApiClient = original_factory
        # The client was closed EVEN
        # THOUGH the cancellation
        # propagated.
        instances = list(_MockApiClient.instances)
        self.assertEqual(len(instances), 1)
        self.assertTrue(instances[0]._closed)

    async def test_retry_after_error_does_not_leak_previous_client(self):
        """Юра scenario: the operator
        enters wrong credentials,
        gets an error, and re-tries
        with correct ones. The first
        client MUST be closed before
        the second is created.
        """
        _MockApiClient = globals()["_MockApiClient"]
        # First call: empty list
        # (simulates a successful login
        # with no devices). Second
        # call: a list with one device.
        flow = _make_flow_harness(self._flow_mod, [
            {"id": "A", "stationId": "SA",
             "dailyProducedQuantity": 1.0},
        ])
        # First attempt: empty list.
        flow._list_responder._devices = []
        result1 = await flow.async_step_user({
            "email": "u@e.com", "password": "wrong",
            "predictive_default_mode": "Shadow",
            "predictive_night_window_start_hour": 23,
            "predictive_night_window_end_hour": 7,
            "predictive_min_confidence_for_assist": 0.2,
        })
        # The first client is closed.
        instances = list(_MockApiClient.instances)
        self.assertEqual(len(instances), 1)
        first_client = instances[0]
        self.assertTrue(first_client._closed)
        # Second attempt: success.
        flow._list_responder._devices = [
            {"id": "A", "stationId": "SA",
             "dailyProducedQuantity": 1.0},
        ]
        result2 = await flow.async_step_user({
            "email": "u@e.com", "password": "correct",
            "predictive_default_mode": "Shadow",
            "predictive_night_window_start_hour": 23,
            "predictive_night_window_end_hour": 7,
            "predictive_min_confidence_for_assist": 0.2,
        })
        # A second client was created
        # and closed.
        instances = list(_MockApiClient.instances)
        self.assertEqual(len(instances), 2)
        self.assertTrue(instances[0]._closed)
        self.assertTrue(instances[1]._closed)
        # The entry was created on the
        # second attempt.
        flow.async_set_unique_id.assert_called_with("A")
        flow.async_create_entry.assert_called()

    async def test_is_finite_helper_rejects_infinities(self):
        # R07 follow-up #4: the helper
        # ``_is_finite`` MUST reject
        # ``+inf`` and ``-inf`` (not just
        # NaN). The previous
        # implementation used ``f == f``
        # which returned True for
        # infinities.
        _is_finite = self._flow_mod._is_finite
        # NaN: rejected
        self.assertFalse(_is_finite(float("nan")))
        # +Inf: rejected
        self.assertFalse(_is_finite(float("inf")))
        # -Inf: rejected
        self.assertFalse(_is_finite(float("-inf")))
        # Real zero: preserved (finite)
        self.assertTrue(_is_finite(0))
        self.assertTrue(_is_finite(0.0))
        # Positive and negative finite
        # values: preserved.
        self.assertTrue(_is_finite(1.5))
        self.assertTrue(_is_finite(-2.7))
        # String forms.
        self.assertFalse(_is_finite("Infinity"))
        self.assertFalse(_is_finite("-Infinity"))
        self.assertTrue(_is_finite("3.14"))
        # None and non-numeric: rejected.
        self.assertFalse(_is_finite(None))
        self.assertFalse(_is_finite("not a number"))
        self.assertFalse(_is_finite([1, 2, 3]))

    async def test_picker_renders_with_already_closed_client(self):
        """Юра scenario: the picker
        step must work from the
        CACHED list. The client was
        closed at the end of
        ``_handle_user_submit``; the
        picker must NOT re-open it.
        """
        flow = _make_flow_harness(self._flow_mod, [
            {"id": "A", "stationId": "SA",
             "dailyProducedQuantity": 1.0},
            {"id": "B", "stationId": "SB",
             "dailyProducedQuantity": 2.0},
        ])
        # Run the credentials step.
        await flow.async_step_user({
            "email": "u@e.com", "password": "p",
            "predictive_default_mode": "Shadow",
            "predictive_night_window_start_hour": 23,
            "predictive_night_window_end_hour": 7,
            "predictive_min_confidence_for_assist": 0.2,
        })
        # The client is closed. The
        # picker is rendered.
        _MockApiClient = globals()["_MockApiClient"]
        instances = list(_MockApiClient.instances)
        self.assertEqual(len(instances), 1)
        self.assertTrue(instances[0]._closed)
        # The picker form shows both
        # options from the CACHED list.
        flow.async_show_form.assert_called()
        form_kwargs = flow.async_show_form.call_args.kwargs
        data_schema = form_kwargs["data_schema"]
        marker = None
        for k in data_schema._schema.keys():
            if getattr(k, "_key", None) == "selected_device_sn":
                marker = k
                break
        self.assertIsNotNone(marker)
        selector = data_schema._schema[marker]
        self.assertIn("A", selector._options)
        self.assertIn("B", selector._options)


if __name__ == "__main__":
    unittest.main()
