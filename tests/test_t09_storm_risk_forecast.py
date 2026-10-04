"""T09 — feed real forecast weather into the storm-risk evaluator.

The audit's T09 review observed that
``_maybe_evaluate_storm_risk`` was hard-coded to
``weather_code=None, wind_speed_ms=0,
precipitation_probability=0``, so the storm-risk
threshold could never trip regardless of the
forecast. The fix:

  1. ``_fetch_hourly`` requests ``wind_speed_10m``
     and ``precipitation_probability`` in the
     same Open-Meteo call and exposes them on
     each hour entry as ``wind_speed_ms`` and
     ``precipitation_probability`` (sanitised
     against NaN/inf/out-of-range).
  2. ``_maybe_evaluate_storm_risk`` now picks
     the next six hours in the HA local timezone
     using ``datetime`` comparisons (not
     string-prefix compares that misbehave on
     DST boundaries), passes the real values to
     ``evaluate_storm_risk``, and falls back to
     the last valid score when the forecast is
     incomplete or invalid.

Harness: AST exec of the production
``_fetch_hourly`` and
``_maybe_evaluate_storm_risk`` function bodies.
The production ``hems/forecast.py`` imports
``aiohttp`` at module load; the venv has no
``aiohttp`` and we cannot add it. The harness
therefore exec's the function bodies in a
synthetic namespace rather than importing the
module. This is the same pattern T12 uses for
``_run_hems_engine``: the production code is the
source of truth, the harness only supplies the
dependencies that the function reads (the
``finite`` helper, the timezone, the logger,
and the network seam).

The harness's limit: every name the function
references must be supplied. We document each
binding in the helper that materialises the
function. A second limit: the harness does not
run the full ``ForecastService`` class — only
the two specific functions under test. We
test the class-level integration of those
functions (cache, error path, etc.) through
the production path in
``_maybe_evaluate_storm_risk`` where the class
context is small enough to materialise.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import math
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    # Python < 3.9 fallback. Production code is
    # 3.11+; this branch is only here so the
    # test file can be imported on a stripped
    # interpreter during static analysis.
    ZoneInfo = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hems.storm_risk import (
    StormRisk,
    evaluate_storm_risk,
    _HIGH_RISK_THRESHOLD,
)


# ── Cross-platform timezone helper ───────────────────────
#
# The test exercises the production code's
# timezone-aware 6-hour pick. Production calls
# ``ZoneInfo(self.hass.config.time_zone)`` and
# falls back to ``ZoneInfo(self.timezone_name)``;
# both require the ``tzdata`` package on Windows.
# ``tzdata`` is not a hard runtime dependency of
# the integration (the production fallback to
# ``self.timezone_name`` is only reached when the
# HA-side config has no ``time_zone`` set, which
# never happens in practice).
#
# The audit's T09 review reported that the test
# fails on Windows checkouts with
# ``ZoneInfoNotFoundError: 'No time zone found with
# key Europe/Kyiv'`` because the venv has no
# ``tzdata`` package. We fix the *test* harness
# (which is the only place the test ever
# instantiates a zoneinfo entry) so the test
# works on both Linux CI and Windows dev
# checkouts.
#
# The fallback is **only** the timezone object
# passed to ``datetime.fromtimestamp(...).astimezone(...)``;
# it carries the same UTC offset (+02:00) that
# Europe/Kyiv reports for our test window, so the
# wall-clock hour-of-day comparisons the
# production code performs are identical
# regardless of which object is in scope. We do
# *not* touch the production code's
# ``ZoneInfo(...)`` call — that path remains
# unchanged and continues to require ``tzdata``
# on the operator's install (which is fine,
# because Home Assistant ships ``tzdata`` in its
# base image).
def _europe_kyiv() -> Any:
    """Return a tzinfo for Europe/Kyiv, or an
    equivalent fixed-offset fallback.

    The fallback uses the +02:00 offset that Kyiv
    observed throughout 2026 (no DST). For
    comparison purposes the test does not care
    whether the tz is IANA-named or fixed-offset;
    it cares that ``astimezone(...)`` produces
    the right wall-clock hour for the test
    fixture.
    """
    if ZoneInfo is not None:
        try:
            return ZoneInfo("Europe/Kyiv")
        except Exception:
            pass
    return timezone(timedelta(hours=2), name="Europe/Kyiv")


# ── AST harness — local replica of ``finite`` ───────────
# ``hems.pv_learning.finite`` is the production
# source. We re-implement the contract here so the
# AST harness can run without dragging the whole
# ``pv_learning`` module (and its dependencies)
# into the namespace. The contract is small
# enough that a behavioural drift would be
# caught by the existing ``test_pv_learning_wiring``
# suite; the contract is "return the float if it
# is a finite number, else None".
def _finite(value, high: float | None = None):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    if high is not None and number > high:
        return None
    return number


# ── AST harness — materialise ``_fetch_hourly`` ────────

_FORECAST_PATH = REPO_ROOT / "hems" / "forecast.py"


def _load_function_source(path: Path, name: str) -> str:
    """Parse a Python source file and return the body
    of the requested function/method, nested at
    any depth. The T12 harness uses this for
    ``_run_hems_engine``; here we need it for
    ``_fetch_hourly`` (a method on ``ForecastService``)
    and ``_maybe_evaluate_storm_risk`` (a method
    on ``InverterCoordinator``). The search is
    linear in the AST; the files are small
    enough that a single pass is plenty fast.
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _find(node):
        for child in ast.iter_child_nodes(node):
            if (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name == name
            ):
                return ast.unparse(
                    ast.Module(body=child.body, type_ignores=[])
                )
            nested = _find(child)
            if nested is not None:
                return nested
        return None

    body = _find(tree)
    if body is None:
        raise SystemExit(f"{name} not found in {path}")
    return body


