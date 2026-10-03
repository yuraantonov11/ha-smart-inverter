"""T14 regression — partial failure in HistoryCoordinator gather.

The audit showed that ``asyncio.gather(return_exceptions=True)``
returns the *exception instance* in the result list. The original
code path then called ``len(...)`` and ``.get(...)`` on those
instances, which raised ``TypeError`` and ``AttributeError``,
cascading into a complete history-coordinator failure.

The new code unwraps each result and falls back to the last known
good series when an individual endpoint fails. This test runs the
real body of ``_unwrap_history_results`` against synthetic results
that include:

  * a successful list,
  * a list that came back as a list-like but is empty,
  * an exception instance,
  * a totally wrong type (e.g. None),
  * a dict (for the total_energy endpoint),
  * an exception with a non-trivial ``__str__`` that should NOT
    leak into the response dict.

Run with:
    /tmp/powmr-venv/bin/python tests/test_t14_history_partial_failure.py
"""
from __future__ import annotations

import ast
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
coord_src = (ROOT / "coordinator.py").read_text(encoding="utf-8")
lines = coord_src.splitlines(keepends=True)
tree = ast.parse(coord_src)


def _function_src(name: str) -> str:
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            start = node.lineno - 1
            end = node.end_lineno
            return textwrap.dedent("".join(lines[start:end]))
    raise SystemExit(f"{name} not found")


unwrap_src = _function_src("_unwrap_history_results")
ns: dict[str, Any] = {
    "__name__": "_t14_isolated",
    "_LOGGER": __import__("logging").getLogger("t14_isolated"),
}
exec(unwrap_src, ns)  # noqa: S102 — controlled test code
_unwrap = ns["_unwrap_history_results"]


# ── scenarios from the audit ────────────────────────────────────────────

# 1. All four endpoints succeed.
class _OkDaily(list):
    pass

class _OkMonthly(list):
    pass

class _OkYearly(list):
    pass

daily = _OkDaily([{"time": "00", "value": 1}])
monthly = _OkMonthly([{"date": "2026-09-01", "value": 2}])
yearly = _OkYearly([{"month": "2026-09", "value": 3}])
total = {"value": 4.5, "totalEnergy": 4.5}

td, tm, ty, tt = _unwrap([daily, monthly, yearly, total])
assert td is daily
assert tm is monthly
assert ty is yearly
assert tt == total

# 2. fetch_daily_power raised.
err = RuntimeError("daily boom")
td, tm, ty, tt = _unwrap([err, monthly, yearly, total])
# The unwrap contract: failed endpoint falls back to an empty list
# (or empty dict for the total endpoint) so downstream len() and
# .get() succeed without the coordinator crashing.
assert td == [], f"daily must be empty after failure, got {td!r}"
assert tm is monthly
assert ty is yearly
assert tt == total

# 3. ALL endpoints fail — the coordinator must still produce
#    usable empty structures, not raise.
results = [
    RuntimeError("a"),
    RuntimeError("b"),
    RuntimeError("c"),
    RuntimeError("d"),
]
td, tm, ty, tt = _unwrap(results)
assert td == []
assert tm == []
assert ty == []
assert tt == {}

# 4. fetch_total_energy returned None — must be normalised to {}.
td, tm, ty, tt = _unwrap([daily, monthly, yearly, None])
assert tt == {}, f"None total must become {{}}, got {tt!r}"
assert td is daily  # other endpoints intact

# 5. fetch_total_energy returned a list (mistaken shape) — must be
#    normalised to {}, not crash on .get().
td, tm, ty, tt = _unwrap([daily, monthly, yearly, [1, 2, 3]])
assert tt == {}

# 6. fetch_daily_power returned a string (totally wrong type) —
#    must fall back to [].
td, tm, ty, tt = _unwrap(["not a list", monthly, yearly, total])
assert td == []

# 7. The exception's text must NOT leak into the success channels.
#    The audit's concern is that an error message containing
#    sensitive data (e.g. a URL with a token) would be returned to
#    the caller. We verify that the unwrap result does not include
#    any string from the exception's str() output.
sensitive = "Authorization=Bearer SECRET_TOKEN_AAA111"
results = [RuntimeError(sensitive), monthly, yearly, total]
td, tm, ty, tt = _unwrap(results)
for label, value in (("daily", td), ("monthly", tm), ("yearly", ty), ("total", tt)):
    s = str(value)
    assert "SECRET_TOKEN_AAA111" not in s, (
        f"{label} leaked sensitive error data: {s!r}"
    )

# 8. Indexing the result list beyond its length is a programming
#    error in production, but it should still be surfaced cleanly.
#    The unwrap helper must not crash if the list has fewer items
#    than the four canonical endpoints — defensive defaulting.
try:
    td, tm, ty, tt = _unwrap([daily, monthly])  # only 2 endpoints
    # Default values for the missing ones must be safe.
    assert td is daily
    assert tm is monthly
    assert ty == [], "missing yearly must default to []"
    assert tt == {}, "missing total must default to {}"
except Exception as exc:  # noqa: BLE001
    raise SystemExit(
        f"_unwrap_history_results crashed on short list: {exc!r}"
    )


# 9. Exception instance has no __str__ (pathological but possible
#    if someone raises a non-Exception). Make sure we don't crash.
class _Bare:
    pass


# We can't pass a non-Exception through the actual asyncio.gather
# (Python enforces that the awaitable raises BaseException), but
# we still test the unwrap function directly.
results = [_Bare(), monthly, yearly, total]
td, tm, ty, tt = _unwrap(results)
assert td == []
assert tm is monthly


# 10. Re-running the unwrap with the same instance is idempotent
#     — the audit calls out "subsequent reloads shouldn't add
#     duplicates"; we mirror that contract here.
results = [err, monthly, yearly, total]
td1, _, _, _ = _unwrap(results)
td2, _, _, _ = _unwrap(results)
assert td1 == td2 == []


print("T14 OK — partial failure in HistoryCoordinator does not crash")
sys.exit(0)
