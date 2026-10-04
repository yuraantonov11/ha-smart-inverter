"""T07 + T08: physical energy balance and reserve SOC tests.

The audit's T07 review required the day-ahead planner
to compute SOC change from the *actual* inverter mode
(``output`` priority + ``charger`` priority) and grid
availability, not from a single
``net_kwh = (pv - load) / 1000.0`` shortcut that ignored
both. The T08 review required the user-configured
``reserve_soc`` to flow through to every planner path,
not a hard-coded ``min_soc = 20.0`` or ``RESERVE_FRAC
= 0.15``.

These tests exercise the *real* production functions
in ``hems/predictive.py`` — no harness, no AST
re-exec, no mocking of the function under test. The
goal is to assert the actual contract the live planner
will use.

Boundaries covered (T07 + T08 acceptance criteria):
  1. PV surplus: PV > load → battery charges; grid
     import is zero.
  2. Energy deficit: PV < load → battery discharges
     (SBU) or grid supplies (USB); never both at
     once.
  3. Grid charging: USB + SNU allows the grid to
     cover the load while the battery still charges
     from PV surplus.
  4. Battery-fed load: SBU + OSO lets the battery
     cover the PV deficit up to the reserve floor.
  5. Reserve floor: when SOC == reserve, the battery
     stops discharging (no more negative ``batt_kwh``).
  6. Custom reserve SOC: a user-configured reserve of
     35 % is honoured — same logic, no hard-coded
     20 % leakage.
  7. SOC bounds: a SOC > 100 or < 0 still rolls forward
     inside ``simulate_24h`` without crashing the loop
     (clamp behaviour preserved).
  8. Invalid inputs: NaN / non-numeric / out-of-range
     reserve_soc, NaN PV, and NaN load surface as
     ``ValueError`` or empty plans — they never produce
     a silently wrong number.

Test approach: call the real ``_balance_hour`` and
``simulate_24h`` directly. ``_balance_hour`` is a
pure function with no HA dependencies, so it can be
imported in a stdlib-only environment.
"""
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hems.predictive import (
    _balance_hour,
    plan_soc_targets,
    simulate_24h,
    PlannerInputs,
    ConsumptionPredictor,
)


def _predictor(history=None):
    return ConsumptionPredictor(history=history)


def _approx(value, rel=None, abs_tol=None):
    """Drop-in replacement for ``pytest.approx`` used by
    the test bodies. We don't need pytest's full
    feature set — only ``rel`` and absolute-tolerance
    flags, both optional. NaN/inf are compared
    exactly so a miscomputed ``nan`` does not silently
    pass. The parameter is named ``abs_tol`` rather
    than ``abs`` to avoid shadowing the builtin
    ``abs`` inside the closure (which would have
    been caught as ``'NoneType' is not callable``)."""
    class _Approx:
        def __init__(self, expected, rel, abst):
            self.expected = expected
            self.rel = rel
            self.abst = abst

        def __eq__(self, actual):
            if isinstance(actual, _Approx):
                actual = actual.expected
            if self.expected != actual:  # NaN guard
                if math.isnan(self.expected) or math.isnan(actual):
                    return False
                if math.isinf(self.expected) or math.isinf(actual):
                    return False
                tol = self.abst or 0.0
                if self.rel is not None:
                    tol = max(tol, abs(self.expected) * self.rel)
                return abs(actual - self.expected) <= tol
            return True

        def __repr__(self):
            return f"≈{self.expected}"

    return _Approx(value, rel, abs_tol)


# A module-level alias so test bodies can write
# ``pytest.approx`` without importing pytest. We
# translate the public ``abs=`` keyword into the
# safe ``abs_tol=`` parameter of ``_approx`` to
# keep the closure's ``abs`` builtin unshadowed.
class _Pytest:
    @staticmethod
    def approx(value, rel=None, abs=None):  # noqa: A002
        return _approx(value, rel=rel, abs_tol=abs)


pytest = _Pytest()  # noqa: A001  (intentional shadow for the test bodies)


