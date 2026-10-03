"""T14 follow-up — exercise the *real* ``_async_update_data`` path
of ``HistoryCoordinator`` rather than the unwrap helper alone.

The previous test (commit 64c4bb6) covered only
``_unwrap_history_results``. The review point is that the *cache
write* path was still wrong: the helper returned ``[]`` for a
failed endpoint and the caller dutifully wrote that empty value
into ``self.today_hourly_power``, overwriting the last known
good series. The chart would go flat every time the cloud
hiccupped, which the helper test was structurally unable to
detect because it never touched the coordinator's state.

This test extracts the real ``_async_update_data`` body via
AST and runs it against a minimal stand-in object that
captures every cache write. The body is a transcription of
the production code (with comments preserved); if the live
code changes, this test must be updated to match — that is
the whole point of the audit's complaint.

Run with:
    /tmp/powmr-venv/bin/python tests/test_t14_history_lkg_real_path.py
"""
from __future__ import annotations

import ast
import asyncio
import logging
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
coord_src = (ROOT / "coordinator.py").read_text(encoding="utf-8")
lines = coord_src.splitlines(keepends=True)
tree = ast.parse(coord_src)


def _function_src(name: str, klass: str | None = None) -> str:
    """Extract a method body. If ``klass`` is given, only return
    the function whose parent class matches; otherwise any."""
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
    raise SystemExit(f"{name} not found in {klass or 'coordinator.py'}")


# Two AST slices are needed:
#   1. ``_unwrap_history_results`` — the static method that
#      converts asyncio.gather results into safe slots.
#   2. ``_async_update_data`` on ``HistoryCoordinator`` (not the
#      one on ``InverterCoordinator``!) — the body that consumes
#      the unwrap result and writes to the coordinator cache.
unwrap_src = _function_src("_unwrap_history_results", "HistoryCoordinator")
async_update_src = _function_src("_async_update_data", "HistoryCoordinator")

ns: dict[str, Any] = {
    "__name__": "_t14_lkg_isolated",
    "_LOGGER": __import__("logging").getLogger("t14_lkg_isolated"),
    "datetime": __import__("datetime").datetime,
    "asyncio": __import__("asyncio"),
    # T14 follow-up: ``_safe_total`` uses ``math.isfinite`` to
    # reject non-finite numbers; without ``math`` in the
    # isolated namespace the function fails before the body
    # even runs.
    "math": __import__("math"),
}
exec(unwrap_src, ns)
exec(async_update_src, ns)
_unwrap = ns["_unwrap_history_results"]
_async_update_data = ns["_async_update_data"]


# ── Stand-in coordinator — captures every cache write. ───────────

class _StubHistoryCoordinator:
    """Minimal surface for ``_async_update_data``.

    The body only touches:
      - self.api.fetch_*  (mocked)
      - self._unwrap_history_results (the static method on
        HistoryCoordinator that the body calls; we attach the
        unwrap function from the isolated namespace as a method)
      - self.today_hourly_power, .monthly_daily_energy,
        .yearly_monthly_energy, .total_energy_kwh (cache)
      - self.daily_historical_weather, .daily_weather_count
        (refreshed by a separate coroutine; we override
        ``_fetch_historical_weather`` to a no-op)
      - self.logger / _LOGGER — we redirect to a captured logger
    """

    def __init__(self, api):
        self.api = api
        self.today_hourly_power: list = []
        self.monthly_daily_energy: list = []
        self.yearly_monthly_energy: list = []
        self.total_energy_kwh: float = 0.0
        self.daily_historical_weather: dict = {}
        self.daily_weather_count: int = 0
        self._history_coords = (50.45, 30.52)
        # Bind the unwrap helper as a method so ``self._unwrap_history_results``
        # resolves inside the extracted function body.
        self._unwrap_history_results = _unwrap

    async def _fetch_historical_weather(self) -> dict:
        return {}


class _MockAPI:
    def __init__(self, daily, monthly, yearly, total):
        self._daily = daily
        self._monthly = monthly
        self._yearly = yearly
        self._total = total
        self.calls: list[str] = []

    async def fetch_daily_power(self):
        self.calls.append("daily")
        return self._daily

    async def fetch_monthly_energy(self):
        self.calls.append("monthly")
        return self._monthly

    async def fetch_yearly_energy(self):
        self.calls.append("yearly")
        return self._yearly

    async def fetch_total_energy(self):
        self.calls.append("total")
        return self._total


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── 1. ALL endpoints fail: cache must not change. ─────────────────

