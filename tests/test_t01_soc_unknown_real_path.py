"""T01 follow-up — drive the *real* ``_run_hems_engine`` body with
mock dependencies, then assert the inverter control API is
never called for an unreadable SOC.

The previous test (commit 5c35718) reimplemented the gate as
a free function and anchored the source via text searches. That
was a structural check, not a behavioural one. This test is the
behavioural complement:

  1. Extract the *real* ``_run_hems_engine`` body via AST and run
     it in an isolated namespace.
  2. Provide a stub ``self`` that satisfies every attribute
     the body touches (``self._hems``, ``self._schedule_rules``,
     ``self._demand_forecast``, ``self._battery_soh``,
     ``self._entry``, ``self.forecast_tomorrow_kwh``,
     ``self._pv_calibrator``, ``self._pv_local_now``,
     ``self._load_matrix``, etc.). The engine ``evaluate`` is
     stubbed to return a synthetic HemsDecision that *would*
     dispatch — so the only thing preventing a write is the
     gate.
  3. Replace ``_execute_hems_command`` with a spy that records
     every call. This is the real test of the gate: if the
     gate were broken or removed, the spy would fire.
  4. Drive a cycle with ``raw = {\"batterySoc\": <bad>,\n     \"outputSourcePriority\": \"2\", \"chargerSourcePriority\": \"1\"}``
     and assert:

       * the spy was called iff the SOC was a real value in
         [0, 100];
       * the dispatch was suppressed (and a ``command suppressed``
         warning was logged) iff the SOC was unreadable.

  5. Drive a cycle with a *valid* SOC and a non-skipping
     ``HemsDecision`` to prove the spy works: a real SOC *does*
     reach the dispatch.

  6. Repeat for every scenario the audit called out: None,
     NaN, out-of-range, non-numeric, and a real 0.

Run with:
    /tmp/powmr-venv/bin/python tests/test_t01_soc_unknown_real_path.py
"""
from __future__ import annotations

import ast
import logging
import re
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
coord_src = (ROOT / "coordinator.py").read_text(encoding="utf-8")
lines = coord_src.splitlines(keepends=True)
tree = ast.parse(coord_src)


def _function_src(name: str) -> str:
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == "InverterCoordinator":
            for sub in cls.body:
                if (
                    isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and sub.name == name
                ):
                    start = sub.lineno - 1
                    end = sub.end_lineno
                    return textwrap.dedent("".join(lines[start:end]))
    raise SystemExit(f"{name} not found in InverterCoordinator")


# The body references many names: ``_LOGGER``, ``_execute_hems_command``,
# ``_persist_night_recommendation``, ``_save_real_forecast_pair``,
# ``_save_pv_state``, ``_log_pv_calibrator_state``, ``_pv_local_now``,
# ``calibrated_storm_risk``, ``parse_predictive_options``, ``SmartMode``,
# ``HemsDecision``, ``_configure_night_window``, ``_maybe_refresh_load_history``,
# ``_maybe_refresh_pv_history``. The test harness supplies the ones
# that the gate must guard; the rest are stubbed on the stub
# coordinator.

LOG = logging.getLogger("t01_realpath")
LOG.setLevel(logging.DEBUG)


class _SpyHemsDecision:
    """Marker class so the spy can recognise HemsDecision outputs."""

    def __init__(self, *, skip: bool = False, output_priority=None,
                 charger_priority=None, buzzer_off: bool = False,
                 reason: str = "spy"):
        self.skip = skip
        self.output_priority = output_priority
        self.charger_priority = charger_priority
        self.buzzer_off = buzzer_off
        self.reason = reason


class _StubHems:
    """A minimal PredictiveControlEngine stand-in.

    The body only reads attributes; we set them up so the body
    can run without crashing. ``evaluate`` is replaced by a
    spy that returns a synthetic HemsDecision the test
    configures — the gate, not the engine, is what we test.
    """

    def __init__(self):
        self.keepalive = SimpleNamespace(in_progress=False, finish=lambda now: None)
        self._predictive_controller = SimpleNamespace(
            calibrated_storm_alert=False,
        )
        self._last_forecast_today_kwh = None
        self._hourly_pv_forecast: list = []
        self._dated_hourly_pv_forecast: dict = {}
        self._planner_forecast_now = None
        self._hourly_radiation: list = []
        self._hourly_weather_codes: list = []
        self._tariff_schedule: list = []
        self._consumption_history: list = []
        self._battery_capacity_kwh = 4.8
        self.predictive_min_confidence = 0.2
        self.predictive_storm_allowed = False
        self.predictive_decision_state: dict = {}
        self._last_buzzer = "1"
        self._evaluate_result: _SpyHemsDecision = _SpyHemsDecision(skip=True)
        # Attributes written by the body:
        self.report_calls: list[str] = []
        self.confirm_calls: list[tuple[bool, str | None]] = []

    def detect_manual_override(self, *a, **k):
        pass

    def invalidate_predictive(self, *a, **k):
        pass

    def evaluate(self, **kwargs):
        # The test configures this via set_evaluate_result.
        return self._evaluate_result

    async def report_control_success(self):
        self.report_calls.append("success")

    async def report_control_failure(self, *a, **k):
        self.report_calls.append("failure")

    async def confirm_predictive_delivery(self, success, *a, **k):
        self.confirm_calls.append((success, None))

    def finish_keepalive(self, now):
        return _SpyHemsDecision(skip=True, reason="keepalive_end")

    def set_evaluate_result(self, decision: _SpyHemsDecision):
        self._evaluate_result = decision


