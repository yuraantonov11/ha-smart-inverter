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

    def __init__(self, email: str, password: str) -> None:
        self._email = email
        self._password = password
        self._session: aiohttp.ClientSession | None = None

        # Auth state
        self.access_token: str | None = None
        self.user_id: str | None = None
        self.device_sn: str | None = None
        self.current_station_id: str | None = None
        self.current_mode: int | None = None

        # Energy stats
        self.daily_energy: float = 0.0
        self.total_energy: float = 0.0
        self.co2_reduction: float = 0.0

        # Rate limiting
        self._last_request_time: dict[str, float] = {}

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

    async def authenticate(self) -> bool:
        """Login to solar.siseli.com and fetch device list."""
        await self._ensure_session()
        await self._apply_rate_limit(ENDPOINT_LOGIN)

        # MD5 password (already MD5 if 32 hex chars)
        if len(self._password) == 32:
            password_md5 = self._password.lower()
        else:
            password_md5 = hashlib.md5(self._password.encode()).hexdigest()

        body = {"account": self._email, "password": password_md5}
        headers = self._build_headers("POST", body)

        try:
            compact_body = self._json_compact(body)
            async with self._session.post(
                ENDPOINT_LOGIN, data=compact_body, headers=headers
            ) as resp:
                status = resp.status
                raw_text = await resp.text()
                _LOGGER.debug("Login response status=%d body=%s", status, raw_text[:500])
                try:
                    data = json.loads(raw_text)
                except (ValueError, TypeError) as json_err:
                    _LOGGER.error(
                        "Login JSON parse error: %s. Raw: %s",
                        json_err, raw_text[:300]
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
            _LOGGER.error(
                "Login failed: code=%s msg=%s data=%s",
                code, msg, str(data)[:300]
            )
            raise InverterAuthError(msg)

        resp_data = data.get("data", {})
        self.access_token = resp_data.get("accessToken") or resp_data.get("token")
        self.user_id = str(resp_data.get("userId", ""))

        await self._fetch_device_list()
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

    async def _fetch_device_list(self) -> None:
        """Fetch device list and populate device_sn / station_id."""
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
            if devices:
                dev = devices[0]
                self.device_sn = str(dev.get("id", ""))
                self.current_station_id = str(dev.get("stationId", ""))
                self.daily_energy = self._parse_double(
                    dev.get("dailyProducedQuantity")
                )
                self.total_energy = self._parse_double(
                    dev.get("totalProducedQuantity")
                )
                self._update_co2()
                _LOGGER.info(
                    "Device found: SN=%s station=%s",
                    self.device_sn,
                    self.current_station_id,
                )
            else:
                raise InverterApiError("No devices found for this account")
        else:
            raise InverterApiError(
                data.get("msg", "Failed to fetch device list")
            )

    async def fetch_realtime_data(self) -> dict[str, Any] | None:
        """Fetch real-time inverter data mirroring Dart getRealTimeData."""
        if not self.device_sn:
            _LOGGER.warning("No device selected, trying to re-fetch")
            await self._fetch_device_list()
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
        if code == 9 or (isinstance(message, str) and "token" in message.lower() and "expired" in message.lower()):
            raise TokenExpiredError(f"Token expired on {endpoint}")

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
        inverter_temp = _val("radiatorTemperature") or _val("invTemperature") or _val("temperature")
        pv_input_voltage = _val("pvVoltage") or _val("solarVoltage") or _val("pvInputVoltage")

        return {
            "pvPower": pv_power,
            "gridPower": grid_power,
            "batteryPower": battery_power,
            "loadPower": load_power,
            "batterySoc": _val("batterySoc", 100.0) or _val("batteryCapacity", 100.0),
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
                    await asyncio.sleep(0.8)
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

    async def _fetch_overview(self, category: str, summary_key: str) -> list[dict[str, Any]]:
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
        return await self._fetch_overview("daily", SUMMARY_KEY_POWER)

    async def fetch_monthly_energy(self) -> list[dict[str, Any]]:
        """Fetch daily PV energy for current month (Monthly Energy chart, kWh)."""
        return await self._fetch_overview("monthly", SUMMARY_KEY_ENERGY)

    async def fetch_yearly_energy(self) -> list[dict[str, Any]]:
        """Fetch monthly PV energy for current year (Yearly Energy chart, kWh)."""
        return await self._fetch_overview("yearly", SUMMARY_KEY_ENERGY)

    async def fetch_total_energy(self) -> dict[str, Any]:
        """Fetch total cumulative PV energy (Total Energy, kWh)."""
        result = await self._fetch_overview("total", SUMMARY_KEY_ENERGY)
        return result[0] if result else {}

    # ── Helpers ────────────────────────────────────────────────────────

    def _update_co2(self) -> None:
        """Update CO2 reduction estimate."""
        self.co2_reduction = (
            self.daily_energy + self.total_energy
        ) * CARBON_EMISSION_FACTOR

    @staticmethod
    def _parse_double(value: Any, default: float = 0.0) -> float:
        """Safely parse a numeric value (mirrors _parseDouble from Dart)."""
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default