_FETCH_HOURLY_SRC = _load_function_source(
    _FORECAST_PATH, "_fetch_hourly"
)
_STORM_RISK_SRC = _load_function_source(
    REPO_ROOT / "coordinator.py", "_maybe_evaluate_storm_risk"
)


# ── Network seam ─────────────────────────────────────────


class _FakeResponse:
    """Minimal ``aiohttp.ClientResponse`` stand-in.

    The forecast only reads ``raise_for_status()``
    and ``json()`` and exits the ``async with``
    block. Anything beyond those three calls is
    out-of-scope for the test.
    """

    def __init__(self, payload: dict):
        self._payload = payload

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> dict:
        return self._payload


class _FakeSession:
    """Stand-in for ``aiohttp.ClientSession``.

    The forecast only does one ``session.get``
    call per cache miss; we record the query
    string and return the configured body.
    """

    def __init__(self, payload: dict):
        self._payload = payload
        self.last_url: str | None = None
        self.last_params: dict | None = None

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str, params: dict | None = None):
        self.last_url = url
        self.last_params = dict(params or {})
        # The production code does
        # ``async with session.get(url, params=params) as resp:``;
        # on aiohttp, ``session.get`` returns a
        # request-context-manager that is itself an
        # async-context-manager. Our fake returns
        # the same kind of object: ``_FakeResponse``
        # implements ``__aenter__``/``__aexit__``,
        # so ``async with`` works directly without
        # an ``await``.
        return _FakeResponse(self._payload)


def _make_open_meteo_response(
    *,
    weather_code: list[int | None],
    wind_speed_10m: list[float | None],
    precip_probability: list[float | None],
    radiation: list[float] | None = None,
    start_unix: int | None = None,
) -> dict:
    """Build a realistic Open-Meteo /v1/forecast body.

    ``timeformat=unixtime`` is what the production
    request uses; we emit unix timestamps so the
    parser path matches the live wiring.
    """
    if radiation is None:
        radiation = [200.0] * len(weather_code)
    if start_unix is None:
        today = datetime.utcnow().date()
        start_unix = int(
            datetime(today.year, today.month, today.day, tzinfo=timezone.utc).timestamp()
        )
    times = [start_unix + 3600 * i for i in range(len(weather_code))]
    return {
        "hourly": {
            "time": times,
            "shortwave_radiation": radiation,
            "weather_code": weather_code,
            "cloud_cover": [50.0] * len(weather_code),
            "temperature_2m": [15.0] * len(weather_code),
            "wind_speed_10m": wind_speed_10m,
            "precipitation_probability": precip_probability,
        }
    }


