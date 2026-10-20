"""Regression tests for the cloud polling degradation analyzer.

Юра: «розділи налаштований інтервал, фактичний старт запитів, їх тривалість
і проміжки між успішними відповідями. Перевір timeout/retry/backoff та
класифікацію empty/malformed responses.»

The critical distinction these tests pin down:
  * a cycle with ``cloud_response_received_at = None`` had a request that
    produced NO response. Its duration is UNKNOWN, not 0.0. Averaging it in
    as zero would understate request cost and hide the timeout pattern.
  * empty_response and malformed_response both HAVE a cloud timestamp, so
    they are distinct from timeout and must be classified separately.
  * configured interval and actual interval are separate measurements.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path("/opt/data/powmr-ai-work/powmr_inverter")
spec = importlib.util.spec_from_file_location(
    "rwb1_cloud_polling", REPO / "scripts" / "rwb1_cloud_polling.py"
)
cp = importlib.util.module_from_spec(spec)
sys.modules["rwb1_cloud_polling"] = cp
spec.loader.exec_module(cp)

T0 = datetime(2026, 10, 20, 3, 0, 0, tzinfo=timezone.utc)


def cycle(offset_s: float, outcome: str, cloud_offset_s: float | None,
          source: str, availability: str, error: str | None = None,
          seq: int = 0) -> dict:
    t = T0 + timedelta(seconds=offset_s)
    d = {
        "sequence": seq,
        "event_type": "cycle",
        "event_timestamp": t.isoformat(),
        "outcome": outcome,
        "cloud_response_received_at": (
            None if cloud_offset_s is None
            else (t + timedelta(seconds=cloud_offset_s)).isoformat()
        ),
        "state": {"source": source, "availability": availability},
        "error": None if error is None else {"class": error},
    }
    return d


def write_journal(cycles, d: tempfile.TemporaryDirectory) -> Path:
    p = Path(d) / "events.jsonl"
    p.write_text("\n".join(json.dumps(c) for c in cycles) + "\n")
    return p


class RequestDurationTests(unittest.TestCase):
    def test_missing_cloud_ts_is_unknown_not_zero(self):
        """A timeout cycle has no cloud_response_received_at. Its
        duration must be reported as MISSING, never as 0.0.
        """
        cycles = [
            cycle(0.0, "fresh_success", 0.003, "cloud_primary", "online", seq=1),
            cycle(5.0, "timeout", None, "cache", "unknown", "TimeoutError", seq=2),
            cycle(41.0, "fresh_success", 0.004, "cloud_primary", "online", seq=3),
        ]
        dur, missing = cp.request_durations(cycles)
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["outcome"], "timeout")
        # The timeout must NOT be in the duration sample.
        self.assertEqual(dur.n, 2)
        for v in dur.values:
            self.assertNotEqual(v, 0.0)

    def test_zero_duration_distinct_from_missing(self):
        """empty_response really does have a cloud timestamp and a ~0 s
        duration. It is a real observation, unlike the timeout.
        """
        cycles = [
            cycle(0.0, "empty_response", 0.000, "cache", "offline", seq=1),
        ]
        dur, missing = cp.request_durations(cycles)
        self.assertEqual(len(missing), 0)
        self.assertEqual(dur.n, 1)
        self.assertEqual(dur.values[0], 0.0)


class ClassificationTests(unittest.TestCase):
    def test_four_outcome_classes_are_distinct(self):
        cycles = [
            cycle(0.0, "fresh_success", 0.003, "cloud_primary", "online", seq=1),
            cycle(5.0, "empty_response", 0.000, "cache", "offline", seq=2),
            cycle(10.0, "malformed_response", 0.000, "cache", "offline", seq=3),
            cycle(15.0, "timeout", None, "cache", "unknown", "TimeoutError", seq=4),
        ]
        cls = cp.classify(cycles)
        self.assertEqual(set(cls), {
            "fresh_success", "empty_response", "malformed_response", "timeout",
        })
        # Only timeout lacks a cloud timestamp.
        self.assertEqual(cls["timeout"]["no_cloud_ts"], 1)
        for o in ("fresh_success", "empty_response", "malformed_response"):
            self.assertEqual(cls[o]["no_cloud_ts"], 0)
        # source attribution
        self.assertEqual(cls["fresh_success"]["source"], {"cloud_primary": 1})
        self.assertEqual(cls["timeout"]["source"], {"cache": 1})
        # availability attribution
        self.assertEqual(cls["timeout"]["availability"], {"unknown": 1})
        self.assertEqual(cls["empty_response"]["availability"], {"offline": 1})

    def test_empty_and_malformed_are_not_merged(self):
        cycles = [
            cycle(0.0, "empty_response", 0.0, "cache", "offline", seq=1),
            cycle(5.0, "malformed_response", 0.0, "cache", "offline", seq=2),
        ]
        cls = cp.classify(cycles)
        self.assertEqual(cls["empty_response"]["n"], 1)
        self.assertEqual(cls["malformed_response"]["n"], 1)


class RunLengthTests(unittest.TestCase):
    def test_timeout_runs_are_grouped(self):
        """28 timeouts in 4 runs (2, 1, 8, 17) is the real shape, not
        28 independent events. Run length is what distinguishes a cloud
        outage from sporadic blips.
        """
        cycles = []
        seq = 0
        def add(off, outcome):
            nonlocal seq
            seq += 1
            cycles.append(cycle(off, outcome, 0.0 if outcome != "timeout" else None,
                                "cloud_primary" if outcome == "fresh_success" else "cache",
                                "online" if outcome == "fresh_success" else "offline",
                                "TimeoutError" if outcome == "timeout" else None, seq=seq))
        off = 0.0
        for run_len in (2, 1, 8, 17):
            add(off, "fresh_success"); off += 5
            for _ in range(run_len):
                add(off, "timeout"); off += 36
            add(off, "fresh_success"); off += 5
        runs = cp.consecutive_runs(cycles, "timeout")
        self.assertEqual(runs, [2, 1, 8, 17])

    def test_empty_response_single_long_run(self):
        """The 102 empty_response cycles were ONE contiguous run, i.e. a
        sustained condition, not 102 independent failures.
        """
        cycles = []
        for i in range(102):
            cycles.append(cycle(i * 5.0, "empty_response", 0.0, "cache", "offline", seq=i))
        cycles.append(cycle(102 * 5.0, "fresh_success", 0.003, "cloud_primary", "online", seq=999))
        runs = cp.consecutive_runs(cycles, "empty_response")
        self.assertEqual(runs, [102])


class StallAttributionTests(unittest.TestCase):
    def test_stall_matching_http_timeout(self):
        """The 36 s stalls equal the 30 s aiohttp total timeout plus the
        5 s coordinator interval. The analyzer must attribute them to
        the preceding outcome and report the timeout match.

        With 4 cycles there are 3 gaps: 5.0, 36.0, 5.0 — so exactly
        ONE stall above 30 s.
        """
        cycles = [
            cycle(0.0, "fresh_success", 0.003, "cloud_primary", "online", seq=1),
            cycle(5.0, "timeout", None, "cache", "unknown", "TimeoutError", seq=2),
            cycle(41.0, "timeout", None, "cache", "unknown", "TimeoutError", seq=3),
            cycle(46.0, "fresh_success", 0.003, "cloud_primary", "online", seq=4),
        ]
        res = cp.attribute_stalls(cycles, threshold_s=30.0, http_timeout_s=30.0)
        self.assertEqual(res.n_stalls, 1)
        # The stall is preceded by a timeout.
        self.assertEqual(dict(res.preceding_outcome), {"timeout": 1})
        # The 36 s gap is NOT within 1 s of 30 s, so it is not a direct
        # timeout-length match — it is timeout + coordinator interval.
        self.assertEqual(res.matched_http_timeout, 0)
        self.assertEqual(res.gap_values, [36.0])

    def test_stall_equal_to_http_timeout_is_matched(self):
        """A gap exactly equal to the HTTP timeout (and above the
        threshold) is counted and matched.
        """
        cycles = [
            cycle(0.0, "fresh_success", 0.003, "cloud_primary", "online", seq=1),
            cycle(30.0, "timeout", None, "cache", "unknown", "TimeoutError", seq=2),
        ]
        res = cp.attribute_stalls(cycles, threshold_s=29.0, http_timeout_s=30.0)
        self.assertEqual(res.n_stalls, 1)
        self.assertEqual(res.matched_http_timeout, 1)
        self.assertIn("1/1", res.notes)

    def test_short_gaps_are_not_stalls(self):
        cycles = [
            cycle(0.0, "fresh_success", 0.003, "cloud_primary", "online", seq=1),
            cycle(5.0, "fresh_success", 0.003, "cloud_primary", "online", seq=2),
            cycle(10.0, "fresh_success", 0.003, "cloud_primary", "online", seq=3),
        ]
        res = cp.attribute_stalls(cycles, threshold_s=30.0, http_timeout_s=30.0)
        self.assertEqual(res.n_stalls, 0)


class IntervalSeparationTests(unittest.TestCase):
    def test_configured_and_actual_are_separate(self):
        """The report must carry both. `configured_interval_s` is what the
        code asks for; `actual_cycle_interval` is what the journal shows.
        """
        cycles = []
        for i in range(20):
            cycles.append(cycle(i * 5.0, "fresh_success", 0.003,
                                "cloud_primary", "online", seq=i))
        rep = cp.full_report(cycles, configured_interval_s=5.0)
        self.assertEqual(rep["configured_interval_s"], 5.0)
        self.assertAlmostEqual(rep["actual_cycle_interval"].median, 5.0, places=6)

    def test_success_gaps_exclude_failures(self):
        """Success gaps are measured only between fresh_success cycles.
        A failure between two successes must appear as a long gap.
        """
        cycles = [
            cycle(0.0, "fresh_success", 0.003, "cloud_primary", "online", seq=1),
            cycle(5.0, "timeout", None, "cache", "unknown", "TimeoutError", seq=2),
            cycle(41.0, "fresh_success", 0.003, "cloud_primary", "online", seq=3),
        ]
        g = cp.success_gaps(cycles)
        self.assertEqual(g.n, 1)
        self.assertEqual(g.values[0], 41.0)


if __name__ == "__main__":
    unittest.main()