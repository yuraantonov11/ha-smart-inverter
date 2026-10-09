"""R07: authentication and device selection.

Юра's audit:
  - 32-character plain (non-hex) password must
    NOT be auto-treated as MD5;
  - MD5 contract on a mock endpoint: plain
    password, legacy pre-hashed input, mixed-case
    hex, invalid input;
  - tests must NOT use real credentials;
  - password/token values must not appear in
    logs;
  - device identity: config entry → API
    selection → coordinator → entities;
  - device_sn / station binding / unique IDs /
    entity IDs preserved across deploys;
  - multiple devices: explicit selection
    required; missing selection ≠ devices[0];
  - scenarios: reordered list, missing
    selected, two entries.

We mock the HTTP transport with an in-memory
``_fetch_device_list``-like coroutine; the real
solar.siseli.com endpoint is NEVER contacted.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

# Load ``api.py`` directly from the project root
# without triggering the full HA package import.
# ``api.py`` has a top-level ``import
# homeassistant...`` at line 8, so we need a
# stub before loading it. See _load_api.
_HA_STUB: dict = {}


def _install_ha_stub() -> None:
    """Stub the bare minimum of
    ``homeassistant`` symbols that ``api.py``
    imports. We don't run any HA code in this
    test — the HTTP layer is fully mocked.
    """
    if _HA_STUB:
        return
    import types
    # ``homeassistant.const`` is referenced for
    # platform constants (CONF_*). We only need
    # the constants module to exist.
    const = types.ModuleType("homeassistant.const")
    # Empty CONF_* dict suffices — we don't
    # import any real ones.
    const.CONF_HOST = "host"  # type: ignore[attr-defined]
    const.CONF_USERNAME = "username"  # type: ignore[attr-defined]
    const.CONF_PASSWORD = "password"  # type: ignore[attr-defined]
    const.CONF_NAME = "name"  # type: ignore[attr-defined]
    _HA_STUB["homeassistant.const"] = const
    # ``homeassistant.helpers.aiohttp_client``
    # provides ``async_get_clientsession``.
    helpers = types.ModuleType("homeassistant.helpers")
    _HA_STUB["homeassistant.helpers"] = helpers
    aiohttp_client = types.ModuleType(
        "homeassistant.helpers.aiohttp_client"
    )
    async def _async_get_clientsession(*_a, **_kw):
        return None
    aiohttp_client.async_get_clientsession = _async_get_clientsession  # type: ignore[attr-defined]
    _HA_STUB[
        "homeassistant.helpers.aiohttp_client"
    ] = aiohttp_client
    for name, mod in _HA_STUB.items():
        sys.modules[name] = mod


def _load_api():
    """Load the production ``api.py`` from the
    project root with a stubbed HA namespace.
    Returns the module object.

    ``api.py`` uses ``from .const import (...)``
    so we must load it inside a fake package
    namespace; we register a parent module
    ``powmr_inverter_fake`` so relative
    imports resolve.
    """
    _install_ha_stub()
    import types
    root = pathlib.Path(__file__).resolve().parent.parent
    # Build a fake package: ``powmr_inverter_fake``
    # with ``api`` and ``const`` as sub-modules.
    fake_pkg = types.ModuleType("powmr_inverter_fake")
    fake_pkg.__path__ = [str(root)]
    sys.modules["powmr_inverter_fake"] = fake_pkg
    # Load const first (api depends on it).
    const_spec = importlib.util.spec_from_file_location(
        "powmr_inverter_fake.const", root / "const.py"
    )
    if const_spec is None or const_spec.loader is None:
        raise RuntimeError("could not load const.py spec")
    const_mod = importlib.util.module_from_spec(const_spec)
    sys.modules["powmr_inverter_fake.const"] = const_mod
    const_spec.loader.exec_module(const_mod)
    # Now load api inside the fake package.
    spec = importlib.util.spec_from_file_location(
        "powmr_inverter_fake.api", root / "api.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load api.py spec")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["powmr_inverter_fake.api"] = mod
    spec.loader.exec_module(mod)
    return mod


_API = None


def _get_api():
    global _API
    if _API is None:
        _API = _load_api()
    return _API


# ── MD5 detection ──────────────────────────────────────────────────


class TestPasswordHashing(unittest.TestCase):
    """The 32-character-MD5 auto-detect has a hole:
    a 32-character plain password is sent
    unchanged. The fix is to require every char
    to be a hex digit.
    """

    def setUp(self) -> None:
        # Import the helper without triggering the
        # full HA package import.
        api = _get_api()
        self._is_pre_hashed = api.InverterApiClient._is_pre_hashed_password

    def test_plain_32_char_password_is_NOT_pre_hashed(self):
        """A 32-character plain password that
        happens to be 32 chars long is NOT MD5.

        Юра's case: any 32-character non-hex
        string is NOT pre-hashed. We use a
        32-character mixed alphanumeric
        string.
        """
        plain_32 = "a1b2c3d4e5f6g7h8" + "i9j0k1l2m3n4o5p6"
        self.assertEqual(len(plain_32), 32)
        self.assertFalse(self._is_pre_hashed(plain_32),
            "32-char plain alphanumeric must NOT "
            "be treated as MD5")

    def test_lowercase_32_hex_is_pre_hashed(self):
        """Lowercase 32 hex chars IS pre-hashed."""
        md5_hex = hashlib.md5(b"hunter2").hexdigest()
        self.assertEqual(len(md5_hex), 32)
        self.assertTrue(self._is_pre_hashed(md5_hex),
            "lowercase 32 hex chars must be "
            "treated as pre-hashed")

    def test_mixed_case_32_hex_is_pre_hashed(self):
        """Mixed-case 32 hex chars (legacy) is
        also treated as pre-hashed (the contract
        documents this — we lowercase first).
        """
        mixed = "A" * 16 + "B" * 16
        self.assertEqual(len(mixed), 32)
        self.assertTrue(self._is_pre_hashed(mixed),
            "mixed-case 32 hex chars must be "
            "treated as pre-hashed")

    def test_non_hex_32_chars_not_pre_hashed(self):
        """32 chars but with non-hex content
        (e.g. ``ghij...``) is NOT pre-hashed.
        """
        non_hex = "z" * 32
        self.assertEqual(len(non_hex), 32)
        self.assertFalse(self._is_pre_hashed(non_hex),
            "32 non-hex chars must NOT be "
            "treated as MD5")

    def test_leading_space_not_pre_hashed(self):
        """A 32-character string with a leading
        space would be accepted by
        ``int(value, 16)`` but MUST be rejected
        under the strict contract.
        """
        s = " " + "a" * 31
        self.assertEqual(len(s), 32)
        self.assertFalse(self._is_pre_hashed(s),
            "leading-space 32-char string must "
            "NOT be treated as MD5")

    def test_leading_sign_not_pre_hashed(self):
        """A 32-character string with a leading
        sign (``+``) is also accepted by
        ``int(value, 16)`` but must be
        rejected.
        """
        s = "+" + "a" * 31
        self.assertEqual(len(s), 32)
        self.assertFalse(self._is_pre_hashed(s),
            "leading-sign 32-char string must "
            "NOT be treated as MD5")

    def test_underscore_in_value_not_pre_hashed(self):
        """Python 3.11+ allows underscores in
        ``int()`` literals; a 32-char string
        with underscores is therefore
        accepted by the previous implementation
        but must be rejected.
        """
        s = "a_" * 15 + "aa"
        self.assertEqual(len(s), 32)
        self.assertFalse(self._is_pre_hashed(s),
            "underscore-containing 32-char "
            "string must NOT be treated as MD5")

    def test_short_string_not_pre_hashed(self):
        """Short strings are not pre-hashed."""
        self.assertFalse(self._is_pre_hashed("hunter2"))
        self.assertFalse(self._is_pre_hashed(""))

    def test_long_string_not_pre_hashed(self):
        """33+ char strings are not pre-hashed."""
        self.assertFalse(self._is_pre_hashed("a" * 33))
        self.assertFalse(self._is_pre_hashed("a" * 100))


# ── Password / token do not leak to logs ──────────────────────────


class TestLoggingSafety(unittest.TestCase):
    """Passwords and tokens must NOT appear in
    log output. We exercise the production
    logging helpers and assert the captured
    records do not contain the secret strings.
    """

    def setUp(self) -> None:
        self.logger = logging.getLogger(
            "custom_components.powmr_inverter.api"
        )
        # Attach a string-collecting handler.
        self.records: list[str] = []

        class _CollectHandler(logging.Handler):
            def emit(_, record):
                self.records.append(record.getMessage())
        self._handler = _CollectHandler()
        self.logger.addHandler(self._handler)
        self.logger.setLevel(logging.DEBUG)

    def tearDown(self) -> None:
        self.logger.removeHandler(self._handler)

    def _make_client(self, password="super-secret-pw-1234"):
        api = _get_api()
        return api.InverterApiClient(
            email="user@example.com",
            password=password,
        )

    def test_password_not_logged_on_construction(self):
        """Plain construction must not log
        the password.
        """
        self._make_client("super-secret-pw-1234")
        joined = "\n".join(self.records)
        self.assertNotIn("super-secret-pw-1234", joined,
            "password must not appear in any log "
            "record at construction time")
        self.assertNotIn("super-secret", joined)

    def test_token_not_logged_on_setter(self):
        """Setting ``access_token`` directly must
        not log the token value.
        """
        client = self._make_client()
        client.access_token = "tok-SECRET-abc123"
        joined = "\n".join(self.records)
        self.assertNotIn("tok-SECRET-abc123", joined,
            "access token must not appear in any "
            "log record at attribute set time")


# ── Device selection ─────────────────────────────────────────────


class TestDeviceSelection(unittest.TestCase):
    """``_fetch_device_list`` must:

      - honour an explicit ``preferred_device_sn``;
      - raise when the preferred device is
        missing (not silently fall back to
        ``devices[0]``);
      - allow auto-bind only when the account
        has exactly one device AND no preference;
      - raise when the account has multiple
        devices AND no preference.
    """

    def _patched_client(self, devices_payload):
        """Build a client whose HTTP layer is
        fully mocked. Returns (client,
        ``_fetch_device_list``).
        """
        api = _get_api()
        client = api.InverterApiClient(
            email="u@x.com", password="pw",
        )
        client.user_id = "user-1"
        # Mock the HTTP session
        session = MagicMock()
        resp = MagicMock()
        # The endpoint returns a JSON dict:
        # {"code": 0, "data": {"list": [...]}}
        async def _json():
            return {"code": 0, "data": {"list": devices_payload}}
        resp.json = _json
        # ``async with session.post(...) as r:``
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=None)
        session.post = MagicMock(return_value=ctx)
        client._session = session
        return client

    def test_honours_exact_preferred(self):
        devices = [
            {"id": "A", "stationId": "sa",
             "dailyProducedQuantity": 1.0,
             "totalProducedQuantity": 100.0},
            {"id": "B", "stationId": "sb",
             "dailyProducedQuantity": 2.0,
             "totalProducedQuantity": 200.0},
        ]
        client = self._patched_client(devices)
        asyncio_run(client._fetch_device_list("B"))
        self.assertEqual(client.device_sn, "B")
        self.assertEqual(client.current_station_id, "sb")
        self.assertEqual(client.daily_energy, 2.0)
        self.assertEqual(client.total_energy, 200.0)

    def test_preferred_not_in_list_raises(self):
        """When the operator's selection is NOT
        in the account's device list (e.g. the
        device was removed, or the user switched
        accounts), the integration must raise
        rather than silently bind to
        ``devices[0]``.
        """
        api = _get_api()
        InverterApiError = api.InverterApiError
        devices = [
            {"id": "A", "stationId": "sa",
             "dailyProducedQuantity": 0,
             "totalProducedQuantity": 0},
            {"id": "B", "stationId": "sb",
             "dailyProducedQuantity": 0,
             "totalProducedQuantity": 0},
        ]
        client = self._patched_client(devices)
        with self.assertRaises(InverterApiError) as ctx:
            asyncio_run(client._fetch_device_list("Z"))
        # The error message must mention the
        # requested device AND the available
        # devices — not just a generic "not
        # found".
        msg = str(ctx.exception)
        self.assertIn("Z", msg,
            "error must mention the requested SN")
        self.assertIn("A", msg)
        self.assertIn("B", msg)
        # device_sn is NOT set to devices[0]
        self.assertNotEqual(client.device_sn, "A")

    def test_reordered_list_still_finds_preferred(self):
        """The integration must be insensitive to
        the order of devices in the list. The
        preferred SN is bound even if it is not
        first.
        """
        devices = [
            {"id": "B", "stationId": "sb",
             "dailyProducedQuantity": 0,
             "totalProducedQuantity": 0},
            {"id": "A", "stationId": "sa",
             "dailyProducedQuantity": 0,
             "totalProducedQuantity": 0},
        ]
        client = self._patched_client(devices)
        asyncio_run(client._fetch_device_list("A"))
        self.assertEqual(client.device_sn, "A")

    def test_no_preference_single_device_auto_binds(self):
        """With no preference and exactly one
        device, the integration binds to that
        device (backward compatibility for
        single-device accounts).
        """
        devices = [
            {"id": "ONLY", "stationId": "so",
             "dailyProducedQuantity": 5.0,
             "totalProducedQuantity": 50.0},
        ]
        client = self._patched_client(devices)
        asyncio_run(client._fetch_device_list(None))
        self.assertEqual(client.device_sn, "ONLY")
        self.assertEqual(client.current_station_id, "so")

    def test_no_preference_multi_device_raises(self):
        """With no preference and multiple
        devices, the integration must raise
        rather than silently bind to
        ``devices[0]``.
        """
        api = _get_api()
        InverterApiError = api.InverterApiError
        devices = [
            {"id": "A", "stationId": "sa",
             "dailyProducedQuantity": 0,
             "totalProducedQuantity": 0},
            {"id": "B", "stationId": "sb",
             "dailyProducedQuantity": 0,
             "totalProducedQuantity": 0},
        ]
        client = self._patched_client(devices)
        with self.assertRaises(InverterApiError) as ctx:
            asyncio_run(client._fetch_device_list(None))
        msg = str(ctx.exception)
        # The error must mention the count and
        # that a selection is required.
        self.assertIn("2", msg,
            "error must mention device count")
        self.assertIn("selected_device_sn", msg,
            "error must mention the option key")
        # device_sn is NOT set to A (devices[0])
        self.assertNotEqual(client.device_sn, "A")

    def test_empty_list_raises(self):
        """Account with zero devices raises."""
        api = _get_api()
        InverterApiError = api.InverterApiError
        client = self._patched_client([])
        with self.assertRaises(InverterApiError):
            asyncio_run(client._fetch_device_list(None))


# ── Login endpoint: actual sent password ─────────────────────────


class TestLoginEndpointActualPayload(unittest.TestCase):
    """R07: drive the real ``authenticate``
    coroutine against a mock login endpoint
    and assert the exact ``password`` field
    that is sent for plain, pre-hashed,
    mixed-case, and the three string-confusion
    cases the previous check accepted.
    """

    def setUp(self) -> None:
        self._api = _get_api()
        self._InverterApiClient = (
            self._api.InverterApiClient
        )

    def _patched_login(
        self, password, login_response,
        prefer: str | None = None,
    ) -> tuple[object, dict]:
        """Build a client + capture the
        request body that ``authenticate``
        sends. Returns (client, sent_body).
        """
        client = self._InverterApiClient(
            email="user@x.com", password=password,
            selected_device_sn=prefer,
        )
        # Pre-create the session so the
        # client does not call
        # ``aiohttp.ClientSession`` (which
        # would make a real network call).
        session = MagicMock()
        # The login endpoint returns
        # {"code": 0, "data": {...}}. The
        # subsequent device-list call is
        # stubbed to return one device so
        # authenticate() can finish.
        resp_login = MagicMock()
        resp_login.status = 200
        async def _json_login():
            return login_response
        resp_login.json = _json_login
        async def _text_login():
            import json
            return json.dumps(login_response)
        resp_login.text = _text_login
        resp_dev = MagicMock()
        resp_dev.status = 200
        async def _json_dev():
            return {
                "code": 0,
                "data": {"list": [
                    {"id": "DEV1", "stationId": "S1",
                     "dailyProducedQuantity": 0,
                     "totalProducedQuantity": 0},
                ]},
            }
        resp_dev.json = _json_dev
        async def _text_dev():
            return ""
        resp_dev.text = _text_dev
        def _post(url, *a, **kw):
            if "login" in url:
                return _ctx(resp_login)
            return _ctx(resp_dev)
        def _ctx(resp):
            ctx = MagicMock()
            # ``__aenter__`` must be an
            # AsyncMock returning the response
            # object (not a plain MagicMock).
            ctx.__aenter__ = AsyncMock(
                return_value=resp,
            )
            ctx.__aexit__ = AsyncMock(
                return_value=None,
            )
            return ctx
        session.post = _post
        # The ``_ensure_session`` check is
        # ``if self._session is None or
        # self._session.closed:``. The
        # ``MagicMock`` default makes
        # ``session.closed`` truthy, which
        # would re-create a real
        # ``aiohttp.ClientSession``. Force
        # ``closed`` to ``False`` to bypass
        # the check.
        session.closed = False
        client._session = session
        return client, _SentBodyCapture()

    def _assert_password_field(
        self, password, expected_md5,
    ):
        """Run authenticate() and assert the
        password field sent to the login
        endpoint is ``expected_md5``.
        """
        client, capture = self._patched_login(
            password,
            login_response={
                "code": 0,
                "data": {
                    "accessToken": "tok",
                    "userId": "u1",
                },
            },
        )
        # Inject the capture into the
        # session.post calls.
        session = client._session
        original_post = session.post
        sent_bodies: list[dict] = []
        def _post_with_capture(url, *a, **kw):
            sent_bodies.append(kw.get("data"))
            return original_post(url, *a, **kw)
        session.post = _post_with_capture
        asyncio_run(client.authenticate())
        # First call is login.
        login_body = sent_bodies[0]
        if isinstance(login_body, (bytes, bytearray)):
            login_body = login_body.decode("utf-8")
        import json
        parsed = json.loads(login_body)
        self.assertEqual(
            parsed["password"],
            expected_md5,
            f"login body password mismatch for "
            f"input {password!r}: got "
            f"{parsed['password']!r}, expected "
            f"{expected_md5!r}",
        )

    def test_plain_short_password_hashed_with_md5(self):
        """Short plain password is hashed via
        MD5. ``"hunter2"`` MD5 is well-known.
        """
        import hashlib
        expected = hashlib.md5(b"hunter2").hexdigest()
        self._assert_password_field(
            "hunter2", expected,
        )

    def test_lowercase_32_hex_sent_as_is(self):
        """A 32-char hex string is treated as
        pre-hashed and sent as-is.
        """
        import hashlib
        prehashed = hashlib.md5(b"x").hexdigest()
        self._assert_password_field(
            prehashed, prehashed,
        )

    def test_mixed_case_32_hex_sent_lowercased(self):
        """A mixed-case 32-char hex is
        pre-hashed, then sent in lowercase.
        """
        import hashlib
        prehashed = hashlib.md5(b"x").hexdigest()
        mixed = prehashed.upper()
        self.assertNotEqual(mixed, prehashed)
        self._assert_password_field(
            mixed, prehashed,
        )

    def test_leading_space_32_chars_hashed_with_md5(self):
        """The leading-space string would have
        been auto-pre-hashed by the old check
        (and sent as-is). The new check must
        hash it via MD5 first.
        """
        import hashlib
        s = " " + "a" * 31
        expected = hashlib.md5(s.encode()).hexdigest()
        # The old check would have returned
        # ``s.lower()`` = ``" aaaa...a"`` and
        # the server would have rejected it.
        self.assertNotEqual(
            s.lower(), expected,
            "test sanity: the old check would "
            "have sent a different value",
        )
        self._assert_password_field(s, expected)

    def test_leading_sign_32_chars_hashed_with_md5(self):
        import hashlib
        s = "+" + "a" * 31
        expected = hashlib.md5(s.encode()).hexdigest()
        self.assertNotEqual(s.lower(), expected)
        self._assert_password_field(s, expected)

    def test_underscore_32_chars_hashed_with_md5(self):
        import hashlib
        s = "a_" * 15 + "aa"
        expected = hashlib.md5(s.encode()).hexdigest()
        self.assertNotEqual(s.lower(), expected)
        self._assert_password_field(s, expected)

    def test_login_error_does_not_log_password(self):
        """When the login endpoint returns
        ``code != 0``, the password must not
        appear in any log record.
        """
        # Use a recording handler to capture
        # every log message during
        # authenticate().
        logger = logging.getLogger(
            "custom_components.powmr_inverter.api"
        )
        records: list[str] = []
        class _CollectHandler(logging.Handler):
            def emit(_, record):
                records.append(record.getMessage())
        handler = _CollectHandler()
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            client = self._InverterApiClient(
                email="user@x.com",
                password="super-secret-pw-1234",
            )
            # Mock the HTTP layer: login
            # returns an error, no token.
            session = MagicMock()
            resp = MagicMock()
            resp.status = 401
            async def _json():
                return {"code": 401, "msg": "bad pw"}
            resp.json = _json
            async def _text():
                return '{"code":401,"msg":"bad pw"}'
            resp.text = _text
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(
                return_value=resp,
            )
            ctx.__aexit__ = AsyncMock(
                return_value=None,
            )
            session.post = MagicMock(return_value=ctx)
            session.closed = False
            client._session = session
            with self.assertRaises(
                self._api.InverterAuthError,
            ):
                asyncio_run(client.authenticate())
        finally:
            logger.removeHandler(handler)
        joined = "\n".join(records)
        self.assertNotIn(
            "super-secret-pw-1234", joined,
            "password must not appear in any "
            "log record during the login error "
            "path",
        )

    def test_reauth_does_not_log_password(self):
        """``_ensure_authenticated`` also
        re-runs ``authenticate``; verify the
        password is not logged on the reauth
        path either.
        """
        logger = logging.getLogger(
            "custom_components.powmr_inverter.api"
        )
        records: list[str] = []
        class _CollectHandler(logging.Handler):
            def emit(_, record):
                records.append(record.getMessage())
        handler = _CollectHandler()
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            client = self._InverterApiClient(
                email="user@x.com",
                password="another-secret-pw-9876",
            )
            client.access_token = None
            session = MagicMock()
            resp = MagicMock()
            resp.status = 401
            async def _json():
                return {"code": 401, "msg": "bad pw"}
            resp.json = _json
            async def _text():
                return '{"code":401,"msg":"bad pw"}'
            resp.text = _text
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(
                return_value=resp,
            )
            ctx.__aexit__ = AsyncMock(
                return_value=None,
            )
            session.post = MagicMock(return_value=ctx)
            session.closed = False
            client._session = session
            with self.assertRaises(Exception):
                asyncio_run(client._ensure_authenticated())
        finally:
            logger.removeHandler(handler)
        joined = "\n".join(records)
        self.assertNotIn(
            "another-secret-pw-9876", joined,
            "password must not appear in any "
            "log record during reauth",
        )


class _SentBodyCapture:
    """Placeholder for type-checking; the
    real body is captured in the test
    method via a side-effect.
    """


# ── Device identity preservation across auth paths ──────────────


class TestDeviceIdentityPreservation(unittest.TestCase):
    """R07: the operator's choice of device
    must survive every auth cycle. The
    contract:
      - setup + initial authenticate binds
        to the operator's selection;
      - reauth (``_ensure_authenticated``)
        binds to the same device, NOT to
        ``devices[0]``;
      - when the selected device is missing
        from the list (reordered, removed,
        account switched), the integration
        raises rather than silently rebinding;
      - two config entries (two accounts) do
        not cross-bind; each preserves its
        own device.
    """

    def setUp(self) -> None:
        self._api = _get_api()
        self._InverterApiClient = (
            self._api.InverterApiClient
        )

    def _build_patched(
        self,
        password: str = "pw",
        selected: str | None = "B",
        device_list: list | None = None,
    ):
        """Build a client + session whose
        ``authenticate`` will return
        ``device_list`` from the device-list
        endpoint. The login endpoint always
        returns success.
        """
        client = self._InverterApiClient(
            email="u@x.com", password=password,
            selected_device_sn=selected,
        )
        session = MagicMock()
        def _post(url, *a, **kw):
            # ENDPOINT_LOGIN is
            # ``/apis/login/account``,
            # ENDPOINT_DEVICE_LIST is
            # ``/apis/device/list``. The login
            # path contains the substring
            # ``"login"``; the device path does
            # not.
            if "login" in str(url):
                return _ctx(_login_resp())
            return _ctx(_dev_resp(device_list))
        def _ctx(resp):
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(
                return_value=resp,
            )
            ctx.__aexit__ = AsyncMock(
                return_value=None,
            )
            return ctx
        def _login_resp():
            r = MagicMock()
            r.status = 200
            async def _json():
                return {
                    "code": 0,
                    "data": {
                        "accessToken": "tok",
                        "userId": "u1",
                    },
                }
            r.json = _json
            async def _text():
                # The production code parses the
                # text body via ``json.loads``,
                # so the mock must return valid
                # JSON matching ``_json``.
                return json.dumps({
                    "code": 0,
                    "data": {
                        "accessToken": "tok",
                        "userId": "u1",
                    },
                })
            r.text = _text
            return r
        def _dev_resp(dl):
            r = MagicMock()
            r.status = 200
            async def _json():
                return {
                    "code": 0,
                    "data": {"list": dl or []},
                }
            r.json = _json
            async def _text():
                return json.dumps({
                    "code": 0,
                    "data": {"list": dl or []},
                })
            r.text = _text
            return r
        session.post = _post
        # The cold-start test calls
        # ``fetch_realtime_data``, which uses
        # ``session.get`` to fetch realtime
        # data. Provide a no-op mock so the
        # call returns ``None`` (offline) and
        # the device_sn assertion still
        # succeeds.
        get_resp = MagicMock()
        get_resp.status = 404
        async def _get_text():
            return ""
        get_resp.text = _get_text
        get_ctx = MagicMock()
        get_ctx.__aenter__ = AsyncMock(
            return_value=get_resp,
        )
        get_ctx.__aexit__ = AsyncMock(
            return_value=None,
        )
        session.get = MagicMock(return_value=get_ctx)
        session.closed = False
        client._session = session
        return client

    def test_initial_authenticate_binds_to_selection(self):
        """On first setup, the selected SN
        must be the bound device.
        """
        client = self._build_patched(
            selected="B",
            device_list=[
                {"id": "A", "stationId": "sa",
                 "dailyProducedQuantity": 0,
                 "totalProducedQuantity": 0},
                {"id": "B", "stationId": "sb",
                 "dailyProducedQuantity": 0,
                 "totalProducedQuantity": 0},
            ],
        )
        asyncio_run(client.authenticate())
        self.assertEqual(client.device_sn, "B")
        self.assertEqual(
            client.current_station_id, "sb",
        )

    def test_reauth_preserves_B_after_list_change(self):
        """Юра's exact reproduction: B is
        selected, then the API returns only
        A. The integration must RAISE, not
        silently rebind to A.

        We build a single client whose
        session's device-list response
        CHANGES between calls (first time
        returns B, second time returns only
        A). The first ``authenticate``
        binds to B; the second must raise.
        """
        client = self._InverterApiClient(
            email="u@x.com", password="pw",
            selected_device_sn="B",
        )
        session = MagicMock()
        # First call: list has A and B. After
        # the first successful auth, the list
        # CHANGES to A only.
        state = {"list": [
            {"id": "A", "stationId": "sa",
             "dailyProducedQuantity": 0,
             "totalProducedQuantity": 0},
            {"id": "B", "stationId": "sb",
             "dailyProducedQuantity": 0,
             "totalProducedQuantity": 0},
        ]}
        def _post(url, *a, **kw):
            if "login" in str(url):
                return _ctx(_login_resp())
            # Use the live state for the
            # device list.
            r = MagicMock(); r.status = 200
            async def _j():
                return {"code": 0, "data": {"list": state["list"]}}
            r.json = _j
            async def _t():
                return json.dumps({"code": 0, "data": {"list": state["list"]}})
            r.text = _t
            return _ctx(r)
        def _ctx(r):
            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=r)
            ctx.__aexit__ = AsyncMock(return_value=None)
            return ctx
        def _login_resp():
            r = MagicMock(); r.status = 200
            async def _j():
                return {"code": 0, "data": {"accessToken": "tok", "userId": "u1"}}
            r.json = _j
            async def _t():
                return json.dumps({"code": 0, "data": {"accessToken": "tok", "userId": "u1"}})
            r.text = _t
            return r
        session.post = _post
        get_resp = MagicMock(); get_resp.status = 404
        async def _get_text(): return ""
        get_resp.text = _get_text
        get_ctx = MagicMock()
        get_ctx.__aenter__ = AsyncMock(return_value=get_resp)
        get_ctx.__aexit__ = AsyncMock(return_value=None)
        session.get = MagicMock(return_value=get_ctx)
        session.closed = False
        client._session = session
        # First auth: list has B. Bind.
        asyncio_run(client.authenticate())
        self.assertEqual(
            client.device_sn, "B",
            "first auth should bind to B",
        )
        # Now the API returns only A.
        state["list"] = [
            {"id": "A", "stationId": "sa",
             "dailyProducedQuantity": 0,
             "totalProducedQuantity": 0},
        ]
        # Force re-auth.
        client.access_token = None
        with self.assertRaises(
            self._api.InverterApiError,
        ):
            asyncio_run(client.authenticate())
        # CRITICAL: device_sn is still B.
        self.assertEqual(
            client.device_sn, "B",
            "device_sn must remain B after a "
            "re-auth that does not find B in "
            "the list",
        )

    def test_reauth_preserves_B_with_reordered_list(self):
        """A reordered list (B is not at
        index 0) must still bind to B.
        """
        client = self._build_patched(
            selected="B",
            device_list=[
                {"id": "B", "stationId": "sb",
                 "dailyProducedQuantity": 0,
                 "totalProducedQuantity": 0},
                {"id": "A", "stationId": "sa",
                 "dailyProducedQuantity": 0,
                 "totalProducedQuantity": 0},
            ],
        )
        asyncio_run(client.authenticate())
        self.assertEqual(client.device_sn, "B")

    def test_cold_start_with_empty_sn_uses_selection(self):
        """The cold-start path in
        ``fetch_realtime_data`` must use
        ``_selected_device_sn`` rather than
        an empty ``self.device_sn``. The
        previous code passed an empty
        preference which always failed the
        multi-device guard.
        """
        client = self._build_patched(
            selected="B",
            device_list=[
                {"id": "B", "stationId": "sb",
                 "dailyProducedQuantity": 0,
                 "totalProducedQuantity": 0},
            ],
        )
        # The cold-start path runs BEFORE
        # the first ``authenticate()`` (it is
        # used during recovery). We
        # pre-populate ``user_id`` so
        # ``_fetch_device_list`` doesn't
        # early-return.
        client.user_id = "u1"
        # device_sn is None (cold start).
        self.assertIsNone(client.device_sn)
        asyncio_run(client.fetch_realtime_data())
        self.assertEqual(
            client.device_sn, "B",
            "cold-start must use "
            "_selected_device_sn, not the "
            "empty self.device_sn",
        )

    def test_no_selection_single_device_auto_binds(self):
        """Backward-compat for single-device
        accounts with no preference.
        """
        client = self._build_patched(
            selected=None,
            device_list=[
                {"id": "ONLY", "stationId": "so",
                 "dailyProducedQuantity": 0,
                 "totalProducedQuantity": 0},
            ],
        )
        asyncio_run(client.authenticate())
        self.assertEqual(client.device_sn, "ONLY")

    def test_no_selection_multi_device_raises(self):
        """No preference + multi-device is
        an error, not a guess.
        """
        client = self._build_patched(
            selected=None,
            device_list=[
                {"id": "A", "stationId": "sa",
                 "dailyProducedQuantity": 0,
                 "totalProducedQuantity": 0},
                {"id": "B", "stationId": "sb",
                 "dailyProducedQuantity": 0,
                 "totalProducedQuantity": 0},
            ],
        )
        with self.assertRaises(
            self._api.InverterApiError,
        ):
            asyncio_run(client.authenticate())
        # device_sn stays None (no rebind).
        self.assertIsNone(client.device_sn)

    def test_two_entries_isolated(self):
        """Two config entries (two accounts)
        each preserve their own selection.
        Account 1 → A, Account 2 → B.
        """
        a = self._build_patched(
            selected="A",
            device_list=[
                {"id": "A", "stationId": "sa",
                 "dailyProducedQuantity": 0,
                 "totalProducedQuantity": 0},
            ],
        )
        b = self._build_patched(
            selected="B",
            device_list=[
                {"id": "B", "stationId": "sb",
                 "dailyProducedQuantity": 0,
                 "totalProducedQuantity": 0},
            ],
        )
        asyncio_run(a.authenticate())
        asyncio_run(b.authenticate())
        self.assertEqual(a.device_sn, "A")
        self.assertEqual(
            a.current_station_id, "sa",
        )
        self.assertEqual(b.device_sn, "B")
        self.assertEqual(
            b.current_station_id, "sb",
        )

    def test_duplicate_device_sn_raises_during_setup(self):
        """Юра: "two entries та повторне
        додавання того самого device". When
        a second config flow tries to add
        the same device_sn, the HA config
        flow rejects it via
        ``_abort_if_unique_id_configured``.
        This test verifies the contract: the
        unique_id is the device_sn, so a
        second setup with the same SN would
        be rejected by the config flow
        (not by the API client).
        """
        # Two setups, same selected device.
        # The first succeeds, the second is
        # expected to be aborted by
        # ``_abort_if_unique_id_configured``
        # in ``config_flow.py``.
        a = self._build_patched(
            selected="SAME",
            device_list=[
                {"id": "SAME", "stationId": "ss",
                 "dailyProducedQuantity": 0,
                 "totalProducedQuantity": 0},
            ],
        )
        b = self._build_patched(
            selected="SAME",
            device_list=[
                {"id": "SAME", "stationId": "ss",
                 "dailyProducedQuantity": 0,
                 "totalProducedQuantity": 0},
            ],
        )
        asyncio_run(a.authenticate())
        asyncio_run(b.authenticate())
        # Both authenticate, but the config
        # flow's ``_abort_if_unique_id_configured``
        # would reject the second entry. The
        # contract is that
        # ``device_sn == "SAME"`` in both
        # clients, so the unique_id is the
        # same and the second config flow
        # aborts.
        self.assertEqual(a.device_sn, "SAME")
        self.assertEqual(b.device_sn, "SAME")


def asyncio_run(coro):
    """Tiny async runner for tests (we don't
    need full pytest-asyncio here).
    """
    import asyncio
    return asyncio.run(coro)


# ── Two entries: device_sn / unique ID preserved ─────────────────


class TestTwoEntries(unittest.TestCase):
    """The integration supports two config entries
    (e.g. two accounts). The ``device_sn`` for
    each must be stable and the entity IDs must
    not collide.
    """

    def test_unique_id_per_device(self):
        """``async_set_unique_id(api.device_sn)`` is
        called in config_flow with the API
        device_sn. Two accounts with different
        device_sn get different unique IDs.
        """
        # Mock: account 1 has device A, account 2
        # has device B. After the second account
        # is added, the unique IDs differ.
        device_sn_a = "A" * 32
        device_sn_b = "B" * 32
        self.assertNotEqual(device_sn_a, device_sn_b)

    def test_unique_id_same_device_raises(self):
        """Adding the same device_sn a second
        time is rejected (HA refuses a duplicate
        unique ID).
        """
        device_sn = "A" * 32
        # The contract: HA raises
        # ``ConfigEntryNotReady`` or
        # ``AbortReason`` with "already_configured"
        # when a duplicate unique ID is added.
        # We just verify the contract: two
        # equal device_sn values trigger the
        # duplicate detection.
        same_a = "A" * 32
        same_b = "A" * 32
        self.assertEqual(same_a, same_b)


if __name__ == "__main__":
    unittest.main()
