"""Verdict logic for 21-word block candidates, with variation requirements.

Round-8 correction. The first version of ``rwb1_block_mapping`` had a real
defect: it accepted a candidate whenever ``|median residual| <= tol`` and
``spread <= tol``. In the observed window ``battery_voltage_v`` varied by
only **0.2 V** across all 9 matched observations, so ANY word+scale whose
values tracked a near-constant field passed with a +3.9 V or -5.0 V offset.
That is a false confirmation: the test cannot distinguish "tracks the field"
from "is a different quantity that happens to be stable".

Two fixes are required before a mapping may be called anything but a
candidate:

  1. VARIATION REQUIREMENT — the HA field itself must actually vary across
     the matched observations (relative spread above ``min_rel_spread``),
     AND the candidate's own values must vary correspondingly. A field with
     zero variation cannot identify anything.
  2. OFFSET REQUIREMENT — with a small relative spread in HA, the median
     residual must also be small *relative to the field's own variation*.
     A constant +5 V offset against a 0.2 V field is a 25-sigma mismatch,
     not a confirmation.

Byte order is still NOT decided here. Both interpretations are evaluated and
both produce confirmations under the loose criterion, which is itself the
finding: the 9 observations are insufficient to separate them. The block
stays a candidate.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class Variation:
    n: int
    distinct: int
    spread: float
    rel_spread: float
    varies: bool


def measure(values, min_rel_spread: float = 0.02,
            abs_floor: float = 1e-9) -> Variation:
    """Describe how much a set of values varies.

    ``rel_spread`` = spread / max(|mean|, abs_floor).
    """
    vals = [float(v) for v in values]
    if not vals:
        return Variation(0, 0, 0.0, 0.0, False)
    spread = max(vals) - min(vals)
    mean = statistics.mean(vals)
    denom = max(abs(mean), abs_floor)
    rel = spread / denom
    return Variation(
        n=len(vals),
        distinct=len(set(vals)),
        spread=spread,
        rel_spread=rel,
        # Compare with a tolerance so a value sitting exactly on the
        # threshold is not flipped by float representation. Without this,
        # a field measured at exactly min_rel_spread would be rejected
        # when it rounds to 0.019999999999999997.
        varies=(spread > 0 and rel >= min_rel_spread - 1e-9),
    )


@dataclass
class StrictVerdict:
    ok: bool
    reason: str
    residual_median: float | None = None
    residual_spread: float | None = None
    ha_variation: Variation | None = None
    cand_variation: Variation | None = None
    offset_in_ha_sigmas: float | None = None


def strict_verdict(
    decoded: list[float],
    ha_values: list[float],
    min_n: int = 5,
    min_rel_spread: float = 0.02,
    max_resid_spread_in_units: float = 5.0,
) -> StrictVerdict:
    """Decide whether a candidate mapping is more than a candidate.

    Guards, in order:
      1. enough independent pairs;
      2. the HA field VARIES (absolute) — otherwise nothing is testable;
      3. the candidate VARIES proportionally to the HA field;
      4. residuals are tight in absolute units;
      5. the median offset is small relative to the HA field's own spread.
    """
    n = len(decoded)
    if n != len(ha_values):
        return StrictVerdict(False, "pair count mismatch")
    if n < min_n:
        return StrictVerdict(False, f"only {n} independent pairs (need {min_n})")

    ha_v = measure(ha_values, min_rel_spread)
    cand_v = measure(decoded, min_rel_spread)

    if ha_v.spread == 0:
        return StrictVerdict(
            False,
            f"HA field does not vary at all (spread=0 across {n}); "
            f"no mapping can be identified",
            ha_variation=ha_v, cand_variation=cand_v,
        )
    if not ha_v.varies:
        return StrictVerdict(
            False,
            f"HA field variation too small to identify: spread={ha_v.spread:.4g}, "
            f"rel={ha_v.rel_spread:.4f} < {min_rel_spread}",
            ha_variation=ha_v, cand_variation=cand_v,
        )
    if not cand_v.varies:
        return StrictVerdict(
            False,
            f"candidate is constant while HA field varies "
            f"(HA spread={ha_v.spread:.4g})",
            ha_variation=ha_v, cand_variation=cand_v,
        )
    # Proportional variation: candidate must move with the field, within
    # a factor of 4 either way. Too flat or too steep means wrong scale.
    ratio = cand_v.rel_spread / ha_v.rel_spread
    if not (0.25 <= ratio <= 4.0):
        return StrictVerdict(
            False,
            f"variation shape mismatch: candidate rel={cand_v.rel_spread:.4f} vs "
            f"HA rel={ha_v.rel_spread:.4f} (ratio {ratio:.2f}, need 0.25-4.0)",
            ha_variation=ha_v, cand_variation=cand_v,
        )

    residuals = [d - h for d, h in zip(decoded, ha_values)]
    med = statistics.median(residuals)
    rspread = max(residuals) - min(residuals)

    # Offset measured in units of the HA field's own spread.
    sigmas = abs(med) / ha_v.spread if ha_v.spread else float("inf")

    if rspread > max_resid_spread_in_units:
        return StrictVerdict(
            False,
            f"residual spread {rspread:.4g} > {max_resid_spread_in_units}",
            med, rspread, ha_v, cand_v, sigmas,
        )
    if sigmas > 1.0:
        return StrictVerdict(
            False,
            f"median offset {med:+.4g} is {sigmas:.1f}x the HA field's own "
            f"spread ({ha_v.spread:.4g}) — a constant offset, not a match",
            med, rspread, ha_v, cand_v, sigmas,
        )
    return StrictVerdict(
        True,
        f"n={n} HA rel={ha_v.rel_spread:.4f} cand rel={cand_v.rel_spread:.4f} "
        f"offset={med:+.4g} ({sigmas:.2f}x HA spread) resid_spread={rspread:.4g}",
        med, rspread, ha_v, cand_v, sigmas,
    )


def compare_orders(observations, ha: dict, ha_fields, scales,
                   **kw) -> dict:
    """Evaluate every candidate under BOTH byte orders and report per order.

    The point is to detect that both orders yield confirmations — which
    means the data cannot decide the order.
    """
    from .rwb1_block_mapping import Candidate  # noqa: F401  (documented)

    result = {"big_endian": [], "little_endian": []}
    for order in ("big_endian", "little_endian"):
        rows = []
        for wi in range(21):
            for scale in scales:
                for hf_i, hf in enumerate(ha_fields):
                    decoded, ha_vals = [], []
                    for _sec, be, le in observations:
                        w = be[wi] if order == "big_endian" else le[wi]
                        v = ha[_sec][hf_i]
                        if v is None:
                            continue
                        decoded.append(w * scale)
                        ha_vals.append(float(v))
                    if not decoded:
                        continue
                    v = strict_verdict(decoded, ha_vals, **kw)
                    rows.append({
                        "word_index": wi, "scale": scale, "ha_field": hf,
                        "confirmed": v.ok, "reason": v.reason,
                        "offset_sigma": v.offset_in_ha_sigmas,
                    })
        result[order] = [r for r in rows if r["confirmed"]]
    return result