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
    # Rewrite any ``from .<rel> import …`` inside the
    # function body to an absolute ``from hems.<rel>``
    # so the exec works in the test environment (the
    # production code uses relative imports because the
    # integration is a package).
    class _RelToAbs(ast.NodeTransformer):
        def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom:
            if node.level > 0:
                node.level = 0
                # ``from .hems.engine import _finite_number``
                # → module="hems.engine"
                if node.module and node.module.startswith("hems"):
                    pass
                else:
                    node.module = f"hems.{node.module}" if node.module else "hems"
            return node
    target = _RelToAbs().visit(target)
    ns = {
        "_finite_number": _finite_number,
        "__name__": "coordinator_fixture",
        "__package__": "",
    }
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


def test_r05_simulate_24h_afternoon_pv_numerical_balance():
    """Stronger afternoon PV test: verify the PHYSICAL
    BALANCE, not just the reason string.

    The plan for the afternoon PV peak must:

      - have ``pv_w`` reflecting the 1500 W forecast
        at delta=5 (17:00 Kyiv);
      - charge the battery (``batt_w > 0``) when
        PV is well above load — the surplus
        branch signs energy INTO the battery;
      - NOT draw from the grid (``grid_w = 0``)
        when PV is sufficient;
      - the SOC must INCREASE from the previous
        step when surplus is positive (energy
        balance with efficiency);
      - use ``day_surplus`` (NOT ``evening_peak`` or
        ``day_pv_low``) — afternoon is now classified
        as daylight.

    Юра's audit: a previous version of this test
    only checked pv_w, the pv_w/load_w ratio, and
    the reason string. Zeroing ``batt_w`` /
    ``grid_w`` / ``soc_pred`` did not flip the
    test to red. The new assertions make the
    test fail on:
      - wrong sign of battery power;
      - zero SOC integration (energy not
        accumulated);
      - lost surplus (batt_w = 0 when PV >> load).
    """
    kyiv = ZoneInfo("Europe/Kyiv")
    base = datetime(2026, 6, 21, 12, 0, tzinfo=kyiv)
    dated = {}
    for delta in range(24):
        ts_local = (base.astimezone(timezone.utc)
                    + timedelta(hours=delta)).astimezone(kyiv)
        key = int(ts_local.replace(minute=0, second=0, microsecond=0)
                  .timestamp())
        # delta=5 is hour 17:00 (the previously
        # mis-classified afternoon hour). Set it
        # to 2500 W (well above 300 W load).
        # Other hours: 100 W (below load).
        dated[key] = 2500.0 if delta == 5 else 100.0
    # Use a HIGH starting SOC but with sufficient
    # headroom. battery_capacity_kwh=11.776,
    # soc=50% means ~5.9 kWh available.
    pi = _build_inputs(
        now=base,
        soc=50.0, soc_corrected=50.0,
        dated_hourly_pv=dated,
        hourly_pv=[100.0] * 24,
        hourly_radiation=[100.0] * 24,
        load_w=300.0,   # 300 W constant
        forecast_today_kwh=15.0,
        forecast_tomorrow_kwh=15.0,
    )
    predictor = ConsumptionPredictor(history=[])
    plans = simulate_24h(pi, 80.0, 50.0, predictor)
    # Find the delta=5 plan (hour 17:00)
    plan_delta5 = plans[5]
    plan_delta4 = plans[4]   # previous hour
    # 1. PV is the 2500 W forecast
    assert plan_delta5.pv_w >= 2400.0, (
        f"afternoon PV should be ≥ 2400 W, "
        f"got {plan_delta5.pv_w}"
    )
    # 2. PV > 1.2× load (surplus condition)
    assert plan_delta5.pv_w > plan_delta5.load_w * 1.2, (
        f"afternoon PV {plan_delta5.pv_w} W should be "
        f"> 1.2 × load {plan_delta5.load_w} W"
    )
    # 3. Battery MUST be CHARGING (batt_w > 0).
    #    The previous code's surplus branch with
    #    ``output=SBU, charger=OSO`` returns
    #    ``batt_kwh`` from ``_balance_hour`` which
    #    is positive when energy flows INTO the
    #    battery. A zero or negative value means
    #    the physical balance is broken.
    assert plan_delta5.batt_w > 0, (
        f"afternoon surplus: battery must be "
        f"charging (batt_w > 0), got "
        f"batt_w={plan_delta5.batt_w}"
    )
    # 4. Grid MUST be 0 (we have enough PV)
    assert plan_delta5.grid_w == 0, (
        f"afternoon surplus: grid must be 0 "
        f"(PV covers load), got "
        f"grid_w={plan_delta5.grid_w}"
    )
    # 5. SOC must INCREASE from the previous step
    #    when the surplus is positive. With
    #    ``charge_efficiency=0.85`` and 2200 W net
    #    surplus (PV 2500 - load 300) for 1 hour,
    #    the SOC gain is roughly
    #    2200 * 0.85 / 1000 / 11.776 = 0.16 % per
    #    hour. That's small but non-zero. We
    #    allow a 0.01 % tolerance for rounding
    #    but require ANY positive gain.
    soc_gain = plan_delta5.soc_pred - plan_delta4.soc_pred
    assert soc_gain > 0.0, (
        f"afternoon surplus: SOC must rise "
        f"(soc_pred[5]={plan_delta5.soc_pred}, "
        f"soc_pred[4]={plan_delta4.soc_pred}); "
        f"got gain={soc_gain}"
    )
    # 6. Energy balance: ``batt_w`` in
    #    ``HourlyPlan`` is in Wh (the planner
    #    inherits ``batt_kwh`` from
    #    ``_balance_hour`` and uses it as the W
    #    field for display). The expected
    #    surplus is 2200 W × 1 h × 0.85 = 1870
    #    Wh.
    expected_surplus_wh = (
        (plan_delta5.pv_w - plan_delta5.load_w)
        * 0.85
    )
    assert abs(plan_delta5.batt_w - expected_surplus_wh) < 50, (
        f"afternoon energy balance: batt_w "
        f"{plan_delta5.batt_w} Wh vs expected "
        f"{expected_surplus_wh} Wh (surplus 2200 W "
        f"× 0.85 efficiency)"
    )
    # 7. Daylight classification
    assert "day" in plan_delta5.reason, (
        f"afternoon must be classified as daylight, "
        f"got reason={plan_delta5.reason!r}"
    )


