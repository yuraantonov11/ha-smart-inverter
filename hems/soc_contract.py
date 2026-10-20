"""Single SOC contract: raw / estimated / quality.

PROPOSAL — not deployed. Nothing here changes live control, modes or
dispatcher behaviour. It adds an explicit reading contract so a consumer
can tell WHAT it is looking at instead of receiving one number whose
provenance is invisible.

Why this exists
---------------
``get_real_soc()`` returns a single float and collapses two different
kinds of knowledge into it:

  * the inverter's reported SOC (``batterySoc`` / ``batteryCapacity``), and
  * a voltage-derived estimate from the 16S LiFePO4 OCV table.

It also picks between them with a hard ±10 % gate, so a change of a few
volts produces a step: on 2026-10-20 the charging-mode transition moved
``batteryVoltage`` 58.0 → 52.8 V and ``corrected_soc`` stepped
100.0 → 80.0 in a single 5-second cycle. Consumers cannot see that the
number changed KIND, not just value.

The contract
------------
Every SOC observation is a ``SocReading`` with:

  raw        the number the device reported (or None if absent)
  estimated  the number a consumer should use for planning
  quality    one of: ``reported`` / ``estimated`` / ``blended`` /
             ``unknown``
  source     where the raw value came from
  reason     why the estimate was chosen, in words

``estimated`` is never presented as if it were ``raw``. A consumer that
needs the BMS number reads ``raw``; one that needs a planning number
reads ``estimated`` and checks ``quality``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Quality = Literal["reported", "estimated", "blended", "unknown"]

# Maximum change in the ESTIMATED series per observation. A genuine
# charge/discharge cycle moves SOC by well under this in five seconds;
# a branch flip moves it by 10-20 points at once. The limiter is
# applied to the estimate only, never to the reported value.
DEFAULT_SLEW_POINTS = 5.0


@dataclass(frozen=True)
class SocReading:
    raw: float | None
    estimated: float | None
    quality: Quality
    source: str
    reason: str
    voltage_v: float | None = None
    voltage_soc: float | None = None
    disagreement: float | None = None
    slew_limited: bool = False

    @property
    def is_estimated(self) -> bool:
        return self.quality in ("estimated", "blended")


def soc_reading(
    reported_soc: float | None,
    voltage: float,
    battery_current: float = 0.0,
    *,
    get_real_soc,
    voltage_to_soc,
    previous_estimated: float | None = None,
    slew_points: float | None = DEFAULT_SLEW_POINTS,
    source: str = "cloud_raw",
) -> SocReading:
    """Build a SocReading from the device report plus voltage."""
    if reported_soc is None:
        return SocReading(
            raw=None, estimated=None, quality="unknown", source=source,
            reason="device reported no SOC",
            voltage_v=voltage,
        )

    raw = max(0.0, min(100.0, float(reported_soc)))
    v_soc = None
    if voltage > 10.0:
        v_soc = voltage_to_soc(voltage - battery_current * 0.0128)

    estimate = float(get_real_soc(raw, voltage, battery_current))
    disagree = None if v_soc is None else abs(raw - v_soc)

    if v_soc is None:
        quality: Quality = "reported"
        reason = "voltage unusable; kept the device report"
    elif disagree <= 10.0:
        quality = "reported"
        reason = "device and voltage agree within 10 points"
    else:
        quality = "estimated"
        reason = (
            f"device and voltage disagree by {disagree:.1f} points; "
            "estimate is voltage-derived, NOT the device value"
        )

    slew_limited = False
    if slew_points is not None and previous_estimated is not None:
        delta = estimate - previous_estimated
        if abs(delta) > slew_points:
            estimate = max(0.0, min(100.0,
                                    previous_estimated
                                    + (slew_points if delta > 0 else -slew_points)))
            slew_limited = True
            reason += (
                f"; step limited to {slew_points:.0f} points "
                f"(was {delta:+.1f})"
            )
            if quality == "reported":
                quality = "blended"

    return SocReading(
        raw=raw,
        estimated=estimate,
        quality=quality,
        source=source,
        reason=reason,
        voltage_v=voltage,
        voltage_soc=v_soc,
        disagreement=disagree,
        slew_limited=slew_limited,
    )


def series(readings: list[SocReading]) -> dict:
    """Summarise a series: raw and estimated tracked separately."""
    raw = [r.raw for r in readings if r.raw is not None]
    est = [r.estimated for r in readings if r.estimated is not None]
    steps_raw = [b - a for a, b in zip(raw, raw[1:])]
    steps_est = [b - a for a, b in zip(est, est[1:])]
    return {
        "n": len(readings),
        "raw_steps": steps_raw,
        "raw_max_step": max((abs(s) for s in steps_raw), default=0.0),
        "estimated_steps": steps_est,
        "estimated_max_step": max((abs(s) for s in steps_est), default=0.0),
        "quality_changes": [
            (a.quality, b.quality) for a, b in zip(readings, readings[1:])
            if a.quality != b.quality
        ],
    }