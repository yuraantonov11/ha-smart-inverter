"""Per-field source choice with explicit freshness and quality rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Iterable, Mapping

from .models import FieldObservation, SourceFrame, SourceIdentity, ValueQuality


@dataclass(frozen=True, slots=True)
class FieldPolicy:
    max_receipt_age: timedelta
    max_measurement_age: timedelta | None = None
    require_measurement_time: bool = False
    allowed_qualities: frozenset[ValueQuality] = frozenset({ValueQuality.MEASURED})
    allowed_providers: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.max_receipt_age.total_seconds() < 0:
            raise ValueError("max_receipt_age cannot be negative")
        if self.max_measurement_age is not None and self.max_measurement_age.total_seconds() < 0:
            raise ValueError("max_measurement_age cannot be negative")
        if self.allowed_providers is not None:
            if not self.allowed_providers:
                raise ValueError("allowed_providers cannot be empty")
            for entry in self.allowed_providers:
                if not isinstance(entry, str) or not entry.strip():
                    raise ValueError("allowed_providers entries must be non-empty strings")


@dataclass(frozen=True, slots=True)
class DiagnosticObservation:
    """A valid observation that was *not* selected, kept for diagnostics.

    It exists so a provider that is not allowed to drive decisions can still
    be seen by operators. It never enters :attr:`SelectionResult.fields`.
    """

    provider_id: str
    observation: FieldObservation

    @property
    def value(self) -> float | None:
        return self.observation.value


@dataclass(frozen=True, slots=True)
class SelectionResult:
    fields: Mapping[str, FieldObservation]
    sources: Mapping[str, SourceIdentity]
    missing_required: frozenset[str]
    excluded: Mapping[str, tuple[str, ...]]
    diagnostic_values: Mapping[str, tuple[DiagnosticObservation, ...]] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))
        object.__setattr__(self, "sources", MappingProxyType(dict(self.sources)))
        object.__setattr__(self, "excluded", MappingProxyType(dict(self.excluded)))
        object.__setattr__(
            self, "diagnostic_values", MappingProxyType(dict(self.diagnostic_values)),
        )


def select_fields(
    frames: Iterable[SourceFrame],
    *,
    device_id: str,
    now: datetime,
    priority: Mapping[str, int],
    policies: Mapping[str, FieldPolicy],
    required_fields: frozenset[str] = frozenset(),
    diagnostic_providers: Iterable[str] = (),
) -> SelectionResult:
    """Choose each field independently; never synthesize missing data as zero.

    Lower priority numbers win. Within one provider, the newest received
    observation wins. Receipt freshness and measurement freshness are tested
    independently; missing measurement time remains explicitly unknown.

    ``diagnostic_providers`` names providers whose otherwise-valid
    observations should be reported in :attr:`SelectionResult.diagnostic_values`
    when they are *not* selected - the diagnostics view for a source that is
    not allowed to drive decisions. It never widens the selection gate: a
    provider outside ``policy.allowed_providers`` is still excluded from
    :attr:`SelectionResult.fields`, and an observation that fails a freshness,
    quality or value check is not reported at all.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    diagnostics_wanted = frozenset(diagnostic_providers)
    candidates: dict[str, list[tuple[int, datetime, FieldObservation, SourceIdentity]]] = {}
    excluded: dict[str, list[str]] = {}
    diagnostics: dict[str, list[DiagnosticObservation]] = {}

    for frame in frames:
        if frame.identity.device_id != device_id:
            excluded.setdefault("device", []).append(
                f"{frame.identity.provider_id}: different device identity"
            )
            continue
        for name, observation in frame.fields.items():
            policy = policies.get(name)
            if policy is None:
                continue
            reason = None
            receipt_age = now_utc - observation.received_at
            if receipt_age < timedelta(0):
                reason = "receipt timestamp is in the future"
            elif receipt_age > policy.max_receipt_age:
                reason = "receipt data is stale"
            elif observation.quality not in policy.allowed_qualities:
                reason = f"quality {observation.quality.value} is not allowed"
            elif observation.value is None:
                reason = "value is unknown"
            elif policy.require_measurement_time and observation.measured_at is None:
                reason = "measurement timestamp is unknown"
            elif observation.measured_at is not None:
                measurement_age = now_utc - observation.measured_at
                if measurement_age < timedelta(0):
                    reason = "measurement timestamp is in the future"
                elif (
                    policy.max_measurement_age is not None
                    and measurement_age > policy.max_measurement_age
                ):
                    reason = "measurement data is stale"
            if reason:
                excluded.setdefault(name, []).append(f"{frame.identity.provider_id}: {reason}")
                continue
            # Per-field source allow-list is enforced as the last gate so the
            # decision reads naturally in the excluded-reasons log. A provider
            # not in ``allowed_providers`` cannot contribute this field even
            # if its freshness, quality and value would otherwise be valid.
            if (
                policy.allowed_providers is not None
                and frame.identity.provider_id not in policy.allowed_providers
            ):
                excluded.setdefault(name, []).append(
                    f"{frame.identity.provider_id}: provider is not allowed for this field"
                )
                if frame.identity.provider_id in diagnostics_wanted:
                    diagnostics.setdefault(name, []).append(
                        DiagnosticObservation(frame.identity.provider_id, observation)
                    )
                continue
            candidates.setdefault(name, []).append((
                priority.get(frame.identity.provider_id, 10_000),
                observation.received_at,
                observation,
                frame.identity,
            ))

    selected: dict[str, FieldObservation] = {}
    selected_sources: dict[str, SourceIdentity] = {}
    for name, values in candidates.items():
        # Respect configured provider order first, then select latest frame
        # from that provider. Unknown providers sort after configured ones.
        chosen = min(values, key=lambda item: (item[0], -item[1].timestamp()))
        selected[name] = chosen[2]
        selected_sources[name] = chosen[3]
    return SelectionResult(
        fields=selected,
        sources=selected_sources,
        missing_required=frozenset(required_fields.difference(selected)),
        excluded={key: tuple(reasons) for key, reasons in excluded.items()},
        diagnostic_values={
            name: tuple(values) for name, values in diagnostics.items()
        },
    )
