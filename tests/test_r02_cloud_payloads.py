"""R02 — historical (overview) cloud buckets — **synthetic** payload tests.

Audit R02 (round 3):
  * These tests use a **synthetic** JSON payload authored in
    ``tests/fixtures/r02_cloud_payloads.json``. They exercise
    production ``measured_pv_days`` / ``measured_pv_hours`` with
    that synthetic payload.
  * They DO NOT prove the absence of ``measured_at`` /
    ``sequenceId`` / ``deviceTime`` in **real** API responses.
    For real-API conclusions, a separate **cleaned capture with
    provenance** would be required. We do not have such a capture.
  * The historical ``timePoints`` for daily kWh and half-hour W
    are a separate contract from realtime telemetry. The realtime
    path is in ``test_r02_realtime_telemetry.py``.
  * The freshness policy (proposed, not implemented) is documented
    in ``docs/audit-r02-cloud-freshness.md`` §8.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from hems.cloud_history import measured_pv_days, measured_pv_hours  # noqa: E402

FIXTURE_PATH = os.path.join(
    REPO_ROOT, "tests", "fixtures", "r02_cloud_payloads.json"
)


def _wrap_property(points, key, unit):
    return [{
        "property": {"key": key, "unit": unit},
        "timePoints": points,
    }]


def _run_daily(points):
    """Run production ``measured_pv_days`` for the date range."""
    properties = _wrap_property(points, "pvGeneratedEnergy", "kWh")
    return measured_pv_days(properties, date(2026, 10, 1), date(2026, 10, 15))


def test_scenario_A_old_timestamp_fresh_fetch() -> None:
    """Historical bucket: ``point.time = 2026-10-07`` (yesterday) and a
    recent ``fetched_at`` → the bucket is taken as the actual for
    2026-10-07. The ``fetched_at`` is NOT passed to the parser; the
    parser only sees ``properties`` (the bucket boundary is in
    ``point.time``).
    """
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)
    sc = next(s for s in fixture["scenarios"]
              if s["name"] == "A_old_timestamp_fresh_fetch")
    actual = _run_daily(sc["points"])
    assert actual == {"2026-10-07": 6.5}, (
        f"Expected {{'2026-10-07': 6.5}}; got {actual}"
    )


def test_scenario_B_repeated_payload_idempotent() -> None:
    """Repeated invocation of the same payload yields the same result.

    The user noted that the previous test invoked the parser only once.
    Here we invoke it THREE times with the same payload, asserting
    idempotence at each step.
    """
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)
    sc = next(s for s in fixture["scenarios"]
              if s["name"] == "B_repeated_payload")
    expected = {"2026-10-07": 6.5}
    for i in range(3):
        actual = _run_daily(sc["points"])
        assert actual == expected, (
            f"Iteration {i + 1}: expected {expected}; got {actual}"
        )


def test_scenario_C_missing_time_field() -> None:
    """Missing ``point.time`` → the point is silently rejected.

    This is a parse-error check, not a freshness policy.
    """
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)
    sc = next(s for s in fixture["scenarios"]
              if s["name"] == "C_missing_time_field")
    actual = _run_daily(sc["points"])
    assert actual == {}, f"Missing time must reject; got {actual}"


def test_scenario_D_out_of_range_or_parse_error() -> None:
    """Future date, unparseable string, empty string → all rejected.

    The "future date" is rejected because the request range is
    [2026-10-01, 2026-10-15] — 2099-12-31 is outside. The
    unparseable/empty strings raise ValueError in ``date.fromisoformat``
    which the parser catches.
    """
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)
    sc = next(s for s in fixture["scenarios"]
              if s["name"] == "D_future_or_garbage_timestamp")
    actual = _run_daily(sc["points"])
    assert actual == {}, f"Out-of-range/garbage must reject; got {actual}"


def test_scenario_E_fresh_zero_at_night() -> None:
    """``point.time = 2026-10-08``, value = 0.0, ``isRealValue = true``,
    and the point is within the request range → the actual for
    2026-10-08 is 0.0. A nightly zero is a valid measurement.
    """
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)
    sc = next(s for s in fixture["scenarios"]
              if s["name"] == "E_fresh_zero_at_night")
    actual = _run_daily(sc["points"])
    assert actual == {"2026-10-08": 0.0}, (
        f"Fresh zero at night must be valid; got {actual}"
    )


def test_scenario_F_half_hour_idempotent() -> None:
    """For half-hour ``measured_pv_hours``: two consecutive calls with
    the same payload produce the same output. The parser returns
    exactly one bucket per pair of half-hour samples.
    """
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)
    sc = next(s for s in fixture["scenarios"]
              if s["name"] == "F_same_value_no_new_evidence")
    properties = [{
        "property": {"key": "generationPower", "unit": "kW"},
        "timePoints": sc["points"],
    }]
    rows_1 = measured_pv_hours(properties, date(2026, 10, 8),
                                ZoneInfo("Europe/Kyiv"))
    rows_2 = measured_pv_hours(properties, date(2026, 10, 8),
                                ZoneInfo("Europe/Kyiv"))
    assert rows_1 == rows_2, (
        f"Two consecutive parses must be equal: {rows_1} vs {rows_2}"
    )
    assert len(rows_1) == 1
    assert rows_1[0]["mean"] == 0.0
    assert rows_1[0]["source"] == "cloud_half_hour_samples"
    # 'start' = epoch seconds UTC for 07:00 Kyiv = 04:00 UTC of 2026-10-08
    expected_start = int(
        datetime(2026, 10, 8, 4, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    )
    assert rows_1[0]["start"] == expected_start, (
        f"Bucket start must be 04:00 UTC (= 07:00 Kyiv); got {rows_1[0]['start']}"
    )


def test_r02_historical_is_separate_from_realtime() -> None:
    """Document the contract: historical buckets are NOT the source of
    dispatch freshness. The realtime path (``fetch_realtime_data``) is
    what HEMS uses for live decisions. The historical ``daily``
    and ``half-hour`` endpoints are for OFFLINE analysis
    (calibrator, dashboard). A nightly PV=0 historical fact is
    valid; a stale realtime value is a separate concern.
    """
    # We do not connect a new policy here. We document the separation.
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)
    sc_e = next(s for s in fixture["scenarios"]
                if s["name"] == "E_fresh_zero_at_night")
    actual = _run_daily(sc_e["points"])
    # A zero value at the bucket boundary is recorded as the day's
    # actual. The fact that the bucket was 0 doesn't tell us anything
    # about realtime freshness.
    assert actual == {"2026-10-08": 0.0}
    # The production ``measured_pv_days`` does not accept fetched_at;
    # it only inspects ``point.time`` and ``point.value`` /
    # ``point.isRealValue``. A future implementation that wants to
    # enforce a "fetched_at - now < threshold" check would need to
    # plumb fetched_at into the parser; we explicitly do NOT do that
    # in R02.


if __name__ == "__main__":
    test_scenario_A_old_timestamp_fresh_fetch()
    print("test_scenario_A_old_timestamp_fresh_fetch: PASS")
    test_scenario_B_repeated_payload_idempotent()
    print("test_scenario_B_repeated_payload_idempotent: PASS")
    test_scenario_C_missing_time_field()
    print("test_scenario_C_missing_time_field: PASS")
    test_scenario_D_out_of_range_or_parse_error()
    print("test_scenario_D_out_of_range_or_parse_error: PASS")
    test_scenario_E_fresh_zero_at_night()
    print("test_scenario_E_fresh_zero_at_night: PASS")
    test_scenario_F_half_hour_idempotent()
    print("test_scenario_F_half_hour_idempotent: PASS")
    test_r02_historical_is_separate_from_realtime()
    print("test_r02_historical_is_separate_from_realtime: PASS")
    print("\nAll 7 tests passed (0 failed).")
    sys.exit(0)
