"""Opt-in, diagnostic-only RWB1 BLE reader over Home Assistant's Bluetooth API.

Scope, stated once so nothing downstream has to guess:

* **Diagnostic only.** Everything this module produces is published as
  ``EntityCategory.DIAGNOSTIC`` sensors. No BLE value is returned to the
  coordinator, the HEMS engine, the outage planner, SOC selection or any
  automation. Cloud remains the primary and only *selected* source; the field
  policy in :mod:`.telemetry_sources.ble_policies` is cloud-only for every
  field and this module does not change it.
* **Read only.** The one and only Modbus frame this module can put on the wire
  is the allow-listed ``05 03 11 95 00 15 90 91`` read. The allow-list lives in
  :mod:`.telemetry_sources.rwb1_ble_profile` and is enforced by the codec, not
  by this file. No write, no configuration, no pairing, no register sweep, no
  control command exists anywhere in this path.
* **Rate limited and serialised.** The minimum interval is
  :data:`BLE_MIN_INTERVAL_SECONDS` (5 s) and cannot be configured below it.
  A single :class:`asyncio.Lock` serialises sessions, so two BLE sessions can
  never overlap.
* **Fail closed.** Any error stops the polling for that cycle, is recorded with
  its exact reason, and leaves cloud untouched.

Activation is opt-in and defaults to off. Two independent levers, either is
sufficient:

* config-entry option ``ble_diagnostic`` = ``True``; or
* the presence of the marker file ``<config>/powmr_ble_diagnostic.enabled``.

The marker file is the deployment lever used by the operator. It is read when
the runner starts, so removing it takes effect on the next reload of the
config entry -- not instantly.
"""

from __future__ import annotations

import asyncio
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DOMAIN = "powmr_inverter"

#: Hard safety floor between two BLE reads. The operational interval is the
#: operator's ``interval`` setting; this only stops an accidental 1-second
#: hammer on a device that also has to serve the vendor phone app. It is not the
#: requested rate -- 300 s was an earlier requirement, later relaxed.
BLE_MIN_INTERVAL_SECONDS = 5.0

#: Config-entry option keys (all optional, all default off/empty).
OPT_ENABLED = "ble_diagnostic"
OPT_DTU_ID = "ble_dtu_id"
OPT_ADDRESS = "ble_device_address"
OPT_INTERVAL = "ble_read_interval"
OPT_RESPONSE_TIMEOUT = "response_timeout"

#: Marker file that enables polling without touching the options flow.
MARKER_FILENAME = "powmr_ble_diagnostic.enabled"
#: Optional key=value file for the opt-in and the DTU id.
CONF_FILENAME = "powmr_ble_diagnostic.conf"

DEFAULT_ADDRESS = "D4:27:87:E4:F9:4F"

#: Fields published as individual diagnostic sensors, in display order.
PUBLISHED_FIELDS: tuple[tuple[str, str, str], ...] = (
    # (field name, unit, device_class-ish icon)
    ("grid_voltage", "V", "mdi:flash"),
    ("battery_voltage", "V", "mdi:battery"),
    ("battery_soc", "%", "mdi:battery"),
    ("pv_power", "W", "mdi:solar-power"),
    ("load_power", "W", "mdi:home-import-outline"),
)

STATUS_DISABLED = "disabled"
STATUS_IDLE = "idle"
STATUS_RUNNING = "reading"
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_NO_DTU = "dtu_id_not_configured"
STATUS_DEVICE_UNKNOWN = "device_not_known_to_home_assistant"
#: A cycle the scheduler deliberately skipped: the provider's own minimum
#: interval had not elapsed. Not a device failure, not an error.
STATUS_SKIPPED = "skipped_local_rate_limit"
STATUS_NO_CLIENT = "ble_client_class_unavailable"

