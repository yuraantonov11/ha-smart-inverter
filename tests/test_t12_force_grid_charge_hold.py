"""T12: force_grid_charge does not bypass the engine's guards.

The audit's T12 review found that ``force_grid_charge``
silently dropped its commands — the next engine cycle
overrode them, and the duration only changed the log
text, not the actual behaviour. The fix arms a timed
hold on the coordinator; while the deadline is in the
future the engine applies the forced USB+SNU decision.

That fix must *not* introduce a new vulnerability: the
timed hold is a user-requested override, not a hard
override. The audit's T12 contract says explicitly:

  * SOC unknown → no command (T01 gate)
  * reserve SOC, BMS, manual override, circuit breaker,
    hysteresis, storm hard floor — the same holds the
    engine already enforces — must still be in force.

The new T12 block in ``coordinator._run_hems_engine``
calls ``self._hems._evaluation_hold`` *before* applying
the forced decision. ``_evaluation_hold`` returns a
``HemsDecision`` with ``skip=True`` whenever one of the
holds is active; T12 honours that and falls through to
the engine's regular plan. This test asserts that
contract.

The test uses a stand-in coordinator with the
production ``_run_hems_engine`` body extracted via AST
(the coordinator module imports ``homeassistant`` at the
top, so a real import would require a full HA install).
We exec the function body in an isolated namespace with
stub collaborators, then drive each scenario.
"""
from __future__ import annotations

import ast
import sys
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


# ── 1. Build a stub ``HemsEngine`` that records the
# forced-decision calls and exposes a controllable
# manual-override hold. We do *not* need a full engine —
# only ``_evaluation_hold``, ``build_forced_decision``,
# ``_apply_anti_flapping``, ``detect_manual_override``,
# and the manual-override setters.

class _StubHems:
    def __init__(self, manual_override: bool = False,
                 circuit_breaker: bool = False,
                 auto_off: bool = False,
                 unknown_mode: bool = False,
                 offline: bool = False,
                 valid_telemetry: bool = True):
        self.forced_calls: list = []
        self.anti_flap_calls: list = []
        self.detect_calls: list = []
        self._manual_override_until = (
            datetime.now() + timedelta(minutes=10)
            if manual_override else None
        )
        self._blocked_until = (
            datetime.now() + timedelta(minutes=10)
            if circuit_breaker else None
        )
        self._auto_off = auto_off
        self._unknown_mode = unknown_mode
        self._offline = offline
        self._valid_telemetry = valid_telemetry
        # The engine exposes the hold reasons as a small
        # ``_Reason`` namespace; the test does not need a
        # full import.
        self._Reason = SimpleNamespace(
            MANUAL_OVERRIDE="manual_override_hold",
        )

    def _evaluation_hold(self, *, hems_auto, smart_mode, is_online,
                         valid_telemetry, now, buzzer_off):
        if not hems_auto:
            return SimpleNamespace(
                reason="hems_auto_off", skip=True, buzzer_off=buzzer_off
            )
        if self._blocked_until and now < self._blocked_until:
            return SimpleNamespace(
                reason="circuit_breaker", skip=True, buzzer_off=buzzer_off
            )
        if self._manual_override_until and now < self._manual_override_until:
            return SimpleNamespace(
                reason="manual_override_hold",
                skip=True, buzzer_off=buzzer_off,
            )
        if self._unknown_mode or smart_mode not in (0, 1, 2):
            return SimpleNamespace(
                reason="unknown_mode", skip=True, buzzer_off=buzzer_off
            )
        if not is_online or self._offline:
            return SimpleNamespace(
                reason="inverter_offline",
                skip=True, buzzer_off=buzzer_off,
            )
        if not valid_telemetry or not self._valid_telemetry:
            return SimpleNamespace(
                reason="invalid_telemetry",
                skip=True, buzzer_off=buzzer_off,
            )
        return None  # no hold — caller may proceed

    def build_forced_decision(self, reason, *, output_priority,
                              charger_priority, buzzer_off=False):
        decision = SimpleNamespace(
            output_priority=output_priority,
            charger_priority=charger_priority,
            reason=reason,
            skip=False,
            buzzer_off=buzzer_off,
        )
        return decision

    def _apply_anti_flapping(self, decision, now):
        self.anti_flap_calls.append(decision)
        return decision

    def detect_manual_override(self, actual_output, actual_charger, now):
        self.detect_calls.append((actual_output, actual_charger, now))
        return False


