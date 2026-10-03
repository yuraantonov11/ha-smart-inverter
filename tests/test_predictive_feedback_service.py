"""Feedback state/persistence and real service routing without HA imports."""
import ast
import asyncio
import sys
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hems.predictive_control import PredictiveControlEngine, apply_feedback, restore_feedback

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


class TestFeedback(unittest.TestCase):
    def test_reject_hour(self):
        e = PredictiveControlEngine()
        record = apply_feedback(e, "reject", NOW, 60)
        self.assertEqual(e._manual_override_until, NOW + timedelta(hours=1))
        self.assertEqual(record["action"], "reject")

    def test_modify_pending_target(self):
        e = PredictiveControlEngine()
        apply_feedback(e, "modify", NOW, new_target_soc=75)
        self.assertEqual(e._predictive_user_target_soc, 75)
        self.assertEqual(e._manual_override_until, NOW + timedelta(minutes=30))

    def test_approve_changes_nothing(self):
        e = PredictiveControlEngine()
        before = dict(e.__dict__)
        self.assertIsNone(apply_feedback(e, "approve", NOW))
        self.assertEqual(before, e.__dict__)

    def test_restore_and_expiry(self):
        old, new = PredictiveControlEngine(), PredictiveControlEngine()
        record = apply_feedback(old, "modify", NOW, 60, 75)
        restore_feedback(new, record, NOW + timedelta(minutes=1))
        self.assertEqual(new._manual_override_until, old._manual_override_until)
        self.assertEqual(new._predictive_user_target_soc, 75)
        expired = PredictiveControlEngine()
        restore_feedback(expired, record, NOW + timedelta(hours=2))
        self.assertIsNone(expired._manual_override_until)

    def test_validation_and_existing_hold(self):
        e = PredictiveControlEngine()
        for soc in (None, True, 19, 101, 75.5):
            with self.assertRaises(ValueError): apply_feedback(e, "modify", NOW, new_target_soc=soc)
        e._manual_override_until = NOW + timedelta(hours=2)
        apply_feedback(e, "reject", NOW, 30)
        self.assertEqual(e._manual_override_until, NOW + timedelta(hours=2))

    def test_real_service_routes_entry_and_refuses_ambiguity(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "services/__init__.py").read_text(encoding="utf-8"))
        register = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "async_register_services")
        handler = next(n for n in register.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_predictive_feedback")
        handler.returns = None
        handler.args.args[0].annotation = None
        c1, c2 = Mock(), Mock()
        hass = SimpleNamespace(data={"powmr_inverter": {"one": {"coordinator": c1}, "two": {"coordinator": c2}}},
            config_entries=SimpleNamespace(async_entries=lambda _: [SimpleNamespace(entry_id="one"), SimpleNamespace(entry_id="two")]))
        namespace = {"hass": hass, "DOMAIN": "powmr_inverter"}
        exec(compile(ast.Module(body=[handler], type_ignores=[]), "handler", "exec"), namespace)
        f = namespace[handler.name]
        asyncio.run(f(SimpleNamespace(data={"entry_id": "two", "action": "reject", "duration_min": 60})))
        c2.async_predictive_feedback.assert_called_once_with("reject", 60, None)
        c1.async_predictive_feedback.assert_not_called()
        with self.assertRaises(ValueError): asyncio.run(f(SimpleNamespace(data={"action": "approve"})))


if __name__ == "__main__": unittest.main()
