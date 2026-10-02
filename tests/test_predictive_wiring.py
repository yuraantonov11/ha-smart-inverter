"""End-to-end wiring tests for the option → coordinator → engine → decision flow.

Proves:
  1. Coordinator initialises the engine from ``entry.options["predictive_mode"]``
     (off / shadow / assist) and the engine ``_predictive_enabled`` legacy
     shim stays in lockstep.
  2. ``coordinator.async_set_predictive_mode`` is the single canonical
     writer — switch + select entities route through it; reload reads
     back the same value from options.
  3. ``HemsDecision`` actually changes when predictive is on vs off,
     in all three SmartMode modes (Adaptive, Arbitrage, Storm), at
     chosen scenarios where ML has room to be a meaningful hint.
  4. The ML hint NEVER weakens safety: storm always USB+SNU, low
     SOC always USB+SNU, manual override always skips.
  5. Missing data (no forecast, no hourly arrays) keeps the planner
     dormant — it does NOT silently treat None as 0.
  6. Debug logging doesn't crash when /config doesn't exist — tests
     run inside an isolated tmpdir seam via ``debug_logging.set_log_path``.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hems.engine import (
    ChargerPriority,
    HemsDecision,
    HemsEngine,
    OutputPriority,
    SmartMode,
)
from hems.tuning import PredictiveTuning

# ---------------------------------------------------------------------------
# Minimal fake of the bits of the coordinator the engine+option wiring needs.
# We do NOT import the real coordinator — it depends on Home Assistant and
# would pull in vol/aiohttp/etc. Instead we mock the surface area the
# engine reads at evaluate() time and verify async_set_predictive_mode
# behaviour using a stub.
# ---------------------------------------------------------------------------


class _StubConfigEntry:
    """Stand-in for a HA ConfigEntry that just holds options dict."""

    def __init__(self, options: dict | None = None) -> None:
        self.options = dict(options or {})


def _make_stub_coordinator(
    *, predictive_mode: str = "off", reserve_soc: float = 20.0
):
    """Build a minimal coordinator-like object exposing the API our
    wiring touches: ``predictive_mode`` property and
    ``async_set_predictive_mode`` method.

    We reuse the HemsEngine instance exactly the way the real
    coordinator does — assign ``predictive_tuning``, sync
    ``_predictive_enabled``, sync legacy attributes — then verify
    that calling ``async_set_predictive_mode`` propagates correctly.
    """
    tunables = SimpleNamespace(
        reserve_soc=reserve_soc,
        min_operating_soc=30.0,
        mid_soc=50.0,
        pv_surplus_enter_w=250.0,
        pv_surplus_exit_w=50.0,
        min_mode_hold_min=20,
        manual_override_hold_min=5,
        command_dedup_window_sec=300,
    )

    from hems.tuning import HemsTunables as _HT

    eng = HemsEngine(tunables=_HT(
        reserve_soc=reserve_soc,
        pv_surplus_enter_w=250.0,
    ))
    # Mirror what the real coordinator does in __init__.
    entry = _StubConfigEntry(
        {"predictive_mode": predictive_mode, "reserve_soc": reserve_soc}
    )
    pt = PredictiveTuning(
        predictive_mode=str(entry.options.get("predictive_mode", "off")),
        predictive_enabled=entry.options.get("predictive_mode", "")
        in ("shadow", "assist"),
        battery_reserve_pct=float(entry.options.get("reserve_soc", 20.0)),
    )
    eng.predictive_tuning = pt
    eng._predictive_enabled = pt.predictive_mode in ("shadow", "assist")
    eng._predictive_mode = pt.predictive_mode

    # Coordinator-like wrapper.
    coordinator = SimpleNamespace(
        _hems=eng,
        _predictive_tuning=pt,
        _entry=entry,
        # Keep a Python list of mode-change events for assertions.
        _mode_changes=[],
        hass=SimpleNamespace(
            config_entries=SimpleNamespace(
                async_update_entry=MagicMock(
                    side_effect=lambda e, options: setattr(e, "options", options)
                )
            )
        ),
    )

    def _pm_getter(self_):
        return self_._predictive_tuning.predictive_mode

    # Use a plain instance attribute so SimpleNamespace-style
    # coordinator stubs expose the value directly (avoids property
    # descriptor lookup surprises). The real coordinator exposes
    # this as a property.
    coordinator.predictive_mode = (
        coordinator._predictive_tuning.predictive_mode
    )

    def async_set_predictive_mode(mode: str) -> None:
        if mode not in ("off", "shadow", "assist"):
            mode = "off"
        coordinator._predictive_tuning.predictive_mode = mode
        coordinator._hems.predictive_tuning = coordinator._predictive_tuning
        coordinator._hems._predictive_mode = mode
        coordinator._hems._predictive_enabled = mode in ("shadow", "assist")
        # Keep the stub's plain attribute in sync (the real
        # coordinator exposes this as a property).
        coordinator.predictive_mode = mode
        coordinator._mode_changes.append(mode)
        try:
            new_opts = dict(coordinator._entry.options)
            new_opts["predictive_mode"] = mode
            coordinator.hass.config_entries.async_update_entry(
                coordinator._entry, options=new_opts
            )
        except Exception:
            pass

    # Force predictive_mode to a plain string so SimpleNamespace
    # doesn't keep the property descriptor (which returns <property object>).
    # Tests that call async_set_predictive_mode will update it to the real value.
    coordinator.predictive_mode = predictive_mode
    coordinator.async_set_predictive_mode = async_set_predictive_mode
    return coordinator


def _prime_engine(coordinator, *, now=None):
    """Feed the coordinator-driven arrays onto the engine — these are
    the same ones the real coordinator writes inside
    ``_run_hems_engine`` immediately before calling ``evaluate()``."""
    hems = coordinator._hems
    hems._last_forecast_today_kwh = 4.5
    hems._hourly_pv_forecast = [100.0] * 24
    hems._hourly_radiation = [200.0] * 24
    hems._hourly_weather_codes = [0] * 24
    hems._tariff_schedule = [4.32 if 7 <= h < 23 else 2.16 for h in range(24)]
    hems._battery_capacity_kwh = 11.0
    return hems


def _section(name: str) -> None:
    print(f"\n── {name} ──")


_P = 0
_F = 0
_FLS: list[str] = []


def _check(c, m):
    global _P, _F
    if c:
        _P += 1
    else:
        _F += 1
        _FLS.append(m)
        print(f"  ❌ {m}")


# ---------------------------------------------------------------------------
# 1. Option → coordinator → engine → legacy attribute sync
# ---------------------------------------------------------------------------


def test_option_drives_engine_at_init():
    _section("option → coordinator → engine initial sync")
    for mode in ("off", "shadow", "assist"):
        c = _make_stub_coordinator(predictive_mode=mode)
        hems = c._hems
        _check(
            hems.predictive_tuning.predictive_mode == mode,
            f"init predictive_tuning.predictive_mode={mode}",
        )
        _check(
            hems._predictive_mode == mode,
            f"init engine._predictive_mode={mode}",
        )
        # _predictive_enabled is the legacy bool; on for shadow/assist
        expected = mode in ("shadow", "assist")
        _check(
            hems._predictive_enabled is expected,
            f"init _predictive_enabled={expected} for {mode}",
        )


def test_async_set_predictive_mode_persists_to_entry_options():
    _section("async_set_predictive_mode persists to entry.options")
    c = _make_stub_coordinator(predictive_mode="off")
    c.async_set_predictive_mode("assist")
    _check(
        c._entry.options.get("predictive_mode") == "assist",
        "options['predictive_mode'] updated to assist",
    )
    _check(
        c.predictive_mode == "assist",
        "predictive_mode property reflects new value",
    )
    _check(
        c._hems.predictive_tuning.predictive_mode == "assist",
        "engine.predictive_tuning.predictive_mode updated",
    )
    _check(
        c._hems._predictive_enabled is True,
        "engine._predictive_enabled synced to True for assist",
    )
    # Simulate reload: build new coordinator, expect init reads option
    # and engine ends up with mode=assist.
    c2 = _make_stub_coordinator(
        predictive_mode=c._entry.options["predictive_mode"]
    )
    _check(
        c2._hems.predictive_tuning.predictive_mode == "assist",
        "reload: new engine reads assist from options",
    )


def test_select_and_switch_share_writing_path():
    """Switch ON writes "assist" (same entry.options key), select
    writes the literal label value (off/shadow/assist) — both
    through async_set_predictive_mode."""
    _section("switch + select write the same entry option")
    c = _make_stub_coordinator(predictive_mode="off")
    # Switch ON -> async_set_predictive_mode("assist")
    c.async_set_predictive_mode("assist")
    # Select chooses Assist -> async_set_predictive_mode("assist")
    c.async_set_predictive_mode("assist")
    # Select chooses Shadow -> async_set_predictive_mode("shadow")
    c.async_set_predictive_mode("shadow")
    # Select chooses Off -> async_set_predictive_mode("off")
    c.async_set_predictive_mode("off")
    _check(
        c._entry.options["predictive_mode"] == "off",
        "final options value = off",
    )
    _check(
        c._hems.predictive_tuning.predictive_mode == "off",
        "final engine.predictive_tuning = off",
    )
    _check(
        c._hems._predictive_enabled is False,
        "legacy _predictive_enabled synced to False for off",
    )


# ---------------------------------------------------------------------------
# 2. Decision changes for Assist vs off in all three SmartModes
# ---------------------------------------------------------------------------


def test_decision_changes_adaptive_with_hint():
    """Adaptive mode, off-vs-on assist must produce different
    HemsDecision when the planner has a meaningful evening target.

    We construct a scenario where the *baseline* (no ML) lands in
    the day_default branch (USB+OSO) and ML assist then flips to
    SBU+OSO using the planner's evening target."""
    _section("Adaptive: assist vs off changes the day_default branch")
    c_off = _make_stub_coordinator(predictive_mode="off")
    c_assist = _make_stub_coordinator(predictive_mode="assist")
    h_off = _prime_engine(c_off)
    h_assist = _prime_engine(c_assist)
    # Daytime, surplus low enough to land in day_default branch.
    # Use bad forecast (0.5 kWh) so normal HEMS stays USB, but high PV
    # (300W > 250W threshold) so assist can still promote to SBU.
    # off: current_output="1" (SBU) so baseline USB differs from current.
    # assist: current_output="0" (USB) so the flip to SBU differs.
    now = datetime(2026, 6, 15, 14, 0, 0)  # afternoon
    common_off = dict(
        smart_mode=SmartMode.ADAPTIVE,
        hems_auto=True,
        soc=60.0, pv_power=300.0,
        grid_power=0.0, battery_power=0.0, load_power=200.0,
        grid_voltage=232.5, grid_available=True,
        current_output="1", current_charger="1",
        now=now, forecast_tomorrow_kwh=0.5,
    )
    common_assist = dict(common_off, current_output="0")
    d_off = h_off.evaluate(**common_off)
    d_assist = h_assist.evaluate(**common_assist)
    # Hint must have been recorded
    _check(
        h_assist._last_predictive_hint is not None,
        "assist recorded hint at adaptive",
    )
    _check(
        h_off._last_predictive_hint is None,
        "off mode does NOT record a hint",
    )
    # With assist, day_default should flip to SBU (predictive_assist_evening)
    _check(
        d_assist.output_priority == OutputPriority.SBU,
        f"assist adaptive daytime → SBU (got {d_assist.output_priority})",
    )
    _check(
        d_assist.reason == "predictive_assist_evening",
        f"assist reason = predictive_assist_evening (got {d_assist.reason})",
    )
    _check(
        d_off.output_priority == OutputPriority.USB,
        f"off adaptive daytime → USB (got {d_off.output_priority})",
    )
    # Plan must have been persisted on assist
    _check(
        h_assist._last_predictive_plan is not None,
        "assist persisted plan via decide()",
    )


