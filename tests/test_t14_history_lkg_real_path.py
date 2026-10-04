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
import datetime
import logging
import math
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
coord_src = (ROOT / "coordinator.py").read_text(encoding="utf-8")
coord_lines = coord_src.splitlines(keepends=True)
coord_tree = ast.parse(coord_src)


def _function_src(name: str, klass: str | None = None) -> str:
    """Extract a method body. If ``klass`` is given, only return
    the function whose parent class matches; otherwise any."""
    for cls in ast.walk(coord_tree):
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
                    return textwrap.dedent("".join(coord_lines[start:end]))
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
    # The module-level import in ``coordinator.py`` is
    # ``from datetime import datetime, timedelta, timezone``
    # — ``datetime`` is the *class*, not the module. The
    # previous test passed the module and the body then
    # failed at ``datetime.now()``. Mirror the production
    # imports here.
    "datetime": __import__("datetime").datetime,
    "timedelta": __import__("datetime").timedelta,
    "timezone": __import__("datetime").timezone,
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


# Real-API mock used by the end-to-end scenarios below. The
# ``fetch_total_energy`` method runs the *real* production
# body — extracted via AST so we do not need to import the
# whole ``api`` module, which pulls ``aiohttp`` and
# ``homeassistant``. Other fetchers return the values the
# test passed in (they are not under audit here).
class _RealApiMock(_MockAPI):
    def __init__(self, daily, monthly, yearly, overview_payload):
        super().__init__(daily, monthly, yearly, total=None)
        self._overview_payload = overview_payload

    async def fetch_total_energy(self):
        self.calls.append("total")
        # The body of ``InverterApiClient.fetch_total_energy``
        # uses ``await self._fetch_overview("total",
        # SUMMARY_KEY_ENERGY)``. Our mock supplies that.
        return await _fetch_total(self)

    async def _fetch_overview(self, scope, key):
        self.calls.append(f"fetch_overview:{scope}:{key}")
        return self._overview_payload


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
    """Mimics the surface ``fetch_total_energy`` reads AND runs
    the *real* ``_async_update_data`` body end-to-end.

    The body of ``InverterApiClient.fetch_total_energy`` does
    ``await self._fetch_overview("total", SUMMARY_KEY_ENERGY)``;
    our mock supplies that. We then drive the production
    ``HistoryCoordinator._async_update_data`` (extracted via
    AST for isolation) and assert on the cache it set.
    """

    def __init__(self, daily, monthly, yearly, overview_payload):
        self._daily = daily
        self._monthly = monthly
        self._yearly = yearly
        self._overview_payload = overview_payload
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
        # Run the production body.
        return await _fetch_total(self)

    async def _fetch_overview(self, scope, key):
        self.calls.append(f"fetch_overview:{scope}:{key}")
        return self._overview_payload