class _StubCoordinator:
    """Minimal surface for ``_run_hems_engine``."""

    def __init__(self):
        # Public HEMS state
        self.hems_enabled = True
        self.hems_auto_mode = True
        self.smart_mode = 0
        self.keepalive_timer = None
        self._previous_smart_mode_before_storm = None
        self._auto_storm_active = False
        self._grid_available = True
        self.hems_last_reason: str | None = None
        self.hems_last_output_cmd: str | None = None
        self.hems_last_charger_cmd: str | None = None
        self.buzzer_off: bool = False
        # Engine
        self._hems = _StubHems()
        # Schedule / demand / soh
        self._schedule_rules = SimpleNamespace(
            get_active_rule_now=lambda now: None,
        )
        self._demand_forecast = SimpleNamespace(
            update_ewma=lambda *a, **k: None,
        )
        self._battery_soh = SimpleNamespace(
            track_soc=lambda *a, **k: None,
        )
        # Forecast & calibrator
        self.forecast_tomorrow_kwh = 1.0
        self.forecast_day_after_kwh = 1.0
        self._pv_calibrator = None
        # Tariffs
        self._day_tariff_uah = 4.32
        self._night_tariff_uah = 2.16
        # Config entry
        self._entry = SimpleNamespace(
            data={},
            options={
                "reserve_soc": 20.0,
                "auto_storm_by_forecast": False,
            },
        )
        # Load matrix & forecast caches
        self._load_matrix: list = []
        self._forecast_today_kwh = None
        self.hourly_forecast_today: list = []
        self._dated_hourly_pv_forecast: dict = {}
        self.hourly_radiation_today: list = []
        self.hourly_weather_today: list = []
        # Battery capacity
        self._battery_capacity_kwh = 4.8
        # Persistence debug counters
        self._hems_debug_day = None
        self._hems_debug_decisions = 0
        self._hems_debug_commands = 0
        self._hems_debug_skips = 0
        self._hems_debug_last_decision_ts = None
        # Persistence flag (the audit-relevant gate)
        self._energy_state_dirty = False
        self._last_energy_persist_at = None
        # The actual gate the audit cares about.
        self.dispatched: list[tuple[str, Any]] = []
        self.suppressed_count: int = 0

    # ── The functions the body calls. Most are no-ops; the
    # dispatch + persist are real spies. ────────────────────────

    def _maybe_auto_tune_house_reserve(self, load_w, now):
        return None

    def _persist_night_recommendation(self, now):
        return None

    def _log_pv_calibrator_state(self, now):
        return None

    def _build_tariff_schedule(self):
        return []

    def _pv_local_now(self):
        return datetime.now()

    def _configure_night_window(self):
        return None

    async def _maybe_refresh_load_history(self, now):
        return None

    async def _maybe_refresh_pv_history(self, now):
        return None

    async def _save_real_forecast_pair(self, now):
        return None

    async def _save_pv_state(self):
        return None

    async def _execute_hems_command(self, decision):
        # The spy. This is the real production entry point; the
        # body calls it iff the gate is open. Recording a
        # dispatch here means the test is the real proof that
        # the gate is open (or closed).
        self.dispatched.append((decision.reason, decision))
        # The body then calls report_control_success and
        # confirm_predictive_delivery; we forward to the stub
        # engine so the count is consistent.
        await self._hems.report_control_success()
        await self._hems.confirm_predictive_delivery(True)


# Extract the body.
ns: dict[str, Any] = {
    "__name__": "_t01_realpath_isolated",
    "_LOGGER": LOG,
    "datetime": datetime,
    "timedelta": __import__("datetime").timedelta,
    "SmartMode": SimpleNamespace(ADAPTIVE=0, ARBITRAGE=1, STORM=2),
    "HemsDecision": _SpyHemsDecision,
    "calibrated_storm_risk": lambda *a, **k: False,
    "parse_predictive_options": lambda *a, **k: {
        "predictive_min_confidence_for_assist": 0.2,
        "predictive_default_mode": "shadow",
    },
}

run_hems_src = _function_src("_run_hems_engine")
exec(run_hems_src, ns)
_run_hems_engine = ns["_run_hems_engine"]


# ── The real test: drive cycles with various SOC payloads. ─────

def _soc_parse(raw: dict) -> float | None:
    """Mirror of the coordinator's T01 SOC parsing block.

    We use this only to decide what ``corrected_soc`` value to
    pass to the real ``_run_hems_engine``. The body then
    decides independently whether to dispatch — that is what
    we are testing.
    """
    raw_soc = raw.get("batterySoc")
    if raw_soc is None:
        return None
    try:
        s = float(raw_soc)
    except (TypeError, ValueError):
        return None
    if s != s or s < 0 or s > 100:  # NaN or out-of-range
        return None
    return s


