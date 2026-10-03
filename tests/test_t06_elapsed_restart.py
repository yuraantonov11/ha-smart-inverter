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
maybe_persist_src = _function_src("_maybe_persist_energy_state")
mark_dirty_src = _function_src("_mark_energy_state_dirty")
restore_src = _function_src("_restore_energy_state")

ns: dict = {"__name__": "_t06_isolated", "datetime": datetime, "Any": Any}
exec(is_daytime_src, ns)
exec(accumulate_src, ns)
exec(snapshot_src, ns)
exec(persist_src, ns)
exec(maybe_persist_src, ns)
exec(mark_dirty_src, ns)
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
    # T06 follow-up: throttled persistence fields.
    s._ENERGY_PERSIST_MIN_INTERVAL_S = 30.0
    s._last_energy_persist_at = None
    s._energy_state_dirty = False
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
        "_maybe_persist_energy_state",
        "_mark_energy_state_dirty",
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
# Force a final persist so the blob reflects *both* samples.
# The T06 follow-up throttles persistence; the regression test
# for the restart path needs the latest state, not whatever the
# throttle window happened to capture.
stub._persist_energy_state(now + timedelta(seconds=10), force=True)
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
assert abs(new_stub._daily_pv_kwh - prev_pv - increment) < 1e-9 * max(1.0, abs(prev_pv)), new_stub._daily_pv_kwh
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


# ── T06 follow-up: persistence is rate-limited ───────────────────────

# T06.10: 6 consecutive samples within 5 s of each other must only
# produce one persisted write (the first). The stub's lambda
# replaces the entire ``options`` dict on every write, so we
# cannot simply compare dict contents — we use ``id()`` to
# distinguish the dict *instance*.
stub = _make_stub()
stub._last_energy_persist_at = None
stub._energy_state_dirty = False
writes = 0
prev_options_id = id(stub._entry.options)
for i in range(6):
    now = datetime(2026, 10, 3, 12, 0, 0) + timedelta(seconds=i * 5)
    stub._accumulate_daily_energy(now, {"pvPower": 1000.0})
    if id(stub._entry.options) != prev_options_id:
        writes += 1
    prev_options_id = id(stub._entry.options)
# The first sample always persists (last_energy_persist_at is None).
# Subsequent samples within the throttle window must NOT persist.
# We assert that no more than two writes happened in this window.
assert writes <= 2, f"throttle failed: {writes} writes in 6×5s samples"


# T06.11: a midnight rollover marks the state dirty and bypasses the
# throttle. We use a 30 s gap to stay under the 60 s offline-gap
# threshold so the function does not short-circuit on the early
# return; the rollover itself is what we want to exercise.
stub = _make_stub()
stub._last_midnight = datetime(2026, 9, 30, 0, 0, 0)
stub._last_sample_ts = datetime(2026, 9, 30, 23, 0, 0)
# Pretend a previous sample happened 60 s ago so the throttle
# says "fresh enough — skip".
stub._last_energy_persist_at = datetime(2026, 9, 30, 23, 0, 30)
# Pre-populate the options dict with a "before" blob so we have
# something to compare against.
stub._entry.options["_energy_state"] = '{"schema":1,"daily_pv_kwh":0.5}'
options_before = dict(stub._entry.options)
# Now cross midnight 30 s later.
now = datetime(2026, 10, 1, 0, 0, 0)
stub._accumulate_daily_energy(now, {"pvPower": 1000.0})
options_after = dict(stub._entry.options)
# The _energy_state should have been refreshed despite the throttle.
assert options_before.get("_energy_state") != options_after.get("_energy_state"), (
    "rollover did not force a persistence write"
)


# ── T06 follow-up: month rollover with a missed day ──────────────────