def _run_async_update(api, daily, monthly, yearly, seed_total):
    """Drive the *real* ``_async_update_data`` body on a stub
    HistoryCoordinator and return the resulting ``_StubHistoryCoordinator``
    (with the cache it set, ready for assertions).

    We can't import the coordinator module because it pulls
    ``homeassistant``. Instead, extract the body of
    ``_async_update_data`` via AST, exec it in an isolated
    namespace, and bind it to a stub. The stub provides
    ``_safe_total`` (via exec of the helper), the four
    attributes the body writes, and the ``api`` instance.
    """
    coord_src = (ROOT / "coordinator.py").read_text(encoding="utf-8")
    coord_lines = coord_src.splitlines(keepends=True)
    coord_tree = ast.parse(coord_src)

    # Find the HistoryCoordinator class and its
    # _async_update_data method body.
    for cls in coord_tree.body:
        if (
            isinstance(cls, ast.ClassDef)
            and cls.name == "HistoryCoordinator"
        ):
            for sub in cls.body:
                if (
                    isinstance(sub, ast.AsyncFunctionDef)
                    and sub.name == "_async_update_data"
                ):
                    start = sub.lineno - 1
                    end = sub.end_lineno
                    body_src = textwrap.dedent(
                        "".join(coord_lines[start:end])
                    )
                    break
            else:
                raise SystemExit(
                    "_async_update_data not found in HistoryCoordinator"
                )
            break
    else:
        raise SystemExit("HistoryCoordinator not found in coordinator.py")

    # Build the namespace the body expects. The body calls
    # ``self.api.fetch_*`` and reads/writes ``self.total_energy_kwh``,
    # ``self.today_hourly_power``, ``self.monthly_daily_energy``,
    # ``self.yearly_monthly_energy`` and ``self._safe_total``.
    ns: dict[str, Any] = {
        "__name__": "_t14_e2e_real_async",
        "asyncio": asyncio,
        "math": math,
        # The body of ``_async_update_data`` (and the in-line
        # ``_unwrap_history_results``) calls ``datetime.now()``
        # and ``datetime.date()``. The module-level import in
        # ``coordinator.py`` is ``from datetime import
        # datetime, timedelta, timezone`` — i.e. ``datetime``
        # is the *class*, not the module. We must mirror that
        # in the isolated namespace or the body fails with
        # ``module 'datetime' has no attribute 'now'``.
        "datetime": __import__("datetime").datetime,
        "timedelta": __import__("datetime").timedelta,
        "timezone": __import__("datetime").timezone,
        "Any": __import__("typing").Any,
        "_LOGGER": logging.getLogger("t14_e2e_real"),
    }
    # ``_async_update_data`` calls ``self._unwrap_history_results``,
    # which in turn defines ``_safe_total`` as a nested
    # function. The body only sees names that exist in *its*
    # closure, so we must exec both methods into the same
    # namespace and bind them as methods on the stub. The
    # nested ``_safe_total`` then resolves through the
    # unwrap function's closure at call time.
    unwrap_src_local = _function_src(
        "_unwrap_history_results", "HistoryCoordinator"
    )
    async_update_src_local = _function_src(
        "_async_update_data", "HistoryCoordinator"
    )
    exec(unwrap_src_local, ns)
    exec(async_update_src_local, ns)
    _unwrap_local = ns["_unwrap_history_results"]
    _async_update_data_local = ns["_async_update_data"]

    class _StubHistoryCoordinator:
        def __init__(self, api):
            self.api = api
            self.today_hourly_power = None
            self.monthly_daily_energy = None
            self.yearly_monthly_energy = None
            self.total_energy_kwh = seed_total
            self._last_total_fallback = False
            # Bind the production methods to this instance.
            # ``_async_update_data`` is a coroutine; we wrap it
            # so ``await self._async_update_data()`` works as
            # in the live coordinator. ``_unwrap_history_results``
            # is a staticmethod on the live class — its AST
            # signature is ``def _unwrap_history_results(results)``
            # with no ``self``. We bind it as a staticmethod
            # on the stub so calls like
            # ``self._unwrap_history_results(results)`` route to
            # ``_unwrap_local(results)`` without an extra
            # ``self`` argument. (MethodType would prepend
            # ``self`` and break the call signature.)
            import types as _types
            self._async_update_data = _types.MethodType(
                _async_update_data_local, self
            )
            self._unwrap_history_results = staticmethod(
                _unwrap_local
            )

        def _safe_total(self, value, label):
            # The real coordinator passes the result and a label.
            # The body of ``_unwrap_history_results`` defines
            # ``_safe_total`` as a nested function that takes
            # both arguments; we call it through the closure
            # the production function holds. Because the AST
            # exec left ``_safe_total`` inside the closure of
            # ``_unwrap_local``, we cannot import it directly;
            # the *body* of ``_async_update_data`` does
            # ``total_pair = _safe_total(total_raw, "total")``
            # which resolves through the closure automatically.
            # So this stub method is intentionally a no-op —
            # the real call goes through the body.
            raise NotImplementedError(
                "_safe_total is a nested function inside "
                "_unwrap_history_results and must be reached "
                "via self._unwrap_history_results"
            )

    c = _StubHistoryCoordinator(api)
    return c


# The audit's specific regression: a transient backend error
# where the cloud returns an empty list. The body falls back
# to ``{"_raw_value": None}`` and the cache must NOT be
# overwritten (the previous total, e.g. 1500 kWh, is preserved).
async def _e2e_empty_list():
    daily = [{"time": "13", "value": 11.0}]
    monthly = [{"date": "2026-09-30", "value": 12.0}]
    yearly = [{"month": "2026-09", "value": 13.0}]
    api = _RealApiMock(daily, monthly, yearly, overview_payload=[])
    c = _run_async_update(api, daily, monthly, yearly, seed_total=1500.0)
    await c._async_update_data()
    # The cache must not have been overwritten.
    assert c.total_energy_kwh == 1500.0, (
        f"empty-list total must not zero the cache: "
        f"got {c.total_energy_kwh!r}"
    )


_run(_e2e_empty_list())


# The audit's specific regression: the cloud returns a list of
# points whose latest entry lacks a numeric value. The body
# falls back to scanning, fails, and stamps ``_raw_value=None``.
async def _e2e_missing_value():
    daily = [{"time": "13", "value": 11.0}]
    monthly = [{"date": "2026-09-30", "value": 12.0}]
    yearly = [{"month": "2026-09", "value": 13.0}]
    api = _RealApiMock(
        daily, monthly, yearly,
        overview_payload=[{"time": "13"}, {"time": "12"}],
    )
    c = _run_async_update(api, daily, monthly, yearly, seed_total=1500.0)
    await c._async_update_data()
    assert c.total_energy_kwh == 1500.0


