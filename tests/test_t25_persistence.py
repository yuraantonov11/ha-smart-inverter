"""T25: regression tests for per-entry persistence.

The audit demanded:
  * Schedule rules added
    or removed via the
    ``add_schedule_rule`` /
    ``delete_schedule_rule``
    services MUST survive
    an HA restart.
  * Battery SoH cycle
    count and last
    install date MUST
    survive an HA
    restart.
  * Demand forecast EWMA
    profile MUST survive
    an HA restart.
  * The keepalive
    command path has
    exactly one owner:
    the Python engine.
  * There is a single
    active schedule
    rules registry.
  * Persistence is
    throttled (no
    per-cycle
    ``async_update_entry``).

The tests inspect the
production source
directly and exec the
``save_to_dict`` /
``to_dict`` contract
through the service
modules. The full
coordinator is not
imported because the
test runner does not
have Home Assistant
installed — the
audit's test rig is
the same as T22/T23:
production code
exercised in isolation,
no mocks for the
target of the test.
"""
from __future__ import annotations

import ast
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from hems.battery_soh import BatterySoH  # noqa: E402
from hems.schedule_rules import (  # noqa: E402
    ScheduleRulesService,
    ScheduleRule,
)
from hems.demand_forecast import (  # noqa: E402
    DemandForecastService,
)