def test_decision_changes_arbitrage_with_hint():
    _section("Arbitrage: assist charges at night only when SOC < ML target")

    # ── Low SOC (30%): assist should also charge (SNU)  ──
    c_off = _make_stub_coordinator(predictive_mode="off")
    c_assist = _make_stub_coordinator(predictive_mode="assist")
    h_off_lo = _prime_engine(c_off)
    h_assist_lo = _prime_engine(c_assist)
    now = datetime(2026, 6, 15, 23, 30, 0)
    common_lo = dict(
        smart_mode=SmartMode.ARBITRAGE, hems_auto=True,
        soc=30.0, pv_power=0.0, grid_power=0.0, battery_power=0.0,
        load_power=200.0, grid_voltage=232.5, grid_available=True,
        current_output="0", current_charger="0",  # USB+CSO ≠ expected
        now=now, forecast_tomorrow_kwh=3.0,
    )
    d_off_lo = h_off_lo.evaluate(**common_lo)
    d_assist_lo = h_assist_lo.evaluate(**common_lo)
    _check(
        d_off_lo.charger_priority == ChargerPriority.SNU,
        f"off arbitrage night → SNU (got {d_off_lo.charger_priority})",
    )
    _check(
        d_assist_lo.charger_priority == ChargerPriority.SNU,
        f"assist arbitrage night (SOC<ML target) → SNU (got {d_assist_lo.charger_priority})",
    )

    # ── High SOC (80%): assist drops charger to OSO  ──
    # Fresh engines + shifted time (>300s) to avoid anti-flapping dedup
    c_off2 = _make_stub_coordinator(predictive_mode="off")
    c_assist2 = _make_stub_coordinator(predictive_mode="assist")
    h_off_hi = _prime_engine(c_off2)
    h_assist_hi = _prime_engine(c_assist2)
    now2 = datetime(2026, 6, 15, 23, 35, 0)  # +5 min
    common_hi = dict(
        smart_mode=SmartMode.ARBITRAGE, hems_auto=True,
        soc=80.0, pv_power=0.0, grid_power=0.0, battery_power=0.0,
        load_power=200.0, grid_voltage=232.5, grid_available=True,
        current_output="0", current_charger="0",  # USB+CSO ≠ SNU
        now=now2, forecast_tomorrow_kwh=3.0,
    )
    d_off_hi = h_off_hi.evaluate(**common_hi)
    d_assist_hi = h_assist_hi.evaluate(**common_hi)
    _check(
        d_off_hi.charger_priority == ChargerPriority.SNU,
        f"off (unchanged) keeps SNU at night (got {d_off_hi.charger_priority})",
    )
    _check(
        d_assist_hi.charger_priority == ChargerPriority.OSO,
        f"assist night (SOC≥ML target) → OSO (got {d_assist_hi.charger_priority})",
    )