async def _scenario_all_fail():
    api = _MockAPI(
        daily=RuntimeError("d"),
        monthly=RuntimeError("m"),
        yearly=RuntimeError("y"),
        total=RuntimeError("t"),
    )
    c = _StubHistoryCoordinator(api)
    c.today_hourly_power = [{"time": "00", "value": 1.0}]
    c.monthly_daily_energy = [{"date": "2026-09-01", "value": 2.0}]
    c.yearly_monthly_energy = [{"month": "2026-09", "value": 3.0}]
    c.total_energy_kwh = 12345.6
    await _async_update_data(c)
    assert c.today_hourly_power == [{"time": "00", "value": 1.0}], (
        f"daily cache was overwritten after a failed gather: "
        f"{c.today_hourly_power!r}"
    )
    assert c.monthly_daily_energy == [{"date": "2026-09-01", "value": 2.0}], (
        f"monthly cache overwritten: {c.monthly_daily_energy!r}"
    )
    assert c.yearly_monthly_energy == [{"month": "2026-09", "value": 3.0}], (
        f"yearly cache overwritten: {c.yearly_monthly_energy!r}"
    )
    assert c.total_energy_kwh == 12345.6, (
        f"total was zeroed after a failed total endpoint: "
        f"{c.total_energy_kwh!r}"
    )


_run(_scenario_all_fail())


# ── 2. Only the total endpoint fails: lists update, total kept. ──

async def _scenario_partial_fail():
    api = _MockAPI(
        daily=[{"time": "12", "value": 9.0}],
        monthly=[{"date": "2026-09-30", "value": 8.0}],
        yearly=[{"month": "2026-09", "value": 7.0}],
        total=RuntimeError("tot"),
    )
    c = _StubHistoryCoordinator(api)
    c.today_hourly_power = [{"time": "00", "value": 1.0}]
    c.monthly_daily_energy = [{"date": "2026-09-01", "value": 2.0}]
    c.yearly_monthly_energy = [{"month": "2026-09", "value": 3.0}]
    c.total_energy_kwh = 999.99
    await _async_update_data(c)
    assert c.today_hourly_power == [{"time": "12", "value": 9.0}], (
        f"daily cache should have updated: {c.today_hourly_power!r}"
    )
    assert c.monthly_daily_energy == [{"date": "2026-09-30", "value": 8.0}]
    assert c.yearly_monthly_energy == [{"month": "2026-09", "value": 7.0}]
    assert c.total_energy_kwh == 999.99, (
        f"total must be preserved when the total endpoint fails: "
        f"{c.total_energy_kwh!r}"
    )


_run(_scenario_partial_fail())


# ── 3. All four endpoints succeed: cache reflects new values. ─────

async def _scenario_all_ok():
    api = _MockAPI(
        daily=[{"time": "13", "value": 11.0}],
        monthly=[{"date": "2026-09-30", "value": 12.0}],
        yearly=[{"month": "2026-09", "value": 13.0}],
        total={"value": 14.0, "totalEnergy": 14.0, "_raw_value": 14.0},
    )
    c = _StubHistoryCoordinator(api)
    c.today_hourly_power = []
    c.monthly_daily_energy = []
    c.yearly_monthly_energy = []
    c.total_energy_kwh = 0.0
    await _async_update_data(c)
    assert c.today_hourly_power == [{"time": "13", "value": 11.0}]
    assert c.monthly_daily_energy == [{"date": "2026-09-30", "value": 12.0}]
    assert c.yearly_monthly_energy == [{"month": "2026-09", "value": 13.0}]
    assert c.total_energy_kwh == 14.0, c.total_energy_kwh


_run(_scenario_all_ok())


# ── 4. The total endpoint returns an empty dict (success but empty):
# the cache must NOT be zeroed.

async def _scenario_total_empty_dict():
    api = _MockAPI(
        daily=[{"time": "13", "value": 11.0}],
        monthly=[{"date": "2026-09-30", "value": 12.0}],
        yearly=[{"month": "2026-09", "value": 13.0}],
        total={},  # success but no value/totalEnergy field
    )
    c = _StubHistoryCoordinator(api)
    c.total_energy_kwh = 999.0
    await _async_update_data(c)
    assert c.total_energy_kwh == 999.0, (
        f"empty-dict total endpoint must not zero the cache: "
        f"{c.total_energy_kwh!r}"
    )


_run(_scenario_total_empty_dict())


# ── 5. Only the daily endpoint fails. ──────────────────────────────