#: Failure kinds that mean the LINK is gone. Only these discard the provider so
#: the next cycle rebuilds the session. A "no complete response" failure does
#: not: the connection is demonstrably alive, so throwing it away would cause
#: exactly the connect/disconnect churn this change exists to remove.
CONNECTION_LEVEL_ERRORS = frozenset({
    "BleakError",
    "TimeoutError",
    "device_not_known_to_home_assistant",
    "ble_client_class_unavailable",
    "Rwb1BleTransportError",
})


#: Name of the "skipped locally" exception, kept only so the diagnostics can
#: report it without importing the provider module.
RATE_LIMITED_ERROR = "Rwb1BleRateLimited"


def is_rate_limited(err: BaseException) -> bool:
    """True for a locally skipped cycle, False for anything the device did.

    A plain ``isinstance`` on the class the runner itself imported. There is
    deliberately no class-name fallback: within the integration the relative
    import resolves to the very module the provider raises from, so identity
    holds. A name comparison once hid a genuinely broken import behind a
    passing test.
    """
    from .telemetry_sources.rwb1_ble import Rwb1BleRateLimited

    return isinstance(err, Rwb1BleRateLimited)


def is_connection_level(kind: str | None) -> bool:
    """True when ``kind`` means the BLE session must be rebuilt."""
    if kind is None:
        return False
    return kind in CONNECTION_LEVEL_ERRORS or kind.startswith("Bleak")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ble_conf_path(hass: HomeAssistant) -> Path:
    """Absolute path of the optional BLE configuration file."""
    return Path(hass.config.config_dir) / CONF_FILENAME


def read_ble_conf(hass: HomeAssistant) -> dict[str, str]:
    """Parse the operator's key=value file. Missing file is not an error."""
    out: dict[str, str] = {}
    try:
        text = ble_conf_path(hass).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip().lower()] = value.strip()
    return out


def ble_marker_path(hass: HomeAssistant) -> Path:
    """Absolute path of the opt-in marker file."""
    return Path(hass.config.config_dir) / MARKER_FILENAME