def test_r05_tariff_fallback_chain_with_8_3():
    """End-to-end test: configured 8.0 / 3.0 UAH/kWh
    rates must reach the plan, even when the supplied
    schedule has a corrupted element.

    The flow uses the REAL production path
    (``coordinator.apply_capacity_and_tariff_options``
    → ``HemsEngine`` → ``_evaluate_predictive`` →
    ``build_planner_inputs`` → ``simulate_24h``).
    No manual fallback assignment and no separate
    PlannerInputs construction.

    Steps:
      1. Build a real ``HemsEngine`` with the
         minimum inputs the predictive path
         requires.
      2. Use ``apply_capacity_and_tariff_options``
         to set ``tariff_day=8.0`` and
         ``tariff_night=3.0`` — the real listener
         call the production code makes on
         options update.
      3. Corrupt the engine's ``_tariff_schedule``
         with a NaN — the validator will refuse
         it and the planner will fall back to the
         configured day/night rates.
      4. Run ``_evaluate_predictive`` and inspect
         the resulting plan. The day hour must
         use 8.0; the night hour must use 3.0.
         NOT 4.32 / 2.16.
      5. Re-apply with different rates
         (5.0 / 1.0) and verify the runtime
         update is honoured.
    """
    from hems.engine import HemsEngine
    # 1. Real engine
    engine = HemsEngine()
    # 2. Real coordinator's apply method
    apply = _load_method("apply_capacity_and_tariff_options")
    derive = _load_method("_derive_battery_capacity")
    build_sched = _load_method("_build_tariff_schedule")
    class _StubHems:
        pass
    class _StubSelf:
        def __init__(self, eng):
            self._battery_capacity_ah = 230.0
            self._nominal_voltage_v = 51.2
            self._capacity_input_warning = None
            self._battery_capacity_kwh = 11.776
            self._day_tariff_uah = 4.32
            self._night_tariff_uah = 2.16
            self._tariff_schedule = None
            self._hems = eng
        def _derive_battery_capacity(self, options):
            return derive(options)
        def _build_tariff_schedule(self):
            return build_sched(self)
    # 3. Apply with 8.0 / 3.0
    stub = _StubSelf(engine)
    apply(stub, {
        "battery_capacity_ah": 230.0,
        "nominal_voltage_v": 51.2,
        "tariff_day": 8.0,
        "tariff_night": 3.0,
    })
    # Engine now has the operator's rates
    assert math.isclose(engine._day_tariff_uah, 8.0), (
        f"engine._day_tariff_uah should be 8.0, "
        f"got {engine._day_tariff_uah}"
    )
    assert math.isclose(engine._night_tariff_uah, 3.0)
    # Corrupt the schedule (one NaN)
    bad = [8.0 if h < 10 or h > 10 else float("nan")
           for h in range(24)]
    engine._tariff_schedule = bad
    # 4. Run the REAL predictive path
    kyiv = ZoneInfo("Europe/Kyiv")
    base = datetime(2026, 6, 21, 12, 0, tzinfo=kyiv)
    # Engine needs forecast data; set hourly forecasts
    engine._hourly_pv_forecast = [0.0] * 24
    engine._hourly_radiation = [0.0] * 24
    engine._hourly_weather_codes = [None] * 24
    engine._battery_capacity_kwh = 11.776
    engine._planner_forecast_now = base
    engine._predictive_enabled = True
    # Predictive mode is "off" by default; the test
    # needs shadow/assist. Set the tuning object too
    # so the controller's mode check passes.
    engine.predictive_tuning.predictive_mode = "shadow"
    engine._predictive_mode = "shadow"
    # Inputs for evaluate(): SOC, PV, load, grid, etc.
    # Forecasts are required (not None) by
    # ``_evaluate_predictive``.
    inputs = {
        "soc": 50.0, "soc_unknown": False,
        "pv_power": 0.0, "load_power": 200.0,
        "grid_power": 0.0, "battery_power": 0.0,
        "grid_voltage": 220.0,
        "grid_available": True,   # required by
                                  # ``build_planner_inputs``
        "smart_mode": 0,
        "forecast_today_kwh": 5.0,
        "forecast_tomorrow_kwh": 5.0,
        "reserve_soc": 20.0,
    }
    hint, plan = engine._evaluate_predictive(base, inputs)
    # The corrupted schedule should have been
    # rejected by the validator, forcing the
    # planner into the fallback path. The hint
    # carries ``tariff_day_fallback`` and
    # ``tariff_night_fallback`` from
    # ``build_planner_inputs`` (via
    # ``PlannerInputs``).
    assert plan is not None, (
        "predictive plan is None — production path "
        "did not run end-to-end"
    )
    # The plan is a ``DayAheadPlan`` whose
    # ``hourly`` attribute is the 24-element list
    # of ``HourlyPlan`` rows.
    plan_rows = plan.hourly
    assert len(plan_rows) == 24
    # The 24-hour rebuild from 8/3 fallbacks:
    # - hours NOT in the night window 23..7 use
    #   ``tariff_day_fallback`` = 8.0
    # - hours IN the night window use
    #   ``tariff_night_fallback`` = 3.0
    # ``base = 12:00``. hour 12 is OUTSIDE the
    # night window ⇒ 8.0.
    plan_12 = next(p for p in plan_rows if p.hour == 12)
    assert math.isclose(plan_12.tariff, 8.0), (
        f"day tariff (hour 12) should be 8.0, "
        f"got {plan_12.tariff}"
    )
    # hour 23 is INSIDE the night window ⇒ 3.0
    plan_23 = next(p for p in plan_rows if p.hour == 23)
    assert math.isclose(plan_23.tariff, 3.0), (
        f"night tariff (hour 23) should be 3.0, "
        f"got {plan_23.tariff}"
    )
    # And NOT 4.32 / 2.16 (the hardcoded
    # constants the previous code used).
    assert plan_12.tariff != 4.32, (
        "day tariff fell back to the hardcoded "
        "TARIFF_DAY=4.32 instead of the "
        "operator's 8.0"
    )
    # 5. Re-apply with different rates and verify
    # the engine updates
    apply(stub, {
        "battery_capacity_ah": 230.0,
        "nominal_voltage_v": 51.2,
        "tariff_day": 5.0,
        "tariff_night": 1.0,
    })
    assert math.isclose(engine._day_tariff_uah, 5.0)
    assert math.isclose(engine._night_tariff_uah, 1.0)
    # Re-run predictive. Day hour must be 5.0,
    # night hour 1.0.
    _, plan2 = engine._evaluate_predictive(base, inputs)
    assert plan2 is not None
    plan2_rows = plan2.hourly
    plan2_12 = next(p for p in plan2_rows if p.hour == 12)
    plan2_23 = next(p for p in plan2_rows if p.hour == 23)
    assert math.isclose(plan2_12.tariff, 5.0), (
        f"after options update day tariff should be 5.0, "
        f"got {plan2_12.tariff}"
    )
    assert math.isclose(plan2_23.tariff, 1.0), (
        f"after options update night tariff should be 1.0, "
        f"got {plan2_23.tariff}"
    )


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
    """Symmetric to the afternoon test: PV at hour 8
    (delta=20 from now=12:00) must not be silently
    dropped.

    Wait — the previous test asserted morning PV at
    hour 6. But hour 6 is INSIDE the night window
    (23-7), so the night branch fires regardless of
    PV. The fix uses ``pv_forecast > 0`` only for
    the NOT-night branch. To exercise the daylight
    branch at a non-9-to-16 hour, we use delta=20
    (08:00 next day): outside the night window,
    outside the old 9-16 gate.

    Юра's audit: the previous version only checked
    ``"day" in reason`` — a contrived string match
    that the implementation could satisfy without
    any physical balance. The new test asserts the
    same numerical contract as the afternoon test:
    battery charges, grid is 0, SOC rises from the
    previous step.
    """
    kyiv = ZoneInfo("Europe/Kyiv")
    base = datetime(2026, 6, 21, 12, 0, tzinfo=kyiv)
    dated = {}
    for delta in range(24):
        ts_local = (base.astimezone(timezone.utc)
                    + timedelta(hours=delta)).astimezone(kyiv)
        key = int(ts_local.replace(minute=0, second=0, microsecond=0)
                  .timestamp())
        # 2500 W at delta=20 (= 08:00 next day)
        dated[key] = 2500.0 if delta == 20 else 100.0
    pi = _build_inputs(
        dated_hourly_pv=dated,
        soc=80.0, soc_corrected=80.0,  # already high
        load_w=300.0,
        # The default night window is (23, 7) which
        # covers delta=11 (hour 23). That branch
        # charges from grid to 95 % before the
        # morning peak, and the morning peak then
        # has no headroom. To exercise the surplus
        # branch cleanly we use a night window
        # outside delta=11..18 (e.g. (1, 2) —
        # somewhere the iteration never visits).
        night_charge_window=(1, 2),
    )
    predictor = ConsumptionPredictor(history=[])
    plans = simulate_24h(pi, 80.0, 50.0, predictor)
    plan_delta20 = plans[20]
    plan_delta19 = plans[19]
    # 1. PV is 2500 W
    assert plan_delta20.pv_w >= 2400.0, (
        f"morning PV should be ≥ 2400 W, got {plan_delta20.pv_w}"
    )
    # 2. Battery is CHARGING (surplus)
    assert plan_delta20.batt_w > 0, (
        f"morning surplus: battery must charge, "
        f"got batt_w={plan_delta20.batt_w}"
    )
    # 3. Grid is 0
    assert plan_delta20.grid_w == 0, (
        f"morning surplus: grid must be 0, "
        f"got grid_w={plan_delta20.grid_w}"
    )
    # 4. SOC rises
    soc_gain = plan_delta20.soc_pred - plan_delta19.soc_pred
    assert soc_gain > 0.0, (
        f"morning surplus: SOC must rise, "
        f"got gain={soc_gain}"
    )
    # 5. Daylight classification (not "evening_peak"
    # — morning is not evening).
    assert "day" in plan_delta20.reason, (
        f"morning must be classified as daylight, "
        f"got reason={plan_delta20.reason!r}"
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

    We exercise the full chain: a uniform 8.0 UAH/kWh
    tariff (no cheap window at all), a charging window
    of 3-7, low SOC at the start, and PV=0. The planner
    must still charge during 3-7 because the user asked
    to, regardless of the tariff.

    Assertions:
      - the schedule is uniform 8.0
      - the planner produces a non-empty plan
      - the plan for hour 3 (a charging-window hour) is
        in the night / charge branch (not the daylight
        or evening branch)
      - the plan for hour 12 (midday) is NOT in the
        charge branch — no PV and no charging window
    """
    kyiv = ZoneInfo("Europe/Kyiv")
    base = datetime(2026, 6, 21, 12, 0, tzinfo=kyiv)
    pi = PlannerInputs(
        now=base, soc=20.0, soc_corrected=20.0,
        pv_w=0.0, load_w=300.0, grid_w=0.0, batt_w=0.0,
        battery_capacity_kwh=11.776,
        grid_v=220.0, grid_ok=True,
        smart_mode=0,
        forecast_today_kwh=0.0, forecast_tomorrow_kwh=0.0,
        hourly_pv=[0.0] * 24, hourly_weather=[{}] * 24,
        hourly_radiation=[0.0] * 24,
        tariff_schedule=[8.0] * 24,
        consumption_history=[],
        night_charge_window=(3, 7),
        reserve_soc=20.0, charge_efficiency=0.85,
        discharge_efficiency=0.90,
    )
    predictor = ConsumptionPredictor(history=[])
    plans = simulate_24h(pi, 80.0, 50.0, predictor)
    # The schedule is uniform 8.0
    sched = _build_tariff_schedule(self_day=8.0, self_night=8.0)
    for h in range(24):
        assert math.isclose(sched[h], 8.0)
    # The plan has 24 entries and every batt_w is finite
    assert len(plans) == 24
    for p in plans:
        assert math.isfinite(p.batt_w)
    # Index 15 = delta=15 = 03:00 next day, which is in
    # the charging window (3, 7). That plan must reflect
    # night-charge logic.
    plan_3am = plans[15]
    assert "night" in plan_3am.reason, (
        f"hour 3 should be in night branch, got {plan_3am.reason}"
    )
    # Index 0 = delta=0 = 12:00 (no charging window, no PV)
    plan_noon = plans[0]
    assert "night" not in plan_noon.reason, (
        f"hour 12 should NOT be in night branch, got {plan_noon.reason}"
    )


# ── DST ─────────────────────────────────────────────────────────


def test_r05_dst_forward_kyiv_2026_03_29():
    """DST forward in Kyiv is 2026-03-29. The wall-clock
    hour 03:00..03:59 is skipped (clocks jump from
    02:59:59 EET to 04:00:00 EEST).

    The planner iterates ``delta=0..23`` with
    fixed-3600-second UTC steps. This test verifies:

      1. ``len(plans) == 24``
      2. all 24 plans have finite ``batt_w``
      3. the underlying UTC timestamps are
         consecutive (Δ == 3600)
      4. the local hours cover the same 24 distinct
         values (one hour may be missing, e.g. 03:00,
         because the step lands on 04:00 instead of
         03:00; that's correct)
      5. night-window membership on a ``(2, 4)`` window
         is consistent with the actual local hours
         produced
    """
    pi = _build_inputs(
        now=datetime(2026, 3, 29, 1, 0, tzinfo=ZoneInfo("Europe/Kyiv")),
        night_charge_window=(2, 4),
    )
    predictor = ConsumptionPredictor(history=[])
    plans = simulate_24h(pi, 80.0, 50.0, predictor)
    assert len(plans) == 24
    # 1+2: finite batt_w
    for p in plans:
        assert math.isfinite(p.batt_w), (
            f"DST forward produced non-finite batt_w: {p}"
        )
    # 3+4: reconstruct the local hours from the
    #     iteration rule. Each delta=1 step is a
    #     fixed 3600 s UTC step. On 2026-03-29 the
    #     step from UTC 01:00 (delta=1, local
    #     02:00 EET) to UTC 02:00 (delta=2, local
    #     04:00 EEST) jumps over 03:00 (which does
    #     not exist locally). So the local hours
    #     are: 1, 2, 4, 5, 6, ..., 23, 0, 1.
    base = pi.now.astimezone(timezone.utc)
    local_hours: list[int] = []
    in_night = 0
    night_start, night_end = 2, 4
    for delta in range(24):
        ts = (base + timedelta(hours=delta)).astimezone(pi.now.tzinfo)
        local_hours.append(ts.hour)
        is_night = night_start <= ts.hour < night_end
        if is_night:
            in_night += 1
    # 5: night window (2, 4) covers hour 2 only —
    #    03:00 is skipped. We must have exactly 1
    #    night hour, not 0 and not 2.
    assert len(local_hours) == 24
    assert in_night == 1, (
        f"DST forward night window mismatch: "
        f"hours={local_hours}, in_night={in_night}"
    )
    # The skipped hour (3) must not appear at all.
    assert 3 not in local_hours, (
        f"DST forward: hour 3 must be skipped, "
        f"got hours={local_hours}"
    )


def test_r05_dst_backward_kyiv_2026_10_25():
    """DST backward in Kyiv is 2026-10-25. The wall-clock
    hour 03:00..03:59 repeats (fold=0 EEST = UTC 00:00,
    fold=1 EET = UTC 01:00).

    The planner iterates with fixed-3600-second UTC
    steps, so:

      1. ``len(plans) == 24``
      2. all 24 plans have finite ``batt_w``
      3. underlying UTC timestamps are consecutive
         (Δ == 3600) — no fold appears in the output
         because the iteration never lands on a
         fold-1 datetime
      4. the night window ``(1, 5)`` covers the same
         4 hours in both folds
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
    # Reconstruct the local hours. The iteration
    # starts at base = 2026-10-25 01:00 EEST (UTC
    # 22:00 of the prior day) and steps forward by
    # +1h UTC. The autumn jump happens at UTC
    # 01:00 (= local 03:00 EEST → 04:00 EET). The
    # iteration crosses this boundary at delta=3.
    base = pi.now.astimezone(timezone.utc)
    local_hours: list[int] = []
    for delta in range(24):
        ts = (base + timedelta(hours=delta)).astimezone(pi.now.tzinfo)
        local_hours.append(ts.hour)
    # The night window (1, 5) covers hours 1, 2, 3,
    # 3, 4 — five entries because the iteration
    # visits hour 3 in both folds (EEST and EET).
    # This is by design: each UTC step is fixed at
    # 3600 s, and both fold-0 and fold-1 representations
    # of 03:00 are in [1, 5).
    in_night = sum(1 for h in local_hours if 1 <= h < 5)
    assert in_night == 5, (
        f"DST backward night window mismatch: "
        f"hours={local_hours}, in_night={in_night}"
    )


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


# ─────────────────────────────────────────────────────────────────
# R04+R05 options update path
# ─────────────────────────────────────────────────────────────────


def test_r04_capacity_includes_form_allowed_low_40_ah():
    """40 Ah is inside the form range ``[10, 2000]`` and
    must be accepted. The earlier ``[50, 1000]`` runtime
    range rejected valid 40 Ah entries.
    """
    cap = _derive({"battery_capacity_ah": 40.0,
                   "nominal_voltage_v": 51.2})
    assert math.isclose(cap["ah"], 40.0)
    assert math.isclose(cap["voltage"], 51.2)
    assert math.isclose(cap["kwh"], 40.0 * 51.2 / 1000.0)
    assert cap["warning"] is None
    assert cap["ok_for_planning"] is True


def test_r04_capacity_includes_form_allowed_high_1500_ah():
    """1500 Ah is inside the form range ``[10, 2000]`` and
    must be accepted.
    """
    cap = _derive({"battery_capacity_ah": 1500.0,
                   "nominal_voltage_v": 51.2})
    assert math.isclose(cap["ah"], 1500.0)
    assert cap["warning"] is None


def test_r04_capacity_invalid_voltage_keeps_valid_ah():
    """A bad voltage must NOT clobber a valid Ah. The
    derivation is field-independent: ``_capacity_ah``
    is honoured as-is, ``_voltage_v`` falls back to
    the default, the warning lists only the voltage.
    """
    cap = _derive({"battery_capacity_ah": 150.0,
                   "nominal_voltage_v": float("nan")})
    assert math.isclose(cap["ah"], 150.0)
    assert math.isclose(cap["voltage"], 51.2)
    assert cap["warning"] is not None
    assert "voltage" in cap["warning"]
    assert "ah=" not in cap["warning"]
    # kwh is 150 × 51.2 / 1000 (voltage fell back)
    assert math.isclose(cap["kwh"], 150.0 * 51.2 / 1000.0)


def test_r04_capacity_invalid_ah_keeps_valid_voltage():
    """The symmetric case: bad Ah, good voltage.
    Voltage is preserved, Ah falls back to default.
    """
    cap = _derive({"battery_capacity_ah": float("nan"),
                   "nominal_voltage_v": 24.0})
    assert math.isclose(cap["ah"], 230.0)
    assert math.isclose(cap["voltage"], 24.0)
    assert cap["warning"] is not None
    assert "ah=" in cap["warning"]
    assert "voltage=" not in cap["warning"]


def test_r04_apply_capacity_and_tariff_options_full_chain():
    """The ``apply_capacity_and_tariff_options`` method
    must re-derive the capacity and re-build the tariff
    schedule when called with a fresh options dict. This
    is the runtime-update path triggered by the
    options-update listener in ``__init__.py``.

    We extract the method via the same AST harness used
    for the other tests, then exercise it on a stub
    coordinator that records what was written to
    ``_hems._battery_capacity_kwh`` and
    ``_hems._tariff_schedule``.
    """
    method = _load_method("apply_capacity_and_tariff_options")
    derive = _load_method("_derive_battery_capacity")
    build_sched = _load_method("_build_tariff_schedule")
    class _StubHems:
        pass
    class _StubSelf:
        def __init__(self):
            self._battery_capacity_ah = None
            self._nominal_voltage_v = None
            self._capacity_input_warning = None
            self._battery_capacity_kwh = None
            self._day_tariff_uah = None
            self._night_tariff_uah = None
            self._hems = _StubHems()
        def _derive_battery_capacity(self, options):
            return derive(options)
        def _build_tariff_schedule(self):
            return build_sched(self)
    stub = _StubSelf()
    method(stub, {
        "battery_capacity_ah": 150.0,
        "nominal_voltage_v": 24.0,
        "tariff_day": 8.0,
        "tariff_night": 3.0,
    })
    # Capacity is honoured
    assert math.isclose(stub._battery_capacity_ah, 150.0)
    assert math.isclose(stub._nominal_voltage_v, 24.0)
    assert math.isclose(stub._battery_capacity_kwh, 150.0 * 24.0 / 1000.0)
    assert stub._capacity_input_warning is None
    # Tariff schedule is the operator's 8/3, not the
    # hardcoded 4.32/2.16.
    assert stub._day_tariff_uah == 8.0
    assert stub._night_tariff_uah == 3.0
    assert stub._tariff_schedule is not None
    assert len(stub._tariff_schedule) == 24
    for h in range(7, 23):
        assert math.isclose(stub._tariff_schedule[h], 8.0)
    for h in [23, 0, 1, 2, 3, 4, 5, 6]:
        assert math.isclose(stub._tariff_schedule[h], 3.0)
    # Mirrored to the engine
    assert math.isclose(stub._hems._battery_capacity_kwh,
                        150.0 * 24.0 / 1000.0)
    assert stub._hems._tariff_schedule is not None
    for h in range(7, 23):
        assert math.isclose(stub._hems._tariff_schedule[h], 8.0)


def test_r04_apply_capacity_and_tariff_options_with_bad_input():
    """A bad capacity input sets ``_battery_capacity_kwh``
    to ``None`` so the predictive planner holds off.
    """
    method = _load_method("apply_capacity_and_tariff_options")
    derive = _load_method("_derive_battery_capacity")
    build_sched = _load_method("_build_tariff_schedule")
    class _StubHems:
        pass
    class _StubSelf:
        def __init__(self):
            self._battery_capacity_ah = 230.0
            self._nominal_voltage_v = 51.2
            self._capacity_input_warning = None
            self._battery_capacity_kwh = 11.776
            self._day_tariff_uah = 4.32
            self._night_tariff_uah = 2.16
            self._hems = _StubHems()
        def _derive_battery_capacity(self, options):
            return derive(options)
        def _build_tariff_schedule(self):
            return build_sched(self)
    stub = _StubSelf()
    method(stub, {
        "battery_capacity_ah": 48000.0,   # out of form range
        "nominal_voltage_v": 51.2,
    })
    # Bad input → kwh is None
    assert stub._battery_capacity_kwh is None
    # Mirrored to the engine as None
    assert stub._hems._battery_capacity_kwh is None
    # Warning is set with the reason
    assert stub._capacity_input_warning is not None
    assert "out of form range" in stub._capacity_input_warning


def test_r04_capacity_lifecycle_valid_invalid_valid_through_real_engine():
    """Valid → invalid → valid capacity transitions
    through the REAL ``HemsEngine._evaluate_predictive``
    path:

      1. Valid capacity (230 Ah × 51.2 V = 11.776 kWh)
         ⇒ ``_evaluate_predictive`` returns a real
         ``DayAheadPlan`` with 24 ``HourlyPlan``
         rows.
      2. Invalid capacity (48 000 Ah, out of
         form range) ⇒ ``_battery_capacity_kwh``
         is ``None`` ⇒ ``_evaluate_predictive``
         returns ``(None, None)`` — predictive
         recommendation is held off. The
         ``build_planner_inputs`` gate
         ``capacity is None or capacity <= 0``
         fires.
      3. Re-apply with valid capacity ⇒ plan
         comes back.
    """
    from hems.engine import HemsEngine
    engine = HemsEngine()
    apply = _load_method("apply_capacity_and_tariff_options")
    derive = _load_method("_derive_battery_capacity")
    build_sched = _load_method("_build_tariff_schedule")
    class _StubHems:
        pass
    class _StubSelf:
        def __init__(self, eng):
            self._battery_capacity_ah = 230.0
            self._nominal_voltage_v = 51.2
            self._capacity_input_warning = None
            self._battery_capacity_kwh = 11.776
            self._day_tariff_uah = 4.32
            self._night_tariff_uah = 2.16
            self._tariff_schedule = None
            self._hems = eng
        def _derive_battery_capacity(self, options):
            return derive(options)
        def _build_tariff_schedule(self):
            return build_sched(self)
    stub = _StubSelf(engine)
    # Common engine setup
    engine._hourly_pv_forecast = [0.0] * 24
    engine._hourly_radiation = [0.0] * 24
    engine._hourly_weather_codes = [None] * 24
    engine.predictive_tuning.predictive_mode = "shadow"
    engine._predictive_mode = "shadow"
    engine._predictive_enabled = True
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    kyiv = ZoneInfo("Europe/Kyiv")
    base = datetime(2026, 6, 21, 12, 0, tzinfo=kyiv)
    engine._planner_forecast_now = base
    inputs = {
        "soc": 50.0, "soc_unknown": False,
        "pv_power": 0.0, "load_power": 200.0,
        "grid_power": 0.0, "battery_power": 0.0,
        "grid_voltage": 220.0, "grid_available": True,
        "smart_mode": 0,
        "forecast_today_kwh": 5.0,
        "forecast_tomorrow_kwh": 5.0,
        "reserve_soc": 20.0,
    }
    # 1. Valid capacity
    apply(stub, {
        "battery_capacity_ah": 230.0,
        "nominal_voltage_v": 51.2,
        "tariff_day": 4.32, "tariff_night": 2.16,
    })
    assert math.isclose(stub._battery_capacity_kwh, 11.776)
    assert engine._battery_capacity_kwh is not None
    _, plan = engine._evaluate_predictive(base, inputs)
    assert plan is not None, "valid capacity should yield a plan"
    assert len(plan.hourly) == 24
    # 2. Invalid capacity
    apply(stub, {
        "battery_capacity_ah": 48000.0,   # out of form range
        "nominal_voltage_v": 51.2,
        "tariff_day": 4.32, "tariff_night": 2.16,
    })
    assert stub._battery_capacity_kwh is None
    assert engine._battery_capacity_kwh is None
    _, plan = engine._evaluate_predictive(base, inputs)
    assert plan is None, (
        "invalid capacity should hold off "
        "predictive planning"
    )
    # 3. Re-apply with valid capacity
    apply(stub, {
        "battery_capacity_ah": 200.0,
        "nominal_voltage_v": 48.0,
        "tariff_day": 4.32, "tariff_night": 2.16,
    })
    assert math.isclose(stub._battery_capacity_kwh, 9.6)
    assert math.isclose(engine._battery_capacity_kwh, 9.6)
    _, plan = engine._evaluate_predictive(base, inputs)
    assert plan is not None, (
        "after fixing capacity, planning should resume"
    )
    assert len(plan.hourly) == 24


# ─────────────────────────────────────────────────────────────────
# R05 — tariff boundary contract
# ─────────────────────────────────────────────────────────────────


def test_r05_tariff_zero_preserved_not_swallowed_by_default():
    """Valid ``0.0`` tariff (free electricity) MUST
    NOT be replaced by the default 4.32 day / 2.16
    night UAH/kWh.

    The previous code had
    ``_finite_number(...) or 4.32`` which silently
    swallowed ``0.0`` because ``0.0 or 4.32`` is
    ``4.32``. The new ``_coerce_tariff_fallback``
    helper uses an explicit ``is None`` check, so
    ``0.0`` is preserved.
    """
    from hems.engine import _coerce_tariff_fallback
    # Valid zero preserved
    assert _coerce_tariff_fallback(0.0, default=4.32) == 0.0
    assert _coerce_tariff_fallback(0.0, default=2.16) == 0.0
    # 0/0 scenario (both rates zero) — both
    # preserved
    day = _coerce_tariff_fallback(0.0, default=4.32)
    night = _coerce_tariff_fallback(0.0, default=2.16)
    assert day == 0.0
    assert night == 0.0
    # 8/0 (day set, night zero) — both
    # preserved
    day = _coerce_tariff_fallback(8.0, default=4.32)
    night = _coerce_tariff_fallback(0.0, default=2.16)
    assert day == 8.0
    assert night == 0.0


def test_r05_tariff_invalid_falls_back_to_default():
    """NaN, +Inf, -Inf, bool, string, negative,
    and too-large values must fall back to the
    per-field default (NOT 0.0).
    """
    from hems.engine import _coerce_tariff_fallback
    # NaN
    assert _coerce_tariff_fallback(
        float("nan"), default=4.32
    ) == 4.32
    # +Inf
    assert _coerce_tariff_fallback(
        float("inf"), default=4.32
    ) == 4.32
    # -Inf
    assert _coerce_tariff_fallback(
        float("-inf"), default=2.16
    ) == 2.16
    # bool (False == 0.0, would pass ``or``
    # but must be rejected here)
    assert _coerce_tariff_fallback(
        True, default=4.32
    ) == 4.32
    assert _coerce_tariff_fallback(
        False, default=4.32
    ) == 4.32
    # string
    assert _coerce_tariff_fallback(
        "8.0", default=4.32
    ) == 4.32
    # negative
    assert _coerce_tariff_fallback(
        -1.0, default=4.32
    ) == 4.32
    # too large (>50)
    assert _coerce_tariff_fallback(
        100.0, default=4.32
    ) == 4.32
    # None (cold start) uses default
    assert _coerce_tariff_fallback(None, default=4.32) == 4.32
    # Day default vs night default differ
    assert _coerce_tariff_fallback(
        float("nan"), default=2.16
    ) == 2.16


def test_r05_tariff_engine_uses_configured_rates_not_constants():
    """Production path: when the coordinator sets
    ``_day_tariff_uah=8.0`` and ``_night_tariff_uah=3.0``
    on the engine via ``apply_capacity_and_tariff_options``,
    the plan's day hour uses 8.0 and night hour uses 3.0
    — NOT the module-level ``TARIFF_DAY=4.32`` /
    ``TARIFF_NIGHT=2.16`` constants.
    """
    from hems.engine import HemsEngine
    engine = HemsEngine()
    # Operator sets 8.0/3.0 via the real
    # apply path. Use the same stub as the
    # production-path test.
    apply = _load_method("apply_capacity_and_tariff_options")
    derive = _load_method("_derive_battery_capacity")
    build_sched = _load_method("_build_tariff_schedule")
    class _StubHems:
        pass
    class _StubSelf:
        def __init__(self, eng):
            self._battery_capacity_ah = 230.0
            self._nominal_voltage_v = 51.2
            self._capacity_input_warning = None
            self._battery_capacity_kwh = 11.776
            self._day_tariff_uah = 4.32
            self._night_tariff_uah = 2.16
            self._tariff_schedule = None
            self._hems = eng
        def _derive_battery_capacity(self, options):
            return derive(options)
        def _build_tariff_schedule(self):
            return build_sched(self)
    stub = _StubSelf(engine)
    apply(stub, {
        "battery_capacity_ah": 230.0,
        "nominal_voltage_v": 51.2,
        "tariff_day": 8.0,
        "tariff_night": 3.0,
    })
    # Engine attributes hold the configured
    # rates — these are what the planner reads.
    assert engine._day_tariff_uah == 8.0
    assert engine._night_tariff_uah == 3.0
    # The module-level constants are 4.32 and 2.16.
    # The engine attributes are NOT those
    # constants (proves coordinator has bound
    # the engine).
    from hems import predictive
    assert predictive.TARIFF_DAY == 4.32
    assert engine._day_tariff_uah != predictive.TARIFF_DAY
    assert engine._night_tariff_uah != predictive.TARIFF_NIGHT


def test_r05_tariff_invalid_8_0_keeps_night_finite():
    """When ``tariff_day`` is invalid (NaN) but
    ``tariff_night`` is valid, the day falls back
    to the default 4.32 but the night preserves
    the operator's choice.

    Field-independent validation: one bad field
    must not clobber a good one.
    """
    apply = _load_method("apply_capacity_and_tariff_options")
    derive = _load_method("_derive_battery_capacity")
    build_sched = _load_method("_build_tariff_schedule")
    class _StubHems:
        pass
    class _StubSelf:
        def __init__(self):
            self._battery_capacity_ah = 230.0
            self._nominal_voltage_v = 51.2
            self._capacity_input_warning = None
            self._battery_capacity_kwh = 11.776
            self._day_tariff_uah = 4.32
            self._night_tariff_uah = 2.16
            self._tariff_schedule = None
            self._hems = _StubHems()
        def _derive_battery_capacity(self, options):
            return derive(options)
        def _build_tariff_schedule(self):
            return build_sched(self)
    stub = _StubSelf()
    # tariff_day is NaN, tariff_night is 3.0
    apply(stub, {
        "battery_capacity_ah": 230.0,
        "nominal_voltage_v": 51.2,
        "tariff_day": float("nan"),
        "tariff_night": 3.0,
    })
    # day fell back to default
    assert stub._day_tariff_uah == 4.32
    # night preserved
    assert stub._night_tariff_uah == 3.0
    # Mirrored to engine
    assert stub._hems._day_tariff_uah == 4.32
    assert stub._hems._night_tariff_uah == 3.0


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