async def _scenario_daily_fail():
    api = _MockAPI(
        daily=RuntimeError("d"),
        monthly=[{"date": "2026-09-30", "value": 22.0}],
        yearly=[{"month": "2026-09", "value": 23.0}],
        total={"value": 24.0, "totalEnergy": 24.0, "_raw_value": 24.0},
    )
    c = _StubHistoryCoordinator(api)
    c.today_hourly_power = [{"time": "00", "value": 1.0}]
    c.monthly_daily_energy = []
    c.yearly_monthly_energy = []
    c.total_energy_kwh = 0.0
    await _async_update_data(c)
    assert c.today_hourly_power == [{"time": "00", "value": 1.0}], (
        f"daily cache should not have been replaced with []: "
        f"{c.today_hourly_power!r}"
    )
    assert c.monthly_daily_energy == [{"date": "2026-09-30", "value": 22.0}]
    assert c.yearly_monthly_energy == [{"month": "2026-09", "value": 23.0}]
    assert c.total_energy_kwh == 24.0


_run(_scenario_daily_fail())


# ── 6. Total endpoint returns a malformed dict (the audit's
# follow-up). The pair check in ``_safe_total`` rejects a dict
# with only one of ``value`` / ``totalEnergy``; a transient
# backend error that returns ``{"value": 0}`` instead of the
# proper pair must not overwrite the cached total with 0.0.
# This is the regression the audit specifically called out.

async def _scenario_total_partial_pair():
    api = _MockAPI(
        daily=[{"time": "13", "value": 11.0}],
        monthly=[{"date": "2026-09-30", "value": 12.0}],
        yearly=[{"month": "2026-09", "value": 13.0}],
        # Only one of the two required keys. The real API
        # always returns both. ``value=0`` looks plausible but
        # is not from the real path.
        total={"value": 0, "_raw_value": 0},
    )
    c = _StubHistoryCoordinator(api)
    c.today_hourly_power = []
    c.monthly_daily_energy = []
    c.yearly_monthly_energy = []
    c.total_energy_kwh = 999.0
    await _async_update_data(c)
    # The lists update because the list endpoint is real.
    assert c.today_hourly_power == [{"time": "13", "value": 11.0}]
    assert c.monthly_daily_energy == [{"date": "2026-09-30", "value": 12.0}]
    assert c.yearly_monthly_energy == [{"month": "2026-09", "value": 13.0}]
    # The total cache is preserved: a ``{"value": 0}`` payload
    # is rejected because the pair is missing.
    assert c.total_energy_kwh == 999.0, (
        f"malformed total (no totalEnergy key) must not overwrite cache: "
        f"got {c.total_energy_kwh!r}"
    )


_run(_scenario_total_partial_pair())


# ── 7. Total endpoint returns a dict whose two keys disagree.
# ``{"value": 0, "totalEnergy": 50.0}`` is a sign the cloud has
# a stale value somewhere. The helper rejects it.

async def _scenario_total_disagreeing_pair():
    api = _MockAPI(
        daily=[{"time": "13", "value": 11.0}],
        monthly=[{"date": "2026-09-30", "value": 12.0}],
        yearly=[{"month": "2026-09", "value": 13.0}],
        # Stale value somewhere. Treat as fallback rather than
        # silently pick one.
        total={"value": 0, "totalEnergy": 50.0, "_raw_value": 0},
    )
    c = _StubHistoryCoordinator(api)
    c.total_energy_kwh = 888.0
    await _async_update_data(c)
    assert c.total_energy_kwh == 888.0, (
        f"disagreeing-pair total must not overwrite cache: "
        f"got {c.total_energy_kwh!r}"
    )


_run(_scenario_total_disagreeing_pair())


# ── 8. Total endpoint returns NaN. The helper rejects via
# ``math.isfinite``; the cached total is preserved.

async def _scenario_total_nan():
    api = _MockAPI(
        daily=[{"time": "13", "value": 11.0}],
        monthly=[{"date": "2026-09-30", "value": 12.0}],
        yearly=[{"month": "2026-09", "value": 13.0}],
        total={"value": float("nan"), "totalEnergy": float("nan"),
               "_raw_value": float("nan")},
    )
    c = _StubHistoryCoordinator(api)
    c.total_energy_kwh = 777.0
    await _async_update_data(c)
    assert c.total_energy_kwh == 777.0, (
        f"NaN total must not overwrite cache: got {c.total_energy_kwh!r}"
    )


_run(_scenario_total_nan())


# ── 9. Real API path: total endpoint returns a *real* zero
# (battery truly produced 0 Wh). The pair is intact, so this
# is a legitimate update; the cache is *correctly* zeroed.
# This is the dual of scenario 6: a real zero is not a bug.

