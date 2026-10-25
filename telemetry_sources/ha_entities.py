"""Read-only adapter for telemetry entities already published in Home Assistant.

This lets a separately installed local bridge (for example, an ESP32/MQTT
device with HA discovery) feed the integration without making OpenWrt or a
broker client a hard dependency. It only reads HA states; it exposes no write
or service-call path.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Callable, Mapping, Any

from .models import FieldObservation, SourceFrame, SourceIdentity, SourceKind, ValueQuality


_UNAVAILABLE = frozenset({"", "unknown", "unavailable", "none", "null"})


class HomeAssistantEntityProvider:
    """Map explicitly configured HA entity IDs to canonical telemetry fields.

    ``get_state`` is typically ``hass.states.get``. ``entity_ids`` maps a
    canonical field name (e.g. ``battery_soc``) to a Home Assistant entity ID.
    ``expected_units`` is required for every configured field; values are
    rejected as unknown when the entity reports a different unit. HA's
    ``last_reported`` is preferred for receipt freshness, with ``last_updated``
    as a compatibility fallback. Physical measurement time remains unknown.
    """

    def __init__(
        self,
        identity: SourceIdentity,
        get_state: Callable[[str], Any | None],
        entity_ids: Mapping[str, str],
        expected_units: Mapping[str, str],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if identity.kind is not SourceKind.HOME_ASSISTANT:
            raise ValueError("identity kind must be home_assistant")
        if not entity_ids:
            raise ValueError("at least one entity mapping is required")
        if set(entity_ids) != set(expected_units):
            raise ValueError("each entity mapping must have exactly one expected unit")
        if any(not field.strip() or not entity_id.strip() for field, entity_id in entity_ids.items()):
            raise ValueError("field names and entity IDs must be non-empty")
        if any(not unit.strip() for unit in expected_units.values()):
            raise ValueError("expected units must be non-empty")
        self.identity = identity
        self._get_state = get_state
        self._entity_ids = dict(entity_ids)
        self._expected_units = dict(expected_units)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def read(self) -> SourceFrame:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        now = now.astimezone(timezone.utc)
        observations: dict[str, FieldObservation] = {}

        for field, entity_id in self._entity_ids.items():
            state = self._get_state(entity_id)
            value: float | None = None
            quality = ValueQuality.UNKNOWN
            received_at = now
            unit = self._expected_units[field]

            if state is not None:
                attributes = getattr(state, "attributes", {}) or {}
                reported_unit = attributes.get("unit_of_measurement")
                timestamps = (
                    getattr(state, "last_reported", None),
                    getattr(state, "last_updated", None),
                )
                timestamp = next((item for item in timestamps if _aware_datetime(item)), None)
                if timestamp is not None:
                    received_at = timestamp.astimezone(timezone.utc)

                raw = getattr(state, "state", None)
                if (
                    timestamp is not None
                    and isinstance(raw, str)
                    and raw.strip().lower() not in _UNAVAILABLE
                    and reported_unit == unit
                ):
                    try:
                        parsed = float(raw)
                    except (TypeError, ValueError, OverflowError):
                        parsed = math.nan
                    if math.isfinite(parsed):
                        value = parsed
                        quality = ValueQuality.MEASURED

            observations[field] = FieldObservation(
                value=value,
                unit=unit,
                received_at=received_at,
                measured_at=None,
                quality=quality,
            )

        newest_received = max(
            (item.received_at for item in observations.values()), default=now,
        )
        return SourceFrame(
            identity=self.identity,
            received_at=newest_received,
            fields=observations,
            profile_id="ha-entity-map-v1",
        )

    async def close(self) -> None:
        """State access is owned by Home Assistant; nothing to close."""


def _aware_datetime(value: Any) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )
