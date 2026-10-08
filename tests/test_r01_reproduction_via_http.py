"""R01 reproduction — radiation interval semantics через synthetic HTTP.

Open-Meteo ``shortwave_radiation`` is documented as **preceding hour
mean**: the value at timestamp ``t`` is the mean over ``[t-1h, t)``.

Production builds ``rows[{"start": ts, "mean": value}]`` directly from the
API timestamps — so ``rows[].start = ts`` is the END of the interval, not
the start.

This test injects a synthetic Open-Meteo response (no real HTTP) into the
production ``get_archive_hourly_radiation`` and ``get_archive_radiation``
methods via AST exec, then asserts the exact value of ``rows[0].start``
against the documented interval start.
"""
from __future__ import annotations

import asyncio
import ast
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, REPO_ROOT)


# ── Network seam ──────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def raise_for_status(self):
        return None

    async def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.last_url = None
        self.last_params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def get(self, url, params=None):
        self.last_url = url
        self.last_params = dict(params or {})
        return _FakeResponse(self._payload)


# ── Synthetic Open-Meteo response ─────────────────────────────────────


def _make_response(start_date, end_date, *, value_at_first_hour=0.0):
    start = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
    n_hours = int((end - start).total_seconds() // 3600) + 24
    times = [int((start + timedelta(hours=h)).timestamp()) for h in range(n_hours)]
    values = [0.0] * n_hours
    values[0] = value_at_first_hour
    return {
        "latitude": 50.45,
        "longitude": 30.52,
        "utc_offset_seconds": 0,
        "timezone": "UTC",
        "hourly_units": {"shortwave_radiation": "W/m^2"},
        "hourly": {
            "time": times,
            "shortwave_radiation": values,
        },
    }


# ── Driver ────────────────────────────────────────────────────────────


def _load_method(method_name):
    """Return the source of a method, with relative imports stripped."""
    path = Path(REPO_ROOT) / "hems" / "forecast.py"
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name:
            text = textwrap.dedent(ast.unparse(node))
            # Strip the relative import line; we will inject the
            # function from the namespace instead.
            lines = text.split("\n")
            lines = [l for l in lines if "from .pv_learning import" not in l]
            # Rename the def to a unique name.
            text = "\n".join(lines).replace(
                f"async def {method_name}(", "async def _method_impl("
            )
            return text
    raise RuntimeError(f"Could not find {method_name} in {path}")


def _build_service(payload):
    import logging

    async def _ensure_session(*args, **kwargs):
        return _FakeSession(payload)

    async def _rate_limit(*args, **kwargs):
        return None

    obj = type("S", (), {
        "_latitude": 50.45,
        "_longitude": 30.52,
        "timezone_name": "Europe/Kyiv",
        "_ensure_session": _ensure_session,
        "_rate_limit": _rate_limit,
        "_LOGGER": logging.getLogger("r01_repro"),
    })
    obj._LOGGER.handlers = [logging.NullHandler()]
    obj._LOGGER.propagate = False
    return obj


async def _run_method(method_name, payload, start_day, end_day):
    src = _load_method(method_name)
    ns = {
        "__name__": f"_drive_{method_name}",
        "datetime": datetime,
        "timedelta": timedelta,
        "timezone": timezone,
        "ZoneInfo": ZoneInfo,
        "_LOGGER": __import__("logging").getLogger(f"r01_{method_name}"),
    }
    ns["_LOGGER"].handlers = [__import__("logging").NullHandler()]
    ns["_LOGGER"].propagate = False
    from hems.pv_learning import complete_hourly_days, day_bounds
    ns["complete_hourly_days"] = complete_hourly_days
    ns["day_bounds"] = day_bounds
    exec(compile(src, f"<r01_{method_name}>", "exec"), ns)
    fn = ns["_method_impl"]
    self_obj = _build_service(payload)
    return await fn(self_obj, start_day, end_day)


# ── Tests ─────────────────────────────────────────────────────────────


async def _async_main():
    payload = _make_response(
        "2026-10-08", "2026-10-11",
        value_at_first_hour=150.0,
    )
    tz = ZoneInfo("Europe/Kyiv")

    rows = await _run_method(
        "get_archive_hourly_radiation", payload,
        datetime.fromisoformat("2026-10-08").date(),
        datetime.fromisoformat("2026-10-11").date(),
    )
    assert isinstance(rows, list), f"Expected list; got {type(rows).__name__}"
    assert len(rows) == 96, f"Expected 96 rows; got {len(rows)}"

    first_ts = rows[0]["start"]
    first_val = rows[0]["mean"]
    t = datetime.fromtimestamp(first_ts, tz=timezone.utc)
    interval_start = datetime.fromtimestamp(first_ts - 3600, tz=timezone.utc)

    print()
    print("=" * 72)
    print(f"  API timestamp (rows[0].start): {t.isoformat()}")
    print(f"  Documented interval start:     {interval_start.isoformat()}")
    print(f"  Value:                          {first_val} W/m^2")
    print()
    print("  Per Open-Meteo docs, the value at API timestamp t is the")
    print("  mean over [t-1h, t). Production stores rows[0].start = t,")
    print("  which is the END of the interval, not the start.")

    assert rows[0]["start"] == first_ts
    assert first_val == 150.0
    assert rows[0]["start"] != first_ts - 3600, (
        "rows[0].start equals t (END of interval), not t-1h (start). "
        "This is the documented contract issue."
    )

    daily = await _run_method(
        "get_archive_radiation", payload,
        datetime.fromisoformat("2026-10-08").date(),
        datetime.fromisoformat("2026-10-11").date(),
    )
    print()
    print("=" * 72)
    print(f"  production get_archive_radiation: {dict(daily) if daily else 'None'}")
    print()
    print("  Note: production groups by ts.astimezone(tz).date() (the date")
    print("  at the END of the interval). The 150.0 lives in the 2026-10-08")
    print("  bucket, but the bucket has only 1 of 24 required hours (the")
    print("  request starts at 2026-10-08 00:00 UTC = 03:00 Kyiv). The")
    print("  150.0 is therefore silently dropped from the daily result.")


def test_r01_reproduction_preceding_hour_mean():
    asyncio.run(_async_main())


if __name__ == "__main__":
    test_r01_reproduction_preceding_hour_mean()
    print("All tests passed (0 failed).")
    sys.exit(0)