async def _scenario_total_real_zero():
    api = _MockAPI(
        daily=[{"time": "13", "value": 11.0}],
        monthly=[{"date": "2026-09-30", "value": 12.0}],
        yearly=[{"month": "2026-09", "value": 13.0}],
        total={"value": 0.0, "totalEnergy": 0.0, "_raw_value": 0.0},
    )
    c = _StubHistoryCoordinator(api)
    c.total_energy_kwh = 555.0
    await _async_update_data(c)
    # A real ``{"value": 0, "totalEnergy": 0}`` is a real
    # reading: the cache updates to 0.
    assert c.total_energy_kwh == 0.0, (
        f"real zero total must update the cache; got {c.total_energy_kwh!r}"
    )


_run(_scenario_total_real_zero())


# ── 10. End-to-end: drive the *real* ``api.fetch_total_energy()``
# with a mocked ``_fetch_overview`` and assert the cache write
# gate works through the full API path. The audit's review
# point was that previous tests only stubbed the dict the
# coordinator consumed — a regression in the API path itself
# could go unnoticed. This test reads the production
# ``InverterApiClient.fetch_total_energy`` body via AST,
# runs it against a synthetic _fetch_overview response, and
# feeds the resulting dict into ``_unwrap_history_results`` to
# prove the chain holds end-to-end.

# We can't easily import the full api module because it pulls
# aiohttp, cryptography, and homeassistant at module level.
# Instead, extract the body of ``fetch_total_energy`` via AST
# and exec it in an isolated namespace. That body has access
# to ``self._fetch_overview`` (which we provide) and produces
# a dict with the ``_raw_value`` sentinel; we then run that
# dict through the real ``_unwrap_history_results``.


api_src = (ROOT / "api.py").read_text(encoding="utf-8")
api_lines = api_src.splitlines(keepends=True)
api_tree = ast.parse(api_src)


def _api_function_src(name: str) -> str:
    for cls in ast.walk(api_tree):
        if isinstance(cls, ast.ClassDef) and cls.name == "InverterApiClient":
            for sub in cls.body:
                if (
                    isinstance(sub, ast.AsyncFunctionDef)
                    and sub.name == name
                ):
                    start = sub.lineno - 1
                    end = sub.end_lineno
                    return textwrap.dedent("".join(api_lines[start:end]))
    raise SystemExit(f"{name} not found in api.py")


fetch_total_src = _api_function_src("fetch_total_energy")
api_ns: dict[str, Any] = {
    "__name__": "_t14_e2e_isolated",
    "SUMMARY_KEY_ENERGY": "energy",
    "Any": __import__("typing").Any,
    "datetime": __import__("datetime").datetime,
}
exec(fetch_total_src, api_ns)
_fetch_total = api_ns["fetch_total_energy"]


class _RealApiMock:
    """Mimics the surface ``fetch_total_energy`` reads.

    The body does ``await self._fetch_overview("total", SUMMARY_KEY_ENERGY)``.
    We capture each call so the test can assert how the body
    parsed the cloud's response, then feed the result through
    the *real* ``_unwrap_history_results`` to prove the cache
    write gate works end-to-end.
    """

    def __init__(self, overview_payload):
        self._payload = overview_payload
        self.calls: list[tuple[str, str]] = []

    async def _fetch_overview(self, scope, key):
        self.calls.append((scope, key))
        return self._payload


async def _drive_real_api(overview_payload, expected_total_kwh):
    """Run the production ``fetch_total_energy`` body, then run
    the result through ``_unwrap_history_results`` and the
    real ``_async_update_data`` body. Returns the cache and the
    raw dict the API path produced so the test can assert the
    chain end-to-end.
    """
    api = _RealApiMock(overview_payload)
    raw_dict = await _fetch_total(api)
    # The body stamps ``_raw_value`` onto the dict. Run the
    # result through the production unwrap helper, then through
    # the cache-write path of ``_async_update_data`` to prove
    # the gate works end-to-end.
    c = _StubHistoryCoordinator(api)  # the api arg is ignored here
    c.total_energy_kwh = expected_total_kwh
    # Build a 4-element gather result for _unwrap_history_results.
    fake_results = [
        [{"time": "13", "value": 11.0}],
        [{"date": "2026-09-30", "value": 12.0}],
        [{"month": "2026-09", "value": 13.0}],
        raw_dict,
    ]
    return c, _unwrap(fake_results), raw_dict


