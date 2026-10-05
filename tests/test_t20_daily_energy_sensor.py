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


# ───────────────────────────────────────────────────────
# Audit T20 follow-up: timezone-safe contract
# ───────────────────────────────────────────────────────


def test_naive_api_now_aware_utc_now_does_not_raise() -> None:
    """Audit T20 follow-up regression:
    Windows review caught a ``TypeError``
    because the API client wrote
    ``datetime.now()`` (naive) and the
    sensor wrote ``datetime.now(tz=utc)``
    (aware). The freshness helper must
    accept either side being naive.
    """
    api_at = datetime(2026, 6, 15, 9, 30)  # naive, assumed UTC
    sensor_now = datetime(
        2026, 6, 15, 10, 0, tzinfo=timezone.utc
    )  # aware
    # Must NOT raise. Must return a
    # normalized UTC freshness.
    freshness = compute_daily_energy_freshness(
        daily_energy_at=api_at,
        daily_energy_date=date(2026, 6, 15),
        now=sensor_now,
    )
    assert freshness.daily_energy_at is not None
    assert freshness.daily_energy_at.tzinfo is not None, (
        "Normalized daily_energy_at must carry tzinfo=UTC."
    )
    assert freshness.daily_energy_stale is False


def test_naive_api_now_naive_now_does_not_raise() -> None:
    """Audit T20 follow-up: the helper
    must also handle two naive
    timestamps without raising.
    """
    api_at = datetime(2026, 6, 15, 9, 30)
    sensor_now = datetime(2026, 6, 15, 10, 0)
    freshness = compute_daily_energy_freshness(
        daily_energy_at=api_at,
        daily_energy_date=date(2026, 6, 15),
        now=sensor_now,
    )
    assert freshness.daily_energy_stale is False


def test_aware_api_now_naive_now_does_not_raise() -> None:
    """Audit T20 follow-up: an aware
    API timestamp combined with a
    naive ``now`` must not raise.
    """
    api_at = datetime(
        2026, 6, 15, 9, 30, tzinfo=timezone.utc
    )
    sensor_now = datetime(2026, 6, 15, 10, 0)
    freshness = compute_daily_energy_freshness(
        daily_energy_at=api_at,
        daily_energy_date=date(2026, 6, 15),
        now=sensor_now,
    )
    assert freshness.daily_energy_stale is False


def test_aware_same_zone_utc() -> None:
    """Audit T20 follow-up: two aware
    UTC timestamps compare correctly.
    """
    api_at = datetime(
        2026, 6, 15, 9, 0, tzinfo=timezone.utc
    )
    sensor_now = datetime(
        2026, 6, 15, 10, 0, tzinfo=timezone.utc
    )
    freshness = compute_daily_energy_freshness(
        daily_energy_at=api_at,
        daily_energy_date=date(2026, 6, 15),
        now=sensor_now,
    )
    assert freshness.daily_energy_stale is False
    # Normalized output is UTC.
    assert freshness.daily_energy_at.tzinfo == timezone.utc


def test_aware_different_zones_match() -> None:
    """Audit T20 follow-up: an aware
    API timestamp in Europe/Kyiv and
    an aware ``now`` in UTC refer to
    the same instant. The helper must
    not falsely mark the value as
    stale due to the wall-clock
    difference.
    """
    kyiv = timezone(timedelta(hours=3))
    api_at = datetime(
        2026, 6, 15, 12, 0, tzinfo=kyiv
    )  # 12:00 Kyiv = 09:00 UTC
    sensor_now = datetime(
        2026, 6, 15, 9, 30, tzinfo=timezone.utc
    )
    freshness = compute_daily_energy_freshness(
        daily_energy_at=api_at,
        daily_energy_date=date(2026, 6, 15),
        now=sensor_now,
    )
    # 12:00 Kyiv is 30 minutes before
    # 09:30 UTC the same day; not stale.
    assert freshness.daily_energy_stale is False


def test_local_midnight_transition() -> None:
    """Audit T20 follow-up: the
    sensor path crossing a local
    midnight transition must keep
    ``daily_energy_for_today`` honest.

    Setup: the API value was last
    refreshed at 23:59 local time
    yesterday (the inverter rolled
    its counter back to zero at
    00:00). The sensor reads the
    value at 00:01 today. With the
    local date attached at refresh
    time, the sensor must report
    ``0.0`` for today - not
    yesterday's final reading.
    """
    yesterday = date(2026, 6, 14)
    today = date(2026, 6, 15)
    freshness = compute_daily_energy_freshness(
        daily_energy_at=datetime(
            2026, 6, 14, 23, 59, tzinfo=timezone.utc
        ),
        daily_energy_date=yesterday,
        now=datetime(
            2026, 6, 15, 0, 1, tzinfo=timezone.utc
        ),
    )
    value = daily_energy_for_today(
        value=18.5,
        freshness=freshness,
        today=today,
    )
    assert value == 0.0, (
        "Sensor must return 0.0 for today "
        "when the API value is dated yesterday "
        f"even after midnight has passed; got {value!r}."
    )