def _read_source(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def _function_node(src: str, name: str):
    """Return the AST
    ``FunctionDef`` for
    ``name``. Searches at
    module level first
    and inside
    ``ClassDef`` bodies.
    """
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if (
                    isinstance(child, ast.FunctionDef)
                    and child.name == name
                ):
                    return child, node.name
        elif isinstance(node, ast.FunctionDef) and node.name == name:
            return node, None
    return None, None


class TestScheduleRulesPersistence(unittest.TestCase):
    """T25: ``add_rule`` and
    ``delete_rule`` must
    trigger a
    ``config_entries.async_update_entry``
    call so the change
    survives restart."""

    def test_coordinator_exposes_persist_helper(self) -> None:
        coord_src = _read_source("coordinator.py")
        self.assertIn(
            "_persist_schedule_rules",
            coord_src,
            "coordinator.py must expose "
            "_persist_schedule_rules so "
            "schedule-rule mutations can "
            "be persisted on add / delete / "
            "update.",
        )
        node, _ = _function_node(
            coord_src, "_persist_schedule_rules"
        )
        self.assertIsNotNone(
            node, "AST: _persist_schedule_rules missing"
        )
        src_text = ast.unparse(node)
        self.assertIn("async_update_entry", src_text)
        self.assertIn("schedule_rules", src_text)
        self.assertIn("save_to_dict", src_text)

    def test_services_call_persist_helper(self) -> None:
        services_src = _read_source(
            "services/control.py"
        )
        for handle in (
            "handle_add_schedule_rule",
            "handle_delete_schedule_rule",
        ):
            self.assertIn(handle, services_src)
        self.assertIn(
            "_persist_schedule_rules",
            services_src,
            "services/control.py must call "
            "coordinator._persist_schedule_rules "
            "after every add / delete so "
            "the registry survives restart.",
        )
        # ``add_rule`` must
        # appear BEFORE
        # ``_persist_schedule_rules``
        # in the handler so
        # the new state is
        # captured.
        add_idx = services_src.find(
            "coordinator.schedule_rules.add_rule"
        )
        self.assertGreater(add_idx, 0)
        add_persist_idx = services_src.find(
            "_persist_schedule_rules",
            add_idx,
        )
        self.assertGreater(
            add_persist_idx, add_idx,
            "add_rule must run BEFORE "
            "_persist_schedule_rules so "
            "the persisted blob reflects "
            "the new rule.",
        )

    def test_service_persists_added_rule(self) -> None:
        """End-to-end on the
        real
        ``ScheduleRulesService``:
        ``add_rule`` then
        ``save_to_dict``
        returns the new
        rule."""
        svc = ScheduleRulesService()
        rule = ScheduleRule(
            name="morning",
            days_of_week=[1, 2, 3, 4, 5],
            start_hour=7,
            start_minute=0,
            end_hour=9,
            end_minute=0,
            mode=0,
            priority=5,
        )
        svc.add_rule(rule)
        blob = svc.save_to_dict()
        self.assertIn(
            "schedule_rules_v1", blob
        )
        self.assertEqual(
            len(blob["schedule_rules_v1"]), 1
        )
        self.assertEqual(
            blob["schedule_rules_v1"][0]["name"],
            "morning",
        )

    def test_delete_then_save(self) -> None:
        svc = ScheduleRulesService()
        rule = ScheduleRule(
            name="evening",
            days_of_week=[5, 6],
            start_hour=20,
            start_minute=0,
            end_hour=22,
            end_minute=0,
            mode=2,
            priority=5,
        )
        svc.add_rule(rule)
        svc.delete_rule(rule.id)
        blob = svc.save_to_dict()
        self.assertEqual(
            len(blob["schedule_rules_v1"]), 0
        )


class TestBatterySoHPersistence(unittest.TestCase):
    """T25: ``track_soc`` must
    eventually persist the
    cycle_count and
    install_date so the
    data survives an HA
    restart. Persistence
    is rate-limited."""

    def test_persist_helper_in_coordinator(self) -> None:
        coord_src = _read_source("coordinator.py")
        node, _ = _function_node(
            coord_src, "_persist_battery_soh"
        )
        self.assertIsNotNone(node)
        src_text = ast.unparse(node)
        self.assertIn("async_update_entry", src_text)
        self.assertIn("battery_soh", src_text)
        self.assertIn("to_dict", src_text)

    def test_maybe_persist_helper_throttles(self) -> None:
        coord_src = _read_source("coordinator.py")
        node, _ = _function_node(
            coord_src, "_maybe_persist_battery_soh"
        )
        self.assertIsNotNone(node)
        src_text = ast.unparse(node)
        self.assertIn(
            "_SOH_PERSIST_MIN_INTERVAL_S", src_text
        )

    def test_to_dict_round_trip(self) -> None:
        soh = BatterySoH(cycle_count=7)
        soh._install_date = datetime(
            2024, 1, 1, tzinfo=timezone.utc
        )
        blob = soh.to_dict()
        self.assertEqual(blob["cycle_count"], 7)
        self.assertIsNotNone(blob["install_date"])
        soh2 = BatterySoH()
        soh2.load_from_dict(blob)
        self.assertEqual(soh2.cycle_count, 7)
        self.assertIsNotNone(soh2._install_date)

    def test_persist_called_in_main_loop(self) -> None:
        """The
        ``_maybe_persist_battery_soh``
        and
        ``_maybe_persist_demand_forecast``
        calls must appear
        inside the main
        update loop, not
        just defined."""
        coord_src = _read_source("coordinator.py")
        # Look in the
        # update loop body:
        # ``_run_hems_engine``
        # is where the
        # live trackers
        # are wired.
        i = coord_src.find("async def _run_hems_engine")
        j = coord_src.find(
            "async def _maybe_refresh_energy_stats", i
        )
        if j == -1:
            j = coord_src.find(
                "async def _maybe_refresh_load_history", i
            )
        if j == -1:
            j = len(coord_src)
        body = coord_src[i:j]
        self.assertIn(
            "_maybe_persist_battery_soh", body
        )
        self.assertIn(
            "_maybe_persist_demand_forecast", body
        )


class TestDemandForecastPersistence(unittest.TestCase):
    """T25: ``update_ewma``
    must eventually
    persist the EWMA
    profile. Persistence
    is rate-limited."""

    def test_to_dict_returns_serialisable_blob(self) -> None:
        svc = DemandForecastService()
        blob = svc.to_dict()
        # Must be
        # JSON-serialisable.
        json.dumps(blob)

    def test_persist_helper_in_coordinator(self) -> None:
        coord_src = _read_source("coordinator.py")
        node, _ = _function_node(
            coord_src, "_persist_demand_forecast"
        )
        self.assertIsNotNone(node)
        src_text = ast.unparse(node)
        self.assertIn("async_update_entry", src_text)
        self.assertIn(
            "demand_forecast_profile", src_text
        )
        self.assertIn("to_dict", src_text)


class TestKeepaliveSingleOwner(unittest.TestCase):
    """T25: keepalive has
    exactly one owner —
    the Python
    ``hems.engine``
    ``check_keepalive``
    method. The
    ``battery_keepalive.yaml``
    automation is dormant."""

    def test_engine_check_keepalive_is_referenced_by_coordinator(self) -> None:
        coord_src = _read_source("coordinator.py")
        self.assertIn(
            "_hems.keepalive",
            coord_src,
            "coordinator.py must reference "
            "self._hems.keepalive to gate "
            "the keepalive command path "
            "through the engine.",
        )

    def test_battery_keepalive_yaml_is_marked_dormant(self) -> None:
        """Defence in depth: the
        YAML automation is
        hard-wired to never
        fire."""
        import yaml
        path = (
            REPO_ROOT
            / "automations"
            / "battery_keepalive.yaml"
        )
        with open(path) as f:
            data = yaml.safe_load(f)
        self.assertIn("DORMANT", data.get("alias", ""))
        conds = data.get("condition", [])
        self.assertIn(
            "false",
            conds[0].get("value_template", "").lower(),
        )

    def test_engine_check_keepalive_has_safety_guards(self) -> None:
        """The audit required
        ``check_keepalive``
        to have its safety
        guards documented
        and present in the
        source."""
        eng_src = _read_source("hems/engine.py")
        tree = ast.parse(eng_src)
        found = False
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if (
                        isinstance(child, ast.FunctionDef)
                        and child.name == "check_keepalive"
                    ):
                        found = True
                        body_src = ast.unparse(child)
                        self.assertIn(
                            "in_progress", body_src
                        )
                        self.assertIn(
                            "last_activity_at", body_src
                        )
        self.assertTrue(
            found,
            "check_keepalive not found in hems/engine.py",
        )


class TestScheduleServicesSingleRegistry(unittest.TestCase):
    """T25: the schedule
    services write to one
    registry —
    ``coordinator._schedule_rules``."""

    def test_no_duplicate_schedule_registry(self) -> None:
        coord_src = _read_source("coordinator.py")
        services_src = _read_source(
            "services/control.py"
        )
        self.assertIn("_schedule_rules", coord_src)
        for bad in (
            "_hems_schedule",
            "_schedule_registry_alt",
            "_rules_registry",
            "_extra_rules",
        ):
            self.assertNotIn(bad, coord_src)
            self.assertNotIn(bad, services_src)
        self.assertIn(
            "coordinator.schedule_rules", services_src
        )


if __name__ == "__main__":
    unittest.main()
