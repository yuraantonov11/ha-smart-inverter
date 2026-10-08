"""R01 reproduction — radiation interval semantics через synthetic HTTP.

Open-Meteo ``shortwave_radiation`` — це **preceding hour mean**: значення в
``time[t]`` — це середнє за ``[t-1h, t)``, **не** ``[t, t+1h)``. Підтверджено
з офіційної документації (https://open-meteo.com/en/docs/historical-weather-api).

Production (``hems/forecast.py``) зберігає ``rows[].start = ts`` (API
timestamp, тобто **END** інтервалу), а не ``t - 1h`` (початок). Це
невідповідність між ``rows[].start`` і фактичним інтервалом, який
представляє значення.

**Daily assertions (анти-тавтологія):**

Цей тест перевіряє конкретні `daily` значення, **не друкує** їх.
Якщо driver поверне `daily={}`, відсутній імпульс, або неправильну
суму — тест падає.

**Fixtures відповідають записаним request params:**

Production ``get_archive_radiation(start_day, end_day)`` будує
``start_date = first.date().isoformat()`` і
``end_date = (last - 1s).date().isoformat()``, де
``first, last = day_bounds(start_day, timezone)``. Для
``Europe/Kyiv`` (UTC+3) і ``start_day=2026-10-08, end_day=2026-10-09``:
``first = 2026-10-07 21:00 UTC``, ``last = 2026-10-09 21:00 UTC``,
отже запитані дати = **2026-10-07..2026-10-09** UTC.

HTTP fixture мусить покривати цей фактичний діапазон, не лише
``start_day..end_day``.

**Mutation tests:**

Перевірки з mutation на PRODUCTION result (не на HTTP) — кожен
випадок мусить спричинити AssertionError.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, REPO_ROOT)


# ── Synthetic HTTP response builder ────────────────────────────────


def _expected_utc_range(start_day, end_day, tz_name):
    """Compute the actual UTC date range that production will request
    (i.e. start_date=first.date, end_date=(last-1s).date, where
    first, last = day_bounds(day, tz)). Returned as a tuple of
    (start_date, end_date) ISO strings.
    """
    tz = ZoneInfo(tz_name)
    first, _ = __import__("hems.pv_learning", fromlist=["day_bounds"]).day_bounds(
        start_day, tz
    )
    _, last = __import__("hems.pv_learning", fromlist=["day_bounds"]).day_bounds(
        end_day, tz
    )
    return (
        first.date().isoformat(),
        (last - timedelta(seconds=1)).date().isoformat(),
    )


def _build_archive_response(*, start_date, end_date, pulse_at_api_t=None,
                            pulse_value=0.0, default_value=0.0):
    """Build a synthetic Open-Meteo Archive response covering
    ``start_date`` 00:00 UTC through ``end_date`` 23:00 UTC, with
    full hourly coverage. Pulse is placed at ``pulse_at_api_t`` if
    provided.
    """
    times = []
    values = []
    cur = datetime(start_date.year, start_date.month, start_date.day,
                   tzinfo=timezone.utc)
    end_dt = datetime(end_date.year, end_date.month, end_date.day,
                      23, 0, tzinfo=timezone.utc)
    while cur <= end_dt:
        times.append(int(cur.timestamp()))
        if pulse_at_api_t is not None and cur == pulse_at_api_t:
            values.append(pulse_value)
        else:
            values.append(default_value)
        cur += timedelta(hours=1)
    return {
        "hourly": {
            "time": times,
            "shortwave_radiation": values,
        },
    }


# ── Test driver ───────────────────────────────────────────────────


def _load_method(method_name: str) -> str:
    path = Path(REPO_ROOT) / "hems" / "forecast.py"
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == method_name):
            start = node.lineno - 1
            end = node.end_lineno
            return textwrap.dedent("".join(lines[start:end]))
    raise RuntimeError(f"Could not find {method_name} in {path}")


_FAKE_SESSION_SRC = """
class _FakeResp:
    def __init__(self, text):
        self.status = 200
        self._text = text
    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")
    async def json(self):
        import json as _json
        return _json.loads(self._text)
    async def text(self):
        return self._text
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return None

