"""T05: energy counters must be in kWh, not Wh.

The previous implementation added ``W * h`` (Wh) to fields named
``_daily_*_kwh``. After the fix, every counter is divided by 1000.

Run with:
    /tmp/powmr-venv/bin/python tests/test_t05_energy_kwh.py
"""
from __future__ import annotations

import ast
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

ns: dict = {
    "__name__": "_t05_isolated",
    "datetime": datetime,
    "timedelta": timedelta,
    "Any": Any,
}
exec(is_daytime_src, ns)
exec(accumulate_src, ns)

# Build a stand-in ``self`` carrying only the state the function reads
# and writes. We must run the *real* body, just with a stub harness.
class _Stub:
    pass


def _make_stub() -> SimpleNamespace:
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
    s._accumulate_daily_energy = ns["_accumulate_daily_energy"]
    s._is_daytime = ns["_is_daytime"]
    # T05 stub: T06 added a real _persist_energy_state call inside
    # _accumulate_daily_energy. We provide a no-op so the energy unit
    # assertions are isolated from persistence concerns. The
    # throttle-aware variants (_maybe_persist_energy_state,
    # _mark_energy_state_dirty) are not exercised here, so a
    # simple ``lambda *a, **k: None`` is enough.
    s._persist_energy_state = lambda *a, **k: None
    s._maybe_persist_energy_state = lambda *a, **k: None
    s._mark_energy_state_dirty = lambda *a, **k: None
    # _MAX_SAMPLE_GAP_S is the T06 threshold. Setting it to None makes
    # any elapsed gap trigger the "offline" return path, so the test
    # only exercises the unit conversion on its first sample. We set
    # it to 60 s so the unit-only tests can keep accumulating.
    s._MAX_SAMPLE_GAP_S = 60.0
    return s


# ── regression: 1000 W × 3600 s = 1 kWh, not 1000 kWh ────────────────

# Simulate "1000 W for 1 hour" by stepping 720 samples of 1000 W with
# dt_h = 5/3600 each → total 1 h, total energy = 1 kWh.
stub = _make_stub()
# 12:00 noon — daytime branch
now = datetime(2026, 10, 3, 12, 0, 0)
# Initialise last_midnight so the rollover branch does not fire.
stub._last_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
for _ in range(720):
    stub._accumulate_daily_energy(stub, now, {"pvPower": 1000.0})
    # advance by 5 s
    from datetime import timedelta
    now = now + timedelta(seconds=5)

# Expected: 1000 W × 1 h = 1 kWh, not 1 Wh or 1000 kWh.
assert abs(stub._daily_pv_kwh - 1.0) < 1e-6, stub._daily_pv_kwh
print(f"  pv kWh after 1h@1000W: {stub._daily_pv_kwh} (expected 1.0)")


# ── regression: short sample, 1000 W × 5 s = 0.001388889 kWh ─────────

stub = _make_stub()
now = datetime(2026, 10, 3, 12, 0, 0)
stub._last_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
stub._accumulate_daily_energy(stub, now, {"pvPower": 1000.0})
expected = 1000.0 * (5.0 / 3600.0) / 1000.0  # = 0.001388888... kWh
assert abs(stub._daily_pv_kwh - expected) < 1e-9, (stub._daily_pv_kwh, expected)
print(f"  pv kWh after 1 sample@1000W/5s: {stub._daily_pv_kwh} (expected {expected})")


# ── regression: import vs export use the same unit ────────────────────

# Daytime grid import +500 W
stub = _make_stub()
now = datetime(2026, 10, 3, 12, 0, 0)
stub._last_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
for _ in range(360):  # 30 min
    stub._accumulate_daily_energy(stub, now, {"gridPower": 500.0})
    from datetime import timedelta
    now = now + timedelta(seconds=5)
expected_day = 500.0 * 0.5 / 1000.0  # 0.25 kWh
assert abs(stub._daily_grid_import_day_kwh - expected_day) < 1e-6, stub._daily_grid_import_day_kwh


# Night import
stub = _make_stub()
now = datetime(2026, 10, 3, 2, 0, 0)  # 02:00 — night
stub._last_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
for _ in range(360):
    stub._accumulate_daily_energy(stub, now, {"gridPower": 500.0})
    from datetime import timedelta
    now = now + timedelta(seconds=5)
expected_night = 500.0 * 0.5 / 1000.0  # 0.25 kWh
assert abs(stub._daily_grid_import_night_kwh - expected_night) < 1e-6, stub._daily_grid_import_night_kwh


# Export (negative gridPower) — note the threshold is grid_w < -10.
stub = _make_stub()
now = datetime(2026, 10, 3, 12, 0, 0)
stub._last_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
for _ in range(360):
    stub._accumulate_daily_energy(stub, now, {"gridPower": -500.0})
    from datetime import timedelta
    now = now + timedelta(seconds=5)
expected_export = 500.0 * 0.5 / 1000.0
assert abs(stub._daily_grid_export_kwh - expected_export) < 1e-6, stub._daily_grid_export_kwh


# Battery discharge keeps the same unit. battery_w < -10 is "discharging".
stub = _make_stub()
now = datetime(2026, 10, 3, 12, 0, 0)
stub._last_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
for _ in range(360):
    stub._accumulate_daily_energy(stub, now, {"batteryPower": -1000.0})
    from datetime import timedelta
    now = now + timedelta(seconds=5)
expected_discharge = 1000.0 * 0.5 / 1000.0
assert abs(stub._daily_battery_discharge_day_kwh - expected_discharge) < 1e-6, stub._daily_battery_discharge_day_kwh


# ── guard against the OLD bug: pv_kwh must NOT be in Wh range ────────
# Walk back through the most recent stubs to confirm the magnitude
# of the day-counter for a 30-minute PV run was 0.5 kWh, not 0.5 Wh
# (which would round to 0 in the dashboard's 3-decimal display).
# The strongest regression check: 1000 W × 30 min = 0.5 kWh, not
# 0.0005 kWh. If the unit conversion regressed, the value would be
# roughly 1000× smaller.
# We re-run a 30-minute pass on a fresh stub and assert the value
# matches the kWh expectation, not the Wh expectation.
stub = _make_stub()
now = datetime(2026, 10, 3, 12, 0, 0)
stub._last_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
for _ in range(360):
    stub._accumulate_daily_energy(stub, now, {"pvPower": 1000.0})
    now = now + timedelta(seconds=5)
half_hour_kwh = 0.5
half_hour_wh = 0.0005
assert abs(stub._daily_pv_kwh - half_hour_kwh) < 1e-6, stub._daily_pv_kwh
assert stub._daily_pv_kwh != half_hour_wh, "regressed to Wh scale"


# ── savings formula still works with the new unit ─────────────────────
# 1 kWh × 4.32 UAH = 4.32 UAH.
stub = _make_stub()
now = datetime(2026, 10, 3, 12, 0, 0)
stub._last_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
# 1000 W discharge for 1 h = 1 kWh discharged during daytime.
for _ in range(720):
    stub._accumulate_daily_energy(stub, now, {"batteryPower": -1000.0})
    from datetime import timedelta
    now = now + timedelta(seconds=5)
assert abs(stub._daily_savings_uah - 4.32) < 0.01, stub._daily_savings_uah


print("T05 OK — all energy counters stored in kWh, savings consistent")
sys.exit(0)
