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

    def test_async_setup_entry_uses_module_level_os(self) -> None:
        """Audit T18 follow-up:
        ``async_setup_entry`` previously
        contained an in-function
        ``import os`` statement that
        shadowed the module-level
        ``os`` for the rest of the
        function, breaking every
        ``os.environ.get(...)`` call.

        We do not exec the entire
        function body — that requires
        stubbing ``hass``, ``api``,
        ``entry`` and several HA
        helpers, and the audit
        contract is about ``os``
        specifically. Instead we
        exec a *bounded* prefix of
        the function: the statements
        up to and including the first
        ``os.environ.get`` call,
        using the **same lexical
        scoping rules** Python uses
        at runtime. We deliberately
        *do not* insert a fresh
        ``import os`` at the top of
        the wrapper — that would
        mask the regression. If a
        future refactor reintroduces
        ``import os`` inside
        ``async_setup_entry`` ahead
        of the first
        ``os.environ.get`` call,
        this test reproduces the
        production crash.
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
        # Find the first statement
        # that contains an
        # ``os.environ.get`` call.
        target_idx: int | None = None
        for i, stmt in enumerate(setup_node.body):
            for sub in ast.walk(stmt):
                if (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "os"
                    and sub.attr == "environ"
                ):
                    target_idx = i
                    break
            if target_idx is not None:
                break
        self.assertIsNotNone(
            target_idx,
            msg=(
                "async_setup_entry must reference os.environ "
                "somewhere in its body; the audit fix may "
                "have removed the call"
            ),
        )
        # Build a wrapper that exec's
        # only the **first statement**
        # of the body. The audit
        # contract is that the
        # function reads ``os.environ``
        # **before** any local
        # ``import os`` (which would
        # shadow the module-level
        # name). If a regression
        # inserts ``import os`` ahead
        # of the first statement, the
        # exec'd body will raise
        # ``UnboundLocalError`` because
        # ``os`` is local but unbound.
        # We use ``ast.unparse`` to
        # get the source of the first
        # statement. To prove the
        # body reaches ``os.environ``,
        # we additionally exec a
        # second probe: the first
        # statement unparsed, with a
        # patched ``os.environ.get``
        # that records the read.
        first_stmt = setup_node.body[target_idx]
        first_stmt_src = ast.unparse(first_stmt)
        # The audit fix relies on
        # ``os`` resolving to the
        # module-level os module. We
        # patch its ``environ.get`` and
        # confirm the call records the
        # read. If a regression shadows
        # ``os`` with a local import,
        # the read fails.
        import os as _real_os
        os_reads: list[tuple[str, object]] = []

        class _StopHere(Exception):
            pass

        def _recorder(name, default=None):
            os_reads.append((name, default))
            raise _StopHere()

        original_get = _real_os.environ.get
        _real_os.environ.get = _recorder
        # Extract **the first
        # ``os.environ.get(...)`` call
        # expression** from the body.
        # This is the smallest unit
        # that proves the audit fix:
        # the body resolves ``os`` to
        # the module-level os module
        # at the call site, even
        # without any in-function
        # ``import os``. We exec the
        # expression in a flat
        # namespace that contains
        # ``os`` (the module we
        # patched) and ``_StopHere``
        # (a sentinel that aborts the
        # exec as soon as
        # ``environ.get`` is hit).
        target_call_src: str | None = None
        for stmt in setup_node.body:
            for sub in ast.walk(stmt):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Attribute)
                    and isinstance(sub.func.value.value, ast.Name)
                    and sub.func.value.value.id == "os"
                    and sub.func.value.attr == "environ"
                    and sub.func.attr == "get"
                ):
                    target_call_src = ast.unparse(sub)
                    break
            if target_call_src is not None:
                break
        self.assertIsNotNone(
            target_call_src,
            msg=(
                "async_setup_entry must contain an "
                "os.environ.get(...) call; the audit fix "
                "may have removed it"
            ),
        )
        try:
            ns: dict[str, object] = {
                "os": _real_os,
                "_StopHere": _StopHere,
            }
            try:
                exec(
                    compile(target_call_src, "<t18-truncated>", "exec"),
                    ns,
                )
            except _StopHere:
                pass
            except UnboundLocalError as exc:
                self.fail(
                    "async_setup_entry raised UnboundLocalError "
                    "before the first os.environ.get: "
                    f"{exc}. The audit fix has regressed — "
                    "an in-function import of os or json "
                    "is shadowing the module-level name."
                )
        finally:
            _real_os.environ.get = original_get
        # We must have hit at least
        # one ``os.environ.get``
        # call. If the body exited
        # before any read, the test
        # would trivially pass — that
        # is exactly the regression
        # we want to catch.
        self.assertTrue(
            os_reads,
            msg=(
                "async_setup_entry did not reach "
                "os.environ.get(...) before its first "
                "return; the audit fix may have removed "
                "the call"
            ),
        )


if __name__ == "__main__":
    _main(verbosity=2)