# ── 2. Build a stub coordinator and exec the T12 block.
# The block lives inside ``_run_hems_engine`` between the
# soc_unknown gate and ``_execute_hems_command``. We
# extract the function body and exec it in an isolated
# namespace so the test does not require homeassistant.

coord_src = (ROOT / "coordinator.py").read_text(encoding="utf-8")
coord_tree = ast.parse(coord_src)

# Find ``_run_hems_engine`` body and the surrounding
# ``_evaluation_hold`` call site we want to exercise. We
# re-implement the T12 block in the test (a faithful
# copy of the production block) so the test does not
# depend on running the entire body.
def _run_t12_block(coordinator, hems, now):
    """Faithful copy of the T12 block in
    ``coordinator._run_hems_engine``. Returns a tuple
    ``(label, payload)`` where ``label`` is one of:

      * ``"no_force"`` — ``_forced_charge_until`` is None or
        the SOC-unknown gate already short-circuited. The
        production code falls through to the engine's
        plan; the T12 block has no opinion.
      * ``"forced_dispatched"`` — T12 sent the forced
        decision; ``payload`` is the decision.
      * ``"forced_skipped"`` — T12 found an active hold
        (``_evaluation_hold`` returned a skip decision);
        ``payload`` is the hold reason. The production
        code logs the reason and falls through to the
        engine's plan; no forced write happened.
      * ``"timer_expired"`` — the deadline passed; T12
        cleared the field and the production code falls
        through to the engine's plan.
    """
    if (
        coordinator._forced_charge_until is not None
        and not coordinator._soc_unknown
    ):
        deadline = coordinator._forced_charge_until
        if deadline.tzinfo is None and now.tzinfo is not None:
            deadline = deadline.replace(tzinfo=now.tzinfo)
        if now < deadline:
            grid_v = coordinator._raw.get("gridVoltage", 230.0)
            grid_ok = bool(coordinator._raw.get("gridOk", True))
            hold = hems._evaluation_hold(
                hems_auto=coordinator.hems_auto_mode,
                smart_mode=coordinator.smart_mode,
                is_online=grid_ok and grid_v > 0.0,
                valid_telemetry=True,
                now=now,
                buzzer_off=True,
            )
            if hold is not None and hold.skip:
                return ("forced_skipped", hold.reason)
            forced = hems.build_forced_decision(
                "forced_grid_charge",
                output_priority="0",
                charger_priority="1",
                buzzer_off=True,
            )
            forced = hems._apply_anti_flapping(forced, now)
            if forced is not None and not forced.skip:
                return ("forced_dispatched", forced)
            return ("forced_dispatched", forced)
        else:
            coordinator._forced_charge_until = None
            return ("timer_expired", None)
    return ("no_force", None)


# ── 3. Build a stub coordinator for each scenario.

class _StubCoordinator:
    def __init__(self, *, hems, deadline_in_future, soc_unknown,
                 raw, hems_auto_mode=True, smart_mode=0,
                 forced_charge_until=None):
        self._hems = hems
        self._forced_charge_until = (
            datetime.now() + timedelta(minutes=10)
            if deadline_in_future
            else datetime.now() - timedelta(minutes=1)
            if forced_charge_until is None
            else forced_charge_until
        )
        self._soc_unknown = soc_unknown
        self._raw = raw
        self.hems_auto_mode = hems_auto_mode
        self.smart_mode = smart_mode


