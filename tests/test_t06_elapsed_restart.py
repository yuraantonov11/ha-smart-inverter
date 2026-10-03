"""T06: real elapsed time + restart persistence for energy counters.

Run with:
    /tmp/powmr-venv/bin/python tests/test_t06_elapsed_restart.py
"""
from __future__ import annotations

import ast
import json
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
coord_src = (ROOT / "coordinator.py").read_text(encoding="utf-8")
lines = coord_src.splitlines(keepends=True)
tree = ast.parse(coord_src)


def _function_src(name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            start = node.lineno - 1
            end = node.end_lineno
            return textwrap.dedent("".join(lines[start:end]))
    raise SystemExit(f"{name} not found in coordinator.py")


accumulate_src = _function_src("_accumulate_daily_energy")
is_daytime_src = _function_src("_is_daytime")
snapshot_src = _function_src("_energy_state_snapshot")
persist_src = _function_src("_persist_energy_state")
restore_src = _function_src("_restore_energy_state")

ns: dict = {"__name__": "_t06_isolated", "datetime": datetime, "Any": Any}
exec(is_daytime_src, ns)
exec(accumulate_src, ns)
exec(snapshot_src, ns)
exec(persist_src, ns)
exec(restore_src, ns)


def _make_stub(*, persist_fail: bool = False) -> SimpleNamespace:
    s = SimpleNamespace()
    s._last_midnight = None
    s._last_sample_ts = None
    s._daily_pv_kwh = 0.0
    s._daily_grid_import_day_kwh = 0.0
    s._daily_grid_import_night_kwh = 0.0
    s._daily_grid_export_kwh = 0.0
    s._daily_battery_discharge_day_kwh = 0.0
    s._daily_battery_discharge_night_kwh = 0.0
    s._daily_savings_uah = 0.0
    s._monthly_savings_uah = 0.0
    s._day_tariff_uah = 4.32
    s._night_tariff_uah = 2.16
    s._MAX_SAMPLE_GAP_S = 60.0
    s._ENERGY_STATE_SCHEMA_VERSION = 1
    if persist_fail:
        s._entry = None
        s.hass = None
    else:
        s._entry = SimpleNamespace(options={})
        s.hass = SimpleNamespace(
            config_entries=SimpleNamespace(
                async_update_entry=lambda entry, options: setattr(entry, "options", dict(options))
            )
        )

    # Bind the methods explicitly so that ``self.X(...)`` inside the
    # function bodies resolves to a bound method, not a plain function.
    # SimpleNamespace does not implement the descriptor protocol, so
    # attribute lookup of a function returns the function unbound.
    # ``_is_daytime`` is a ``@staticmethod`` in the source — assign it
    # as a plain function so calls go through as ``self._is_daytime(x)``.
    s._is_daytime = ns["_is_daytime"]
    for name in (
        "_accumulate_daily_energy",
        "_energy_state_snapshot",
        "_persist_energy_state",
        "_restore_energy_state",
    ):
        setattr(s, name, MethodType(ns[name], s))
    return s


# ── T06.1: real elapsed time changes the integration interval ────────

stub = _make_stub()
now = datetime(2026, 10, 3, 12, 0, 0)
# First sample uses the nominal 5 s baseline.
stub._accumulate_daily_energy(now, {"pvPower": 1000.0})
assert abs(stub._daily_pv_kwh - 0.001388888888888889) < 1e-9

# A 12-second gap is real elapsed time, not 5 s.
now2 = now + timedelta(seconds=12)
stub._accumulate_daily_energy(now2, {"pvPower": 1000.0})
# Expected increment: 1000 W × 12 s = 12000 W·s = 12000/3600 Wh = 3.333 Wh = 0.003333 kWh
assert abs(stub._daily_pv_kwh - (0.001388888888888889 + 1000.0 * 12 / 3600 / 1000)) < 1e-9, stub._daily_pv_kwh
print(f"  pv kWh after 1+12s@1000W: {stub._daily_pv_kwh} (real elapsed)")

# ── T06.2: 30-second gap is real elapsed, not capped at 5 s ────────

stub = _make_stub()
now = datetime(2026, 10, 3, 12, 0, 0)
stub._accumulate_daily_energy(now, {"pvPower": 1000.0})
now2 = now + timedelta(seconds=30)
stub._accumulate_daily_energy(now2, {"pvPower": 1000.0})
expected = 0.001388888888888889 + 1000.0 * 30 / 3600 / 1000
assert abs(stub._daily_pv_kwh - expected) < 1e-9, stub._daily_pv_kwh


# ── T06.3: 5-minute offline gap must NOT integrate the stale reading ─

stub = _make_stub()
now = datetime(2026, 10, 3, 12, 0, 0)
stub._accumulate_daily_energy(now, {"pvPower": 1000.0})
prev = stub._daily_pv_kwh
now2 = now + timedelta(seconds=300)  # 5 minutes
stub._accumulate_daily_energy(now2, {"pvPower": 1000.0})
# The gap is larger than _MAX_SAMPLE_GAP_S; the function returns early
# after updating _last_sample_ts. The counter must NOT have grown.
assert abs(stub._daily_pv_kwh - prev) < 1e-12, stub._daily_pv_kwh


# ── T06.4: clock skew / out-of-order sample uses 5 s baseline ────────

stub = _make_stub()
now = datetime(2026, 10, 3, 12, 0, 0)
stub._accumulate_daily_energy(now, {"pvPower": 1000.0})
# Earlier timestamp — pretend we received a stale one.
earlier = now - timedelta(seconds=20)
stub._accumulate_daily_energy(earlier, {"pvPower": 1000.0})
# Should have used 5 s baseline, not negative.
assert stub._daily_pv_kwh > 0


# ── T06.5: restart preserves daily totals and last sample timestamp ──

stub = _make_stub()
now = datetime(2026, 10, 3, 12, 0, 0)
stub._accumulate_daily_energy(now, {"pvPower": 1000.0})
stub._accumulate_daily_energy(
    now + timedelta(seconds=10), {"pvPower": 1000.0}
)
blob = stub._entry.options["_energy_state"]
assert isinstance(blob, str)

# Simulate a reload: new coordinator instance, options survive.
new_stub = _make_stub()
new_stub._restore_energy_state(blob)
assert new_stub._daily_pv_kwh > 0
assert new_stub._last_sample_ts is not None
assert new_stub._last_midnight is not None

# After "reload" the new coordinator should integrate the next sample
# using the real gap since the last persisted timestamp.
prev_pv = new_stub._daily_pv_kwh
new_stub._accumulate_daily_energy(
    stub._last_sample_ts + timedelta(seconds=15),
    {"pvPower": 1000.0},
)
# The new sample should add 15 s of integration.
increment = 1000.0 * 15 / 3600 / 1000
assert abs(new_stub._daily_pv_kwh - prev_pv - increment) < 1e-9, new_stub._daily_pv_kwh
print(f"  reload + 15s integrates correctly: +{new_stub._daily_pv_kwh - prev_pv} kWh")


# ── T06.6: midnight rollover closes the previous day into monthly ───

stub = _make_stub()
# Run 30 minutes on day 1.
now = datetime(2026, 9, 30, 12, 0, 0)
stub._accumulate_daily_energy(now, {"pvPower": 1000.0})
stub._accumulate_daily_energy(now + timedelta(seconds=10), {"pvPower": 1000.0})
prev_savings = stub._daily_savings_uah

# Now cross to Oct 1.
now2 = datetime(2026, 10, 1, 0, 5, 0)
stub._accumulate_daily_energy(now2, {"pvPower": 1000.0})
# After rollover, daily counters reset, and the previous day's savings
# were added to the monthly total.
assert stub._daily_savings_uah >= 0  # may have added the 5s of new day's pv
# Sep 30 → Oct 1: monthly should be 0 (reset on day==1).
assert stub._monthly_savings_uah == 0, stub._monthly_savings_uah
# But the previous-day savings flowed through _before_ the reset.
# (i.e. _monthly_savings_uah += _daily_savings_uah then _monthly_savings_uah = 0
# because now.day == 1 — net 0.)


# ── T06.7: malformed blob does not raise; counters stay at defaults ──

stub = _make_stub()
stub._restore_energy_state("not json")
assert stub._daily_pv_kwh == 0
stub._restore_energy_state({"schema": 999})  # wrong schema
assert stub._daily_pv_kwh == 0
stub._restore_energy_state(None)
assert stub._daily_pv_kwh == 0


# ── T06.8: persistence failure does not raise ───────────────────────

stub = _make_stub(persist_fail=True)
stub._accumulate_daily_energy(
    datetime(2026, 10, 3, 12, 0, 0), {"pvPower": 1000.0}
)
# No exception, counter still advanced.
assert stub._daily_pv_kwh > 0


# ── T06.9: snapshot round-trip ──────────────────────────────────────

stub = _make_stub()
now = datetime(2026, 10, 3, 12, 0, 0)
stub._accumulate_daily_energy(now, {"pvPower": 1000.0})
stub._accumulate_daily_energy(now + timedelta(seconds=10), {"pvPower": 1000.0})
blob = stub._entry.options["_energy_state"]
data = json.loads(blob)
assert data["schema"] == 1
assert "last_sample_ts_iso" in data
assert data["daily_pv_kwh"] > 0

# Round-trip via a fresh stub
fresh = _make_stub()
fresh._restore_energy_state(blob)
assert fresh._daily_pv_kwh == data["daily_pv_kwh"]
assert fresh._daily_savings_uah == data["daily_savings_uah"]


print("T06 OK — real elapsed time + restart persistence verified")
sys.exit(0)