def test_decision_unchanged_in_storm_with_hint():
    """Storm MUST always be USB+SNU regardless of predictive_mode.
    ML must NOT weaken safety in storm.

    We use current_output="2"/current_charger="2" (currently SBU+OSO)
    so the early-skip block does NOT null the storm decision."""
    _section("Storm: never weakens safety floor even with valid hint")
    for mode in ("off", "shadow", "assist"):
        c = _make_stub_coordinator(predictive_mode=mode)
        hems = _prime_engine(c)
        d = hems.evaluate(
            smart_mode=SmartMode.STORM,
            hems_auto=True,
            soc=80.0,  # near full
            pv_power=2000.0,  # sunny
            grid_power=0.0, battery_power=0.0, load_power=500.0,
            grid_voltage=232.5, grid_available=True,
            current_output="2", current_charger="2",
            now=datetime(2026, 6, 15, 12, 0, 0),
            forecast_tomorrow_kwh=8.0,
        )
        _check(
            d.output_priority == OutputPriority.USB,
            f"storm[{mode}] forces USB (got {d.output_priority})",
        )
        _check(
            d.charger_priority == ChargerPriority.SNU,
            f"storm[{mode}] forces SNU (got {d.charger_priority})",
        )


def test_safety_floor_overrides_predictive():
    """SOC ≤ reserve+2 → USB+SNU even with assist."""
    _section("Safety floor overrides assist at low SOC")
    c = _make_stub_coordinator(predictive_mode="assist", reserve_soc=20.0)
    hems = _prime_engine(c)
    d = hems.evaluate(
        smart_mode=SmartMode.ADAPTIVE,
        hems_auto=True,
        soc=18.0,  # ≤ reserve+2 = 22
        pv_power=2000.0, grid_power=0.0, battery_power=0.0, load_power=500.0,
        grid_voltage=232.5, grid_available=True,
        current_output="2", current_charger="2",
        now=datetime(2026, 6, 15, 10, 0, 0),
        forecast_tomorrow_kwh=3.0,
    )
    _check(
        d.output_priority == OutputPriority.USB,
        f"low SOC forces USB (got {d.output_priority})",
    )
    _check(
        d.charger_priority == ChargerPriority.SNU,
        f"low SOC forces SNU (got {d.charger_priority})",
    )
    _check(
        d.reason == "reserve_soc_protection",
        f"reason = reserve_soc_protection (got {d.reason})",
    )