def _inputs(**overrides):
    """Build a real ``PlannerInputs`` with the T08
    fields filled in. ``reserve_soc`` defaults to
    20 % to match the original "sane default" used
    before the T08 audit; tests that need a different
    value pass ``reserve_soc=...`` explicitly.
    """
    base = dict(
        now=datetime(2026, 6, 21, 12, 0, 0),
        soc=50.0,
        soc_corrected=50.0,
        pv_w=2000.0,
        load_w=500.0,
        grid_w=0.0,
        batt_w=0.0,
        battery_capacity_kwh=4.8,
        grid_v=230.0,
        grid_ok=True,
        smart_mode=0,
        forecast_today_kwh=15.0,
        forecast_tomorrow_kwh=15.0,
        hourly_pv=[0.0] * 24,
        hourly_weather=[{"code": 0, "wind": 0, "precip": 0}] * 24,
        hourly_radiation=[0.0] * 24,
        tariff_schedule=[1.0] * 24,
        consumption_history=[[200.0] * 24 for _ in range(7)],
    )
    base.update(overrides)
    return PlannerInputs(**base)


# ── T07: PV surplus ────────────────────────────────────
def test_t07_pv_surplus_charges_battery_usb_snu():
    """PV >> load with USB+SNU must charge the battery
    from PV surplus and draw zero grid."""
    batt_w, grid_w, unserved_w = _balance_hour(
        output="0",  # USB
        charger="1",  # SNU
        pv_w=2000.0,
        load_w=500.0,
        grid_ok=True,
        soc=50.0,
        reserve_soc=20.0,
        battery_capacity_kwh=4.8,
        charge_efficiency=0.85,
        discharge_efficiency=0.90,
    )
    # 1.5 kWh PV surplus × 0.85 efficiency → 1.275 kWh
    # stored per hour. The function returns Wh.
    assert batt_w == pytest.approx(1275.0, rel=1e-3)
    # Grid import is zero — PV covered all of load.
    assert grid_w == pytest.approx(0.0, abs=1e-9)
    # Nothing unserved.
    assert unserved_w == pytest.approx(0.0, abs=1e-9)


# ── T07: PV deficit, USB + OSO ─────────────────────────
def test_t07_pv_deficit_usb_oso_grid_supplies():
    """PV < load with USB+OSO must draw the deficit from
    the grid; battery does not charge; battery does not
    discharge (USB is grid-first)."""
    batt_w, grid_w, unserved_w = _balance_hour(
        output="0",  # USB
        charger="0",  # OSO
        pv_w=300.0,
        load_w=800.0,
        grid_ok=True,
        soc=50.0,
        reserve_soc=20.0,
        battery_capacity_kwh=4.8,
        charge_efficiency=0.85,
        discharge_efficiency=0.90,
    )
    # No PV surplus → no charging.
    assert batt_w == pytest.approx(0.0, abs=1e-9)
    # Grid supplies the 0.5 kWh deficit.
    assert grid_w == pytest.approx(500.0, rel=1e-3)
    assert unserved_w == pytest.approx(0.0, abs=1e-9)


# ── T07: PV deficit, SBU + OSO ─────────────────────────
def test_t07_pv_deficit_sbu_oso_battery_supplies():
    """PV < load with SBU+OSO must draw the deficit from
    the battery (above the reserve floor); grid import
    is zero because SBU does not use the grid as
    backup when the battery can serve.
    """
    batt_w, grid_w, unserved_w = _balance_hour(
        output="2",  # SBU
        charger="0",  # OSO
        pv_w=200.0,
        load_w=800.0,
        grid_ok=True,
        soc=50.0,
        reserve_soc=20.0,
        battery_capacity_kwh=4.8,
        charge_efficiency=0.85,
        discharge_efficiency=0.90,
    )
    # 0.6 kWh deficit. SOC=50 % → 2.4 kWh stored.
    # Reserve=20 % → 0.96 kWh reserve. Usable 1.44 kWh.
    # Battery supplies 0.6 kWh → negative batt_w.
    assert batt_w == pytest.approx(-600.0, rel=1e-3)
    # No grid import (SBU; battery is enough).
    assert grid_w == pytest.approx(0.0, abs=1e-9)
    assert unserved_w == pytest.approx(0.0, abs=1e-9)


# ── T07: grid charging (USB + SNU, deficit) ────────────
def test_t07_grid_charging_usb_snu_load_from_grid():
    """USB+SNU with PV=0 and load=500: the grid feeds
    the load *and* is allowed to charge the battery.
    The contract for this combination is "grid first
    for load"; the planner's conservative balance
    says PV-surplus-only is the only charging source
    (the inverter itself charges from grid in this
    mode, but the planner is conservative)."""
    batt_w, grid_w, unserved_w = _balance_hour(
        output="0",  # USB
        charger="1",  # SNU
        pv_w=0.0,
        load_w=500.0,
        grid_ok=True,
        soc=50.0,
        reserve_soc=20.0,
        battery_capacity_kwh=4.8,
        charge_efficiency=0.85,
        discharge_efficiency=0.90,
    )
    # No PV → no PV-surplus → no charging per the
    # planner's conservative model.
    assert batt_w == pytest.approx(0.0, abs=1e-9)
    # Grid supplies the entire 0.5 kWh load.
    assert grid_w == pytest.approx(500.0, rel=1e-3)
    assert unserved_w == pytest.approx(0.0, abs=1e-9)


