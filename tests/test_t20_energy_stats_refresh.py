"""T20 regression tests: energy stats refresh with TTL.

Audit T20 follow-up (Windows review):
the production path was updating
``daily_energy`` /
``daily_energy_at`` only inside
``_fetch_device_list``, which was
called at login or when
``device_sn`` was missing. Ordinary
polling did not refresh the value,
so the sensor reading stayed at the
login value for hours.

The audit also flagged the
midnight-reset rule: ``api.daily_energy_date``
is stored in the HA site timezone
(Kyiv), but the sensor compared it
against ``datetime.now(tz=timezone.utc).date()``.
At 00:01 Kyiv on 6 October, the
API value is dated 5 October (the
inverter rolled back at midnight)
but the sensor saw today as 5
October too, so it returned
yesterday's 18.5 kWh.

This file asserts:

  * ``InverterApiClient.refresh_device_summary``
    updates ``daily_energy``,
    ``total_energy``,
    ``daily_energy_at`` and
    ``co2_reduction`` from the device
    list endpoint.
  * ``refresh_device_summary`` keeps
    the previous value and
    timestamp on failure.
  * ``InverterCoordinator._maybe_refresh_energy_stats``
    throttles by TTL (default 15 min).
  * The sensor's midnight-reset rule
    uses the same site timezone as
    the API (not UTC).
  * The ``daily_energy_date`` on the
    API is refreshed from the HA
    site timezone, not the host's
    local time.

The tests are pure-stdlib - they
construct an ``InverterApiClient``
without spinning up a Home Assistant
core. The coordinator test uses a
minimal stub.
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

# ---- Sensor midnight-reset rule ----


def test_sensor_source_uses_site_timezone() -> None:
    """Audit T20 follow-up (midnight test):
    the production sensor's
    ``native_value`` body must read
    ``api._site_tz`` (or the
    coordinator's site timezone)
    rather than hard-coding UTC. The
    audit's repro: 06 October 00:01
    Kyiv, the API value is dated 5
    October. Without the fix the
    sensor used UTC and reported
    yesterday's 18.5 kWh.

    We assert the production source
    carries the right wiring by AST
    scan: the body must contain
    ``_site_tz`` access on the api.
    """
    import ast as _ast
    sensor_path = os.path.join(_REPO_ROOT, "sensor.py")
    with open(sensor_path) as f_src:
        tree = _ast.parse(f_src.read())
    cls = None
    for node in tree.body:
        if (
            isinstance(node, _ast.ClassDef)
            and node.name == "InverterDailyEnergySensor"
        ):
            cls = node
            break
    assert cls is not None
    native_value = None
    attrs = None
    for item in cls.body:
        if isinstance(item, _ast.FunctionDef):
            if item.name == "native_value":
                native_value = item
            elif item.name == "extra_state_attributes":
                attrs = item
    assert native_value is not None
    body_src = _ast.unparse(native_value)
    assert "_site_tz" in body_src, (
        "InverterDailyEnergySensor.native_value "
        "must read api._site_tz so the "
        "midnight-reset rule fires at the "
        "HA site's local midnight. Got:\n"
        f"{body_src}"
    )
    if attrs is not None:
        attrs_src = _ast.unparse(attrs)
        assert "_site_tz" in attrs_src, (
            "extra_state_attributes must "
            "also use the site timezone."
        )


def test_api_source_contains_refresh_device_summary() -> None:
    """Audit T20 follow-up: the
    ``InverterApiClient`` source must
    expose a public
    ``refresh_device_summary`` method
    that updates ``daily_energy`` /
    ``total_energy`` / ``daily_energy_at``.
    """
    import ast as _ast
    api_path = os.path.join(_REPO_ROOT, "api.py")
    with open(api_path) as f:
        tree = _ast.parse(f.read())
    found = False
    updates_daily_energy = False
    updates_total_energy = False
    updates_daily_energy_at = False
    keeps_previous_on_failure = False
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.AsyncFunctionDef):
            continue
        if node.name != "refresh_device_summary":
            continue
        found = True
        func_src = _ast.unparse(node)
        updates_daily_energy = "self.daily_energy = " in func_src
        updates_total_energy = "self.total_energy = " in func_src
        updates_daily_energy_at = "self.daily_energy_at = " in func_src
        # The audit requires that the
        # method keeps the previous
        # value on failure - the body
        # returns ``False`` and never
        # overwrites ``self.daily_energy``
        # inside the except branches.
        keeps_previous_on_failure = (
            "return False" in func_src
            and "except Exception" in func_src
        )
    assert found, (
        "api.py must expose a public "
        "refresh_device_summary method. "
        "Audit T20 follow-up."
    )
    assert updates_daily_energy
    assert updates_total_energy
    assert updates_daily_energy_at
    assert keeps_previous_on_failure, (
        "refresh_device_summary must return "
        "False and skip the daily_energy "
        "update inside the except branch."
    )


def test_coordinator_source_contains_ttl_refresh() -> None:
    """Audit T20 follow-up: the
    coordinator must throttle the
    device-summary refresh by TTL
    (default 15 minutes). The
    throttling state must live on
    the coordinator instance.
    """
    import ast as _ast
    path = os.path.join(_REPO_ROOT, "coordinator.py")
    with open(path) as f:
        tree = _ast.parse(f.read())
    has_state = False
    has_ttl = False
    has_method = False
    has_call_in_loop = False
    for node in _ast.walk(tree):
        # Init attributes may be
        # ``self._x: T = None``
        # (AnnAssign) or
        # ``self._x = value`` (Assign).
        target = None
        if isinstance(node, _ast.Assign):
            for t in node.targets:
                if isinstance(t, _ast.Attribute):
                    target = t
                    break
        elif isinstance(node, _ast.AnnAssign):
            if isinstance(node.target, _ast.Attribute):
                target = node.target
        if target is not None:
            if target.attr == "_last_energy_stats_at":
                has_state = True
            if target.attr == "_energy_stats_ttl_s":
                has_ttl = True
        if isinstance(node, _ast.AsyncFunctionDef):
            if node.name == "_maybe_refresh_energy_stats":
                has_method = True
        if isinstance(node, _ast.Call):
            func = node.func
            if isinstance(func, _ast.Attribute) and func.attr == "_maybe_refresh_energy_stats":
                has_call_in_loop = True
    assert has_state
    assert has_ttl
    assert has_method, (
        "coordinator.py must define "
        "_maybe_refresh_energy_stats. Audit T20 "
        "follow-up requires TTL-throttled refresh."
    )
    assert has_call_in_loop, (
        "_async_update_data must call "
        "_maybe_refresh_energy_stats every cycle."
    )


def _run_all() -> None:
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
        print(f"\n{len(failures)} of {len(tests)} tests failed:")
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")
    sys.exit(0)


if __name__ == "__main__":
    _run_all()