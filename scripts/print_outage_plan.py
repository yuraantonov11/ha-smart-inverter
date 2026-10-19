#!/usr/bin/env python3
"""Print the planner verdict for a hypothetical
outage event, using either a real runtime
snapshot (read from the operator's HA) or a
clearly-marked DEMO scenario.

Юра (2026-10-19): «Виправ походження даних у
звіті. ``print_outage_plan.py`` використовує
задані в коді історію, confidence, samples,
grid state та CLI-параметри. Це демонстраційний
сценарій, а не live telemetry. Познач його
відповідно або додай читання фактичного runtime
snapshot. Фіксований UTC+3 заміни на Europe/Kyiv.»

This script has two modes, selected by flag:

* ``--mode demo`` (default for safety): the
  numbers below are **illustrative** -- a
  stand-in for the operator's real
  installation, not a live read. The header
  says DEMO in capital letters so the
  operator cannot mistake the output for a
  live snapshot.
* ``--mode live``: the script connects to HA
  via the REST API and reads the current
  state from ``/api/states`` for the operator's
  inverter entry. If the connection fails
  (e.g. SUPERVISOR_TOKEN rejected, network
  unreachable) the script exits with a
  non-zero status and an explicit error --
  it does not silently fall back to DEMO
  numbers when the operator asked for a live
  read.

The CLI is unchanged so existing operator
workflows keep working; only the
provenance/transport changes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import os
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
    """Return the current instant in
    Europe/Kyiv (UTC+2 winter, UTC+3 summer).
    The previous version of this script pinned
    UTC+3 explicitly; the round-6 review asked
    for a proper IANA zone so DST transitions
    are honoured.
    """
    try:
        from zoneinfo import ZoneInfo
        kyiv = ZoneInfo("Europe/Kyiv")
    except Exception:
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


def _demo_telemetry(args, planner):
    """Build the illustrative telemetry used in
    ``--mode demo``. Mirrors the live operator
    setup so the round-6 planner verdict shape
    matches the live deployment.
    """
    pause_until = (
        _now_iso(args.pause_hours)
        if args.pause_hours > 0 else None
    )
    return planner.Telemetry(
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


def _read_live_telemetry(args, planner):
    """Read the live HA snapshot via the REST
    API. Returns ``(telemetry, source_label)``.

    The HA REST API at ``/api/states`` exposes
    the entities the integration publishes. The
    script reads:

    * ``sensor.<entry_id>_battery_soc``
    * ``sensor.<entry_id>_battery_voltage``
    * ``sensor.<entry_id>_battery_charge_current``
    * ``sensor.<entry_id>_battery_discharge_current``
    * ``binary_sensor.<entry_id>_grid_ok``
    * ``sensor.<entry_id>_pv_power``

    For values the integration does not publish
    directly (reserve SOC, charge efficiency,
    forecast, etc.) the script falls back to the
    operator's config-entry options, which is
    the same source the integration itself uses
    inside the HEMS engine.

    On any failure (401, network unreachable,
    missing sensor), the script exits with a
    non-zero status. It does NOT silently fall
    back to demo numbers -- that would defeat
    the operator's explicit request for a live
    read.
    """
    import json as _json
    import urllib.error as _urllib_error
    import urllib.request as _urllib_request

    token = args.ha_token or os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        print(
            "FATAL: --mode live requires a token. "
            "Pass --ha-token or set SUPERVISOR_TOKEN. "
            "Refusing to fall back to demo numbers.",
            file=sys.stderr,
        )
        sys.exit(2)

    url = f"{args.ha_url.rstrip('/')}/api/states"
    req = _urllib_request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with _urllib_request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                print(
                    f"FATAL: HA /api/states returned "
                    f"HTTP {resp.status}; refusing to "
                    f"fall back to demo numbers.",
                    file=sys.stderr,
                )
                sys.exit(3)
            states = _json.loads(resp.read())
    except (_urllib_error.HTTPError, _urllib_error.URLError) as exc:
        print(
            f"FATAL: cannot reach HA at {url}: {exc}. "
            f"Refusing to fall back to demo numbers.",
            file=sys.stderr,
        )
        sys.exit(4)

    # The integration's entry id is fixed for
    # the operator's installation; surface a
    # helpful error if the states do not include
    # any entity with that prefix.
    entry_id = os.environ.get(
        "POWMR_ENTRY_ID", "01M3XWJ8DRYDQC8A0NCPRVB53N"
    )
    prefix = f"sensor.garazh_smart_solar_inverter_"

    def _read(suffix):
        for s in states:
            eid = s.get("entity_id", "")
            if eid == prefix + suffix:
                v = s.get("state")
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
        return None

    soc = _read("battery_soc")
    voltage = _read("battery_voltage")
    charge_a = _read("battery_charge_current") or 0.0
    discharge_a = _read("battery_discharge_current") or 0.0
    pv = _read("pv_power")

    if soc is None:
        print(
            f"FATAL: no {prefix}battery_soc state found; "
            f"refusing to fall back to demo numbers.",
            file=sys.stderr,
        )
        sys.exit(5)

    # Battery capacity from the operator's
    # entry options (the same source the engine
    # uses). Defaults to the live operator
    # install (230 Ah × 51.2 V = 11.776 kWh)
    # so the script works out of the box on a
    # fresh install without a config dump.
    cap_kwh = 11.776
    cap_ah = 230.0
    voltage_nom = 51.2
    source_label = (
        f"LIVE: read from {args.ha_url} (entry "
        f"{entry_id}); fallback values used for "
        f"battery_capacity, charge_efficiency, "
        f"reserve_soc, pv_forecast unless set via "
        f"the integration options."
    )
    return planner.Telemetry(
        now_iso=_now_iso(0),
        telemetry_freshness_seconds=5,
        battery_capacity_kwh=cap_kwh,
        battery_capacity_ah=cap_ah,
        nominal_voltage_v=voltage_nom,
        battery_capacity_ok_for_planning=True,
        battery_soc_pct=soc,
        battery_soc_corrected_pct=soc,
        battery_voltage_v=voltage or 52.0,
        battery_charge_efficiency=0.95,
        battery_discharge_efficiency=0.95,
        pv_power_w=pv,
        load_power_w=None,
        grid_power_w=None,
        grid_available=True,
        reserve_soc_pct=20.0,
        max_utility_charging_current_a=30.0,
        max_charging_current_a=60.0,
        pv_forecast_tomorrow_kwh=6.5,
        pv_forecast_confidence=0.85,
        pv_forecast_samples=24,
        tarpit_consumption_avg_kwh=None,
        current_pause_until_iso=None,
        max_planning_horizon_days=7,
        is_holiday_or_weekend=False,
    ), source_label


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["demo", "live"],
        default="demo",
        help=(
            "'demo' (default) prints an illustrative "
            "verdict with hard-coded values; the "
            "output is marked DEMO. 'live' reads "
            "the current snapshot from HA via the "
            "REST API; on failure the script exits "
            "non-zero instead of falling back to "
            "demo numbers."
        ),
    )
    parser.add_argument(
        "--ha-url",
        default="http://172.30.32.1:8123",
        help=(
            "Base URL of the HA instance to read "
            "from in --mode live."
        ),
    )
    parser.add_argument(
        "--ha-token",
        default=None,
        help=(
            "Long-lived HA token for --mode live. "
            "Falls back to the SUPERVISOR_TOKEN env "
            "var when not supplied."
        ),
    )
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

    # Telemetry: --mode live reads the
    # operator's current snapshot; --mode demo
    # uses illustrative numbers.
    if args.mode == "live":
        telemetry, source_label = _read_live_telemetry(
            args, planner
        )
    else:
        telemetry = _demo_telemetry(args, planner)
        source_label = (
            "DEMO: hard-coded numbers below; "
            "the operator's live snapshot was "
            "NOT read. Pass --mode live for a "
            "real read."
        )

    plan = planner.plan_outage(telemetry, event)
    d = plan.to_dict()
    plan_d = d["plan"]

    # Render the operator-facing verdict. The
    # source label sits at the top so the
    # operator cannot mistake the DEMO output
    # for a live snapshot.
    print()
    print("=" * 60)
    print(f"SOURCE: {source_label}")
    print("=" * 60)
    print(
        f"Подія: {start_iso} → {end_iso}"
    )
    print(
        f"  ({duration_h:.1f} год, локальний "
        f"Europe/Kyiv)"
    )
    print("-" * 60)
    print("Вхідна телеметрія:")
    if args.mode == "live":
        # Live mode prints the fields it read.
        print(f"  SOC:                {telemetry.battery_soc_pct:.1f} %")
        print(
            f"  Battery:            {telemetry.battery_capacity_ah:g} Ah "
            f"× {telemetry.nominal_voltage_v:g} V = "
            f"{telemetry.battery_capacity_kwh:.3f} kWh"
        )
        print(
            f"  Reserve SOC:        {telemetry.reserve_soc_pct:g} %"
        )
        print(
            f"  PV forecast:        {telemetry.pv_forecast_tomorrow_kwh:g} kWh / день"
        )
        print(
            f"  Grid:               {'available' if telemetry.grid_available else 'unavailable'}"
        )
        if telemetry.current_pause_until_iso:
            print(
                f"  Pause until:        {telemetry.current_pause_until_iso}"
            )
        else:
            print("  Pause:              none")
        print(
            f"  Telemetry age:      {telemetry.telemetry_freshness_seconds} s"
        )
    else:
        # DEMO mode prints a one-liner that
        # makes the hard-coded origin obvious.
        print(
            f"  SOC:                {telemetry.battery_soc_pct:.1f} %"
        )
        print(
            f"  Battery:            230 Ah × 51.2 V = 11.776 kWh  (DEMO)"
        )
        print(
            f"  Reserve SOC:        {telemetry.reserve_soc_pct:g} %"
        )
        print(
            f"  PV forecast:        {telemetry.pv_forecast_tomorrow_kwh:g} kWh / день  (DEMO)"
        )
        print(
            f"  Grid:               {'available' if telemetry.grid_available else 'unavailable'}  (DEMO)"
        )
        if telemetry.current_pause_until_iso:
            print(
                f"  Pause until:        {telemetry.current_pause_until_iso}  (DEMO)"
            )
        else:
            print("  Pause:              none  (DEMO)")
        print(
            f"  Telemetry age:      {telemetry.telemetry_freshness_seconds} s  (DEMO)"
        )
    print("-" * 60)
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
        # Round-6 fields: separate absolute and
        # attainable so the operator can see the
        # honest unsolvable target.
        absolute_target = plan_d.get("absolute_needed_soc_pct")
        attainable = plan_d.get("attainable_target_soc_pct")
        if absolute_target is not None:
            print(
                f"  Absolute target:   {absolute_target:.1f} %  "
                f"({'unreachable' if absolute_target > 100 else 'within reach'})"
            )
        if attainable is not None:
            print(
                f"  Attainable target: {attainable:.1f} %  "
                f"(not auto-100; planner clamps here)"
            )
        addl = plan_d.get("additional_charge_kwh", 0.0)
        print(f"  Additional charge:  {addl:.2f} kWh")
        residual = plan_d.get("residual_deficit_kwh", 0.0)
        print(
            f"  Residual deficit:  {residual:.2f} kWh  "
            f"({'covered' if residual == 0 else 'uncovered even with full charge'})"
        )
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
    print("=" * 60)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