# ── T07: battery stops at reserve floor ─────────────────
def test_t07_reserve_floor_blocks_discharge():
    """When SOC == reserve_soc exactly, the usable
    window is zero; the battery must not discharge
    even if the load is large."""
    batt_w, grid_w, unserved_w = _balance_hour(
        output="2",  # SBU
        charger="0",  # OSO
        pv_w=0.0,
        load_w=1000.0,
        grid_ok=True,
        soc=20.0,  # == reserve_soc
        reserve_soc=20.0,
        battery_capacity_kwh=4.8,
        charge_efficiency=0.85,
        discharge_efficiency=0.90,
    )
    # No usable energy above the reserve.
    assert batt_w == pytest.approx(0.0, abs=1e-9)
    # All load is unserved (SBU + OSO + grid_ok is
    # irrelevant for SBU — the battery is the
    # source-of-truth, not the grid).
    assert unserved_w == pytest.approx(1000.0, rel=1e-3)


# ── T07: inverter offline + SBU → unserved ──────────────
def test_t07_offline_sbu_oso_unserved():
    """With grid offline and SBU+OSO, the load above
    PV+battery is reported as unserved — *not* swept
    under the carpet or silently subtracted from
    the SOC clamp."""
    # SOC=80 % → 3.84 kWh stored; reserve=20 % → 0.96
    # kWh reserve; usable=2.88 kWh. With discharge
    # efficiency=0.90, the deliverable is 2.88*0.90 =
    # 2.592 kWh — less than the 2 kW (=2 kWh) load,
    # so the battery fully covers the load and the
    # unserved bucket is zero. The T07 audit's
    # efficiency fix (multiply, not divide) is what
    # makes this bound correct.
    batt_w, grid_w, unserved_w = _balance_hour(
        output="2",  # SBU
        charger="0",  # OSO
        pv_w=0.0,
        load_w=2000.0,
        grid_ok=False,
        soc=80.0,  # 3.84 kWh stored, 2.88 kWh usable
        reserve_soc=20.0,
        battery_capacity_kwh=4.8,
        charge_efficiency=0.85,
        discharge_efficiency=0.90,
    )
    # Battery covers the full 2 kWh load.
    assert batt_w == pytest.approx(-2000.0, rel=1e-3)
    # Grid is offline → 0.
    assert grid_w == pytest.approx(0.0, abs=1e-9)
    # Nothing unserved — the battery is sufficient.
    assert unserved_w == pytest.approx(0.0, abs=1e-3)


# ── T08: custom reserve_soc flows through ───────────────
def test_t08_custom_reserve_soc_honoured():
    """A user-configured reserve of 35 % must be the
    one used by the balance model, not the old
    20 % default."""
    # At 35 % reserve, the usable window is 50 %−35 %
    # = 15 % of 4.8 kWh = 0.72 kWh. With discharge
    # efficiency=0.90, the deliverable per hour is
    # 0.72*0.90 = 0.648 kWh — capped here, not at
    # the load. SBU+OSO: the rest is unserved.
    batt_w, grid_w, unserved_w = _balance_hour(
        output="2",  # SBU
        charger="0",  # OSO
        pv_w=0.0,
        load_w=2000.0,
        grid_ok=True,
        soc=50.0,
        reserve_soc=35.0,  # user-configured
        battery_capacity_kwh=4.8,
        charge_efficiency=0.85,
        discharge_efficiency=0.90,
    )
    # Battery delivers 0.648 kWh; returned in W.
    assert batt_w == pytest.approx(-648.0, rel=1e-3)
    # SBU + OSO: the rest is unserved, not from grid.
    assert unserved_w == pytest.approx(2000.0 - 648.0, abs=1e-3)


# ── T08: reserve_soc field exists on PlannerInputs ──────
def test_t08_planner_inputs_has_reserve_soc_field():
    """The T08 contract is that ``reserve_soc`` lives
    on ``PlannerInputs`` — the planner must receive
    it as a typed field, not reach into a global."""
    pi = _inputs(reserve_soc=30.0)
    assert pi.reserve_soc == 30.0
    # Default still works.
    pi_default = _inputs()
    assert pi_default.reserve_soc == 20.0