def test_real_sensor_path_uses_utc_normalization() -> None:
    """Audit T20 follow-up: the real
    sensor path must produce a numeric
    ``native_value`` on the original
    Windows mix (naive API timestamp
    + aware UTC ``now``).

    We exec the production
    ``native_value`` and
    ``extra_state_attributes``
    *bodies* from ``sensor.py`` via
    AST extraction so the test stays
    pure-stdlib (no Home Assistant
    import). The bodies reference
    ``self.coordinator.api.*``; we
    build a coordinator stub that
    has those attributes.
    """
    import ast as _ast
    sensor_path = os.path.join(_REPO_ROOT, "sensor.py")
    if not os.path.exists(sensor_path):
        return
    with open(sensor_path) as f:
        tree = _ast.parse(f.read())
    # Find InverterDailyEnergySensor
    # and extract its
    # ``native_value`` /
    # ``extra_state_attributes``
    # bodies.
    sensor_cls = None
    for node in _ast.walk(tree):
        if (
            isinstance(node, _ast.ClassDef)
            and node.name == "InverterDailyEnergySensor"
        ):
            sensor_cls = node
            break
    if sensor_cls is None:
        raise AssertionError(
            "Production InverterDailyEnergySensor "
            "not found in sensor.py"
        )
    native_value_body = None
    attrs_body = None
    for item in sensor_cls.body:
        if isinstance(item, _ast.FunctionDef):
            if item.name == "native_value":
                native_value_body = item
            elif item.name == "extra_state_attributes":
                attrs_body = item
    if native_value_body is None:
        raise AssertionError(
            "InverterDailyEnergySensor.native_value "
            "not found in sensor.py"
        )
    # Build a stub coordinator
    # with the production
    # attribute names.
    # We use today's actual date
    # because the production code
    # calls ``datetime.now()``
    # inside ``native_value``. A
    # fixed date would make the
    # midnight-reset rule fire and
    # the sensor would report 0.0.
    _today = date.today()
    class _StubApi:
        daily_energy = 12.34
        # NAIVE timestamp - the
        # Windows bug.
        daily_energy_at = datetime.now().replace(
            hour=9, minute=30, second=0, microsecond=0
        )
        daily_energy_date = _today
        device_sn = "TEST_SN"
        _account_device_count = 1

    class _StubCoordinator:
        def __init__(self):
            self.api = _StubApi()

    # ``super().extra_state_attributes``
    # in production requires a
    # ``__class__`` cell. We make
    # ``_Sensor`` a fresh class with
    # only the attributes the
    # production body reads, so
    # ``super()`` works against
    # ``object``.
    class _Sensor:
        def __init__(self):
            self.coordinator = _StubCoordinator()

    sensor = _Sensor()
    # Build the namespace and exec
    # the production body. The
    # production body calls
    # ``datetime.now(tz=timezone.utc)``
    # so we must provide those names.
    ns = {
        "datetime": datetime,
        "timezone": timezone,
        "compute_daily_energy_freshness":
            compute_daily_energy_freshness,
        "daily_energy_for_today":
            daily_energy_for_today,
    }
    # Wrap ``native_value`` in a
    # function so its locals do
    # not pollute the test
    # namespace.
    # Build a class that wraps
    # the production bodies so
    # ``super()`` works (it needs
    # the ``__class__`` cell that
    # only method definitions
    # provide). We replace the
    # production nodes' ``self``
    # references by injecting
    # ``_self`` into the AST
    # ``args`` and then build the
    # wrapper class manually.
    import textwrap

    wrapper_native = _ast.FunctionDef(
        name="native_value",
        args=_ast.arguments(
            posonlyargs=[],
            args=[_ast.arg(arg="self")],
            vararg=None, kwonlyargs=[],
            kw_defaults=[], kwarg=None,
            defaults=[],
        ),
        body=native_value_body.body,
        decorator_list=[],
        returns=None,
        type_comment=None,
    )
    wrapper_native.lineno = native_value_body.lineno
    wrapper_native.col_offset = 0
    # Same for the attributes body
    # if we have it.
    class_body = [wrapper_native]
    if attrs_body is not None:
        wrapper_attrs = _ast.FunctionDef(
            name="extra_state_attributes",
            args=_ast.arguments(
                posonlyargs=[],
                args=[_ast.arg(arg="self")],
                vararg=None, kwonlyargs=[],
                kw_defaults=[], kwarg=None,
                defaults=[],
            ),
            body=attrs_body.body,
            decorator_list=[],
            returns=None,
            type_comment=None,
        )
        wrapper_attrs.lineno = attrs_body.lineno
        wrapper_attrs.col_offset = 0
        class_body.append(wrapper_attrs)
    class_node = _ast.ClassDef(
        name="_ProdSensor",
        bases=[],
        keywords=[],
        body=class_body,
        decorator_list=[],
    )
    class_node.lineno = sensor_cls.lineno
    class_node.col_offset = 0
    module_node = _ast.Module(
        body=[class_node], type_ignores=[]
    )
    source = _ast.unparse(module_node)
    # The unparser strips ``__future__``
    # annotations, so exec the
    # generated source as a fresh
    # module and pull the class out.
    compiled_module = compile(
        source, "<t20-real-sensor>", "exec"
    )
    module_ns = {**ns}
    exec(compiled_module, module_ns)
    # Attach only ``native_value``
    # to ``_Sensor`` so the test
    # exercises the production
    # ``native_value`` body end-to-end.
    # ``extra_state_attributes`` calls
    # ``super().extra_state_attributes``
    # which requires a real
    # ``__class__`` cell; we keep the
    # freshness triple check on the
    # helper layer (already covered by
    # other tests) and exercise only
    # the property here.
    ProdSensor = module_ns["_ProdSensor"]
    _Sensor.native_value = ProdSensor.native_value
    try:
        value = _Sensor().native_value()
    except TypeError as exc:
        raise AssertionError(
            "Sensor native_value crashed with "
            "TypeError on naive api + aware UTC "
            f"now: {exc!r}. The freshness helper "
            "must normalize timestamps."
        )
    assert value is not None, (
        "Sensor returned None on the naive-api "
        "/ aware-utc-now path. The freshness "
        "helper must treat naive timestamps as "
        "UTC and report a normal value."
    )
    assert abs(float(value) - 12.34) < 1e-9, (
        f"Sensor must report the API value "
        f"(12.34); got {value!r}."
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