"""Immutable models shared by telemetry transports."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable


class SourceKind(str, Enum):
    CLOUD = "cloud"
    SERIAL = "serial"
    LAN = "lan"
    BLE = "ble"
    GATEWAY = "gateway"
    HOME_ASSISTANT = "home_assistant"


class ValueQuality(str, Enum):
    MEASURED = "measured"
    ESTIMATED = "estimated"
    DERIVED = "derived"
    CACHED = "cached"
    UNKNOWN = "unknown"


def _utc(value: datetime | None, name: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    """Stable source/device identity; address and port are not identity."""

    provider_id: str
    device_id: str
    kind: SourceKind

    def __post_init__(self) -> None:
        if not self.provider_id.strip() or not self.device_id.strip():
            raise ValueError("provider_id and device_id must be non-empty")


@dataclass(frozen=True, slots=True)
class FieldObservation:
    """One value with separate receipt and physical-measurement times."""

    value: float | None
    unit: str
    received_at: datetime
    measured_at: datetime | None
    quality: ValueQuality = ValueQuality.MEASURED

    def __post_init__(self) -> None:
        received = _utc(self.received_at, "received_at")
        measured = _utc(self.measured_at, "measured_at")
        object.__setattr__(self, "received_at", received)
        object.__setattr__(self, "measured_at", measured)
        if not self.unit:
            raise ValueError("unit must be non-empty")
        if self.value is not None:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise TypeError("value must be a finite number or None")
            if not math.isfinite(float(self.value)):
                raise ValueError("value must be finite")
            object.__setattr__(self, "value", float(self.value))
        if self.value is None and self.quality is ValueQuality.MEASURED:
            object.__setattr__(self, "quality", ValueQuality.UNKNOWN)


@dataclass(frozen=True, slots=True)
class SourceFrame:
    """A source's observations for one stable inverter identity."""

    identity: SourceIdentity
    received_at: datetime
    fields: Mapping[str, FieldObservation]
    sequence: int | str | None = None
    profile_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "received_at", _utc(self.received_at, "received_at"))
        copied = dict(self.fields)
        if any(not key.strip() for key in copied):
            raise ValueError("field names must be non-empty")
        if any(not isinstance(item, FieldObservation) for item in copied.values()):
            raise TypeError("fields must contain FieldObservation values")
        object.__setattr__(self, "fields", MappingProxyType(copied))


@runtime_checkable
class TelemetryProvider(Protocol):
    """Read-only transport contract. Deliberately exposes no command method."""

    @property
    def identity(self) -> SourceIdentity: ...

    async def read(self) -> SourceFrame: ...

    async def close(self) -> None: ...