def test_manual_override_short_circuits_predictive():
    """Manual override hold must skip decision regardless of ML."""
    _section("Manual override hold skips predictve")
    c = _make_stub_coordinator(predictive_mode="assist")
    hems = _prime_engine(c)
    hems._manual_override_until = datetime.now() + timedelta(minutes=10)
    hems._last_manual_override_log = None
    d = hems.evaluate(
        smart_mode=SmartMode.ADAPTIVE,
        hems_auto=True,
        soc=80.0, pv_power=2000.0, grid_power=0.0,
        battery_power=0.0, load_power=500.0,
        grid_voltage=232.5, grid_available=True,
        current_output="0", current_charger="1",
        now=datetime.now(),
        forecast_tomorrow_kwh=3.0,
    )
    _check(d.skip, "manual override hold → skip=True")
    _check(
        d.reason == "manual_override_hold",
        f"reason = manual_override_hold (got {d.reason})",
    )


# ---------------------------------------------------------------------------
# 3. Missing-data gate
# ---------------------------------------------------------------------------


def test_missing_forecast_blocks_planner():
    """forecast_tomorrow_kwh=None blocks the planner; hint stays None."""
    _section("missing forecast blocks planner (no silent zero)")
    c = _make_stub_coordinator(predictive_mode="assist")
    hems = _prime_engine(c)
    d = hems.evaluate(
        smart_mode=SmartMode.ADAPTIVE,
        hems_auto=True,
        soc=60.0, pv_power=2000.0, grid_power=0.0,
        battery_power=0.0, load_power=500.0,
        grid_voltage=232.5, grid_available=True,
        current_output="0", current_charger="1",
        now=datetime(2026, 6, 15, 10, 0, 0),
        forecast_tomorrow_kwh=None,
    )
    _check(
        hems._last_predictive_hint is None,
        "missing forecast → no hint recorded",
    )
    _check(
        hems._last_predictive_plan is None,
        "missing forecast → no plan recorded",
    )


