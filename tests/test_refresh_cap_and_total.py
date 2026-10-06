"""Refresh correctness tests using
the shared
``tests/_refresh_harness.py``.

These tests are the audit's
behavioural replacement for the
three per-suite harnesses
(``test_t20_energy_stats_refresh.py``,
``test_t19_t20_round3.py``,
``test_predictive_wiring.py``). They
exercise the **production**
``InverterApiClient.refresh_device_summary``
body through the shared
``run_refresh`` driver and bind
``_parse_double`` /
``_is_valid_double`` on the
``FakeApi`` class as
``staticmethod``/method - no
hand-rolled copies of the
validator.

Audit T20 round 4 (Windows review)
defects caught here:

  * The previous ``test_daily_above_max_daily_kwh_is_rejected``
    in ``test_t19_t20_round3.py`` did
    not pass ``ENDPOINT_DEVICE_LIST``
    into the namespace, so the
    production body raised
    ``NameError`` and silently
    returned ``False`` BEFORE
    reaching the ``_MAX_DAILY_KWH``
    check. The shared harness passes
    the constant, so the test now
    verifies the production cap
    check is exercised.

  * Total values above 20 000 must
    be accepted end-to-end through
    ``refresh_device_summary``,
    not only at the validator
    surface.

  * Cumulative decrease is still
    rejected (monotonic protection).
"""

from __future__ import annotations

import asyncio
import os
import sys

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
sys.path.insert(0, os.path.join(_REPO_ROOT, "tests"))

from _refresh_harness import (  # noqa: E402
    FakeApi,
    attach_api_io,
    run_refresh,
)


def _run_all() -> None:
    import unittest

    failures: list[tuple[str, str]] = []
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
    print(f"\nAll {len(tests)} tests passed.")
    sys.exit(0)


# ────────────────────────────────────────────────────────────
# Daily cap behaviour
# ────────────────────────────────────────────────────────────


def test_valid_daily_updates_cache() -> None:
    """A daily reading within
    ``_MAX_DAILY_KWH`` updates the
    cache. This exercises the
    production ``_MAX_DAILY_KWH``
    branch (the audit's RED test).
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
        assert ok is True, (
            f"Valid daily 12.5 must return True; "
            f"got {ok}. The harness passed "
            f"ENDPOINT_DEVICE_LIST into the "
            f"namespace, so a NameError can no "
            f"longer hide the production cap "
            f"check."
        )
        assert fake.daily_energy == 12.5
        assert fake.total_energy == 1234.5
        assert len(log) == 1, (
            "Exactly one HTTP POST should "
            "have been made."
        )
    asyncio.run(_run())


def test_junk_daily_preserves_cache() -> None:
    """A daily reading above
    ``_MAX_DAILY_KWH`` (5 000 kWh)
    is rejected; the cache
    ``daily_energy`` /
    ``total_energy`` stay put.
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
        attach_api_io(fake, payload)
        ok = await run_refresh(fake)
        assert ok is False, (
            "Daily reading of 5 000 kWh must be "
            "rejected as out of range; got "
            f"ok={ok}. Audit T20 round 4: the "
            "shared harness must drive the "
            "production cap check, not a "
            "re-implementation."
        )
        assert fake.daily_energy == 100.0, (
            "Cache must be preserved on junk "
            "daily."
        )
        assert fake.total_energy == 800.0
    asyncio.run(_run())


# ────────────────────────────────────────────────────────────
# Cumulative total beyond 20 000
# ────────────────────────────────────────────────────────────


def test_total_above_20000_accepted_end_to_end() -> None:
    """A cumulative
    ``totalProducedQuantity`` of
    25 000 kWh is a legitimate
    lifetime total - not rejected
    at any point in the refresh
    path (validator, cap, decrease
    guard).
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
                        "totalProducedQuantity": 25000.0,
                    }
                ]
            },
        }
        fake = FakeApi(
            device_sn="B",
            current_station_id="S2",
            daily_energy=10.0,
            total_energy=24000.0,
        )
        log = []
        attach_api_io(fake, payload, request_log=log)
        ok = await run_refresh(fake)
        assert ok is True, (
            "Cumulative total 25 000 kWh must "
            "be accepted; got "
            f"ok={ok}. Audit T20 round 4: the "
            "cap is daily-only."
        )
        assert fake.daily_energy == 12.5
        assert fake.total_energy == 25000.0, (
            "Cache must be updated with the "
            "cumulative total."
        )
        assert len(log) == 1
    asyncio.run(_run())


def test_total_decrease_still_rejected() -> None:
    """Audit T20 round 2: a
    decrease in cumulative total
    is rejected (monotonic
    protection). The cumulative
    total ``1234`` after a
    previously stored ``5000`` is
    a re-mapped device or a stale
    cache; we keep the old value.
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
                        "totalProducedQuantity": 1234.0,
                    }
                ]
            },
        }
        fake = FakeApi(
            device_sn="B",
            current_station_id="S2",
            daily_energy=10.0,
            total_energy=5000.0,
        )
        attach_api_io(fake, payload)
        ok = await run_refresh(fake)
        assert ok is False, (
            "Decreasing total must be rejected; "
            f"got ok={ok}."
        )
        assert fake.total_energy == 5000.0
    asyncio.run(_run())


# ────────────────────────────────────────────────────────────
# Spy / request log
# ────────────────────────────────────────────────────────────


def test_request_log_records_one_post_per_call() -> None:
    """The harness records each
    POST the production body
    initiates, so a regression
    that adds a second POST per
    cycle surfaces as ``len(log) ==
    2``.
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
        await run_refresh(fake)
        assert len(log) == 1, (
            "Exactly one POST must be issued per "
            f"refresh; got {len(log)}."
        )
    asyncio.run(_run())


if __name__ == "__main__":
    _run_all()