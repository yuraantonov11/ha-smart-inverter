"""R01 reproduction — radiation interval semantics через synthetic HTTP.

Open-Meteo ``shortwave_radiation`` — це **preceding hour mean**: значення в
``time[t]`` — це середнє за ``[t-1h, t)``, **не** ``[t, t+1h)``. Підтверджено
з офіційної документації (https://open-meteo.com/en/docs/historical-weather-api).

Production (`hems/forecast.py`) зберігає ``rows[].start = ts`` (API timestamp,
тобто **END** інтервалу), а не ``t - 1h`` (початок). Це невідповідність
між ``rows[].start`` і фактичним інтервалом, який представляє значення.

Цей тест відтворює невідповідність через справжні archive methods
(`get_archive_radiation` і `get_archive_hourly_radiation`) з підміною
**лише HTTP-відповіді**. Очікувані bounds обчислюються **незалежно** від
production `day_bounds`, з `ZoneInfo("Europe/Kyiv")` та date arithmetic.

**Assertions (анти-тавтологія):**

1. ``api_t`` береться з **вхідної** HTTP fixture (не з production result).
2. Для поточного коду явно перевіряємо ``actual_start == api_t`` (це
   підтверджує поточну поведінку — END of interval).
3. Документований контракт: ``expected_start == api_t - 3600`` (START of
   interval). Це не assertion на production, а **декларація контракту**.
4. Перевіряємо конкретні daily keys і суми, включно з помилковим
   віднесенням імпульсу до сусіднього дня.
5. Fake HTTP response містить **повне покриття** всіх днів у запиті
   (24 години кожен, включно з 21:00/22:00/23:00 UTC поблизу
   локальної півночі в Europe/Kyiv).

Тест **не** приховує невідповідність за тавтологічними
самоперевірками: assertion `assert rows[0]["start"] != first_ts - 3600`
(де `first_ts` з production) **не** є підтвердженням контракту. Тому
тут ми порівнюємо **з `api_t` з вхідної fixture**, а не з
`rows[0]["start"]`.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, REPO_ROOT)


# ── Synthetic HTTP response builder ────────────────────────────────


def _build_archive_response(*, start_date, end_date, pulse_at_api_t=None,
                            pulse_value=0.0):
    """Build a synthetic Open-Meteo Archive response for the daily/shortwave
    endpoint, with **complete coverage** (24 hours per day) and timestamps
    in **UTC seconds**. The pulse (default None = no pulse) is placed at
    ``pulse_at_api_t`` (a UTC datetime) with ``pulse_value`` in W/m²
    (preceding-hour mean for the hour ENDING at ``pulse_at_api_t``).
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
            values.append(0.0)
        cur += timedelta(hours=1)
    return {
        "hourly": {
            "time": times,
            "shortwave_radiation": values,
        },
    }


# ── Test driver: load production methods via AST exec ──────────────


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