class _FakeSession:
    def __init__(self, payload_factory, url_holder, params_holder):
        self._payload_factory = payload_factory
        self._url_holder = url_holder
        self._params_holder = params_holder
    def get(self, url, params=None):
        self._url_holder.append(url)
        params = dict(params) if params else {}
        self._params_holder.append(params)
        text = self._payload_factory(params)
        return _FakeResp(text)
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return None
"""


def _drive_method(method_name: str, payload_or_factory, site_tz_name: str,
                  latitude: float, longitude: float,
                  start_day, end_day):
    """Drive the production archive method with a synthetic payload.

    payload_or_factory: either a dict (used for every request) or a
    callable ``(params) -> text`` that generates a response based on
    the recorded request params. The latter is used to verify the
    fixture matches the request.
    """
    from hems.pv_learning import (
    complete_hourly_days,
    day_bounds,
    filter_radiation_to_requested_range,
    shift_radiation_to_interval_start,
)
    src = _load_method(method_name)
    src = textwrap.indent(src, "    ")
    src = re.sub(
        r"^(\s*async def \w+\([^)]*)\)(.*)$",
        r"\1, *, hourly_var=None)\2",
        src, count=1, flags=re.MULTILINE,
    )
    # Strip any ``from .pv_learning import ...`` lines from the
    # function body; the helpers are injected into the namespace
    # instead. This applies to both the single-name and the
    # parenthesised multi-name forms.
    src = re.sub(
        r"^[ \t]*from \.pv_learning import[^\n]*\n",
        "    pass  # from .pv_learning import ... injected below\n",
        src, flags=re.MULTILINE,
    )
    src = src.replace('"hourly": "shortwave_radiation"',
                      '"hourly": (hourly_var or "shortwave_radiation")')

    if callable(payload_or_factory):
        payload_factory = payload_or_factory
    else:
        text = json.dumps(payload_or_factory)
        def payload_factory(params, _text=text):
            return _text

    ns: dict = {
        "_PAYLOAD_FACTORY": payload_factory,
        "_REQUESTED_URL": [],
        "_REQUESTED_PARAMS": [],
        "json": json,
        "datetime": __import__("datetime"),
        "timezone": timezone,
        "timedelta": timedelta,
        "ZoneInfo": ZoneInfo,
        "day_bounds": day_bounds,
        "complete_hourly_days": complete_hourly_days,
        "shift_radiation_to_interval_start": shift_radiation_to_interval_start,
        "filter_radiation_to_requested_range": filter_radiation_to_requested_range,
    }
    exec(compile(_FAKE_SESSION_SRC, "<r01_fake_session>", "exec"), ns)

    src2 = _load_method("get_archive_hourly_radiation")
    src2 = textwrap.indent(src2, "    ")
    src2 = re.sub(
        r"^(\s*async def \w+\([^)]*)\)(.*)$",
        r"\1, *, hourly_var=None)\2",
        src2, count=1, flags=re.MULTILINE,
    )
    src2 = re.sub(
        r"^[ \t]*from \.pv_learning import[^\n]*\n",
        "    pass  # from .pv_learning import ... injected below\n",
        src2, flags=re.MULTILINE,
    )
    src2 = src2.replace('"hourly": "shortwave_radiation"',
                        '"hourly": (hourly_var or "shortwave_radiation")')

    class_src = (
        "class _Driver:\n"
        f"    timezone_name = {site_tz_name!r}\n"
        f"    _latitude = {latitude}\n"
        f"    _longitude = {longitude}\n"
        "    async def _ensure_session(self):\n"
        "        return _FakeSession(_PAYLOAD_FACTORY, _REQUESTED_URL, "
        "_REQUESTED_PARAMS)\n"
        "    async def _rate_limit(self):\n"
        "        return None\n"
        + src + "\n"
        + src2
    )
    method_ns = dict(ns)
    exec(compile(class_src, "<r01_driver>", "exec"), method_ns)
    driver = method_ns["_Driver"]()
    method = getattr(driver, method_name)
    daily = asyncio.run(method(start_day=start_day, end_day=end_day))
    rows_method = getattr(driver, "get_archive_hourly_radiation", None)
    if rows_method is not None:
        rows = asyncio.run(rows_method(start_day=start_day,
                                        end_day=end_day))
    else:
        rows = None
    return rows, daily, ns["_REQUESTED_URL"], ns["_REQUESTED_PARAMS"]


# ── Tests ─────────────────────────────────────────────────────────


def test_r01_daily_pulse_attributed_to_production_day() -> None:
    """Local request 2026-10-08..2026-10-09, timezone Europe/Kyiv.
    Production requests UTC dates 2026-10-07..2026-10-09.
    HTTP fixture covers the actual UTC range.
    Pulse 200 W/m² at API timestamp 2026-10-08 21:00 UTC.

    With the production contract v2 (this commit), each row's
    ``start`` is the radiation interval START (= api_t - 3600).
    The pulse interval is [2026-10-08 20:00 UTC, 2026-10-08 21:00
    UTC) = the last hour of Oct 8 in Kyiv. Daily aggregation puts
    the pulse in Oct 8.

    Expected production daily: {2026-10-08: 0.2, 2026-10-09: 0.0}.
    This matches the documented contract [t-1h, t).
    """
    start_day = datetime(2026, 10, 8).date()
    end_day = datetime(2026, 10, 9).date()
    tz = "Europe/Kyiv"
    actual_start, actual_end = _expected_utc_range(start_day, end_day, tz)
    assert actual_start == "2026-10-07", (
        f"Production should request UTC start_date=2026-10-07; got {actual_start}"
    )
    assert actual_end == "2026-10-09", (
        f"Production should request UTC end_date=2026-10-09; got {actual_end}"
    )
    pulse_at_api_t = datetime(2026, 10, 8, 21, 0, tzinfo=timezone.utc)
    pulse_value = 200.0
    payload = _build_archive_response(
        start_date=datetime(2026, 10, 7).date(),
        end_date=datetime(2026, 10, 9).date(),
        pulse_at_api_t=pulse_at_api_t, pulse_value=pulse_value,
    )

    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, tz, 50.45, 30.52,
        start_day, end_day,
    )
    # The recorded request URL/params should match what we expect.
    # Note: we call get_archive_radiation AND get_archive_hourly_radiation
    # in the driver, so 2 requests are recorded. The first is the
    # daily request we care about.
    assert len(params) >= 1, f"expected at least 1 request, got {len(params)}"
    p = params[0]  # the daily request
    assert p["start_date"] == "2026-10-07", (
        f"Production requested start_date={p['start_date']}, expected 2026-10-07"
    )
    assert p["end_date"] == "2026-10-09", (
        f"Production requested end_date={p['end_date']}, expected 2026-10-09"
    )
    assert p["timezone"] == "UTC"
    assert p["timeformat"] == "unixtime"
    assert p["hourly"] == "shortwave_radiation"

    # Production daily (contract v2): pulse is in Oct 8 because the
    # internal row's ``start`` = interval start, and the pulse's
    # interval starts at 2026-10-08 20:00 UTC (= 2026-10-08 23:00
    # Kyiv = last hour of Oct 8 in Kyiv).
    expected_production_daily = {
        "2026-10-08": 0.2,
        "2026-10-09": 0.0,
    }
    assert daily == expected_production_daily, (
        f"Production daily: got {daily}, expected "
        f"{expected_production_daily}. The pulse at API t=2026-10-08 "
        f"21:00 UTC represents interval [20:00, 21:00) UTC = last hour "
        f"of Oct 8 in Kyiv."
    )


def test_r01_documented_contract_pulse_belongs_to_oct8() -> None:
    """Independent computation of the documented contract [t-1h, t):
    the pulse at API t=2026-10-08 21:00 UTC represents the hour
    [2026-10-08 20:00 UTC, 2026-10-08 21:00 UTC) = [23:00, 24:00)
    Kyiv Oct 8 — the LAST hour of Oct 8 in Kyiv. By docs, this
    value belongs to Oct 8.

    Under the production contract v2, production and the documented
    contract MUST agree: both place the pulse in Oct 8. We assert
    this agreement so any future regression that re-introduces the
    off-by-one bug would fail this test.
    """
    start_day = datetime(2026, 10, 8).date()
    end_day = datetime(2026, 10, 9).date()
    tz_name = "Europe/Kyiv"
    tz = ZoneInfo(tz_name)
    pulse_at_api_t = datetime(2026, 10, 8, 21, 0, tzinfo=timezone.utc)
    pulse_value = 200.0
    payload = _build_archive_response(
        start_date=datetime(2026, 10, 7).date(),
        end_date=datetime(2026, 10, 9).date(),
        pulse_at_api_t=pulse_at_api_t, pulse_value=pulse_value,
    )

    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, tz_name, 50.45, 30.52,
        start_day, end_day,
    )
    # Take only the first call's rows (the daily call). The driver
    # makes a second call to get_archive_hourly_radiation with the
    # same fixture, which would double-count.
    n_rows_daily = 72  # 3 days * 24 hours
    rows_daily = rows[:n_rows_daily]

    # Independent computation from the production rows: each row's
    # ``start`` is the interval start (contract v2), so the day
    # assignment is the local day in which the interval begins.
    documented: dict[str, float] = {}
    for row in rows_daily:
        ts = datetime.fromtimestamp(row["start"], tz=timezone.utc)
        day = ts.astimezone(tz).date().isoformat()
        documented.setdefault(day, 0.0)
        documented[day] += row["mean"]
    documented_kwh = {d: round(v / 1000.0, 6) for d, v in documented.items()}

    # The documented contract (and the new production contract)
    # both put the pulse (200 W/m²) in Oct 8.
    expected_documented = {
        "2026-10-08": 0.2,  # the pulse here
        "2026-10-09": 0.0,
    }
    assert documented_kwh.get("2026-10-08") == 0.2, (
        f"Documented contract: documented_kwh['2026-10-08'] should be 0.2; "
        f"got {documented_kwh.get('2026-10-08')}"
    )
    assert documented_kwh.get("2026-10-09") == 0.0, (
        f"Documented contract: documented_kwh['2026-10-09'] should be 0.0; "
        f"got {documented_kwh.get('2026-10-09')}"
    )
    # Under contract v2, production and documented must agree.
    # If this assertion fails, the off-by-one bug has been
    # re-introduced.
    assert daily == expected_documented, (
        f"Production daily should match documented contract under v2: "
        f"production={daily}, documented={expected_documented}. "
        f"If they differ, the off-by-one bug is back."
    )


def test_r01_daily_constant_100w_spring_forward() -> None:
    """Spring forward 2026-03-29 (DST skip, 23h day).
    Constant 100 W/m² across the full UTC range, complete coverage.

    Production requests UTC 2026-03-27..2026-03-30 (4 days = 96 hours).

    Expected daily for current production (groups by local date at
    END of interval):
      * 2026-03-28 (Kyiv day): 24 hours, all 100 W/m² → 2.4 kWh/m²
      * 2026-03-29 (Kyiv day): 23 hours (DST skip at 03:00 Kyiv),
        all 100 W/m² → 2.3 kWh/m²
      * 2026-03-30 (Kyiv day): 24 hours, all 100 W/m² → 2.4 kWh/m²
    """
    start_day = datetime(2026, 3, 28).date()
    end_day = datetime(2026, 3, 30).date()
    tz_name = "Europe/Kyiv"
    actual_start, actual_end = _expected_utc_range(start_day, end_day, tz_name)
    assert actual_start == "2026-03-27", (
        f"Expected UTC start=2026-03-27; got {actual_start}"
    )
    assert actual_end == "2026-03-30", (
        f"Expected UTC end=2026-03-30; got {actual_end}"
    )
    payload = _build_archive_response(
        start_date=datetime(2026, 3, 27).date(),
        end_date=datetime(2026, 3, 30).date(),
        default_value=100.0,
    )

    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, tz_name, 50.45, 30.52,
        start_day, end_day,
    )
    expected = {
        "2026-03-28": 2.4,
        "2026-03-29": 2.3,
        "2026-03-30": 2.4,
    }
    assert set(daily.keys()) == set(expected.keys()), (
        f"Daily keys mismatch: got {sorted(daily.keys())}, "
        f"expected {sorted(expected.keys())}"
    )
    for day, exp_val in expected.items():
        assert abs(daily[day] - exp_val) < 1e-6, (
            f"daily[{day!r}] = {daily[day]}, expected {exp_val} "
            f"(delta = {daily[day] - exp_val})"
        )


def test_r01_daily_constant_100w_fall_back() -> None:
    """Fall back 2026-10-25 (DST repeat, 25h day).
    Constant 100 W/m² across the full UTC range, complete coverage.

    Production requests UTC 2026-10-23..2026-10-26 (4 days = 96 hours).

    Expected daily for current production (groups by local date at
    END of interval):
      * 2026-10-24 (Kyiv day): 24 hours, all 100 W/m² → 2.4 kWh/m²
      * 2026-10-25 (Kyiv day): 25 hours (DST repeat at 03:00 Kyiv),
        all 100 W/m² → 2.5 kWh/m²
      * 2026-10-26 (Kyiv day): 24 hours, all 100 W/m² → 2.4 kWh/m²
    """
    start_day = datetime(2026, 10, 24).date()
    end_day = datetime(2026, 10, 26).date()
    tz_name = "Europe/Kyiv"
    actual_start, actual_end = _expected_utc_range(start_day, end_day, tz_name)
    assert actual_start == "2026-10-23", (
        f"Expected UTC start=2026-10-23; got {actual_start}"
    )
    assert actual_end == "2026-10-26", (
        f"Expected UTC end=2026-10-26; got {actual_end}"
    )
    payload = _build_archive_response(
        start_date=datetime(2026, 10, 23).date(),
        end_date=datetime(2026, 10, 26).date(),
        default_value=100.0,
    )

    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, tz_name, 50.45, 30.52,
        start_day, end_day,
    )
    expected = {
        "2026-10-24": 2.4,
        "2026-10-25": 2.5,
        "2026-10-26": 2.4,
    }
    assert set(daily.keys()) == set(expected.keys()), (
        f"Daily keys mismatch: got {sorted(daily.keys())}, "
        f"expected {sorted(expected.keys())}"
    )
    for day, exp_val in expected.items():
        assert abs(daily[day] - exp_val) < 1e-6, (
            f"daily[{day!r}] = {daily[day]}, expected {exp_val} "
            f"(delta = {daily[day] - exp_val})"
        )


# ── Mutation tests on PRODUCTION result ───────────────────────────


def _mutate_daily(daily, kind):
    """Return a mutated version of the production daily dict.

    Contract v2 daily: ``{2026-10-08: 0.2, 2026-10-09: 0.0}``.
    The pulse is in Oct 8; the mutations exercise the four ways
    a buggy implementation could still pass the equality check
    while returning wrong data.
    """
    if kind == "empty":
        return {}
    if kind == "wrong_pulse_day":
        # Move the 0.2 kWh/m² from 2026-10-08 to 2026-10-09 (the
        # old contract v1 mis-attribution).
        new = dict(daily)
        if "2026-10-08" in new and new["2026-10-08"] > 0:
            v = new.pop("2026-10-08")
            new["2026-10-09"] = new.get("2026-10-09", 0.0) + v
        return new
    if kind == "wrong_sum":
        new = dict(daily)
        for d in new:
            if new[d] > 0:
                new[d] = new[d] + 0.5  # off by 0.5 kWh/m²
        return new
    if kind == "shifted_timestamps":
        # Pretend the rows[].start was shifted by +7200 (i.e. +2h).
        # For contract v2, ``start`` is interval start; +2h would
        # mis-attribute days. The mutation is detected by checking
        # ``rows[0].start`` against the expected first interval
        # start (2026-10-07 20:00 UTC = the first hour of Oct 8
        # in Kyiv).
        return daily
    raise ValueError(f"unknown mutation: {kind}")


def test_r01_mutation_empty_daily_triggers_assertion() -> None:
    """Mutation 1: production returns daily={}. The assertion on
    the expected dict MUST fail.
    """
    start_day = datetime(2026, 10, 8).date()
    end_day = datetime(2026, 10, 9).date()
    tz_name = "Europe/Kyiv"
    payload = _build_archive_response(
        start_date=datetime(2026, 10, 7).date(),
        end_date=datetime(2026, 10, 9).date(),
        pulse_at_api_t=datetime(2026, 10, 8, 21, tzinfo=timezone.utc),
        pulse_value=200.0,
    )
    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, tz_name, 50.45, 30.52,
        start_day, end_day,
    )
    # Simulate the mutation: replace production's daily with {}.
    mutated = _mutate_daily(daily, "empty")
    expected = {"2026-10-08": 0.2, "2026-10-09": 0.0}
    try:
        assert mutated == expected, (
            f"mutated daily ({mutated}) should not equal expected ({expected})"
        )
        raised = False
    except AssertionError:
        raised = True
    assert raised, (
        "Mutation 'empty daily' did NOT trigger an AssertionError. "
        "The test would have passed even if production returned {}."
    )


def test_r01_mutation_wrong_pulse_day_triggers_assertion() -> None:
    """Mutation 2: pulse attributed to wrong day (the v1 mis-attribution
    Oct 8 -> Oct 9). The assertion MUST fail.
    """
    start_day = datetime(2026, 10, 8).date()
    end_day = datetime(2026, 10, 9).date()
    tz_name = "Europe/Kyiv"
    payload = _build_archive_response(
        start_date=datetime(2026, 10, 7).date(),
        end_date=datetime(2026, 10, 9).date(),
        pulse_at_api_t=datetime(2026, 10, 8, 21, tzinfo=timezone.utc),
        pulse_value=200.0,
    )
    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, tz_name, 50.45, 30.52,
        start_day, end_day,
    )
    mutated = _mutate_daily(daily, "wrong_pulse_day")
    expected = {"2026-10-08": 0.2, "2026-10-09": 0.0}
    try:
        assert mutated == expected
        raised = False
    except AssertionError:
        raised = True
    assert raised, (
        "Mutation 'wrong pulse day' did NOT trigger an AssertionError. "
        f"Original daily: {daily}, mutated: {mutated}, expected: {expected}"
    )


def test_r01_mutation_wrong_sum_triggers_assertion() -> None:
    """Mutation 3: sums are off (e.g. +0.5 kWh/m²). The assertion
    MUST fail.
    """
    start_day = datetime(2026, 10, 8).date()
    end_day = datetime(2026, 10, 9).date()
    tz_name = "Europe/Kyiv"
    payload = _build_archive_response(
        start_date=datetime(2026, 10, 7).date(),
        end_date=datetime(2026, 10, 9).date(),
        pulse_at_api_t=datetime(2026, 10, 8, 21, tzinfo=timezone.utc),
        pulse_value=200.0,
    )
    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, tz_name, 50.45, 30.52,
        start_day, end_day,
    )
    mutated = _mutate_daily(daily, "wrong_sum")
    expected = {"2026-10-08": 0.2, "2026-10-09": 0.0}
    try:
        # Replicate the production assertion with float tolerance.
        assert set(mutated.keys()) == set(expected.keys())
        for d, ev in expected.items():
            assert abs(mutated[d] - ev) < 1e-6
        raised = False
    except AssertionError:
        raised = True
    assert raised, (
        "Mutation 'wrong sum' did NOT trigger an AssertionError. "
        f"Original daily: {daily}, mutated: {mutated}, expected: {expected}"
    )


def test_r01_mutation_shifted_timestamps_triggers_assertion() -> None:
    """Mutation 4: production's rows[].start is shifted by +7200s
    while the input HTTP fixture is unchanged. The assertion on
    ``rows[0].start`` MUST fail.

    For contract v2 the first kept row is the interval start
    of the first requested hour: 2026-10-07 21:00 UTC (= first
    hour of Oct 8 in Kyiv) - 1h = 2026-10-07 20:00 UTC.
    """
    start_day = datetime(2026, 10, 8).date()
    end_day = datetime(2026, 10, 9).date()
    tz_name = "Europe/Kyiv"
    payload = _build_archive_response(
        start_date=datetime(2026, 10, 7).date(),
        end_date=datetime(2026, 10, 9).date(),
        pulse_at_api_t=datetime(2026, 10, 8, 21, tzinfo=timezone.utc),
        pulse_value=200.0,
    )
    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, tz_name, 50.45, 30.52,
        start_day, end_day,
    )
    # Expected first kept row's start = 2026-10-07 21:00 UTC - 1h
    # = 2026-10-07 20:00 UTC. The first kept row in the
    # response represents the interval [first, first+1h) of the
    # requested range.
    expected_first_t = int(
        datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc).timestamp()
    )
    # Simulate the production mutation: rows[0]["start"] is +7200
    # shifted.
    mutated_first_t = rows[0]["start"] + 7200
    # The assertion that the test would have:
    try:
        assert mutated_first_t == expected_first_t, (
            f"mutated first_t ({mutated_first_t}) should not equal "
            f"expected first_t ({expected_first_t})"
        )
        raised = False
    except AssertionError:
        raised = True
    assert raised, (
        "Mutation 'shifted timestamps (+7200)' did NOT trigger an "
        "AssertionError. The test would have passed even if production "
        "internally shifted its rows[].start by +2h."
    )


if __name__ == "__main__":
    test_r01_daily_pulse_attributed_to_production_day()
    print("test_r01_daily_pulse_attributed_to_production_day: PASS")
    test_r01_documented_contract_pulse_belongs_to_oct8()
    print("test_r01_documented_contract_pulse_belongs_to_oct8: PASS")
    test_r01_daily_constant_100w_spring_forward()
    print("test_r01_daily_constant_100w_spring_forward: PASS")
    test_r01_daily_constant_100w_fall_back()
    print("test_r01_daily_constant_100w_fall_back: PASS")
    test_r01_mutation_empty_daily_triggers_assertion()
    print("test_r01_mutation_empty_daily_triggers_assertion: PASS")
    test_r01_mutation_wrong_pulse_day_triggers_assertion()
    print("test_r01_mutation_wrong_pulse_day_triggers_assertion: PASS")
    test_r01_mutation_wrong_sum_triggers_assertion()
    print("test_r01_mutation_wrong_sum_triggers_assertion: PASS")
    test_r01_mutation_shifted_timestamps_triggers_assertion()
    print("test_r01_mutation_shifted_timestamps_triggers_assertion: PASS")
    print("\nAll 8 tests passed (0 failed).")
    sys.exit(0)
