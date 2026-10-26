"""Home Assistant BLE transport for the experimental RWB1 read path.

The transport implements :class:`telemetry_sources.models.TelemetryProvider`:
read-only, one transaction at a time, bounded deadlines, no control command.

Dependencies are injected. The ``bleakenector`` argument is any object with the
narrow BleakClient-like surface used here, **or** a zero-argument callable that
builds one::

    connect() / disconnect()
    write_gatt_char(characteristic, data, response=True)
    start_notify(characteristic, callback) / stop_notify(characteristic)
    services  ->  service collection with get_service(uuid)  (only after connect)
    mtu_size  ->  negotiated MTU                             (only after connect)

Both ``services`` and ``mtu_size`` are read *after* ``connect()``, because real
``bleak`` raises ``BleakError("Service Discovery has not been performed yet")``
before a connection exists (verified on bleak 3.0.2).

Two factory paths exist and neither imports ``bleak`` at module import time:

* :func:`default_bleakenector_factory` builds a client from a MAC address
  string using ``bleak_retry_connector.BleakClientWithServiceCache``. The class
  is **not** importable from ``bleak`` itself on bleak 3.x; importing it from
  there raised ``ImportError`` on every live read.
* :func:`ha_bleakenector_factory` is the Home Assistant path: it resolves the
  address through the documented
  ``homeassistant.components.bluetooth.async_ble_device_from_address`` API and
  hands the resulting ``BLEDevice`` object to the client class. That is what
  makes network-attached Bluetooth proxies (Xiaomi Gateway 3, ESPHome
  Bluetooth proxies) usable, because HA's client routes through the proxy's
  adapter rather than opening a local BlueZ socket. A call site that already
  holds a ``BLEDevice`` passes it straight through.

``aioesphomeapi`` is deliberately never imported directly.

Rate limiting: the first read after construction or after a failure is not
issued sooner than ``min_interval`` seconds (default 5 s) from the previous
attempt. Reconnection uses bounded exponential backoff capped by
``max_backoff`` seconds. ``close()`` unsubscribes indications and disconnects.
Cancelling a read releases the link exactly like a failure does, but is not
counted as a device failure.

MIT-licensed protocol reference used to cross-check this implementation:
https://github.com/Jpedrotas/easun-rwb1-esphome (Copyright (c) 2026 EASUN RWB1
ESPHome contributors) - ``tools/ble_modbus_read.py``.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from .models import SourceFrame, SourceIdentity, SourceKind
from .rwb1_ble_codec import (
    FragmentReassembler,
    decrypt_response,
    derive_key,
    encrypt_request,
    fragment_payload,
    max_fragment_payload,
)
from .rwb1_ble_profile import (
    Rwb1BleError,
    build_allowlisted_request,
    decode_read_response,
)


SERVICE_UUID = "0000fee7-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000fed5-0000-1000-8000-00805f9b34fb"
INDICATE_UUID = "0000fed6-0000-1000-8000-00805f9b34fb"
CCCD_UUID = "00002902-0000-1000-8000-00805f9b34fb"

DEFAULT_MIN_INTERVAL = 5.0
# Safety floor only, and it must stay in step with the runner's
# ``BLE_MIN_INTERVAL_SECONDS``. The two layers disagreed once already:
# the runner accepted 10 s while this rejected it, so every cycle ended in
# a ValueError instead of a read. The floor is a guard against an
# accidental hammer on a device that also serves the vendor phone app --
# it is not a measured limit of the hardware.
DEFAULT_CONNECT_TIMEOUT = 20.0
DEFAULT_RESPONSE_TIMEOUT = 5.0
DEFAULT_INITIAL_BACKOFF = 1.0
DEFAULT_MAX_BACKOFF = 60.0
# The device keeps sending unsolicited frames; one clean reply window plus a
# short grace period is enough and keeps the link idle the rest of the time.
SETTLE_SECONDS = 1.0


class Rwb1BleTransportError(Rwb1BleError):
    """BLE connection, notification or framing failure."""


class Rwb1BleTimeout(Rwb1BleTransportError):
    """No complete, valid response arrived before the deadline."""


class Rwb1BleRateLimited(Rwb1BleError):
    """A local scheduling decision, not anything the device did.

    Raised when ``read()`` is called before the provider's own minimum interval
    has elapsed. It is deliberately **not** a ``Rwb1BleTransportError``: nothing
    was sent to the radio, no reply was awaited, and the session is healthy.
    Callers must not treat it as a device failure or start recovery for it --
    conflating the two produced a spurious "error" in the diagnostics after a
    live interval change.
    """


class Bleakenector(Protocol):
    """The minimal BleakClient-like surface this transport depends on."""

    mtu_size: int

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def write_gatt_char(
        self, characteristic: Any, data: bytes, response: bool = ...
    ) -> None: ...

    async def start_notify(self, characteristic: Any, callback: Callable[..., None]) -> None: ...

    async def stop_notify(self, characteristic: Any) -> None: ...

    @property
    def services(self) -> Any: ...


def resolve_bleak_client_class() -> type:
    """Return the client class used for live BLE access.

    ``BleakClientWithServiceCache`` ships with **bleak-retry-connector**, not
    with ``bleak``: on bleak 3.0.2 ``from bleak import
    BleakClientWithServiceCache`` raises ``ImportError``. The service cache is
    what makes network-attached adapters and HA proxies usable, so there is no
    fallback to the plain ``BleakClient``.
    """
    try:
        from bleak_retry_connector import BleakClientWithServiceCache
    except ImportError as err:
        raise Rwb1BleTransportError(
            "bleak-retry-connector is required for live BLE access "
            "(it provides BleakClientWithServiceCache); "
            "inject a bleakenector for tests"
        ) from err
    return BleakClientWithServiceCache


def default_bleakenector_factory(
    address: str,
    *,
    timeout: float = DEFAULT_CONNECT_TIMEOUT,
    client_class: type | None = None,
) -> Bleakenector:
    """Build a client for a MAC address string.

    ``client_class`` exists so the runtime wiring can be exercised without a
    radio; production always resolves
    :func:`resolve_bleak_client_class` lazily.
    """
    if not isinstance(address, str) or not address.strip():
        raise Rwb1BleTransportError("a device address is required to build a client")
    factory = client_class if client_class is not None else resolve_bleak_client_class()
    return factory(address, timeout=timeout)


def ha_bleakenector_factory(
    hass: Any,
    device_or_address: Any,
    *,
    timeout: float = DEFAULT_CONNECT_TIMEOUT,
    connectable: bool = True,
    bluetooth_api: Any | None = None,
    client_class: type | None = None,
) -> Bleakenector:
    """Home Assistant path: resolve a ``BLEDevice`` and connect through HA.

    ``device_or_address`` is either an address string - resolved with the
    documented ``bluetooth.async_ble_device_from_address`` API - or a
    ``BLEDevice`` the call site already holds, which is passed through
    untouched. Going through HA (rather than handing bleak a bare address) is
    what routes the connection through a network-attached adapter or proxy.

    ``bluetooth_api`` and ``client_class`` are injection seams for tests; both
    default to the real Home Assistant / bleak objects.
    """
    if isinstance(device_or_address, str):
        api = bluetooth_api if bluetooth_api is not None else _ha_bluetooth_api()
        device = api.async_ble_device_from_address(
            hass, device_or_address, connectable=connectable,
        )
        if device is None:
            raise Rwb1BleTransportError(
                f"device {device_or_address} is not known to Home Assistant; "
                "make sure the bluetooth integration has discovered it"
            )
    else:
        device = device_or_address
    if device is None:
        raise Rwb1BleTransportError("a Home Assistant BLEDevice is required")
    factory = client_class if client_class is not None else resolve_bleak_client_class()
    return factory(device, timeout=timeout)


class _LazyBleDeviceConnector:
    """A real-bleak client built lazily around a Home Assistant ``BLEDevice``.

    ``ble_device_to_bleakenector`` returns this wrapper; it does NOT
    instantiate the underlying bleak client class at construction time.
    That matters because the test path uses a synthetic ``BLEDevice``
    (no adapter ``details``), and the real constructor touches the
    adapter-specific backend eagerly. Building the wrapper is cheap and
    safe; the real ``client_class(device)`` runs only when ``connect()`` is
    invoked, by which point a production caller has a real adapter.
    """

    def __init__(
        self,
        device: Any,
        *,
        timeout: float = DEFAULT_CONNECT_TIMEOUT,
        client_class: type | None = None,
    ) -> None:
        if device is None:
            raise Rwb1BleTransportError("a BLEDevice is required to build a client")
        self._device = device
        self._timeout = timeout
        self._client_class = (
            client_class if client_class is not None else resolve_bleak_client_class()
        )
        self._inner: Any = None

    async def connect(self) -> Any:
        self._inner = self._client_class(self._device, timeout=self._timeout)
        return await self._inner.connect()

    async def disconnect(self) -> Any:
        if self._inner is None:
            return None
        return await self._inner.disconnect()


    async def start_notify(self, *args: Any, **kwargs: Any) -> Any:
        if self._inner is None:
            raise Rwb1BleTransportError("start_notify called before connect()")
        return await self._inner.start_notify(*args, **kwargs)

    async def stop_notify(self, *args: Any, **kwargs: Any) -> Any:
        if self._inner is None:
            return None
        return await self._inner.stop_notify(*args, **kwargs)

    @property
    def services(self) -> Any:
        if self._inner is None:
            raise Rwb1BleTransportError("services accessed before connect()")
        return self._inner.services

    @property
    def mtu_size(self) -> int:
        if self._inner is None:
            return 23
        return self._inner.mtu_size


def ble_device_to_bleakenector(
    device: Any,
    *,
    timeout: float = DEFAULT_CONNECT_TIMEOUT,
    client_class: type | None = None,
) -> Bleakenector:
    """Return a deferred wrapper for an already-resolved Home Assistant
    ``BLEDevice``.

    This is the no-``hass``-required path used by the provider when a caller
    has already resolved the ``BLEDevice`` (e.g. through Home Assistant's
    bluetooth API). Unlike ``ha_bleakenector_factory`` it does no address
    lookup and does not require ``hass``. The real bleak client class is
    constructed lazily inside :meth:`_LazyBleDeviceConnector.connect`,
    so a synthetic ``BLEDevice`` (no adapter ``details``) is safe at
    construction time; the real ``client_class(device)`` only runs when
    production code calls ``connect()``.
    """
    return _LazyBleDeviceConnector(
        device, timeout=timeout, client_class=client_class,
    )


def _ha_bluetooth_api() -> Any:
    """Import the documented Home Assistant bluetooth API lazily."""
    try:
        from homeassistant.components import bluetooth
    except ImportError as err:
        raise Rwb1BleTransportError(
            "homeassistant.components.bluetooth is required to use a BLEDevice; "
            "add the bluetooth integration dependency or pass a bare address"
        ) from err
    return bluetooth


# Bounded teardown budgets. Per step, so one stalled adapter call cannot
# consume the whole budget; overall, so a caller waiting on teardown is
# released even when the backend never answers.
# Tight enough to finish inside Home Assistant's shutdown budget: a proxy
# disconnect is a short API call, and a teardown that overruns the shutdown
# deadline is a teardown that never happens. Both numbers are per the whole
# teardown, not per retry.
CLEANUP_STEP_TIMEOUT = 3.0
CLEANUP_DEADLINE = 6.0


async def _teardown_client(
    client: Any, *, notifying: bool, step_timeout: float
) -> list[str]:
    """Release one BLE client: unsubscribe, then disconnect. Never raises.

    This coroutine is only ever run as a *tracked task* (see
    ``_start_cleanup``), so a caller's cancellation cannot reach it. That is the
    whole point: ``asyncio.wait_for`` in the old inline teardown cancelled the
    in-flight ``disconnect`` coroutine when the caller was cancelled, and
    swallowing the resulting ``CancelledError`` did not make the backend release
    its proxy slot. Here the only thing that can stop a step is its own
    deadline.

    Each step is bounded by ``step_timeout`` so a wedged adapter cannot stall
    teardown indefinitely. Returns the names of the steps that did not complete
    -- ``["disconnect"]`` means the backend proxy slot may still be held, and
    that must never be reported as a clean release.
    """
    failed: list[str] = []
    if notifying:
        stop = getattr(client, "stop_notify", None)
        if stop is not None:
            completed, error = await _await_cleanup(
                stop, INDICATE_UUID, timeout=step_timeout
            )
            if not completed:
                # An open indication subscription also pins the backend slot,
                # so it is reported -- but the disconnect below is the step
                # that actually frees it.
                failed.append(f"stop_notify{f': {error}' if error else ''}")
    disconnect = getattr(client, "disconnect", None)
    if disconnect is not None:
        # The step that actually frees the backend slot. If it does not
        # complete, the caller must be told: clearing the flags is not a release.
        completed, error = await _await_cleanup(disconnect, timeout=step_timeout)
        if not completed:
            failed.append(f"disconnect{f': {error}' if error else ''}")
    return failed


async def _await_cleanup(
    coro_factory: Callable[..., Any], *args: Any, timeout: float
) -> tuple[bool, str | None]:
    """Run one cleanup step to its deadline, then always move to the next.

    Used by :func:`_teardown_client`, which runs as a tracked task the caller
    cannot cancel. So the ``CancelledError`` caught here is not a caller's
    cancel -- it is the step's own deadline expiring, or an adapter error --
    and the handler exists to keep teardown moving to the remaining steps.

    The handler is deliberately ``BaseException`` so nothing raised by a step
    (including a cancellation delivered by ``asyncio.wait_for`` on timeout)
    aborts the steps that follow. ``task.uncancel()`` keeps the enclosing
    task's cancellation bookkeeping consistent when we absorbed one.

    Returns ``(completed, error)``: ``completed`` is ``True`` only if the
    step actually finished, and ``error`` describes why it did not -- the
    exception text for a step that raised, or ``"deadline"`` for one that
    ran out of time. The caller reports it, so the operator learns *which*
    step was left unfinished and why, instead of only that something was.
    """
    try:
        await asyncio.wait_for(coro_factory(*args), timeout=timeout)
    except asyncio.TimeoutError:
        task = asyncio.current_task()
        if task is not None and task.cancelling() > 0:
            task.uncancel()
        return False, "deadline"
    except BaseException as err:  # noqa: BLE001 - teardown must always move on.
        task = asyncio.current_task()
        if task is not None and task.cancelling() > 0:
            task.uncancel()
        return False, f"{type(err).__name__}: {err}"
    return True, None



@dataclass(frozen=True, slots=True)
class _Outcome:
    frame: SourceFrame
    cmd_no: str | None
    cmd_no_present: bool


class Rwb1BleTelemetryProvider:
    """One allow-listed RWB1 BLE read per :meth:`read`, serialized."""

    def __init__(
        self,
        *,
        identity: SourceIdentity,
        dtu_id: str,
        bleakenector: Bleakenector | Callable[[], Bleakenector] | None = None,
        device_address: str | None = None,
        ble_device: Any | None = None,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        response_timeout: float = DEFAULT_RESPONSE_TIMEOUT,
        initial_backoff: float = DEFAULT_INITIAL_BACKOFF,
        max_backoff: float = DEFAULT_MAX_BACKOFF,
        settle_seconds: float = SETTLE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        cleanup_step_timeout: float = CLEANUP_STEP_TIMEOUT,
        cleanup_deadline: float = CLEANUP_DEADLINE,
    ) -> None:
        if identity.kind is not SourceKind.BLE:
            raise ValueError("identity kind must be ble")
        if min_interval < DEFAULT_MIN_INTERVAL:
            raise ValueError(
                f"min_interval must be at least {DEFAULT_MIN_INTERVAL} seconds"
            )
        if response_timeout <= 0 or connect_timeout <= 0:
            raise ValueError("timeouts must be positive")
        if not 0 < initial_backoff <= max_backoff:
            raise ValueError("backoff bounds are inconsistent")
        self._identity = identity
        self._dtu_id = dtu_id
        self._connector = bleakenector
        self._device_address = device_address
        self._ble_device = ble_device
        self._min_interval = float(min_interval)
        self._connect_timeout = float(connect_timeout)
        self._response_timeout = float(response_timeout)
        self._initial_backoff = float(initial_backoff)
        self._max_backoff = float(max_backoff)
        self._settle_seconds = float(settle_seconds)
        # Bounded teardown budgets. Injectable so a test can exercise the
        # deadline without waiting it out; defaults are the shipping values.
        self._cleanup_step_timeout = float(cleanup_step_timeout)
        self._cleanup_deadline = float(cleanup_deadline)
        if self._cleanup_step_timeout <= 0 or self._cleanup_deadline <= 0:
            raise ValueError("cleanup timeouts must be positive")
        self._clock = clock
        self._wall_clock = wall_clock
        self._key = derive_key(dtu_id)
        self._lock = asyncio.Lock()
        self._client: Bleakenector | None = None
        self._notifying = False
        # Teardown state. ``_cleanup_client`` is kept alive for as long as the
        # tracked task owns it, and ``_teardown_incomplete`` records a teardown
        # that did NOT finish within its deadline -- clearing flags is not
        # evidence that the backend proxy slot was released.
        self._cleanup_task: asyncio.Task[None] | None = None
        self._cleanup_client: Any = None
        self._teardown_incomplete: str | None = None
        self._closed = False
        self._consecutive_failures = 0
        self._next_attempt_at = 0.0
        self._last_attempt_at = 0.0
        # Per-transaction notification state, defined up front because the
        # device may push an unsolicited frame while we are still connecting.
        self._active = False
        self._received = asyncio.Event()
        self._messages: list[bytes] = []
        self._received_at: datetime | None = None
        self._failure: Rwb1BleError | None = None
        self._assembler = FragmentReassembler()

    @property
    def identity(self) -> SourceIdentity:
        return self._identity

    @property
    def ble_device(self) -> Any | None:
        """The Home Assistant ``BLEDevice`` this provider was wired to.

        Exposed for diagnostics and for call sites that assemble the provider
        before the device is known; it is never logged and never holds the DTU
        identifier.
        """
        return self._ble_device

    @property
    def consecutive_failures(self) -> int:
        """Successful reads reset this; diagnostics only, never logged."""
        return self._consecutive_failures

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._notifying

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(provider_id={self._identity.provider_id!r}, "
            f"device_id={self._identity.device_id!r}, connected={self.is_connected})"
        )

    def _build_client(self) -> Bleakenector:
        """Resolve the connector lazily; never stores the DTU identifier.

        Order of preference:

        1. an already-built client object (it carries ``connect``),
        2. the injected zero-argument factory - this is the Home Assistant
           call-site path and it is *called*, not replaced,
        3. a Home Assistant ``BLEDevice`` or a bare address string, which goes
           through the default factories.
        """
        connector = self._connector
        if connector is not None:
            if hasattr(connector, "connect"):
                return connector
            if callable(connector):
                built = connector()
                if not hasattr(built, "connect"):
                    raise Rwb1BleTransportError(
                        "bleakenector must be a client or a factory returning one"
                    )
                self._connector = built
                return built
        if self._ble_device is not None:
            self._connector = ble_device_to_bleakenector(
                self._ble_device, timeout=self._connect_timeout,
            )
            return self._connector
        if self._device_address:
            self._connector = default_bleakenector_factory(
                self._device_address, timeout=self._connect_timeout,
            )
            return self._connector
        if connector is None:
            raise Rwb1BleTransportError(
                "a device address or a Home Assistant BLEDevice is required "
                "to build a client, or inject a bleakenector"
            )
        raise Rwb1BleTransportError("bleakenector must be a client or a factory")

    def _backoff_delay(self) -> float:
        delay = self._initial_backoff * (2 ** (self._consecutive_failures - 1))
        return min(delay, self._max_backoff)

    async def _connect(self) -> None:
        if self._client is not None:
            return
        now = self._clock()
        if now < self._next_attempt_at:
            raise Rwb1BleTimeout("connection backoff is still in effect")
        client = self._build_client()
        notifying = False
        try:
            # Order matters: real bleak refuses ``client.services`` until the
            # connection has completed service discovery, so discovery is read
            # only *after* connect() returned.
            await asyncio.wait_for(client.connect(), timeout=self._connect_timeout)
            service = client.services.get_service(SERVICE_UUID)
            if service is None:
                raise Rwb1BleTransportError("FEE7 service was not discovered")
            await asyncio.wait_for(client.start_notify(INDICATE_UUID, self._on_notify), 10.0)
            notifying = True
        except BaseException:
            # ``BaseException``: a cancellation must release the link too, and
            # a *partially* connected client already holds the backend proxy
            # slot -- connect() acquired it before discovery finished. This is
            # the same tracked teardown the fully-connected path uses, so a
            # second cancellation cannot strand it.
            task = self._start_cleanup(client, notifying=notifying)
            await self._await_tracked_cleanup(task, deadline=self._cleanup_deadline)
            raise
        self._client = client
        self._notifying = True

    def _on_notify(self, _characteristic: object, data: bytearray) -> None:
        # Only frames arriving inside a live transaction are collected.
        if not self._active:
            return
        try:
            state = self._assembler.feed(bytes(data))
        except Rwb1BleError as err:
            self._failure = err
            self._messages.append(b"")
            if self._received.is_set() is False:
                self._received.set()
            return
        if state.complete is not None:
            self._messages.append(state.complete)
            # ``received_at`` marks the moment the *complete* message arrived,
            # not the moment the request was sent: the round trip (several GATT
            # writes plus a notification window) is real elapsed time and the
            # selection layer measures freshness against this stamp.
            self._received_at = datetime.fromtimestamp(self._wall_clock(), tz=timezone.utc)
            self._received.set()

    @property
    def teardown_incomplete(self) -> str | None:
        """Why the last teardown did not finish, or ``None`` if it did.

        A ``None`` here means the backend was actually released. A non-``None``
        value means flags were cleared but the proxy slot may still be held --
        callers must treat that as a real failure, not a clean close.
        """
        return self._teardown_incomplete

    def _start_cleanup(self, client: Any, *, notifying: bool) -> asyncio.Task[None]:
        """Create (or reuse) the single tracked teardown task for ``client``.

        Idempotent: while a teardown is in flight the same task is returned, so
        a concurrent ``close()`` joins the running cleanup instead of starting
        a second one or finding no client to release.
        """
        existing = self._cleanup_task
        if existing is not None and not existing.done():
            return existing
        if client is None:
            raise Rwb1BleTransportError("no client to tear down")

        # Ownership transfer: ``_client`` is what ``_connect`` short-circuits
        # on, so it must be cleared here or the next read would reuse a
        # disconnected client. ``_cleanup_client`` keeps the reference alive for
        # as long as teardown needs it, so ``close()`` can still join.
        if self._client is client:
            self._client = None
        self._cleanup_client = client
        # A new teardown starts with a clean verdict; the previous one is
        # already recorded in whatever the caller observed.
        self._teardown_incomplete = None

        async def run() -> None:
            try:
                failed = await _teardown_client(
                    client,
                    notifying=notifying,
                    step_timeout=self._cleanup_step_timeout,
                )
                if failed:
                    self._teardown_incomplete = (
                        f"teardown step(s) {', '.join(failed)} did not complete; "
                        "the backend proxy slot may still be held"
                    )
            finally:
                # Only now is the client reference allowed to go: until teardown
                # actually ends, ``close()`` still needs something to wait on.
                if self._cleanup_client is client:
                    self._cleanup_client = None
                self._notifying = False

        task = asyncio.get_running_loop().create_task(run())
        self._cleanup_task = task

        def _finished(finished: asyncio.Task[None]) -> None:
            if self._cleanup_task is finished:
                self._cleanup_task = None
            if finished.cancelled():
                self._teardown_incomplete = "teardown task was cancelled"
                return
            error = finished.exception()
            if error is not None:  # pragma: no cover - _teardown_client swallows
                self._teardown_incomplete = f"teardown raised {error!r}"

        task.add_done_callback(_finished)
        return task

    async def _await_tracked_cleanup(
        self, task: asyncio.Task[None], *, deadline: float
    ) -> bool:
        """Wait for a tracked teardown without letting caller cancel abort it.

        ``asyncio.wait`` -- unlike ``asyncio.shield`` or a bare ``await`` -- does
        not propagate a cancellation of the awaiting task into the task it is
        waiting on. A caller cancelled mid-teardown therefore keeps waiting
        instead of orphaning the disconnect, while still being released by the
        bounded ``deadline``. The caller's ``CancelledError`` is not swallowed:
        it is re-raised here once teardown has finished, so ``close()`` stays
        cancellable and ``read()`` still re-raises the cancel it was handling.

        Returns ``True`` when teardown completed, ``False`` when the deadline
        expired with the slot possibly still held.
        """
        loop = asyncio.get_running_loop()
        end = loop.time() + deadline
        caller_cancelled: asyncio.CancelledError | None = None
        while True:
            remaining = end - loop.time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait({task}, timeout=remaining)
                break
            except asyncio.CancelledError as err:
                # Keep waiting for the teardown -- the caller is not allowed to
                # orphan the release -- but remember the cancel so it can be
                # re-raised once teardown has finished.
                caller_cancelled = err

        if not task.done():
            self._teardown_incomplete = (
                f"teardown did not finish within {deadline:g}s; "
                "the backend proxy slot may still be held"
            )
        if caller_cancelled is not None:
            # The caller's cancellation is honoured, but only after teardown.
            raise caller_cancelled
        return not task.cancelled() and task.exception() is None

    async def _disconnect(self) -> bool:
        """Release the link through a tracked teardown the caller cannot abort.

        Returns ``True`` when the backend was actually released. A ``False``
        return means the bounded deadline expired first; the client reference is
        kept so a later ``close()`` can still wait on the running task.
        """
        client = self._client if self._client is not None else self._cleanup_client
        if client is None:
            self._notifying = False
            return True

        running = self._cleanup_task
        if running is not None and not running.done():
            # A teardown is already in flight; join it rather than starting
            # another one against the same client.
            self._notifying = False
            completed = await self._await_tracked_cleanup(
                running, deadline=self._cleanup_deadline
            )
            return completed and self._teardown_incomplete is None

        task = self._start_cleanup(client, notifying=self._notifying)
        self._notifying = False
        completed = await self._await_tracked_cleanup(
            task, deadline=self._cleanup_deadline
        )
        # ``completed`` only says the task finished; a step that hit its own
        # deadline still leaves the backend slot held, so the verdict is the
        # conjunction of both.
        return completed and self._teardown_incomplete is None


    async def _transaction(self) -> _Outcome:
        self._received = asyncio.Event()
        self._messages = []
        self._received_at = None
        self._failure = None
        limit = max_fragment_payload(self._client.mtu_size)
        self._assembler = FragmentReassembler(max_payload=limit)
        payload, cmd_no = encrypt_request(build_allowlisted_request(), self._key)
        fragments = fragment_payload(payload, max_payload=limit)
        self._active = True
        try:
            for fragment in fragments:
                await asyncio.wait_for(
                    self._client.write_gatt_char(WRITE_UUID, fragment, response=True),
                    self._connect_timeout,
                )
            try:
                await asyncio.wait_for(self._received.wait(), timeout=self._response_timeout)
            except asyncio.TimeoutError as err:
                raise Rwb1BleTimeout("no complete response before the deadline") from err
            if self._failure is not None:
                raise self._failure
            if self._settle_seconds > 0:
                await asyncio.sleep(self._settle_seconds)
            message = self._messages[-1] if self._messages else b""
            if not message:
                raise Rwb1BleTimeout("device produced no usable response")
        finally:
            # Stop collecting the moment the window closes; a late unsolicited
            # frame must never leak into the next transaction.
            self._active = False
        received_at = self._received_at or datetime.fromtimestamp(
            self._wall_clock(), tz=timezone.utc,
        )
        decoded = decrypt_response(message, self._key, expected_cmd_no=cmd_no)
        frame = decode_read_response(
            decoded.modbus, identity=self._identity, received_at=received_at,
        )
        return _Outcome(frame=frame, cmd_no=decoded.cmd_no, cmd_no_present=decoded.cmd_no_present)

    async def read(self) -> SourceFrame:
        """Perform one allow-listed read; serialize and rate-limit access."""
        if self._closed:
            raise Rwb1BleTransportError("provider is closed")
        async with self._lock:
            # ``close()`` takes this same lock, so a read that queued behind
            # another read can acquire it *after* teardown; re-check here.
            if self._closed:
                raise Rwb1BleTransportError("provider is closed")
            now = self._clock()
            if self._last_attempt_at and now - self._last_attempt_at < self._min_interval:
                # Local rate limit, not a device failure: see Rwb1BleRateLimited.
                raise Rwb1BleRateLimited(
                    f"read skipped: minimum interval of {self._min_interval:g}s not elapsed"
                )
            self._last_attempt_at = now
            try:
                await self._connect()
                outcome = await self._transaction()
            except asyncio.CancelledError:
                # ``CancelledError`` is a ``BaseException`` since Python 3.8,
                # so it must be handled explicitly: the link is torn down (the
                # proxy connection slot is released) but a caller-initiated
                # cancellation is not device evidence, so it does not feed the
                # backoff.
                self._active = False
                await self._release_after_cancel()
                raise
            except Exception:
                self._consecutive_failures += 1
                self._next_attempt_at = self._clock() + self._backoff_delay()
                await self._disconnect()
                raise
            self._consecutive_failures = 0
            self._next_attempt_at = 0.0
            return outcome.frame

    async def _release_after_cancel(self) -> None:
        """Release the link; a further cancellation must not orphan the teardown.

        The tracked task owns the actual ``disconnect``, so a second
        ``task.cancel()`` delivered while teardown runs cannot cancel it. This
        coroutine only waits, and a deadline expiry is reported through
        :attr:`teardown_incomplete` rather than being papered over by clearing
        the flags.
        """
        try:
            await self._disconnect()
        except BaseException:  # noqa: BLE001 - the original cancel is re-raised.
            pass

    async def close(self) -> None:
        """Unsubscribe indications and disconnect; safe to call repeatedly."""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            await self._disconnect()


__all__ = [
    "Bleakenector",
    "CCCD_UUID",
    "CLEANUP_DEADLINE",
    "CLEANUP_STEP_TIMEOUT",
    "INDICATE_UUID",
    "Rwb1BleTelemetryProvider",
    "Rwb1BleTimeout",
    "Rwb1BleTransportError",
    "SERVICE_UUID",
    "WRITE_UUID",
    "default_bleakenector_factory",
    "ha_bleakenector_factory",
    "resolve_bleak_client_class",
]