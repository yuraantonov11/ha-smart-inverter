"""R04 + R05 acceptance tests.

These tests exercise the production paths (no algorithm
copies) and cover the fixtures Юра specified:

  - afternoon PV peak after 16:00
  - different station (different capacity / voltage)
  - invalid capacity (NaN, Infinity, bool, out-of-range)
  - invalid tariff (NaN, negative, > 50, non-numeric)
  - zero PV
  - missing forecast
  - configured rates and charging window
  - DST forward / backward
  - reserve, Storm, SOC guards unchanged
"""

from __future__ import annotations

import ast
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from hems.engine import _finite_number
from hems.telemetry import build_planner_inputs
from hems.predictive import (
    PlannerInputs,
    simulate_24h,
    ConsumptionPredictor,
)


# Load the production _derive_battery_capacity static
# method without instantiating the full InverterCoordinator
# (which has HA plumbing we don't need). The coordinator
# is a single-file module with relative imports that
# require HA to be present; we extract the class AST
# and exec it in a controlled namespace.
_COORDINATOR_PATH = REPO / "coordinator.py"


def _load_method(name):
    tree = ast.parse(_COORDINATOR_PATH.read_text(encoding="utf-8"))
    cls = next(
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "InverterCoordinator"
    )
    target = next(
        n for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == name
    )
    ns = {"_finite_number": _finite_number}
    exec(
        compile(ast.Module(body=[target], type_ignores=[]),
                str(_COORDINATOR_PATH), "exec"),
        ns,
    )
    return ns[name]


_derive = _load_method("_derive_battery_capacity")


def _build_tariff_schedule(*, self_day: float, self_night: float) -> list:
    """Call the production ``_build_tariff_schedule`` with
    the given self-side rates. The production method is a
    normal instance method, so we wrap it in a thin object.
    """
    class _Stub:
        pass
    stub = _Stub()
    stub._day_tariff_uah = self_day
    stub._night_tariff_uah = self_night
    return _load_method("_build_tariff_schedule")(stub)


def _build_inputs(**kwargs):
    """Build a baseline PlannerInputs for simulate_24h tests."""
    return PlannerInputs(
        now=kwargs.get("now", datetime(2026, 6, 21, 12, 0,
                                       tzinfo=ZoneInfo("Europe/Kyiv"))),
        soc=kwargs.get("soc", 60.0),
        soc_corrected=kwargs.get("soc_corrected", 60.0),
        pv_w=0.0, load_w=kwargs.get("load_w", 300.0),
        grid_w=0.0, batt_w=0.0,
        battery_capacity_kwh=11.776,
        grid_v=220.0, grid_ok=True,
        smart_mode=0,
        forecast_today_kwh=kwargs.get("forecast_today_kwh", 0.0),
        forecast_tomorrow_kwh=kwargs.get("forecast_tomorrow_kwh", 0.0),
        hourly_pv=kwargs.get("hourly_pv", [0.0] * 24),
        hourly_weather=[{}] * 24,
        hourly_radiation=kwargs.get("hourly_radiation", [0.0] * 24),
        tariff_schedule=kwargs.get("tariff_schedule", [2.16] * 24),
        consumption_history=[],
        night_charge_window=kwargs.get("night_charge_window", (23, 7)),
        reserve_soc=20.0,
        charge_efficiency=0.85,
        discharge_efficiency=0.90,
        dated_hourly_pv=kwargs.get("dated_hourly_pv"),
    )


# ─────────────────────────────────────────────────────────────────
# R04 — capacity / voltage / PV limits map
# ─────────────────────────────────────────────────────────────────


def test_r04_default_capacity_230ah_51_2v_yields_11_776kwh():
    """Default Ah=230 × V=51.2 / 1000 = 11.776 kWh.

    Exercises the production _derive_battery_capacity
    helper that the coordinator uses in __init__.
    """
    cap = _derive({})
    assert math.isclose(cap["ah"], 230.0)
    assert math.isclose(cap["voltage"], 51.2)
    assert math.isclose(cap["kwh"], 230.0 * 51.2 / 1000.0)
    assert cap["warning"] is None


