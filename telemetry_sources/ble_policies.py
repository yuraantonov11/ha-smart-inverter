"""Per-field evidence-anchored policies for the experimental RWB1 BLE source.

This module is the *only* place where a per-field rule says which provider may
contribute a value to a decision. It encodes the evidence level recorded in
``rwb1_ble_profile.FIELD_MAP`` and in ``docs/rwb1-ble-experimental.md``.
Updating the policy here is the correct way to promote or demote a field once
new captures justify it; the rest of the system picks up the change without
further edits.

Current position (Юра, 2026-10): **every** BLE field is diagnostic only. Two
voltage readings that happen to land close to each other is not yet sufficient
validation for control, so the two previously corroborated fields -
``grid_voltage`` and ``battery_voltage`` - are no longer selectable from BLE
either. :func:`ble_policies` refuses to return a policy that allows the BLE
provider at all, so a future edit that re-promotes a field fails loudly (and
its test) instead of quietly feeding HEMS.

BLE observations stay *visible* for diagnostics:

* :func:`ble_diagnostic_values` returns the raw BLE observations of a frame
  set, for display and evidence work;
* :func:`telemetry_sources.selection.select_fields` accepts
  ``diagnostic_providers=`` and reports the observations it refused to select
  in ``SelectionResult.diagnostic_values``.

Both are display-only by construction: diagnostics never enter
``SelectionResult.fields`` and therefore can never reach a control path.

Evidence anchors behind the per-field rules (all from the user's hardware;
see ``docs/rwb1-ble-experimental.md`` for the full table)::

* ``grid_voltage``     — 232.0 V (BLE) vs 231.8 V (cloud): close, but a single
                         comparison is not cross-validation.
* ``battery_voltage``  —  58.1 V (BLE) vs  57.8 V (cloud): same caveat.
* ``pv_power``         — two BLE reads only, unconfirmed (same evidence class
                         as ``load_power`` and ``battery_soc``).
* ``battery_soc``      — 100 % (BLE) vs 95 % (cloud), no cross-check with
                         the inverter display or mobile app.
* ``load_power``       — 301 W (BLE) vs 398 W (cloud), no cross-check.
* ``battery_current``  — **not mapped** in ``rwb1_ble_profile.FIELD_MAP``;
                         deliberately omitted here so the gate must be
                         re-decided whenever the mapping materialises
                         (default: BLE = blocked until revalidated).

The contract is intentionally small: ``ble_policies()`` returns a fresh
``dict[str, FieldPolicy]`` mapping *canonical* field names to
:class:`FieldPolicy` values. Each policy declares an ``allowed_providers``
set; for every field declared here that set is exactly the cloud.
Untouched fields keep behaving as if no per-source restriction existed.

All numeric values (max receipt age, max measurement age, etc.) match the
values used by the existing cloud-only policy tables so promoting a field
later does not require tuning again.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Mapping

from collections.abc import Iterable

from .models import FieldObservation, SourceFrame
from .rwb1_ble_profile import FIELD_BY_NAME
from .selection import FieldPolicy
from .settings import BLE_PROVIDER_ID, CLOUD_PROVIDER_ID


#: The only provider that may be *selected* from for any declared field.
_CLOUD_ONLY: frozenset[str] = frozenset({CLOUD_PROVIDER_ID})

#: Fields the experimental BLE transport may report, for diagnostics only.
#: Mirrors ``rwb1_ble_profile.FIELD_MAP`` - the set is the profile's, never a
#: second hand-maintained list.
BLE_DIAGNOSTIC_FIELDS: frozenset[str] = frozenset(FIELD_BY_NAME)

_FRESHNESS = dict(
    max_receipt_age=timedelta(seconds=10),
    max_measurement_age=timedelta(seconds=30),
    require_measurement_time=False,
)


def ble_diagnostic_candidates() -> frozenset[str]:
    """Field names BLE may produce for diagnostics; never for selection."""
    return BLE_DIAGNOSTIC_FIELDS


def ble_diagnostic_values(
    frames: Iterable[SourceFrame],
    *,
    device_id: str,
) -> Mapping[str, FieldObservation]:
    """Newest BLE observation per field, for display and evidence work only.

    Frames from another device or another provider are ignored, as are
    observations without a value. The result is a plain diagnostic view: it is
    never fed back into :func:`telemetry_sources.selection.select_fields`, and
    it deliberately bypasses the freshness gates so an operator can see *how
    stale* a BLE reading is.
    """
    newest: dict[str, FieldObservation] = {}
    for frame in frames:
        if frame.identity.provider_id != BLE_PROVIDER_ID:
            continue
        if frame.identity.device_id != device_id:
            continue
        for name, observation in frame.fields.items():
            if observation.value is None:
                continue
            previous = newest.get(name)
            if previous is None or observation.received_at >= previous.received_at:
                newest[name] = observation
    return newest


class Rwb1BlePolicyError(RuntimeError):
    """A policy would have let BLE drive a decision."""


def _cloud_only_policy() -> FieldPolicy:
    return FieldPolicy(allowed_providers=_CLOUD_ONLY, **_FRESHNESS)


def ble_policies() -> Mapping[str, FieldPolicy]:
    """Return a fresh per-field policy map reflecting the current evidence.

    The returned mapping is intentionally read-only by convention; callers
    must not mutate it. Tests and production wiring consume it identically
    via :func:`telemetry_sources.selection.select_fields`.
    """
    policies: dict[str, FieldPolicy] = {
        name: _cloud_only_policy() for name in sorted(BLE_DIAGNOSTIC_FIELDS)
    }
    # Invariant, not documentation: a BLE-allowing policy must never leave this
    # module. Selection correctness must not depend on a reviewer remembering.
    for name, policy in policies.items():
        if BLE_PROVIDER_ID in (policy.allowed_providers or frozenset()):
            raise Rwb1BlePolicyError(
                f"{name} may not be selected from {BLE_PROVIDER_ID}: every BLE "
                "field is diagnostic only until cross-validated"
            )
    return policies


__all__ = [
    "BLE_DIAGNOSTIC_FIELDS",
    "Rwb1BlePolicyError",
    "ble_diagnostic_candidates",
    "ble_diagnostic_values",
    "ble_policies",
]
