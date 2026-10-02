"""Standalone runner for telemetry RED→GREEN verification.

Mirrors the existing ``tests/test_hems_all.py`` pattern: importable
as a module, runnable as a script with ``python tests/test_telemetry.py``.

Pytest cannot collect this directory because the project's top-level
``__init__.py`` imports Home Assistant (only available inside HA). The
existing tests run as plain scripts, so we follow the same convention
to keep the test workflow identical to what's already documented.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime

from hems.telemetry import build_planner_inputs


# ──────────────────────────────────────────────────────────────────
# Test helpers
# ──────────────────────────────────────────────────────────────────

_PASS = 0
_FAIL = 0
_FAILURES: list[str] = []


def _check(cond: bool, msg: str) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        _FAILURES.append(msg)
        print(f"  ❌ {msg}")


def _section(name: str) -> None:
    print(f"\n── {name} ──")


def _build(**kw):
    defaults = {
        "raw": {"gridVoltage": 232.5, "batterySoc": 60.0,
                "pvPower": 1000.0, "loadPower": 500.0,
                "gridPower": 0.0, "batteryPower": 0.0},
        "now": datetime(2026, 6, 15, 10, 0, 0),
        "battery_capacity_kwh": 4.8,
        "grid_available": True,
    }
    defaults.update(kw)
    return build_planner_inputs(**defaults)


# ──────────────────────────────────────────────────────────────────
# Test cases
# ──────────────────────────────────────────────────────────────────


def test_voltage_sanitisation() -> None:
    _section("voltage sanitisation")
    pi = _build()
    _check(pi.grid_v == 232.5, "normal 232.5V passes through")
    _check(pi.grid_v_source.origin == "api", "normal voltage origin=api")

    pi = _build(raw={"gridVoltage": 0, "batterySoc": 60.0, "pvPower": 1000.0,
                      "loadPower": 500.0, "gridPower": 0.0, "batteryPower": 0.0})
    _check(pi.grid_v == 230.0, "0V → fallback 230")
    _check(pi.grid_v_source.origin == "fallback", "0V origin=fallback")

    pi = _build(raw={"gridVoltage": -5.0, "batterySoc": 60.0, "pvPower": 1000.0,
                      "loadPower": 500.0, "gridPower": 0.0, "batteryPower": 0.0})
    _check(pi.grid_v == 230.0, "negative V → fallback")

    pi = _build(raw={"gridVoltage": 400.0, "batterySoc": 60.0, "pvPower": 1000.0,
                      "loadPower": 500.0, "gridPower": 0.0, "batteryPower": 0.0})
    _check(pi.grid_v == 230.0, "out-of-range high → fallback")

    pi = _build(raw={"gridVoltage": float("nan"), "batterySoc": 60.0,
                      "pvPower": 1000.0, "loadPower": 500.0,
                      "gridPower": 0.0, "batteryPower": 0.0})
    _check(math.isfinite(pi.grid_v), "NaN voltage → finite")
    _check(pi.grid_v_source.origin == "fallback", "NaN origin=fallback")


def test_missing_forecast_semantics() -> None:
    _section("missing ≠ zero")
    pi = _build(forecast_tomorrow_kwh=None)
    _check(pi.forecast_tomorrow_kwh is None, "missing forecast stays None")
    _check(pi.forecast_source.origin == "fallback", "missing forecast origin=fallback")

    pi = _build(forecast_tomorrow_kwh=-1.0)
    _check(pi.forecast_tomorrow_kwh is None, "negative forecast → None")

    pi = _build(forecast_tomorrow_kwh=float("nan"))
    _check(pi.forecast_tomorrow_kwh is None, "NaN forecast → None")

    pi = _build(forecast_tomorrow_kwh=3.5)
    _check(pi.forecast_tomorrow_kwh == 3.5, "valid 3.5 kWh passes through")
    _check(pi.forecast_source.origin == "api", "valid origin=api")

    pi = _build(forecast_tomorrow_kwh=200.0)
    _check(pi.forecast_tomorrow_kwh is None, "implausible 200 kWh → None")

    pi = _build(hourly_pv=None)
    _check(pi.hourly_pv == [], "None hourly_pv → []")

    pi = _build(hourly_pv=[float("nan"), 100.0, None, 200.0])
    _check(pi.hourly_pv == [0.0, 100.0, 0.0, 200.0], "NaN/None replaced with 0")


def test_consumption_history_bounds() -> None:
    _section("consumption history bounds")
    pi = _build()
    _check(pi.consumption_history == [], "no history → []")
    _check(pi.consumption_source.origin == "fallback", "no history origin=fallback")

    days = [[100.0] * 24 for _ in range(10)]
    pi = _build(consumption_history=days)
    _check(len(pi.consumption_history) == 7, "history capped at 7 days")
    _check(pi.consumption_source.origin == "api", "real history origin=api")

    days = [[100.0] * 24, [100.0] * 12]
    pi = _build(consumption_history=days)
    _check(len(pi.consumption_history) == 1, "wrong-length day dropped")

    days = [[100.0] * 24]
    days[0][5] = 999_999.0
    pi = _build(consumption_history=days)
    _check(max(pi.consumption_history[0]) <= 50_000.0, "extreme values clamped")


def test_tariff_schedule() -> None:
    _section("tariff schedule")
    pi = _build(tariff_schedule=[4.32] * 12)
    _check(pi.tariff_schedule == [], "short tariff dropped")

    sched = [2.16 if 23 <= h or h < 7 else 4.32 for h in range(24)]
    pi = _build(tariff_schedule=sched)
    _check(len(pi.tariff_schedule) == 24, "24-element tariff accepted")
    _check(pi.tariff_schedule[0] == 2.16, "hour 0 is night tariff")
    _check(pi.tariff_schedule[12] == 4.32, "hour 12 is day tariff")


def test_soc_clamping() -> None:
    _section("SOC clamping")
    pi = _build(raw={"gridVoltage": 230.0, "batterySoc": 150.0,
                      "pvPower": 1000.0, "loadPower": 500.0,
                      "gridPower": 0.0, "batteryPower": 0.0})
    _check(pi.soc == 100.0, "soc>100 clamped to 100")
    # Out-of-range SOC falls back to safe value 100% (full battery
    # assumption prevents unnecessary grid charge at night).
    pi = _build(raw={"gridVoltage": 230.0, "batterySoc": -10.0,
                      "pvPower": 1000.0, "loadPower": 500.0,
                      "gridPower": 0.0, "batteryPower": 0.0})
    _check(pi.soc == 100.0, "out-of-range soc falls back to safe 100%")
    _check(pi.soc_source.origin == "fallback", "out-of-range soc origin=fallback")


def test_missing_fields_helper() -> None:
    _section("missing_fields() helper")
    pi = _build(forecast_tomorrow_kwh=3.0,
                consumption_history=[[200.0] * 24])
    _check(pi.missing_fields() == [], "no missing fields when all real")

    pi = _build(raw={"gridVoltage": 0.0})
    missing = pi.missing_fields()
    _check("grid_v" in missing, "missing grid_v reported")
    _check("forecast" in missing, "missing forecast reported")
    _check("consumption" in missing, "missing consumption reported")


# ──────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    test_voltage_sanitisation()
    test_missing_forecast_semantics()
    test_consumption_history_bounds()
    test_tariff_schedule()
    test_soc_clamping()
    test_missing_fields_helper()

    print(f"\n{_PASS} passed, {_FAIL} failed")
    if _FAIL:
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("✅ ALL TELEMETRY TESTS PASSED")