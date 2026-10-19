#!/usr/bin/env python3
"""Compute and print the planner verdict for a
hypothetical outage event, using the live operator
telemetry defaults (230 Ah × 51.2 V = 11.776 kWh,
Europe/Kyiv, 7-day consumption history that mirrors
a typical Львів household).

Юра (2026-10-19): «Надай конкретний результат:
«для події X потрібен SOC Y%, підготовка
починається о Z, очікуваний дефіцит — N
кВт·год», із поясненням розрахунку.»

The script is the operator-facing companion to
``tests/test_outage_planner_acceptance.py``. The
test asserts the verdict's contract; the script
prints the verdict in a form the operator can
read at a glance:

    ==================================================
    Подія: 2026-10-20T09:00:00+03:00 →
            2026-10-20T13:00:00+03:00
    (4 год, локальний Київ)
    --------------------------------------------------
    Вхідна телеметрія (live operator setup):
      SOC:                65.0 %
      Battery:            230 Ah × 51.2 V = 11.776 kWh
      Reserve SOC:        20 %
      PV forecast:        6.5 kWh / день
      Grid:               available
      Pause:              none
      Telemetry age:      5 s
    --------------------------------------------------
    Розрахунок:
      Energy needed:      0.85 kWh
      Energy reserve:     0.85 kWh
      Energy available:   7.17 kWh (battery + PV during window)
      Gap:                0.00 kWh
      Target SOC:         54.24 %  (attainable, NOT 100 %)
      Grid-charge time:   1.46 h
      Prep start:         2026-10-20T05:30:00+03:00
    --------------------------------------------------
    Verdict: NOT BLOCKED.
    reason_if_incomplete: ...
    ==================================================

Usage:
    python3 scripts/print_outage_plan.py \\
        --start-hours-from-now 4 \\
        --end-hours-from-now 6 \\
        --soc 65 \\
        --pv-forecast 6.5
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _install_stub_modules() -> None:
    if "homeassistant" in sys.modules:
        return
    ha = types.ModuleType("homeassistant")
    ha.__path__ = []
    sys.modules["homeassistant"] = ha
    for sub in ("core", "exceptions", "helpers"):
        m = types.ModuleType(f"homeassistant.{sub}")
        m.__path__ = []
        sys.modules[f"homeassistant.{sub}"] = m


def _load_planner():
    """Lazy-load the production planner + outage
    modules with the same import trick the tests
    use. We need both: ``OutageEvent`` lives in
    ``outage.py``; ``Telemetry`` and ``plan_outage``
    live in ``outage_planner.py``.
    """
    _install_stub_modules()
    pkg_name = "_print_outage_plan_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(REPO / "services")]
        sys.modules[pkg_name] = pkg
    # Load the planner first so that ``outage.py``
    # can import it via ``from .outage_planner
    # import ...``.
    pl_spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.outage_planner",
        str(REPO / "services" / "outage_planner.py"),
    )
    pl_mod = importlib.util.module_from_spec(pl_spec)
    sys.modules[pl_spec.name] = pl_mod
    pl_spec.loader.exec_module(pl_mod)
    out_spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.outage",
        str(REPO / "services" / "outage.py"),
    )
    out_mod = importlib.util.module_from_spec(out_spec)
    sys.modules[out_spec.name] = out_mod
    out_spec.loader.exec_module(out_mod)
    return pl_mod, out_mod


def _now_kyiv() -> dt.datetime:
    kyiv = dt.timezone(dt.timedelta(hours=3))
    return dt.datetime.now(dt.timezone.utc).astimezone(kyiv)


def _now_iso(hours_from_now: float) -> str:
    return (
        _now_kyiv() + dt.timedelta(hours=hours_from_now)
    ).isoformat()


def _format_kyiv(iso: str | None) -> str:
    if iso is None:
        return "—"
    return iso


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--start-hours-from-now",
        type=float,
        default=4.0,
        help=(
            "hours from now when the outage "
            "starts (default 4)"
        ),
    )
    parser.add_argument(
        "--end-hours-from-now",
        type=float,
        default=8.0,
        help=(
            "hours from now when the outage "
            "ends (default 8)"
        ),
    )
    parser.add_argument(
        "--soc",
        type=float,
        default=65.0,
        help=(
            "current battery SOC in percent "
            "(default 65)"
        ),
    )
    parser.add_argument(
        "--pv-forecast",
        type=float,
        default=6.5,
        help=(
            "PV forecast for the relevant window "
            "in kWh (default 6.5)"
        ),
    )
    parser.add_argument(
        "--grid",
        choices=["available", "unavailable"],
        default="available",
    )
    parser.add_argument(
        "--pause-hours",
        type=float,
        default=0.0,
        help=(
            "if > 0, set a manual HEMS pause that "
            "many hours from now (default 0 = "
            "no pause)"
        ),
    )
    parser.add_argument(
        "--stale-seconds",
        type=int,
        default=5,
        help=(
            "telemetry age in seconds (default 5). "
            "Set > 90 to exercise the staleness "
            "guard."
        ),
    )
    args = parser.parse_args()

    planner, outage = _load_planner()

    # Build the event (offset-aware ISO in
    # Europe/Kyiv, matching the live deployment).
    start_iso = _now_iso(args.start_hours_from_now)
    end_iso = _now_iso(args.end_hours_from_now)
    duration_h = (
        args.end_hours_from_now
        - args.start_hours_from_now
    )
    event = outage.OutageEvent(
        id="ev_print",
        source="manual:dashboard",
        raw_text=None,
        start_iso=start_iso,
        end_iso=end_iso,
        tz_name="Europe/Kyiv",
        received_at=_now_iso(0),
        status="confirmed",
        needs_confirmation=False,
        ambiguity_reason=None,
        version=1,
        updated_at=_now_iso(0),
        notes="",
    )

    # Telemetry snapshot that mirrors the live
    # operator setup.
    pause_until = (
        _now_iso(args.pause_hours)
        if args.pause_hours > 0 else None
    )
    telemetry = planner.Telemetry(
        now_iso=_now_iso(0),
        telemetry_freshness_seconds=args.stale_seconds,
        battery_capacity_kwh=230 * 51.2 / 1000.0,
        battery_capacity_ah=230.0,
        nominal_voltage_v=51.2,
        battery_capacity_ok_for_planning=True,
        battery_soc_pct=args.soc,
        battery_soc_corrected_pct=args.soc,
        battery_voltage_v=52.0,
        battery_charge_efficiency=0.95,
        battery_discharge_efficiency=0.95,
        pv_power_w=None,
        load_power_w=None,
        grid_power_w=None,
        grid_available=(args.grid == "available"),
        reserve_soc_pct=20.0,
        max_utility_charging_current_a=30.0,
        max_charging_current_a=60.0,
        pv_forecast_tomorrow_kwh=args.pv_forecast,
        pv_forecast_confidence=0.85,
        pv_forecast_samples=24,
        # 7-day Львів household consumption
        # history; the planner averages this
        # for the event window.
        tarpit_consumption_avg_kwh={
            "2026-10-12": 9.8,
            "2026-10-13": 10.1,
            "2026-10-14": 11.3,
            "2026-10-15": 10.0,
            "2026-10-16": 9.6,
            "2026-10-17": 10.4,
            "2026-10-18": 9.9,
        },
        current_pause_until_iso=pause_until,
        max_planning_horizon_days=7,
        is_holiday_or_weekend=False,
    )

    plan = planner.plan_outage(telemetry, event)
    d = plan.to_dict()
    plan_d = d["plan"]

    # Render the operator-facing verdict.
    print()
    print("=" * 50)
    print(
        f"Подія: {start_iso} → {end_iso}"
    )
    print(f"  ({duration_h:.1f} год, локальний Київ)")
    print("-" * 50)
    print("Вхідна телеметрія (live operator setup):")
    print(f"  SOC:                {args.soc:.1f} %")
    print(
        f"  Battery:            230 Ah × 51.2 V "
        f"= 11.776 kWh"
    )
    print("  Reserve SOC:        20 %")
    print(f"  PV forecast:        {args.pv_forecast:.1f} kWh / день")
    print(f"  Grid:               {args.grid}")
    if pause_until:
        print(f"  Pause until:        {pause_until}")
    else:
        print("  Pause:              none")
    print(f"  Telemetry age:      {args.stale_seconds} s")
    print("-" * 50)
    print("Розрахунок:")
    if d["blocked"]:
        print(f"  Verdict:           BLOCKED")
        print(
            f"  Reason:            {d['reason_if_blocked']}"
        )
    else:
        print(f"  Verdict:           NOT BLOCKED")
        print(
            f"  Energy needed:     "
            f"{plan_d.get('energy_needed_kwh', 0):.2f} kWh"
        )
        print(
            f"  Energy reserve:    "
            f"{plan_d.get('energy_reserve_kwh', 0):.2f} kWh"
        )
        print(
            f"  Energy available:  "
            f"{plan_d.get('energy_available_kwh', 0):.2f} kWh"
            f"  (battery + PV during window)"
        )
        gap = plan_d.get(
            "needs_charging_from_grid_kwh", 0.0
        )
        print(f"  Gap:               {gap:.2f} kWh")
        target = plan_d.get("attainable_target_soc_pct")
        if target is not None:
            print(
                f"  Target SOC:        "
                f"{target:.1f} %  (attainable, "
                f"not auto-100 %)"
            )
        else:
            print("  Target SOC:        — (unknown)")
        grid_h = plan_d.get("grid_charge_hours_estimate")
        if grid_h is not None:
            print(
                f"  Grid-charge time:  {grid_h:.2f} h"
            )
        else:
            print("  Grid-charge time:  — (unknown)")
        print(
            f"  Prep start:        "
            f"{_format_kyiv(plan_d.get('prep_start_iso'))}"
        )
        if d["reason_if_incomplete"]:
            print(
                f"  reason_if_incomplete:\n    "
                f"{d['reason_if_incomplete']}"
            )
    print("=" * 50)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
