"""Transport-neutral collection and failover across read-only providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from .models import SourceFrame, TelemetryProvider
from .selection import FieldPolicy, SelectionResult, select_fields
from .settings import TelemetrySettings


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    """Safe provider diagnostic; deliberately excludes exception text."""

    provider_id: str
    error_type: str


@dataclass(frozen=True, slots=True)
class TelemetryRead:
    """One collection pass, including partial-source failures."""

    selection: SelectionResult
    provider_failures: tuple[ProviderFailure, ...]
    providers_read: int


class TelemetrySourceRouter:
    """Read all configured providers and select valid values per field.

    Providers are polled sequentially so this module stays independent of HA
    and event-loop implementations. Network providers must enforce their own
    connection/read deadlines. A failure from one provider is isolated; other
    sources may still satisfy some or all fields.

    ``diagnostic_providers`` is forwarded to
    :func:`telemetry_sources.selection.select_fields`: observations from those
    providers are reported in ``SelectionResult.diagnostic_values`` even when
    the per-field policy refuses to select them. Display only - it never widens
    the selection gate.
    """

    @classmethod
    def from_settings(
        cls,
        providers: Iterable[TelemetryProvider],
        *,
        settings: TelemetrySettings,
        policies: Mapping[str, FieldPolicy],
        required_fields: frozenset[str] = frozenset(),
        diagnostic_providers: Iterable[str] = (),
    ) -> TelemetrySourceRouter:
        """Exclude non-opted-in sources entirely, even as field fallbacks.

        The caller owns any providers it creates but does not pass through.
        Prefer creating only the configured providers in production wiring.
        """
        supplied = tuple(providers)
        by_id = {provider.identity.provider_id: provider for provider in supplied}
        if len(by_id) != len(supplied):
            raise ValueError("provider_id values must be unique")
        enabled = tuple(by_id[key] for key in settings.provider_order if key in by_id)
        return cls(
            enabled,
            priority={provider.identity.provider_id: index for index, provider in enumerate(enabled)},
            policies=policies,
            required_fields=required_fields,
            diagnostic_providers=diagnostic_providers,
        )

    def __init__(
        self,
        providers: Iterable[TelemetryProvider],
        *,
        priority: Mapping[str, int],
        policies: Mapping[str, FieldPolicy],
        required_fields: frozenset[str] = frozenset(),
        diagnostic_providers: Iterable[str] = (),
    ) -> None:
        self._providers = tuple(providers)
        provider_ids = [provider.identity.provider_id for provider in self._providers]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("provider_id values must be unique")
        if set(priority).difference(provider_ids):
            raise ValueError("priority contains an unconfigured provider")
        self._priority = dict(priority)
        self._policies = dict(policies)
        self._required_fields = frozenset(required_fields)
        self._diagnostic_providers = frozenset(diagnostic_providers)
        self._closed = False

    async def collect(self, *, device_id: str, now: datetime) -> TelemetryRead:
        if self._closed:
            raise RuntimeError("telemetry source router is closed")

        frames: list[SourceFrame] = []
        failures: list[ProviderFailure] = []
        for provider in self._providers:
            provider_id = provider.identity.provider_id
            try:
                frame = await provider.read()
                if frame.identity != provider.identity:
                    failures.append(ProviderFailure(provider_id, "IdentityMismatch"))
                    continue
                frames.append(frame)
            except Exception as err:  # noqa: BLE001 - isolate one source failure.
                failures.append(ProviderFailure(provider_id, type(err).__name__))

        return TelemetryRead(
            selection=select_fields(
                frames,
                device_id=device_id,
                now=now,
                priority=self._priority,
                policies=self._policies,
                required_fields=self._required_fields,
                diagnostic_providers=self._diagnostic_providers,
            ),
            provider_failures=tuple(failures),
            providers_read=len(frames),
        )

    async def close(self) -> tuple[ProviderFailure, ...]:
        """Close every provider once; return safe diagnostics for failures."""
        if self._closed:
            return ()
        self._closed = True
        failures: list[ProviderFailure] = []
        for provider in self._providers:
            try:
                await provider.close()
            except Exception as err:  # noqa: BLE001 - continue closing others.
                failures.append(
                    ProviderFailure(provider.identity.provider_id, type(err).__name__)
                )
        return tuple(failures)
