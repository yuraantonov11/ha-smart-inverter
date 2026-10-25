"""Opt-in telemetry settings: existing entries always remain cloud-only."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


CLOUD_PROVIDER_ID = "powmr_cloud"
BLE_PROVIDER_ID = "rwb1_ble"
"""Provider identifier used by ``SourceMode.BLE``.

A real opt-in path requires three things to come together: an explicit
``mode == "ble"`` submission, a non-empty ``provider_id`` matching
:meth:`Rwb1BleTelemetryProvider.identity.provider_id`, and a positive
:data:`EXPERIMENTAL_BLE_CONSENT_FLAG` flag. The default ``provider_order``
keeps the cloud as the primary reader so BLE can never silently win a
priority contest for an unconfirmed field.
"""


EXPERIMENTAL_BLE_CONSENT_FLAG = "experimental_ble_consent"
"""Submission field name the wizard must set to True to enable BLE mode.

Documented here so the consent key stays a single source of truth: a future
config flow that forgets to set this flag is rejected by
:meth:`validate_telemetry_settings` rather than silently falling back to
cloud.
"""


class SourceMode(str, Enum):
    CLOUD = "cloud"
    ROUTER = "router"
    # Experimental BLE opt-in. Requires an explicit provider_id (typically
    # ``BLE_PROVIDER_ID``) and the consent flag. ``provider_order`` keeps
    # cloud as the primary reader so BLE can never silently take priority.
    BLE = "ble"
    # Internal recovery state, never offered by the setup wizard.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class TelemetrySettings:
    mode: SourceMode = SourceMode.CLOUD
    collector_id: str | None = None
    cloud_fallback: bool = True
    experimental_ble_consent: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.mode, SourceMode):
            raise ValueError("unsupported telemetry mode")
        if type(self.cloud_fallback) is not bool:
            raise ValueError("cloud_fallback must be boolean")
        if type(self.experimental_ble_consent) is not bool:
            raise ValueError("experimental_ble_consent must be boolean")
        if self.mode is SourceMode.ROUTER:
            if (
                not isinstance(self.collector_id, str)
                or not self.collector_id.strip()
                or self.collector_id != self.collector_id.strip()
                or len(self.collector_id) > 128
                or self.collector_id == CLOUD_PROVIDER_ID
            ):
                raise ValueError("router mode requires a distinct collector ID")
        if self.mode is SourceMode.BLE:
            if not self.experimental_ble_consent:
                raise ValueError(
                    "ble mode requires explicit experimental consent"
                )
            if (
                not isinstance(self.collector_id, str)
                or not self.collector_id.strip()
                or self.collector_id != self.collector_id.strip()
                or len(self.collector_id) > 128
                or self.collector_id == CLOUD_PROVIDER_ID
            ):
                raise ValueError(
                    "ble mode requires a distinct provider_id distinct from the cloud"
                )

    @property
    def provider_order(self) -> tuple[str, ...]:
        """Only these providers may be read, including during failure.

        For all modes, the cloud is included implicitly when ``cloud_fallback``
        is set; for BLE mode, the cloud is **always** primary regardless of
        that flag, so a misconfigured BLE entry can never silently win.
        """
        if self.mode is SourceMode.CLOUD:
            return (CLOUD_PROVIDER_ID,)
        if self.mode is SourceMode.UNAVAILABLE:
            return ()
        if self.mode is SourceMode.BLE:
            assert self.collector_id is not None
            return (CLOUD_PROVIDER_ID, self.collector_id)
        assert self.collector_id is not None
        return (
            (self.collector_id, CLOUD_PROVIDER_ID)
            if self.cloud_fallback else (self.collector_id,)
        )


def validate_telemetry_settings(data: Mapping[str, Any]) -> TelemetrySettings:
    """Validate a wizard submission before saving; no connection side effects.

    The wizard must independently verify device identity, the decoder profile,
    and fresh field coverage before activating a router collector. BLE mode
    additionally requires the consent flag and a non-cloud provider_id.
    """
    mode = SourceMode(data.get("mode", SourceMode.CLOUD.value))
    if mode is SourceMode.UNAVAILABLE:
        raise ValueError("unavailable is a runtime state, not a source choice")
    if mode is SourceMode.CLOUD:
        # Disabled collector leftovers never prevent returning to cloud mode.
        return TelemetrySettings()
    consent = data.get(EXPERIMENTAL_BLE_CONSENT_FLAG, False)
    raw_fallback = data.get("cloud_fallback", True)
    return TelemetrySettings(
        mode=mode,
        collector_id=data.get("collector_id"),
        # Preserve the original value so the dataclass __post_init__ can
        # reject non-booleans (e.g. "false" or "true") the same way as before.
        cloud_fallback=raw_fallback,
        experimental_ble_consent=consent,
    )


@dataclass(frozen=True, slots=True)
class LoadedTelemetrySettings:
    settings: TelemetrySettings
    issue: str | None = None


def load_telemetry_settings(options: Mapping[str, Any]) -> LoadedTelemetrySettings:
    """Read saved options without making optional setup fatal for HA startup.

    No ``telemetry`` section is a normal existing installation, not an error.
    Invalid optional settings carry a diagnostic code for the future runtime/
    Repairs UI. They recover to cloud unless the entry explicitly disallows
    cloud fallback, in which case no telemetry provider is enabled. The stored
    entry is not modified here. This policy controls telemetry only; other
    existing integration functions may still use the vendor cloud.
    """
    data = options.get("telemetry")
    if data is None:
        return LoadedTelemetrySettings(TelemetrySettings())
    if not isinstance(data, Mapping):
        return LoadedTelemetrySettings(TelemetrySettings(), "invalid_telemetry_settings")
    try:
        return LoadedTelemetrySettings(validate_telemetry_settings(data))
    except (TypeError, ValueError):
        if data.get("cloud_fallback") is False:
            # Corrupt local configuration must not undo an explicit opt-out.
            return LoadedTelemetrySettings(
                TelemetrySettings(mode=SourceMode.UNAVAILABLE, cloud_fallback=False),
                "invalid_telemetry_settings",
            )
        return LoadedTelemetrySettings(TelemetrySettings(), "invalid_telemetry_settings")