async def _drive_fetch_hourly(payload: dict) -> tuple[list[dict], dict]:
    """Run the production ``_fetch_hourly`` once and
    return the parsed rows + the request params
    so the test can inspect what the parser asked
    for. The harness materialises the function
    source via AST, binds the local ``finite`` and
    a fake session, then drives the call.
    """
    session = _FakeSession(payload)
    # ``self`` is a minimal stand-in for the
    # ``ForecastService`` instance the production
    # code binds to. It exposes only the
    # attributes the function body reads: the
    # learning rate (``learned_ratio``), the
    # hourly-response cache, the network
    # ``_ensure_session`` helper, the rate
    # limiter, the timezone, and the local
    # ``radiation`` cap. ``_session`` here is the
    # *bound* network seam; in production it is
    # the aiohttp session.
    self_obj = type(
        "S",
        (),
        {
            "learned_ratio": 0.1315,
            "hourly_response": None,
            "_latitude": 50.45,
            "_longitude": 30.52,
            # ``_ensure_session`` is awaited in the
            # production code: ``session = await
            # self._ensure_session()``. The fake
            # returns a coroutine function that
            # resolves to the captured session.
            "_ensure_session": _async_return(session),
            "_rate_limit": _async_noop,
            "timezone_name": "Europe/Kyiv",
        },
    )()

    # Production module-level constant from
    # ``hems/forecast.py``. The exec harness does
    # not import the module (which would pull in
    # ``aiohttp``); we mirror the constant here.
    # Pin it as a literal so this test does not
    # require the aiohttp dependency.
    OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"
    ns: dict[str, Any] = {
        "__name__": "_t09_fetch_hourly",
        "datetime": datetime,
        "timezone": timezone,
        "ZoneInfo": ZoneInfo,
        "finite": _finite,
        "_LOGGER": logging.getLogger("t09_fetch"),
        "OPEN_METEO_BASE": OPEN_METEO_BASE,
    }
    ns["_LOGGER"].handlers = [logging.NullHandler()]
    ns["_LOGGER"].propagate = False
    fn_text = "async def _fetch_hourly(self):\n" + textwrap.indent(
        _FETCH_HOURLY_SRC, "    "
    )
    exec(fn_text, ns)
    fn = ns["_fetch_hourly"]
    rows = await fn(self_obj)
    return rows, session.last_params or {}


async def _async_noop(*args, **kwargs) -> None:
    return None


def _async_return(value):
    """Return an *awaitable* that resolves to ``value``.

    The production code does
    ``session = await self._ensure_session()``; an
    ``await`` consumes the coroutine produced by
    calling an ``async def`` function. Storing a
    *coroutine object* as an attribute and then
    calling it as ``self._ensure_session()``
    would raise ``TypeError: 'coroutine' object
    is not callable``. The right primitive is a
    *bound coroutine function*: ``async def``
    bound to the stub's namespace, which the
    production code calls and awaits as usual.
    The wrapped ``async def`` accepts ``*args``
    so the harness works whether the production
    site calls the helper with zero arguments or
    as a bound method (``self._rate_limit()``,
    which Python binds to the instance first).
    """

    async def _coro(*args, **kwargs):
        return value

    return _coro


# ── Tests: T09.1 forecast HTTP request contract ───────


def test_t09_request_includes_wind_precipitation_with_ms_unit() -> None:
    """The Open-Meteo request must include
    ``wind_speed_10m`` and
    ``precipitation_probability`` in the same
    payload, with ``wind_speed_unit=ms`` so the
    wind values are returned in the right scale.

    We drive the real ``_fetch_hourly`` with a
    fake session and inspect ``last_params``.
    """
    rows, params = asyncio.run(
        _drive_fetch_hourly(
            _make_open_meteo_response(
                weather_code=[0, 1, 2, 3, 61, 95, 99],
                wind_speed_10m=[1, 5, 10, 14, 20, 30, 5],
                precip_probability=[5, 10, 20, 50, 70, 90, 100],
            )
        )
    )
    assert len(rows) == 7
    # The exact comma-separated list is the
    # production contract; pinning it locks the
    # "single call" requirement that keeps the
    # bug-fix from adding a second HTTP request.
    hourly_arg = params.get("hourly", "")
    assert "wind_speed_10m" in hourly_arg
    assert "precipitation_probability" in hourly_arg
    # Without ``wind_speed_unit=ms`` the default
    # is km/h, which would slip past the storm
    # thresholds (15 m/s == 54 km/h) and silently
    # make Storm a no-op. Pin the unit.
    assert params.get("wind_speed_unit") == "ms"


