"""Unit tests for ``scripts/probe_r01_live.py``.

The probe must:
  - reject NaN/Inf in forecast/actual values
  - require the SAME model tag in both journals
  - report FAIL when a v2 pair exists but is invalid
  - report NOT_YET_VERIFIED when no v2 pair exists
  - treat the empty forecast_diagnostic as the expected
    "forecast not yet received" state
  - require ALL documented diagnostic keys even when empty
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

PROBE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "probe_r01_live.py"
spec = importlib.util.spec_from_file_location("probe_r01_live", PROBE_PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_is_finite_number_rejects_nan_and_inf() -> None:
    assert probe._is_finite_number(0.0) is True
    assert probe._is_finite_number(1) is True
    assert probe._is_finite_number(-1.5) is True
    # Booleans are not numbers in our contract.
    assert probe._is_finite_number(True) is False
    assert probe._is_finite_number(False) is False
    # NaN / Inf are not finite.
    assert probe._is_finite_number(math.nan) is False
    assert probe._is_finite_number(math.inf) is False
    assert probe._is_finite_number(-math.inf) is False
    # Strings, None, dicts are not numbers.
    assert probe._is_finite_number("1.0") is False
    assert probe._is_finite_number(None) is False
    assert probe._is_finite_number({}) is False


def test_check_forecast_diagnostic_empty_is_not_yet_verified() -> None:
    """An empty diagnostic (no rows yet) is the expected
    'forecast not yet received' state, not a failure. The
    schema must still be present (otherwise the production
    code is broken).
    """
    state = {
        "attributes": {
            "forecast_diagnostic": {
                "forecast_received_at": None,
                "forecast_timezone": "Europe/Kyiv",
                "radiation_contract_version": 2,
                "forecast_dates": [],
                "intervals_per_date": {},
                "sample_row": None,
                "rows_with_diff_ne_3600": 0,
                "forecast_model_tags": [],
                "forecast_rows_total": 0,
            }
        }
    }
    status, name, msg = probe.check_forecast_diagnostic(state)
    assert status == "NOT_YET_VERIFIED", msg
    assert name == "forecast_diagnostic"


def test_check_forecast_diagnostic_empty_missing_keys_is_fail() -> None:
    """If the schema is missing, the production code is
    broken — that's a FAIL, not NOT_YET_VERIFIED.
    """
    state = {
        "attributes": {
            "forecast_diagnostic": {
                "forecast_received_at": None,
                # missing required keys
            }
        }
    }
    status, name, msg = probe.check_forecast_diagnostic(state)
    assert status == "FAIL"
    assert "missing keys" in msg


def test_check_forecast_diagnostic_none_state_is_not_yet_verified() -> None:
    """No sensor state at all → NOT_YET_VERIFIED, never FAIL.
    This is the case the operator hits when neither
    --state-input nor POWMR_HA_TOKEN is provided.
    """
    status, name, msg = probe.check_forecast_diagnostic(None)
    assert status == "NOT_YET_VERIFIED"
    assert "no sensor state" in msg


def test_check_forecast_diagnostic_rejects_nan_contract() -> None:
    """A rows_total > 0 but NaN/Inf/non-int contract is a FAIL."""
    state = {
        "attributes": {
            "forecast_diagnostic": {
                "forecast_received_at": "2026-10-09T10:00:00+00:00",
                "forecast_timezone": "Europe/Kyiv",
                "radiation_contract_version": math.nan,
                "forecast_dates": ["2026-10-09", "2026-10-10", "2026-10-11"],
                "intervals_per_date": {"2026-10-09": 24},
                "sample_row": None,
                "rows_with_diff_ne_3600": 0,
                "forecast_model_tags": ["hourly_response_v2"],
                "forecast_rows_total": 72,
            }
        }
    }
    status, name, msg = probe.check_forecast_diagnostic(state)
    assert status == "FAIL"
    assert "radiation_contract_version is non-finite" in msg


def test_check_forecast_diagnostic_rejects_empty_received_at_string() -> None:
    """An empty received_at string is a FAIL when rows_total
    > 0 (the production sensor must update the time on every
    successful refresh). A non-empty, non-ISO string is
    also a FAIL because the contract requires a parseable
    ISO-8601 timestamp.
    """
    state_empty_str = {
        "attributes": {
            "forecast_diagnostic": {
                "forecast_received_at": "   ",
                "forecast_timezone": "Europe/Kyiv",
                "radiation_contract_version": 2,
                "forecast_dates": ["2026-10-09", "2026-10-10", "2026-10-11"],
                "intervals_per_date": {"2026-10-09": 24},
                "sample_row": None,
                "rows_with_diff_ne_3600": 0,
                "forecast_model_tags": ["hourly_response_v2"],
                "forecast_rows_total": 72,
            }
        }
    }
    status, _, _ = probe.check_forecast_diagnostic(state_empty_str)
    assert status == "FAIL"
    # A valid ISO-8601 string is accepted (PASS).
    state_ok = {
        "attributes": {
            "forecast_diagnostic": {
                "forecast_received_at": "2026-10-09T10:00:00+00:00",
                "forecast_timezone": "Europe/Kyiv",
                "radiation_contract_version": 2,
                "forecast_dates": ["2026-10-09", "2026-10-10", "2026-10-11"],
                "intervals_per_date": {"2026-10-09": 24},
                "sample_row": None,
                "rows_with_diff_ne_3600": 0,
                "forecast_model_tags": ["hourly_response_v2"],
                "forecast_rows_total": 72,
            }
        }
    }
    status, _, _ = probe.check_forecast_diagnostic(state_ok)
    assert status == "PASS"


def _build_pair_check_journal_real(journal_pairs, real_pairs):
    """Build a fake host by patching _read_remote_json."""
    original = probe._read_remote_json
    def fake(host, path):
        if "pv_fact_pairs" in path:
            return {
                "version": 3,
                "radiation_contract_version": 2,
                "calibration_model": "hourly_response_v2",
                "pairs": journal_pairs,
            }
        if "real_forecast_pairs" in path:
            return {
                "version": 2,
                "radiation_contract_version": 2,
                "pairs": real_pairs,
            }
        return None
    probe._read_remote_json = fake
    return original


def test_check_completed_pair_rejects_nan_values() -> None:
    """NaN/Inf in forecast or actual → FAIL, not PASS."""
    original = _build_pair_check_journal_real(
        journal_pairs={
            "2026-10-08": {
                "forecast_kwh": float("nan"),
                "actual_kwh": float("inf"),
                "forecast_model": "hourly_response_v2",
                "issued_at": "2026-10-07T00:00:00+00:00",
            },
        },
        real_pairs=[
            {
                "date": "2026-10-08",
                "forecast_kwh": 0.1,
                "actual_kwh": 0.05,
                "captured_at": "2026-10-08T00:00:00+00:00",
                "used": True,
                "forecast_model": "hourly_response_v2",
            },
        ],
    )
    try:
        status, name, msg = probe.check_completed_pair("fake-host", "x")
    finally:
        probe._read_remote_json = original
    assert status == "FAIL", msg
    assert "non-finite" in msg


def test_check_completed_pair_rejects_mismatched_model_tags() -> None:
    """hourly_response_v2 in journal + station_gain_v2 in
    real_pairs → FAIL, not PASS.
    """
    original = _build_pair_check_journal_real(
        journal_pairs={
            "2026-10-08": {
                "forecast_kwh": 0.1,
                "actual_kwh": 0.05,
                "forecast_model": "hourly_response_v2",
                "issued_at": "2026-10-07T00:00:00+00:00",
            },
        },
        real_pairs=[
            {
                "date": "2026-10-08",
                "forecast_kwh": 0.1,
                "actual_kwh": 0.05,
                "captured_at": "2026-10-08T00:00:00+00:00",
                "used": True,
                "forecast_model": "station_gain_v2",
            },
        ],
    )
    try:
        status, name, msg = probe.check_completed_pair("fake-host", "x")
    finally:
        probe._read_remote_json = original
    assert status == "FAIL", msg
    assert "model tags differ" in msg


def test_check_completed_pair_no_v2_pair_is_not_yet_verified() -> None:
    """No v2 pair at all → NOT_YET_VERIFIED."""
    original = _build_pair_check_journal_real(
        journal_pairs={
            "2026-10-07": {
                "forecast_kwh": 0.1,
                "actual_kwh": 0.05,
                "forecast_model": "station_gain_v1",  # v1, not v2
                "issued_at": "2026-10-06T00:00:00+00:00",
            },
        },
        real_pairs=[
            {
                "date": "2026-10-07",
                "forecast_kwh": 0.1,
                "actual_kwh": 0.05,
                "captured_at": "2026-10-07T00:00:00+00:00",
                "used": True,
                "forecast_model": "station_gain_v1",
            },
        ],
    )
    try:
        status, name, msg = probe.check_completed_pair("fake-host", "x")
    finally:
        probe._read_remote_json = original
    assert status == "NOT_YET_VERIFIED", msg
    assert "no completed v2 pair" in msg


def test_check_completed_pair_v2_pending_is_not_yet_verified() -> None:
    """A v2 pair exists but used=False → NOT_YET_VERIFIED."""
    original = _build_pair_check_journal_real(
        journal_pairs={
            "2026-10-08": {
                "forecast_kwh": 0.1,
                "actual_kwh": 0.05,
                "forecast_model": "hourly_response_v2",
                "issued_at": "2026-10-07T00:00:00+00:00",
            },
        },
        real_pairs=[
            {
                "date": "2026-10-08",
                "forecast_kwh": 0.1,
                "actual_kwh": 0.05,
                "captured_at": "2026-10-08T00:00:00+00:00",
                "used": False,  # not yet completed
                "forecast_model": "hourly_response_v2",
            },
        ],
    )
    try:
        status, name, msg = probe.check_completed_pair("fake-host", "x")
    finally:
        probe._read_remote_json = original
    assert status == "NOT_YET_VERIFIED", msg
    assert "v2-tagged pairs present" in msg


def test_check_completed_pair_valid_pair_passes() -> None:
    """A complete, valid v2 pair → PASS."""
    original = _build_pair_check_journal_real(
        journal_pairs={
            "2026-10-08": {
                "forecast_kwh": 0.5,
                "actual_kwh": 0.45,
                "forecast_model": "hourly_response_v2",
                "issued_at": "2026-10-07T00:00:00+00:00",
            },
        },
        real_pairs=[
            {
                "date": "2026-10-08",
                "forecast_kwh": 0.5,
                "actual_kwh": 0.45,
                "captured_at": "2026-10-08T00:00:00+00:00",
                "used": True,
                "forecast_model": "hourly_response_v2",
            },
        ],
    )
    try:
        status, name, msg = probe.check_completed_pair("fake-host", "x")
    finally:
        probe._read_remote_json = original
    assert status == "PASS", msg
    assert "hourly_response_v2" in msg


def test_check_completed_pair_rejects_issued_after_day() -> None:
    """issued_at.date() >= day → FAIL (forecast was issued
    after the predicted day, which is impossible).
    """
    original = _build_pair_check_journal_real(
        journal_pairs={
            "2026-10-08": {
                "forecast_kwh": 0.5,
                "actual_kwh": 0.45,
                "forecast_model": "hourly_response_v2",
                "issued_at": "2026-10-08T15:00:00+00:00",  # AFTER the day
            },
        },
        real_pairs=[
            {
                "date": "2026-10-08",
                "forecast_kwh": 0.5,
                "actual_kwh": 0.45,
                "captured_at": "2026-10-08T00:00:00+00:00",
                "used": True,
                "forecast_model": "hourly_response_v2",
            },
        ],
    )
    try:
        status, name, msg = probe.check_completed_pair("fake-host", "x")
    finally:
        probe._read_remote_json = original
    assert status == "FAIL", msg
    assert "not before day" in msg


if __name__ == "__main__":
    import inspect
    tests = sorted(
        (n, fn) for n, fn in globals().items()
        if n.startswith("test_") and callable(fn)
    )
    failed = []
    for n, fn in tests:
        try:
            fn()
            print(f"  {n}: PASS")
        except Exception as exc:
            failed.append((n, repr(exc)))
            print(f"  {n}: FAIL ({exc!r})")
    if failed:
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed (0 failed).")