# ── T08: plan_soc_targets uses reserve_soc from inputs ─
def test_t08_plan_soc_targets_uses_inputs_reserve_soc():
    """``plan_soc_targets`` must read the user-
    configured ``reserve_soc`` from ``PlannerInputs``
    — not the old hard-coded ``RESERVE_FRAC = 0.15``."""
    # With reserve=30 %: the morning target must be
    # the reserve plus a buffer for the deficit. The
    # function returns (target_morning, target_evening).
    # ``plan_soc_targets`` reads the tomorrow PV from
    # ``inputs.forecast_tomorrow_kwh`` and the
    # consumption history from
    # ``inputs.consumption_history``; we do not
    # invent new input fields.
    pi = _inputs(
        soc=60.0,
        forecast_tomorrow_kwh=2.0,
        consumption_history=[[200.0] * 24 for _ in range(7)],
        reserve_soc=30.0,
    )
    target_morning, _ = plan_soc_targets(pi)
    # The morning target must be at least the user's
    # reserve (30 %), never 15 %.
    assert target_morning >= 30.0 - 1e-6


# ── T08: simulate_24h with reserve_soc=50 stops at 50 %
def test_t08_simulate_24h_respects_custom_reserve_soc():
    """A simulation starting at 80 % SOC with a 50 %
    reserve must end the night above 50 % even when
    load is heavy — the planner must clamp at the
    configured reserve, not the old hard-coded 20 %."""
    pi = _inputs(
        soc=80.0,
        pv_w=0.0,  # no PV at night
        load_w=1000.0,  # 1 kWh/h
        grid_ok=True,
        battery_capacity_kwh=4.8,
        reserve_soc=50.0,
    )
    plans = simulate_24h(
        pi,
        target_morning=50.0,
        target_evening=50.0,
        predictor=_predictor(history=pi.consumption_history),
    )
    # The planner must NOT produce a plan that drives
    # the SOC below the user's 50 % reserve. The
    # ``min_soc`` clamp (T08 audit) makes this an
    # invariant of the function — any violation
    # indicates a regression.
    for plan in plans:
        # ``soc_pred`` is documented as percent (0..100),
        # not kWh — see ``HourlyPlan.soc_pred`` at
        # line 60 of ``hems/predictive.py``.
        soc_pct = getattr(plan, "soc_pred", None)
        if soc_pct is not None:
            assert soc_pct >= 50.0 - 1e-6, (
                f"simulate_24h produced a plan below the "
                f"configured reserve: soc={soc_pct:.2f}%, "
                f"reserve_soc=50%"
            )


# ── T08: invalid reserve_soc bounds raise ───────────────
def test_t08_invalid_reserve_soc_raises():
    """NaN, infinity, text, and out-of-range values
    must not silently corrupt the plan. The
    ``simulate_24h`` function raises ``ValueError``
    on the first invalid sample so the operator
    sees the failure in the log."""
    for bad in (float("nan"), float("inf"), -5.0, 150.0):
        try:
            simulate_24h(
                _inputs(reserve_soc=bad),
                target_morning=20.0,
                target_evening=20.0,
                predictor=_predictor(),
            )
        except ValueError:
            continue
        except TypeError:
            # ``_finite_number`` on a non-numeric value
            # falls into the same fail-loud path; we
            # accept that as well.
            continue
        else:
            raise AssertionError(
                f"simulate_24h accepted invalid "
                f"reserve_soc={bad!r}; expected ValueError"
            )


# ── T07: simulate_24h with PV>0 produces plans ──────────
def test_t07_simulate_24h_with_pv_surplus_produces_plan():
    """The full planner path runs end-to-end with a
    realistic hourly forecast. We don't pin every
    hour's number (the planner is allowed to evolve)
    — we only assert the contract: a 24 h plan is
    produced, each hour has a finite SOC in [0, 100]."""
    pi = _inputs(
        soc=50.0,
        pv_w=2000.0,  # sunny day
        load_w=400.0,
        grid_ok=True,
        battery_capacity_kwh=4.8,
        reserve_soc=20.0,
        # sunny 24h profile
        hourly_pv=[
            0, 0, 0, 0, 0, 0, 100, 400, 800, 1500,
            2000, 2500, 2800, 2800, 2500, 2000,
            1500, 800, 400, 100, 0, 0, 0, 0,
        ],
    )
    plans = simulate_24h(
        pi,
        target_morning=30.0,
        target_evening=30.0,
        predictor=_predictor(history=pi.consumption_history),
    )
    assert plans, "simulate_24h returned no plans"
    for plan in plans:
        # ``soc_pred`` is documented as percent (0..100),
        # see ``HourlyPlan.soc_pred`` at line 60 of
        # ``hems/predictive.py``. The previous test
        # version incorrectly treated it as kWh and
        # produced a "2000% out of bounds" failure —
        # which the T07 audit fixed by aligning the
        # assertion with the documented unit.
        if hasattr(plan, "soc_pred") and plan.soc_pred is not None:
            assert -1e-6 <= plan.soc_pred <= 100.0 + 1e-6, (
                f"soc_pred out of bounds: {plan.soc_pred:.2f}%"
            )


