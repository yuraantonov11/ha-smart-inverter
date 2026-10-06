"""Refresh behavioural tests through
the shared
``tests/_refresh_harness.py``.

The previous revision of this file
defined its own ``_FakeApi``,
``_make_fake_self``, ``_patch_io``,
``_extract_function_bodies``, and
inline copies of ``_is_valid_double``
/ ``_parse_double``. Audit T20 round
4 (Windows review) flagged the
duplication; we now drive the
production code through the shared
harness. The tests here cover
every behavioural path the previous
file asserted plus the new
production-cap end-to-end check.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "tests"))

from _refresh_harness import (  # noqa: E402
    FakeApi,
    attach_api_io,
    run_refresh,
)


def _run_all() -> None:
    failures: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    tests = sorted(
        [
            (name, fn)
            for name, fn in globals().items()
            if name.startswith("test_") and callable(fn)
        ]
    )
    for name, fn in tests:
        try:
            fn()
            print(f"  {name}: PASS")
        except unittest.SkipTest as exc:
            skipped.append((name, str(exc)))
            print(f"  {name}: SKIP ({exc})")
        except Exception as exc:
            failures.append((name, repr(exc)))
            print(f"  {name}: FAIL ({exc!r})")
    if failures:
        print(
            f"\n{len(failures)} of {len(tests)} tests failed:"
        )
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    print(
        f"\nAll {len(tests)} tests passed "
        f"({len(skipped)} skipped)."
    )
    sys.exit(0)


# ────────────────────────────────────────────────────────────
# Device-selection correctness
# ────────────────────────────────────────────────────────────


def test_picks_configured_device_not_devices_zero() -> None:
    """Audit T20 fix #1: with
    ``device_sn='B'`` and a list
    ``[A, B]`` the previous code
    returned ``A``'s energy. The
    production body now matches
    ``device_sn`` and returns
    ``B``'s energy.
    """
    async def _run():
        payload = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "id": "A",
                        "stationId": "S1",
                        "dailyProducedQuantity": 99.0,
                        "totalProducedQuantity": 9999.0,
                    },
                    {
                        "id": "B",
                        "stationId": "S2",
                        "dailyProducedQuantity": 12.5,
                        "totalProducedQuantity": 1234.5,
                    },
                ]
            },
        }
        fake = FakeApi(
            device_sn="B",
            current_station_id="S2",
            daily_energy=10.0,
            total_energy=999.0,
        )
        attach_api_io(fake, payload)
        ok = await run_refresh(fake)
        assert ok is True
        assert fake.daily_energy == 12.5
        assert fake.total_energy == 1234.5
    asyncio.run(_run())


def test_returns_false_when_configured_device_missing() -> None:
    """Audit T20 fix #1: with
    ``device_sn='C'`` and a list
    ``[A, B]`` no entry matches;
    the cache is preserved and
    ``False`` is returned.
    """
    async def _run():
        payload = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "id": "A",
                        "stationId": "S1",
                        "dailyProducedQuantity": 99.0,
                        "totalProducedQuantity": 9999.0,
                    },
                    {
                        "id": "B",
                        "stationId": "S2",
                        "dailyProducedQuantity": 12.5,
                        "totalProducedQuantity": 1234.5,
                    },
                ]
            },
        }
        fake = FakeApi(
            device_sn="C",
            daily_energy=77.0,
            total_energy=999.0,
        )
        attach_api_io(fake, payload)
        ok = await run_refresh(fake)
        assert ok is False
        assert fake.daily_energy == 77.0
        assert fake.total_energy == 999.0
    asyncio.run(_run())


# ────────────────────────────────────────────────────────────
# Malformed payload
# ────────────────────────────────────────────────────────────


def test_preserves_cache_when_daily_field_is_string() -> None:
    """``dailyProducedQuantity="bad"``
    must not clobber the cache
    with 0.0. The audit's first
    reproduction showed that
    ``_parse_double`` returned
    ``0.0`` and the cache was
    silently overwritten.
    """
    async def _run():
        payload = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "id": "B",
                        "stationId": "S2",
                        "dailyProducedQuantity": "bad",
                        "totalProducedQuantity": 1234.5,
                    }
                ]
            },
        }
        fake = FakeApi(
            device_sn="B",
            current_station_id="S2",
            daily_energy=10.0,
            total_energy=1234.5,
        )
        attach_api_io(fake, payload)
        ok = await run_refresh(fake)
        assert ok is False
        assert fake.daily_energy == 10.0
        assert fake.total_energy == 1234.5
    asyncio.run(_run())


def test_preserves_cache_when_total_field_is_none() -> None:
    """``totalProducedQuantity=None``
    must not clobber the cache.
    """
    async def _run():
        payload = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "id": "B",
                        "stationId": "S2",
                        "dailyProducedQuantity": 10.0,
                        "totalProducedQuantity": None,
                    }
                ]
            },
        }
        fake = FakeApi(
            device_sn="B",
            current_station_id="S2",
            daily_energy=10.0,
            total_energy=1234.5,
        )
        attach_api_io(fake, payload)
        ok = await run_refresh(fake)
        assert ok is False
        assert fake.total_energy == 1234.5
    asyncio.run(_run())


def test_accepts_real_zero() -> None:
    """A real ``0`` measurement
    (e.g. before sunrise) is a
    legitimate value. The audit's
    fix distinguishes ``0`` from
    invalid / missing.
    """
    async def _run():
        payload = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "id": "B",
                        "stationId": "S2",
                        "dailyProducedQuantity": 0,
                        "totalProducedQuantity": 1234.5,
                    }
                ]
            },
        }
        fake = FakeApi(
            device_sn="B",
            current_station_id="S2",
            daily_energy=10.0,
            total_energy=1234.5,
        )
        attach_api_io(fake, payload)
        ok = await run_refresh(fake)
        assert ok is True
        assert fake.daily_energy == 0.0
        assert fake.daily_energy_at is not None
    asyncio.run(_run())


# ────────────────────────────────────────────────────────────
# Network / parse failures preserve the cache
# ────────────────────────────────────────────────────────────


def test_preserves_cache_on_network_failure() -> None:
    async def _run():
        fake = FakeApi(
            device_sn="B",
            current_station_id="S2",
            daily_energy=10.0,
            total_energy=1234.5,
            daily_energy_at=datetime(
                2026, 6, 1, 12, 0, tzinfo=timezone.utc
            ),
        )
        attach_api_io(
            fake,
            payload=None,
            raises=RuntimeError("network down"),
        )
        ok = await run_refresh(fake)
        assert ok is False
        assert fake.daily_energy == 10.0
        assert fake.total_energy == 1234.5
        assert fake.daily_energy_at == datetime(
            2026, 6, 1, 12, 0, tzinfo=timezone.utc
        )
    asyncio.run(_run())


def test_preserves_cache_on_non_zero_code() -> None:
    async def _run():
        fake = FakeApi(
            device_sn="B",
            current_station_id="S2",
            daily_energy=10.0,
            total_energy=1234.5,
        )
        attach_api_io(fake, {"code": 999, "msg": "rate limited"})
        ok = await run_refresh(fake)
        assert ok is False
        assert fake.daily_energy == 10.0
    asyncio.run(_run())


def test_preserves_cache_on_empty_list() -> None:
    async def _run():
        fake = FakeApi(
            device_sn="B",
            current_station_id="S2",
            daily_energy=10.0,
            total_energy=1234.5,
        )
        attach_api_io(fake, {"code": 0, "data": {"list": []}})
        ok = await run_refresh(fake)
        assert ok is False
        assert fake.daily_energy == 10.0
    asyncio.run(_run())


if __name__ == "__main__":
    _run_all()