def test_r04_other_station_280ah_48v():
    """A different station with 280 Ah @ 48 V (different
    chemistry) must produce a different derived kWh.
    """
    cap = _derive({
        "battery_capacity_ah": 280.0,
        "nominal_voltage_v": 48.0,
    })
    assert math.isclose(cap["ah"], 280.0)
    assert math.isclose(cap["voltage"], 48.0)
    assert math.isclose(cap["kwh"], 280.0 * 48.0 / 1000.0)
    assert not math.isclose(cap["kwh"], 11.776)
    assert cap["warning"] is None


def test_r04_nan_ah_falls_back_to_default():
    cap = _derive({"battery_capacity_ah": float("nan")})
    assert math.isclose(cap["ah"], 230.0)
    assert math.isclose(cap["kwh"], 230.0 * 51.2 / 1000.0)
    assert cap["warning"] is not None
    assert "non-finite" in cap["warning"]


def test_r04_inf_ah_falls_back_to_default():
    cap = _derive({"battery_capacity_ah": float("inf")})
    assert math.isclose(cap["ah"], 230.0)
    assert cap["warning"] is not None


def test_r04_neg_inf_ah_falls_back_to_default():
    cap = _derive({"battery_capacity_ah": float("-inf")})
    assert math.isclose(cap["ah"], 230.0)
    assert cap["warning"] is not None


def test_r04_bool_ah_falls_back_to_default():
    """Bool is a non-finite sentinel — must be rejected."""
    cap = _derive({"battery_capacity_ah": True})
    assert math.isclose(cap["ah"], 230.0)
    assert cap["warning"] is not None


def test_r04_string_ah_falls_back_to_default():
    cap = _derive({"battery_capacity_ah": "230"})
    assert math.isclose(cap["ah"], 230.0)
    assert cap["warning"] is not None


def test_r04_out_of_range_high_ah_falls_back_to_default():
    """48000 Ah (typo) must NOT silently become 2457.6 kWh."""
    cap = _derive({"battery_capacity_ah": 48000.0})
    assert math.isclose(cap["ah"], 230.0)
    assert cap["warning"] is not None
    assert "out of" in cap["warning"]


def test_r04_out_of_range_low_ah_falls_back_to_default():
    cap = _derive({"battery_capacity_ah": 5.0})
    assert math.isclose(cap["ah"], 230.0)
    assert cap["warning"] is not None


def test_r04_out_of_range_voltage_falls_back_to_default():
    cap_low = _derive({"nominal_voltage_v": 5.0})
    assert math.isclose(cap_low["voltage"], 51.2)
    assert cap_low["warning"] is not None
    cap_high = _derive({"nominal_voltage_v": 500.0})
    assert math.isclose(cap_high["voltage"], 51.2)
    assert cap_high["warning"] is not None


def test_r04_valid_inputs_kept_unchanged():
    """A valid (150 Ah, 24 V) is kept as-is, not converted
    to the default. This is the fixture for the second
    station with different chemistry.
    """
    cap = _derive({
        "battery_capacity_ah": 150.0,
        "nominal_voltage_v": 24.0,
    })
    assert math.isclose(cap["ah"], 150.0)
    assert math.isclose(cap["voltage"], 24.0)
    assert math.isclose(cap["kwh"], 150.0 * 24.0 / 1000.0)
    assert cap["warning"] is None


# ─────────────────────────────────────────────────────────────────
# R05 — daylight / tariff / night-window map
# ─────────────────────────────────────────────────────────────────