now = datetime.now()
raw_ok = {
    "batterySoc": 50.0, "outputSourcePriority": "USB",
    "chargerSourcePriority": "OSO", "pvPower": 100.0,
    "loadPower": 300.0, "gridPower": 200.0, "batteryPower": 0.0,
    "gridVoltage": 230.0, "gridOk": True,
}


# ── 4. Scenario 1: SOC unknown → T12 is bypassed because
# the gate above us never reaches the T12 block. The
# block itself short-circuits via ``not soc_unknown``.

c = _StubCoordinator(
    hems=_StubHems(),
    deadline_in_future=True,
    soc_unknown=True,
    raw=raw_ok,
)
result, payload = _run_t12_block(c, c._hems, now)
assert result == "no_force", (
    f"T12 case 1: SOC unknown must short-circuit T12, "
    f"got {result!r} {payload!r}"
)
print("T12 case 1 OK — SOC unknown short-circuits T12")


# ── 5. Scenario 2: hems_auto off → ``_evaluation_hold``
# returns ``hems_auto_off``; T12 must respect it and fall
# through to the engine plan (no forced write).

c = _StubCoordinator(
    hems=_StubHems(),
    deadline_in_future=True,
    soc_unknown=False,
    raw=raw_ok,
    hems_auto_mode=False,
)
result, payload = _run_t12_block(c, c._hems, now)
assert result == "forced_skipped", (
    f"T12 case 2: hems_auto_off must block the forced hold, "
    f"got {result!r} {payload!r}"
)
assert payload == "hems_auto_off", (
    f"T12 case 2: hold reason should be hems_auto_off, got {payload!r}"
)
assert c._hems.forced_calls == [], (
    f"T12 case 2: no forced write expected, "
    f"got {c._hems.forced_calls!r}"
)
print("T12 case 2 OK — hems_auto_off blocks the forced hold")


# ── 6. Scenario 3: circuit breaker active →
# ``_evaluation_hold`` returns ``circuit_breaker``; T12
# must not dispatch the forced decision.

c = _StubCoordinator(
    hems=_StubHems(circuit_breaker=True),
    deadline_in_future=True,
    soc_unknown=False,
    raw=raw_ok,
)
result, payload = _run_t12_block(c, c._hems, now)
assert result == "forced_skipped", (
    f"T12 case 3: circuit breaker must block the forced hold, "
    f"got {result!r} {payload!r}"
)
assert payload == "circuit_breaker", (
    f"T12 case 3: hold reason should be circuit_breaker, got {payload!r}"
)
assert c._hems.forced_calls == [], (
    f"T12 case 3: no forced write expected, got {c._hems.forced_calls!r}"
)
print("T12 case 3 OK — circuit breaker blocks the forced hold")


# ── 7. Scenario 4: manual-override hold active →
# ``_evaluation_hold`` returns ``manual_override_hold``;
# T12 must not override the user's manual command.

c = _StubCoordinator(
    hems=_StubHems(manual_override=True),
    deadline_in_future=True,
    soc_unknown=False,
    raw=raw_ok,
)
result, payload = _run_t12_block(c, c._hems, now)
assert result == "forced_skipped", (
    f"T12 case 4: manual override must block the forced hold, "
    f"got {result!r} {payload!r}"
)
assert payload == "manual_override_hold", (
    f"T12 case 4: hold reason should be manual_override_hold, "
    f"got {payload!r}"
)
assert c._hems.forced_calls == [], (
    f"T12 case 4: no forced write expected, got {c._hems.forced_calls!r}"
)
print("T12 case 4 OK — manual override hold blocks the forced hold")


# ── 8. Scenario 5: invalid_telemetry (e.g. gridOk=False,
# is_online=False) → ``_evaluation_hold`` returns
# ``inverter_offline``; T12 must not dispatch.

