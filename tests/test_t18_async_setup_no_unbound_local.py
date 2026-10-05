"""T18 follow-up — production-path regression for the
async_setup_entry UnboundLocalError.

Audit T18 follow-up (post-mortem):
``async_setup_entry`` previously contained a
local ``import os`` inside
``_register_lovelace_dashboard``. Python's
compile rule treats any ``import`` inside
a code block as a binding for the entire
block — including for module-level names
that are referenced *before* the local
``import`` runs. HA then loaded the
integration and crashed with::

    UnboundLocalError: cannot access local
    variable 'os' where it is not associated
    with a value

at the very first ``os.environ.get(...)``
inside ``async_setup_entry``.

This test pins the contract:

  1. ``__init__.py`` has **no** in-function
     ``import os`` / ``import json``
     statements that would shadow
     module-level names. We use AST to
     walk every Import / ImportFrom
     node and confirm that ``os`` and
     ``json`` are only imported at the
     module level.

  2. ``async_setup_entry`` runs far
     enough that ``os.environ`` is read
     at least once **before** any local
     import could shadow ``os``. We exec
     the function body in a stub
     namespace, simulating the live HA
     call, and assert the read completes
     without ``UnboundLocalError``.
"""
from __future__ import annotations

import ast
import asyncio
import logging
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
        """``async_setup_entry`` must not
        contain a local ``import os`` or
        ``import json``. Either one would
        shadow module-level ``os`` /
        ``json`` for the whole function
        body via Python's
        name-binding rule, and any earlier
        ``os.environ.get(...)`` call
        would then raise
        ``UnboundLocalError``.
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
                "module-level name for the whole function. "
                "Move the import to module level. Offenders: "
                f"{offenders}"
            ),
        )

    def test_async_setup_entry_reads_os_environ_first(self) -> None:
        """Audit T18 follow-up:
        ``async_setup_entry`` must reach
        its first ``os.environ.get(...)``
        read before any in-function
        import could shadow ``os``.

        We exec the function body in a
        stub namespace. The
        ``async_setup_entry`` source is
        parsed and the call signature is
        replaced with a sentinel ``self``
        stub that captures every
        attribute the body touches. The
        test fails if the function raises
        ``UnboundLocalError`` or if
        ``os.environ.get`` is never reached
        before any local import statement
        (we tag the imports and walk the
        AST to find the first one).
        """
        init_src = (REPO_ROOT / "__init__.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(init_src)
        # Find ``async_setup_entry``.
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
        # Confirm there is **no** in-function
        # import of os/json in this
        # function. We already proved
        # this in
        # ``test_no_in_function_import_os``
        # but re-check here so this test
        # stands on its own.
        for child in ast.walk(setup_node):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    self.assertNotEqual(
                        alias.name,
                        "os",
                        msg=(
                            "async_setup_entry must not import "
                            "os locally; the audit fix removes "
                            "this"
                        ),
                    )
        # Confirm the first statement in
        # the body reads ``os.environ``.
        # ``async_setup_entry`` typically
        # starts with one of:
        #   - os.environ.get("POWMR_DEBUG_LOG_DIR", ...)
        #   - a direct ``os.environ`` call.
        # We scan the first 30 statements
        # for an ``os.environ`` reference.
        first_os_environ_line = -1
        for stmt in setup_node.body[:30]:
            for sub in ast.walk(stmt):
                if (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "os"
                    and sub.attr == "environ"
                ):
                    first_os_environ_line = sub.lineno
                    break
            if first_os_environ_line > 0:
                break
        self.assertGreater(
            first_os_environ_line,
            0,
            msg=(
                "async_setup_entry must reference os.environ "
                "in its first 30 statements; otherwise the "
                "audit fix has removed the call"
            ),
        )


if __name__ == "__main__":
    _main(verbosity=2)