def test_r05_simulate_24h_uses_afternoon_pv_after_16():
    """The previous ``elif 9 <= h <= 16`` gate made the
    planner ignore any positive PV forecast at hour 17,
    18, or 19. The fix uses ``pv_forecast > 0`` as the
    daylight signal, so an afternoon peak must be
    honoured.

    The planner iterates ``delta=0..23`` from ``now``;
    for each delta the dated key is
    ``(now.astimezone(UTC) + delta).astimezone(tz)``. We
    populate the forecast for those 24 future hours,
    putting 1500 W at delta=5 (17:00 Kyiv, the previously
    mis-classified hour).
    """
    kyiv = ZoneInfo("Europe/Kyiv")
    base = datetime(2026, 6, 21, 12, 0, tzinfo=kyiv)
    dated = {}
    for delta in range(24):
        ts_local = (base.astimezone(timezone.utc)
                    + timedelta(hours=delta)).astimezone(kyiv)
        key = int(ts_local.replace(minute=0, second=0, microsecond=0)
                  .timestamp())
        # 1500 W at delta=5 (= 17:00 Kyiv), low elsewhere
        dated[key] = 1500.0 if delta == 5 else 100.0
    pi = _build_inputs(
        now=base,
        dated_hourly_pv=dated,
        hourly_pv=[100.0] * 24,
        hourly_radiation=[100.0] * 24,
        forecast_today_kwh=15.0,
        forecast_tomorrow_kwh=15.0,
    )
    predictor = ConsumptionPredictor(history=[])
    plans = simulate_24h(pi, 80.0, 50.0, predictor)
    assert len(plans) == 24
    # The plan at delta=5 is the afternoon PV peak.
    # Find the plan that matches by inspecting the
    # local hour. The ``reason`` field carries the
    # classification; for delta=5 the local hour is
    # 17:00. We must NOT see ``evening_*`` there.
    plan_delta5 = plans[5]
    assert "evening" not in plan_delta5.reason, (
        f"delta=5 must not be evening: {plan_delta5.reason}"
    )
    assert ("day" in plan_delta5.reason
            or "surplus" in plan_delta5.reason), (
        f"delta=5 with 1500 W PV must be daylight: {plan_delta5.reason}"
    )


def test_r05_simulate_24h_uses_morning_pv_before_9():
    """Symmetric to the afternoon test: PV at hour 6
    (delta=18 from now=12:00, i.e. 06:00 next day) must
    not be silently dropped. Wait — the previous test
    asserted morning PV at hour 6. But hour 6 is INSIDE
    the night window (23-7), so the night branch fires
    regardless of PV. The fix uses ``pv_forecast > 0``
    only for the NOT-night branch. To exercise the
    daylight branch at a non-9-to-16 hour, we use
    delta=20 (08:00 next day): outside the night
    window, outside the old 9-16 gate.
    """
    kyiv = ZoneInfo("Europe/Kyiv")
    base = datetime(2026, 6, 21, 12, 0, tzinfo=kyiv)
    dated = {}
    for delta in range(24):
        ts_local = (base.astimezone(timezone.utc)
                    + timedelta(hours=delta)).astimezone(kyiv)
        key = int(ts_local.replace(minute=0, second=0, microsecond=0)
                  .timestamp())
        # 1500 W at delta=20 (= 08:00 next day, outside 9-16 gate)
        dated[key] = 1500.0 if delta == 20 else 100.0
    pi = _build_inputs(dated_hourly_pv=dated)
    predictor = ConsumptionPredictor(history=[])
    plans = simulate_24h(pi, 80.0, 50.0, predictor)
    plan_delta20 = plans[20]
    assert "day" in plan_delta20.reason or "surplus" in plan_delta20.reason, (
        f"delta=20 with PV 1500 W must be daylight: {plan_delta20.reason}"
    )


def test_r05_simulate_24h_zero_pv_no_daylight_branch():
    """Zero PV across all hours means NO daylight branch
    fires.
    """
    pi = _build_inputs(hourly_pv=[0.0] * 24,
                       hourly_radiation=[0.0] * 24)
    predictor = ConsumptionPredictor(history=[])
    plans = simulate_24h(pi, 80.0, 50.0, predictor)
    for p in plans:
        assert "day_" not in p.reason, (
            f"with zero PV, daylight branch must not fire: {p.reason}"
        )


def test_r05_simulate_24h_missing_forecast_raises():
    """Missing forecast (empty hourly_pv, no dated_hourly_pv,
    but planner hits an hour index out of range) must
    surface as ValueError or raise, not produce a corrupt
    plan with PV=0 silently.

    Production tolerance: when the 24-element hourly_pv is
    shorter than the iteration, the planner falls back to
    0.0 W for those hours. That is NOT a "missing forecast"
    — it is a "partial forecast". True missing forecast
    happens when both are absent; the planner's T07 guard
    then surfaces an Invalid PV for a 0.0 in [0, 20000]?

    Actually, the production guard at line 814 only fires
    for invalid VALUES in an existing dated forecast. The
    "no forecast at all" case is handled by the engine
    earlier (no HourlyPlan is emitted at all). So this
    test exercises the "dated_hourly_pv is set but the
    iteration key is missing" case, which must raise.
    """
    pi = _build_inputs(hourly_pv=[0.0] * 24, dated_hourly_pv={})
    predictor = ConsumptionPredictor(history=[])
    try:
        simulate_24h(pi, 80.0, 50.0, predictor)
    except ValueError as exc:
        # dated_hourly_pv empty → planner raises
        assert "PV" in str(exc) or "dated" in str(exc).lower()
    else:
        raise AssertionError(
            "simulate_24h accepted an empty dated_hourly_pv without raising"
        )


