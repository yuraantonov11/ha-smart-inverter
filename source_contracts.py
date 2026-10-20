"""Freshness and SOC contracts for a two-source telemetry stream.

DIAGNOSTIC ONLY. Nothing here switches a production source or changes the
SOC algorithm; it defines the contracts so that a later policy decision
cannot accidentally make transport re-interpret measurements.

Юра's corrections in this round:

1. Remove the rule "later HTTP response => fresher cloud". A repeated
   response that carries the same values must NOT keep renewing
   freshness forever.
2. Track three separate clocks: RECEPTION (transport), LOCAL MESSAGE
   (cap_ts of the passive block), MEASUREMENT TIME (unknown — the device
   does not report it).
3. Name metrics as intervals BETWEEN CHANGES, not message frequency.
4. Raw SOC from local and cloud must normalise to ONE contract;
   ``corrected_soc`` is computed separately by the SAME function for both,
   so transport cannot change the algorithm.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# ── the three clocks, deliberately separate ───────────────────────────


@dataclass
class ReceptionEvent:
    """An HTTP response body was read. Says nothing about measurement age."""
    received_at: datetime


@dataclass
class LocalBlockEvent:
    """A passive block was captured. cap_ts = packet time on the router."""
    cap_ts: datetime
    values: dict[str, float]


@dataclass
class MeasurementTime:
    """Time the device actually sampled. UNKNOWN for this device.

    ``device_measurement_at`` is null in 100 % of evidence-journal records,
    so no source can populate this today.
    """
    value: datetime | None = None

    @property
    def known(self) -> bool:
        return self.value is not None


# ── freshness is measured on CHANGES, not on receptions ───────────────


@dataclass
class ChangeTracker:
    """Track the last time a field's VALUE actually changed.

    ``reception_at`` alone never renews freshness: a repeat of the same
    value leaves ``changed_at`` untouched, which is exactly the failure
    mode Юра described.
    """
    tolerance: float = 0.0
    _last_value: dict[str, float] = field(default_factory=dict)
    _changed_at: dict[str, datetime] = field(default_factory=dict)

    def observe(self, key: str, value: float | None, at: datetime) -> bool:
        """Record an observation. Returns True when the value CHANGED."""
        if value is None:
            return False
        v = round(float(value), 6)
        prev = self._last_value.get(key)
        changed = prev is None or abs(v - prev) > self.tolerance
        if changed:
            self._last_value[key] = v
            self._changed_at[key] = at
        return changed

    def last_change(self, key: str) -> datetime | None:
        return self._changed_at.get(key)

    def change_age_s(self, key: str, now: datetime) -> float | None:
        t = self._changed_at.get(key)
        if t is None:
            return None
        return (now - t).total_seconds()

    def receptions_since_change(self, key: str) -> int:
        """How many observations repeated the current value.

        Exposed so a report can state 'N receptions carried no new
        measurement' instead of pretending they were fresh.
        """
        return self._repeat.get(key, 0)

    _repeat: dict[str, int] = field(default_factory=dict)

    def observe_counted(self, key: str, value: float | None, at: datetime) -> bool:
        changed = self.observe(key, value, at)
        if not changed and value is not None:
            self._repeat[key] = self._repeat.get(key, 0) + 1
        return changed


def intervals_between_changes(times: list[datetime]) -> list[float]:
    """Seconds between successive changes. NOT message frequency."""
    return [(b - a).total_seconds() for a, b in zip(times, times[1:])]


def message_frequency(times: list[datetime]) -> float:
    """Messages per minute. Reported SEPARATELY from change intervals."""
    if len(times) < 2:
        return 0.0
    span = (times[-1] - times[0]).total_seconds()
    return (len(times) - 1) / span * 60.0 if span > 0 else 0.0


# ── raw SOC contract, shared by both transports ───────────────────────

# PS4Z word 6 = batteryCapacity (raw SOC, integer percent)
LOCAL_RAW_SOC_WORD = 6
LOCAL_RAW_SOC_SCALE = 1.0


@dataclass
class RawSocReading:
    """One normalised raw-SOC observation, whichever transport produced it."""
    value_pct: float
    observed_at: datetime
    source: str            # "cloud_raw" | "local_raw"
    transport_reception: datetime | None = None   # None for local
    measurement_time: MeasurementTime = field(default_factory=MeasurementTime)


def normalise_raw_soc_local(word_value: int) -> float:
    """PS4Z word 6 -> raw SOC percent. No correction, no clamping beyond
    the 0..100 physical range."""
    v = float(word_value) * LOCAL_RAW_SOC_SCALE
    return max(0.0, min(100.0, v))


def normalise_raw_soc_cloud(raw_key_value: float | None) -> float | None:
    """Cloud raw SOC -> the same contract.

    The cloud response has NO ``batterySoc`` field; ``api.py`` falls back
    to ``batteryCapacity``. Both normalise to raw SOC percent.
    """
    if raw_key_value is None:
        return None
    return max(0.0, min(100.0, float(raw_key_value)))


def corrected_soc_for(raw_soc_pct: float | None,
                      voltage_v: float | None,
                      current_a: float | None,
                      get_real_soc) -> float | None:
    """Apply the ONE production correction to either transport's raw SOC.

    Both sources must call this same wrapper so that switching transport
    cannot change the algorithm. ``get_real_soc`` is injected (the
    production function) so this module never duplicates or re-orders the
    SOC logic.
    """
    if raw_soc_pct is None:
        return None
    v = float(voltage_v) if voltage_v is not None else 0.0
    i = float(current_a) if current_a is not None else 0.0
    return float(get_real_soc(float(raw_soc_pct), v, i))