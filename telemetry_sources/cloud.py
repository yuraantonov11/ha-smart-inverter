"""Normalize the integration's existing cloud response to a source frame."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Mapping

from .models import FieldObservation, SourceFrame, SourceIdentity, SourceKind, ValueQuality


# Canonical fields deliberately limited to direct readings already consumed
# by the coordinator and independently corroborated in the RWB1 investigation.
_FIELD_MAP: dict[str, tuple[str, str, float, float]] = {
    "pv_power": ("pvPower", "W", 0.0, 50_000.0),
    "load_power": ("loadPower", "W", 0.0, 50_000.0),
    "grid_voltage": ("gridVoltage", "V", 0.0, 300.0),
    "battery_voltage": ("batteryVoltage", "V", 0.0, 100.0),
    "battery_soc": ("batterySoc", "%", 0.0, 100.0),
}


def cloud_payload_to_frame(
    payload: Mapping[str, Any],
    *,
    device_id: str,
    received_at: datetime,
) -> SourceFrame:
    """Create a canonical cloud frame without inventing values or timestamps.

    ``received_at`` is when the caller received this response, not the
    inverter's physical measurement time. If a key is absent, invalid, or out
    of range, the field is represented as unknown rather than zero.
    """
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("received_at must be timezone-aware")
    identity = SourceIdentity("powmr_cloud", device_id, SourceKind.CLOUD)
    fields: dict[str, FieldObservation] = {}
    for canonical_name, (api_key, unit, minimum, maximum) in _FIELD_MAP.items():
        raw_value = payload.get(api_key)
        value: float | None = None
        if raw_value is not None and not isinstance(raw_value, bool):
            try:
                parsed = float(raw_value)
            except (TypeError, ValueError, OverflowError):
                parsed = math.nan
            if math.isfinite(parsed) and minimum <= parsed <= maximum:
                value = parsed
        fields[canonical_name] = FieldObservation(
            value=value,
            unit=unit,
            received_at=received_at,
            measured_at=None,
            quality=ValueQuality.MEASURED if value is not None else ValueQuality.UNKNOWN,
        )
    return SourceFrame(
        identity=identity,
        received_at=received_at,
        fields=fields,
        profile_id="powmr-cloud-response-v1",
    )
