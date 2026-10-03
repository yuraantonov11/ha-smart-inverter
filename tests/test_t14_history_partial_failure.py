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
    # T14 follow-up: the safety check uses ``math.isfinite`` to
    # reject non-finite numbers coming out of the dict slot.
    "math": __import__("math"),
}
exec(unwrap_src, ns)
_unwrap = ns["_unwrap_history_results"]


# ── helpers ─────────────────────────────────────────────────────────


def _unpack(results):
    """Unpack the new (value, is_fallback, total_kwh)-tuple contract."""
    td_p, tm_p, ty_p, tot_p = _unwrap(results)
    return (
        (td_p[0], td_p[1]),
        (tm_p[0], tm_p[1]),
        (ty_p[0], ty_p[1]),
        (tot_p[0], tot_p[1], tot_p[2]),
    )


# ── 1. All four endpoints succeed. ──────────────────────────────

class _OkDaily(list): pass
class _OkMonthly(list): pass
class _OkYearly(list): pass

daily = _OkDaily([{"time": "00", "value": 1}])
monthly = _OkMonthly([{"date": "2026-09-01", "value": 2}])
yearly = _OkYearly([{"month": "2026-09", "value": 3}])
# T14 follow-up: ``_raw_value`` is the sentinel the API client
# stamps onto the dict. A real cumulative reading has
# ``_raw_value`` not None. We pass a numeric ``_raw_value``
# (the same one the API would have extracted from the cloud)
# to make the test mirror the production payload.
total = {"value": 4.5, "totalEnergy": 4.5, "_raw_value": 4.5}

(td, tdf), (tm, tmf), (ty, tyf), (tot, totk, _) = _unpack([daily, monthly, yearly, total])
assert td is daily
assert tm is monthly
assert ty is yearly
assert tot == total
assert tdf is False, "successful daily must NOT be a fallback"
assert tmf is False
assert tyf is False


# ── 2. fetch_daily_power raised. ────────────────────────────────

err = RuntimeError("daily boom")
(td, tdf), (tm, tmf), (ty, tyf), (tot, totk, _) = _unpack([err, monthly, yearly, total])
assert td == [] and tdf is True, f"failed daily: {td!r} / {tdf!r}"
assert tm is monthly and tmf is False
assert ty is yearly and tyf is False
assert tot == total
assert totk == 4.5


# ── 3. ALL endpoints fail. ────────────────────────────────────

results = [RuntimeError("a"), RuntimeError("b"), RuntimeError("c"), RuntimeError("d")]
(td, tdf), (tm, tmf), (ty, tyf), (tot, totk, _) = _unpack(results)
assert td == [] and tdf is True
assert tm == [] and tmf is True
assert ty == [] and tyf is True
assert tot == {} and totk == 0.0


# ── 4. fetch_total_energy returned None → safe {}. ──────────

(td, _), (tm, _), (ty, _), (tot, totk, _) = _unpack([daily, monthly, yearly, None])
assert tot == {} and totk == 0.0
assert td is daily

# Wrong type for the dict slot.
(td, _), (tm, _), (ty, _), (tot, totk, _) = _unpack([daily, monthly, yearly, [1, 2, 3]])
assert tot == {} and totk == 0.0

# Total dict with no value / totalEnergy field.
(td, _), (tm, _), (ty, _), (tot, totk, _) = _unpack([daily, monthly, yearly, {"foo": 1}])
assert totk == 0.0


# ── 5. fetch_daily_power returned a string → safe []. ──────────

(td, tdf), _, _, _ = _unpack(["not a list", monthly, yearly, total])
assert td == [] and tdf is True


# ── 6. Exception text must NOT leak into the success channels.

sensitive = "Authorization=Bearer SECRET_TOKEN_AAA111"
results = [RuntimeError(sensitive), monthly, yearly, total]
(td, _), (tm, _), (ty, _), (tot, _, _) = _unpack(results)
for label, value in (("daily", td), ("monthly", tm), ("yearly", ty), ("total", tot)):
    s = str(value)
    assert "SECRET_TOKEN_AAA111" not in s, f"{label} leaked sensitive data: {s!r}"


# ── 7. Short result list — must not raise IndexError. ───────────

try:
    (td, _), (tm, _), (ty, tyf), (tot, _, _) = _unpack([daily, monthly])  # only 2
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

(td, _), (tm, _), (ty, _), (tot, totk, _) = _unpack(
    [daily, monthly, yearly, {"value": "12.34", "totalEnergy": "12.34",
                              "_raw_value": "12.34"}]
)
assert totk == 12.34