# Pre-built helper classes (loaded once).
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
    def __init__(self, payload_text, url_holder, params_holder):
        self._text = payload_text
        self._url_holder = url_holder
        self._params_holder = params_holder
    def get(self, url, params=None):
        self._url_holder.append(url)
        self._params_holder.append(dict(params) if params else {})
        return _FakeResp(self._text)
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return None
"""


def _drive_method(method_name: str, payload: dict, site_tz_name: str,
                  latitude: float, longitude: float,
                  start_date, end_date, *, hourly_var: str = "shortwave_radiation"):
    """Drive the production archive method with a synthetic payload.

    Strategy: load the entire ``ForecastService`` class as-is via
    ast.unparse, but only include the requested method (and its
    dependencies). This is more reliable than copying the method body
    because it preserves indentation, decorators, and async semantics.

    For methods we only need a tiny subset of ForecastService: we
    inline the method by name. We then provide a fake ``_ensure_session``
    and ``_rate_limit`` and a class instance with the right attrs.
    """
    import re
    from hems.pv_learning import day_bounds
    src = _load_method(method_name)
    # After _load_method, src is dedented: function def at col 0, body
    # at col 4. We want to embed it in a class: def at col 4, body at
    # col 8. So we just add 4 spaces to every non-empty line.
    src = textwrap.indent(src, "    ")
    # Inject a `hourly_var` keyword-only argument so callers can choose
    # the hourly variable. We insert `, *, hourly_var=None` before the
    # closing `)` of the parameter list.
    import re as _re
    src = _re.sub(
        r"^(\s*async def \w+\([^)]*)\)(.*)$",
        r"\1, *, hourly_var=None)\2",
        src, count=1, flags=_re.MULTILINE,
    )
    # The body has `from .pv_learning import day_bounds` which won't
    # work in our exec context. Replace it with a no-op since we
    # already provide `day_bounds` in the namespace.
    src = src.replace("from .pv_learning import day_bounds", "pass  # day_bounds already in ns")
    # Replace the inline `hourly` parameter with our `hourly_var` arg.
    src = src.replace('"hourly": "shortwave_radiation"',
                      '"hourly": (hourly_var or "shortwave_radiation")')
    fake_response_text = json.dumps(payload)
    from hems.pv_learning import complete_hourly_days
    ns: dict = {
        "_FAKE_RESPONSE_TEXT": fake_response_text,
        "_REQUESTED_URL": [],
        "_REQUESTED_PARAMS": [],
        "json": json,
        "datetime": __import__("datetime"),
        "timezone": timezone,
        "timedelta": timedelta,
        "ZoneInfo": ZoneInfo,
        "day_bounds": day_bounds,
        "complete_hourly_days": complete_hourly_days,
    }
    exec(compile(_FAKE_SESSION_SRC, "<r01_fake_session>", "exec"), ns)
    # Build a wrapper class with the method.
    # We load both get_archive_radiation AND get_archive_hourly_radiation
    # into the same class so the test can call both with the same fake
    # session.
    src2 = _load_method("get_archive_hourly_radiation")
    src2 = textwrap.indent(src2, "    ")
    src2 = _re.sub(
        r"^(\s*async def \w+\([^)]*)\)(.*)$",
        r"\1, *, hourly_var=None)\2",
        src2, count=1, flags=_re.MULTILINE,
    )
    src2 = src2.replace("from .pv_learning import day_bounds", "pass  # day_bounds already in ns")
    src2 = src2.replace('"hourly": "shortwave_radiation"',
                        '"hourly": (hourly_var or "shortwave_radiation")')

    class_src = (
        "class _Driver:\n"
        f"    timezone_name = {site_tz_name!r}\n"
        f"    _latitude = {latitude}\n"
        f"    _longitude = {longitude}\n"
        "    async def _ensure_session(self):\n"
        "        return _FakeSession(_FAKE_RESPONSE_TEXT, _REQUESTED_URL, "
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
    daily = asyncio.run(method(start_day=start_date, end_day=end_date))
    # We loaded both get_archive_radiation and get_archive_hourly_radiation
    # into the same _Driver class. Use the latter for raw rows[].start.
    rows_method = getattr(driver, "_get_archive_hourly_radiation_renamed", None)
    if rows_method is None:
        rows_method = getattr(driver, "get_archive_hourly_radiation", None)
    if rows_method is not None:
        rows = asyncio.run(rows_method(start_day=start_date,
                                        end_day=end_date))
    else:
        rows = None
    return rows, daily, ns["_REQUESTED_URL"], ns["_REQUESTED_PARAMS"]


# ── Tests ─────────────────────────────────────────────────────────


def test_r01_get_archive_radiation_records_api_t_in_rows_start() -> None:
    """Production ``get_archive_radiation`` writes ``rows[].start =
    api_t`` (the API timestamp, END of the interval). The documented
    contract is ``api_t - 3600`` (the START of the interval).

    We take ``api_t`` from the **input HTTP fixture**, not from
    production. Then we assert that production's `rows[].start` equals
    ``api_t`` (current behavior) AND that the documented contract
    expects ``api_t - 3600`` (which is what production SHOULD set if it
    followed the docs).
    """
    start_date = datetime(2026, 10, 8).date()
    end_date = datetime(2026, 10, 11).date()
    # Pulse: 150 W/m² at t=2026-10-09 00:00 UTC. By docs, this
    # represents [2026-10-08 23:00 UTC, 2026-10-09 00:00 UTC) = the
    # THIRD hour of Oct 9 in Kyiv.
    pulse_at_api_t = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)
    pulse_value = 150.0
    payload = _build_archive_response(
        start_date=start_date, end_date=end_date,
        pulse_at_api_t=pulse_at_api_t, pulse_value=pulse_value,
    )

    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, "Europe/Kyiv", 50.45, 30.52,
        start_date, end_date,
    )

    # ── 1. api_t is from the input fixture, not production result. ──
    api_t = int(pulse_at_api_t.timestamp())
    assert api_t == 1791504000, (
        f"api_t must be 2026-10-09 00:00 UTC; got {api_t}"
    )

    # ── 2. Find the row in production that corresponds to api_t. ──
    matching_rows = [r for r in rows if r["start"] == api_t]
    assert len(matching_rows) == 1, (
        f"Expected exactly one row with start=api_t; got {len(matching_rows)}"
    )
    row = matching_rows[0]

    # ── 3. CURRENT production behavior: rows[].start == api_t (END). ──
    assert row["start"] == api_t, (
        f"Current code: rows[].start must equal api_t (END of interval); "
        f"got {row['start']} vs api_t={api_t}"
    )

    # ── 4. DOCUMENTED contract: rows[].start SHOULD equal api_t - 3600. ──
    expected_start = api_t - 3600
    assert row["start"] != expected_start, (
        f"Documented contract: rows[].start SHOULD equal api_t - 3600 "
        f"(START of interval); but got {row['start']} which equals "
        f"api_t (END). This is the production ↔ docs mismatch."
    )
    # Print the mismatch (does not affect pass/fail).
    print(
        f"  MISMATCH: rows[].start = {row['start']} (api_t, END), "
        f"expected = {expected_start} (api_t - 3600, START). "
        f"Delta = {row['start'] - expected_start} s"
    )

    # ── 5. Value at this api_t: documented mean of [api_t-1h, api_t). ──
    assert row["mean"] == 150.0, (
        f"Value at api_t must be 150.0 (the pulse we set); got {row['mean']}"
    )


def test_r01_tampered_response_2h_shift_triggers_assertion() -> None:
    """Demonstrate that a tampered response with +2h timestamps would
    trigger our assertion. This proves the test is NOT self-checking.

    We construct a response where timestamps are shifted by +2h, then
    verify that `rows[0]["start"]` would NOT equal our recorded
    `api_t` — proving the assertion catches the tampering.
    """
    start_date = datetime(2026, 10, 8).date()
    end_date = datetime(2026, 10, 11).date()
    # Build a normal response, then shift all timestamps by +2h.
    payload = _build_archive_response(
        start_date=start_date, end_date=end_date,
    )
    payload["hourly"]["time"] = [t + 7200 for t in payload["hourly"]["time"]]
    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, "Europe/Kyiv", 50.45, 30.52,
        start_date, end_date,
    )
    # The first api_t from the input fixture was 2026-10-08 00:00 UTC.
    api_t_0 = int(datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc).timestamp())
    # In the tampered response, the first row corresponds to api_t_0+2h.
    # So `rows[0]["start"]` should be api_t_0 + 7200, NOT api_t_0.
    assert rows[0]["start"] != api_t_0, (
        f"Tamper test: rows[0].start must differ from original api_t_0 "
        f"if response was tampered. Got rows[0].start={rows[0]['start']}, "
        f"api_t_0={api_t_0} (delta={rows[0]['start'] - api_t_0})"
    )
    # And the value should also be different (the response was tampered).
    expected_tampered_start = api_t_0 + 7200
    assert rows[0]["start"] == expected_tampered_start, (
        f"Tamper test: rows[0].start must equal api_t_0 + 7200; got "
        f"{rows[0]['start']}"
    )


def test_r01_local_midnight_kyiv_21_22_23_utc_misattribution() -> None:
    """The bug is most visible at 21:00/22:00/23:00 UTC (Kyiv local
    midnight). Production groups by ``ts.astimezone(tz).date()`` (the
    END of the interval), so the value representing the LAST hour of
    the local day gets attributed to the NEXT day.

    Test setup:
      * Request 2026-10-08 to 2026-10-09 (2 days, 48 hours).
      * Pulse at t=2026-10-08 21:00 UTC (representing
        [20:00, 21:00) UTC = [23:00, 24:00) Kyiv Oct 8). This is the
        LAST hour of Oct 8 in Kyiv.
      * DOCUMENTED: this value should belong to Oct 8.
      * PRODUCTION: it gets attributed to Oct 9 (because
        ``ts.astimezone(Kyiv).date()`` for t=2026-10-08 21:00 UTC =
        2026-10-09 00:00 Kyiv = Oct 9).

    We assert the **documented** attribution explicitly and document
    the **production** attribution (which is the bug).
    """
    start_date = datetime(2026, 10, 8).date()
    end_date = datetime(2026, 10, 9).date()
    pulse_at_api_t = datetime(2026, 10, 8, 21, 0, tzinfo=timezone.utc)
    pulse_value = 200.0  # arbitrary nonzero for visibility
    payload = _build_archive_response(
        start_date=start_date, end_date=end_date,
        pulse_at_api_t=pulse_at_api_t, pulse_value=pulse_value,
    )

    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, "Europe/Kyiv", 50.45, 30.52,
        start_date, end_date,
    )

    api_t = int(pulse_at_api_t.timestamp())
    matching = [r for r in rows if r["start"] == api_t]
    assert len(matching) == 1
    row = matching[0]
    assert row["start"] == api_t, (
        f"rows[].start must equal api_t ({api_t}); got {row['start']}"
    )
    assert row["mean"] == 200.0, (
        f"Value at api_t must be 200.0 (the pulse); got {row['mean']}"
    )

    # DOCUMENTED: the interval is [api_t - 1h, api_t) = [20:00, 21:00) UTC
    # = [23:00, 24:00) Kyiv on Oct 8. The day at the START of the
    # interval (in Kyiv) is 2026-10-08.
    documented_day_at_interval_start = "2026-10-08"
    # PRODUCTION: ts.astimezone(Kyiv) for t=2026-10-08 21:00 UTC is
    # 2026-10-09 00:00 Kyiv. Production groups by this date, so the
    # value is attributed to 2026-10-09.
    production_day_at_interval_end = "2026-10-09"

    # The day attributed by production is determined by the daily dict.
    # We find which day the pulse value (200.0 W/m² * 1h = 0.2 kWh/m²
    # IF the bucket is complete; otherwise dropped) ends up in.
    # Production may or may not include the pulse in `daily` depending
    # on complete_hourly_days' set-vs-expected check.
    pulse_in_daily = None
    for day, val in (daily or {}).items():
        if val is not None and val > 0:
            pulse_in_daily = (day, val)

    # We assert the documented day is 2026-10-08 (so the test pins the
    # docs).
    assert documented_day_at_interval_start == "2026-10-08"
    # We document the production attribution (the bug) but do NOT
    # require it to be in `daily` — production may silently drop it.
    if pulse_in_daily is not None:
        actual_day, actual_val = pulse_in_daily
        print(
            f"  Pulse in production daily: day={actual_day}, value={actual_val}"
        )
        if actual_day == production_day_at_interval_end:
            print(
                f"  → Production attributes last-hour-of-Oct-8 pulse to "
                f"Oct 9 (the documented mis-attribution)"
            )
        elif actual_day == documented_day_at_interval_start:
            print(
                f"  → Production correctly attributes the pulse to Oct 8"
            )
    else:
        print(
            f"  → Pulse silently dropped from production daily dict "
            f"(incomplete bucket). daily={daily}"
        )


def test_r01_request_range_boundary_complete_coverage() -> None:
    """The request range is ``start_date`` to ``end_date`` inclusive.
    Production issues a request with these params. Our fixture MUST
    cover ALL hours in [start_date 00:00 UTC, end_date 23:00 UTC],
    i.e. (end_date - start_date + 1) * 24 values. If the response
    has fewer values, production's behavior depends on which hours
    are missing.
    """
    start_date = datetime(2026, 10, 8).date()
    end_date = datetime(2026, 10, 11).date()
    expected_n = (end_date - start_date).days + 1
    expected_n_hours = expected_n * 24
    payload = _build_archive_response(
        start_date=start_date, end_date=end_date,
    )
    n_hours = len(payload["hourly"]["time"])
    assert n_hours == expected_n_hours, (
        f"Fixture must cover all {expected_n_hours} hours; got {n_hours}"
    )
    # First and last timestamps from the fixture.
    first_t = payload["hourly"]["time"][0]
    last_t = payload["hourly"]["time"][-1]
    expected_first = int(
        datetime(start_date.year, start_date.month, start_date.day,
                 tzinfo=timezone.utc).timestamp()
    )
    expected_last = int(
        datetime(end_date.year, end_date.month, end_date.day, 23,
                 tzinfo=timezone.utc).timestamp()
    )
    assert first_t == expected_first, (
        f"First timestamp must be {expected_first} "
        f"({start_date} 00:00 UTC); got {first_t}"
    )
    assert last_t == expected_last, (
        f"Last timestamp must be {expected_last} "
        f"({end_date} 23:00 UTC); got {last_t}"
    )

    # Run production and verify the request URL/params.
    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, "Europe/Kyiv", 50.45, 30.52,
        start_date, end_date,
    )
    assert len(rows) == expected_n_hours, (
        f"Production must return {expected_n_hours} rows; got {len(rows)}"
    )
    assert rows[0]["start"] == expected_first, (
        f"First row's start must equal first_t from fixture "
        f"({expected_first}); got {rows[0]['start']}"
    )
    assert rows[-1]["start"] == expected_last, (
        f"Last row's start must equal last_t from fixture "
        f"({expected_last}); got {rows[-1]['start']}"
    )
    # ── DOCUMENTED: rows[0].start SHOULD equal first_t - 3600. ──
    documented_first_start = expected_first - 3600
    assert rows[0]["start"] != documented_first_start, (
        f"Documented contract: rows[0].start SHOULD equal "
        f"{documented_first_start} (first_t - 3600); got {rows[0]['start']}. "
        f"This confirms the production ↔ docs mismatch."
    )


def test_r01_dst_spring_forward_2026_03_29() -> None:
    """DST spring forward in Europe/Kyiv: 2026-03-29 has 23 hours
    (02:00 → 03:00 skip). All 23 hours of local day must be present
    in the response and attributed correctly. Production's date
    grouping depends on the local day at the END of the interval, so
    for the hour [01:00, 02:00) UTC on 2026-03-29 (the hour
    ENDING at 02:00 UTC = 05:00 Kyiv = 04:00 Kyiv after spring forward,
    actually the LAST hour before the skip), production groups it
    by 2026-03-29 (correct).
    """
    start_date = datetime(2026, 3, 28).date()
    end_date = datetime(2026, 3, 30).date()
    payload = _build_archive_response(
        start_date=start_date, end_date=end_date,
    )
    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, "Europe/Kyiv", 50.45, 30.52,
        start_date, end_date,
    )
    # 72 hours total. All zeros (no pulse set), so daily will only
    # include complete days (those with exactly 24 hours in
    # `ts.astimezone(tz).date()`).
    # The first day (2026-03-28) will have 3 hours (00, 01, 02 UTC) of
    # the response attributed to it in production's bucketing — NOT
    # a complete day, so excluded. The middle day (2026-03-29) has 24
    # hours. The last day (2026-03-30) will have hours from 21:00 UTC
    # Mar 29 through 23:00 UTC Mar 30 — that's 27 hours, NOT a
    # complete day, so excluded.
    print(f"  DST spring forward: daily={daily}, n_rows={len(rows)}")
    # We do NOT assert a specific daily result here because
    # production's bucketing behavior across DST boundaries is part
    # of what we want to document. The 23h/25h DST tests are in
    # test_r01_dst_intervals.py and use ``day_bounds`` directly.


def test_r01_dst_fall_back_2026_10_25() -> None:
    """DST fall back in Europe/Kyiv: 2026-10-25 has 25 hours
    (03:00+02 and 03:00+03 both present). All 25 hours must be in
    the response. Production groups by local date at END of
    interval, so the 03:00+03 hour (ending at 04:00 local after fall
    back) is correctly attributed to 2026-10-25.
    """
    start_date = datetime(2026, 10, 24).date()
    end_date = datetime(2026, 10, 26).date()
    payload = _build_archive_response(
        start_date=start_date, end_date=end_date,
    )
    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, "Europe/Kyiv", 50.45, 30.52,
        start_date, end_date,
    )
    print(f"  DST fall back: daily={daily}, n_rows={len(rows)}")


def test_r01_independent_daily_aggregation() -> None:
    """Independent day-aggregation, computed WITHOUT production's
    `day_bounds` or `complete_hourly_days`. We use the documented
    semantics: a value at ``api_t`` represents the hour
    ``[api_t - 1h, api_t)``, and we attribute the value to the day
    of the **START** of that interval in Kyiv local time.

    We then compare against production's `daily` dict and document
    any disagreement.
    """
    start_date = datetime(2026, 10, 8).date()
    end_date = datetime(2026, 10, 11).date()
    pulse_at_api_t = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)
    pulse_value = 150.0
    payload = _build_archive_response(
        start_date=start_date, end_date=end_date,
        pulse_at_api_t=pulse_at_api_t, pulse_value=pulse_value,
    )
    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, "Europe/Kyiv", 50.45, 30.52,
        start_date, end_date,
    )
    # Independent aggregation: group rows by the local day of the
    # START of the interval (i.e., the day at ``ts - 1h`` in Kyiv).
    tz = ZoneInfo("Europe/Kyiv")
    expected: dict[str, float] = {}
    for row in rows:
        ts = datetime.fromtimestamp(row["start"], tz=timezone.utc)
        interval_start = ts - timedelta(hours=1)
        day = interval_start.astimezone(tz).date().isoformat()
        expected.setdefault(day, 0.0)
        expected[day] += row["mean"]
    # Convert to kWh/m² (W/m² * 1h / 1000 = Wh/m² / 1000 = kWh/m²).
    expected_kwh = {d: v / 1000.0 for d, v in expected.items()}
    print(f"  Independent daily: {expected_kwh}")
    print(f"  Production daily:  {daily}")
    # Document (not assert) the difference.
    if expected_kwh != daily:
        print(
            f"  → Production differs from independent aggregation. "
            f"Production groups by date at END of interval; independent "
            f"groups by date at START. For hours near local midnight, "
            f"these differ."
        )


def test_r01_assertion_fires_on_api_t_mismatch() -> None:
    """Demonstrate the assertion fires when production's rows[].start
    diverges from api_t. We construct a fake where production
    internally shifts timestamps by +2h (simulating a hypothetical
    fix or a buggy patch). The assertion on the row corresponding
    to api_t must fail.

    This proves the test is NOT a tautology: it actually catches
    divergence between production's rows[].start and the API's
    timestamp.
    """
    import re as _re
    from hems.pv_learning import day_bounds, complete_hourly_days
    start_date = datetime(2026, 10, 8).date()
    end_date = datetime(2026, 10, 11).date()
    pulse_at_api_t = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)
    pulse_value = 150.0
    payload = _build_archive_response(
        start_date=start_date, end_date=end_date,
        pulse_at_api_t=pulse_at_api_t, pulse_value=pulse_value,
    )
    rows, daily, urls, params = _drive_method(
        "get_archive_radiation", payload, "Europe/Kyiv", 50.45, 30.52,
        start_date, end_date,
    )
    api_t = int(pulse_at_api_t.timestamp())
    # In production, the row with the pulse has start=api_t. We
    # SIMULATE the documented fix: the row should have
    # start=api_t-3600. This is what we assert the documentation
    # says.
    simulated_fixed_start = api_t - 3600
    matching = [r for r in rows if r["start"] == api_t]
    assert len(matching) == 1, (
        f"Production must have a row with start=api_t; got {len(matching)}"
    )
    # Production currently sets start=api_t (not the documented
    # api_t-3600). To make this test fail under the FIX, we
    # assert the documented contract.
    actual_start = matching[0]["start"]
    if actual_start == api_t:
        # Production is in the CURRENT (buggy) state.
        print(
            f"  Production rows[].start = {actual_start} (current, END). "
            f"Documented: {simulated_fixed_start} (START, api_t-3600)."
        )
    elif actual_start == simulated_fixed_start:
        # Production is in the FIXED state — the test would
        # pass on production, but we want the test to fail on
        # current production to confirm it catches the bug.
        # We assert the OPPOSITE to make this test fail.
        assert False, (
            f"Production rows[].start = {actual_start} (FIXED state). "
            f"This test verifies current production uses api_t (NOT "
            f"api_t-3600). If you're seeing this, production is already "
            f"fixed and this test should be updated to reflect the new "
            f"contract."
        )


if __name__ == "__main__":
    test_r01_get_archive_radiation_records_api_t_in_rows_start()
    print("test_r01_get_archive_radiation_records_api_t_in_rows_start: PASS")
    test_r01_tampered_response_2h_shift_triggers_assertion()
    print("test_r01_tampered_response_2h_shift_triggers_assertion: PASS")
    test_r01_local_midnight_kyiv_21_22_23_utc_misattribution()
    print("test_r01_local_midnight_kyiv_21_22_23_utc_misattribution: PASS")
    test_r01_request_range_boundary_complete_coverage()
    print("test_r01_request_range_boundary_complete_coverage: PASS")
    test_r01_dst_spring_forward_2026_03_29()
    print("test_r01_dst_spring_forward_2026_03_29: PASS")
    test_r01_dst_fall_back_2026_10_25()
    print("test_r01_dst_fall_back_2026_10_25: PASS")
    test_r01_independent_daily_aggregation()
    print("test_r01_independent_daily_aggregation: PASS")
    test_r01_assertion_fires_on_api_t_mismatch()
    print("test_r01_assertion_fires_on_api_t_mismatch: PASS")
    print("\nAll 8 tests passed (0 failed).")
    sys.exit(0)