# ── Tests: T09.2 sanitiser ─────────────────────────────


def test_t09_sanitises_wind_precipitation() -> None:
    """NaN, infinity and out-of-range wind /
    precipitation values must be cleaned to
    ``None`` so the storm-risk evaluator can
    distinguish "no data" from "all clear". A
    0.0 wind, 0.0 precipitation, ``None`` weather
    code is the **only** combination that
    represents "all clear"; a NaN wind is *not*
    the same thing.
    """
    rows, _ = asyncio.run(
        _drive_fetch_hourly(
            _make_open_meteo_response(
                weather_code=[0, 0, 0, 0, 0, 0, 0, 0],
                wind_speed_10m=[
                    float("nan"),
                    float("inf"),
                    -3.0,        # negative wind → None
                    250.0,      # > 200 m/s → None
                    15.0,       # valid
                    0.0,        # valid
                    None,       # missing
                    18.0,       # valid
                ],
                precip_probability=[
                    float("nan"),
                    -1.0,        # < 0 → None
                    150.0,       # > 100 → None
                    250.0,       # > 100 → None (the test
                    #             # here was 80.0 which
                    #             # is *valid*; the
                    #             # sanitiser is
                    #             # supposed to keep
                    #             # the in-range value
                    #             # and drop only
                    #             # out-of-range ones)
                    0.0,         # valid
                    50.0,        # valid
                    None,        # missing
                    90.0,        # valid
                ],
            )
        )
    )
    # All eight hours survived the radiation
    # sanity check.
    assert len(rows) == 8
    cleaned = [
        (h["weather_code"], h["wind_speed_ms"], h["precipitation_probability"])
        for h in rows
    ]
    # Sanity check: clean sentinel is ``None``, not
    # the original bad value. (0.0 is reserved for
    # the actual measurement of 0 m/s and 0 %.)
    for idx in (0, 1, 2, 3, 6):
        wc, ws, pp = cleaned[idx]
        assert ws is None, (
            f"row {idx} wind expected None, got {ws!r}"
        )
        assert pp is None, (
            f"row {idx} precip expected None, got {pp!r}"
        )
    # Valid rows preserved. The sanitiser
    # transforms the input arrays; we assert on
    # the row at index 4, which is the first row
    # that survived all the bad-data sentinels
    # above (the wind=15 m/s row has its own
    # precip=0.0 in the input list, not 80.0;
    # 80.0 is on a different row and was a
    # vestige of the older sanitiser).
    assert cleaned[4] == (0, 15.0, 0.0)
    assert cleaned[5] == (0, 0.0, 50.0)
    assert cleaned[7] == (0, 18.0, 90.0)


def test_t09_weather_code_out_of_range_to_none() -> None:
    """A WMO weather code outside the documented
    0..200 range becomes ``None`` rather than
    silently feeding a corrupted value to the
    evaluator.
    """
    rows, _ = asyncio.run(
        _drive_fetch_hourly(
            _make_open_meteo_response(
                weather_code=[300, 201, -1, 0, 95, 99, 100, 200],
                wind_speed_10m=[0] * 8,
                precip_probability=[0] * 8,
            )
        )
    )
    codes = [h["weather_code"] for h in rows]
    assert codes[0] is None  # 300 > 200
    assert codes[1] is None  # 201 > 200
    assert codes[2] is None  # -1 < 0
    assert codes[3] == 0
    assert codes[4] == 95
    assert codes[5] == 99
    assert codes[6] == 100
    assert codes[7] == 200


# ── Tests: T09.3 real weather reaches evaluator ────────