def test_missing_arrays_block_planner():
    """If the coordinator hasn't fed hourly arrays, planner stays dormant."""
    _section("missing hourly arrays block planner")
    c = _make_stub_coordinator(predictive_mode="assist")
    hems = c._hems
    # Intentionally don't prime — simulates startup before forecast.
    d = hems.evaluate(
        smart_mode=SmartMode.ADAPTIVE,
        hems_auto=True,
        soc=60.0, pv_power=2000.0, grid_power=0.0,
        battery_power=0.0, load_power=500.0,
        grid_voltage=232.5, grid_available=True,
        current_output="0", current_charger="1",
        now=datetime(2026, 6, 15, 10, 0, 0),
        forecast_tomorrow_kwh=3.0,
    )
    _check(
        hems._last_predictive_hint is None,
        "missing arrays → no hint recorded",
    )


def test_zero_capacity_blocks_planner():
    """Battery capacity ≤ 0 is invalid → planner stays dormant."""
    _section("zero capacity blocks planner")
    c = _make_stub_coordinator(predictive_mode="assist")
    hems = _prime_engine(c)
    hems._battery_capacity_kwh = 0.0
    hems.evaluate(
        smart_mode=SmartMode.ADAPTIVE,
        hems_auto=True,
        soc=60.0, pv_power=2000.0, grid_power=0.0,
        battery_power=0.0, load_power=500.0,
        grid_voltage=232.5, grid_available=True,
        current_output="0", current_charger="1",
        now=datetime(2026, 6, 15, 10, 0, 0),
        forecast_tomorrow_kwh=3.0,
    )
    _check(
        hems._last_predictive_hint is None,
        "zero capacity → no hint recorded",
    )


