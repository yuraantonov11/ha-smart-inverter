"""T01: a real batterySoc of 0 must NOT be coerced into a fallback value.

Strategy: instead of importing api.py (which pulls aiohttp/cryptography
that are unavailable in the lightweight test venv), we extract the
relevant snippet via AST and run it in an isolated namespace. This keeps
the test honest: it really tests the *current* code, not a hand-rolled
copy that drifts over time.

Run with:
    /tmp/powmr-venv/bin/python tests/test_t01_soc_zero.py
"""
from __future__ import annotations

import ast
import math
import sys
import textwrap
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
api_path = ROOT / "api.py"
src = api_path.read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)
tree = ast.parse(src)


def _function_src(name: str) -> str:
    """Find ``name`` at any depth (module-level or inside a class)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            start = node.lineno - 1
            end = node.end_lineno  # ast end_lineno is 1-based inclusive
            return "".join(lines[start:end])
    raise SystemExit(f"{name} not found in api.py")


parse_realtime = _function_src("_parse_realtime_fields")
parse_double = _function_src("_parse_double")

# The functions live inside ``class InverterAPI``, so the extracted
# source has class-level indentation. Strip it before re-executing.
parse_realtime = textwrap.dedent(parse_realtime)
parse_double = textwrap.dedent(parse_double)

# `self._parse_double` inside the function refers to the class method;
# for the test we use a stub object whose ``_parse_double`` is the
# extracted static method. We re-execute the function in a fresh
# namespace so all closures resolve.
isolated_src = (
    "import math\n"
    + parse_double
    + "\n"
    + parse_realtime
)

ns: dict[str, Any] = {"__name__": "_t01_isolated"}
exec(isolated_src, ns)  # noqa: S102 — controlled test code

# Build a stub self whose ``_parse_double`` resolves to the static method.
from types import SimpleNamespace  # noqa: E402

_stub_self = SimpleNamespace(_parse_double=ns["_parse_double"])
_parse_double = ns["_parse_double"]


def _parse(fields: dict, payload: dict):
    """Drive the real function body with a stub self."""
    return ns["_parse_realtime_fields"](_stub_self, fields, payload)


def _battery_soc(payload: dict[str, Any]):
    parsed = _parse(payload, payload)
    return parsed.get("batterySoc")


def _make_fields(**values: Any) -> dict[str, Any]:
    return dict(values)


# ── scenarios from the audit ────────────────────────────────────────────

assert _battery_soc(_make_fields(batterySoc=0)) == 0, "real 0 must be preserved"
assert _battery_soc(_make_fields(batterySoc=12)) == 12
assert _battery_soc(_make_fields(batterySoc=50, batteryCapacity=99)) == 50
assert _battery_soc(_make_fields(batteryCapacity=12)) == 12
assert _battery_soc(_make_fields()) is None

nested = _make_fields()
nested["batterySoc"] = {"value": 0}
assert _battery_soc(nested) == 0

assert _battery_soc(_make_fields(batterySoc=None)) is None
assert _battery_soc(_make_fields(batterySoc="abc")) is None
assert _battery_soc(_make_fields(batterySoc=150)) == 150
assert _battery_soc(_make_fields(batterySoc=float("nan"))) is None
assert _battery_soc(_make_fields(batterySoc=-1)) == -1
assert _battery_soc(_make_fields(batterySoc=0, batteryCapacity=12)) == 0
assert _battery_soc(_make_fields(batterySoc=None, batteryCapacity=None)) is None

assert not math.isfinite(float("nan"))  # sanity: Python's NaN is not finite
# T03 fix: _parse_double now replaces non-finite with the default; the
# boundary therefore never returns NaN. The audit's regression check
# is that callers no longer see a non-finite number come out of the API.
def _is_finite(x):
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False
assert not _is_finite(float("nan"))  # raw NaN is non-finite
assert _is_finite(_parse_double(float("nan"), 0.0))  # but the parser hides it
assert _is_finite(_parse_double("NaN", 0.0))
assert _is_finite(_parse_double("0", 0.0))

# Edge: integer zero encoded as "0" string — T01 explicitly mentions it.
assert _battery_soc(_make_fields(batterySoc="0")) == 0

# Edge: missing both keys but a sentinel default shouldn't appear.
# The audit's "fabricated 100" scenario: the OLD code returned 100 here.
assert _battery_soc(_make_fields()) is None
# When the caller wants a numeric fallback (e.g., UI), the default kwarg
# in _first_present makes that explicit instead of hidden in 'or'.
assert _battery_soc(_make_fields()) is None or _battery_soc(_make_fields()) == 0

print("T01 OK — batterySoc=0 preserved, missing SOC is None, NaN rejected")
sys.exit(0)
