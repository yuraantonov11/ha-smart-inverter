"""SOC reading contract: reported / validated / control / display.

REJECTED: the previous slew-limited proposal in this file. It was unsafe.
With previous_estimated=80, reported=25, voltage=51 it returned 75.0 for
planning — a 50-point INFLATION of a critically low battery — and the
limit was frequency-dependent (5 s cycle -> 25 % after 60 s; 60 s cycle
-> 75 % after 5 s). It also reduced a visible jump without improving the
truth of the decision. Withdrawn and replaced by what follows.

This module has FOUR separate quantities and no smoothing anywhere near
the control path.

    reported   the device value, as received, never altered. Present
               whenever the device sent one, even if invalid, so a
               consumer can see what actually arrived.
    valid      whether that value is usable at all (finite, in range).
               A value can be present and invalid.
    control    what a DECISION may use. None when nothing is
               trustworthy — for a planner that is the safe answer;
               inventing a number is not.
    display    optional cosmetic smoothing, explicitly NOT for control,
               never fed to HEMS or the planner.

Why the voltage estimate needs a rest gate
------------------------------------------
An OCV table describes a battery AT REST. Under load the terminal voltage
is not OCV, so a table lookup while the pack is working systematically
mis-reports SOC. That is exactly the 2026-10-20 failure: the
charging-mode transition moved batteryVoltage 58.0 -> 52.8 V, the table
mapped 52.8 V to 80 %, and corrected_soc stepped 100 -> 80 while the
device kept reporting 100.

The table is admissible only when the pack is quiescent: current near
zero AND voltage inside a sane band. Otherwise no estimate is offered.

Reported origin is not a trust guarantee either. On this device the
reported value was a constant 100.0 across a 5.2 V change — "the device
said so" is precisely the claim that failed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

Quality = Literal[
    "reported_valid",      # device value present and usable
    "reported_invalid",    # device value present but not usable
    "voltage_estimate",    # device unusable, rest-confirmed table value
    "unknown",             # nothing usable
]

# A pack moving current is not at rest. |I| below this is "quiescent
# enough" for a first-order table. A heuristic, documented as one.
QUIESCENT_CURRENT_A = 1.0

# Outside this band the reading is either a deeply asleep pack or not a
# battery voltage at all.
SANE_VOLTURE_V = (40.0, 65.0)


@dataclass(frozen=True)
class SocReading:
    reported: float | None
    reported_valid: bool
    control: float | None
    quality: Quality
    reason: str
    display: float | None = None
    voltage_v: float | None = None
    battery_current_a: float | None = None
    voltage_soc: float | None = None
    quiescent: bool | None = None

    @property
    def usable(self) -> bool:
        return self.control is not None


def _as_float_or_none(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _is_valid_reported(value) -> bool:
    """Finite, numeric, inside 0..100. No coercion, no substitution."""
    if value is None or isinstance(value, bool):
        return False
    f = _as_float_or_none(value)
    if f is None:
        return False
    return 0.0 <= f <= 100.0


def evaluate(reported_soc, voltage_v, battery_current_a, *, voltage_to_soc) -> SocReading:
    """Classify one observation. No smoothing, no interpolation.

    `voltage_to_soc` is injected so this module never duplicates the
    production OCV table.
    """
    v = _as_float_or_none(voltage_v)
    i = _as_float_or_none(battery_current_a)

    rep_present = reported_soc is not None
    rep_valid = _is_valid_reported(reported_soc)

    quiescent = None
    v_soc = None
    if v is not None and i is not None:
        sane = SANE_VOLTURE_V[0] <= v <= SANE_VOLTURE_V[1]
        quiescent = sane and abs(i) <= QUIESCENT_CURRENT_A
        if quiescent:
            v_soc = voltage_to_soc(v)

    if rep_valid:
        return SocReading(
            reported=float(reported_soc),
            reported_valid=True,
            control=float(reported_soc),
            quality="reported_valid",
            reason="device value finite and within 0..100",
            voltage_v=v, battery_current_a=i,
            voltage_soc=v_soc, quiescent=quiescent,
        )

    if rep_present and not rep_valid:
        # The device SAID something but it was unusable. Substituting a
        # voltage estimate here would silently coerce a broken reading
        # into a "trustworthy" SOC. Refuse instead — the device's
        # gesture is the smoking gun; consumers must see it.
        extra = ("" if quiescent else
                 "; pack not at rest, so the OCV table is NOT admissible")
        return SocReading(
            reported=reported_soc,
            reported_valid=False,
            control=None,
            quality="reported_invalid",
            reason=("device value present but invalid "
                    "(NaN/Infinity/out of range)" + extra
                    + " -> no trustworthy SOC, voltage estimate NOT offered"),
            voltage_v=v, battery_current_a=i,
            voltage_soc=v_soc, quiescent=quiescent,
        )

    if v_soc is not None:
        return SocReading(
            reported=None,
            reported_valid=False,
            control=float(v_soc),
            quality="voltage_estimate",
            reason=("device reported no SOC; pack quiescent so the OCV table "
                    "is admissible. ESTIMATE, not a measurement."),
            voltage_v=v, battery_current_a=i,
            voltage_soc=v_soc, quiescent=quiescent,
        )

    return SocReading(
        reported=None,
        reported_valid=False,
        control=None,
        quality="unknown",
        reason="device reported no SOC and no admissible voltage estimate",
        voltage_v=v, battery_current_a=i,
        voltage_soc=v_soc, quiescent=quiescent,
    )


@dataclass
class DisplaySmoother:
    """COSMETIC ONLY. Never feed its output to HEMS or the planner.

    Kept separate on purpose: a display may be pleasant to look at and
    still be a poor basis for a decision. Explicitly not a correctness
    mechanism.
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
    """Attach a cosmetic smoothed value. Control is untouched."""
    d = smoother.update(reading.control)
    return SocReading(
        reported=reading.reported,
        reported_valid=reading.reported_valid,
        control=reading.control,
        quality=reading.quality,
        reason=reading.reason,
        display=d,
        voltage_v=reading.voltage_v,
        battery_current_a=reading.battery_current_a,
        voltage_soc=reading.voltage_soc,
        quiescent=reading.quiescent,
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
    }