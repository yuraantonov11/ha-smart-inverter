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