def ble_is_enabled(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """True when the operator opted in, via either lever. Default is off."""
    if entry.options.get(OPT_ENABLED) is True:
        return True
    if entry.data.get(OPT_ENABLED) is True:
        return True
    try:
        return ble_marker_path(hass).exists()
    except OSError:  # pragma: no cover - unreadable config dir
        return False


#: Response budget for one BLE transaction, in seconds.
DEFAULT_RESPONSE_TIMEOUT_SECONDS = 5.0
RESPONSE_TIMEOUT_MIN = 3.0
RESPONSE_TIMEOUT_MAX = 20.0


def ble_response_timeout(
    entry: ConfigEntry, default: str | None = None
) -> float:
    """Response budget, operator-tunable and clamped to a sane band.

    The 5 s value was inherited from the per-reconnect regime. With a
    persistent session the observed successful read is ~1 s while every failure
    aborts at exactly the 5 s mark, so the budget is a tuning knob rather than
    a hardcoded constant.

    Reads the ConfigEntry option first, then the operator's key=value file, so
    the conf lever behaves the same way ``interval`` does.
    """
    raw = entry_option(entry, OPT_RESPONSE_TIMEOUT)
    if raw in (None, "") and default is not None:
        raw = default
    if raw in (None, ""):
        raw = DEFAULT_RESPONSE_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_RESPONSE_TIMEOUT_SECONDS
    if value != value:  # NaN never compares equal to itself
        value = DEFAULT_RESPONSE_TIMEOUT_SECONDS
    return min(RESPONSE_TIMEOUT_MAX, max(RESPONSE_TIMEOUT_MIN, value))


def ble_configured_interval(
    entry: ConfigEntry, default: float | None = None
) -> float:
    """Requested interval, clamped to the safety floor. Never below it."""
    floor = BLE_MIN_INTERVAL_SECONDS
    raw = entry_option(entry, OPT_INTERVAL)
    if raw in (None, "") and default is not None:
        raw = default
    try:
        requested = float(raw) if raw not in (None, "") else floor
    except (TypeError, ValueError):
        return floor
    return max(requested, floor)


def entry_option(entry: ConfigEntry, key: str, default: Any = None) -> Any:
    """Read one option, falling back to the entry data then to ``default``."""
    if key in entry.options:
        return entry.options[key]
    if key in entry.data:
        return entry.data[key]
    return default


def _resolve_client_class() -> type | None:
    """Return a bleak client class with a service cache, or ``None``.

    Home Assistant ships its own in-tree client first, so this adds no runtime
    dependency and leaves ``manifest.json`` untouched. The standalone
    ``bleak-retry-connector`` package is accepted as a fallback for a host that
    already has it. Plain ``bleak.BleakClient`` is deliberately *not* accepted:
    it has no service cache, and without the cache a network-attached proxy
    adapter re-reads the service table on every operation.
    """
    try:
        from homeassistant.components.bluetooth.client import (  # type: ignore[import-not-found]
            BleakClientWithServiceCache,
        )

        return BleakClientWithServiceCache
    except Exception:  # noqa: BLE001 - any import failure falls through
        pass
    try:
        from bleak_retry_connector import (  # type: ignore[import-not-found]
            BleakClientWithServiceCache,
        )

        return BleakClientWithServiceCache
    except Exception:  # noqa: BLE001
        return None


class BleDiagnosticRunner:
    """Owns the BLE session lifecycle and the last observation."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        conf = read_ble_conf(hass)
        self.address = str(
            entry_option(entry, OPT_ADDRESS) or conf.get("address") or DEFAULT_ADDRESS
        )
        self.dtu_id = (
            str(entry_option(entry, OPT_DTU_ID) or "").strip()
            or (conf.get("dtu_id") or "").strip()
            or None
        )
        try:
            requested_interval = float(conf.get("interval") or 0.0)
        except ValueError:
            requested_interval = 0.0
        self.interval = ble_configured_interval(
            entry, default=requested_interval or None
        )
        self.response_timeout = ble_response_timeout(
            entry, conf.get("response_timeout")
        )
        # Seams for tests: the clock the wait is measured against and the
        # sleep itself. Production uses the provider's monotonic clock.
        self._clock = time.monotonic
        self._sleeper = asyncio.sleep
        self.enabled = ble_is_enabled(hass, entry) or conf.get("enabled") in (
            "1", "true", "yes", "on"
        )

        self._provider: Any = None
        # A dropped session that has not yet been replaced. A reconnect is
        # counted when a *new* session takes the place of a dropped one --
        # not when a provider object is created, so a session dropped during
        # shutdown is never counted as a reconnect.
        self._dropped_sessions = 0
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        # Diagnostic entities attach here and are pushed a state write at
        # the end of every cycle. They have no coordinator, so without this
        # they would be written once at setup and never again.
        self._entities: list[Any] = []

        # When the counting started. Every counter below is relative to this
        # moment, so a restart is visible instead of silently resetting the
        # numbers. Set once, here -- a duplicate key later in this dict would
        # silently override it.
        counters_started_at = _utcnow().isoformat()

        self.state: dict[str, Any] = {
            "enabled": self.enabled,
            "status": STATUS_DISABLED if not self.enabled else STATUS_IDLE,
            "address": self.address,
            "dtu_id_configured": self.dtu_id is not None,
            "interval_s": self.interval,
            "cycle": 0,
            "counters_started_at": counters_started_at,
            "response_timeout_s": None,
            "interval_change": None,
            "skipped_cycles": 0,
            # Cumulative counters. ``last_error`` alone cannot be read as
            # "no errors ever": it only shows the most recent attempt.
            "reads_ok": 0,
            "reads_failed": 0,
            "reconnects": 0,
            "sessions_dropped": 0,
            "rescheduled_delay_s": None,
            "last_disconnect": None,
            "session_built": 0,
            "session_reused": 0,
            "seconds_since_previous_success": None,
            "last_attempt": None,
            "last_success": None,
            "last_error": None,
            "error_kind": None,
            "device_name": None,
            "device_known": None,
            "proxy": None,
            "client_class": None,
            "values": {},
            "received_at": None,
            "profile_id": None,
        }

    def attach(self, entity: Any) -> None:
        """Register a diagnostic entity for push updates."""
        if entity not in self._entities:
            self._entities.append(entity)

    def detach(self, entity: Any) -> None:
        try:
            self._entities.remove(entity)
        except ValueError:
            pass

    async def _push(self) -> None:
        """Write the state of every attached diagnostic entity."""
        for entity in list(self._entities):
            try:
                entity.async_write_ha_state()
            except Exception:  # noqa: BLE001 - one bad entity must not stop the rest
                _LOGGER.debug("BLE diagnostic entity state write failed",
                              exc_info=True)

    # ------------------------------------------------------------------ proxy
    def _proxy_info(self) -> dict[str, Any]:
        """Report which adapter HA would use, read-only, no connection.

        ``async_get_adapters`` is not available in every HA release, so the
        authoritative answer is the ``source`` carried in the ``BLEDevice``
        details, which names the adapter that actually saw the device. The
        adapter list is a best-effort extra.
        """
        info: dict[str, Any] = {"integration": "home_assistant.bluetooth"}
        adapters: list[str] = []
        try:
            from homeassistant.components import bluetooth

            for call in (
                lambda: bluetooth.async_get_adapters(hass=self.hass),
                lambda: bluetooth.async_get_adapters(self.hass),
            ):
                try:
                    found = call()
                except Exception:  # noqa: BLE001 - try the other signature
                    continue
                if found:
                    adapters = sorted(
                        str(getattr(a, "address", a)) for a in found
                    )
                break
        except Exception:  # noqa: BLE001 - bluetooth not importable
            adapters = []
        info["adapters"] = adapters
        info["adapter_count"] = len(adapters)
        return info

    def _resolve_device(self) -> Any:
        """Resolve the RWB1 through HA's Bluetooth API (no radio action here)."""
        from homeassistant.components import bluetooth

        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        self.state["device_known"] = device is not None
        if device is not None:
            self.state["device_name"] = getattr(device, "name", None)
            self.state["rssi"] = getattr(device, "rssi", None)
            details = getattr(device, "details", None)
            if isinstance(details, dict):
                source = details.get("source")
                if source:
                    # The adapter that actually saw the device: this is the
                    # gateway proxy when one is in the path.
                    self.state["proxy"]["source"] = source
                    self.state["adapter_for_device"] = source
        return device

    # ------------------------------------------------------------------- read
    async def read_once(self) -> dict[str, Any]:
        """One read cycle. Never raises; records the exact reason on failure."""
        if self._lock.locked():  # pragma: no cover - defensive, serialised above
            self.state["status"] = STATUS_ERROR
            self.state["error_kind"] = "session_already_running"
            self.state["last_error"] = "another BLE session is already in flight"
            return self.state

        async with self._lock:
            try:
                return await self._read_locked()
            finally:
                await self._push()

    async def _read_locked(self) -> dict[str, Any]:
        await self._reload_operational_settings()
        self.state["cycle"] = int(self.state["cycle"]) + 1
        self.state["last_attempt"] = _utcnow().isoformat()
        self.state["last_error"] = None
        self.state["error_kind"] = None
        self.state["proxy"] = self._proxy_info()
        self.state.setdefault("adapter_for_device", None)

        client_class = _resolve_client_class()
        self.state["client_class"] = getattr(client_class, "__name__", None)
        if client_class is None:
            return self._fail(STATUS_NO_CLIENT,
                              "no bleak client with a service cache is available "
                              "(neither homeassistant.components.bluetooth.client "
                              "nor bleak_retry_connector could be imported)")

        if not self.dtu_id:
            return self._fail(
                STATUS_NO_DTU,
                "the RWB1 DTU identifier is not configured, so the read frame "
                "cannot be encrypted; set the ble_dtu_id option",
            )

        provider: Any = self._provider
        try:
            if provider is None:
                device = await asyncio.get_running_loop().run_in_executor(
                    None, self._resolve_device
                )
                if device is None:
                    return self._fail(
                        STATUS_DEVICE_UNKNOWN,
                        f"{self.address} is not known to Home Assistant's bluetooth "
                        "integration; let it discover the device first",
                    )
                provider = self._build_provider(device, client_class)
                self._provider = provider
                built = int(self.state.get("session_built") or 0) + 1
                self.state["session_built"] = built
                dropped = int(getattr(self, "_dropped_sessions", 0) or 0)
                if dropped > 0:
                    # A live session was lost and this one replaces it: that
                    # is a reconnect, counted on the replacement, not on the
                    # creation of the object.
                    self._dropped_sessions = dropped - 1
                    self.state["reconnects"] = int(
                        self.state.get("reconnects") or 0
                    ) + 1
                self.state["sessions_dropped"] = int(
                    self.state.get("sessions_dropped") or 0
                )
                self.state["session_reused"] = 0
            else:
                self.state["session_reused"] = int(
                    self.state.get("session_reused") or 0
                ) + 1

            frame = await provider.read()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - the reason is the deliverable
            if is_rate_limited(err):
                # A local scheduling skip. Nothing was sent, nothing was awaited
                # and the session is healthy, so this must NOT count as a device
                # error and must NOT start recovery.
                _LOGGER.debug(
                    "RWB1 BLE read skipped by the local rate limit: %s", err
                )
                return self._skip(str(err))
            kind = type(err).__name__

            if is_connection_level(kind):
                # The link is gone: drop the session so the next cycle rebuilds
                # instead of reusing a dead one.
                await self._drop_session()
            return self._fail(kind, f"{kind}: {err}")

        values: dict[str, float] = {}
        for name, _unit, _icon in PUBLISHED_FIELDS:
            obs = frame.fields.get(name)
            values[name] = None if obs is None else obs.value

        self.state["values"] = values
        self.state["received_at"] = (
            frame.received_at.isoformat() if frame.received_at else None
        )
        self.state["profile_id"] = getattr(frame, "profile_id", None)
        now = _utcnow()
        # Publish both operational settings on every success. They used to be
        # written only on change, so a fresh runner reported interval_s = None
        # until the first edit.
        self.state["interval_s"] = self.interval
        self.state["response_timeout_s"] = self.response_timeout
        previous = self.state.get("last_success")
        if previous:
            try:
                gap = (now - datetime.fromisoformat(previous)).total_seconds()
                self.state["seconds_since_previous_success"] = round(gap, 1)
            except (TypeError, ValueError):
                self.state["seconds_since_previous_success"] = None
        self.state["last_success"] = now.isoformat()
        self.state["reads_ok"] = int(self.state.get("reads_ok") or 0) + 1
        self.state["status"] = STATUS_OK
        return self.state

    def _build_provider(self, device: Any, client_class: type) -> Any:
        """Create the long-lived provider for this session."""
        from .telemetry_sources.models import SourceIdentity, SourceKind
        from .telemetry_sources.rwb1_ble import Rwb1BleTelemetryProvider

        connect_timeout = float(
            entry_option(self.entry, "ble_connect_timeout", 20.0)
        )
        # A callable factory (no ``connect`` attribute) is what the provider
        # treats as a factory, and it is resolved lazily, so the bleak client is
        # only constructed when a session is actually opened.
        return Rwb1BleTelemetryProvider(
            identity=SourceIdentity(
                provider_id="rwb1_ble",
                device_id=f"{DOMAIN}_rwb1_ble",
                kind=SourceKind.BLE,
            ),
            dtu_id=self.dtu_id,
            ble_device=device,
            bleakenector=lambda: client_class(device, timeout=connect_timeout),
            min_interval=self.interval,
            settle_seconds=0.0,
            response_timeout=self.response_timeout,
        )

    async def _reload_operational_settings(self) -> None:
        """Pick up interval / response-timeout changes without a restart.

        The operator's key=value file used to be read once, in ``__init__``,
        which meant every interval change cost a full HA restart -- and a
        restart is exactly what leaves a BLE session stranded on a network
        proxy. Re-reading the two operational keys per cycle is a tiny file
        read and removes the restart from the tuning loop entirely.
        A settings reload must never be able to take down a read cycle, so any
        failure to read the file is swallowed and the running values stand.
        """
        try:
            conf = read_ble_conf(self.hass)
        except Exception:  # noqa: BLE001 - reload is best effort by design
            _LOGGER.debug("BLE settings reload skipped", exc_info=True)
            return
        try:
            interval = ble_configured_interval(
                self.entry, default=float(conf.get("interval") or 0.0) or None
            )
        except (TypeError, ValueError):
            return
        if interval != self.interval:
            self.state["interval_change"] = {
                "from": self.interval,
                "to": interval,
                "at": _utcnow().isoformat(),
            }
            self.interval = interval
            self.state["interval_s"] = interval
            # The provider enforces its own floor; a narrower interval needs a
            # matching provider, otherwise the next cycle raises ValueError.
            provider = self._provider
            if provider is not None:
                await self._reschedule(provider, interval)
        budget = ble_response_timeout(self.entry, conf.get("response_timeout"))
        if budget != self.response_timeout:
            self.response_timeout = budget
            provider = self._provider
            if provider is not None:
                provider._response_timeout = budget

    async def _sleep_until(self, due: float) -> None:
        """Wait until ``due`` on the provider's clock, interruptibly.

        Split out so the wait is one seam: production uses the real event loop,
        tests drive a fake clock and return instantly.
        """
        while not self._stop.is_set():
            remaining = due - self._clock()
            if remaining <= 0:
                return
            await self._sleeper(min(remaining, 0.5))

    async def _reschedule(self, provider: Any, interval: float) -> None:
        """Apply a new interval to a live session and wait out the old attempt.

        The time of the real last attempt is preserved. After a change the
        next read is allowed at ``last_attempt + interval``; if that moment has
        not arrived yet the cycle simply waits for it. Forgetting the stamp
        instead would let a read through early, and raising the provider floor
        on its own made it reject the next read -- one wasted cycle and a
        phantom error per change.

        The wait stays interruptible so stopping the runner does not have to
        wait out a long backoff.
        """
        provider._min_interval = interval
        last = getattr(provider, "_last_attempt_at", None)
        if last is None:
            return
        clock = getattr(provider, "_clock", None) or time.monotonic
        delay = (last + interval) - clock()
        self.state["rescheduled_delay_s"] = round(max(delay, 0.0), 3)
        if delay <= 0:
            return
        await self._sleep_until(clock() + delay)

    async def _drop_session(self) -> None:
        """Close and forget the session; the next cycle rebuilds it.

        The disconnect outcome is logged at INFO, not debug: a session that is
        stranded on a network proxy is invisible from HA's state afterwards, and
        the operator needs the "did the BLE client actually disconnect?" answer
        to be readable after a restart or a shutdown.
        """
        provider, self._provider = self._provider, None
        if provider is None:
            return
        self._dropped_sessions = int(getattr(self, "_dropped_sessions", 0) or 0) + 1
        self.state["sessions_dropped"] = int(
            self.state.get("sessions_dropped") or 0
        ) + 1
        started = _utcnow()
        try:
            await provider.close()
        except Exception:  # noqa: BLE001 - teardown is best effort
            _LOGGER.warning(
                "RWB1 BLE session close raised; the proxy slot may be stranded",
                exc_info=True,
            )
            self.state["last_disconnect"] = {
                "ok": False,
                "reason": "close raised",
                "at": _utcnow().isoformat(),
            }
            return
        # The provider reports an incomplete teardown when a backend step did
        # not answer inside its deadline; that is the interesting case.
        incomplete = bool(getattr(provider, "teardown_incomplete", False))
        elapsed = (_utcnow() - started).total_seconds()
        self.state["last_disconnect"] = {
            "ok": not incomplete,
            "teardown_incomplete": incomplete,
            "seconds": round(elapsed, 3),
            "at": _utcnow().isoformat(),
        }
        _LOGGER.info(
            "RWB1 BLE session disconnected: ok=%s teardown_incomplete=%s in %.3fs",
            not incomplete,
            incomplete,
            elapsed,
        )

    def _skip(self, reason: str) -> dict[str, Any]:
        """Record a locally skipped cycle without touching the last error."""
        self.state["status"] = STATUS_SKIPPED
        self.state["skipped_cycles"] = int(self.state.get("skipped_cycles") or 0) + 1
        # Deliberately leaves last_error / error_kind alone: the previous real
        # error is still the last real error.
        return self.state

    def _fail(self, kind: str, message: str) -> dict[str, Any]:
        # One place to count a failed cycle, so the device failures and the
        # configuration failures are counted exactly once each.
        self.state["reads_failed"] = int(self.state.get("reads_failed") or 0) + 1
        self.state["status"] = STATUS_ERROR if kind == "error" else kind
        if self.state["status"] == STATUS_ERROR:
            self.state["status"] = STATUS_ERROR
        self.state["error_kind"] = kind
        self.state["last_error"] = message
        _LOGGER.warning("RWB1 BLE diagnostic read did not complete: %s", message)
        return self.state

    # ------------------------------------------------------------------- loop
    async def async_start(self) -> None:
        await self._push()
        if not self.enabled:
            _LOGGER.info(
                "RWB1 BLE diagnostics are OFF (opt-in). Cloud remains the only "
                "selected telemetry source."
            )
            return
        self._stop.clear()
        self._task = asyncio.get_running_loop().create_task(self._loop())
        _LOGGER.info(
            "RWB1 BLE diagnostics started: device=%s, interval=%ss, read-only, "
            "diagnostic entities only",
            self.address,
            self.interval,
        )

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.state["status"] = STATUS_RUNNING
                # Exactly one request in flight: read_once holds self._lock, and
                # the provider holds its own lock around the transaction.
                await self.read_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive anything
                _LOGGER.exception("RWB1 BLE diagnostic cycle crashed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.interval
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise

    async def async_stop(self) -> None:
        """Stop the loop and release the BLE session.

        The disconnect has to reach the backend even when Home Assistant
        cancels the shutdown: a proxy slot that is never released stays held by
        the gateway, and the device then stops being reachable. So the close is
        run as its own task and awaited to the end, whatever the caller does.
        """
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        close_task = asyncio.ensure_future(self._drop_session())
        while True:
            try:
                await asyncio.shield(close_task)
                break
            except asyncio.CancelledError:
                # Cancelled while disconnecting. Keep waiting: the release is
                # the point of this call.
                if close_task.done():
                    raise
                continue


def runner_for(hass: HomeAssistant, entry: ConfigEntry) -> BleDiagnosticRunner | None:
    """Return the entry's runner, or ``None`` when not set up."""
    data = (hass.data.get(DOMAIN) or {}).get(entry.entry_id) or {}
    runner = data.get("ble_diagnostic")
    return runner if isinstance(runner, BleDiagnosticRunner) else None


__all__ = [
    "BLE_MIN_INTERVAL_SECONDS",
    "BleDiagnosticRunner",
    "PUBLISHED_FIELDS",
    "STATUS_DISABLED",
    "STATUS_ERROR",
    "STATUS_IDLE",
    "STATUS_OK",
    "STATUS_RUNNING",
    "ble_configured_interval",
    "CONNECTION_LEVEL_ERRORS",
    "ble_conf_path",
    "ble_is_enabled",
    "ble_marker_path",
    "read_ble_conf",
    "RATE_LIMITED_ERROR",
    "is_rate_limited",
    "is_connection_level",
    "runner_for",
]