# ── 10. Total dict with negative value is parsed (callers clamp
# separately; the helper's job is just to surface a number).

(td, _), (tm, _), (ty, _), (tot, totk, _) = _unpack(
    [daily, monthly, yearly, {"value": -5.0, "totalEnergy": -5.0,
                              "_raw_value": -5.0}]
)
assert totk == -5.0


# ── 11. Total dict with only one of the two required keys is
# a fallback. The audit's follow-up: a transient backend
# error that returns ``{"value": 0}`` instead of the proper
# pair must NOT overwrite the cached total. The pair check
# in ``_safe_total`` rejects this case.

# Only "value", no "totalEnergy".
(td, _), (tm, _), (ty, _), (tot, totk, _) = _unpack(
    [daily, monthly, yearly, {"value": 0}]
)
assert tot == {}, f"single-key total must be a fallback, got {tot!r}"
assert totk == 0.0

# Only "totalEnergy", no "value".
(td, _), (tm, _), (ty, _), (tot, totk, _) = _unpack(
    [daily, monthly, yearly, {"totalEnergy": 12.0}]
)
assert tot == {}
assert totk == 0.0

# Neither key.
(td, _), (tm, _), (ty, _), (tot, totk, _) = _unpack(
    [daily, monthly, yearly, {"foo": 1}]
)
assert tot == {}
assert totk == 0.0


# ── 12. Total dict with the two keys disagreeing is a fallback.
# ``{"value": 0, "totalEnergy": 50}`` is a sign the cloud has
# a stale value; the helper refuses to pick a side.

(td, _), (tm, _), (ty, _), (tot, totk, _) = _unpack(
    [daily, monthly, yearly, {"value": 0, "totalEnergy": 50.0,
                              "_raw_value": 0}]
)
assert tot == {}, f"disagreeing pair must be a fallback, got {tot!r}"
assert totk == 0.0


# ── 13. Total dict with non-finite numbers is a fallback.
# NaN and +/-Infinity are not real readings.

(td, _), (tm, _), (ty, _), (tot, totk, _) = _unpack(
    [daily, monthly, yearly, {"value": float("nan"), "totalEnergy": float("nan"),
                              "_raw_value": float("nan")}]
)
assert tot == {}
assert totk == 0.0

(td, _), (tm, _), (ty, _), (tot, totk, _) = _unpack(
    [daily, monthly, yearly, {"value": float("inf"), "totalEnergy": float("inf"),
                              "_raw_value": float("inf")}]
)
assert tot == {}
assert totk == 0.0


# ── 14. Real zero: a proper pair of zeros is a real reading,
# not a fallback. The helper surfaces 0.0 with ``is_fallback=False``.

(td, _), (tm, _), (ty, _), (tot, totk, _) = _unpack(
    [daily, monthly, yearly, {"value": 0.0, "totalEnergy": 0.0,
                              "_raw_value": 0.0}]
)
assert tot == {"value": 0.0, "totalEnergy": 0.0, "_raw_value": 0.0}
assert totk == 0.0


# ── 15. The audit's specific follow-up: a transient backend
# error where the API returns ``{"value": 0, "totalEnergy": 0}``
# because the underlying data was missing (the API path sets
# ``_raw_value`` to ``None``). Even though the *pair* is intact
# and the values are equal, ``_raw_value is None`` must trigger
# a fallback. This is the regression the audit specifically
# called out: without the sentinel, the cache would be
# zeroed over a real 1500 kWh reading.

(td, _), (tm, _), (ty, _), (tot, totk, _) = _unpack(
    [daily, monthly, yearly, {"value": 0.0, "totalEnergy": 0.0,
                              "_raw_value": None}]
)
assert tot == {}, f"_raw_value=None must be a fallback, got {tot!r}"
assert totk == 0.0


# ── 16. Missing ``_raw_value`` field is treated as ``None`` —
# a payload that was not produced by the production API path.
# This is the *default* defence in depth: even if a future
# refactor drops the ``_raw_value`` field entirely, the
# helper reverts to the safe behaviour.

(td, _), (tm, _), (ty, _), (tot, totk, _) = _unpack(
    [daily, monthly, yearly, {"value": 12.5, "totalEnergy": 12.5}]
)
assert tot == {}, f"missing _raw_value must be a fallback, got {tot!r}"
assert totk == 0.0


print("T14 OK — unwrap helper is defensive, sensitive data is masked, partial-failure semantics correct")
sys.exit(0)
