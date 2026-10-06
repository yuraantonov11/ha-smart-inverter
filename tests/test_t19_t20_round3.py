"""T19/T20 round 3 (audit Windows
review) - behavioural tests
through the SHARED harness.

Audit T20 round 4 (Windows review)
findings rolled into this file:

  * No copy of ``_is_valid_double``
    or ``_parse_double``. The
    harness binds the production
    helpers on the ``FakeApi`` class.
  * No copy of ``_extract_function_bodies``;
    ``tests/_refresh_harness.extract_production_body``
    is the single shared extractor.
  * No copy of the ``_MAX_DAILY_KWH``
    constant; the fake carries it as
    a class attribute, overrideable
    per test.
  * DST test skips with
    ``unittest.SkipTest`` (not
    ``pytest.skip``).
  * Midnight test uses UTC+03:00
    fixed offset (no IANA lookup),
    so Windows without tzdata does
    not raise ``ZoneInfoNotFoundError``.
  * Confidence test compares
    ``one usable`` vs ``one usable
    + two gap-filled`` - the
    earlier test used three usable
    rows which already hit the
    ceiling, so it could not detect
    over-counting.

These tests are behavioural
regressions: the previous T20
round-3 tests in this module were
green because the harness did not
pass ``ENDPOINT_DEVICE_LIST`` into
the namespace; the production body
silently raised ``NameError`` and
returned ``False``. The shared
harness passes the constant.
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
    StubCoordinator,
    attach_api_io,
    run_maybe_refresh,
    run_refresh,
)


# ────────────────────────────────────────────────────────────
# T20.2 (validator round 1): cumulative total accepted
# ────────────────────────────────────────────────────────────


def test_cumulative_total_above_20000_validator() -> None:
    """Audit T20 round 3: a station
    with a cumulative
    ``totalProducedQuantity`` of
    25 000 kWh must not be silently
    rejected by the validator. The
    audit was: a universal
    ``<= 20 000`` upper cap is
    wrong for the cumulative total.
    """
    # The shared harness binds the
    # production ``_is_valid_double``
    # as a class attribute on the
    # ``FakeApi``. Calling it
    # through an instance drives
    # the real production code path
    # - we do not reimplement the
    # validator.
    fake = FakeApi()
    assert fake._is_valid_double(25000.0, 25000.0) is True, (
        "Cumulative 25 000 kWh must be "
        "accepted; the production validator "
        "is supposed to drop the "
        "universal upper cap."
    )
    assert fake._is_valid_double(999999.0, 999999.0) is True, (
        "999 999 kWh must also be "
        "accepted."
    )
    assert fake._is_valid_double("bad", 0.0) is False, (
        "Invalid raw value must be "
        "rejected."
    )
    assert fake._is_valid_double(None, 0.0) is False


# ────────────────────────────────────────────────────────────
# T19.1 (round 3): date from response completion
# ────────────────────────────────────────────────────────────


def test_daily_energy_date_uses_response_completion() -> None:
    """Audit T19/T20 round 3:
    ``_maybe_refresh_energy_stats``
    must compute
    ``daily_energy_date`` from
    ``api.daily_energy_at`` (the
    response-completion timestamp
    that ``refresh_device_summary``
    wrote), not from a pre-await
    ``now``.
    """
    async def _run():
        api = FakeApi(device_sn="B", current_station_id="S2")
        coord = StubCoordinator(
            api,
            ttl_s=0,
            site_tz_offset=timezone(timedelta(hours=3)),
            hass_config_time_zone="UTC+03:00",
        )
        response_completion_utc = datetime(
            2026, 10, 5, 21, 0, 4, tzinfo=timezone.utc
        )

        async def _refresh():
            api.daily_energy_at = response_completion_utc
            return True

        pre_await_now = datetime(
            2026, 10, 5, 20, 59, 55, tzinfo=timezone.utc
        )
        await run_maybe_refresh(
            coord, pre_await_now, refresh_side_effect=_refresh
        )
        assert api.daily_energy_date == date(2026, 10, 6), (
            f"daily_energy_date must be 6 Oct "
            f"in UTC+3; got {api.daily_energy_date}. "
            f"Audit T19 round 3: pre-await "
            f"``now`` would give 5 Oct."
        )
    asyncio.run(_run())


# ────────────────────────────────────────────────────────────
# T20.4 (round 3): real TTL behaviour
# ────────────────────────────────────────────────────────────


def test_real_ttl_throttles_within_ttl() -> None:
    """Audit T20 round 3: drive
    ``_maybe_refresh_energy_stats``
    end-to-end.
    """
    async def _run():
        api = FakeApi(device_sn="B", current_station_id="S2")
        coord = StubCoordinator(api, ttl_s=1)
        refresh_calls = {"n": 0}

        async def _refresh():
            refresh_calls["n"] += 1
            api.daily_energy_at = datetime.now(tz=timezone.utc)
            return True

        t0 = datetime(2026, 10, 5, 21, 0, 0, tzinfo=timezone.utc)
        called1 = await run_maybe_refresh(
            coord, t0, refresh_side_effect=_refresh
        )
        assert called1 is True

        t1 = t0 + timedelta(seconds=0.5)
        called2 = await run_maybe_refresh(
            coord, t1, refresh_side_effect=_refresh
        )
        assert called2 is False, (
            f"Second call within TTL must NOT "
            f"refresh; got {called2}."
        )

        t2 = t0 + timedelta(seconds=2)
        called3 = await run_maybe_refresh(
            coord, t2, refresh_side_effect=_refresh
        )
        assert called3 is True

        assert refresh_calls["n"] == 2, (
            f"Two API calls (first + after TTL); "
            f"got {refresh_calls['n']}."
        )
    asyncio.run(_run())


def test_real_ttl_preserves_cache_on_failure() -> None:
    """On refresh failure the cache
    is preserved.
    """
    async def _run():
        api = FakeApi(
            device_sn="B",
            current_station_id="S2",
            daily_energy=10.0,
            total_energy=1000.0,
            daily_energy_at=datetime(
                2026, 10, 5, 21, 0, 0, tzinfo=timezone.utc
            ),
            daily_energy_date=date(2026, 10, 5),
        )
        coord = StubCoordinator(api, ttl_s=0)

        async def _refresh():
            return False

        before_at = api.daily_energy_at
        before_date = api.daily_energy_date
        await run_maybe_refresh(
            coord,
            datetime.now(tz=timezone.utc),
            refresh_side_effect=_refresh,
        )
        assert api.daily_energy == 10.0
        assert api.total_energy == 1000.0
        assert api.daily_energy_at == before_at
        assert api.daily_energy_date == before_date
    asyncio.run(_run())


# ────────────────────────────────────────────────────────────
# T19.5 (round 3): confidence over-count
# ────────────────────────────────────────────────────────────


def test_confidence_does_not_over_count_gap_rows() -> None:
    """One usable vs one usable +
    two gap-filled rows must give
    the same confidence. The
    earlier ``3 usable rows``
    test already hit the
    ``history_factor = 0.5``
    ceiling and could not detect
    over-counting.
    """
    from hems.telemetry import (
        build_planner_inputs,
    )
    from hems.predictive import (
        PredictiveHemsController,
    )

    real = [600.0] * 24
    gap = [600.0] * 24
    inputs_a = build_planner_inputs(
        {},
        consumption_history=[
            (date(2026, 6, 1), real, False),
        ],
        battery_capacity_kwh=10.0,
        forecast_tomorrow_kwh=5.0,
    )
    inputs_b = build_planner_inputs(
        {},
        consumption_history=[
            (date(2026, 6, 1), real, False),
            (date(2026, 6, 2), gap, True),
            (date(2026, 6, 3), gap, True),
        ],
        battery_capacity_kwh=10.0,
        forecast_tomorrow_kwh=5.0,
    )
    ctrl = PredictiveHemsController()
    conf_a = ctrl._estimate_confidence(inputs_a)
    conf_b = ctrl._estimate_confidence(inputs_b)
    assert abs(conf_a - conf_b) < 1e-9, (
        f"One usable + two gap-filled must "
        f"equal one usable alone; got "
        f"a={conf_a} b={conf_b}."
    )


# ────────────────────────────────────────────────────────────
# T20 round 4 - daily cap via shared harness
# ────────────────────────────────────────────────────────────


def test_daily_above_max_daily_kwh_is_rejected_via_shared_harness() -> None:
    """Audit T20 round 4: the
    shared ``run_refresh`` driver
    passes ``ENDPOINT_DEVICE_LIST``
    into the namespace, so the
    production cap check is
    exercised. A 5 000 kWh daily
    reading is rejected; the cache
    stays put.
    """
    async def _run():
        payload = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "id": "B",
                        "stationId": "S2",
                        "dailyProducedQuantity": 5000.0,
                        "totalProducedQuantity": 1000.0,
                    }
                ]
            },
        }
        fake = FakeApi(
            device_sn="B",
            current_station_id="S2",
            daily_energy=100.0,
            total_energy=800.0,
        )
        log = []
        attach_api_io(fake, payload, request_log=log)
        ok = await run_refresh(fake)
        assert ok is False, (
            "Daily reading of 5 000 kWh must be "
            f"rejected; got ok={ok}. The harness "
            f"passed ENDPOINT_DEVICE_LIST so the "
            f"production cap check ran."
        )
        assert fake.daily_energy == 100.0
        assert fake.total_energy == 800.0
    asyncio.run(_run())


def test_valid_daily_updates_cache_via_shared_harness() -> None:
    """A valid daily reading
    updates the cache through the
    production body.
    """
    async def _run():
        payload = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "id": "B",
                        "stationId": "S2",
                        "dailyProducedQuantity": 12.5,
                        "totalProducedQuantity": 1234.5,
                    }
                ]
            },
        }
        fake = FakeApi(
            device_sn="B",
            current_station_id="S2",
            daily_energy=10.0,
            total_energy=999.0,
        )
        log = []
        attach_api_io(fake, payload, request_log=log)
        ok = await run_refresh(fake)
        assert ok is True
        assert fake.daily_energy == 12.5
        assert fake.total_energy == 1234.5
        assert len(log) == 1
    asyncio.run(_run())


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


if __name__ == "__main__":
    _run_all()