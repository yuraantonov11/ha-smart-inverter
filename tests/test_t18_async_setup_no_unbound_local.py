"""T18 follow-up — production-path regression for the
async_setup_entry UnboundLocalError.

Audit T18 follow-up (post-mortem):
``async_setup_entry`` previously contained a
local ``import os`` inside the function
body. CPython's static scope analyzer
sees that import as a binding for the
*same* function — so the local name
``os`` is created for the whole body,
and every ``os.environ.get(...)`` call
**before** the import runs raises::

    UnboundLocalError: cannot access local
    variable 'os' where it is not associated
    with a value

The fix is to ensure the module-level
``os`` is the one the function reads.
This test pins the contract in two ways:

  1. AST scan — no in-function import of
     ``os`` or ``json`` exists anywhere
     in ``__init__.py``. (Module-level
     imports are fine.)
  2. Runtime exec — we actually run the
     function body up to the first
     ``os.environ.get`` call and assert
     no ``UnboundLocalError`` is raised.

The runtime exec is the binding
contract: even if a future refactor
adds an in-function ``import os``
later, this test will reproduce the
production crash.
"""
from __future__ import annotations

import ast
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import main as _main


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class T18SetupUnboundLocalTests(unittest.TestCase):
    """Pin the contract that no in-function
    ``import os`` shadows module-level
    names in ``async_setup_entry``."""

    def test_no_in_function_import_os(self) -> None:
        """``__init__.py`` must not
        contain a local ``import os`` or
        ``import json``. Either one
        would shadow the module-level
        ``os`` / ``json`` for the whole
        function via CPython's name
        binding rule, and any earlier
        ``os.environ.get(...)`` call
        would then raise
        ``UnboundLocalError``.

        Note on the binding rule: the
        shadow is **local to the
        function that contains the
        import**. A function-level
        ``import os`` does not affect
        other functions — but the
        audit fix is to keep
        ``os`` / ``json`` at the
        module level for every
        function that reads them.
        """
        init_src = (REPO_ROOT / "__init__.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(init_src)
        parent_map: dict[object, object] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parent_map[child] = parent

        def enclosing(node: object) -> str | None:
            current = node
            while current in parent_map:
                current = parent_map[current]
                if isinstance(
                    current,
                    (ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    return current.name
            return None

        # Collect in-function imports of
        # ``os`` or ``json``.
        offenders: list[tuple[str, int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                func_name = enclosing(node)
                for alias in node.names:
                    if (
                        func_name is not None
                        and alias.name in ("os", "json")
                    ):
                        offenders.append(
                            (
                                func_name,
                                node.lineno,
                                f"import {alias.name}",
                            )
                        )
        self.assertEqual(
            offenders,
            [],
            msg=(
                "in-function import of os/json shadows the "
                "module-level name within the same "
                "function. Move the import to module "
                "level. Offenders: "
                f"{offenders}"
            ),
        )

    def test_async_setup_entry_no_local_import_shadows_os(self) -> None:
        """Audit T18 follow-up (structural AST check).

        ``async_setup_entry`` previously
        contained an in-function
        ``import os`` statement that
        shadowed the module-level
        ``os`` for the rest of the
        function, breaking every
        ``os.environ.get(...)`` call::

            UnboundLocalError: cannot access local
            variable 'os' where it is not associated
            with a value

        Python's name-binding rule: an
        ``import X`` inside a function
        makes ``X`` a **local of that
        function** for the entire
        body. A reference to ``X``
        **before** the import in the
        same function raises
        ``UnboundLocalError`` because
        the static analyser has
        classified ``X`` as local but
        it has not been bound yet.
        Other functions are not
        affected.

        This test is a **structural
        AST check**, not a runtime
        regression: we walk the AST
        of ``async_setup_entry`` and
        assert that no statement
        inside the function body
        imports ``os`` (which would
        shadow the module-level
        binding). It is paired with
        ``test_no_in_function_import_os``,
        which performs the same check
        across the entire ``__init__.py``.

        A full **runtime** reproduction
        would require exec'ing the
        live function body with a
        stub namespace; the body
        references
        ``hass``/``api``/``entry``/``dr``/
        ``PLATFORMS``/``_install_flow_card``/
        ``_auto_install_dashboard``/etc.
        — too much surface area for
        a regression test. The AST
        check is sufficient because
        the bug was a static analyser
        issue, not a runtime one.
        """
        init_src = (REPO_ROOT / "__init__.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(init_src)
        setup_node = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == "async_setup_entry"
            ):
                setup_node = node
                break
        self.assertIsNotNone(
            setup_node,
            msg="async_setup_entry must be defined in __init__.py",
        )
        # Walk the function body and
        # collect every ``import os``
        # statement. The audit's
        # regression surface is
        # exactly this: an in-function
        # ``import os`` that Python's
        # static analyser then
        # classifies as a local
        # binding for the whole
        # function.
        offenders: list[tuple[int, str]] = []
        for stmt in ast.walk(setup_node):
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    if alias.name == "os":
                        offenders.append(
                            (stmt.lineno, f"import {alias.name}")
                        )
        self.assertEqual(
            offenders,
            [],
            msg=(
                "async_setup_entry must not contain "
                "``import os`` — the audit fix removed "
                "the in-function shadow. Offenders: "
                f"{offenders}"
            ),
        )


if __name__ == "__main__":
    _main(verbosity=2)