_run(_e2e_missing_value())


# The audit's specific regression: a transient backend error
# where the cloud returns a list whose every point has a
# non-numeric ``value`` (e.g. ``"bad"``). The body previously
# collapsed the parse failure to ``total=0.0`` while
# preserving the bad string as ``_raw_value``, which the
# cache write gate then accepted. The fix stamps
# ``_raw_value=None`` on parse failure, so the cache stays
# at the previous reading.
async def _e2e_bad_string():
    daily = [{"time": "13", "value": 11.0}]
    monthly = [{"date": "2026-09-30", "value": 12.0}]
    yearly = [{"month": "2026-09", "value": 13.0}]
    api = _RealApiMock(
        daily, monthly, yearly,
        overview_payload=[{"value": "bad", "totalEnergy": "bad"}],
    )
    c = _run_async_update(api, daily, monthly, yearly, seed_total=1500.0)
    await c._async_update_data()
    assert c.total_energy_kwh == 1500.0, (
        f"non-numeric 'bad' value must NOT zero the cache: "
        f"got {c.total_energy_kwh!r}"
    )


_run(_e2e_bad_string())


# The audit's specific regression: a list where every point
# has ``value=None``. The body falls back to scanning, finds
# no numeric, stamps ``_raw_value=None``, and the cache gate
# fires.
async def _e2e_all_none():
    daily = [{"time": "13", "value": 11.0}]
    monthly = [{"date": "2026-09-30", "value": 12.0}]
    yearly = [{"month": "2026-09", "value": 13.0}]
    api = _RealApiMock(
        daily, monthly, yearly,
        overview_payload=[{"value": None}, {"value": None}],
    )
    c = _run_async_update(api, daily, monthly, yearly, seed_total=1500.0)
    await c._async_update_data()
    assert c.total_energy_kwh == 1500.0


_run(_e2e_all_none())


# The happy path: a real cloud response with a numeric
# ``value`` and ``totalEnergy``. ``_raw_value`` is set to the
# extracted value; the cache updates to the new reading.
async def _e2e_real_reading():
    daily = [{"time": "13", "value": 11.0}]
    monthly = [{"date": "2026-09-30", "value": 12.0}]
    yearly = [{"month": "2026-09", "value": 13.0}]
    api = _RealApiMock(
        daily, monthly, yearly,
        overview_payload=[{"value": 12.5, "totalEnergy": 12.5}],
    )
    c = _run_async_update(api, daily, monthly, yearly, seed_total=0.0)
    await c._async_update_data()
    assert c.total_energy_kwh == 12.5, (
        f"real reading must update the cache: "
        f"got {c.total_energy_kwh!r}"
    )


_run(_e2e_real_reading())


# The audit's regression #2: ``value=0`` (a real cumulative
# zero) used to be silently swapped to ``totalEnergy`` by
# Python's ``or`` short-circuit. We now require ``value`` is
# genuinely missing (``is None``) before falling back, so a
# real ``value=0`` is treated as a real reading and the cache
# updates.
async def _e2e_real_zero():
    daily = [{"time": "13", "value": 11.0}]
    monthly = [{"date": "2026-09-30", "value": 12.0}]
    yearly = [{"month": "2026-09", "value": 13.0}]
    api = _RealApiMock(
        daily, monthly, yearly,
        overview_payload=[{"value": 0, "totalEnergy": 0}],
    )
    c = _run_async_update(api, daily, monthly, yearly, seed_total=42.0)
    await c._async_update_data()
    # Real 0 means the inverter's cumulative is 0; the cache
    # is overwritten to 0 (not preserved at the previous 42).
    assert c.total_energy_kwh == 0.0, (
        f"real cumulative zero must update the cache: "
        f"got {c.total_energy_kwh!r}"
    )


_run(_e2e_real_zero())


# A real ``value=0`` with a non-zero ``totalEnergy``: the
# previous ``or`` would have picked ``totalEnergy`` (because
# 0 is falsy), masking the real ``value=0``. The new body
# honors ``value`` because it is not ``None`` — and the
# ``_safe_total`` pair check then *rejects* this payload as
# a disagreement fallback (a real reading of 0.0 and 12.5
# cannot both be true). The cache stays at the previous
# reading.
async def _e2e_value_zero_total_mismatch():
    daily = [{"time": "13", "value": 11.0}]
    monthly = [{"date": "2026-09-30", "value": 12.0}]
    yearly = [{"month": "2026-09", "value": 13.0}]
    api = _RealApiMock(
        daily, monthly, yearly,
        overview_payload=[{"value": 0, "totalEnergy": 12.5}],
    )
    c = _run_async_update(api, daily, monthly, yearly, seed_total=1500.0)
    await c._async_update_data()
    # The pair disagrees, so the body stamps a fallback.
    # The cache must NOT be overwritten.
    assert c.total_energy_kwh == 1500.0, (
        f"disagreeing (value=0, totalEnergy=12.5) must not "
        f"update the cache: got {c.total_energy_kwh!r}"
    )


