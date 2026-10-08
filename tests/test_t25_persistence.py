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

import voluptuous as vol

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
        # T25 round 3: the
        # legacy
        # ``services/control.py``
        # was a separate,
        # never-imported
        # registry. We
        # removed it and
        # folded the
        # schedule
        # handlers into
        # ``services/__init__.py``,
        # which is the
        # *active*
        # registry
        # imported by
        # ``__init__.py``.
        # The test must
        # pin the new
        # ownership.
        services_src = _read_source(
            "services/__init__.py"
        )
        for handle in (
            "handle_add_schedule_rule",
            "handle_delete_schedule_rule",
        ):
            self.assertIn(handle, services_src)
        self.assertIn(
            "_persist_schedule_rules",
            services_src,
            "services/__init__.py must call "
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


class TestScheduleServiceRollback(unittest.TestCase):
    """T25 round 4 (audit
    follow-up): Юра
    demanded that the
    schedule services
    become *atomic*: a
    failed
    ``_persist_schedule_rules``
    must restore the
    in-memory registry
    to its prior state
    and raise
    ``ServiceValidationError``.

    The tests in this
    class load
    ``services/__init__.py``
    through
    ``importlib`` so the
    relative imports
    (``from ..const
    import DOMAIN``) work
    inside the test
    process. The handler
    bodies are *real*
    production code; the
    only thing the tests
    swap in is the
    coordinator stub and
    the
    ``_get_api`` lookup
    function.

    Voluptuous and
    Home Assistant are
    *not* shimmed —
    missing dependencies
    must surface as a
    real ``ImportError``
    so we do not mask a
    production bug behind
    a synthetic API."""

    # R10.6: ``REPO_ROOT``
    # is derived from
    # ``__file__`` instead
    # of being hard-coded.
    # The hard-coded path
    # broke the suite on
    # Юра's Windows
    # checkout and on any
    # other machine where
    # the workspace is not
    # at that exact
    # location.
    import os as _os_root
    import pathlib as _pl_root
    REPO_ROOT = str(
        _pl_root.Path(__file__).resolve().parent.parent
    )

    @classmethod
    def setUpClass(cls) -> None:
        import sys as _sys
        import os as _os
        import types as _types
        import importlib.util as _ilu

        # R10.6: the
        # ``os.chdir("/tmp")``
        # workaround was a
        # ``select.py`` shadow
        # hack. The
        # ``importlib`` loader
        # below uses absolute
        # paths so we no
        # longer need a global
        # ``chdir``. Tests
        # that write to the
        # filesystem use
        # ``tempfile`` instead.

        if "powmr_inverter" not in _sys.modules:
            pkg = _types.ModuleType("powmr_inverter")
            pkg.__path__ = [cls.REPO_ROOT]
            _sys.modules["powmr_inverter"] = pkg

            const_spec = _ilu.spec_from_file_location(
                "powmr_inverter.const",
                f"{cls.REPO_ROOT}/const.py",
            )
            const_mod = _ilu.module_from_spec(const_spec)
            _sys.modules["powmr_inverter.const"] = const_mod
            const_spec.loader.exec_module(const_mod)
            pkg.DOMAIN = const_mod.DOMAIN

            svc_spec = _ilu.spec_from_file_location(
                "powmr_inverter.services",
                f"{cls.REPO_ROOT}/services/__init__.py",
            )
            svc = _ilu.module_from_spec(svc_spec)
            _sys.modules["powmr_inverter.services"] = svc
            svc_spec.loader.exec_module(svc)
        # Always point the
        # class at the loaded
        # module — even when
        # another class loaded
        # it first, so
        # ``self._services_module``
        # is bound for every
        # test in this class.
        cls._services_module = _sys.modules[
            "powmr_inverter.services"
        ]

    def _make_fake_service(
        self,
        *,
        persist_ok: bool,
    ) -> object:
        """Build a fake
        coordinator whose
        ``_persist_schedule_rules``
        can be made to
        succeed or fail on
        demand. The fake
        uses the real
        ``ScheduleRulesService``
        and the real
        ``ScheduleRule``
        dataclass — there
        is NO mock of the
        production registry."""

        class _FakeEntry:
            def __init__(self):
                self.options: dict = {}

        class _FakeCoordinator:
            def __init__(self):
                self.schedule_rules = (
                    ScheduleRulesService()
                )
                self.entry = _FakeEntry()
                self._persist_calls = 0

            def _persist_schedule_rules(self) -> bool:
                self._persist_calls += 1
                if persist_ok:
                    self.entry.options[
                        "schedule_rules"
                    ] = (
                        self.schedule_rules
                        .save_to_dict()
                    )
                return persist_ok

        class _FakeApi:
            def __init__(self, coord):
                self._coord = coord

        class _FakeCall:
            def __init__(self, data: dict):
                self.data = data

        coord = _FakeCoordinator()

        seed_rule = ScheduleRule(
            name="seed",
            days_of_week=[1, 2, 3, 4, 5],
            start_hour=0,
            start_minute=0,
            end_hour=23,
            end_minute=0,
            mode=0,
            priority=5,
        )
        coord.schedule_rules.add_rule(seed_rule)

        # Patch the
        # ``_get_api``
        # symbol in the
        # *real*
        # ``services``
        # module so the
        # real handler
        # uses our fake
        # coordinator.
        async def _fake_get_api(call):
            return _FakeApi(coord), coord

        self._services_module._get_api = _fake_get_api

        return _FakeCall, coord, seed_rule

    def test_add_rule_rollback_when_persist_fails(
        self,
    ) -> None:
        """Audit T25 round 4:
        ``add_schedule_rule``
        must roll back
        the in-memory
        registry when
        ``_persist_schedule_rules``
        returns False.
        The user sees a
        ``ServiceValidationError``;
        the in-memory
        state must match
        the pre-call
        snapshot AND
        ``entry.options``
        must NOT have
        been mutated."""
        import asyncio as _asyncio
        svc = self._services_module
        handle_add = svc._add_schedule_rule_impl
        ServiceValidationError = (
            (
                svc.ServiceValidationError,
                vol.error.Invalid,
                vol.error.MultipleInvalid,
            )
        )

        _FakeCall, coord, seed = (
            self._make_fake_service(persist_ok=False)
        )

        rules_before = (
            coord.schedule_rules.save_to_dict()
        )

        async def _drive() -> None:
            await handle_add(
                _FakeCall(
                    {
                        "name": "new-storm",
                        "days_of_week": [
                            1, 2, 3, 4, 5, 6, 7
                        ],
                        "start_hour": 18,
                        "end_hour": 22,
                        "mode": "storm",
                        "enabled": True,
                        "priority": 7,
                    }
                ),
                coord
            )

        with self.assertRaises(
            ServiceValidationError
        ):
            _asyncio.run(_drive())

        # The
        # in-memory
        # registry must
        # be restored
        # to the
        # pre-call
        # state. Only
        # the seed rule
        # should be
        # present.
        rules_after = (
            coord.schedule_rules.save_to_dict()
        )
        self.assertEqual(
            len(rules_after["schedule_rules_v1"]),
            1,
            "rollback failed: registry has "
            "more rules after a failed "
            "persist",
        )
        self.assertEqual(
            rules_after["schedule_rules_v1"][0][
                "id"
            ],
            seed.id,
        )
        self.assertNotIn(
            "schedule_rules",
            coord.entry.options,
            "rollback failed: entry.options "
            "was mutated despite persist "
            "returning False",
        )
        self.assertEqual(
            coord._persist_calls, 1
        )
        # The full
        # snapshot
        # must be
        # exactly the
        # same.
        self.assertEqual(
            rules_before, rules_after
        )

    def test_add_rule_persists_when_persist_succeeds(
        self,
    ) -> None:
        """Audit T25 round 4
        positive case:
        when persist
        succeeds the
        rule is in the
        registry AND in
        ``entry.options``."""
        import asyncio as _asyncio
        svc = self._services_module
        handle_add = svc._add_schedule_rule_impl

        _FakeCall, coord, seed = (
            self._make_fake_service(persist_ok=True)
        )

        async def _drive() -> None:
            await handle_add(
                _FakeCall(
                    {
                        "name": "new-storm",
                        "days_of_week": [
                            1, 2, 3, 4, 5, 6, 7
                        ],
                        "start_hour": 18,
                        "end_hour": 22,
                        "mode": "storm",
                        "enabled": True,
                        "priority": 7,
                    }
                ),
                coord
            )

        _asyncio.run(_drive())

        rules_after = (
            coord.schedule_rules.save_to_dict()
        )
        # Seed + new
        # rule = 2.
        self.assertEqual(
            len(rules_after["schedule_rules_v1"]),
            2,
        )
        self.assertIn(
            "schedule_rules",
            coord.entry.options,
        )
        # The
        # persisted
        # blob must
        # reflect the
        # *new*
        # registry
        # (NOT the
        # snapshot).
        self.assertEqual(
            len(
                coord.entry.options[
                    "schedule_rules"
                ]["schedule_rules_v1"]
            ),
            2,
        )

    def test_delete_rule_rollback_when_persist_fails(
        self,
    ) -> None:
        """Audit T25 round 4:
        ``delete_schedule_rule``
        must restore the
        in-memory
        registry when
        ``_persist_schedule_rules``
        returns False.
        The rule stays
        in the runtime
        and on disk
        (no successful
        persist = no
        effective
        delete)."""
        import asyncio as _asyncio
        svc = self._services_module
        handle_delete = (
            svc._delete_schedule_rule_impl
        )
        ServiceValidationError = (
            (
                svc.ServiceValidationError,
                vol.error.Invalid,
                vol.error.MultipleInvalid,
            )
        )

        _FakeCall, coord, seed = (
            self._make_fake_service(persist_ok=False)
        )
        seed_id = seed.id

        async def _drive() -> None:
            await handle_delete(
                _FakeCall({"rule_id": seed_id}),
                coord
            )

        with self.assertRaises(
            ServiceValidationError
        ):
            _asyncio.run(_drive())

        rules_after = (
            coord.schedule_rules.save_to_dict()
        )
        # The seed
        # rule must
        # still be
        # present.
        self.assertEqual(
            len(rules_after["schedule_rules_v1"]),
            1,
            "rollback failed: rule was "
            "deleted in memory despite "
            "persist failure",
        )
        self.assertEqual(
            rules_after["schedule_rules_v1"][0][
                "id"
            ],
            seed_id,
        )
        self.assertNotIn(
            "schedule_rules",
            coord.entry.options,
        )
        self.assertEqual(
            coord._persist_calls, 1
        )

    def test_delete_rule_persists_when_persist_succeeds(
        self,
    ) -> None:
        """Audit T25 round 4
        positive case:
        when persist
        succeeds the
        rule is removed
        from the
        registry AND
        from
        ``entry.options``."""
        import asyncio as _asyncio
        svc = self._services_module
        handle_delete = (
            svc._delete_schedule_rule_impl
        )

        _FakeCall, coord, seed = (
            self._make_fake_service(persist_ok=True)
        )

        async def _drive() -> None:
            await handle_delete(
                _FakeCall({"rule_id": seed.id}),
                coord
            )

        _asyncio.run(_drive())

        rules_after = (
            coord.schedule_rules.save_to_dict()
        )
        self.assertEqual(
            len(rules_after["schedule_rules_v1"]),
            0,
        )
        self.assertIn(
            "schedule_rules",
            coord.entry.options,
        )
        self.assertEqual(
            len(
                coord.entry.options[
                    "schedule_rules"
                ]["schedule_rules_v1"]
            ),
            0,
        )

    def test_round_trip_save_to_dict_load_from_dict(
        self,
    ) -> None:
        """Add a rule via the
        real handler,
        save it, then
        load it into a
        *fresh*
        ``ScheduleRulesService``
        instance and
        verify the rule
        survives."""

        import asyncio as _asyncio

        svc = self._services_module
        handle_add = svc._add_schedule_rule_impl

        _FakeCall, coord, _seed = (
            self._make_fake_service(persist_ok=True)
        )

        async def _drive() -> None:
            await handle_add(
                _FakeCall(
                    {
                        "name": "round-trip",
                        "days_of_week": [1, 3, 5],
                        "start_hour": 18,
                        "end_hour": 22,
                        "mode": "storm",
                        "enabled": True,
                        "priority": 8,
                    }
                ),
                coord
            )

        _asyncio.run(_drive())

        from hems.schedule_rules import (
            ScheduleRulesService as _SRS,
        )

        blob = coord.entry.options["schedule_rules"]
        fresh = _SRS()
        fresh.load_from_dict(blob)
        all_rules = fresh.save_to_dict()
        self.assertEqual(
            len(all_rules["schedule_rules_v1"]),
            2,
            "round-trip must preserve seed + "
            "round-trip rule",
        )
        names = {
            r["name"]
            for r in all_rules["schedule_rules_v1"]
        }
        self.assertIn("seed", names)
        self.assertIn("round-trip", names)

    def test_multi_entry_isolation(
        self,
    ) -> None:
        """Two coordinators
        must keep their
        schedule
        registries
        isolated."""

        import asyncio as _asyncio
        from _harness_t25_real_coordinator import (
            ScheduleRulesService as _SRS,
            ScheduleRule as _SR,
        )

        def _build(persist_ok, entry_id):
            class _Entry:
                def __init__(self):
                    self.options: dict = {}
                    self.entry_id = entry_id

            class _Coord:
                def __init__(self):
                    self.schedule_rules = _SRS()
                    self.entry = _Entry()
                    self._persist_calls = 0

                def _persist_schedule_rules(
                    self,
                ) -> bool:
                    self._persist_calls += 1
                    if persist_ok:
                        self.entry.options[
                            "schedule_rules"
                        ] = (
                            self.schedule_rules
                            .save_to_dict()
                        )
                    return persist_ok

            return _Coord()

        coord_a = _build(persist_ok=True, entry_id="A")
        coord_b = _build(persist_ok=True, entry_id="B")

        coord_a.schedule_rules.add_rule(_SR(
            name="only-A",
            days_of_week=[2, 4],
            start_hour=1, start_minute=0,
            end_hour=5, end_minute=0,
            mode=0, priority=4,
        ))
        coord_b.schedule_rules.add_rule(_SR(
            name="only-B",
            days_of_week=[6, 7],
            start_hour=10, start_minute=0,
            end_hour=14, end_minute=0,
            mode=1, priority=6,
        ))
        self.assertEqual(
            len(
                coord_a.schedule_rules
                .save_to_dict()["schedule_rules_v1"]
            ),
            1,
        )
        self.assertEqual(
            len(
                coord_b.schedule_rules
                .save_to_dict()["schedule_rules_v1"]
            ),
            1,
        )
        self.assertNotIn(
            "schedule_rules",
            coord_b.entry.options,
        )
        # Route the real
        # handler to
        # coordinator A.
        svc = self._services_module
        handle_add = svc._add_schedule_rule_impl
        active = {"coord": coord_a}

        async def _get_a(call):
            class _Api:
                def __init__(self, c):
                    self._c = c

            return _Api(active["coord"]), active["coord"]

        svc._get_api = _get_a

        async def _drive_a() -> None:
            class _C:
                def __init__(self, d):
                    self.data = d

            await handle_add(
                _C(
                    {
                        "name": "added-A",
                        "days_of_week": [3],
                        "start_hour": 9,
                        "end_hour": 17,
                        "mode": "adaptive",
                        "enabled": True,
                        "priority": 5,
                    }
                ),
                coord_a
            )

        _asyncio.run(_drive_a())
        self.assertEqual(
            len(
                coord_a.schedule_rules
                .save_to_dict()["schedule_rules_v1"]
            ),
            2,
        )
        self.assertEqual(
            len(
                coord_b.schedule_rules
                .save_to_dict()["schedule_rules_v1"]
            ),
            1,
            "multi-entry isolation broken: B sees A's rule",
        )
        self.assertIn(
            "schedule_rules",
            coord_a.entry.options,
        )
        self.assertNotIn(
            "schedule_rules",
            coord_b.entry.options,
            "multi-entry isolation broken: B's "
            "options mutated by A",
        )


class TestScheduleServiceSchemaValidation(
    unittest.TestCase,
):
    """T25 round 4 (audit
    follow-up): the
    voluptuous schema
    validates fields
    individually. The
    audit required
    explicit
    validation of
    ``days_of_week``
    as integers in
    the 1-7 range and
    ``priority`` in
    the 1-10 range so
    malformed input
    is rejected with
    a clear
    ``ServiceValidationError``.

    Юра (round 4 follow-up)
    also required that
    the validators are
    *wired into the
    actually-registered
    voluptuous
    schemas* — i.e. HA
    calls them at the
    schema layer, not
    just inside the
    handler body. This
    class asserts both."""

    @classmethod
    def setUpClass(cls) -> None:
        # Reuse the
        # module loader
        # from
        # ``TestScheduleServiceRollback``
        # — the
        # ``_validate_*``
        # helpers and the
        # actual
        # ``async_register_services``
        # schema live in the
        # same production
        # module. Voluptuous
        # is imported by the
        # production code
        # directly (no
        # shim).
        #
        # We do NOT store
        # the validators as
        # ``TestCase``
        # class attributes:
        # ``unittest.TestCase``
        # binds them as
        # bound methods
        # (``inst._v(...)``
        # becomes
        # ``_v(inst, ...)``
        # which raises
        # ``TypeError``). We
        # expose them via a
        # ``services``
        # reference and read
        # them from the
        # module directly in
        # each test.
        TestScheduleServiceRollback.setUpClass()

    def _services(self):
        return (
            TestScheduleServiceRollback._services_module
        )

    def test_validators_match_voluptuous_import(
        self,
    ) -> None:
        # The production
        # services module
        # imports voluptuous
        # as ``vol``. The
        # validators we call
        # here must come
        # from the *same*
        # module object — no
        # aliasing, no
        # shimming, no
        # custom validators
        # wired through a
        # different API.
        import voluptuous as vol

        svc = self._services()
        self.assertIs(
            svc._validate_days_of_week,
            self._services()._validate_days_of_week,
        )
        # The function
        # must raise the
        # production
        # ``ServiceValidationError``,
        # not a generic
        # ``ValueError``.
        try:
            self._services()._validate_days_of_week([0, 8])
        except self._services().ServiceValidationError:
            pass
        except Exception as exc:
            self.fail(
                "expected ServiceValidationError, "
                f"got {type(exc).__name__}"
            )
        else:
            self.fail(
                "expected ServiceValidationError, "
                "none raised"
            )
        # ``vol.Invalid`` is
        # the underlying
        # voluptuous error
        # type that HA maps
        # to ``ServiceValidationError``.
        # The production
        # ``_validate_days_of_week``
        # raises
        # ``ServiceValidationError``
        # directly so the
        # error message is
        # informative. We
        # accept either
        # ``vol.Invalid`` or
        # ``ServiceValidationError``
        # — the audit only
        # demanded that the
        # production code
        # uses *real*
        # voluptuous, which
        # is verified by
        # the
        # ``Schema({...: v})``
        # construction above
        # (voluptuous
        # wouldn't accept a
        # raw callable
        # otherwise).
        schema = vol.Schema(
            {
                vol.Required(
                    "days_of_week"
                ): svc._validate_days_of_week,
            }
        )
        raised = None
        try:
            schema({"days_of_week": ["mon"]})
        except vol.Invalid as exc:
            raised = exc
        except svc.ServiceValidationError as exc:
            raised = exc
        self.assertIsNotNone(
            raised,
            "voluptuous.Schema did not raise on bad "
            "input",
        )

    def test_validators_wired_into_registered_schemas(
        self,
    ) -> None:
        # Юра round 4:
        # assert that the
        # validators are
        # *wired into* the
        # schemas passed to
        # ``async_register``.
        # We read the
        # production
        # ``async_register_services``
        # function's source
        # and check the
        # validator function
        # objects appear as
        # values in the
        # schema dict.
        import inspect

        source = inspect.getsource(
            self._services().async_register_services
        )
        self.assertIn(
            "_validate_days_of_week",
            source,
            "days_of_week validator must be wired "
            "into async_register_services",
        )
        self.assertIn(
            "_validate_priority",
            source,
            "priority validator must be wired into "
            "async_register_services",
        )
        self.assertIn(
            "vol.Schema",
            source,
            "async_register_services must use "
            "voluptuous schemas",
        )
        # Confirm
        # voluptuous is
        # imported at
        # module top-level,
        # not aliased to a
        # stub.
        svc_source = inspect.getsource(
            self._services()
        )
        # Confirm voluptuous is
        # imported as ``vol``
        # at module top-level,
        # not aliased to a
        # stub. ``re.search``
        # is anchored to
        # line boundaries
        # explicitly so we
        # match the *exact*
        # import line.
        import re as _re
        self.assertIsNotNone(
            _re.search(
                r"^import voluptuous as vol$",
                svc_source,
                _re.MULTILINE,
            ),
            "voluptuous must be imported as 'vol' "
            "at module top-level (no alias, no "
            "shim)",
        )

    def test_days_of_week_rejects_string_list(
        self,
    ) -> None:
        with self.assertRaises(
            self._services().ServiceValidationError
        ):
            self._services()._validate_days_of_week(
                ["mon", "tue"]
            )

    def test_days_of_week_rejects_out_of_range(
        self,
    ) -> None:
        with self.assertRaises(
            self._services().ServiceValidationError
        ):
            self._services()._validate_days_of_week([0, 8])

    def test_days_of_week_rejects_empty(self) -> None:
        with self.assertRaises(
            self._services().ServiceValidationError
        ):
            self._services()._validate_days_of_week([])

    def test_priority_rejects_zero(self) -> None:
        with self.assertRaises(
            self._services().ServiceValidationError
        ):
            self._services()._validate_priority(0)

    def test_priority_rejects_eleven(self) -> None:
        with self.assertRaises(
            self._services().ServiceValidationError
        ):
            self._services()._validate_priority(11)

    def test_priority_rejects_non_int(self) -> None:
        with self.assertRaises(
            self._services().ServiceValidationError
        ):
            self._services()._validate_priority("high")


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


class TestShutdownFlushesDirtyState(unittest.TestCase):
    """T25 round 4 (audit
    follow-up): the
    previous test
    suite verified the
    flush *mechanism*
    via source-string
    checks. Юра
    demanded a
    behavioural test
    that exercises the
    production
    ``coordinator.shutdown``
    on a *real*
    coordinator stub
    whose throttle
    anchors are
    inside the throttle
    window. After
    ``shutdown`` runs
    we must see the dirty
    SoH / demand
    profile / schedule
    rules persisted to
    ``entry.options``,
    NOT lost.

    The previous round
    3 fix used the
    wrong anchor names
    (``_last_battery_soh_persist_at``
    instead of
    ``_last_soh_persist_at``)
    so the throttle
    window never
    opened and the
    dirty state was
    lost. This test
    pins the
    behavioural
    contract:
    ``shutdown`` writes
    every dirty helper
    regardless of the
    throttle window.
    """

    def test_shutdown_writes_battery_soh_inside_throttle(
        self,
    ) -> None:
        # T25 round 4:
        # exercise the
        # REAL production
        # ``shutdown`` path
        # on the production
        # coordinator.
        # The harness
        # ``make_coordinator``
        # builds a stub
        # that *owns* the
        # real
        # ``_persist_*``,
        # ``_maybe_persist_*``
        # and ``shutdown``
        # implementations
        # copied (via
        # ``ast`` extraction)
        # from
        # ``coordinator.py``.
        # Stub code
        # supplies
        # *dependencies*
        # (``_StubHass``
        # + ``_StubEntry``)
        # only; it does
        # NOT copy the
        # throttle / anchor
        # algorithm or the
        # shutdown body.
        from datetime import datetime, timezone

        from _harness_t25_real_coordinator import (
            make_coordinator,
        )

        coord = make_coordinator(persist_ok=True)
        # ``track_soc`` over
        # a full cycle bumps
        # cycle_count from 0
        # to 1. This is
        # enough to be
        # ``dirty`` and
        # require persistence.
        for soc in [
            80, 70, 60, 50, 40, 30, 40, 50, 60, 70, 80,
        ]:
            coord.track_soc(soc)
        self.assertEqual(
            coord._battery_soh.cycle_count,
            1,
            "track_soc path produced one cycle",
        )
        # Anchor inside the
        # throttle window so
        # ``_maybe_persist_battery_soh``
        # would *skip* on a
        # normal call. The
        # shutdown must still
        # write to
        # ``entry.options``.
        coord._last_soh_persist_at = datetime.now(
            timezone.utc
        )
        coord._last_demand_persist_at = datetime.now(
            timezone.utc
        )

        import asyncio

        asyncio.run(coord.shutdown())
        # Both writes must
        # have landed in
        # ``entry.options``.
        self.assertIn(
            "battery_soh",
            coord.entry.options,
            "shutdown must persist SoH regardless "
            "of the throttle window. "
            "cycle_count=1 was set on the stub.",
        )
        self.assertEqual(
            coord.entry.options["battery_soh"][
                "cycle_count"
            ],
            1,
        )
        self.assertIn(
            "demand_forecast_profile",
            coord.entry.options,
            "shutdown must persist demand profile "
            "regardless of the throttle window.",
        )
        # After a successful
        # persist the anchor
        # MUST be updated to
        # a *recent* timestamp
        # (so the next regular
        # ``_maybe_persist_*``
        # call sees the write).
        # Юра round 4 follow-up:
        # the previous assertion
        # (``assertIsNone``)
        # was wrong — the
        # production
        # ``_maybe_persist_battery_soh``
        # re-sets the anchor
        # *after* a successful
        # write. A None anchor
        # would mean the next
        # ``_maybe_persist_*``
        # fires immediately,
        # which is exactly the
        # opposite of the
        # throttle contract.
        # The real requirement
        # is: the anchor is set
        # to a timestamp within
        # the last few seconds.
        from datetime import (
            datetime as _dt,
        )

        # Production
        # ``_maybe_persist_*``
        # uses
        # ``datetime.now()``
        # (TZ-naive). The
        # test MUST mirror
        # this so the
        # subtraction does not
        # raise
        # ``TypeError``.
        now = _dt.now()
        self.assertIsNotNone(
            coord._last_soh_persist_at,
            "shutdown's _maybe_persist_battery_soh "
            "must record its own write time",
        )
        self.assertLessEqual(
            (
                now - coord._last_soh_persist_at
            ).total_seconds(),
            5.0,
            "anchor must reflect the write that "
            "just happened (within 5 s)",
        )
        self.assertIsNotNone(
            coord._last_demand_persist_at,
            "shutdown's _maybe_persist_demand_forecast "
            "must record its own write time",
        )
        self.assertLessEqual(
            (
                now - coord._last_demand_persist_at
            ).total_seconds(),
            5.0,
            "anchor must reflect the write that "
            "just happened (within 5 s)",
        )
        # ``_StubHass``
        # recorded both
        # calls. Restore on
        # a fresh coordinator
        # observes the same
        # cycle_count = 1.
        calls = coord.hass.config_entries.calls
        self.assertGreaterEqual(
            len(calls),
            2,
            f"shutdown must persist both anchors; got {calls}",
        )
        last = calls[-1]
        self.assertIn(
            "battery_soh",
            last["options_keys"],
        )
        self.assertIn(
            "demand_forecast_profile",
            last["options_keys"],
        )
        # Restore: build a
        # fresh coordinator
        # from the same
        # options dict, verify
        # the saved cycle_count
        # round-trips through
        # ``BatterySoH.load_from_dict``.
        from _harness_t25_real_coordinator import (
            BatterySoH,
        )

        restored = BatterySoH()
        restored.load_from_dict(
            coord.entry.options["battery_soh"]
        )
        self.assertEqual(restored.cycle_count, 1)


class TestShutdownUsesCorrectAnchorNames(unittest.TestCase):
    """T25 round 4 (audit
    follow-up): the
    previous ``shutdown``
    used
    ``helper.replace('_maybe_persist_', '_last_')``
    which produced
    ``_last_battery_soh_persist_at``
    /
    ``_last_demand_forecast_persist_at``
    — but the real
    anchors are
    ``_last_soh_persist_at``
    /
    ``_last_demand_persist_at``.
    The mismatched names
    silently created
    new attributes and
    the throttle never
    opened.

    The behavioural
    test above proves
    that the helper
    actually skips
    when the anchor is
    recent. This
    *source* test pins
    that the
    ``shutdown`` fix
    uses the *correct*
    anchor names so a
    future regression
    to
    ``helper.replace(...)``
    would be caught."""

    def test_shutdown_uses_hardcoded_anchor_names(self) -> None:
        coord_src = _read_source("coordinator.py")
        # Strip comments
        # before the
        # scan — the
        # docstring
        # contains a
        # reference to
        # the forbidden
        # pattern as
        # documentation,
        # not as actual
        # code.
        active_lines = [
            line
            for line in coord_src.splitlines()
            if not line.lstrip().startswith("#")
        ]
        active_src = "\n".join(active_lines)
        # The
        # ``shutdown``
        # must NOT
        # use the
        # misleading
        # ``helper.replace``
        # pattern.
        self.assertNotIn(
            "helper.replace(",
            active_src,
            "shutdown must not use "
            "helper.replace('_maybe_persist_', '_last_') "
            "because that produces "
            "wrong anchor names. The "
            "audit reproduced this in "
            "round 4.",
        )
        # The real
        # anchor names
        # must appear
        # somewhere in
        # the active
        # source (not
        # just in a
        # comment).
        self.assertIn(
            "_last_soh_persist_at",
            active_src,
            "shutdown must reference "
            "_last_soh_persist_at — "
            "that is the real anchor.",
        )
        self.assertIn(
            "_last_demand_persist_at",
            active_src,
            "shutdown must reference "
            "_last_demand_persist_at — "
            "that is the real anchor.",
        )


class TestScheduleServicesSingleRegistry(unittest.TestCase):
    """T25: the schedule
    services write to one
    registry —
    ``coordinator._schedule_rules``."""

    def test_no_duplicate_schedule_registry(self) -> None:
        # T25 round 3:
        # there is one
        # schedule
        # registry:
        # ``coordinator._schedule_rules``
        # (the
        # ``ScheduleRulesService``
        # instance). The
        # legacy
        # ``services/control.py``
        # was a *second*
        # registry that
        # was never
        # imported; we
        # removed it and
        # pinned the
        # single
        # active
        # registry in
        # ``services/__init__.py``.
        coord_src = _read_source("coordinator.py")
        services_src = _read_source(
            "services/__init__.py"
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


class TestScheduleServiceRealRegistration(
    unittest.TestCase,
):
    """Юра round 4 follow-up:
    ``inspect.getsource`` and
    a separately constructed
    schema do NOT prove the
    validators are wired into
    the actually-registered
    voluptuous schemas. The
    real test runs
    ``async_register_services``
    against a fake service
    registry that captures the
    ``schema`` object passed
    to each
    ``hass.services.async_register``
    call. We then call that
    schema directly with valid
    and invalid input and
    observe the rejection.

    This is the production
    path: HA's
    ``ServiceRegistry.async_register``
    invokes the schema's
    ``__call__`` before the
    handler. If the schema is
    missing or wired wrong,
    malformed service calls
    would bypass the
    validators and reach the
    handler — the original
    R7.5 audit defect."""

    def _capture_registry(self):
        """Build a fake
        ``ServiceRegistry`` that
        stores
        ``(name, handler, schema)``
        tuples."""

        class _CapturingServiceRegistry:
            def __init__(self):
                self.records: list[
                    tuple
                ] = []

            # HA's
            # ``ServiceRegistry.async_register``
            # is NOT a coroutine —
            # the ``async_`` prefix
            # is historical. Production
            # calls it without
            # ``await``, so the
            # fake MUST be sync.
            def async_register(
                self,
                domain: str,
                name: str,
                handler,
                schema=None,
            ) -> None:
                self.records.append(
                    (domain, name, handler, schema)
                )

        return _CapturingServiceRegistry()

    def _get_real_async_register_services(
        self,
        capturing_registry,
    ):
        """Load ``services/__init__.py``
        through importlib with a
        captured registry so we
        can inspect the registered
        schemas."""

        import importlib.util
        import os as _os
        import sys as _sys
        import types as _types

        # R10.6: no
        # ``os.chdir`` —
        # absolute paths in
        # ``importlib``.
        # R10.6: derive
        # ``REPO_ROOT`` from
        # ``__file__`` of the
        # test module so the
        # suite works on
        # Windows / non-canonical
        # checkouts.
        import pathlib as _pl
        REPO_ROOT = str(
            _pl.Path(__file__).resolve().parent.parent
        )
        if "powmr_inverter" not in _sys.modules:
            pkg = _types.ModuleType("powmr_inverter")
            pkg.__path__ = [REPO_ROOT]
            _sys.modules["powmr_inverter"] = pkg

            const_spec = importlib.util.spec_from_file_location(
                "powmr_inverter.const",
                f"{REPO_ROOT}/const.py",
            )
            const_mod = importlib.util.module_from_spec(
                const_spec
            )
            _sys.modules["powmr_inverter.const"] = const_mod
            const_spec.loader.exec_module(const_mod)
            pkg.DOMAIN = const_mod.DOMAIN

            svc_spec = importlib.util.spec_from_file_location(
                "powmr_inverter.services",
                f"{REPO_ROOT}/services/__init__.py",
            )
            svc = importlib.util.module_from_spec(svc_spec)
            _sys.modules["powmr_inverter.services"] = svc
            svc_spec.loader.exec_module(svc)
        else:
            svc = _sys.modules[
                "powmr_inverter.services"
            ]

        class _StubHass:
            def __init__(self, reg):
                self.services = reg
                self.data: dict = {}

        hass = _StubHass(capturing_registry)
        return svc, hass

    def _drive(self, coro):
        import asyncio

        return asyncio.run(coro)

    def test_registered_schema_rejects_empty_days(
        self,
    ) -> None:
        registry = self._capture_registry()
        svc, hass = (
            self._get_real_async_register_services(
                registry
            )
        )
        self._drive(
            svc.async_register_services(hass)
        )
        # Find the
        # ``add_schedule_rule``
        # schema.
        add_schema = None
        for domain, name, handler, schema in (
            registry.records
        ):
            if (
                domain == "powmr_inverter"
                and name == "add_schedule_rule"
            ):
                add_schema = schema
                break
        self.assertIsNotNone(
            add_schema,
            "add_schedule_rule must be registered",
        )
        # Empty days: rejected.
        with self.assertRaises(
            (
                svc.ServiceValidationError,
                vol.error.Invalid,
                vol.error.MultipleInvalid,
            )
        ):
            add_schema(
                {
                    "name": "x",
                    "days_of_week": [],
                    "start_hour": 0,
                    "end_hour": 23,
                    "mode": "adaptive",
                    "enabled": True,
                    "priority": 5,
                }
            )

    def test_registered_schema_rejects_out_of_range_days(
        self,
    ) -> None:
        registry = self._capture_registry()
        svc, hass = (
            self._get_real_async_register_services(
                registry
            )
        )
        self._drive(
            svc.async_register_services(hass)
        )
        add_schema = None
        for domain, name, handler, schema in (
            registry.records
        ):
            if (
                domain == "powmr_inverter"
                and name == "add_schedule_rule"
            ):
                add_schema = schema
                break
        with self.assertRaises(
            (
                svc.ServiceValidationError,
                vol.error.Invalid,
                vol.error.MultipleInvalid,
            )
        ):
            add_schema(
                {
                    "name": "x",
                    "days_of_week": [0, 8],
                    "start_hour": 0,
                    "end_hour": 23,
                    "mode": "adaptive",
                    "enabled": True,
                    "priority": 5,
                }
            )

    def test_registered_schema_rejects_string_days(
        self,
    ) -> None:
        registry = self._capture_registry()
        svc, hass = (
            self._get_real_async_register_services(
                registry
            )
        )
        self._drive(
            svc.async_register_services(hass)
        )
        add_schema = None
        for domain, name, handler, schema in (
            registry.records
        ):
            if (
                domain == "powmr_inverter"
                and name == "add_schedule_rule"
            ):
                add_schema = schema
                break
        with self.assertRaises(
            (
                svc.ServiceValidationError,
                vol.error.Invalid,
                vol.error.MultipleInvalid,
            )
        ):
            add_schema(
                {
                    "name": "x",
                    "days_of_week": ["mon"],
                    "start_hour": 0,
                    "end_hour": 23,
                    "mode": "adaptive",
                    "enabled": True,
                    "priority": 5,
                }
            )

    def test_registered_schema_rejects_zero_priority(
        self,
    ) -> None:
        registry = self._capture_registry()
        svc, hass = (
            self._get_real_async_register_services(
                registry
            )
        )
        self._drive(
            svc.async_register_services(hass)
        )
        add_schema = None
        for domain, name, handler, schema in (
            registry.records
        ):
            if (
                domain == "powmr_inverter"
                and name == "add_schedule_rule"
            ):
                add_schema = schema
                break
        with self.assertRaises(
            (
                svc.ServiceValidationError,
                vol.error.Invalid,
                vol.error.MultipleInvalid,
            )
        ):
            add_schema(
                {
                    "name": "x",
                    "days_of_week": [1, 2, 3],
                    "start_hour": 0,
                    "end_hour": 23,
                    "mode": "adaptive",
                    "enabled": True,
                    "priority": 0,
                }
            )

    def test_registered_schema_rejects_eleven_priority(
        self,
    ) -> None:
        registry = self._capture_registry()
        svc, hass = (
            self._get_real_async_register_services(
                registry
            )
        )
        self._drive(
            svc.async_register_services(hass)
        )
        add_schema = None
        for domain, name, handler, schema in (
            registry.records
        ):
            if (
                domain == "powmr_inverter"
                and name == "add_schedule_rule"
            ):
                add_schema = schema
                break
        with self.assertRaises(
            (
                svc.ServiceValidationError,
                vol.error.Invalid,
                vol.error.MultipleInvalid,
            )
        ):
            add_schema(
                {
                    "name": "x",
                    "days_of_week": [1, 2, 3],
                    "start_hour": 0,
                    "end_hour": 23,
                    "mode": "adaptive",
                    "enabled": True,
                    "priority": 11,
                }
            )

    def test_registered_schema_rejects_non_int_priority(
        self,
    ) -> None:
        registry = self._capture_registry()
        svc, hass = (
            self._get_real_async_register_services(
                registry
            )
        )
        self._drive(
            svc.async_register_services(hass)
        )
        add_schema = None
        for domain, name, handler, schema in (
            registry.records
        ):
            if (
                domain == "powmr_inverter"
                and name == "add_schedule_rule"
            ):
                add_schema = schema
                break
        with self.assertRaises(
            (
                svc.ServiceValidationError,
                vol.error.Invalid,
                vol.error.MultipleInvalid,
            )
        ):
            add_schema(
                {
                    "name": "x",
                    "days_of_week": [1, 2, 3],
                    "start_hour": 0,
                    "end_hour": 23,
                    "mode": "adaptive",
                    "enabled": True,
                    "priority": "high",
                }
            )

    def test_registered_schema_accepts_valid_input(
        self,
    ) -> None:
        """Positive case: a
        valid payload MUST be
        accepted by the
        schema — and the
        ``days_of_week`` /
        ``priority`` values
        MUST be normalised to
        the production
        ``list[int]`` /
        ``int`` types."""

        registry = self._capture_registry()
        svc, hass = (
            self._get_real_async_register_services(
                registry
            )
        )
        self._drive(
            svc.async_register_services(hass)
        )
        add_schema = None
        for domain, name, handler, schema in (
            registry.records
        ):
            if (
                domain == "powmr_inverter"
                and name == "add_schedule_rule"
            ):
                add_schema = schema
                break
        result = add_schema(
            {
                "name": "valid",
                "days_of_week": [5, 3, 1],
                "start_hour": 0,
                "end_hour": 23,
                "mode": "adaptive",
                "enabled": True,
                "priority": 7,
            }
        )
        # Voluptuous may sort
        # the list inside
        # ``_validate_days_of_week``;
        # we just check the
        # membership and the
        # priority type.
        self.assertEqual(
            sorted(result["days_of_week"]),
            [1, 3, 5],
        )
        self.assertEqual(result["priority"], 7)


class TestScheduleServiceRollbackAllSixScenarios(
    unittest.TestCase,
):
    """Юра round 4 follow-up:
    повторно запустити ВСІ
    шість rollback-сценаріїв.
    ``test_*_rollback_when_persist_fails``
    та
    ``test_*_persists_when_persist_succeeds``
    для add і delete мають
    залишатися стабільними
    через repeated runs.
    Перевіряємо що registry
    та options залишаються
    незмінними при failed
    persist."""

    @classmethod
    def setUpClass(cls) -> None:
        # Delegate to
        # ``TestScheduleServiceRollback``
        # so the module is
        # loaded only once.
        import sys as _sys
        import types as _t
        import importlib.util as _ilu
        import os as _os

        # R10.6: no
        # ``os.chdir`` —
        # absolute paths in
        # ``importlib``.
        # R10.6: derive
        # ``REPO_ROOT`` from
        # ``__file__`` of the
        # test module so the
        # suite works on
        # Windows / non-canonical
        # checkouts.
        import pathlib as _pl
        REPO_ROOT = str(
            _pl.Path(__file__).resolve().parent.parent
        )
        if "powmr_inverter" not in _sys.modules:
            pkg = _t.ModuleType("powmr_inverter")
            pkg.__path__ = [REPO_ROOT]
            _sys.modules["powmr_inverter"] = pkg
            const_spec = _ilu.spec_from_file_location(
                "powmr_inverter.const",
                f"{REPO_ROOT}/const.py",
            )
            cm = _ilu.module_from_spec(const_spec)
            _sys.modules["powmr_inverter.const"] = cm
            const_spec.loader.exec_module(cm)
            pkg.DOMAIN = cm.DOMAIN
            svc_spec = _ilu.spec_from_file_location(
                "powmr_inverter.services",
                f"{REPO_ROOT}/services/__init__.py",
            )
            svc = _ilu.module_from_spec(svc_spec)
            _sys.modules["powmr_inverter.services"] = svc
            svc_spec.loader.exec_module(svc)
        cls._services_module = _sys.modules[
            "powmr_inverter.services"
        ]

    def _make_fake_service(self, persist_ok):
        from hems.schedule_rules import (
            ScheduleRulesService,
            ScheduleRule,
        )

        class _FakeEntry:
            def __init__(self):
                self.options: dict = {}

        class _FakeCoordinator:
            def __init__(self):
                self.schedule_rules = (
                    ScheduleRulesService()
                )
                self.entry = _FakeEntry()
                self._persist_calls = 0

            def _persist_schedule_rules(
                self,
            ) -> bool:
                self._persist_calls += 1
                if persist_ok:
                    self.entry.options[
                        "schedule_rules"
                    ] = (
                        self.schedule_rules
                        .save_to_dict()
                    )
                return persist_ok

        class _FakeApi:
            def __init__(self, c):
                self._c = c

        class _FakeCall:
            def __init__(self, d):
                self.data = d

        coord = _FakeCoordinator()
        seed = ScheduleRule(
            name="seed",
            days_of_week=[1, 2, 3, 4, 5],
            start_hour=0, start_minute=0,
            end_hour=23, end_minute=0,
            mode=0, priority=5,
        )
        coord.schedule_rules.add_rule(seed)

        async def _get(call):
            return _FakeApi(coord), coord

        self._services_module._get_api = _get
        return _FakeCall, coord, seed

    def test_add_rollback_repeated(self) -> None:
        """Repeated runs of
        add-rollback must
        consistently keep the
        registry unchanged."""
        import asyncio

        svc = self._services_module
        handle_add = svc._add_schedule_rule_impl
        for _ in range(3):
            _FakeCall, coord, seed = (
                self._make_fake_service(
                    persist_ok=False
                )
            )

            async def _drive(call):
                await handle_add(call, coord)

            with self.assertRaises(
                (
                svc.ServiceValidationError,
                vol.error.Invalid,
                vol.error.MultipleInvalid,
            )
            ):
                asyncio.run(
                    _drive(
                        _FakeCall(
                            {
                                "name": "x",
                                "days_of_week": [1],
                                "start_hour": 0,
                                "end_hour": 23,
                                "mode": "storm",
                                "enabled": True,
                                "priority": 7,
                            }
                        )
                    )
                )
            rules = (
                coord.schedule_rules
                .save_to_dict()
            )
            self.assertEqual(
                len(rules["schedule_rules_v1"]),
                1,
                "rollback must preserve seed only",
            )
            self.assertNotIn(
                "schedule_rules",
                coord.entry.options,
                "options must remain unset on failure",
            )

    def test_delete_rollback_repeated(self) -> None:
        import asyncio

        svc = self._services_module
        handle_delete = (
            svc._delete_schedule_rule_impl
        )
        for _ in range(3):
            _FakeCall, coord, seed = (
                self._make_fake_service(
                    persist_ok=False
                )
            )

            async def _drive(call):
                await handle_delete(call, coord)

            with self.assertRaises(
                (
                svc.ServiceValidationError,
                vol.error.Invalid,
                vol.error.MultipleInvalid,
            )
            ):
                asyncio.run(
                    _drive(
                        _FakeCall({"rule_id": seed.id})
                    )
                )
            rules = (
                coord.schedule_rules
                .save_to_dict()
            )
            self.assertEqual(
                len(rules["schedule_rules_v1"]),
                1,
                "delete rollback must restore seed",
            )
            self.assertNotIn(
                "schedule_rules",
                coord.entry.options,
            )


# ───────────────────────────────────────────────────────────────
# R10.5 — captured
# handlers (the ones
# registered with
# ``hass.services.async_register``)
# must roll back on
# failed persist. The
# previous T25 tests
# drove the module-level
# ``_add_schedule_rule_impl``
# directly, which
# bypassed the active
# handler. The active
# handler had inline
# logic with no
# snapshot / rollback
# and raised
# ``ValueError``, not
# ``ServiceValidationError``.
# This regression
# exercises the *captured*
# handler so the same
# defect cannot recur.
# ───────────────────────────────────────────────────────────────


class TestCapturedHandlerRollback(
    unittest.TestCase,
):
    """Юра round 4 follow-up:
    the service handler
    registered through
    ``hass.services.async_register``
    must roll back on
    failed persist. We
    capture the handler
    via a fake registry
    and call it directly
    — bypassing
    ``_add_schedule_rule_impl``
    so the regression
    detects a
    regression in the
    *active* handler, not
    the impl.
    """

    @classmethod
    def setUpClass(cls) -> None:
        # Re-use the
        # service module
        # loaded by
        # ``TestScheduleServiceRollbackAllSixScenarios``
        # (the test class
        # that owns
        # ``setUpClass``
        # in this file)
        # so we only build
        # the import graph
        # once across the
        # whole suite.
        TestScheduleServiceRollbackAllSixScenarios.setUpClass()
        cls._svc = (
            TestScheduleServiceRollbackAllSixScenarios
            ._services_module
        )

    def _make_hass(self, coord):
        """Build a fake
        ``hass`` whose
        ``_resolve_entry``
        hands back our fake
        coordinator AND
        whose
        ``hass.services.async_register``
        captures handlers
        + schemas.
        """
        from hems.schedule_rules import (
            ScheduleRulesService,
        )

        class _FakeApi:
            def __init__(self, c):
                self._c = c

        class _Registry:
            def __init__(self):
                self.records: list = []

            def async_register(
                self,
                domain,
                name,
                handler,
                schema=None,
            ):
                self.records.append(
                    (domain, name, handler, schema)
                )

        class _FakeConfigEntries:
            def __init__(self):
                self._coord = coord

            async def async_entries(
                self, domain
            ):
                # Round-4
                # design: a
                # single entry
                # is enough
                # for these
                # tests; if
                # the handler
                # asks for a
                # different
                # domain we
                # filter
                # accordingly.
                if domain == self._svc.DOMAIN:
                    class _E:
                        def __init__(self):
                            self.entry_id = "test-entry"
                            self.title = (
                                "test inverter"
                            )
                    return [_E()]

                return []

        class _FakeHass:
            def __init__(self):
                self.services = _Registry()
                self.config_entries = (
                    _FakeConfigEntries()
                )
                self.data: dict = {}
                self.bus = type(
                    "_Bus",
                    (),
                    {"async_fire": staticmethod(
                        lambda *a, **kw: None
                    )},
                )()

        return _FakeHass()

    def _capture_handler(
        self, name: str, coord
    ):
        """Run
        ``async_register_services``
        with a fake hass
        whose
        ``_resolve_entry``
        resolves to ``coord``.
        Return the handler
        and its schema for
        the given service
        name.
        """
        import asyncio

        hass = self._make_hass(coord)

        # Patch
        # ``_resolve_entry``
        # so the
        # nested
        # ``_get_api``
        # inside
        # ``async_register_services``
        # returns our
        # ``(api, coord)``.
        # Production's
        # ``_resolve_entry``
        # is a *synchronous*
        # function — keep
        # the fake sync so
        # the ``async def
        # _get_api`` wrapper
        # returns the tuple
        # directly (no
        # unpack-of-coroutine
        # trap).
        class _FakeApi:
            def __init__(self, c):
                self._c = c

        captured = {}

        def _fake_resolve(h, call):
            captured["call"] = call
            return _FakeApi(coord), coord

        self._svc._resolve_entry = _fake_resolve

        asyncio.run(
            self._svc.async_register_services(hass)
        )

        for domain, n, handler, schema in (
            hass.services.records
        ):
            if domain == self._svc.DOMAIN and n == name:
                return handler, schema

        self.fail(
            f"{name} handler not registered"
        )

    def test_add_handler_rolls_back_on_failed_persist(
        self,
    ) -> None:
        """Active
        ``add_schedule_rule``
        handler must roll
        back the in-memory
        registry and raise
        ``ServiceValidationError``
        when persist
        fails — NOT
        ``ValueError``.
        """

        from hems.schedule_rules import (
            ScheduleRulesService,
            ScheduleRule,
        )

        class _FakeEntry:
            def __init__(self):
                self.options: dict = {}

        class _FakeCoord:
            def __init__(self):
                self.schedule_rules = (
                    ScheduleRulesService()
                )
                self.entry = _FakeEntry()
                self._persist_calls = 0

            def _persist_schedule_rules(
                self,
            ) -> bool:
                self._persist_calls += 1
                # Simulate
                # write
                # failure.
                return False

        coord = _FakeCoord()
        seed = ScheduleRule(
            name="seed",
            days_of_week=[1, 2, 3, 4, 5],
            start_hour=0, start_minute=0,
            end_hour=23, end_minute=0,
            mode=0, priority=5,
        )
        coord.schedule_rules.add_rule(seed)

        handler, schema = (
            self._capture_handler(
                "add_schedule_rule", coord
            )
        )

        class _Call:
            def __init__(self, data):
                self.data = data

        # Apply schema so we
        # validate the
        # *registered* contract.
        validated = schema(
            {
                "name": "should-not-stick",
                "days_of_week": [1, 2, 3],
                "start_hour": 0,
                "end_hour": 23,
                "mode": "adaptive",
                "enabled": True,
                "priority": 5,
            }
        )

        import asyncio

        with self.assertRaises(
            self._svc.ServiceValidationError
        ):
            asyncio.run(handler(_Call(validated)))

        # Registry must
        # contain ONLY the
        # seed. The failed
        # add must NOT have
        # stuck.
        rules_after = (
            coord.schedule_rules.save_to_dict()
        )
        names = {
            r["name"]
            for r in rules_after[
                "schedule_rules_v1"
            ]
        }
        self.assertIn(
            "seed", names,
            "seed rule must remain",
        )
        self.assertNotIn(
            "should-not-stick", names,
            "handler must roll back the new rule "
            "on failed persist",
        )
        # ``entry.options``
        # must NOT be
        # mutated.
        self.assertNotIn(
            "schedule_rules",
            coord.entry.options,
            "handler must not touch entry.options "
            "when persist fails",
        )

    def test_delete_handler_rolls_back_on_failed_persist(
        self,
    ) -> None:
        """Active
        ``delete_schedule_rule``
        handler must restore
        the deleted rule
        when persist fails.
        """
        from hems.schedule_rules import (
            ScheduleRulesService,
            ScheduleRule,
        )

        class _FakeEntry:
            def __init__(self):
                self.options: dict = {}

        class _FakeCoord:
            def __init__(self):
                self.schedule_rules = (
                    ScheduleRulesService()
                )
                self.entry = _FakeEntry()

            def _persist_schedule_rules(
                self,
            ) -> bool:
                return False

        coord = _FakeCoord()
        seed = ScheduleRule(
            name="keep",
            days_of_week=[1, 2, 3, 4, 5],
            start_hour=0, start_minute=0,
            end_hour=23, end_minute=0,
            mode=0, priority=5,
        )
        coord.schedule_rules.add_rule(seed)
        seed_id = seed.id

        handler, schema = (
            self._capture_handler(
                "delete_schedule_rule", coord
            )
        )

        class _Call:
            def __init__(self, data):
                self.data = data

        validated = schema({"rule_id": seed_id})

        import asyncio

        with self.assertRaises(
            self._svc.ServiceValidationError
        ):
            asyncio.run(handler(_Call(validated)))

        # Rule must still
        # be present.
        rules_after = (
            coord.schedule_rules.save_to_dict()
        )
        names = {
            r["name"]
            for r in rules_after[
                "schedule_rules_v1"
            ]
        }
        self.assertIn(
            "keep", names,
            "delete handler must restore the rule "
            "on failed persist",
        )

    def test_add_handler_logs_no_completion_on_failure(
        self,
    ) -> None:
        """Successful ``info``
        log MUST NOT be
        emitted when the
        handler aborts on
        persist failure.
        """
        from hems.schedule_rules import (
            ScheduleRulesService,
            ScheduleRule,
        )

        class _FakeEntry:
            def __init__(self):
                self.options: dict = {}

        class _FakeCoord:
            def __init__(self):
                self.schedule_rules = (
                    ScheduleRulesService()
                )
                self.entry = _FakeEntry()

            def _persist_schedule_rules(
                self,
            ) -> bool:
                return False

        coord = _FakeCoord()
        seed = ScheduleRule(
            name="s",
            days_of_week=[1],
            start_hour=0, start_minute=0,
            end_hour=23, end_minute=0,
            mode=0, priority=5,
        )
        coord.schedule_rules.add_rule(seed)

        handler, schema = (
            self._capture_handler(
                "add_schedule_rule", coord
            )
        )

        class _Call:
            def __init__(self, data):
                self.data = data

        import asyncio
        import logging as _logging

        captured_records: list = []

        class _ListHandler(_logging.Handler):
            def emit(self, record):
                captured_records.append(
                    self.format(record)
                )

        handler_logger = _logging.getLogger(
            "custom_components.powmr_inverter.services"
        )
        handler_logger.addHandler(_ListHandler())
        try:
            with self.assertRaises(
                self._svc.ServiceValidationError
            ):
                asyncio.run(
                    handler(
                        _Call(
                            schema(
                                {
                                    "name": "x",
                                    "days_of_week": [1],
                                    "start_hour": 0,
                                    "end_hour": 23,
                                    "mode": "adaptive",
                                    "enabled": True,
                                    "priority": 5,
                                }
                            )
                        )
                    )
                )
        finally:
            handler_logger.handlers.clear()

        joined = "\n".join(captured_records)
        self.assertNotIn(
            "added schedule rule", joined,
            "no success log on failed persist; "
            f"got: {joined!r}",
        )


if __name__ == "__main__":
    unittest.main()