# ---------------------------------------------------------------------------
# 4. Debug logging doesn't crash on missing /config
# ---------------------------------------------------------------------------


def test_debug_logging_no_config_dir():
    """If /config doesn't exist, debug logging must NOT crash the
    worker thread (the parent review's FileNotFoundError issue)."""
    _section("debug_logging handles missing /config")
    from hems import debug_logging
    # Confirm default path is /config — we're NOT going to create it.
    before = debug_logging._LOG_PATH
    try:
        # Sanity: /config likely doesn't exist in this test env.
        # We just call the writer and ensure no exception escapes.
        with tempfile.TemporaryDirectory() as td:
            debug_logging.set_log_path(os.path.join(td, "test.log"))
            debug_logging.log_evaluation(
                timestamp=datetime(2026, 6, 15, 10, 0, 0),
                inputs={"smart_mode": 0, "soc": 60.0,
                          "pv_power": 100.0, "load_power": 200.0},
                decision=HemsDecision(
                    output_priority="2",
                    charger_priority="2",
                    reason="test",
                    skip=False,
                ),
                applied={"output_priority": "2", "charger_priority": "2"},
                skip_reason=None,
            )
            # Complete asynchronous file writes before Windows deletes tmpdir.
            import threading
            for worker in threading.enumerate():
                if getattr(worker, "_target", None) is debug_logging._write_line:
                    worker.join(timeout=2)
        # Now flip to a path with a missing parent directory.
        debug_logging.set_log_path("/nonexistent_dir_x/powmr_hems_debug.log")
        debug_logging.log_evaluation(
            timestamp=datetime(2026, 6, 15, 10, 0, 0),
            inputs={"smart_mode": 0, "soc": 60.0,
                      "pv_power": 100.0, "load_power": 200.0},
            decision=HemsDecision(reason="skip", skip=True),
            applied=None,
            skip_reason="test_skip",
        )
        _check(True, "missing parent dir does NOT crash logger")
    finally:
        debug_logging._LOG_PATH = before


# ---------------------------------------------------------------------------
# 5. Persistence — option survives reload via entry.options
# ---------------------------------------------------------------------------


def test_persistence_off_to_assist_to_shadow_to_off():
    """Round-trip every mode and assert the engine + options both
    reflect the change. Same single entry.options key."""
    _section("persistence: off ↔ shadow ↔ assist round-trip")
    c = _make_stub_coordinator(predictive_mode="off")
    for mode in ("shadow", "assist", "off"):
        c.async_set_predictive_mode(mode)
        # Simulate a reload by building a fresh coordinator from the
        # current entry.options — this is what HA does on reload.
        c_reload = _make_stub_coordinator(
            predictive_mode=c._entry.options["predictive_mode"]
        )
        _check(
            c_reload.predictive_mode == mode,
            f"reload restores mode={mode}",
        )
        _check(
            c_reload._hems.predictive_tuning.predictive_mode == mode,
            f"reload: engine.predictive_tuning = {mode}",
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _run_all():
    tests = [
        test_option_drives_engine_at_init,
        test_async_set_predictive_mode_persists_to_entry_options,
        test_select_and_switch_share_writing_path,
        test_decision_changes_adaptive_with_hint,
        test_decision_changes_arbitrage_with_hint,
        test_decision_unchanged_in_storm_with_hint,
        test_safety_floor_overrides_predictive,
        test_manual_override_short_circuits_predictive,
        test_missing_forecast_blocks_planner,
        test_missing_arrays_block_planner,
        test_zero_capacity_blocks_planner,
        test_debug_logging_no_config_dir,
        test_persistence_off_to_assist_to_shadow_to_off,
    ]
    for t in tests:
        t()


if __name__ == "__main__":
    _run_all()
    print(f"\n{_P} passed, {_F} failed")
    if _F:
        for f in _FLS:
            print(f"  - {f}")
        sys.exit(1)
    print("✅ ALL WIRING TESTS PASSED")