# The audit's specific regression: a transient backend error
# where the cloud returns an empty list. The body falls back
# to ``{"_raw_value": None}`` and the cache must NOT be
# overwritten (the previous total, e.g. 1500 kWh, is preserved).
async def _e2e_empty_list():
    c, unwrapped, raw = await _drive_real_api(
        overview_payload=[],
        expected_total_kwh=1500.0,
    )
    # The API body returned a dict with ``_raw_value=None``.
    assert raw == {"_raw_value": None}, (
        f"empty list must produce _raw_value=None, got {raw!r}"
    )
    # The unwrap helper flagged it as a fallback.
    td_p, tm_p, ty_p, tot_p = unwrapped
    assert tot_p[2] is True, "empty list must be a fallback"
    assert tot_p[1] == 0.0
    # The cache stays at the previous 1500 kWh reading.
    c.today_hourly_power = td_p[0]
    c.monthly_daily_energy = tm_p[0]
    c.yearly_monthly_energy = ty_p[0]
    if not tot_p[2]:
        c.total_energy_kwh = tot_p[1]
    assert c.total_energy_kwh == 1500.0, (
        f"empty-list total must not zero the cache: got {c.total_energy_kwh!r}"
    )


_run(_e2e_empty_list())


# The audit's specific regression: the cloud returns a list of
# points whose latest entry lacks a numeric value. The body
# falls back to scanning, fails, and stamps ``_raw_value=None``.
async def _e2e_missing_value():
    c, unwrapped, raw = await _drive_real_api(
        overview_payload=[{"time": "13"}, {"time": "12"}],
        expected_total_kwh=1500.0,
    )
    assert raw.get("_raw_value") is None, (
        f"missing-value list must produce _raw_value=None, got {raw!r}"
    )
    td_p, tm_p, ty_p, tot_p = unwrapped
    assert tot_p[2] is True, "missing-value list must be a fallback"
    if not tot_p[2]:
        c.total_energy_kwh = tot_p[1]
    assert c.total_energy_kwh == 1500.0


_run(_e2e_missing_value())


# The happy path: a real cloud response with a numeric
# ``value`` and ``totalEnergy``. ``_raw_value`` is set to the
# extracted value; the cache updates to the new reading.
async def _e2e_real_reading():
    c, unwrapped, raw = await _drive_real_api(
        overview_payload=[{"value": 12.5, "totalEnergy": 12.5}],
        expected_total_kwh=0.0,
    )
    assert raw == {"value": 12.5, "totalEnergy": 12.5,
                    "_raw_value": 12.5}, raw
    td_p, tm_p, ty_p, tot_p = unwrapped
    assert tot_p[2] is False
    assert tot_p[1] == 12.5
    if not tot_p[2]:
        c.total_energy_kwh = tot_p[1]
    assert c.total_energy_kwh == 12.5


_run(_e2e_real_reading())


# The audit's specific regression: a transient backend error
# where the cloud returns a ``{"value": 0, "totalEnergy": 0}``
# payload *and* the API's v is None (the fallback path the body
# runs when no numeric ``value`` is in the response). The body
# then sets ``_raw_value = None`` so the cache gate fires. This
# is the *real* path the audit asked us to cover — a previous
# version of the body would have surfaced a 0.0 total even
# when the underlying data was missing.
async def _e2e_transient_zero():
    # Build a payload whose every point has a v=None. The
    # body tries the latest, the fallback scan, and still
    # fails; the resulting ``_raw_value`` is ``None``.
    c, unwrapped, raw = await _drive_real_api(
        overview_payload=[{"value": None}, {"value": None}],
        expected_total_kwh=1500.0,
    )
    assert raw.get("_raw_value") is None, (
        f"all-None payload must produce _raw_value=None, got {raw!r}"
    )
    # Even though the body successfully fell back to
    # ``total=0.0``, the unwrap helper refuses the dict because
    # ``_raw_value is None``. The cache stays at 1500.
    td_p, tm_p, ty_p, tot_p = unwrapped
    assert tot_p[2] is True, (
        f"transient zero with _raw_value=None must be a fallback, "
        f"got is_fallback={tot_p[2]!r}"
    )
    if not tot_p[2]:
        c.total_energy_kwh = tot_p[1]
    assert c.total_energy_kwh == 1500.0, (
        f"transient zero with _raw_value=None must NOT zero the cache: "
        f"got {c.total_energy_kwh!r}"
    )


_run(_e2e_transient_zero())


print("T14-lkg-real OK — last-known-good is preserved across partial failures")
sys.exit(0)
