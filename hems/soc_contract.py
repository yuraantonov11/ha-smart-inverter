"""SOC reading contract: payload / validated / diagnostic / control / display.

PRE-CONNECTION STATE. Nothing here is connected to production. Before the
producer at ``hems/telemetry.py`` and ``services/outage_runtime.py``
adopts this contract, the OCV-table conditions are NOT validated, so:

  * voltage-derived values are DIAGNOSTIC ONLY,
  * ``control`` is ``None`` for any observation that lacks a numerically
    valid ``reported`` payload,
  * ``display`` never reaches ``control`` and is documented as cosmetic.

What the contract splits apart
------------------------------
A single SOC observation is now five distinct quantities, never collapsed
into one. Numerical validity is NOT proof of truth.

    reported           verbatim device value, byte-for-byte. Always
                       present whenever the device sent one, even if
                       invalid, so a consumer can see what arrived.
    reported_payload   the same value normalised to ``float`` when
                       possible (None otherwise). It is the
                       device-originated number, NOT a trust verdict.
    validated          the result of a numerical-only check
                       (finite, 0..100). Validity is a property of the
                       bytes, not of the battery. The SOC can be
                       numerically valid AND WRONG (e.g. a stuck-BMS
                       100 % was observed on 2026-10-20).
    control            what a DECISION may use. ``None`` when nothing
                       is trustworthy. Inventing a number is not a
                       safe default for a planner.
    display            optional cosmetic smoothing. Explicitly NOT
                       for control, never fed to HEMS or the planner.

Why voltage stays diagnostic
----------------------------
An OCV table describes a battery AT REST. Conditions for admissibility
include (at minimum) a quiescent current, a sane voltage band, a minimum
dwell time at rest, a temperature-corrected table and the absence of a
charge-cycle pulse. A ``|I| <= 1 A`` threshold alone is INSUFFICIENT to
declare a pack at rest; it merely rules out a heavy load right now.
Until the table-specific conditions are validated for this device, the
voltage lookup is offered as a diagnostic number (``voltage_soc``) and
is NEVER fed to ``control``.

What the old code did that this contract refuses to do
-------------------------------------------------------
* Silently coerced NaN / Infinity / out-of-range ``reported`` into a
  plausible value via the voltage table.
* Recovered from ``corrected_soc = None`` with ``batterySoc`` at
  ``services/outage_runtime.py:350-351`` -- that fallback is what the
  producer-side adoption has to remove.
* Substituted a smoothed estimate for a critical-low reported value
  (the 25 -> 75 inflation).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

Quality = Literal[
    "reported_valid",     # reported payload exists and is numerically OK
    "reported_invalid",   # reported payload exists but fails numeric check
    "no_reported",        # no payload at all
]

# A pack drawing or supplying current is not at rest. This is a FIRST
# HINT, not a confirmation. The full rest condition includes dwell time,
# temperature, the absence of recent charge pulses and a sane voltage
# band -- none of which we have validated for this device.
QUIESCENT_CURRENT_A = 1.0
SANE_VOLTURE_V = (40.0, 65.0)


@dataclass(frozen=True)
class SocReading:
    reported: object            # verbatim; can be None, float, NaN, etc.
    reported_payload: float | None  # normalised to float when possible
    validated: float | None     # finite && 0..100; numerical validity only
    control: float | None       # decision-eligible; None until trusted
    quality: Quality
    reason: str

    display: float | None = None
    voltage_v: float | None = None
    battery_current_a: float | None = None
    voltage_soc: float | None = None       # DIAGNOSTIC only
    quiescent_now: bool | None = None      # current threshold only
    rest_conditions_confirmed: bool = False  # always False in this build

    @property
    def usable(self) -> bool:
        return self.control is not None

    @property
    def is_diagnostic_only(self) -> bool:
        return self.control is None


def _as_float_or_none(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def evaluate(
    reported_soc,
    voltage_v,
    battery_current_a,
    *,
    voltage_to_soc=None,
) -> SocReading:
    """Classify one observation. NEVER invents ``control``.

    `voltage_to_soc` is optional and kept only to populate the
    diagnostic field; ``control`` is NEVER derived from it.
    """
    v = _as_float_or_none(voltage_v)
    i = _as_float_or_none(battery_current_a)

    rep_present = reported_soc is not None
    # Explicit rejection of ``bool``. Python converts ``True`` to 1.0
    # and ``False`` to 0.0 -- both numerically in-range, but neither
    # is a device measurement. A 1 % SOC and a 0 % SOC must never
    # come from a truthy/falsy flag in the codebase.
    is_bool = isinstance(reported_soc, bool)
    payload = (None if (not rep_present or is_bool)
               else _as_float_or_none(reported_soc))
    validated = (
        None if is_bool or payload is None or not (0.0 <= payload <= 100.0)
        else payload
    )

    quiescent_now = None
    voltage_soc = None
    if v is not None and i is not None and voltage_to_soc is not None:
        sane = SANE_VOLTURE_V[0] <= v <= SANE_VOLTURE_V[1]
        quiescent_now = sane and abs(i) <= QUIESCENT_CURRENT_A
        if quiescent_now:
            # Diagnostic only. NEVER reach into ``control``.
            voltage_soc = voltage_to_soc(v)

    if not rep_present:
        return SocReading(
            reported=None,
            reported_payload=None,
            validated=None,
            control=None,
            quality="no_reported",
            reason=("no device payload; voltage estimate is DIAGNOSTIC "
                    "and is NOT promoted to control until rest conditions "
                    "are confirmed"),
            voltage_v=v, battery_current_a=i,
            voltage_soc=voltage_soc, quiescent_now=quiescent_now,
        )

    if validated is None:
        return SocReading(
            reported=reported_soc,
            reported_payload=payload,
            validated=None,
            control=None,
            quality="reported_invalid",
            reason=("device payload failed numerical check "
                    "(NaN/Inf/out of range); no trustworthy SOC"),
            voltage_v=v, battery_current_a=i,
            voltage_soc=voltage_soc, quiescent_now=quiescent_now,
        )

    # Numerical validity is NOT a trust guarantee, but it IS the only
    # thing we have without an independent source. The producer keeps
    # the door open to add a second-source check or to gate on a known
    # battery profile before treating ``validated`` as ``control``.
    return SocReading(
        reported=reported_soc,
        reported_payload=payload,
        validated=validated,
        control=validated,
        quality="reported_valid",
        reason=("device payload is finite and within 0..100; "
                "numerical validity, NOT a trust verdict"),
        voltage_v=v, battery_current_a=i,
        voltage_soc=voltage_soc, quiescent_now=quiescent_now,
    )


@dataclass
class DisplaySmoother:
    """COSMETIC ONLY. ``update.display`` is never fed to ``control``.

    Its alpha is a UI knob; it does not prove anything about the value.
    """
    alpha: float = 0.3
    state: float | None = field(default=None)

    def update(self, control: float | None) -> float | None:
        if control is None:
            return self.state
        if self.state is None:
            self.state = control
            return control
        self.state = (1 - self.alpha) * self.state + self.alpha * control
        return self.state

    def reset(self) -> None:
        self.state = None


def with_display(reading: SocReading, smoother: DisplaySmoother) -> SocReading:
    """Attach a cosmetic smoothed value. ``control`` is untouched."""
    d = smoother.update(reading.control)
    return SocReading(
        reported=reading.reported,
        reported_payload=reading.reported_payload,
        validated=reading.validated,
        control=reading.control,
        quality=reading.quality,
        reason=reading.reason,
        display=d,
        voltage_v=reading.voltage_v,
        battery_current_a=reading.battery_current_a,
        voltage_soc=reading.voltage_soc,
        quiescent_now=reading.quiescent_now,
        rest_conditions_confirmed=reading.rest_conditions_confirmed,
    )


def summarise(readings: list[SocReading]) -> dict:
    ctrl = [r.control for r in readings if r.control is not None]
    steps = [b - a for a, b in zip(ctrl, ctrl[1:])]
    return {
        "n": len(readings),
        "with_control": len(ctrl),
        "without_control": len(readings) - len(ctrl),
        "control_max_step_pp": max((abs(s) for s in steps), default=0.0),
        "control_steps_gt_5pp": sum(1 for s in steps if abs(s) > 5.0),
        "quality_counts": {q: sum(1 for r in readings if r.quality == q)
                           for q in sorted({r.quality for r in readings})},
        "diagnostic_only_count": sum(1 for r in readings if r.is_diagnostic_only),
        "any_voltage_soc_consulted": sum(1 for r in readings
                                         if r.voltage_soc is not None),
    }