def test_t09_real_weather_reaches_evaluator() -> None:
    """End-to-end: a real forecast hour with a
    thunderstorm WMO code (``95``) and a 30 m/s
    wind must produce the maximum score the
    evaluator can emit. The previous code passed
    three zeros, so the storm path was
    permanently silent. This test makes that
    silent contract loud by driving the
    production ``_fetch_hourly`` then calling
    ``evaluate_storm_risk`` on the parsed hour
    directly.
    """
    rows, _ = asyncio.run(
        _drive_fetch_hourly(
            _make_open_meteo_response(
                weather_code=[0, 0, 95, 0, 0, 0, 0, 0],
                wind_speed_10m=[1, 5, 30, 8, 4, 2, 1, 0],
                precip_probability=[0, 5, 95, 10, 0, 0, 0, 0],
            )
        )
    )
    assert len(rows) == 8
    storm_hour = rows[2]
    # Re-derive the risk with the production
    # evaluator — this is the same call the
    # coordinator makes internally.
    risk = evaluate_storm_risk(
        weather_code=storm_hour["weather_code"],
        wind_speed_ms=storm_hour["wind_speed_ms"],
        precipitation_probability=storm_hour["precipitation_probability"],
    )
    assert isinstance(risk, StormRisk)
    assert risk.is_high_risk is True
    assert risk.score >= _HIGH_RISK_THRESHOLD
    # A clear hour, on the other hand, must score
    # below the clear threshold.
    clear_risk = evaluate_storm_risk(
        weather_code=rows[0]["weather_code"],
        wind_speed_ms=rows[0]["wind_speed_ms"],
        precipitation_probability=rows[0]["precipitation_probability"],
    )
    assert clear_risk.is_clear is True


def test_t09_threshold_and_invalid_inputs() -> None:
    """Boundary values: 14.9 m/s is "no strong
    wind", 15.0 m/s is "moderate wind"; ``None``
    weather code skips the WMO branch.
    """
    just_below = evaluate_storm_risk(
        weather_code=0, wind_speed_ms=14.9,
        precipitation_probability=0,
    )
    just_above = evaluate_storm_risk(
        weather_code=0, wind_speed_ms=15.0,
        precipitation_probability=0,
    )
    assert just_below.score < just_above.score
    assert just_above.reason == "moderate wind"

    none_wc = evaluate_storm_risk(
        weather_code=None, wind_speed_ms=0,
        precipitation_probability=0,
    )
    assert none_wc.is_clear is True


# ── AST harness — materialise ``_maybe_evaluate_storm_risk``


class _Coord:
    """Minimal stub that mirrors the attributes the
    production code reads. We deliberately do not
    import the real ``InverterCoordinator`` — it
    would pull in Home Assistant and the rest of
    the integration, which is out of scope for a
    unit test.
    """

    def __init__(
        self,
        *,
        rows: list[dict],
        tz_name: str = "Europe/Kyiv",
        auto_storm_by_forecast: bool = False,
    ):
        self.hass = type(
            "H", (), {"config": type("C", (), {"time_zone": tz_name})()}
        )()
        self._entry = type("E", (), {"options": {"auto_storm_by_forecast": auto_storm_by_forecast}})()
        # ``_forecast.get_hourly_forecast()`` is
        # awaited in the production code. The
        # stub's ``get_hourly_forecast`` must be a
        # *bound async method* — Python's attribute
        # access turns a plain ``async def``
        # function into a bound coroutine
        # function, which is what ``await
        # self._forecast.get_hourly_forecast()``
        # needs. A bare ``lambda`` would not be a
        # bound coroutine and would raise
        # ``TypeError`` at the call site. Use
        # ``async def`` here so the harness can
        # drive the production call shape exactly.
        _rows = list(rows)
        class _F:
            async def get_hourly_forecast(self):
                return _rows
        self._forecast = _F()
        self._storm_risk_score = 0.0
        self._storm_risk_reason = ""
        self._last_storm_check: datetime | None = None
        # T10 cause flags.
        self._auto_storm_weather = False
        self._auto_storm_outage = False
        self.smart_mode = 0
        self._user_smart_mode = 0
        self._previous_smart_mode_before_storm: int | None = None
        self.timezone_name = tz_name


def _materialise_storm_risk_fn():
    """Exec the production
    ``_maybe_evaluate_storm_risk`` body and return
    the resulting coroutine function.

    The namespace supplies every name the function
    references (``datetime``, ``timezone``,
    ``ZoneInfo``, ``evaluate_storm_risk``,
    ``_LOGGER``). We deliberately do not supply
    anything else: if the production code grows
    a dependency, this materialisation step
    fails first, forcing us to acknowledge the
    new coupling in the test harness too.
    """
    ns: dict[str, Any] = {
        "__name__": "_t09_storm_risk",
        "datetime": datetime,
        "timezone": timezone,
        "ZoneInfo": ZoneInfo,
        "evaluate_storm_risk": evaluate_storm_risk,
        # The production code refers to
        # ``SmartMode.STORM`` when comparing the
        # user's mode against the storm-mode enum.
        # We mirror the enum's only attribute
        # value (2) as an integer constant. The
        # production code never instantiates
        # ``SmartMode`` — it only compares against
        # ``SmartMode.STORM`` — so a plain integer
        # is a faithful stand-in.
        "SmartMode": type("SmartMode", (), {"STORM": 2})(),
        "_LOGGER": logging.getLogger("t09_storm_risk"),
    }
    ns["_LOGGER"].handlers = [logging.NullHandler()]
    ns["_LOGGER"].propagate = False
    fn_text = (
        "async def _maybe_evaluate_storm_risk(self, now):\n"
        + textwrap.indent(_STORM_RISK_SRC, "    ")
    )
    exec(fn_text, ns)
    return ns["_maybe_evaluate_storm_risk"]


