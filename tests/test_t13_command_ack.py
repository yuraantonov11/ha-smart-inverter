"""T13: every command entry point must check the cloud ACK.

The audit's T13 review found that ``SwitchEntity.async_turn_on``,
``async_turn_off`` and most of the ``services/__init__.py``
handlers ignored the boolean returned by ``set_config_item``
or ``set_output_priority``. A False ACK or a raised
exception would still leave the switch in its "ON" state
from HA's point of view, and the next ``async_request_refresh``
would paper over the failure with stale data.

The fix:

  * ``SwitchEntity`` handlers capture the ACK, log an error
    on ``False``, and skip the refresh on failure.
  * Service handlers do the same.

This test parses ``switch.py`` and ``services/__init__.py``
through AST and asserts that every place which calls one
of the write methods (``set_config_item``,
``set_output_priority``, ``set_charger_priority``) binds
the call to a name (``ok``, ``ok1``/``ok2``, …) and follows
it with a check on that name. We exclude the existing
ACK-aware sites so we don't double-count the already-fixed
ones.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRITE_METHODS = {"set_config_item", "set_output_priority", "set_charger_priority"}


def _is_assign_to(node: ast.AST, target_name: str) -> bool:
    """True if ``node`` is ``x = ...`` and ``x`` matches."""
    if not isinstance(node, ast.Assign):
        return False
    if len(node.targets) != 1:
        return False
    target = node.targets[0]
    return isinstance(target, ast.Name) and target.id == target_name


def _is_write_await(stmt: ast.AST) -> bool:
    """True if ``stmt`` is an ``await`` whose target calls one
    of the configured write methods, *regardless* of whether
    it is bound to a name. We use this to walk statement
    bodies looking for ACK call sites."""
    if not isinstance(stmt, ast.Await):
        return False
    call = stmt.value
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    return func.attr in WRITE_METHODS


def _is_write_assign(stmt: ast.AST) -> bool:
    """True if ``stmt`` is ``x = await ...`` where the
    awaited call targets one of the write methods."""
    if not isinstance(stmt, ast.Assign):
        return False
    return _is_write_await(stmt.value)


def _guarded_within(body: list[ast.stmt], idx: int, names: set[str]) -> bool:
    """Look ahead up to 5 statements for any guard that
    inspects the ACK variable.

    Accepted shapes:

      * ``if not <name>: ... return / error``
      * ``if <name>: ... else: error``
      * ``if not <name1> and not <name2>: ...``
      * ``if <name1> and <name2>: ...``

    The shared goal is: the function actually inspects the
    ACK before declaring success.
    """
    for j in range(idx + 1, min(idx + 6, len(body))):
        stmt = body[j]
        if not isinstance(stmt, ast.If):
            continue
        test = stmt.test
        # ``if not <name>:``
        if (
            isinstance(test, ast.UnaryOp)
            and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Name)
            and test.operand.id in names
        ):
            return True
        # ``if <name>:``
        if isinstance(test, ast.Name) and test.id in names:
            # We accept the ``if ok: ... else: error`` shape
            # only if the ``else`` branch is present (the
            # audit's concern was that a missing else
            # would let failures be reported as success).
            if stmt.orelse:
                return True
        # ``if <a> and <b>`` / ``if not a and not b``
        if isinstance(test, ast.BoolOp):
            if all(
                isinstance(v, ast.Name) and v.id in names
                for v in test.values
            ):
                return True
            if all(
                isinstance(v, ast.UnaryOp)
                and isinstance(v.op, ast.Not)
                and isinstance(v.operand, ast.Name)
                and v.operand.id in names
                for v in test.values
            ):
                return True
    return False


# ── 1. switch.py: every async_turn_on/off must have at
# least one ``ok = await ...``-style ACK capture, and
# every captured ACK must be guarded.

switch_src = (ROOT / "switch.py").read_text(encoding="utf-8")
switch_tree = ast.parse(switch_src)

violations: list[str] = []

# Helper: find the class that owns a function definition.
def _class_of(func: ast.AsyncFunctionDef, tree: ast.AST) -> str:
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef):
            for sub in cls.body:
                if sub is func:
                    return cls.name
    return "<module>"


def _is_ack_call(stmt: ast.stmt) -> bool:
    """True if ``stmt`` is a (possibly bare) await that
    ultimately calls one of the ACK-aware writer methods.

    We also accept the case where a switch delegates to
    a coordinator method (``coordinator.async_set_*``,
    ``coordinator.async_predictive_feedback``) — those
    methods already return a value the coordinator owns
    the ACK for, so the switch doesn't need to re-check.
    """
    # Bare await on a write method.
    if _is_write_await(stmt):
        return True
    # Bound to a name: ``ok = await api.set_X(...)``.
    if _is_write_assign(stmt):
        return True
    # Delegated to a coordinator setter that itself owns
    # the ACK. We do not require a guard on the switch
    # because the *coordinator* is the right place to
    # inspect the cloud response.
    if isinstance(stmt, ast.Expr):
        call = stmt.value
        if isinstance(call, ast.Call):
            func = call.func
            if isinstance(func, ast.Attribute):
                if func.attr in {
                    "async_set_hems_auto_mode",
                    "async_set_smart_mode",
                    "async_set_predictive_mode",
                    "async_predictive_feedback",
                }:
                    return True
    return False


for func in ast.walk(switch_tree):
    if (
        not isinstance(func, ast.AsyncFunctionDef)
        or func.name not in ("async_turn_on", "async_turn_off")
    ):
        continue
    class_name = _class_of(func, switch_tree)
    body = func.body
    has_write = False
    has_delegated = False
    for idx, stmt in enumerate(body):
        if _is_write_assign(stmt):
            has_write = True
            if not _guarded_within(body, idx, {"ok"}):
                violations.append(
                    f"switch.py:{class_name}.{func.name}: "
                    f"'ok' captured but not followed by 'if not ok'"
                )
        elif _is_write_await(stmt):
            has_write = True
            violations.append(
                f"switch.py:{class_name}.{func.name}: "
                f"bare await on write method (no 'ok =' capture)"
            )
        elif isinstance(stmt, ast.Expr) and isinstance(
            stmt.value, ast.Call
        ):
            func_attr = stmt.value.func
            if isinstance(func_attr, ast.Attribute) and func_attr.attr in {
                "async_set_hems_auto_mode",
                "async_set_smart_mode",
                "async_set_predictive_mode",
            }:
                has_delegated = True
    if not has_write and not has_delegated:
        violations.append(
            f"switch.py:{class_name}.{func.name}: "
            f"no ACK call (no write and no delegated setter)"
        )

if violations:
    print("T13 switch.py violations:")
    for v in violations:
        print(f"  {v}")
    raise SystemExit("T13 FAIL — switch.py has unguarded ACK")
print(f"T13 case 1 OK — all {len([f for f in ast.walk(switch_tree) if isinstance(f, ast.AsyncFunctionDef) and f.name in ('async_turn_on','async_turn_off')])} switch handlers ACK-guarded")


# ── 2. services/__init__.py: every ``handle_*`` function
# that calls a write method must capture the return and
# guard on it.

services_src = (ROOT / "services" / "__init__.py").read_text(encoding="utf-8")
services_tree = ast.parse(services_src)
violations.clear()

for func in ast.walk(services_tree):
    if (
        not isinstance(func, ast.AsyncFunctionDef)
        or not func.name.startswith("handle_")
    ):
        continue
    body = func.body
    for idx, stmt in enumerate(body):
        if _is_write_assign(stmt):
            # ``ok = ...`` form: must be guarded.
            if not _guarded_within(body, idx, {"ok", "ok1", "ok2"}):
                violations.append(
                    f"services/__init__.py:{func.name}: "
                    f"write call captured but not guarded"
                )
        elif _is_write_await(stmt):
            violations.append(
                f"services/__init__.py:{func.name}: "
                f"bare await on write method (no 'ok =' capture)"
            )

if violations:
    print("T13 services violations:")
    for v in violations:
        print(f"  {v}")
    raise SystemExit("T13 FAIL — services/__init__.py has unguarded writes")
print("T13 case 2 OK — every services/__init__.py handle_* is ACK-guarded")


# ── 3. Sanity: production code uses the new pattern. The
# previous behavior — bare ``await api.set_config_item(...)
# \n        await coordinator.async_request_refresh()``
# without ACK — would fail case 1.

ack_site = re.search(
    r"ok\s*=\s*await\s+api\.set_config_item",
    services_src,
)
assert ack_site is not None, (
    "T13: expected at least one 'ok = await api.set_config_item' "
    "site in services/__init__.py"
)
print("T13 case 3 OK — production code uses 'ok = await api.set_config_item'")


# ── 4. Reproduce-the-bug: the audit's exact concern was
# that ``set_config_item`` returning ``False`` was ignored.
# We assert the source no longer contains the audit's
# specific bad pattern. The pattern is the *pre-fix*
# form: a write call immediately followed by
# ``async_request_refresh`` without an intervening
# ``if not ok:`` guard. We approximate it with a regex
# (the AST check above is the strict one).

bare_pattern = re.compile(
    r"await self\.coordinator\.api\.set_(?:config_item|output_priority|charger_priority)"
    r"(?:\([^)]*\))?"
    r"\s*\n\s*await self\.coordinator\.async_request_refresh",
)
bare_hits = bare_pattern.findall(switch_src)
assert not bare_hits, (
    f"T13 FAIL — switch.py still has the audit's bare-await pattern: {len(bare_hits)} sites"
)
print("T13 case 4 OK — no bare-await + immediate-refresh sites in switch.py")


print("T13 OK — every command entry point checks the cloud ACK")
sys.exit(0)