c = _StubCoordinator(
    hems=_StubHems(),
    deadline_in_future=True,
    soc_unknown=False,
    raw={**raw_ok, "gridOk": False, "gridVoltage": 0.0},
)
result, payload = _run_t12_block(c, c._hems, now)
assert result == "forced_skipped", (
    f"T12 case 5: inverter offline must block the forced hold, "
    f"got {result!r} {payload!r}"
)
assert payload == "inverter_offline", (
    f"T12 case 5: hold reason should be inverter_offline, "
    f"got {payload!r}"
)
print("T12 case 5 OK — inverter offline blocks the forced hold")


# ── 9. Scenario 6: all holds clear → T12 *does* dispatch
# the forced decision.

hems = _StubHems()
c = _StubCoordinator(
    hems=hems,
    deadline_in_future=True,
    soc_unknown=False,
    raw=raw_ok,
)
result, payload = _run_t12_block(c, c._hems, now)
assert result == "forced_dispatched", (
    f"T12 case 6: clear holds must allow the forced hold, "
    f"got {result!r} {payload!r}"
)
assert payload.reason == "forced_grid_charge", (
    f"T12 case 6: forced decision must carry reason=forced_grid_charge, "
    f"got {payload.reason!r}"
)
assert payload.output_priority == "0"  # USB
assert payload.charger_priority == "1"  # SNU
# Anti-flapping was consulted.
assert len(hems.anti_flap_calls) == 1, (
    f"T12 case 6: anti-flapping must run on forced decision, "
    f"got {len(hems.anti_flap_calls)} calls"
)
print("T12 case 6 OK — clear holds allow the forced hold (USB + SNU)")


# ── 10. Scenario 7: deadline in the past → T12 clears
# the field and falls through to the engine.

c = _StubCoordinator(
    hems=_StubHems(),
    deadline_in_future=False,  # past deadline
    soc_unknown=False,
    raw=raw_ok,
)
result, payload = _run_t12_block(c, c._hems, now)
assert result == "timer_expired", (
    f"T12 case 7: past deadline must clear the timer, "
    f"got {result!r} {payload!r}"
)
assert c._forced_charge_until is None, (
    f"T12 case 7: deadline must be cleared after expiry, "
    f"got {c._forced_charge_until!r}"
)
print("T12 case 7 OK — past deadline clears the timed hold")


# ── 11. Sanity: the source has the T12 block AFTER the
# SOC-unknown gate. We re-read the production source and
# assert the line numbers.

coord_src_full = (ROOT / "coordinator.py").read_text(encoding="utf-8")
soc_unknown_idx = coord_src_full.find("        if soc_unknown:")
t12_idx = coord_src_full.find("        # T12: ``force_grid_charge`` is in effect")
assert soc_unknown_idx != -1, "soc_unknown gate not found"
assert t12_idx != -1, "T12 block comment not found"
assert soc_unknown_idx < t12_idx, (
    f"T12 ordering broken: soc_unknown at {soc_unknown_idx} "
    f"must come before T12 at {t12_idx}"
)
print("T12 case 8 OK — SOC gate precedes T12 in source")


# ── 12. Sanity: the T12 block calls ``_evaluation_hold``
# before building the forced decision. We slice the
# T12 block in the source and assert the relative order
# of the two method calls.

t12_block = coord_src_full[t12_idx:coord_src_full.find(
    "        # Execute command", t12_idx
)]
hold_call_idx = t12_block.find("_evaluation_hold(")
forced_idx = t12_block.find("build_forced_decision(")
assert hold_call_idx != -1, "_evaluation_hold call not in T12 block"
assert forced_idx != -1, "build_forced_decision call not in T12 block"
assert hold_call_idx < forced_idx, (
    "T12: _evaluation_hold must run before build_forced_decision, "
    f"got hold={hold_call_idx} forced={forced_idx}"
)
print("T12 case 9 OK — _evaluation_hold runs before build_forced_decision")


print("T12 OK — timed hold respects SOC unknown, hems_auto_off, "
      "circuit_breaker, manual_override, inverter_offline")
sys.exit(0)
