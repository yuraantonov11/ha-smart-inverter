"""Fault detection and address-targeted release of a stranded BLE connection.

The failure this module exists for was measured on 2026-10-26: after a Home
Assistant restart the gateway's ESPHome bluetooth proxy still held the BLE
connection to the RWB1. A connected device does not advertise, so HA's
scanner never learned it again and every read failed with "not known to
Home Assistant's bluetooth integration" -- while the proxy happily reported
the address as allocated.

Two rules shaped this module:

* **The fault is decided by the reads, not by the proxy's bookkeeping.**
  ``allocated`` is not evidence of a fault: while the runner holds a healthy
  session the proxy lists that address too. The signal is the age of the
  last successful read together with a run of consecutive failures.
* **A healthy session is never torn down.** The release only runs when the
  runner holds no session at all and the reads have been failing long
  enough to prove the link is gone.

The release itself is address-targeted: the proxy is asked to drop exactly
one connection. It never reboots the gateway, never writes Modbus and never
touches the inverter.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: A fault is only considered when no read succeeded for this long.
DEFAULT_MAX_SUCCESS_AGE_S = 90.0
#: ...and only after this many consecutive failed reads.
DEFAULT_MIN_CONSECUTIVE_FAILURES = 5
#: How many release attempts are allowed per fault episode.
DEFAULT_MAX_RELEASE_ATTEMPTS = 3
#: Pause between release attempts.
DEFAULT_RELEASE_COOLDOWN_S = 300.0
#: How long one release attempt may take.
DEFAULT_RELEASE_TIMEOUT_S = 30.0

#: ESPHome's bluetooth proxy encodes a BLE address big-endian.
#: D4:27:87:E4:F9:4F -> 0xD42787E4F94F.
def address_to_esphome_int(address: str) -> int:
    """Convert a BLE MAC to the integer the ESPHome API expects."""
    raw = bytes.fromhex(address.replace(":", "").replace("-", ""))
    if len(raw) != 6:
        raise ValueError(f"not a BLE address: {address!r}")
    return int.from_bytes(raw, "big")


@dataclass(frozen=True, slots=True)
class FaultSignal:
    """Why the link is considered broken."""

    reason: str
    success_age_s: float | None
    consecutive_failures: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "success_age_s": self.success_age_s,
            "consecutive_failures": self.consecutive_failures,
        }


def detect_fault(
    *,
    last_success_at: datetime | None,
    consecutive_failures: int,
    now: datetime | None = None,
    max_success_age_s: float = DEFAULT_MAX_SUCCESS_AGE_S,
    min_consecutive_failures: int = DEFAULT_MIN_CONSECUTIVE_FAILURES,
) -> FaultSignal | None:
    """Return the fault signal, or ``None`` when the link looks healthy.

    The decision uses only what the reads proved: how long ago a read
    succeeded and how many reads have failed since. Neither the proxy's
    allocation list nor the sensor's own state is consulted -- both report
    "allocated" for a perfectly healthy session.
    """
    if consecutive_failures < min_consecutive_failures:
        return None

    moment = now or datetime.now(timezone.utc)
    if last_success_at is None:
        return FaultSignal(
            reason="no successful read since the runner started",
            success_age_s=None,
            consecutive_failures=consecutive_failures,
        )

    if last_success_at.tzinfo is None:
        last_success_at = last_success_at.replace(tzinfo=timezone.utc)
    age = (moment - last_success_at).total_seconds()
    if age <= max_success_age_s:
        return None

    return FaultSignal(
        reason=(
            f"no successful read for {age:.0f}s "
            f"({consecutive_failures} consecutive failures)"
        ),
        success_age_s=round(age, 1),
        consecutive_failures=consecutive_failures,
    )


@dataclass(slots=True)
class ReleaseBudget:
    """Bounds how often a release may be attempted.

    The timestamps are wall-clock, not monotonic: the budget has to survive a
    restart of Home Assistant, and a monotonic clock does not. The state is
    persisted, so a restart in the middle of a fault episode does not hand
    out a fresh series of attempts.
    """

    max_attempts: int = DEFAULT_MAX_RELEASE_ATTEMPTS
    cooldown_s: float = DEFAULT_RELEASE_COOLDOWN_S
    attempts: int = 0
    last_attempt_at: datetime | None = None

    def may_attempt(self, now: datetime) -> bool:
        """True when an attempt is allowed right now."""
        if self.attempts >= self.max_attempts:
            return False
        if self.last_attempt_at is None:
            return True
        age = (now - self.last_attempt_at).total_seconds()
        return age >= self.cooldown_s

    def record(self, now: datetime) -> None:
        """Count an attempt and start the cooldown."""
        self.attempts += 1
        self.last_attempt_at = now

    def reset(self) -> None:
        """A successful read ends the fault episode."""
        self.attempts = 0
        self.last_attempt_at = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "cooldown_s": self.cooldown_s,
            "last_attempt_at": (
                None if self.last_attempt_at is None
                else self.last_attempt_at.isoformat()
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ReleaseBudget":
        """Rebuild a persisted budget.

        A corrupt or missing record must not grant a fresh series of
        attempts silently: the stored count is kept as it is, and only the
        values that make sense are restored.
        """
        budget = cls()
        if not isinstance(data, dict):
            return budget
        try:
            budget.attempts = max(0, int(data.get("attempts", 0) or 0))
        except (TypeError, ValueError):
            budget.attempts = 0
        try:
            budget.max_attempts = max(
                1, int(data.get("max_attempts", DEFAULT_MAX_RELEASE_ATTEMPTS) or 0)
            )
        except (TypeError, ValueError):
            budget.max_attempts = DEFAULT_MAX_RELEASE_ATTEMPTS
        try:
            budget.cooldown_s = max(
                10.0, float(data.get("cooldown_s", DEFAULT_RELEASE_COOLDOWN_S) or 0.0)
            )
        except (TypeError, ValueError):
            budget.cooldown_s = DEFAULT_RELEASE_COOLDOWN_S
        raw = data.get("last_attempt_at")
        if raw:
            try:
                parsed = datetime.fromisoformat(str(raw))
            except ValueError:
                parsed = None
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                budget.last_attempt_at = parsed
        return budget


@dataclass(frozen=True, slots=True)
class ReleaseOutcome:
    """What the release attempt did."""

    ok: bool
    detail: str
    attempted: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "detail": self.detail, "attempted": self.attempted}


class GatewayConnectionReleaser:
    """Ask the ESPHome proxy to drop one BLE connection, by address.

    The proxy is the ESPHome node that owns the bluetooth scanner for the
    device. Home Assistant's ``bluetooth`` config entry points at the
    ``esphome`` entry that provides it, and that entry carries the node's
    host and port. The node's API client is reused when Home Assistant has
    one, so no credentials are handled here and no second long-lived
    connection is opened.

    The import of ``aioesphomeapi`` is lazy and optional: Home Assistant
    ships it for its own ESPHome integration, but this module must not make
    it a hard requirement of the inverter integration.
    """

    def __init__(self, hass: Any, source: str | None) -> None:
        self.hass = hass
        self.source = (source or "").upper() or None

    # -- discovery -----------------------------------------------------
    def _via_bluetooth_entry(self) -> Any | None:
        """Resolve the proxy through Home Assistant's bluetooth entry.

        The bluetooth entry records which config entry provides the scanner
        (``source_config_entry_id``), which is the authoritative link.
        """
        for entry in self.hass.config_entries.async_entries("bluetooth"):
            data = getattr(entry, "data", None) or {}
            source_entry_id = data.get("source_config_entry_id")
            source_mac = str(
                data.get("source") or getattr(entry, "unique_id", "")
            ).upper()
            if not source_entry_id:
                continue
            if self.source and source_mac != self.source:
                continue
            return self.hass.config_entries.async_get_entry(source_entry_id)
        return None

    def _esphome_entry(self) -> Any | None:
        """Find the ESPHome entry that provides the bluetooth proxy.

        The proxy is identified by the ESPHome entry that carries a
        ``bluetooth_mac_address`` -- that is the node acting as the bluetooth
        scanner. The runner's own source (the adapter that saw the device) is
        used to pick the right one when there are several, but it is not
        required: when the device is unknown to Home Assistant -- exactly the
        stranded case -- there is no source to compare against, and a single
        bluetooth proxy is unambiguous.
        """
        candidates = []
        for entry in self.hass.config_entries.async_entries("esphome"):
            data = getattr(entry, "data", None) or {}
            mac = str(data.get("bluetooth_mac_address") or "").upper()
            if mac:
                candidates.append((mac, entry))

        if self.source:
            # A known source is authoritative: if it does not match any proxy,
            # no other proxy is used. Acting on a different proxy would
            # release a connection that is not the one that is stuck.
            for mac, entry in candidates:
                if mac == self.source:
                    return entry
            return self._via_bluetooth_entry()

        if len(candidates) == 1:
            # No source known -- the stranded case -- and exactly one bluetooth
            # proxy in Home Assistant: that one is unambiguous.
            return candidates[0][1]
        return self._via_bluetooth_entry()

    async def _client(self, entry: Any) -> tuple[Any, bool]:
        """Return an API client and whether this caller owns its lifetime."""
        runtime = getattr(entry, "runtime_data", None)
        client = getattr(runtime, "client", None)
        if client is not None:
            return client, False

        try:
            from aioesphomeapi import APIClient
        except ImportError:  # pragma: no cover - aioesphomeapi ships with HA
            raise RuntimeError("aioesphomeapi is not available") from None

        data = getattr(entry, "data", None) or {}
        host = data.get("host")
        port = int(data.get("port") or 6053)
        password = data.get("password") or ""
        if not host:
            raise RuntimeError("the ESPHome entry has no host")
        client = APIClient(host, port, password)
        await asyncio.wait_for(client.connect(login=True), timeout=20)
        return client, True

    # -- the release ----------------------------------------------------
    async def release(self, address: str) -> ReleaseOutcome:
        """Release the connection for ``address`` on the proxy."""
        entry = self._esphome_entry()
        if entry is None:
            return ReleaseOutcome(
                ok=False,
                detail=(
                    f"no ESPHome proxy found for scanner source {self.source}"
                ),
                attempted=False,
            )

        client = None
        owned = False
        try:
            client, owned = await self._client(entry)
            target = address_to_esphome_int(address)
            await asyncio.wait_for(
                client.bluetooth_device_disconnect(target), timeout=DEFAULT_RELEASE_TIMEOUT_S
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - the outcome is reported
            return ReleaseOutcome(
                ok=False, detail=f"{type(err).__name__}: {err}", attempted=True
            )
        finally:
            if owned and client is not None:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001 - closing must not raise
                    _LOGGER.debug("closing the temporary ESPHome client failed",
                                  exc_info=True)

        _LOGGER.warning(
            "RWB1 BLE recovery: released the stranded connection for %s on the "
            "proxy %s",
            address,
            self.source,
        )
        return ReleaseOutcome(
            ok=True, detail=f"released {address} on proxy {self.source}", attempted=True
        )


__all__ = [
    "DEFAULT_MAX_RELEASE_ATTEMPTS",
    "DEFAULT_MAX_SUCCESS_AGE_S",
    "DEFAULT_MIN_CONSECUTIVE_FAILURES",
    "DEFAULT_RELEASE_COOLDOWN_S",
    "FaultSignal",
    "GatewayConnectionReleaser",
    "ReleaseBudget",
    "ReleaseOutcome",
    "address_to_esphome_int",
    "detect_fault",
]