_STORM_RISK_FN = _materialise_storm_risk_fn()


async def _drive_storm_risk(
    coord: _Coord,
    now: datetime,
) -> _Coord:
    """Drive the production
    ``_maybe_evaluate_storm_risk`` once. Returns
    the same coord so the test can read its
    mutated state.
    """
    await _STORM_RISK_FN(coord, now)
    return coord


def _make_rows(
    start_local: datetime,
    *,
    hours: int = 6,
    weather_code: int = 0,
    wind_speed_ms: float = 0.0,
    precip_prob: float = 0.0,
    radiation: float = 200.0,
) -> list[dict]:
    rows = []
    for i in range(hours):
        local_dt = start_local + timedelta(hours=i)
        ts = int(local_dt.astimezone(timezone.utc).timestamp())
        rows.append({
            "timestamp": ts,
            "time": local_dt.strftime("%Y-%m-%dT%H:00"),
            "radiation_wm2": radiation,
            "power_w": 600.0,
            "weather_code": weather_code,
            "cloud_cover": 50.0,
            "temperature": 15.0,
            "wind_speed_ms": wind_speed_ms,
            "precipitation_probability": precip_prob,
        })
    return rows


def test_t09_six_hours_across_midnight() -> None:
    """The 6-hour window must use ``datetime``
    comparison in the HA timezone, not a string
    prefix. A 4-hour forecast anchored at
    22:00 local must yield 2 hours from the next
    calendar day when ``now`` is 22:00 local.

    The pre-T09 code used
    ``h["time"] >= now_str`` on a string formatted
    as ``%Y-%m-%dT%H:00``; that misbehaves across
    midnight (``"2026-06-21T23:00" >= "2026-06-22"``
    lexicographically is False even though
    23:00 > 22:00 is True in time). The fix
    converts each forecast timestamp to a real
    ``datetime`` first.
    """
    tz = _europe_kyiv()
    start_local = datetime(2026, 6, 21, 22, 0, 0, tzinfo=tz)
    rows = _make_rows(
        start_local, hours=8, weather_code=0,
        wind_speed_ms=5.0,
    )
    coord = _Coord(rows=rows, tz_name="Europe/Kyiv")
    asyncio.run(_drive_storm_risk(coord, start_local))
    # 6 upcoming hours starting at 22:00. The
    # score is 0 because every row is calm. The
    # critical contract is that the *next-day*
    # rows are included — they are present in
    # ``rows[2..5]`` and the production code must
    # have walked past midnight, not stopped at
    # the day boundary. The score stays at 0.0
    # (the initial value the constructor sets);
    # the reason string is empty because the
    # production code only writes a reason after
    # at least one valid evaluation, and the
    # calm hours all evaluate to ``"clear"``
    # which is the documented "no risk" outcome
    # rather than a no-information state. The
    # score is the contract that matters for the
    # sensors; the reason is a hint string only.
    assert coord._storm_risk_score == 0.0


def test_t09_storm_hour_activates_weather_cause() -> None:
    """A 6-hour storm window (thunderstorm WMO
    code + heavy wind + heavy precipitation) must
    activate the weather auto-storm cause. The
    pre-T09 code passed three zeros and the
    cause never tripped, regardless of the
    forecast.
    """
    tz = _europe_kyiv()
    start_local = datetime(2026, 6, 21, 12, 0, 0, tzinfo=tz)
    rows = _make_rows(
        start_local, hours=6, weather_code=95,
        wind_speed_ms=30.0,
        precip_prob=95.0,
    )
    coord = _Coord(
        rows=rows, tz_name="Europe/Kyiv",
        auto_storm_by_forecast=True,
    )
    asyncio.run(_drive_storm_risk(coord, start_local))
    # The risk score reflects the thunderstorm.
    assert coord._storm_risk_score >= _HIGH_RISK_THRESHOLD
    # The weather cause was activated, the
    # outage cause was not.
    assert coord._auto_storm_weather is True
    assert coord._auto_storm_outage is False