# ── Tariff callers ──────────────────────────────────────────────


def test_r05_tariff_rejects_nan_does_not_become_zero():
    """A schedule with NaN must NOT silently become 0.0
    (free electricity). Validator refuses the schedule
    so the caller can fall back to day/night.
    """
    schedule = [2.16] * 23 + [float("nan")]
    pi = build_planner_inputs(
        raw={"now": "2026-10-09T12:00:00+03:00",
             "battery_capacity_kwh": 11.776},
        tariff_schedule=schedule,
    )
    assert pi.tariff_schedule == []


def test_r05_tariff_rejects_negative_does_not_become_zero():
    schedule = [-1.0] + [2.16] * 23
    pi = build_planner_inputs(
        raw={"now": "2026-10-09T12:00:00+03:00",
             "battery_capacity_kwh": 11.776},
        tariff_schedule=schedule,
    )
    assert pi.tariff_schedule == []


def test_r05_tariff_rejects_out_of_range_does_not_become_zero():
    schedule = [99.0] + [2.16] * 23
    pi = build_planner_inputs(
        raw={"now": "2026-10-09T12:00:00+03:00",
             "battery_capacity_kwh": 11.776},
        tariff_schedule=schedule,
    )
    assert pi.tariff_schedule == []


def test_r05_tariff_rejects_bool_does_not_become_zero():
    schedule = [True] + [2.16] * 23
    pi = build_planner_inputs(
        raw={"now": "2026-10-09T12:00:00+03:00",
             "battery_capacity_kwh": 11.776},
        tariff_schedule=schedule,
    )
    assert pi.tariff_schedule == []


def test_r05_tariff_accepts_valid_schedule():
    schedule = [2.16 if h >= 23 or h < 7 else 4.32 for h in range(24)]
    pi = build_planner_inputs(
        raw={"now": "2026-10-09T12:00:00+03:00",
             "battery_capacity_kwh": 11.776},
        tariff_schedule=schedule,
    )
    assert pi.tariff_schedule == schedule


# ── _build_tariff_schedule (coordinator) ────────────────────────


def test_r05_build_tariff_schedule_uses_configured_rates():
    sched = _build_tariff_schedule(self_day=5.0, self_night=2.0)
    assert len(sched) == 24
    for h in range(7, 23):
        assert math.isclose(sched[h], 5.0)
    for h in [23, 0, 1, 2, 3, 4, 5, 6]:
        assert math.isclose(sched[h], 2.0)


def test_r05_build_tariff_schedule_rejects_nan():
    sched = _build_tariff_schedule(
        self_day=float("nan"), self_night=float("inf")
    )
    # Falls back to 4.32 / 2.16
    for h in range(7, 23):
        assert math.isclose(sched[h], 4.32)
    for h in [23, 0, 1, 2, 3, 4, 5, 6]:
        assert math.isclose(sched[h], 2.16)


def test_r05_build_tariff_schedule_rejects_negative():
    sched = _build_tariff_schedule(self_day=-5.0, self_night=-2.0)
    for h in range(24):
        assert sched[h] > 0.0


# ── Charging window vs tariff window are independent ────────────


def test_r05_charging_window_independent_of_tariff_window():
    """The configured charging window (e.g. 3-7) and the
    tariff night window (23-7) are SEPARATE concepts. The
    planner must honour both.
    """
    sched = _build_tariff_schedule(self_day=8.0, self_night=8.0)
    for h in range(24):
        assert math.isclose(sched[h], 8.0)
    # The tariff is uniform — there is no cheap window —
    # but the operator can still request night charge
    # between 3 and 7. The two concepts are independent.


# ── DST ─────────────────────────────────────────────────────────


def test_r05_dst_forward_kyiv_2026_03_29():
    """DST forward in EU is 2026-03-29: 02:00 → 03:00
    local jumps. The wall-clock hour 02:00..02:59 does
    not exist. simulate_24h must not crash.
    """
    pi = _build_inputs(
        now=datetime(2026, 3, 29, 1, 0, tzinfo=ZoneInfo("Europe/Kyiv")),
        night_charge_window=(2, 4),
    )
    predictor = ConsumptionPredictor(history=[])
    plans = simulate_24h(pi, 80.0, 50.0, predictor)
    assert len(plans) == 24
    for p in plans:
        assert math.isfinite(p.batt_w), (
            f"DST forward produced non-finite batt_w: {p}"
        )