_run(_e2e_value_zero_total_mismatch())


# The audit's third follow-up: a payload where ``value`` is a
# real 0 but ``totalEnergy`` is missing or non-numeric. The
# previous API body masked the missing/non-numeric field with
# 0.0, producing ``{"value": 0, "totalEnergy": 0,
# "_raw_value": 0}`` — a placeholder pair the coordinator's
# ``_safe_total`` accepted as a real reading (both fields
# agree at 0, the raw sentinel is non-None). The cache was
# then overwritten with 0.0 even though the second field was
# garbage. The fix:
#   * the API body now stamps ``_pair_valid=False`` when
#     ``totalEnergy`` is missing or non-numeric and leaves
#     ``totalEnergy`` as ``None`` in the returned dict,
#   * ``_safe_total`` treats a ``False`` pair as a fallback
#     even when the numeric ``value`` parses cleanly.
async def _e2e_value_zero_total_missing():
    daily = [{"time": "13", "value": 11.0}]
    monthly = [{"date": "2026-09-30", "value": 12.0}]
    yearly = [{"month": "2026-09", "value": 13.0}]
    api = _RealApiMock(
        daily, monthly, yearly,
        # ``totalEnergy`` key is absent entirely.
        overview_payload=[{"value": 0}],
    )
    c = _run_async_update(api, daily, monthly, yearly, seed_total=1500.0)
    await c._async_update_data()
    assert c.total_energy_kwh == 1500.0, (
        f"value=0 with missing totalEnergy must NOT zero the "
        f"cache: got {c.total_energy_kwh!r}"
    )


_run(_e2e_value_zero_total_missing())


async def _e2e_value_zero_total_bad_string():
    daily = [{"time": "13", "value": 11.0}]
    monthly = [{"date": "2026-09-30", "value": 12.0}]
    yearly = [{"month": "2026-09", "value": 13.0}]
    api = _RealApiMock(
        daily, monthly, yearly,
        overview_payload=[{"value": 0, "totalEnergy": "bad"}],
    )
    c = _run_async_update(api, daily, monthly, yearly, seed_total=1500.0)
    await c._async_update_data()
    assert c.total_energy_kwh == 1500.0, (
        f"value=0 with non-numeric totalEnergy='bad' must NOT "
        f"zero the cache: got {c.total_energy_kwh!r}"
    )


_run(_e2e_value_zero_total_bad_string())


# The same logic when ``value`` parses to a non-zero number
# but ``totalEnergy`` is missing: a transient backend error
# must not cause the cache to revert to the parsed
# ``value``. The cache is preserved.
async def _e2e_value_present_total_missing():
    daily = [{"time": "13", "value": 11.0}]
    monthly = [{"date": "2026-09-30", "value": 12.0}]
    yearly = [{"month": "2026-09", "value": 13.0}]
    api = _RealApiMock(
        daily, monthly, yearly,
        overview_payload=[{"value": 12.5}],
    )
    c = _run_async_update(api, daily, monthly, yearly, seed_total=1500.0)
    await c._async_update_data()
    assert c.total_energy_kwh == 1500.0, (
        f"value=12.5 with missing totalEnergy must NOT update "
        f"the cache: got {c.total_energy_kwh!r}"
    )


_run(_e2e_value_present_total_missing())


# Sanity check: when both fields are present and agree at a
# real number, the new ``_pair_valid`` flag is True and the
# cache updates. This guards against an over-zealous fix
# that would reject every reading.
async def _e2e_pair_valid_updates_cache():
    daily = [{"time": "13", "value": 11.0}]
    monthly = [{"date": "2026-09-30", "value": 12.0}]
    yearly = [{"month": "2026-09", "value": 13.0}]
    api = _RealApiMock(
        daily, monthly, yearly,
        overview_payload=[{"value": 12.5, "totalEnergy": 12.5}],
    )
    c = _run_async_update(api, daily, monthly, yearly, seed_total=0.0)
    await c._async_update_data()
    assert c.total_energy_kwh == 12.5, (
        f"valid pair must update the cache: "
        f"got {c.total_energy_kwh!r}"
    )


_run(_e2e_pair_valid_updates_cache())


print("T14-lkg-real OK — last-known-good is preserved across partial failures")
sys.exit(0)