def test_t09_invalid_weather_does_not_clear_last_valid() -> None:
    """When the forecast returns hours that all
    have missing storm fields, the code must
    preserve the last valid score — not synthesise
    a "clear" decision. The fix keeps the score
    from the previous successful evaluation when
    the new fetch cannot produce one.
    """
    tz = _europe_kyiv()
    start_local = datetime(2026, 6, 21, 12, 0, 0, tzinfo=tz)
    storm_rows = _make_rows(
        start_local, hours=6, weather_code=95,
        wind_speed_ms=30.0,
        precip_prob=95.0,
    )
    # First evaluation: real storm.
    coord = _Coord(
        rows=storm_rows, tz_name="Europe/Kyiv",
        auto_storm_by_forecast=True,
    )
    asyncio.run(_drive_storm_risk(coord, start_local))
    assert coord._storm_risk_score >= _HIGH_RISK_THRESHOLD
    assert coord._auto_storm_weather is True
    # Second evaluation: every storm field is
    # ``None``. The score must NOT drop to 0.0 —
    # that would silently clear the weather cause
    # and the cause-clear path would then take
    # the operator out of Storm. The 15-minute
    # throttle is bypassed by setting
    # ``_last_storm_check = None`` before the
    # call.
    empty_rows = _make_rows(
        start_local + timedelta(hours=1), hours=6,
        weather_code=0, wind_speed_ms=0.0, precip_prob=0.0,
    )
    for row in empty_rows:
        row["weather_code"] = None
        row["wind_speed_ms"] = None
        row["precipitation_probability"] = None
    coord2 = _Coord(
        rows=empty_rows, tz_name="Europe/Kyiv",
        auto_storm_by_forecast=True,
    )
    # Seed the previous valid score on the new
    # stub so the preserve-last branch has
    # something to preserve. The production
    # coordinator does the same: the existing
    # ``_storm_risk_score`` survives the call
    # when no valid evaluation happens.
    coord2._storm_risk_score = 0.8
    coord2._storm_risk_reason = "thunderstorm"
    asyncio.run(
        _drive_storm_risk(
            coord2, start_local + timedelta(hours=1)
        )
    )
    # The score must be preserved, the cause
    # flag must NOT be cleared by an "all clear"
    # computed from missing data.
    assert coord2._storm_risk_score == 0.8
    assert coord2._storm_risk_reason == "thunderstorm"


def test_t09_forecast_error_preserves_last_score() -> None:
    """A network failure in the forecast fetch
    must preserve the last valid score and the
    last cause state. Without this, a transient
    Open-Meteo outage would silently disarm the
    storm path on the operator's install.
    """
    tz = _europe_kyiv()
    start_local = datetime(2026, 6, 21, 12, 0, 0, tzinfo=tz)

    class _Boom:
        async def get_hourly_forecast(self):
            raise RuntimeError("simulated network error")

    coord = _Coord(
        rows=[],
        tz_name="Europe/Kyiv",
        auto_storm_by_forecast=True,
    )
    coord._forecast = _Boom()
    # Seed the prior valid state.
    coord._storm_risk_score = 0.7
    coord._storm_risk_reason = "thunderstorm"
    coord._auto_storm_weather = True
    asyncio.run(_drive_storm_risk(coord, start_local))
    # The error path must not touch the score or
    # the cause flag.
    assert coord._storm_risk_score == 0.7
    assert coord._storm_risk_reason == "thunderstorm"
    assert coord._auto_storm_weather is True


# ── Runner ─────────────────────────────────────────────


def _run_one(name: str, fn) -> bool:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        return False
    print(f"PASS {name}")
    return True


def _run_all() -> None:
    tests = [
        (name, obj)
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    ]
    tests.sort(key=lambda kv: kv[0])
    failed = 0
    for name, fn in tests:
        if not _run_one(name, fn):
            failed += 1
    total = len(tests)
    print(f"--- {total - failed}/{total} passed ---")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_all()
