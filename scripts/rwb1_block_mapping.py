"""Independent matching of the 21-word PS4Z block against HA telemetry.

Юра: «21-word mapping залиш кандидатом до повторних незалежних зіставлень.»

This module exists to RE-DERIVE the 21-word mapping from scratch, so the
conclusion does not rest on the earlier analysis that produced it. It:

  * parses the block independently (own decoder, not the inspect script's),
  * applies only the one byte order that is already cross-checked
    (little-endian, confirmed because word[1]/10 matched a HA grid voltage
    at an identical second),
  * requires an EXACT second match within tolerance, never nearest-match,
  * computes per-field agreement statistics: how many independent
    observations support each candidate (offset, scale) mapping.

A candidate mapping is promoted past "candidate" only when it is supported
by N independent matched pairs AND the residual is below a tolerance. Until
then every field stays unconfirmed and must not reach HEMS.

Byte order of the 21-word block is treated as FIXED for this module on the
basis of the documented cross-check. The RPC registers are NOT decoded here:
their byte order is unconfirmed, and applying the block's order to them
would be exactly the mistake under review.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone


def parse_ts(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_ha_cycles(path) -> dict:
    """HA cycles keyed by epoch second -> (load, grid_v, batt_v, soc, ts)."""
    out: dict[int, tuple] = {}
    with open(path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("event_type") != "cycle":
                continue
            t = parse_ts(d.get("event_timestamp"))
            if t is None:
                continue
            st = d.get("state") or {}
            out[int(t.timestamp())] = (
                st.get("load_power_w"),
                st.get("grid_voltage_v"),
                st.get("battery_voltage_v"),
                st.get("corrected_soc"),
                d.get("event_timestamp"),
            )
    return out


def decode_21_word_le(words: list[int]) -> list[float]:
    """Decode a 21-word block. Little-endian word order is FIXED here.

    Scale is deliberately NOT applied: the caller must try candidate
    scales explicitly so a wrong scale cannot hide behind a default.
    """
    return [float(w) for w in words]


@dataclass
class Candidate:
    """A candidate mapping: word index + scale -> a HA field."""
    word_index: int
    scale: float
    ha_field: str
    pairs: list = field(default_factory=list)   # (decoded, ha_value)

    @property
    def n(self) -> int:
        return len(self.pairs)

    def residuals(self) -> list[float]:
        return [d - h for d, h in self.pairs]

    def verdict(self, min_n: int = 5, tol: float = 5.0) -> tuple[bool, str]:
        """Promote only with enough independent pairs and a tight residual.

        `tol` is in the HA field's own unit, so it is a physical claim,
        not a statistical fudge.
        """
        if self.n < min_n:
            return False, f"only {self.n} independent pairs (need {min_n})"
        r = self.residuals()
        med = statistics.median(r)
        spread = max(r) - min(r)
        if abs(med) > tol or spread > tol:
            return False, (
                f"median residual {med:+.2f}, spread {spread:.2f} both exceed {tol}"
            )
        return True, (
            f"n={self.n} median residual {med:+.3f}, spread {spread:.3f} <= {tol}"
        )


# HA fields the block could plausibly carry, with the units they use.
HA_FIELDS = ("load_power_w", "grid_voltage_v", "battery_voltage_v", "corrected_soc")

# Scales worth trying: 1, 1/10, 1/100, 1/1000, 10, 100.
SCALES = (1.0, 0.1, 0.01, 0.001, 10.0, 100.0)


def build_candidates(
    observations: list[tuple[int, list[float]]],
    ha: dict[int, tuple],
    ha_fields=HA_FIELDS,
    scales=SCALES,
) -> dict[str, list[Candidate]]:
    """For every (word_index, scale, ha_field) triple, collect the pairs.

    `observations` is [(epoch_second_of_RWB1_ts, decoded_words)] — only
    exact-second matched observations may be passed in; the caller is
    responsible for the tolerance decision.
    """
    cands: dict[str, list[Candidate]] = {}
    for word_index in range(21):
        for scale in scales:
            for hf in ha_fields:
                c = Candidate(word_index=word_index, scale=scale, ha_field=hf)
                for sec, words in observations:
                    hv = ha[sec][("load_power_w", "grid_voltage_v",
                                  "battery_voltage_v", "corrected_soc").index(hf)]
                    if hv is None:
                        continue
                    c.pairs.append((words[word_index] * scale, float(hv)))
                if c.n == 0:
                    continue
                ok, reason = c.verdict()
                key = hf
                # Keep only the best-verdict candidate per (word, field).
                cands.setdefault(key, []).append(c)
    out: dict[str, list[Candidate]] = {}
    for hf, cs in cands.items():
        cs.sort(key=lambda c: (not c.verdict()[0], abs(statistics.median(c.residuals()))))
        out[hf] = cs[:5]
    return out


def report(cands: dict[str, list[Candidate]], min_n: int = 5) -> list[dict]:
    rows = []
    for hf, cs in cands.items():
        for c in cs:
            ok, reason = c.verdict(min_n=min_n)
            r = c.residuals()
            rows.append({
                "ha_field": hf,
                "word_index": c.word_index,
                "scale": c.scale,
                "n": c.n,
                "median_residual": round(statistics.median(r), 3),
                "spread": round(max(r) - min(r), 3),
                "confirmed": ok,
                "reason": reason,
            })
    return rows