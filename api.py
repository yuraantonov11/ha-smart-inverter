"""Inverter Smart Inverter API client.

Mirrors the Dart `InverterService` class — handles authentication with
MD5-signed requests, device discovery, real-time data polling, and
control commands to the solar.siseli.com API.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

from .const import (
    APP_ID,
    BASE_URL,
    CARBON_EMISSION_FACTOR,
    CHARGER_CSO,
    CHARGER_OSO,
    CHARGER_SNU,
    CHARGER_UTO,
    ENDPOINT_DEVICE_CONFIG,
    ENDPOINT_DEVICE_CONFIGS_READ,
    ENDPOINT_DEVICE_CONTROL,
    ENDPOINT_DEVICE_LIST,
    ENDPOINT_HISTORY,
    ENDPOINT_LOGIN,
    ENDPOINT_OVERVIEW_BASE,
    ENDPOINT_REALTIME,
    ENDPOINT_REALTIME_FALLBACK,
    ENCRYPTED_APP_SECRET,
    MIN_REQUEST_INTERVAL_MS,
    OUTPUT_SBU,
    OUTPUT_USB,
    SUMMARY_KEY_ENERGY,
    SUMMARY_KEY_POWER,
)

_LOGGER = logging.getLogger(__name__)

# T04: well-known secret field names that must never be written to a
# log line. The set is checked both in JSON objects and inside the
# raw body (e.g. when a malformed response is dumped at ERROR level).
_SECRET_KEYS = frozenset(
    {
        "accessToken",
        "refreshToken",
        "token",
        "idToken",
        "password",
        "passwd",
        "pwd",
        "secret",
        "apiKey",
        "apikey",
    }
)


def _redact_secrets(text: str) -> str:
    """Return ``text`` with well-known secret field values masked.

    Two passes:
      1. JSON-style ``"key":"value"`` or ``"key": "value"`` — replace
         the value with ``"***"``. Operates on the regex level so it
         survives responses that are almost but not quite valid JSON.
      2. URL-style ``key=value`` (e.g. ``?accessToken=…&…``) — replace
         the value with ``***``.

    This is intentionally not exhaustive. The goal is to keep token
    echoes out of HA's diagnostic bundle; a determined attacker who
    has access to the log file already has full access to the host.
    """
    if not text:
        return text
    masked = text
    for key in _SECRET_KEYS:
        # JSON-ish "key": "value"
        masked = re.sub(
            rf'("{re.escape(key)}"\s*:\s*)"[^"]*"',
            r'\1"***"',
            masked,
            flags=re.IGNORECASE,
        )
        # URL-ish key=value
        masked = re.sub(
            rf'({re.escape(key)}\s*=\s*)([^\s&,";]+)',
            r'\1***',
            masked,
            flags=re.IGNORECASE,
        )
    return masked


class InverterApiError(Exception):
    """Raised when the inverter API returns an error."""


class InverterAuthError(InverterApiError):
    """Raised when authentication fails."""


class InverterOfflineError(InverterApiError):
    """Raised when the inverter appears offline."""


class TokenExpiredError(InverterApiError):
    """Raised when the access token is expired and needs refresh/re-auth."""


class InverterApiClient:
    """Async HTTP client for the solar.siseli.com inverter API."""

    def __init__(
        self,
        email: str,
        password: str,
        selected_device_sn: str | None = None,
    ) -> None:
        self._email = email
        self._password = password
        # R07: explicit device preference. The
        # user-selected device (from the config
        # flow) is preserved across auth and
        # re-auth cycles. It is NEVER derived
        # from ``devices[0]`` — multi-device
        # accounts must make an explicit
        # choice.
        self._selected_device_sn: str | None = (
            selected_device_sn
        )
        self._session: aiohttp.ClientSession | None = None

        # Auth state
        self.access_token: str | None = None
        self.user_id: str | None = None
        self.device_sn: str | None = None
        self.current_station_id: str | None = None
        self.current_mode: int | None = None
        self._account_device_count = 0

        # Energy stats
        self.daily_energy: float = 0.0
        self.total_energy: float = 0.0
        self.co2_reduction: float = 0.0

        # Audit T20: freshness
        # metadata for ``daily_energy``.
        # ``daily_energy_at`` is the
        # UTC timestamp the value was
        # last refreshed by the API.
        # ``None`` means we have never
        # refreshed. ``daily_energy_date``
        # is the calendar date (in the
        # HA site's local timezone) when
        # the value was refreshed. The
        # audit explicitly says this
        # date is **not** API-attached -
        # the Powmr API returns a raw
        # ``dailyProducedQuantity`` with
        # no date stamp; the coordinator
        # attaches the date when it
        # refreshes so the sensor can
        # answer "for which day?".
        # ``daily_energy_date`` defaults
        # to ``None`` here and is set
        # to a ``date`` instance after a
        # successful ``fetch_overview``.
        self.daily_energy_at: datetime | None = None
        self.daily_energy_date: Any = None

        # Rate limiting
        self._last_request_time: dict[str, float] = {}
        # Async lock for rate limiting (prevents 5 simultaneous requests
        # when both InverterCoordinator and HistoryCoordinator share one api)
        self._rate_limit_lock: asyncio.Lock = asyncio.Lock()

        # Offline tracking
        self.last_realtime_offline: bool = False

        # Decrypt app secret once
        self._app_secret = self._decrypt_app_secret()

    # ── Crypto helpers (ported from Dart InverterService) ──────────────

    @staticmethod
    def _decrypt_app_secret() -> str:
        """AES-CBC decrypt the app secret (mirrors _decryptAppSecret).
        Uses cryptography library (built into HA) instead of pycryptodome.
        """
        md5_app_id = hashlib.md5(APP_ID.encode()).hexdigest()
        key_hex = md5_app_id[:16]
        iv_hex = md5_app_id[16:32]
        key = key_hex.encode()
        iv = iv_hex.encode()

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        raw = base64.b64decode(ENCRYPTED_APP_SECRET)
        decrypted = decryptor.update(raw) + decryptor.finalize()
        return decrypted.rstrip(b"\x00").decode().strip()

    @staticmethod
    def _generate_nonce(length: int = 32) -> str:
        """Generate random nonce string (mirrors _generateNonce)."""
        chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        return "".join(secrets.choice(chars) for _ in range(length))

    def _json_compact(self, body: dict | None) -> str:
        """Serialize dict to compact JSON (no spaces — matches Dart jsonEncode)."""
        if body is None:
            return "{}"
        return json.dumps(body, separators=(",", ":"), ensure_ascii=False)

    def _calculate_body_hash(self, method: str, body: dict | None) -> str:
        """Calculate SHA-256 body hash (mirrors _calculateBodyHash)."""
        payload = self._json_compact(body) if method.upper() != "GET" else "{}"
        return hashlib.sha256(payload.encode()).hexdigest()

    def _calculate_sign(self, app_id: str, nonce: str, body_hash: str) -> str:
        """Calculate API request signature (mirrors _calculateAppSign)."""
        payload = {
            "IOT-Open-AppID": app_id,
            "IOT-Open-Body-Hash": body_hash,
            "IOT-Open-Nonce": nonce,
        }
        query = "&".join(f"{k}={v}" for k, v in sorted(payload.items()))
        h = hmac.new(
            self._app_secret.encode(),
            digestmod=hashlib.sha256,
        )
        h.update(base64.b64encode(query.encode()))
        return hashlib.md5(h.digest()).hexdigest()

    def _build_headers(self, method: str, body: dict | None = None) -> dict[str, str]:
        """Build signed request headers."""
        nonce = self._generate_nonce()
        body_hash = self._calculate_body_hash(method, body)
        sign = self._calculate_sign(APP_ID, nonce, body_hash)

        headers: dict[str, str] = {
            "IOT-Open-AppID": APP_ID,
            "IOT-Open-Nonce": nonce,
            "IOT-Open-Body-Hash": body_hash,
            "IOT-Open-Sign": sign,
            "IOT-Time-Zone": "Europe/Kyiv",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json; charset=utf-8",
        }
        if self.access_token:
            headers["IOT-Token"] = self.access_token
        return headers

    async def _apply_rate_limit(self, endpoint: str) -> None:
        """Enforce minimum interval between requests to same endpoint."""
        # Lock guards the read-modify-write window so two coordinators
        # sharing one api instance can't race past the gate. Without the
        # lock, both _async_update_data() (InverterCoordinator, 5s loop) and
        # _async_update_data() (HistoryCoordinator, 15-min loop) can read
        # _last_request_time within microseconds of each other and both see
        # an expired entry → 5 simultaneous requests to the same endpoint.
        async with self._rate_limit_lock:
            now = time.monotonic()
            last = self._last_request_time.get(endpoint)
            if last is not None:
                elapsed_ms = (now - last) * 1000
                if elapsed_ms < MIN_REQUEST_INTERVAL_MS:
                    delay = (MIN_REQUEST_INTERVAL_MS - elapsed_ms) / 1000
                    _LOGGER.debug(
                        "Rate limit: %s, delay=%.2fs", endpoint, delay
                    )
                    await asyncio.sleep(delay)
            self._last_request_time[endpoint] = time.monotonic()

    # ── Session management ─────────────────────────────────────────────

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                base_url=BASE_URL,
                timeout=aiohttp.ClientTimeout(total=30, connect=15),
            )
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ── API methods ────────────────────────────────────────────────────

    @staticmethod
    def _is_pre_hashed_password(value: str) -> bool:
        """R07: detect pre-hashed (MD5) passwords.

        Contract:
          - ``len(value) == 32`` AND every char is a
            hex digit (0-9, a-f, A-F) ⇒ pre-hashed.
          - any other 32-character value (e.g. a
            plain password that happens to be
            32 characters long, or a 32-char string
            that contains non-hex whitespace /
            punctuation) is treated as plain
            and hashed here.
          - mixed-case hex (``ABCDEF12...``) is
            normalised to lowercase before
            comparison to be lenient on legacy
            inputs, but the contract is documented
            as ``exactly 32 ASCII hex digits``.

        The previous implementation used
        ``int(value, 16)`` which accepted leading
        whitespace, sign, and underscores (in
        Python 3.11+). Examples that returned True
        for the old check but must return False
        under the documented contract:
          - ``" " + "a" * 31`` (leading space)
          - ``"+" + "a" * 31`` (leading sign)
          - ``"a_" * 15 + "aa"`` (underscores,
            allowed in 3.11+ ``int()`` literals)

        This stricter check uses a per-character
        scan so the result matches the
        documentation.
        """
        if not isinstance(value, str) or len(value) != 32:
            return False
        # Per-character check: every char must be
        # in the hex alphabet. This is O(32) and
        # avoids any ``int()`` leniency.
        for ch in value:
            # ``ord("0") <= ord(ch) <= ord("9")`` OR
            # ``ord("a") <= ord(ch) <= ord("f")`` OR
            # ``ord("A") <= ord(ch) <= ord("F")``
            o = ord(ch)
            if not (
                (0x30 <= o <= 0x39)  # 0-9
                or (0x41 <= o <= 0x46)  # A-F
                or (0x61 <= o <= 0x66)  # a-f
            ):
                return False
        return True

    async def authenticate(
        self, preferred_device_sn: str | None = None,
    ) -> bool:
        """Login to solar.siseli.com and fetch device list.

        ``preferred_device_sn`` overrides the
        instance-level ``_selected_device_sn``
        (useful for tests). When neither is
        set, single-device accounts auto-bind
        and multi-device accounts raise — see
        ``_fetch_device_list`` for the full
        policy.

        The selected device is forwarded to
        ``_fetch_device_list`` so that
        re-authentication cycles (e.g. after a
        token expiry) preserve the device
        identity. Without this, an account
        with multiple devices whose previously
        selected device is ``B`` would silently
        rebind to ``A`` after a token refresh.
        """
        await self._ensure_session()
        await self._apply_rate_limit(ENDPOINT_LOGIN)

        # R07: detect pre-hashed (MD5) passwords
        # explicitly. A 32-character plain password
        # is NOT auto-treated as MD5 — we now check
        # that every character is a hex digit.
        if self._is_pre_hashed_password(self._password):
            password_md5 = self._password.lower()
        else:
            password_md5 = hashlib.md5(
                self._password.encode()
            ).hexdigest()

        body = {"account": self._email, "password": password_md5}
        headers = self._build_headers("POST", body)

        try:
            compact_body = self._json_compact(body)
            async with self._session.post(
                ENDPOINT_LOGIN, data=compact_body, headers=headers
            ) as resp:
                status = resp.status
                raw_text = await resp.text()
                # T04: never log raw response body — login responses
                # can carry accessToken / refreshToken / password echo.
                # We log a safe summary (status + code/error class)
                # and a redaction-stripped subset for diagnostics.
                _LOGGER.debug(
                    "Login response status=%d length=%d", status, len(raw_text)
                )
                try:
                    data = json.loads(raw_text)
                except (ValueError, TypeError) as json_err:
                    # T04 follow-up: never log the response body on
                    # parse failure either. The exception type and
                    # length are enough to triage; a sanitised dump
                    # is still risky because our regex only catches
                    # well-known field names — a token echoed in an
                    # unexpected shape would slip through.
                    _LOGGER.error(
                        "Login JSON parse error: %s (status=%d, body_len=%d)",
                        json_err, status, len(raw_text),
                    )
                    raise InverterAuthError(
                        f"Invalid API response (status={status})"
                    )
        except aiohttp.ClientError as exc:
            _LOGGER.error("Login request failed: %s", exc)
            raise InverterApiError(f"Connection failed: {exc}") from exc

        code = data.get("code")
        if code != 0:
            msg = data.get("msg", data.get("message", "Unknown error"))
            # T04: never dump the full data dict on login failure;
            # it may contain accessToken or refreshToken echoed back.
            _LOGGER.error(
                "Login failed: code=%s msg=%s", code, msg,
            )
            raise InverterAuthError(msg)

        resp_data = data.get("data", {})
        self.access_token = resp_data.get("accessToken") or resp_data.get("token")
        self.user_id = str(resp_data.get("userId", ""))

        # R07: forward the device preference.
        # ``preferred_device_sn`` (caller-supplied)
        # wins over the instance-level
        # ``_selected_device_sn`` (operator's
        # persistent choice). Either keeps the
        # device identity stable across auth
        # cycles.
        effective_pref = (
            preferred_device_sn
            if preferred_device_sn is not None
            else self._selected_device_sn
        )
        await self._fetch_device_list(effective_pref)
        return True

    async def _ensure_authenticated(self) -> None:
        """Re-authenticate silently when token is missing or expired."""
        _LOGGER.info("Re-authenticating with Inverter API")
        self.access_token = None  # force fresh login
        try:
            await self.authenticate()
            _LOGGER.info("Re-authentication successful")
        except InverterAuthError as exc:
            raise TokenExpiredError(
                f"Re-authentication failed (invalid credentials): {exc}"
            ) from exc

    async def _fetch_device_list(
        self, preferred_device_sn: str | None = None,
    ) -> None:
        """Fetch device list and populate device_sn / station_id.

        R07 audit: when ``preferred_device_sn`` is
        provided (from the integration's options),
        we look for that device in the list and
        bind to it. If the preferred device is NOT
        in the list (reordered, removed, account
        switched), we raise an explicit error
        rather than silently falling back to
        ``devices[0]``. The previous behaviour
        silently bound to a different inverter
        when the account had multiple devices and
        the user expected a specific one.

        When ``preferred_device_sn`` is None and
        the account has exactly one device, we
        bind to it. When the account has
        multiple devices and no preference, we
        raise ``InverterApiError`` — the operator
        must select one via the options flow
        rather than letting the integration pick
        arbitrarily.
        """
        if not self.user_id:
            return

        await self._apply_rate_limit(ENDPOINT_DEVICE_LIST)
        body = {"page": 1, "count": 10, "applyModeCategory": 1}
        headers = self._build_headers("POST", body)

        async with self._session.post(
            ENDPOINT_DEVICE_LIST, data=self._json_compact(body), headers=headers
        ) as resp:
            data = await resp.json()

        if data.get("code") == 0 and data.get("data"):
            devices = data["data"].get("list", [])
            self._account_device_count = len(devices)
            if not devices:
                raise InverterApiError("No devices found for this account")
            # R07: pick the device EXPLICITLY.
            dev = None
            if preferred_device_sn:
                for d in devices:
                    if str(d.get("id", "")) == preferred_device_sn:
                        dev = d
                        break
                if dev is None:
                    # Preferred device is missing —
                    # account switched, or the device
                    # was removed. Do NOT silently
                    # fall back to devices[0].
                    raise InverterApiError(
                        f"Selected device {preferred_device_sn!r} "
                        f"not found in account's device list "
                        f"(have: {[str(d.get('id', '')) for d in devices]})"
                    )
            else:
                # No preference: only allow
                # auto-binding when there is exactly
                # one device. Multiple devices
                # without a selection is an error,
                # not a guess.
                if len(devices) > 1:
                    raise InverterApiError(
                        f"Account has {len(devices)} devices but "
                        f"no selection configured. Set "
                        f"'selected_device_sn' in the integration "
                        f"options to choose one of: "
                        f"{[str(d.get('id', '')) for d in devices]}"
                    )
                dev = devices[0]
            self.device_sn = str(dev.get("id", ""))
            self.current_station_id = str(dev.get("stationId", ""))
            self.daily_energy = self._parse_double(
                dev.get("dailyProducedQuantity")
            )
            self.total_energy = self._parse_double(
                dev.get("totalProducedQuantity")
            )
            # Audit T20 follow-up: publish
            # the freshness triple
            # alongside ``daily_energy``.
            # ``_now_utc`` is the UTC
            # timestamp the API was
            # refreshed. We always store
            # ``daily_energy_at`` as a
            # timezone-aware UTC datetime
            # so the freshness helper can
            # subtract it from a
            # timezone-aware ``now`` without
            # ``TypeError``. ``daily_energy_date``
            # is the calendar date in the
            # host's local timezone because
            # the inverter rolls its
            # counter back to zero at local
            # midnight. ``dt_util`` is the
            # HA utility used elsewhere in
            # the codebase; if it is not
            # available (standalone / unit
            # tests) we fall back to the
            # naive ``datetime.now()``
            # which the freshness helper
            # normalises to UTC.
            _now_utc = datetime.now(tz=timezone.utc)
            self.daily_energy_at = _now_utc
            try:
                from homeassistant.util import (
                    dt as _ha_dt,
                )
                _local = _ha_dt.as_local(_now_utc)
            except ImportError:
                _local = _now_utc.astimezone()
            self.daily_energy_date = _local.date()
            self._update_co2()
            _LOGGER.info(
                "Device found: SN=%s station=%s",
                self.device_sn,
                self.current_station_id,
            )
        else:
            raise InverterApiError(
                data.get("msg", "Failed to fetch device list")
            )

    async def _list_devices(self) -> list[dict[str, Any]]:
        """Return the raw device list from the
        account without binding to any
        particular device. Used by the
        config-flow device picker.

        Raises ``InverterApiError`` if the
        request fails.
        """
        if not self.user_id:
            # Caller must ``authenticate()``
            # first.
            return []
        await self._apply_rate_limit(ENDPOINT_DEVICE_LIST)
        body = {"page": 1, "count": 10, "applyModeCategory": 1}
        headers = self._build_headers("POST", body)
        async with self._session.post(
            ENDPOINT_DEVICE_LIST,
            data=self._json_compact(body),
            headers=headers,
        ) as resp:
            data = await resp.json()
        if data.get("code") == 0 and data.get("data"):
            devices = data["data"].get("list", [])
            self._account_device_count = len(devices)
            return devices
        raise InverterApiError(
            data.get("msg", "Failed to fetch device list")
        )

    async def refresh_device_summary(self) -> bool:
        """Refresh ``daily_energy``,
        ``total_energy``, ``daily_energy_at``,
        ``daily_energy_date`` and
        ``co2_reduction`` from the device
        list endpoint.

        Audit T20 follow-up (Windows
        review): the production path
        was previously updating
        ``daily_energy`` /
        ``daily_energy_at`` only inside
        ``_fetch_device_list`` which
        was called at login or when
        ``device_sn`` was missing.
        Ordinary polling did not
        refresh the value, so a
        sensor reading stayed at the
        login value for hours.

        The coordinator now calls
        this method on every polling
        cycle subject to a TTL (default
        15 minutes). Failure keeps
        the previous ``daily_energy``
        and ``daily_energy_at`` so
        the sensor surfaces ``stale``
        instead of an outage.

        Returns ``True`` when the
        device list response was
        parsed and at least one device
        was found; ``False`` otherwise.
        """
        try:
            await self._apply_rate_limit(ENDPOINT_DEVICE_LIST)
        except Exception:
            return False
        body = {"page": 1, "count": 10, "applyModeCategory": 1}
        headers = self._build_headers("POST", body)
        try:
            async with self._session.post(
                ENDPOINT_DEVICE_LIST,
                data=self._json_compact(body),
                headers=headers,
            ) as resp:
                data = await resp.json()
        except Exception:
            # Network or transport error.
            # Keep the previous value
            # and timestamp.
            return False
        if not (data.get("code") == 0 and data.get("data")):
            return False
        devices = data["data"].get("list", [])
        self._account_device_count = len(devices)
        if not devices:
            return False
        # Audit T20 fix #1: match by
        # ``device_sn`` first. The
        # previous bug picked
        # ``devices[0]`` regardless of
        # which device the
        # coordinator was configured
        # for. We match the
        # configured ``device_sn``
        # against each ``entry.id``;
        # ``current_station_id`` is a
        # **fallback** that only runs
        # if ``device_sn`` is empty
        # (older entries that pre-date
        # the per-device selector).
        #
        # The audit is explicit: "якщо
        # його немає — зберігай кеш і
        # повертай failure". If
        # ``device_sn`` is set and no
        # entry matches, we DO NOT
        # fall back to
        # ``current_station_id`` -
        # doing so would silently
        # accept a sibling device's
        # energy.
        target_id = str(self.device_sn or "")
        target_station = str(self.current_station_id or "")
        dev = None
        for entry in devices:
            entry_id = str(entry.get("id", ""))
            entry_station = str(entry.get("stationId", ""))
            if target_id and entry_id == target_id:
                dev = entry
                break
        if dev is None and not target_id and target_station:
            # ``device_sn`` is empty -
            # the older selector is the
            # ``current_station_id``.
            # The audit's predecessor
            # behaviour matches here: a
            # legacy entry without
            # ``device_sn`` still works.
            for entry in devices:
                entry_station = str(entry.get("stationId", ""))
                if entry_station == target_station:
                    dev = entry
                    break
        if dev is None:
            # Audit T20: cache is
            # preserved; the freshness
            # helper exposes
            # ``daily_energy_stale=True``
            # so the user knows the
            # value is stale.
            return False
        # Audit T20 fix #2: validate
        # before mutating any cache
        # field. The audit's round 2
        # observation: a universal
        # ``<= 20 000`` upper cap is
        # wrong for the cumulative
        # ``totalProducedQuantity``
        # which grows monotonically
        # over the station's lifetime.
        # We bound only the daily
        # reading by
        # ``_MAX_DAILY_KWH`` and keep
        # the total unbounded. The
        # monotonic-decrease check
        # below still protects the
        # ``total_increasing`` state
        # class.
        daily_raw = dev.get("dailyProducedQuantity")
        total_raw = dev.get("totalProducedQuantity")
        daily_value = self._parse_double(daily_raw)
        total_value = self._parse_double(total_raw)
        if not self._is_valid_double(daily_raw, daily_value):
            return False
        if not self._is_valid_double(total_raw, total_value):
            return False
        if (
            self._MAX_DAILY_KWH is not None
            and daily_value > self._MAX_DAILY_KWH
        ):
            # The daily reading is
            # out of range. Reject -
            # we do not know whether
            # the API is reporting junk
            # or the inverter is
            # misconfigured.
            return False
        if (
            self.total_energy is not None
            and self.total_energy > 0
            and total_value < self.total_energy - 0.001
        ):
            # Audit T20 fix #2:
            # monotonic-decrease
            # protection for the
            # cumulative total. The
            # inverter's
            # ``totalProducedQuantity``
            # is monotonic over the
            # station's lifetime; a
            # decrease means the
            # device list re-mapped or
            # returned a stale cache.
            return False
        self.daily_energy = daily_value
        self.total_energy = total_value
        from datetime import datetime as _dt, timezone as _tz
        self.daily_energy_at = _dt.now(_tz.utc)
        self._update_co2()
        return True

    @staticmethod
    def _is_valid_double(raw, value) -> bool:
        """Validate an energy payload
        value without silently clobbering
        the cache.

        Audit T20 fix #2 (round 2): a
        universal ``<= 20 000`` upper
        cap is wrong for
        ``totalProducedQuantity`` -
        it is cumulative lifetime
        kWh and grows monotonically.
        A working station with
        25 MWh of cumulative output
        would have a ``total`` of
        25 000 and we would silently
        reject it. Keep the numeric /
        finite / non-negative checks;
        drop the upper cap for the
        cumulative total. The audit
        was explicit: "прибери довільну
        верхню межу cumulative total;
        збережи перевірки числового
        типу, скінченності,
        невід'ємності та захист від
        зменшення."

        Returns ``True`` when the raw
        value parses to a finite,
        non-negative number. ``raw=None``
        / ``raw=""`` / ``raw="bad"``
        are rejected. A real ``0`` is
        accepted.

        The function is the shared
        validator for both the daily and
        the cumulative field; the upper
        cap is the caller's
        responsibility (use
        ``_MAX_DAILY_KWH`` /
        ``_MAX_TOTAL_KWH`` if a cap is
        needed).
        """
        if raw is None:
            return False
        if isinstance(raw, str) and raw.strip() == "":
            return False
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return False
        import math as _math
        if _math.isnan(v) or _math.isinf(v):
            return False
        if v < 0:
            return False
        return True

    # Audit T20 round 2: the
    # daily reading is bounded by
    # the inverter's daily maximum
    # (``~20 kW * 24 h ~ 480 kWh``).
    # The cumulative reading is
    # unbounded; we drop the cap.
    _MAX_DAILY_KWH = 1000.0
    _MAX_TOTAL_KWH = None


    async def fetch_realtime_data(self) -> dict[str, Any] | None:
        """Fetch real-time inverter data mirroring Dart getRealTimeData."""
        if not self.device_sn:
            _LOGGER.warning("No device selected, trying to re-fetch")
            # R07: re-fetch using the
            # operator's persistent preference
            # (``_selected_device_sn``), NOT an
            # empty string. An empty preference
            # is meaningless and would cause the
            # multi-device guard to fail even on
            # a single-device account. We use
            # ``_selected_device_sn`` first; if
            # it is also None, fall back to
            # ``self.device_sn`` (which is
            # already empty in this branch but
            # kept for symmetry with the rest of
            # the auth cycle).
            pref = self._selected_device_sn or self.device_sn
            await self._fetch_device_list(pref)
            if not self.device_sn:
                self.last_realtime_offline = True
                return None

        for attempt in range(2):
            try:
                primary = await self._try_realtime_endpoint(ENDPOINT_REALTIME)
                if primary is not None:
                    return primary

                fallback = await self._try_realtime_endpoint(ENDPOINT_REALTIME_FALLBACK)
                if fallback is not None:
                    return fallback
            except TokenExpiredError:
                if attempt == 0:
                    _LOGGER.warning("Token expired, re-authenticating (attempt %d)", attempt + 1)
                    await self._ensure_authenticated()
                    continue
                raise

            break  # both endpoints returned None (offline/no data)

        _LOGGER.warning(
            "Realtime data empty from both endpoints for deviceId=%s",
            self.device_sn,
        )
        self.last_realtime_offline = True
        return None

    async def _try_realtime_endpoint(self, endpoint: str) -> dict[str, Any] | None:
        """Try a realtime endpoint with GET first, then POST on 405."""
        await self._apply_rate_limit(endpoint)
        params = {"deviceId": self.device_sn, "dataSource": 1}
        headers = self._build_headers("GET", None)

        raw_text = ""
        data: dict[str, Any] | None = None
        try:
            async with self._session.get(endpoint, params=params, headers=headers) as resp:
                raw_text = await resp.text()
                if resp.status == 405:
                    body = {"deviceId": self.device_sn, "dataSource": 1}
                    headers_post = self._build_headers("POST", body)
                    async with self._session.post(
                        endpoint,
                        data=self._json_compact(body),
                        headers=headers_post,
                    ) as resp2:
                        raw_text = await resp2.text()
                data = json.loads(raw_text)
        except aiohttp.ClientError as exc:
            _LOGGER.warning("Realtime request failed for %s: %s", endpoint, exc)
            return None
        except (ValueError, TypeError):
            _LOGGER.warning("Realtime invalid JSON from %s: %s", endpoint, raw_text[:200])
            return None

        code = data.get("code") if isinstance(data, dict) else None
        message = None
        if isinstance(data, dict):
            message = data.get("message") or data.get("localMessage") or data.get("msg")

        # Token expired — must re-authenticate
        # Robust token-expired detection. The previous logic only matched
        # code==9 or the literal phrase "token expired". The cloud has
        # returned 401/1001/1002, plain "Unauthorized", "auth failed",
        # and "invalid token" in production — none of which triggered
        # re-auth, leading to an infinite "no data, retry" loop on stale
        # tokens until the user manually reloaded the integration.
        if code in (9, 401, 1001, 1002, 40101, 40102):
            raise TokenExpiredError(
                f"Token expired on {endpoint} (code={code})"
            )
        if isinstance(message, str):
            msg_lower = message.lower()
            if (
                ("token" in msg_lower and ("expired" in msg_lower or "invalid" in msg_lower))
                or ("auth" in msg_lower and ("fail" in msg_lower or "invalid" in msg_lower or "denied" in msg_lower))
                or ("unauthorized" in msg_lower)
                or ("session" in msg_lower and ("expired" in msg_lower or "invalid" in msg_lower))
            ):
                raise TokenExpiredError(
                    f"Token expired on {endpoint}: {message}"
                )
        # HTTP 401 with empty body — also treat as expired.
        if resp.status == 401 and not message:
            raise TokenExpiredError(
                f"HTTP 401 from {endpoint} (empty body)"
            )

        payload = self._extract_realtime_payload(data) if isinstance(data, dict) else None
        if code == 0 and payload is not None:
            self.last_realtime_offline = False
            fields = payload.get("deviceAttributeState", {})
            return self._parse_realtime_fields(fields, payload)

        is_offline = code == 71000 or (
            isinstance(message, str) and "offline" in message.lower()
        )
        if is_offline:
            self.last_realtime_offline = True
            _LOGGER.info(
                "Inverter offline on %s: code=%s message=%s",
                endpoint,
                code,
                message,
            )
            return None

        shape = self._describe_data_shape(data.get("data") if isinstance(data, dict) else None)
        _LOGGER.warning(
            "Realtime endpoint empty: endpoint=%s code=%s message=%s dataShape=%s",
            endpoint,
            code,
            message,
            shape,
        )
        return None

    def _extract_realtime_payload(self, data: dict) -> dict | None:
        """Extract the nested realtime payload from the response."""
        resp_data = data.get("data")
        if not isinstance(resp_data, dict):
            if isinstance(data.get("deviceAttributeState"), dict):
                return data
            return None

        if "deviceAttributeState" in resp_data:
            return resp_data

        for key in ("payload", "deviceState", "latestState"):
            candidate = resp_data.get(key)
            if isinstance(candidate, dict) and "deviceAttributeState" in candidate:
                return candidate

        return resp_data

    @staticmethod
    def _describe_data_shape(data: Any) -> str:
        """Describe the returned payload shape for diagnostics."""
        if data is None:
            return "null"
        if isinstance(data, dict):
            if not data:
                return "empty-map"
            keys = ",".join(list(data.keys())[:8])
            return f"map(keys={keys})"
        if isinstance(data, list):
            return f"list(len={len(data)})"
        return type(data).__name__

    def _parse_realtime_fields(
        self, fields: dict, payload: dict
    ) -> dict[str, Any]:
        """Parse raw realtime fields into structured data."""
        raw_fields = fields
        nested = fields.get("fields") if isinstance(fields, dict) else None
        if isinstance(nested, dict):
            raw_fields = nested

        def _val(key: str, default: float = 0.0, kw: bool = False) -> float:
            item = raw_fields.get(key, {})
            if isinstance(item, dict):
                val = self._parse_double(item.get("value"), default)
            else:
                val = self._parse_double(item, default)
            return val * 1000 if kw else val

        def _first_present(
            _raw_fields: dict,
            keys: tuple[str, ...],
            default: float | None = 0.0,
        ):
            """Return the first raw field that is present and finite.

            Used to recover a real ``0`` from API payloads: ``_val`` returns
            the supplied default for absent keys, and ``a or b`` swallows a
            legitimate 0 because 0 is falsy. T01 fix.
            """
            sentinel: Any = object()
            for key in keys:
                if key not in _raw_fields:
                    continue
                item = _raw_fields[key]
                if isinstance(item, dict):
                    raw = item.get("value")
                else:
                    raw = item
                parsed = self._parse_double(raw, sentinel)
                if parsed is sentinel:
                    continue
                try:
                    if not math.isfinite(float(parsed)):
                        continue
                except (TypeError, ValueError):
                    continue
                return float(parsed)
            return default

        def _str(key: str, default: str = "") -> str:
            item = raw_fields.get(key, {})
            if isinstance(item, dict):
                return str(item.get("valueDisplay") or item.get("value") or default)
            return str(item) if item else default

        pv_power = _val("pvInputPower") or _val("generationPower") or _val("solarPower") or _val("pvPower")
        load_power = _val("acOutputActivePower", kw=True) or _val("loadPower") or _val("outputPower") or _val("acOutputPower")

        battery_voltage = _val("batteryVoltage")
        battery_charge_current = _val("batteryChargingCurrent")
        battery_discharge_current = _val("batteryDischargeCurrent")

        battery_current = _val("batteryCurrent", 0.0)
        if battery_current == 0.0:
            if battery_charge_current > 0:
                battery_current = battery_charge_current
            elif battery_discharge_current > 0:
                battery_current = -battery_discharge_current

        battery_power = _val("batteryPower")
        if battery_power == 0.0 and battery_voltage > 0:
            if battery_charge_current > 0:
                battery_power = battery_charge_current * battery_voltage
            elif battery_discharge_current > 0:
                battery_power = -battery_discharge_current * battery_voltage

        grid_power = _val("gridPower") or _val("acInputPower")
        grid_direction = _val("gridPowerDirection", 1.0)
        if grid_direction < 0:
            grid_power = -abs(grid_power)

        working_state = _str("workingStates")
        working_state_val = ""
        ws = raw_fields.get("workingStates")
        if isinstance(ws, dict):
            working_state_val = str(ws.get("value", ""))
        is_line_mode = working_state_val == "4" or "line" in working_state.lower()
        if grid_power == 0.0 and is_line_mode and _val("acInputVoltage") > 0:
            grid_power = load_power + max(0.0, battery_power) - pv_power
            if grid_power < 0:
                grid_power = 0.0

        output_priority = _str("outputSourcePriority") or _str("outputSourcePrioritySetting")
        charger_priority = _str("chargerSourcePriority") or _str("chargerSourcePrioritySetting")

        # Additional fields from latest_state API
        ac_output_power = _val("acOutputActivePower", kw=True)
        feed_in_power = _val("feedInPower")
        grid_import_power = grid_power  # alias for Energy Dashboard compatibility
        battery_charge_current_sep = _val("batteryChargingCurrent")
        battery_discharge_current_sep = _val("batteryDischargeCurrent")
        inverter_temp = _val("ntcMaximumTemperature") or _val("radiatorTemperature") or _val("invTemperature") or _val("temperature")
        pv_input_voltage = _val("pvVoltage") or _val("solarVoltage") or _val("pvInputVoltage")

        # Rated/nominal inverter specs (exposed by latest_state API).
        # Useful for verifying operation within the device envelope and
        # for power-quality monitoring.
        nominal_ac_voltage = _val("nominalAcVoltage")
        nominal_ac_current = _val("nominalAcCurrent")
        rated_active_power = _val("ratedActivePower")
        rated_apparent_power = _val("acOutputRatingApparentPower")
        output_apparent_power = _val("outputApparentPower")
        output_frequency = _val("outputFrequency")

        return {
            "pvPower": pv_power,
            "gridPower": grid_power,
            "batteryPower": battery_power,
            "loadPower": load_power,
            # T01 fix: a real batterySoc of 0 must NOT be coerced into a
            # fallback value. Python's ``or`` treats 0 as falsy and would
            # silently swap it for batteryCapacity (or the default 100),
            # making a critically-empty battery look full. Use the first
            # *present and valid* field instead. Missing SOC stays None so
            # downstream consumers (coordinator / sensors) can mark the
            # value as unknown rather than fabricate a 100% reading.
            "batterySoc": _first_present(
                raw_fields, ("batterySoc", "batteryCapacity"), default=None
            ),
            "pvVoltage": pv_input_voltage,
            "gridVoltage": _val("gridVoltage") or _val("acInputVoltage"),
            "batteryVoltage": battery_voltage,
            "loadPercentage": _val("loadPercent") or _val("loadPercentage"),
            "workingMode": working_state or _str("workingMode") or _str("deviceMode"),
            "outputSourcePriority": output_priority,
            "chargerSourcePriority": charger_priority,
            "batteryCurrent": battery_current,
            # New metrics from latest_state
            "acOutputPower": ac_output_power,
            "feedInPower": feed_in_power,
            "gridImportPower": grid_import_power,
            "batteryChargeCurrent": battery_charge_current_sep,
            "batteryDischargeCurrent": battery_discharge_current_sep,
            "inverterTemperature": inverter_temp,
            "nominalAcVoltage": nominal_ac_voltage,
            "nominalAcCurrent": nominal_ac_current,
            "ratedActivePower": rated_active_power,
            "ratedApparentPower": rated_apparent_power,
            "outputApparentPower": output_apparent_power,
            "outputFrequency": output_frequency,
            "rawFields": raw_fields,
            "payload": payload,
        }

    async def set_mode(self, mode: int) -> bool:
        """Set inverter operating mode (0=USB, 2=SBU, etc.)."""
        if not self.device_sn:
            return False

        await self._apply_rate_limit(ENDPOINT_DEVICE_CONTROL)
        body = {
            "deviceSn": self.device_sn,
            "mode": mode,
        }
        headers = self._build_headers("POST", body)

        try:
            async with self._session.post(
                ENDPOINT_DEVICE_CONTROL, data=self._json_compact(body), headers=headers
            ) as resp:
                data = await resp.json()
        except aiohttp.ClientError as exc:
            _LOGGER.error("set_mode request failed: %s", exc)
            return False

        if data.get("code") == 0:
            self.current_mode = mode
            _LOGGER.info("Mode set to %s", mode)
            return True

        _LOGGER.error("set_mode failed: %s", data.get("msg"))
        return False

    async def set_config_item(self, key: str, value: str) -> bool:
        """Write a single configuration item to the inverter.

        Some firmware/API paths intermittently return an error payload for a
        valid value. Retry once with a short backoff before surfacing an error.
        """
        if not self.device_sn:
            return False

        # Backward compatibility: accept legacy keys and route to API keys
        # used by the verified Flutter client.
        key_aliases = {
            "maxChargingCurrent": "setMaxChargingCurrent",
            "maxUtilityChargingCurrent": "setUtilityMaxChargingCurrent",
            "outputSourcePriority": "outputSourcePrioritySetting",
            "chargerSourcePriority": "chargerSourcePrioritySetting",
        }
        normalized_key = key_aliases.get(key, key)
        if normalized_key != key:
            _LOGGER.warning("Legacy config key remapped: %s -> %s", key, normalized_key)
            key = normalized_key

        # Device-safe normalization for charging current writes.
        if key in ("setMaxChargingCurrent", "setUtilityMaxChargingCurrent"):
            try:
                amps = int(round(float(value)))
                amps = max(0, min(200, amps))
                amps = int(round(amps / 5) * 5)
                value = str(max(0, min(200, amps)))
            except (TypeError, ValueError):
                _LOGGER.error("Invalid charging current value for %s: %s", key, value)
                return False

        last_msg: str | None = None
        for attempt in range(2):
            await self._apply_rate_limit(ENDPOINT_DEVICE_CONFIG)
            # Match the Flutter client body format exactly:
            #   queryParameters: {'deviceId': <sn>}
            #   body: {id: <sn>, key: <key>, value: <value>}
            body = {
                "id": self.device_sn,
                "key": key,
                "value": value,
            }
            params = {"deviceId": self.device_sn}
            headers = self._build_headers("POST", body)

            try:
                async with self._session.post(
                    ENDPOINT_DEVICE_CONFIG,
                    params=params,
                    data=self._json_compact(body),
                    headers=headers,
                ) as resp:
                    data = await resp.json()
            except aiohttp.ClientError as exc:
                last_msg = str(exc)
                if attempt == 0:
                    # Exponential backoff with jitter: 0.5s base, doubles each
                    # attempt, plus up to 100ms jitter to desynchronise multiple
                    # devices retrying in lockstep after a brief cloud outage.
                    # Cap at 4s. Plain fixed-sleep retries pile up if many
                    # devices retry at the same instant.
                    backoff = min(0.5 * (2 ** attempt) + random.uniform(0, 0.1), 4.0)
                    await asyncio.sleep(backoff)
                    continue
                _LOGGER.error("set_config_item request failed: %s", exc)
                return False

            ok = data.get("code") == 0
            if ok:
                _LOGGER.info("Config set: %s=%s", key, value)
                return True

            last_msg = data.get("msg")
            if attempt == 0:
                await asyncio.sleep(0.8)
                continue

        if last_msg:
            _LOGGER.warning("Config write rejected: %s=%s — %s", key, value, last_msg)
        else:
            _LOGGER.warning("Config write rejected (no API msg): %s=%s", key, value)
        return False

    async def set_output_priority(self, priority: str) -> bool:
        """Set output source priority. '0'=USB(grid), '2'=SBU(solar/battery)."""
        return await self.set_config_item("outputSourcePrioritySetting", priority)

    async def set_charger_priority(self, priority: str) -> bool:
        """Set charger source priority."""
        return await self.set_config_item("chargerSourcePrioritySetting", priority)

    async def set_max_charging_current(self, amps: int) -> bool:
        """Set max total charging current in amps."""
        # Use the same key as the working Flutter client.
        return await self.set_config_item("setMaxChargingCurrent", str(amps))

    async def set_max_utility_charging_current(self, amps: int) -> bool:
        """Set max utility (grid) charging current in amps."""
        # Some inverters use different key names — try both
        for key in ("setUtilityMaxChargingCurrent", "maxUtilityChargingCurrent"):
            ok = await self.set_config_item(key, str(amps))
            if ok:
                return True
        return False

    async def fetch_device_configs(self) -> dict[str, Any]:
        """Fetch all device configuration settings from the inverter.

        Uses the async batch-read API:
          1. POST /apis/remote/device/configs/read  -> returns batchReadId
          2. GET  /apis/remote/device/configs/read/details?batchReadId=...
             poll until isFinished=True, then extract configAttributeStates
        """
        if not self.device_sn:
            return {}

        # Step 1: initiate batch read
        await self._apply_rate_limit(ENDPOINT_DEVICE_CONFIGS_READ)
        params = {"deviceId": self.device_sn}
        body = {"id": self.device_sn}
        headers = self._build_headers("POST", body)

        try:
            async with self._session.post(
                ENDPOINT_DEVICE_CONFIGS_READ,
                params=params,
                data=self._json_compact(body),
                headers=headers,
            ) as resp:
                data = await resp.json()
        except aiohttp.ClientError:
            _LOGGER.warning("Failed to initiate device config batch read")
            return {}

        code = data.get("code")
        if code == 70021:
            _LOGGER.debug("Device config batch read rate-limited, skipping")
            return {}
        if code != 0:
            _LOGGER.warning("Device config batch read failed: code=%s msg=%s",
                            code, data.get("message"))
            return {}

        batch_id = data.get("data", {}).get("id") or data.get("data", {}).get("deviceId")
        if not batch_id:
            _LOGGER.warning("No batchReadId in config read response")
            return {}

        # Step 2: poll details until finished (max 10 attempts, ~15s total)
        details_url = "/apis/remote/device/configs/read/details"
        for attempt in range(10):
            await asyncio.sleep(1.5)
            try:
                detail_headers = self._build_headers("GET", None)
                async with self._session.get(
                    details_url,
                    params={"batchReadId": batch_id},
                    headers=detail_headers,
                ) as resp:
                    detail_data = await resp.json()
            except aiohttp.ClientError:
                continue

            if detail_data.get("code") != 0:
                continue

            resp_payload = detail_data.get("data", {})
            if not isinstance(resp_payload, dict):
                continue

            if resp_payload.get("isFinished"):
                config_states = resp_payload.get("configAttributeStates", {})
                if isinstance(config_states, dict) and config_states:
                    parsed: dict[str, dict] = {}
                    for key, item in config_states.items():
                        if not isinstance(item, dict):
                            continue
                        parsed[key] = {
                            "value": item.get("value"),
                            "min": item.get("min"),
                            "max": item.get("max"),
                            "step": item.get("step"),
                            "unit": item.get("unit"),
                            "name": item.get("nameDisplay") or item.get("name") or key,
                            "valueDisplay": item.get("valueDisplay"),
                        }
                    _LOGGER.info("Device settings loaded: %d keys", len(parsed))
                    return parsed
                # isFinished but no states — empty config, still success
                return {}

        _LOGGER.warning("Device config batch read timed out after polling")
        return {}

    async def fetch_history(
        self, start: datetime, end: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Fetch historical data for a time range."""
        if not self.device_sn:
            return []

        await self._apply_rate_limit(ENDPOINT_HISTORY)
        body = {
            "deviceSn": self.device_sn,
            "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
            "endTime": (end or datetime.now(timezone.utc)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }
        headers = self._build_headers("POST", body)

        try:
            async with self._session.post(
                ENDPOINT_HISTORY, data=self._json_compact(body), headers=headers
            ) as resp:
                data = await resp.json()
        except aiohttp.ClientError:
            return []

        if data.get("code") == 0 and data.get("data"):
            return data["data"] if isinstance(data["data"], list) else []
        return []

    # ── Owner Overview: station-level history charts ───────────────

    @staticmethod
    def _overview_time_body(category: str) -> dict[str, str]:
        """Build the POST body for a given overview category.

        Time formats verified against live solar.siseli.com HAR capture:
          daily   → "2026-06-26"
          monthly → "2026-06"
          yearly  → "2026"
          total   → ISO 8601 with timezone, e.g. "2026-06-26T20:48:32+03:00"
        """
        now = datetime.now(timezone.utc)
        # Convert to UTC+3 (Europe/Kiev)
        kiev = timezone(timedelta(hours=3))
        local = now.astimezone(kiev)

        if category == "daily":
            return {"time": local.strftime("%Y-%m-%d")}
        if category == "monthly":
            return {"time": local.strftime("%Y-%m")}
        if category == "yearly":
            return {"time": local.strftime("%Y")}
        if category == "total":
            return {"time": local.strftime("%Y-%m-%dT%H:%M:%S+03:00")}
        # fallback
        return {"time": local.strftime("%Y-%m-%d")}

    async def _fetch_overview(self, category: str, summary_key: str, *, month=None, day=None, raw_properties=False) -> list[dict[str, Any]]:
        """Fetch owner overview data (POST with body).

        POST /apis/ownerOverView/station/stateAttributeSummary/category/{category}
            ?summaryCategoryKey={summary_key}
        Body: {"time": "<formatted time>"}
        """
        if not self.current_station_id:
            _LOGGER.warning("No station_id, cannot fetch overview")
            return []

        await self._apply_rate_limit(ENDPOINT_OVERVIEW_BASE)
        params = {"summaryCategoryKey": summary_key}
        url = f"{ENDPOINT_OVERVIEW_BASE}/{category}"
        body = self._overview_time_body(category)
        if day is not None:
            if category != "daily" or month is not None:
                raise ValueError("Historical power overview requires a single day")
            body = {"time": day.isoformat()}
        if month is not None:
            if category != "monthly" or month.day != 1:
                raise ValueError("Historical overview requires a calendar month")
            body = {"time": month.strftime("%Y-%m")}

        try:
            headers = self._build_headers("POST", body)
            async with self._session.post(
                url, params=params, data=self._json_compact(body), headers=headers,
            ) as resp:
                raw_text = await resp.text()
                data = json.loads(raw_text)
                # Log full response structure for debugging
                resp_data = data.get("data", {})
                if isinstance(resp_data, dict):
                    props = resp_data.get("properties", [])
                    _LOGGER.debug(
                        "Overview %s response: status=%d code=%d props_count=%d props_sample=%s",
                        category, resp.status, data.get("code", -1),
                        len(props) if isinstance(props, list) else 0,
                        json.dumps(props[:2], ensure_ascii=False) if isinstance(props, list) else str(type(props)),
                    )
                else:
                    _LOGGER.debug(
                        "Overview %s response: status=%d code=%d data_type=%s body=%s",
                        category, resp.status, data.get("code", -1),
                        type(resp_data).__name__, raw_text[:800],
                    )
        except aiohttp.ClientError as exc:
            _LOGGER.warning("Overview fetch failed (%s/%s): %s", category, summary_key, exc)
            return []
        except (ValueError, TypeError) as exc:
            _LOGGER.warning("Overview JSON parse failed (%s): %s", category, exc)
            return []

        if data.get("code") != 0:
            _LOGGER.warning(
                "Overview error (%s): code=%s msg=%s",
                category, data.get("code"), data.get("message"),
            )
            return []

        # API returns: data = { category: {...}, properties: [{property, timePoints}, ...], hasRealTimePoints }
        payload = data.get("data")
        if isinstance(payload, dict):
            properties = payload.get("properties", [])
            if raw_properties:
                return properties if isinstance(properties, list) else []
            if isinstance(properties, list) and properties:
                # Find the first property that has timePoints data
                for prop_group in properties:
                    time_points = prop_group.get("timePoints", [])
                    if isinstance(time_points, list) and time_points:
                        return time_points
            _LOGGER.debug(
                "Overview %s: no timePoints found in properties (count=%s)",
                category, len(properties) if isinstance(properties, list) else "?",
            )
            return []
        if isinstance(payload, list):
            return payload
        return []

    async def fetch_daily_power(self) -> list[dict[str, Any]]:
        """Fetch hourly PV power for today (Daily Power chart, kW)."""
        properties = await self._fetch_overview("daily", SUMMARY_KEY_POWER, raw_properties=True)
        for group in properties:
            prop = group.get("property", {})
            if prop.get("key") == "generationPower" and prop.get("unit") == "kW":
                return group.get("timePoints", [])
        return []

    async def fetch_hourly_pv_history_day(self, day, timezone_name):
        """Measured historical half-hour PV samples, aggregated by hour."""
        from zoneinfo import ZoneInfo
        from .hems.cloud_history import measured_pv_hours
        if self._account_device_count != 1:
            return []  # Owner overview cannot identify one of several devices.
        properties = await self._fetch_overview("daily", SUMMARY_KEY_POWER,
                                                day=day, raw_properties=True)
        return measured_pv_hours(properties, day, ZoneInfo(timezone_name))

    async def fetch_monthly_energy(self) -> list[dict[str, Any]]:
        """Fetch daily PV energy for current month (Monthly Energy chart, kWh)."""
        return await self._fetch_overview("monthly", SUMMARY_KEY_ENERGY)

    async def fetch_daily_pv_history(self, start, end) -> dict[str, float]:
        """Bounded historical station energy, only for a single-device account.

        The owner overview has no per-device selector. Do not attribute an
        account aggregate to one inverter when several devices are present.
        """
        from .hems.cloud_history import measured_pv_days
        if self._account_device_count != 1:
            _LOGGER.warning("Cloud PV history skipped: account is not unambiguously single-device")
            return {}
        if end < start or (end - start).days > 120:
            raise ValueError("Cloud PV history range must be at most 120 days")
        month = start.replace(day=1)
        facts = {}
        while month <= end:
            properties = await self._fetch_overview("monthly", SUMMARY_KEY_ENERGY,
                                                    month=month, raw_properties=True)
            facts.update(measured_pv_days(properties, start, end))
            month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        return facts

    async def fetch_yearly_energy(self) -> list[dict[str, Any]]:
        """Fetch monthly PV energy for current year (Yearly Energy chart, kWh)."""
        return await self._fetch_overview("yearly", SUMMARY_KEY_ENERGY)

    async def fetch_total_energy(self) -> dict[str, Any]:
        """Fetch total cumulative PV energy (Total Energy, kWh).

        Bug A fix: API returns a list of time-points (each representing the
        cumulative kWh at a snapshot in time). Consumers expect a single
        cumulative total. We use the LATEST point's value (not a sum — the
        API already gives cumulative readings, summing them would multiply
        the true total by however many snapshots exist).

        If the snapshot list is empty, returns ``{}``. If the latest point
        lacks a numeric value, falls back to scanning for one before giving
        up — the cloud occasionally returns the value in an unexpected key.

        The returned dict carries a ``_raw_value`` field that is
        ``None`` whenever the underlying data was missing or
        non-numeric (the fallback path was used to surface a 0.0
        total). Downstream consumers (``HistoryCoordinator.
        _safe_total``) treat this as the canonical signal that
        the value is a placeholder and refuse to overwrite the
        cached total. This avoids the failure mode where a
        transient backend error returns ``{"value": 0, …}`` and
        the cache zeroing writes 0 over a real 1500 kWh reading.
        """
        result = await self._fetch_overview("total", SUMMARY_KEY_ENERGY)
        if not result:
            return {"_raw_value": None}
        latest = result[-1]  # most recent cumulative reading
        if not isinstance(latest, dict):
            return {"_raw_value": None}
        # T14 follow-up: prefer ``value`` but fall back to
        # ``totalEnergy`` *only* when ``value`` is genuinely
        # missing. The previous ``v = latest.get("value") or
        # latest.get("totalEnergy")`` short-circuit silently
        # ignored a real ``value=0`` (Python's ``or`` treats 0
        # as falsy) and the pair check downstream therefore
        # could not distinguish a real zero from a missing
        # number. The explicit ``is not None`` comparison
        # keeps both fields meaningful.
        v = latest.get("value")
        if v is None:
            v = latest.get("totalEnergy")
        if v is None:
            # Fallback: scan all points for a numeric value
            for point in result:
                if isinstance(point, dict):
                    p_v = point.get("value")
                    if p_v is None:
                        p_v = point.get("totalEnergy")
                    if p_v is not None:
                        v = p_v
                        break
        # T14 follow-up: if ``v`` is not numeric (e.g. a string
        # like ``"bad"`` or a dict the cloud accidentally put in
        # a numeric field), the parse below would silently
        # collapse it to 0.0 while still returning the
        # non-numeric ``v`` as ``_raw_value``. The previous
        # version of the body then surfaced ``{"value": 0,
        # "totalEnergy": 0, "_raw_value": "bad"}`` — a
        # placeholder that the coordinator's ``_safe_total``
        # would accept as a real reading because
        # ``_raw_value is not None``. We now stamp
        # ``_raw_value = None`` whenever the parse fails, so
        # the coordinator's cache gate fires.
        total_energy_raw: float | None = None
        # ``pair_valid`` is True iff the cloud returned both a
        # numeric ``value`` AND a numeric ``totalEnergy`` (or
        # the ``value`` itself is sufficient on its own — see
        # below). When ``value`` parses but ``totalEnergy`` is
        # missing or non-numeric, the pair cannot be confirmed
        # and ``_safe_total`` must fall back.
        pair_valid = True
        if v is None:
            total = 0.0
            raw_value = None
        else:
            try:
                total = float(v)
                raw_value = v
            except (TypeError, ValueError):
                total = 0.0
                raw_value = None
                total_energy_raw = None
        # Preserve the cloud's ``totalEnergy`` value as well so
        # ``_safe_total`` can detect a disagreement. The
        # previous version of the body always stamped the
        # same ``total`` into both fields, so a payload like
        # ``{"value": 0, "totalEnergy": 12.5}`` was collapsed
        # to ``{"value": 0, "totalEnergy": 0}`` and the pair
        # check (which guards against a half-stale reading)
        # could not fire. The fix:
        #   * when ``value`` was extractable and parseable,
        #     forward the cloud's ``totalEnergy`` if present
        #     and numeric, otherwise reuse ``total``.
        #   * when ``value`` was missing or non-numeric, mark
        #     ``totalEnergy`` as ``None`` so the pair check
        #     sees a disagreement and falls back.
        if total_energy_raw is None and v is not None:
            # Try to read the cloud's totalEnergy so the pair
            # can be compared. If it's missing or non-numeric,
            # the pair is incomplete — surface a ``None`` so
            # ``_safe_total`` can reject it as a malformed
            # payload rather than accept a 0.0 placeholder
            # that happens to agree with the real ``value=0``
            # and overwrites a non-zero cache.
            e_field = latest.get("totalEnergy")
            if e_field is None:
                # Field is genuinely missing; the pair cannot
                # be confirmed. Mark the pair invalid.
                pair_valid = False
            else:
                try:
                    total_energy_raw = float(e_field)
                except (TypeError, ValueError):
                    # Field is present but non-numeric. Do
                    # *not* mask it with a 0.0 placeholder —
                    # the previous body did that and the
                    # ``_safe_total`` pair check then
                    # accepted ``{"value": 0,
                    # "totalEnergy": 0, "_raw_value": 0}``
                    # as a real reading, overwriting a
                    # populated cache. Mark the pair
                    # invalid so the coordinator's cache
                    # gate fires.
                    total_energy_raw = None
                    pair_valid = False
        # ``_raw_value`` is the sentinel: it is None exactly when
        # the API did not surface a real number. A non-None
        # value means the cloud gave us a usable reading,
        # regardless of whether the resulting ``total`` is 0.0
        # (which is itself a valid cumulative reading for a
        # freshly-installed inverter).
        # ``_pair_valid`` is False when the cloud's
        # ``totalEnergy`` is missing or non-numeric and the
        # ``value`` field alone is insufficient. ``_safe_total``
        # treats a False pair as a fallback even when the
        # numeric ``value`` parses cleanly. The dict still
        # surfaces both keys (the helper's contract requires
        # them) but the ``totalEnergy`` field is left as
        # ``None`` rather than masked to 0.0.
        if pair_valid:
            te_value = total_energy_raw if total_energy_raw is not None else total
        else:
            # Incomplete pair — leave ``totalEnergy`` as None
            # so the helper's ``v is None or e is None``
            # branch fires (it does not depend on numeric
            # equality). The numeric ``value`` is preserved
            # in the dict for diagnostic logging.
            te_value = None
        return {
            "value": total,
            "totalEnergy": te_value,
            "_raw_value": raw_value,
            "_pair_valid": pair_valid,
        }

    # ── Helpers ────────────────────────────────────────────────────────

    def _update_co2(self) -> None:
        """Update CO2 reduction estimate."""
        self.co2_reduction = (
            self.daily_energy + self.total_energy
        ) * CARBON_EMISSION_FACTOR

    @staticmethod
    def _parse_double(value: Any, default: float = 0.0) -> float:
        """Safely parse a numeric value (mirrors _parseDouble from Dart).

        T03: rejects NaN / +/-Infinity and silently returns ``default``.
        A non-finite number is never a valid measurement; propagating it
        would skew averages, fault checks, and the keepalive math.
        """
        if value is None:
            return default
        try:
            parsed = float(value)
        except (ValueError, TypeError):
            return default
        if not math.isfinite(parsed):
            return default
        return parsed
