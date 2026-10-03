"""T03: NaN / +/-Infinity / non-finite inputs at the API boundary.

The fix is in ``InverterAPI._parse_double``: it now returns the
``default`` for any value that is not finite. This test exercises
every supported input shape and proves the propagation of non-finite
values is now stopped at the boundary.

Run with:
    /tmp/powmr-venv/bin/python tests/test_t03_nan_finite.py
"""
from __future__ import annotations

import ast
import math
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
api_src = (ROOT / "api.py").read_text(encoding="utf-8")
lines = api_src.splitlines(keepends=True)
tree = ast.parse(api_src)


def _function_src(name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            start = node.lineno - 1
            end = node.end_lineno
            return textwrap.dedent("".join(lines[start:end]))
    raise SystemExit(f"{name} not found in api.py")


parse_double_src = _function_src("_parse_double")
ns: dict = {"__name__": "_t03_isolated", "math": math}
exec(parse_double_src, ns)  # noqa: S102
_parse_double = ns["_parse_double"]


# ── scenarios from the audit ────────────────────────────────────────────

# NaN — never a valid measurement
assert _parse_double(float("nan"), 0.0) == 0.0
assert _parse_double(float("nan"), 42.0) == 42.0

# Infinity
assert _parse_double(float("inf"), 0.0) == 0.0
assert _parse_double(float("-inf"), 0.0) == 0.0

# String versions are not silently accepted
assert _parse_double("NaN", 0.0) == 0.0
assert _parse_double("Infinity", 0.0) == 0.0
assert _parse_double("-Infinity", 0.0) == 0.0

# Normal numbers pass through
assert _parse_double(0, 99.0) == 0
assert _parse_double(0.0, 99.0) == 0.0
assert _parse_double("0", 99.0) == 0.0
assert _parse_double(150, 0.0) == 150
assert _parse_double("12.5", 0.0) == 12.5
assert _parse_double(-1, 0.0) == -1

# None / non-numeric
assert _parse_double(None, 7.0) == 7.0
assert _parse_double("abc", 7.0) == 7.0
assert _parse_double([], 7.0) == 7.0
assert _parse_double({}, 7.0) == 7.0

# Default fallback is honoured
assert _parse_double(float("nan"), -1.0) == -1.0
assert _parse_double("garbage", -1.0) == -1.0

# A legitimate numeric string with whitespace trims and parses
assert _parse_double("  3.14  ", 0.0) == 3.14

# Truth-table: math.isfinite is what we use
assert math.isfinite(_parse_double(0, 0.0))
assert math.isfinite(_parse_double(0.001, 0.0))
assert not math.isfinite(float("nan"))
assert not math.isfinite(float("inf"))


print("T03 OK — non-finite API inputs are stopped at the boundary")
sys.exit(0)
