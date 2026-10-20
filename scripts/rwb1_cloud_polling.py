"""Cloud polling degradation analyzer.

Юра: «Наступний пріоритет — причина деградації cloud polling: розділи
налаштований інтервал, фактичний старт запитів, їх тривалість і проміжки
між успішними відповідями. Перевір timeout/retry/backoff та класифікацію
empty/malformed responses. Не збільшуй кількість запитів.»

This module measures, from the evidence journal alone:

  1. CONFIGURED interval (from code, passed in) vs ACTUAL cycle interval.
  2. REQUEST DURATION per cycle: event_timestamp - cloud_response_received_at.
     A ``None`` cloud_response_received_at means the request never produced
     a response, so the duration is unknown (not zero).
  3. GAPS BETWEEN SUCCESSFUL RESPONSES ONLY (``fresh_success``).
  4. Outcome classification, including whether the cloud timestamp exists,
     which source was served, and the availability flag.
  5. Stall attribution: which outcome precedes each >threshold stall, and
     whether the stall length matches a configured HTTP timeout.

It never increases the request count: it is a pure reader over the journal.
"""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone


def parse_ts(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_cycles(path) -> list[dict]:
    """Load cycle events from an evidence journal, ordered by sequence."""
    import json
    rows: list[dict] = []
    with open(path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("event_type") != "cycle":
                continue
            rows.append(d)
    rows.sort(key=lambda d: d.get("sequence", 0))
    return rows


def window(rows, start, end):
    a, b = parse_ts(start), parse_ts(end)
    return [d for d in rows if a <= parse_ts(d["event_timestamp"]) <= b]


@dataclass
class IntervalStats:
    n: int
    median: float
    p95: float
    max: float
    values: list

    @property
    def histogram(self) -> list:
        return sorted(set(round(v, 1) for v in self.values))


def _stats(values) -> IntervalStats | None:
    if not values:
        return None
    v = sorted(values)
    return IntervalStats(
        n=len(v),
        median=statistics.median(v),
        p95=v[min(len(v) - 1, int(0.95 * len(v)))],
        max=v[-1],
        values=v,
    )


def cycle_intervals(cycles) -> IntervalStats | None:
    ts = [parse_ts(d["event_timestamp"]) for d in cycles]
    return _stats([(b - a).total_seconds() for a, b in zip(ts, ts[1:])])


def request_durations(cycles) -> tuple[IntervalStats | None, list]:
    """Duration = event_timestamp - cloud_response_received_at.

    Returns (stats_for_cycles_that_got_a_response, missing) where `missing`
    lists the cycles whose cloud_response_received_at is None: those requests
    produced no response, so their duration is UNKNOWN, not zero.
    """
    vals, missing = [], []
    for d in cycles:
        t, r = parse_ts(d["event_timestamp"]), parse_ts(d.get("cloud_response_received_at"))
        if t is None or r is None:
            missing.append(d)
        else:
            vals.append((t - r).total_seconds())
    return _stats(vals), missing


def success_gaps(cycles, outcome: str = "fresh_success") -> IntervalStats | None:
    sel = [d for d in cycles if d.get("outcome") == outcome]
    ts = [parse_ts(d["event_timestamp"]) for d in sel]
    return _stats([(b - a).total_seconds() for a, b in zip(ts, ts[1:])])


def classify(cycles) -> dict:
    """Group cycles by outcome with the evidence that defines each class."""
    out: dict[str, dict] = {}
    for d in cycles:
        o = d.get("outcome", "?")
        rec = out.setdefault(o, {
            "n": 0,
            "no_cloud_ts": 0,
            "source": Counter(),
            "availability": Counter(),
            "error_class": Counter(),
        })
        rec["n"] += 1
        if not d.get("cloud_response_received_at"):
            rec["no_cloud_ts"] += 1
        st = d.get("state") or {}
        rec["source"][st.get("source")] += 1
        rec["availability"][st.get("availability")] += 1
        rec["error_class"][(d.get("error") or {}).get("class")] += 1
    for rec in out.values():
        for key in ("source", "availability", "error_class"):
            rec[key] = dict(rec[key])
    return out


def consecutive_runs(cycles, outcome: str) -> list[int]:
    runs, cur = [], 0
    for d in cycles:
        if d.get("outcome") == outcome:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    return runs


@dataclass
class StallAttribution:
    n_stalls: int
    preceding_outcome: Counter = field(default_factory=Counter)
    gap_values: list = field(default_factory=list)
    matched_http_timeout: int = 0
    notes: str = ""


def attribute_stalls(cycles, threshold_s: float = 30.0,
                     http_timeout_s: float | None = None) -> StallAttribution:
    """For each stall longer than `threshold_s`, record which outcome came
    immediately before it and the stall length.

    If `http_timeout_s` is given, count stalls whose length is within 1 s of
    it: a stall that equals the HTTP timeout is evidence the event loop was
    blocked for that whole timeout, i.e. the client never gave up earlier.
    """
    res = StallAttribution(n_stalls=0)
    for a, b in zip(cycles, cycles[1:]):
        g = (parse_ts(b["event_timestamp"]) - parse_ts(a["event_timestamp"])).total_seconds()
        if g <= threshold_s:
            continue
        res.n_stalls += 1
        res.preceding_outcome[a.get("outcome", "?")] += 1
        res.gap_values.append(g)
        if http_timeout_s is not None and abs(g - http_timeout_s) <= 1.0:
            res.matched_http_timeout += 1
    if http_timeout_s is not None:
        res.notes = (
            f"stalls within 1s of the configured HTTP timeout "
            f"({http_timeout_s}s): {res.matched_http_timeout}/{res.n_stalls}"
        )
    return res


def full_report(cycles, configured_interval_s: float,
                http_timeout_s: float | None = None,
                stall_threshold_s: float = 30.0) -> dict:
    dur, missing = request_durations(cycles)
    return {
        "n_cycles": len(cycles),
        "configured_interval_s": configured_interval_s,
        "actual_cycle_interval": cycle_intervals(cycles),
        "request_duration": dur,
        "requests_without_response": len(missing),
        "success_gaps": success_gaps(cycles),
        "classification": classify(cycles),
        "timeout_runs": consecutive_runs(cycles, "timeout"),
        "empty_response_runs": consecutive_runs(cycles, "empty_response"),
        "malformed_response_runs": consecutive_runs(cycles, "malformed_response"),
        "stalls": attribute_stalls(cycles, stall_threshold_s, http_timeout_s),
    }