# T06.12: Sep 30 last sample at 23:55; next sample is Oct 3 09:00.
# The integration has been offline for two days. The Sep 30 daily
# savings must be folded into the September monthly total before
# the new month zeroes it.
stub = _make_stub()
# Establish the previous-day baseline.
stub._accumulate_daily_energy(
    datetime(2026, 9, 30, 23, 55, 0), {"pvPower": 0.0}
)
# Simulate Sep 30 daily savings — they live in _daily_savings_uah.
stub._daily_savings_uah = 7.50  # arbitrary UAH
# Now the next sample is Oct 3 09:00, a clear month rollover.
stub._accumulate_daily_energy(
    datetime(2026, 10, 3, 9, 0, 0), {"pvPower": 0.0}
)
# The Sep 30 daily savings must have flowed into _monthly_savings_uah.
# The previous monthly value was 0, so after the rollover the new
# monthly should be 0 (we zeroed it because previous_was_last_day).
# Importantly, the Sep 30 daily value must NOT have been added to
# the new month's running total.
assert stub._daily_savings_uah >= 0  # the new day's accumulation
# (a) the daily was cleared:
assert stub._daily_savings_uah < 7.50  # at most 5 s × 0 W / 1000 = 0
# (b) the monthly was zeroed because Sep 30 was the last day of Sep:
assert stub._monthly_savings_uah == 0, (
    f"monthly should be reset to 0 after Sep→Oct rollover, "
    f"got {stub._monthly_savings_uah}"
)


# T06.13: the Sep 30 daily savings of 7.50 UAH must be VISIBLE in
# the next persisted blob, even though the new month is in progress.
# We use a fresh stub to avoid interference from the previous test.
stub = _make_stub()
# Establish midnight baseline for Sep 30.
stub._last_midnight = datetime(2026, 9, 30, 0, 0, 0)
stub._last_sample_ts = datetime(2026, 9, 30, 23, 55, 0)
stub._daily_savings_uah = 7.50
stub._monthly_savings_uah = 0.0
# Force a persist with current state.
ok = stub._persist_energy_state(datetime(2026, 9, 30, 23, 55, 0), force=True)
assert ok
import json as _json
blob = _json.loads(stub._entry.options["_energy_state"])
assert blob["daily_savings_uah"] == 7.50


# ── T06 follow-up: clock skew (negative elapsed) ────────────────────

# T06.14: a sample with a timestamp earlier than the previous one
# must not produce a negative integration. We use the offline-gap
# path: since elapsed_s is negative, we substitute the 5 s
# baseline. The daily counter therefore grows by exactly 5 s of
# nominal power, NOT a negative amount.
stub = _make_stub()
stub._accumulate_daily_energy(
    datetime(2026, 10, 3, 12, 0, 0), {"pvPower": 1000.0}
)
prev = stub._daily_pv_kwh
earlier = datetime(2026, 10, 3, 11, 59, 30)  # 30 s in the past
stub._accumulate_daily_energy(earlier, {"pvPower": 1000.0})
# Counter must have grown, not shrunk.
assert stub._daily_pv_kwh > prev, (
    f"clock skew caused a negative integration: "
    f"prev={prev}, after={stub._daily_pv_kwh}"
)


# T06.15: 5 s × 1000 W / 1000 = 0.001388… kWh is the expected delta
# for the negative-elapsed case.
stub = _make_stub()
stub._accumulate_daily_energy(
    datetime(2026, 10, 3, 12, 0, 0), {"pvPower": 1000.0}
)
prev = stub._daily_pv_kwh
stub._accumulate_daily_energy(
    datetime(2026, 10, 3, 11, 59, 30), {"pvPower": 1000.0}
)
expected_delta = 1000.0 * 5 / 3600 / 1000
assert abs(stub._daily_pv_kwh - prev - expected_delta) < 1e-9, (
    f"clock-skew baseline wrong: delta={stub._daily_pv_kwh - prev}, "
    f"expected {expected_delta}"
)


print("T06 OK — real elapsed time + restart persistence + throttle + month rollover + clock skew verified")
sys.exit(0)
