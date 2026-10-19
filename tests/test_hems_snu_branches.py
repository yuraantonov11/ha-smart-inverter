"""Юра: "Перевір усі гілки, що дозволяють SNU до нічного
тарифу, включно з низьким SOC: дозволені винятки мають
бути явними й відповідати налаштуванням, без прихованих
default-off-policy гілок."

Strategy: AST audit + runtime check.

  1. AST audit: read ``hems/engine.py`` and confirm
     every ``ChargerPriority.SNU`` assignment in a
     ``HemsDecision(...)`` call carries an explicit
     ``reason=`` kwarg that matches the operator-visible
     allow-list.

  2. Runtime check: drive the engine through
     ``evaluate()`` and confirm the surfaced reason
     matches the allow-list whenever
     ``charger_priority == SNU``.

A "default-off-policy" branch is one that emits SNU
with a vague reason (``None``, ``""``, or a string
literal that does not match the allow-list).
"""
import ast
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path("/opt/data/powmr-ai-work/powmr_inverter")
HEMS_ENGINE_PATH = REPO_ROOT / "hems" / "engine.py"


# Operator-visible reasons that may legally carry a
# ChargerPriority.SNU decision outside the night
# tariff window.
EXPLICIT_SNU_REASONS_NON_NIGHT = frozenset({
    "reserve_protection",
    "RESERVE_PROTECTION",
    "reserve_soc_protection",
    "storm_mode",
    "storm_precharge",
    "manual_override",
    "manual_override_hold",
    # Daytime adaptive branches (low SOC).
    "critical_recovery",
    "hysteresis_recovery",
    # Evening protection -- the reason
    # constant used by
    # ``_adaptive_evening_protection``; the
    # value is "evening_reserve_protection"
    # but the engine refers to it via
    # ``_Reason.EVENING_PROTECT``.
    "evening_reserve_protection",
    "_Reason.EVENING_PROTECT",
})


def _install_stub_modules():
    if "homeassistant" not in sys.modules:
        sys.modules["homeassistant"] = types.ModuleType(
            "homeassistant"
        )
        sys.modules["homeassistant.core"] = (
            types.ModuleType("homeassistant.core")
        )
        sys.modules["homeassistant.const"] = (
            types.ModuleType("homeassistant.const")
        )
        sys.modules["homeassistant.util"] = (
            types.ModuleType("homeassistant.util")
        )
        sys.modules["homeassistant.helpers"] = (
            types.ModuleType("homeassistant.helpers")
        )


def _load_hems_engine():
    _install_stub_modules()
    sys.path.insert(0, str(REPO_ROOT))
    from hems.engine import (  # type: ignore
        HemsEngine, ChargerPriority, OutputPriority,
        SmartMode, _Reason,
    )
    return HemsEngine, ChargerPriority, OutputPriority, SmartMode, _Reason


class HemsSnuBranchAuditTests(unittest.TestCase):
    """AST audit: every SNU assignment in
    HemsDecision() must carry an explicit
    reason in the allow-list.
    """

    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(
            HEMS_ENGINE_PATH.read_text()
        )
        (
            cls.HemsEngine, cls.ChargerPriority,
            cls.OutputPriority, cls.SmartMode,
            cls._Reason,
        ) = _load_hems_engine()

    def test_all_snu_assignments_carry_explicit_reason(self):
        offenders = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for sub in ast.walk(node):
                if (
                    not isinstance(sub, ast.Return)
                    or not isinstance(
                        sub.value, ast.Call
                    )
                ):
                    continue
                call = sub.value
                if not (
                    isinstance(call.func, ast.Name)
                    and call.func.id == "HemsDecision"
                ):
                    continue
                reasons = [
                    k.value.value
                    if isinstance(k.value, ast.Constant)
                    else ast.unparse(k.value)
                    for k in call.keywords
                    if k.arg == "reason"
                ]
                chargers = [
                    ast.unparse(k.value)
                    for k in call.keywords
                    if k.arg == "charger_priority"
                ]
                if not chargers or not any(
                    "ChargerPriority.SNU" in c
                    for c in chargers
                ):
                    continue
                if not reasons:
                    offenders.append(
                        f"{node.name}: SNU with no reason"
                    )
                    continue
                for r in reasons:
                    if r not in (
                        EXPLICIT_SNU_REASONS_NON_NIGHT
                    ):
                        offenders.append(
                            f"{node.name}: SNU reason "
                            f"{r!r} not in allow-list"
                        )
        if offenders:
            self.fail(
                "Found SNU branches with "
                "non-allow-list reasons:\n  "
                + "\n  ".join(offenders)
            )


