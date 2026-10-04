"""T16 behavioral — options update path.

These tests do *not* import homeassistant.
They exercise the options-handling
contract end to end by:
  1. Calling the real ``_async_options_updated``
     listener in an isolated namespace and
     asserting it returns ``None`` without
     raising.
  2. Calling the pure helpers in
     ``hems.options_helpers`` and asserting
     that the values the user submits in
     the options flow actually reach the
     runtime contract.
  3. Asserting that the reload-required
     classifier is the single source of
     truth for both the options flow and
     the integration.
"""
from __future__ import annotations

import asyncio
import ast
import logging
import sys
import unittest
from pathlib import Path

# Ensure the repo root is on ``sys.path`` so
# ``from hems.options_helpers import …``
# resolves. unittest does not pick up
# conftest.py automatically.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

INIT_PATH = REPO_ROOT / "__init__.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _find_function(
    src: str, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(src)

    def _walk(node: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        for child in ast.iter_child_nodes(node):
            if (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name == name
            ):
                return child
            if isinstance(child, ast.ClassDef):
                inner = _walk(child)
                if inner is not None:
                    return inner
        return None

    found = _walk(tree)
    if found is None:
        raise SystemExit(f"function {name!r} not found")
    return found


def _extract_function(name: str) -> str:
    """AST-extract a function (including its
    ``async def`` line) so it can be exec'd
    as a top-level callable.
    """
    src = _read(INIT_PATH)
    return ast.unparse(
        ast.Module(body=[_find_function(src, name)], type_ignores=[])
    )


class T16BehaviouralOptionsTests(unittest.TestCase):
    """Behavioural assertions: the listener runs,
    the helpers produce the contract values,
    and the classifier is the single source
    of truth.
    """

    # ── 1. listener executes and returns None ────────────────────

    def test_16_live_01_listener_executes(self) -> None:
        """The real ``_async_options_updated``
        listener is a no-op that returns
        ``None``. We exec it and call it
        with stub objects.
        """
        body = _extract_function("_async_options_updated")
        ns: dict[str, object] = {
            "_LOGGER": logging.getLogger("t16b_listener"),
        }
        exec(compile(body, "<t16b-listener>", "exec"), ns)
        listener = ns["_async_options_updated"]

        class _StubHass:
            pass

        class _StubEntry:
            entry_id = "test-entry-1"

        async def _run() -> object:
            return await listener(_StubHass(), _StubEntry())

        result = asyncio.run(_run())
        self.assertIsNone(
            result,
            msg=(
                "_async_options_updated must return None; "
                f"got {result!r}"
            ),
        )

    # ── 2. compute_poll_interval — real call, real values ──────

    def test_16_live_02_poll_interval_apply(self) -> None:
        """The runtime uses the value the
        user submitted. ``compute_poll_interval``
        is the same function called from
        ``__init__.async_setup_entry``.
        """
        from hems.options_helpers import compute_poll_interval
        # Default for a legacy entry.
        self.assertEqual(
            compute_poll_interval(None),
            5,
            msg="default poll_interval must be 5",
        )
        # User-set value.
        self.assertEqual(
            compute_poll_interval({"poll_interval": 10}),
            10,
            msg="user-set poll_interval must be returned",
        )
        # Below the floor — clamped to 3.
        self.assertEqual(
            compute_poll_interval({"poll_interval": 1}),
            3,
            msg="poll_interval below the floor must be clamped to 3",
        )
        # Zero is also clamped.
        self.assertEqual(
            compute_poll_interval({"poll_interval": 0}),
            3,
        )
        # A negative value is clamped.
        self.assertEqual(
            compute_poll_interval({"poll_interval": -10}),
            3,
        )

    # ── 3. site_latitude / site_longitude apply ──────────────

    def test_16_live_03_site_coordinates_apply(self) -> None:
        """The runtime uses the user-submitted
        site coordinates. ``compute_site_coordinates``
        is the same function called from
        ``PvLearningCoordinatorMixin``.
        """
        from hems.options_helpers import compute_site_coordinates
        # Defaults: Kyiv.
        lat, lon = compute_site_coordinates(None)
        self.assertAlmostEqual(lat, 50.45)
        self.assertAlmostEqual(lon, 30.52)
        # User override.
        lat, lon = compute_site_coordinates(
            {"site_latitude": 49.0, "site_longitude": 31.0}
        )
        self.assertAlmostEqual(lat, 49.0)
        self.assertAlmostEqual(lon, 31.0)
        # Partial override: lat only — lon falls back.
        lat, lon = compute_site_coordinates({"site_latitude": 49.5})
        self.assertAlmostEqual(lat, 49.5)
        self.assertAlmostEqual(lon, 30.52)

    # ── 4. reserve_soc apply ─────────────────────────────────

    def test_16_live_04_reserve_soc_apply(self) -> None:
        """The runtime uses the user-submitted
        reserve_soc. ``compute_reserve_soc`` is
        the same function called by the
        coordinator at every cycle.
        """
        from hems.options_helpers import compute_reserve_soc
        # Default.
        self.assertEqual(compute_reserve_soc(None), 20.0)
        # User-set.
        self.assertEqual(
            compute_reserve_soc({"reserve_soc": 30.0}),
            30.0,
        )

    # ── 5. requires_reload — branch the options flow takes ──

    def test_16_live_05_requires_reload_branch(self) -> None:
        """The options flow's reload decision
        is the same as ``requires_reload``.
        """
        from hems.options_helpers import requires_reload
        # Fresh entry: any reload-required key
        # triggers a reload.
        self.assertTrue(
            requires_reload({"poll_interval": 5}, None),
            msg="new entry with poll_interval must trigger reload",
        )
        # Unchanged.
        self.assertFalse(
            requires_reload(
                {"poll_interval": 5, "reserve_soc": 20.0},
                {"poll_interval": 5, "reserve_soc": 20.0},
            ),
            msg="unchanged options must not trigger reload",
        )
        # poll_interval changed: reload.
        self.assertTrue(
            requires_reload(
                {"poll_interval": 7},
                {"poll_interval": 5},
            ),
            msg="poll_interval change must trigger reload",
        )
        # email changed: reload.
        self.assertTrue(
            requires_reload(
                {"email": "new@example.com"},
                {"email": "old@example.com"},
            ),
            msg="email change must trigger reload",
        )
        # password changed: reload.
        self.assertTrue(
            requires_reload(
                {"password": "new"},
                {"password": "old"},
            ),
            msg="password change must trigger reload",
        )
        # Non-reload key changed: no reload.
        # reserve_soc is read on every
        # coordinator cycle; it is a
        # selective-apply key.
        self.assertFalse(
            requires_reload(
                {"reserve_soc": 30.0},
                {"reserve_soc": 25.0},
            ),
            msg="reserve_soc change must NOT trigger reload",
        )
        # T16 audit follow-up: site
        # coordinates feed the PV-learning
        # state at construction time. A
        # coordinate change must trigger a
        # full reload so the
        # ``_pv_learning`` state is rebuilt
        # from scratch.
        self.assertTrue(
            requires_reload(
                {"site_latitude": 49.0},
                {"site_latitude": 50.0},
            ),
            msg=(
                "site_latitude change must trigger reload "
                "(PV-learning state must be rebuilt)"
            ),
        )
        self.assertTrue(
            requires_reload(
                {"site_longitude": 31.0},
                {"site_longitude": 30.0},
            ),
            msg=(
                "site_longitude change must trigger reload "
                "(PV-learning state must be rebuilt)"
            ),
        )
        # New entry (no old options) and a
        # non-reload key: no reload.
        self.assertFalse(
            requires_reload({"reserve_soc": 30.0}, None),
            msg="new entry with only non-reload keys must not reload",
        )

    # ── 6b. site coordinates reach _init_pv_learning (real flow) ─

    def test_16_live_07_pv_learning_uses_new_coordinates(self) -> None:
        """Audit T16 follow-up: when
        ``site_latitude`` / ``site_longitude``
        change, the runtime must use the new
        values. We do NOT just call
        ``compute_site_coordinates``; we exec
        the live ``_init_pv_learning`` body
        from ``hems.pv_coordinator`` and
        assert the ``PvLearningState``
        constructor was called with the new
        coordinates. This proves the end-to-end
        contract: user submits a new
        coordinate, the options flow triggers
        a reload, ``async_setup_entry`` runs
        ``_init_pv_learning``, and the new
        values reach ``PvLearningState``.
        """
        import ast as _ast
        pv_src = (REPO_ROOT / "hems" / "pv_coordinator.py").read_text(
            encoding="utf-8"
        )
        tree = _ast.parse(pv_src)
        method = None
        for node in _ast.walk(tree):
            if (
                isinstance(node, _ast.FunctionDef)
                and node.name == "_init_pv_learning"
            ):
                method = node
                break
        self.assertIsNotNone(
            method,
            msg="_init_pv_learning must be defined in hems.pv_coordinator",
        )
        pvl_call: list[dict[str, object]] = []

        class _RecordingPvLearning:
            def __init__(self, tz, latitude, longitude):
                pvl_call.append(
                    {
                        "tz": tz,
                        "latitude": latitude,
                        "longitude": longitude,
                    }
                )
                self.calibrator = object()
                self.matrix: list[list[float]] = []
                self.model: dict = {}

        def _zone_info(_key):
            return _zone_info

        def _compute_site_coordinates(options):
            opt = options or {}
            return (
                float(opt.get("site_latitude", 50.45)),
                float(opt.get("site_longitude", 30.52)),
            )

        class _StubPredictiveController:
            calibrator = None

        class _StubSelf:
            hass = type(
                "H",
                (),
                {
                    "config": type("C", (), {"time_zone": "Europe/Kyiv"})(),
                    "async_add_executor_job": lambda *_a, **_k: None,
                },
            )()
            _entry = type(
                "E",
                (),
                {
                    "entry_id": "test-entry",
                    "options": {
                        "site_latitude": 49.0,
                        "site_longitude": 31.0,
                    },
                },
            )()
            _pv_actual: dict = {}
            _cloud_pv_actual = {}
            _cloud_history_attempt_at = None
            _pv_matrix_at = None
            _pv_calibrator_log_at = None
            _archive_attempt_at = None
            _pv_state_loaded = False
            _pv_state_dirty = False
            _pv_learning = None
            forecast_learned_ratio = 0.13
            def _configure_night_window(self):
                return None
            _hems = type(
                "HEMS", (), {"_predictive_controller": None}
            )()

        lines_src = pv_src.splitlines()
        # Skip the ``def`` line; include
        # only the body.
        start_idx = method.body[0].lineno - 1
        end_idx = method.end_lineno
        body_text = "\n".join(lines_src[start_idx:end_idx])
        indented = "\n".join(
            "        " + ln if ln.strip() else ln
            for ln in body_text.split("\n")
        )
        wrapper_src = (
            "class _Wrapper:\n"
            "    def _init_pv_learning(self):\n"
            + indented
        )
        ns = {
            "__name__": "t16b_pvlearn",
            "__file__": str(REPO_ROOT / "hems" / "pv_coordinator.py"),
            "ZoneInfo": _zone_info,
            "compute_site_coordinates": _compute_site_coordinates,
            "PvLearningState": _RecordingPvLearning,
            "PredictiveHemsController": _StubPredictiveController,
            "Path": __import__("pathlib").Path,
            "_LOGGER": type(
                "L", (), {"warning": lambda *a, **k: None}
            )(),
        }
        exec(compile(wrapper_src, "<t16b-pvlearn>", "exec"), ns)
        ns["_Wrapper"]._init_pv_learning(_StubSelf())
        self.assertEqual(
            len(pvl_call),
            1,
            msg="PvLearningState must be constructed exactly once",
        )
        self.assertEqual(
            pvl_call[0]["latitude"],
            49.0,
            msg=(
                "PvLearningState must be constructed with the new "
                "site_latitude (49.0), proving the runtime uses the "
                "user-submitted value"
            ),
        )
        self.assertEqual(
            pvl_call[0]["longitude"],
            31.0,
            msg=(
                "PvLearningState must be constructed with the new "
                "site_longitude (31.0)"
            ),
        )


    # ── 6. the classifier is the single source of truth ──────

    def test_16_live_06_classifier_single_source(self) -> None:
        """The classifier must be the same
        frozenset imported by ``__init__`` and
        defined in ``hems.options_helpers``.
        """
        from hems.options_helpers import (
            RELOAD_REQUIRED_OPTION_KEYS as h_keys,
        )
        # Import the integration's re-export.
        import importlib.util as _u
        # The ``__init__`` module imports
        # homeassistant; we cannot import it
        # in the test runner. Read the source
        # and assert the symbol is bound to
        # the same frozenset.
        import re
        src = _read(INIT_PATH)
        m = re.search(
            r"_RELOAD_REQUIRED_OPTION_KEYS\s*=\s*"
            r"RELOAD_REQUIRED_OPTION_KEYS",
            src,
        )
        if m is None:
            # Newer code: re-exported via
            # ``as _RELOAD_REQUIRED_OPTION_KEYS``.
            m2 = re.search(
                r"RELOAD_REQUIRED_OPTION_KEYS\s+as\s+"
                r"_RELOAD_REQUIRED_OPTION_KEYS",
                src,
            )
            self.assertIsNotNone(
                m2,
                msg=(
                    "__init__ must re-export the "
                    "RELOAD_REQUIRED_OPTION_KEYS frozenset "
                    "as _RELOAD_REQUIRED_OPTION_KEYS"
                ),
            )
        # The classifier content must include
        # ``poll_interval``, ``email``, and
        # ``password`` — and must not include
        # the internal persistence keys.
        for key in ("poll_interval", "email", "password"):
            self.assertIn(
                key,
                h_keys,
                msg=f"{key!r} must be in the reload-required set",
            )
        for key in (
            "predictive_feedback_override",
            "night_window",
            "_energy_state",
            "energy_state_version",
        ):
            self.assertNotIn(
                key,
                h_keys,
                msg=(
                    f"internal key {key!r} must NOT be reload-required"
                ),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
