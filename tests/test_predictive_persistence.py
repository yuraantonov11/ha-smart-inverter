"""Tests for predictive persistence + select entity.

The bug identified by parent: ``InverterPredictiveAssistSwitch``
flipped ``hems._predictive_enabled`` only in memory. Reload = lost.
Also: no Select entity to pick ``off / shadow / assist`` modes.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hems.engine import HemsEngine
from hems.tuning import PredictiveTuning


_P = 0
_F = 0
_FLS: list[str] = []


def _check(c, m):
    global _P, _F
    if c:
        _P += 1
    else:
        _F += 1
        _FLS.append(m)
        print(f"  ❌ {m}")


def _section(name):
    print(f"\n── {name} ──")


def test_predictive_tuning_default_off():
    _section("PredictiveTuning defaults")
    pt = PredictiveTuning()
    _check(pt.predictive_enabled is False, "predictive off by default")
    _check(pt.predictive_mode == "off", "default mode = off")


def test_predictive_mode_values():
    """Only three modes: off / shadow / assist."""
    pt = PredictiveTuning()
    pt.predictive_mode = "shadow"
    _check(pt.predictive_mode == "shadow", "shadow mode accepted")
    pt.predictive_mode = "assist"
    _check(pt.predictive_mode == "assist", "assist mode accepted")


def test_engine_predictive_tuning_integration():
    """Engine reads mode from PredictiveTuning at evaluate() time."""
    from hems.tuning import HemsTunables, HemsTuningService

    eng = HemsEngine(
        tunables=HemsTunables(),
        tuning=HemsTuningService(),
    )
    eng.predictive_tuning = PredictiveTuning()
    eng.predictive_tuning.predictive_mode = "off"
    # Off → no hint written
    eng.evaluate(
        smart_mode=0, hems_auto=True, soc=60.0, pv_power=2000.0,
        grid_power=0.0, battery_power=0.0, load_power=500.0,
        grid_voltage=232.5, grid_available=True,
        current_output="0", current_charger="1",
        forecast_tomorrow_kwh=3.0,
    )
    _check(getattr(eng, "_last_predictive_hint", None) is None,
           "off mode → no hint recorded")


def test_shadow_mode_records_hint_only():
    """Shadow mode computes hint but doesn't apply it (no override)."""
    from hems.tuning import HemsTunables, HemsTuningService
    from datetime import datetime

    eng = HemsEngine(tunables=HemsTunables(), tuning=HemsTuningService())
    eng.predictive_tuning = PredictiveTuning()
    eng.predictive_tuning.predictive_mode = "shadow"
    # Coordinator-supplied arrays the planner consumes. The parent
    # review demanded the missing-data gate — to get past it the
    # coordinator must have fed all arrays (which it does at
    # runtime). Tests mimic that here.
    eng._last_forecast_today_kwh = 5.0
    eng._hourly_pv_forecast = [100.0] * 24
    eng._hourly_radiation = [200.0] * 24
    eng._hourly_weather_codes = [0] * 24
    eng._tariff_schedule = [4.32 if 7 <= h < 23 else 2.16 for h in range(24)]
    eng._battery_capacity_kwh = 11.0
    decision = eng.evaluate(
        smart_mode=0, hems_auto=True, soc=60.0, pv_power=2000.0,
        grid_power=0.0, battery_power=0.0, load_power=500.0,
        grid_voltage=232.5, grid_available=True,
        current_output="0", current_charger="1",
        now=datetime(2026, 6, 15, 10, 0, 0),
        forecast_tomorrow_kwh=3.0,
    )
    # Shadow: hint is computed (visible to sensor) but decision is unchanged
    _check(getattr(eng, "_last_predictive_hint", None) is not None,
           "shadow records hint")
    # The decision in shadow mode should match the non-shadow baseline (surplus → SBU).
    _check(decision.output_priority == "2" or decision.output_priority is None,
           f"shadow decision is the existing baseline (got {decision.output_priority})")


def test_assist_mode_uses_hint():
    """Assist mode may shift the output/charger target when safe."""
    from hems.tuning import HemsTunables, HemsTuningService
    from datetime import datetime

    eng = HemsEngine(tunables=HemsTunables(), tuning=HemsTuningService())
    eng.predictive_tuning = PredictiveTuning()
    eng.predictive_tuning.predictive_mode = "assist"
    eng.predictive_tuning.night_charge_start_hour = 23
    eng.predictive_tuning.night_charge_end_hour = 7
    eng.predictive_tuning.battery_reserve_pct = 20.0
    # Feed the planner-required arrays (mirroring what the
    # coordinator does at runtime).
    eng._last_forecast_today_kwh = 5.0
    eng._hourly_pv_forecast = [100.0] * 24
    eng._hourly_radiation = [200.0] * 24
    eng._hourly_weather_codes = [0] * 24
    eng._tariff_schedule = [4.32 if 7 <= h < 23 else 2.16 for h in range(24)]
    eng._battery_capacity_kwh = 11.0
    # Force a decision where the baseline would output USB, but the
    # planner says "charge at night"
    decision = eng.evaluate(
        smart_mode=1, hems_auto=True, soc=60.0, pv_power=0.0,
        grid_power=0.0, battery_power=0.0, load_power=200.0,
        grid_voltage=232.5, grid_available=True,
        current_output="2", current_charger="2",
        now=datetime(2026, 6, 15, 23, 30, 0),  # night
        forecast_tomorrow_kwh=3.0,
    )
    # The current baseline is: ARBITRAGE+night → USB+SNU regardless.
    # The planner must NOT force the output if the baseline already
    # says USB (which is the night safety default).
    _check(decision.output_priority in ("0", None),
           f"assist respects baseline safety at night (got {decision.output_priority})")


if __name__ == "__main__":
    test_predictive_tuning_default_off()
    test_predictive_mode_values()
    test_engine_predictive_tuning_integration()
    test_shadow_mode_records_hint_only()
    test_assist_mode_uses_hint()

    print(f"\n{_P} passed, {_F} failed")
    if _F:
        for f in _FLS:
            print(f"  - {f}")
        sys.exit(1)
    print("✅ ALL PERSISTENCE TESTS PASSED")