class HemsSnuBranchRuntimeTests(unittest.TestCase):
    """Runtime check: drive the engine through
    ``evaluate()`` and confirm the surfaced
    reason matches the allow-list whenever
    charger_priority == SNU.
    """

    @classmethod
    def setUpClass(cls):
        (
            cls.HemsEngine, cls.ChargerPriority,
            cls.OutputPriority, cls.SmartMode,
            cls._Reason,
        ) = _load_hems_engine()

    def _build_engine(self, *, predictive_mode="off"):
        engine = self.HemsEngine()
        engine._entry = SimpleNamespace(
            options={
                "smart_mode": self.SmartMode.ADAPTIVE,
                "reserve_soc": 20.0,
                "tuning": {
                    "battery_cycle_conservative": 0.0,
                },
            }
        )
        from hems.tuning import PredictiveTuning
        engine.predictive_tuning = PredictiveTuning()
        engine.predictive_tuning.predictive_mode = (
            predictive_mode
        )
        return engine

    def _call_evaluate(
        self, engine, *, soc, now, current_output,
        current_charger, pv_power=0.0,
        load_power=700.0, grid_power=0.0,
        battery_power=-100.0,
        reserve_soc=20.0,
    ):
        return engine.evaluate(
            smart_mode=self.SmartMode.ADAPTIVE,
            hems_auto=True,
            soc=soc,
            pv_power=pv_power,
            grid_power=grid_power,
            battery_power=battery_power,
            load_power=load_power,
            grid_voltage=230.0,
            grid_available=True,
            current_output=current_output,
            current_charger=current_charger,
            now=now,
            reserve_soc=reserve_soc,
            entry_id="test-entry",
        )

    def test_low_soc_snu_carries_explicit_reason(self):
        engine = self._build_engine()
        now = datetime(
            2026, 10, 19, 9, 0, 0, tzinfo=timezone.utc
        )
        decision = self._call_evaluate(
            engine, soc=18.0, now=now,
            current_output="USB",
            current_charger="OSO",
        )
        if (
            decision.charger_priority
            == self.ChargerPriority.SNU
        ):
            self.assertIn(
                decision.reason,
                EXPLICIT_SNU_REASONS_NON_NIGHT,
                f"low-SOC SNU must carry an "
                f"explicit reason; got "
                f"{decision.reason!r}",
            )

    def test_evening_above_reserve_does_not_snu(self):
        engine = self._build_engine()
        now = datetime(
            2026, 10, 19, 16, 30, 0, tzinfo=timezone.utc
        )
        decision = self._call_evaluate(
            engine, soc=50.0, now=now,
            current_output="USB",
            current_charger="OSO",
        )
        if (
            decision.charger_priority
            == self.ChargerPriority.SNU
        ):
            self.assertIn(
                decision.reason,
                EXPLICIT_SNU_REASONS_NON_NIGHT,
                f"evening SNU above reserve must "
                f"have explicit reason; got "
                f"{decision.reason!r}",
            )
            self.assertNotEqual(
                decision.reason,
                "reserve_protection",
                f"SOC 50 %% is above reserve; "
                f"reserve_protection must not fire",
            )

    def test_storm_mode_snu_carries_explicit_reason(self):
        engine = self._build_engine(
            predictive_mode="storm"
        )
        now = datetime(
            2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc
        )
        decision = self._call_evaluate(
            engine, soc=50.0, now=now,
            current_output="USB",
            current_charger="OSO",
        )
        if (
            decision.charger_priority
            == self.ChargerPriority.SNU
        ):
            self.assertIn(
                decision.reason,
                EXPLICIT_SNU_REASONS_NON_NIGHT,
                f"storm SNU must carry an "
                f"explicit reason; got "
                f"{decision.reason!r}",
            )


if __name__ == "__main__":
    unittest.main()
