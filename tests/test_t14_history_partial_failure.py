"""T14 — exercise the unwrap helper directly.

The previous test (commit 64c4bb6) covered the unwrap helper
with a single ``(value, is_fallback)`` flag per slot, and
test_t14_history_lkg_real_path exercises the *cache write*
contract through a real ``HistoryCoordinator._async_update_data``
instance. Together they cover both layers: the helper's defensive
behaviour and the cache's last-known-good preservation.

This file is intentionally small and isolated; if a refactor
breaks the helper, this test catches it without depending on
home-assistant mocks.
"""
from __future__ import annotations

import ast
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
coord_src = (ROOT / "coordinator.py").read_text(encoding="utf-8")
lines = coord_src.splitlines(keepends=True)
tree = ast.parse(coord_src)


def _function_src(name: str, klass: str | None = None) -> str:
    for cls in ast.walk(tree):
        if (
            isinstance(cls, ast.ClassDef)
            and (klass is None or cls.name == klass)
        ):
            for sub in cls.body:
                if (
                    isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and sub.name == name
                ):
                    start = sub.lineno - 1
                    end = sub.end_lineno
                    return textwrap.dedent("".join(lines[start:end]))
    raise SystemExit(f"{name} not found")


unwrap_src = _function_src("_unwrap_history_results", "HistoryCoordinator")
ns = {
    "__name__": "_t14_isolated",
    "_LOGGER": __import__("logging").getLogger("t14_isolated"),
}
exec(unwrap_src, ns)
_unwrap = ns["_unwrap_history_results"]


# ── helpers ─────────────────────────────────────────────────────────


def _unpack(results):
    """Unpack the new (value, is_fallback)-tuple contract."""
    td_p, tm_p, ty_p, tot_p = _unwrap(results)
    return (
        (td_p[0], td_p[1]),
        (tm_p[0], tm_p[1]),
        (ty_p[0], ty_p[1]),
        (tot_p[0], tot_p[1]),
    )


# ── 1. All four endpoints succeed. ──────────────────────────────

class _OkDaily(list): pass
class _OkMonthly(list): pass
class _OkYearly(list): pass

daily = _OkDaily([{"time": "00", "value": 1}])
monthly = _OkMonthly([{"date": "2026-09-01", "value": 2}])
yearly = _OkYearly([{"month": "2026-09", "value": 3}])
total = {"value": 4.5, "totalEnergy": 4.5}

(td, tdf), (tm, tmf), (ty, tyf), (tot, totk) = _unpack([daily, monthly, yearly, total])
assert td is daily
assert tm is monthly
assert ty is yearly
assert tot == total
assert tdf is False, "successful daily must NOT be a fallback"
assert tmf is False
assert tyf is False


# ── 2. fetch_daily_power raised. ────────────────────────────────

err = RuntimeError("daily boom")
(td, tdf), (tm, tmf), (ty, tyf), (tot, totk) = _unpack([err, monthly, yearly, total])
assert td == [] and tdf is True, f"failed daily: {td!r} / {tdf!r}"
assert tm is monthly and tmf is False
assert ty is yearly and tyf is False
assert tot == total
assert totk == 4.5


# ── 3. ALL endpoints fail. ────────────────────────────────────

results = [RuntimeError("a"), RuntimeError("b"), RuntimeError("c"), RuntimeError("d")]
(td, tdf), (tm, tmf), (ty, tyf), (tot, totk) = _unpack(results)
assert td == [] and tdf is True
assert tm == [] and tmf is True
assert ty == [] and tyf is True
assert tot == {} and totk == 0.0


# ── 4. fetch_total_energy returned None → safe {}. ──────────

(td, _), (tm, _), (ty, _), (tot, totk) = _unpack([daily, monthly, yearly, None])
assert tot == {} and totk == 0.0
assert td is daily

# Wrong type for the dict slot.
(td, _), (tm, _), (ty, _), (tot, totk) = _unpack([daily, monthly, yearly, [1, 2, 3]])
assert tot == {} and totk == 0.0

# Total dict with no value / totalEnergy field.
(td, _), (tm, _), (ty, _), (tot, totk) = _unpack([daily, monthly, yearly, {"foo": 1}])
assert totk == 0.0


# ── 5. fetch_daily_power returned a string → safe []. ──────────

(td, tdf), _, _, _ = _unpack(["not a list", monthly, yearly, total])
assert td == [] and tdf is True


# ── 6. Exception text must NOT leak into the success channels.

sensitive = "Authorization=Bearer SECRET_TOKEN_AAA111"
results = [RuntimeError(sensitive), monthly, yearly, total]
(td, _), (tm, _), (ty, _), (tot, _) = _unpack(results)
for label, value in (("daily", td), ("monthly", tm), ("yearly", ty), ("total", tot)):
    s = str(value)
    assert "SECRET_TOKEN_AAA111" not in s, f"{label} leaked sensitive data: {s!r}"


# ── 7. Short result list — must not raise IndexError. ───────────

try:
    (td, _), (tm, _), (ty, tyf), (tot, _) = _unpack([daily, monthly])  # only 2
    assert td is daily
    assert tm is monthly
    assert ty == [] and tyf is True
    assert tot == {} and isinstance(tot, dict)
except Exception as exc:  # noqa: BLE001
    raise SystemExit(f"_unwrap_history_results crashed on short list: {exc!r}")


# ── 8. Idempotent: re-running the unwrap is stable. ───────────

results = [err, monthly, yearly, total]
td1, _, _, _ = _unpack(results)
td2, _, _, _ = _unpack(results)
# ``td1`` and ``td2`` are ``(value, is_fallback)`` tuples.
# Compare the values directly.
assert td1[0] == td2[0] == [] and td1[1] == td2[1] is True


# ── 9. Total dict with value as string parses as float. ────────

(td, _), (tm, _), (ty, _), (tot, totk) = _unpack(
    [daily, monthly, yearly, {"value": "12.34", "totalEnergy": "12.34"}]
)
assert totk == 12.34


# ── 10. Total dict with negative value is parsed (callers clamp
# separately; the helper's job is just to surface a number).

(td, _), (tm, _), (ty, _), (tot, totk) = _unpack(
    [daily, monthly, yearly, {"value": -5.0, "totalEnergy": -5.0}]
)
assert totk == -5.0


print("T14 OK — unwrap helper is defensive, sensitive data is masked, partial-failure semantics correct")
sys.exit(0)
