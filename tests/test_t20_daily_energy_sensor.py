"""T20 regression tests: daily_energy freshness contract.

Audit T20: the ``InverterDailyEnergySensor``
must not surface yesterday's value as today's
morning reading, must not substitute measured
energy with a forecast, and must flag
multi-device accounts.

The freshness logic itself lives in
``hems/energy_freshness.py`` - a pure
stdlib module so we can test it without
spinning up Home Assistant. The sensor
delegates to that module.

Pure stdlib - no HA imports. Runs as
``python tests/test_t20_daily_energy_sensor.py``
and through ``tests/run_all.py``.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from hems.energy_freshness import (  # noqa: E402
    DEFAULT_FRESHNESS_HOURS,
    DailyEnergyFreshness,
    compute_daily_energy_freshness,
    daily_energy_for_today,
)


# ───────────────────────────────────────────────────────
# Test 1: freshness triple is exposed for the sensor
# ───────────────────────────────────────────────────────


def test_freshness_helper_exposes_required_fields() -> None:
    """Audit T20.1: the API must publish the
    freshness triple - ``daily_energy_date``,
    ``daily_energy_at``, ``daily_energy_stale``
    - so the sensor can read it without
    inventing its own.
    """
    today = date(2026, 6, 15)
    fresh = compute_daily_energy_freshness(
        daily_energy_at=datetime(
            2026, 6, 15, 9, 30, tzinfo=timezone.utc
        ),
        daily_energy_date=today,
        now=datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc),
    )
    assert isinstance(fresh, DailyEnergyFreshness)
    assert fresh.daily_energy_date == today
    assert fresh.daily_energy_at == datetime(
        2026, 6, 15, 9, 30, tzinfo=timezone.utc
    )
    assert fresh.daily_energy_stale is False


# ───────────────────────────────────────────────────────
# Test 2: midnight reset - yesterday's value is hidden
# ───────────────────────────────────────────────────────


def test_midnight_reset_zeros_value_for_today() -> None:
    """Audit T20.2: when the API still reports
    yesterday's value, the sensor must report
    ``0.0`` for today (today's accumulator
    starts at zero, no forecast substitution).
    """
    today = date(2026, 6, 15)
    yesterday = today - timedelta(days=1)
    freshness = compute_daily_energy_freshness(
        daily_energy_at=datetime(
            2026, 6, 14, 23, 55, tzinfo=timezone.utc
        ),
        daily_energy_date=yesterday,
        now=datetime(2026, 6, 15, 9, 30, tzinfo=timezone.utc),
    )
    value = daily_energy_for_today(
        value=18.5,
        freshness=freshness,
        today=today,
    )
    assert value == 0.0, (
        f"Sensor must return 0.0 for today when the "
        f"API value is dated yesterday; got {value!r}. "
        "The audit forbids substituting yesterday's "
        "value for today's reading."
    )


# ───────────────────────────────────────────────────────
# Test 3: today value is exposed
# ───────────────────────────────────────────────────────


def test_today_value_is_exposed() -> None:
    """Audit T20 positive case: when the API
    value is dated today, the helper returns
    it unchanged.
    """
    today = date(2026, 6, 15)
    freshness = compute_daily_energy_freshness(
        daily_energy_at=datetime(
            2026, 6, 15, 9, 30, tzinfo=timezone.utc
        ),
        daily_energy_date=today,
        now=datetime(2026, 6, 15, 9, 35, tzinfo=timezone.utc),
    )
    value = daily_energy_for_today(
        value=12.34,
        freshness=freshness,
        today=today,
    )
    assert abs(value - 12.34) < 1e-9, (
        f"Sensor must return the API value for "
        f"today; got {value!r}."
    )


# ───────────────────────────────────────────────────────
# Test 4: stale marker exposed
# ───────────────────────────────────────────────────────


def test_stale_marker_exposed() -> None:
    """Audit T20.3: a value older than the
    freshness threshold produces
    ``daily_energy_stale=True``.
    """
    today = date(2026, 6, 15)
    freshness = compute_daily_energy_freshness(
        daily_energy_at=datetime(
            2026, 6, 15, 1, 0, tzinfo=timezone.utc
        ),
        daily_energy_date=today,
        now=datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
    )
    assert freshness.daily_energy_stale is True, (
        "Helper must mark the value stale when "
        "older than the freshness threshold."
    )


# ───────────────────────────────────────────────────────
# Test 5: never-refreshed -> unknown
# ───────────────────────────────────────────────────────


def test_never_refreshed_returns_unknown() -> None:
    """Audit T20.3: when the API has never
    refreshed the value, ``daily_energy_at``
    and ``daily_energy_date`` are ``None``.
    The helper returns ``None`` for the
    today's value - the audit forbids
    substituting a measured value with a
    forecast.
    """
    today = date(2026, 6, 15)
    freshness = compute_daily_energy_freshness(
        daily_energy_at=None,
        daily_energy_date=None,
        now=datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
    )
    assert freshness.daily_energy_stale is True
    assert freshness.daily_energy_date is None
    value = daily_energy_for_today(
        value=0.0,
        freshness=freshness,
        today=today,
    )
    assert value is None, (
        "Helper must return None when the API "
        f"never refreshed; got {value!r}."
    )


# ───────────────────────────────────────────────────────
# Test 6: freshness threshold default is 6 hours
# ───────────────────────────────────────────────────────


def test_default_freshness_threshold_is_six_hours() -> None:
    """Audit T20.1 follow-up: the default
    freshness threshold is 6 hours so a normal
    30-minute polling slip does not flip the
    sensor to stale.
    """
    assert DEFAULT_FRESHNESS_HOURS == 6, (
        "Production default must be 6 hours; "
        f"got {DEFAULT_FRESHNESS_HOURS}."
    )
    # 5h59m: still fresh.
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    fresh = compute_daily_energy_freshness(
        daily_energy_at=now - timedelta(hours=5, minutes=59),
        daily_energy_date=now.date(),
        now=now,
    )
    assert fresh.daily_energy_stale is False
    # 6h01m: stale.
    stale = compute_daily_energy_freshness(
        daily_energy_at=now - timedelta(hours=6, minutes=1),
        daily_energy_date=now.date(),
        now=now,
    )
    assert stale.daily_energy_stale is True


# ───────────────────────────────────────────────────────
# Test 7: future timestamp is stale
# ───────────────────────────────────────────────────────


def test_future_timestamp_is_stale() -> None:
    """Audit T20.1 follow-up: a timestamp
    from the future is suspicious. The helper
    marks it stale rather than silently
    trusting it.
    """
    today = date(2026, 6, 15)
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    freshness = compute_daily_energy_freshness(
        daily_energy_at=now + timedelta(hours=1),
        daily_energy_date=today,
        now=now,
    )
    assert freshness.daily_energy_stale is True


# ───────────────────────────────────────────────────────
# Test 8: API error path
# ───────────────────────────────────────────────────────


def test_api_error_path_returns_unknown() -> None:
    """Audit T20.3: when the API access
    raises ``InverterApiError`` (or any
    other exception), the freshness helper
    must surface ``unknown``. We simulate
    the API failure by passing
    ``daily_energy_at=None`` (the API
    defaults to ``None`` when it never
    refreshed).
    """
    today = date(2026, 6, 15)
    freshness = compute_daily_energy_freshness(
        daily_energy_at=None,
        daily_energy_date=None,
        now=datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
    )
    value = daily_energy_for_today(
        value=0.0,
        freshness=freshness,
        today=today,
    )
    assert value is None, (
        "Helper must return None on API error; "
        f"got {value!r}."
    )


# ───────────────────────────────────────────────────────
# Test 9: freshness helper is pure stdlib
# ───────────────────────────────────────────────────────


def test_helper_does_not_import_homeassistant() -> None:
    """Audit T20.2 follow-up: the helper
    module must not import Home Assistant -
    it lives in ``hems/`` and is consumed by
    the sensor as a pure-stdlib module.
    """
    import hems.energy_freshness as ef

    for name in ef.__dict__:
        obj = getattr(ef, name)
        if hasattr(obj, "__module__"):
            assert "homeassistant" not in str(obj.__module__), (
                f"{name} is sourced from "
                f"{obj.__module__!r}; the helper "
                "module must be pure stdlib."
            )


# ───────────────────────────────────────────────────────
# Test 10: sensor source declares the freshness hook
# ───────────────────────────────────────────────────────


def test_sensor_source_uses_helper() -> None:
    """Audit T20.1 follow-up: the production
    ``sensor.py`` must call the freshness
    helper rather than reimplement the
    midnight-reset logic. We assert this
    via AST scan.
    """
    import ast

    sensor_path = os.path.join(_REPO_ROOT, "sensor.py")
    if not os.path.exists(sensor_path):
        # The integration tests
        # explicitly skip this check if
        # sensor.py is absent (a
        # bare-bones CI environment).
        return
    with open(sensor_path) as f:
        tree = ast.parse(f.read())
    # Look for either:
    # - an import of the helper, OR
    # - a call to
    #   ``compute_daily_energy_freshness`` /
    #   ``daily_energy_for_today``.
    found_helper_import = False
    found_helper_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.endswith(
                "hems.energy_freshness"
            ):
                found_helper_import = True
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr in {
                    "compute_daily_energy_freshness",
                    "daily_energy_for_today",
                }:
                    found_helper_call = True
            elif isinstance(func, ast.Name):
                if func.id in {
                    "compute_daily_energy_freshness",
                    "daily_energy_for_today",
                }:
                    found_helper_call = True
    assert found_helper_import or found_helper_call, (
        "Production sensor.py must delegate "
        "freshness to hems.energy_freshness "
        "(import or call found: "
        f"import={found_helper_import}, "
        f"call={found_helper_call})."
    )


def _run_all() -> None:
    """Run every test in this module.
    """
    import inspect

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


if __name__ == "__main__":
    _run_all()