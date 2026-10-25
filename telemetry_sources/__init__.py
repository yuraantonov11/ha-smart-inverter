"""Transport-neutral, read-only telemetry contracts.

This package is intentionally independent of Home Assistant and of the
integration's command/control path.
"""

from .models import (
    FieldObservation,
    SourceFrame,
    SourceIdentity,
    SourceKind,
    TelemetryProvider,
    ValueQuality,
)
from .cloud import cloud_payload_to_frame
from .selection import (
    DiagnosticObservation,
    FieldPolicy,
    SelectionResult,
    select_fields,
)
from .ble_policies import (
    BLE_DIAGNOSTIC_FIELDS,
    ble_diagnostic_candidates,
    ble_diagnostic_values,
    ble_policies,
)
from .ha_entities import HomeAssistantEntityProvider
from .router import ProviderFailure, TelemetryRead, TelemetrySourceRouter
from .settings import (
    BLE_PROVIDER_ID,
    EXPERIMENTAL_BLE_CONSENT_FLAG,
    LoadedTelemetrySettings,
    SourceMode,
    TelemetrySettings,
    load_telemetry_settings,
    validate_telemetry_settings,
)

__all__ = [
    "DiagnosticObservation", "FieldObservation", "FieldPolicy", "HomeAssistantEntityProvider", "ProviderFailure",
    "SelectionResult", "SourceFrame", "SourceIdentity", "SourceKind", "TelemetryProvider",
    "TelemetryRead", "TelemetrySourceRouter", "ValueQuality", "cloud_payload_to_frame",
    "BLE_DIAGNOSTIC_FIELDS", "BLE_PROVIDER_ID", "EXPERIMENTAL_BLE_CONSENT_FLAG",
    "LoadedTelemetrySettings", "SourceMode", "TelemetrySettings",
    "ble_diagnostic_candidates", "ble_diagnostic_values", "ble_policies",
    "load_telemetry_settings", "validate_telemetry_settings",
    "select_fields",
]
