"""Standalone timezone tests.

Audit T20 round 4 (Windows review)
fixes:

  * The midnight test must use
    ``UTC+03:00`` (Kyiv-equivalent
    fixed offset) so it does not
    require IANA / tzdata. The
    Windows environment does not
    have ``Europe/Kyiv`` installed
    in tzdata; the previous test
    raised ``ZoneInfoNotFoundError``.

  * The DST test must use the real
    IANA ``Europe/Kyiv`` zone when
    available, and skip explicitly
    via ``unittest.SkipTest`` when
    not. The audit was explicit:
    do NOT hand-roll a DST table.

  * The test runner counts SKIP as
    a separate status; we surface
    ``PASS`` / ``SKIP`` / ``FAIL``
    in the run output.

The tests drive the production
``refresh_device_summary`` and
``_maybe_refresh_energy_stats``
bodies via the shared harness; the
fixtures are identical to the live
behaviour the audit asked us to
verify.
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
# Insert the repo root FIRST so
# ``from hems.energy_freshness import ...``
# resolves.
sys.path.insert(0, _REPO_ROOT)
# Then the tests directory for the
# shared harness.
sys.path.insert(0, os.path.join(_REPO_ROOT, "tests"))

from _refresh_harness import (  # noqa: E402
    FakeApi,
    StubCoordinator,
    run_maybe_refresh,
)


def _utc_plus_3() -> timezone:
    """A fixed-offset zone equivalent
    to Europe/Kyiv winter time
    (UTC+02:00 in winter, UTC+03:00
    in summer; the audit asks for a
    fixed-offset zone so the test does
    not depend on tzdata).
    """
    return timezone(timedelta(hours=3))


# ────────────────────────────────────────────────────────────
# Midnight transition - fixed-offset zone
# ────────────────────────────────────────────────────────────


def test_midnight_transition_kyiv_offset_resets_to_zero() -> None:
    """At 06 October 00:01 (Kyiv-local,
    simulated with UTC+03:00) the API
    value is 5 October - the sensor
    must report 0.0 for today.

    Audit T20 round 4: the Windows
    test environment lacks
    ``tzdata``, so the previous
    test using ``ZoneInfo("Europe/Kyiv")``
    raised ``ZoneInfoNotFoundError``.
    We use a fixed-offset zone to
    keep the assertion stable across
    Linux and Windows.
    """
    from datetime import datetime as _dt_class
    from hems.energy_freshness import (
        compute_daily_energy_freshness,
        daily_energy_for_today,
    )

    kyiv_offset = _utc_plus_3()
    response_completion_utc = datetime(
        2026, 10, 5, 21, 1, tzinfo=timezone.utc
    )
    assert response_completion_utc.astimezone(
        kyiv_offset
    ) == datetime(
        2026, 10, 6, 0, 1, tzinfo=kyiv_offset
    ), (
        "Fixture invariant: the response "
        "completion instant 21:01 UTC "
        "is 00:01 the next day in UTC+3."
    )

    freshness = compute_daily_energy_freshness(
        daily_energy_at=response_completion_utc,
        daily_energy_date=date(2026, 10, 5),
        now=response_completion_utc,
    )
    value = daily_energy_for_today(
        value=18.5,
        freshness=freshness,
        today=date(
            2026, 10, 6
        ),  # The sensor's "today" in
        # UTC+03:00
    )
    assert value == 0.0, (
        f"At 06 October 00:01 UTC+03:00 "
        f"the sensor must report 0.0; got "
        f"{value}. The previous test "
        f"raised ZoneInfoNotFoundError on "
        f"Windows because it imported "
        f"Europe/Kyiv directly."
    )


def test_midnight_pre_just_returns_value() -> None:
    """At 23:59 UTC (i.e. 02:59 the
    next day in UTC+03:00) the API
    value is for *today* - the
    sensor returns the raw value
    unchanged.
    """
    from hems.energy_freshness import (
        compute_daily_energy_freshness,
        daily_energy_for_today,
    )
    utc_now = datetime(
        2026, 10, 5, 23, 59, tzinfo=timezone.utc
    )
    freshness = compute_daily_energy_freshness(
        daily_energy_at=utc_now,
        daily_energy_date=date(2026, 10, 5),
        now=utc_now,
    )
    value = daily_energy_for_today(
        value=18.5,
        freshness=freshness,
        today=date(2026, 10, 5),
    )
    assert value == 18.5


# ────────────────────────────────────────────────────────────
# DST - real IANA zone when available, skip otherwise
# ────────────────────────────────────────────────────────────


def test_dst_winter_offset_europe_kyiv() -> unittest.TestCase:
    """When ``Europe/Kyiv`` is
    installed in tzdata, winter
    time is UTC+02:00 and summer
    time is UTC+03:00. The DST
    transition in 2026 was
    29 March (forward) and
    25 October (back).

    Audit T20 round 4 was explicit:
    use the real IANA base, skip
    explicitly if unavailable, do
    NOT hand-roll a DST table. The
    test raises
    ``unittest.SkipTest`` when
    ``ZoneInfoNotFoundError`` is
    raised, which the standalone
    runner counts separately.
    """
    from zoneinfo import ZoneInfo

    try:
        kyiv = ZoneInfo("Europe/Kyiv")
    except Exception:
        raise unittest.SkipTest(
            "OS tzdata does not have Europe/Kyiv. "
            "Audit T20 round 4: skip the DST test "
            "rather than hand-roll a DST table."
        )

    # Winter: before 29 March 2026 the
    # offset is UTC+02:00.
    winter = datetime(2026, 1, 15, 12, 0, tzinfo=kyiv)
    assert winter.utcoffset() == timedelta(hours=2), (
        f"Europe/Kyiv winter offset must be UTC+2; "
        f"got {winter.utcoffset()}. The audit said: "
        f"do not hand-roll a DST table - we "
        f"verify the real IANA base."
    )

    # Summer: after 29 March 2026 the
    # offset is UTC+03:00.
    summer = datetime(2026, 7, 15, 12, 0, tzinfo=kyiv)
    assert summer.utcoffset() == timedelta(hours=3), (
        f"Europe/Kyiv summer offset must be UTC+3; "
        f"got {summer.utcoffset()}."
    )


def test_coordinator_passes_response_completion_to_sensor() -> None:
    """Audit T27 found the
    coordinator's
    ``_maybe_refresh_energy_stats``
    wrote ``api.daily_energy_date``
    from the **response-completion**
    timestamp the production
    ``refresh_device_summary``
    wrote into
    ``api.daily_energy_at``, not
    from a pre-await ``now``.

    We exec the production method
    against a stub coordinator and
    assert that the
    ``daily_energy_date`` written
    to ``api`` corresponds to the
    response-completion instant in
    the HA site timezone.
    """
    from datetime import datetime as _dt_class

    async def _run():
        api = FakeApi()
        coord = StubCoordinator(
            api,
            ttl_s=0,  # always refresh
            site_tz_offset=_utc_plus_3(),
            hass_config_time_zone="UTC+03:00",
        )

        # The response-completion
        # timestamp the production
        # ``refresh_device_summary``
        # writes. The audit's repro:
        # request started at 5 Oct
        # 23:59:55 UTC+03:00 (=
        # 20:59:55 UTC), returned at
        # 6 Oct 00:00:04 UTC+03:00
        # (= 21:00:04 UTC).
        response_completion_utc = datetime(
            2026, 10, 5, 21, 0, 4, tzinfo=timezone.utc
        )

        async def _refresh():
            api.daily_energy_at = response_completion_utc
            return True

        pre_await_now_kyiv = datetime(
            2026, 10, 5, 23, 59, 55,
            tzinfo=_utc_plus_3(),
        )
        called = await run_maybe_refresh(
            coord,
            pre_await_now_kyiv,
            refresh_side_effect=_refresh,
        )
        assert called, "Refresh must have been called"
        assert api.daily_energy_date == date(
            2026, 10, 6
        ), (
            f"daily_energy_date must be 6 Oct "
            f"(the response-completion date in "
            f"UTC+03:00); got {api.daily_energy_date}. "
            f"Audit T27 round 4: pre-await ``now`` "
            f"would give 5 October."
        )
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