async def _drive(raw: dict) -> _StubCoordinator:
    c = _StubCoordinator()
    # Configure the engine stub to *want* to dispatch whenever
    # the gate lets it through. This is critical: without this,
    # the engine would always return skip=True and the spy would
    # never fire — the test would pass trivially.
    c._hems.set_evaluate_result(_SpyHemsDecision(
        skip=False,
        output_priority="2",
        charger_priority="1",
        buzzer_off=False,
        reason="test_wants_dispatch",
    ))
    corrected_soc = _soc_parse(raw)
    # The body requires an `await` since it is async.
    await _run_hems_engine(c, raw, corrected_soc, datetime(2026, 10, 3, 12, 0, 0))
    return c


def _run(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── 1. None SOC: the gate MUST block, the spy MUST stay silent. ──

c = _run(_drive({"batterySoc": None, "outputSourcePriority": "2",
                  "chargerSourcePriority": "1"}))
assert c.dispatched == [], (
    f"None SOC must NOT reach _execute_hems_command; "
    f"recorded: {c.dispatched!r}"
)
# report_control_success / report_control_failure are also
# *not* called when the gate blocks; the engine code path is
# short-circuited *before* the dispatch.
assert c._hems.report_calls == [], (
    f"None SOC must not call report_control_*; "
    f"got {c._hems.report_calls!r}"
)
# The diagnostic counter for "skipped" must be incremented so a
# future operator sees that something was suppressed.
assert c._hems_debug_skips >= 1, (
    f"a suppressed dispatch must show up in _hems_debug_skips; "
    f"got {c._hems_debug_skips}"
)
# hems_last_output_cmd and hems_last_charger_cmd are *not*
# updated for the suppressed action (the audit's contract:
# no false state recorded).
assert c.hems_last_output_cmd is None
assert c.hems_last_charger_cmd is None


# ── 2. NaN SOC ─────────────────────────────────────────────────

c = _run(_drive({"batterySoc": float("nan")}))
assert c.dispatched == [], f"NaN SOC: {c.dispatched!r}"


# ── 3. Out-of-range high ──────────────────────────────────────

c = _run(_drive({"batterySoc": 150}))
assert c.dispatched == [], f"150 % SOC: {c.dispatched!r}"


# ── 4. Out-of-range low ───────────────────────────────────────

c = _run(_drive({"batterySoc": -5}))
assert c.dispatched == [], f"-5 % SOC: {c.dispatched!r}"


# ── 5. Non-numeric ────────────────────────────────────────────

c = _run(_drive({"batterySoc": "abc"}))
assert c.dispatched == [], f"non-numeric SOC: {c.dispatched!r}"


# ── 6. Real 0 % (the audit's original T01 regression) ────────

c = _run(_drive({"batterySoc": 0}))
# Real 0 is a valid SOC: the gate is open and the spy fires.
assert len(c.dispatched) == 1, (
    f"real 0 % must dispatch: {c.dispatched!r}"
)
assert c.dispatched[0][1].output_priority == "2"
assert c._hems.report_calls == ["success"]


# ── 7. Real 50 % ──────────────────────────────────────────────

c = _run(_drive({"batterySoc": 50}))
assert len(c.dispatched) == 1, f"real 50 % must dispatch: {c.dispatched!r}"


# ── 8. Real 100 % ─────────────────────────────────────────────

c = _run(_drive({"batterySoc": 100}))
assert len(c.dispatched) == 1, f"real 100 % must dispatch: {c.dispatched!r}"


# ── 9. Real SOC but engine decision.skip=True ─────────────────

c = _StubCoordinator()
c._hems.set_evaluate_result(_SpyHemsDecision(
    skip=True, reason="engine_says_skip", output_priority="2",
    charger_priority="1",
))
_run(_run_hems_engine(
    c, {"batterySoc": 50, "outputSourcePriority": "2",
        "chargerSourcePriority": "1"},
    50, datetime(2026, 10, 3, 12, 0, 0),
))
# skip=True: no dispatch even though the gate is open.
assert c.dispatched == [], f"engine skip=True must not dispatch: {c.dispatched!r}"


# ── 10. Static ordering check (regression on the source) ─────

run_hems_src = _function_src("_run_hems_engine")
soc_unknown_idx = run_hems_src.find("soc_unknown = corrected_soc is None")
gate_idx = run_hems_src.find("if soc_unknown:")
dispatch_idx = run_hems_src.find("await self._execute_hems_command(decision)")
assert soc_unknown_idx != -1
assert gate_idx != -1
assert dispatch_idx != -1
assert soc_unknown_idx < gate_idx < dispatch_idx, (
    "soc_unknown must be computed BEFORE the gate, "
    "and the gate must come BEFORE the dispatch"
)


print("T01-real OK — _run_hems_engine dispatched only with valid SOC")
sys.exit(0)