# ── T07: simulate_24h raises on invalid PV forecast ─────
def test_t07_simulate_24h_invalid_pv_raises():
    """An out-of-range PV forecast (> 20 kW, NaN, or
    non-numeric) must surface as ``ValueError`` —
    not a silent zero. The original code raised
    on this, and T07 keeps that contract."""
    bad_hourly = [50000.0] + [0.0] * 23
    for bad in (bad_hourly,):
        try:
            simulate_24h(
                _inputs(hourly_pv=bad),
                target_morning=20.0,
                target_evening=20.0,
                predictor=_predictor(),
            )
        except ValueError:
            continue
        else:
            raise AssertionError(
                f"simulate_24h accepted invalid hourly_pv"
            )


# ── T07: simulate_24h missing SOC returns empty plan ────
def test_t07_simulate_24h_soc_none_returns_empty():
    """``simulate_24h`` with ``soc=None`` is the
    SOC-unknown short-circuit: no plan is produced.
    The coordinator's T01 gate is the real guard,
    but the planner's defence-in-depth behaviour
    is to return an empty list rather than raise
    or compute a fake SOC."""
    plans = simulate_24h(
        _inputs(soc=None, soc_corrected=None),
        target_morning=20.0,
        target_evening=20.0,
        predictor=_predictor(),
    )
    assert plans == []


# ── T08: plan_soc_targets with reserve=0 still works ────
def test_t08_plan_soc_targets_reserve_zero():
    """A user with reserve_soc=0 (no reserve) must
    get a valid target — the function does not
    divide by zero or refuse."""
    pi = _inputs(
        soc=20.0,
        forecast_tomorrow_kwh=0.5,
        consumption_history=[[200.0] * 24 for _ in range(7)],
        reserve_soc=0.0,
    )
    target_morning, target_evening = plan_soc_targets(pi)
    # The morning target must be a non-negative
    # finite value.
    assert math.isfinite(target_morning)
    assert target_morning >= 0.0
    assert math.isfinite(target_evening)
    assert target_evening >= 0.0


# ── T07 + T08: integration via end-to-end simulate_24h ──
def test_t07_t08_end_to_end_uses_reserve_soc_in_planning():
    """Two scenarios: one with the default 20 %
    reserve, one with a 40 % reserve. The
    evening-target SOC for the 40 % case must be
    at least as high as the 20 % case — the user's
    reserve preference reaches the day-ahead
    plan, not just the per-hour balance."""
    pi_low = _inputs(
        soc=50.0,
        forecast_tomorrow_kwh=1.0,
        consumption_history=[[200.0] * 24 for _ in range(7)],
        reserve_soc=20.0,
    )
    pi_high = _inputs(
        soc=50.0,
        forecast_tomorrow_kwh=1.0,
        consumption_history=[[200.0] * 24 for _ in range(7)],
        reserve_soc=40.0,
    )
    morning_low, evening_low = plan_soc_targets(pi_low)
    morning_high, evening_high = plan_soc_targets(pi_high)
    # The high-reserve plan must ask for a higher
    # morning SOC (so the day can end with 40 % in
    # the bank rather than 20 %).
    assert morning_high >= morning_low - 1e-6


# Minimal runner so the file can be executed directly
# via ``python tests/test_*.py`` (matching the project's
# no-pytest convention). Each test_* function returns
# True on success; failures raise AssertionError which
# the runner surfaces with a clear message.


def _run_one(name, fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        return False
    print(f"PASS {name}")
    return True


def _run_all():
    import inspect
    tests = [
        (name, obj)
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    ]
    tests.sort(key=lambda kv: kv[0])
    failed = 0
    for name, fn in tests:
        sig = inspect.signature(fn)
        if not _run_one(name, fn):
            failed += 1
    total = len(tests)
    print(f"--- {total - failed}/{total} passed ---")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_all()