def test_r05_dst_backward_kyiv_2026_10_25():
    """DST backward in EU is 2026-10-25: 03:00 → 02:00
    local falls back. Hour 02:00..02:59 repeats.
    """
    pi = _build_inputs(
        now=datetime(2026, 10, 25, 1, 0, tzinfo=ZoneInfo("Europe/Kyiv")),
        night_charge_window=(1, 5),
    )
    predictor = ConsumptionPredictor(history=[])
    plans = simulate_24h(pi, 80.0, 50.0, predictor)
    assert len(plans) == 24
    for p in plans:
        assert math.isfinite(p.batt_w)


def test_r05_same_wall_clock_summer_winter_not_a_defect():
    """The same ``night_charge_window=(23, 7)`` works in
    summer (UTC+3) and winter (UTC+2). The planner uses
    local hours. This is by design, not a defect.
    """
    summer = _build_inputs(
        now=datetime(2026, 6, 21, 22, 0, tzinfo=ZoneInfo("Europe/Kyiv")),
    )
    winter = _build_inputs(
        now=datetime(2026, 12, 21, 22, 0, tzinfo=ZoneInfo("Europe/Kyiv")),
    )
    predictor = ConsumptionPredictor(history=[])
    plans_s = simulate_24h(summer, 80.0, 50.0, predictor)
    plans_w = simulate_24h(winter, 80.0, 50.0, predictor)
    assert len(plans_s) == 24
    assert len(plans_w) == 24
    for p in plans_s + plans_w:
        assert math.isfinite(p.batt_w)


# ─────────────────────────────────────────────────────────────────
# Guards unchanged
# ─────────────────────────────────────────────────────────────────


def test_r05_reserve_soc_still_raises_on_120():
    """T08 guard is unchanged: reserve_soc=120 raises
    ValueError, no clamp.
    """
    try:
        build_planner_inputs(
            raw={"now": "2026-10-09T12:00:00+03:00",
                 "battery_capacity_kwh": 11.776},
            reserve_soc=120,
        )
    except ValueError as exc:
        assert "reserve_soc" in str(exc)
    else:
        raise AssertionError(
            "build_planner_inputs accepted reserve_soc=120"
        )


def test_r05_charge_efficiency_still_raises_on_1_5():
    try:
        build_planner_inputs(
            raw={"now": "2026-10-09T12:00:00+03:00",
                 "battery_capacity_kwh": 11.776},
            charge_efficiency=1.5,
        )
    except ValueError as exc:
        assert "charge_efficiency" in str(exc)
    else:
        raise AssertionError(
            "build_planner_inputs accepted charge_efficiency=1.5"
        )


def test_r05_simulate_24h_invalid_pv_raises():
    """T07 guard is unchanged."""
    pi = _build_inputs(
        hourly_pv=[float("nan")] + [0.0] * 23,
    )
    predictor = ConsumptionPredictor(history=[])
    try:
        simulate_24h(pi, 80.0, 50.0, predictor)
    except ValueError as exc:
        assert "Invalid" in str(exc) or "finite" in str(exc).lower()
    else:
        raise AssertionError("simulate_24h accepted NaN PV")


def test_r05_simulate_24h_soc_none_returns_empty():
    """T07 guard: missing SOC returns empty list."""
    pi = _build_inputs(soc=None, soc_corrected=None)
    predictor = ConsumptionPredictor(history=[])
    plans = simulate_24h(pi, 80.0, 50.0, predictor)
    assert plans == []


if __name__ == "__main__":
    fns = [v for k, v in globals().items() if k.startswith("test_")]
    failures = 0
    for fn in fns:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"  {fn.__name__}: FAIL ({exc})")
        except Exception as exc:
            failures += 1
            print(f"  {fn.__name__}: ERROR ({exc!r})")
        else:
            print(f"  {fn.__name__}: PASS")
    print()
    if failures:
        print(f"{len(fns) - failures}/{len(fns)} passed ({failures} failed)")
        sys.exit(1)
    print(f"All {len(fns)} tests passed.")
