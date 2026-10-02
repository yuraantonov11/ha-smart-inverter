"""Regression: planner must not crash when consumption history is non-empty
(predictive.py used an undefined name `h` in LoadPredictor.predict)."""
import sys
from datetime import datetime

sys.path.insert(0, ".")
from hems.predictive import PredictiveHemsController
from hems.telemetry import build_planner_inputs

_P = 0
_F = []


def _check(c, m):
    global _P
    if c:
        _P += 1
    else:
        _F.append(m)
        print("  FAIL", m)


def _inputs(hist):
    return build_planner_inputs(
        raw={"gridVoltage": 232.5, "batterySoc": 60, "pvPower": 0, "loadPower": 500,
             "gridPower": 0, "batteryPower": -600},
        now=datetime(2026, 10, 2, 20, 0), smart_mode=0,
        forecast_tomorrow_kwh=0.48, forecast_today_kwh=1.5,
        hourly_pv=[0.0] * 24, hourly_radiation=[0.0] * 24, hourly_weather_codes=[0] * 24,
        tariff_schedule=[4.32 if 7 <= h < 23 else 2.16 for h in range(24)],
        consumption_history=hist, battery_capacity_kwh=11.76, grid_available=True,
    )


for name, hist in (("1 day", [[250.0] * 24]),
                   ("2 days", [[250.0] * 24, [300.0] * 24]),
                   ("3 days", [[250.0] * 24, [300.0] * 24, [280.0] * 24])):
    try:
        hint = PredictiveHemsController().suggest(_inputs(hist))
        _check(0 <= hint.target_soc_morning <= 100, f"{name}: morning target in range")
        _check(hint.confidence > 0.0, f"{name}: confidence > 0 with real history (got {hint.confidence})")
    except Exception as exc:  # noqa: BLE001
        _check(False, f"{name}: planner raised {type(exc).__name__}: {exc}")

print(f"{_P} passed, {len(_F)} failed")
sys.exit(1 if _F else 0)
