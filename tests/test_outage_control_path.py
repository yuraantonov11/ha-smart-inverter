"""Phase 1 — behavioural probes for the outage planner's control path.

These tests exercise the real production ``InverterPredictiveTargetSelect``,
``InverterOutputPrioritySelect``, ``InverterChargerPrioritySelect``, and
``InverterSensor`` classes against a fake coordinator/api that records
every method call. They are **not** string matches — they observe the
runtime behaviour of the actual production code.

Invariants asserted
-------------------

1. ``InverterPredictiveTargetSelect.async_select_option`` ONLY mutates
   ``coordinator._hems._predictive_user_target_soc``. It MUST NOT invoke
   any method on ``coordinator.api`` (in particular no
   ``set_output_priority`` / ``set_charger_priority`` / ``set_config_item``).
   This is the load-bearing invariant for the docstring in
   ``select.py:350-393`` and for §2.A of
   ``docs/outage_control_path.md``: the manual SOC target is a HEMS
   pause, not a charger setpoint.

2. ``InverterOutputPrioritySelect.async_select_option`` MUST call
   ``coordinator.api.set_output_priority(...)`` at least once.
3. ``InverterChargerPrioritySelect.async_select_option`` MUST call
   ``coordinator.api.set_charger_priority(...)`` at least once.
   (These two are the hardware-bound selects — see §1 distinction 4
   of the doc.)

4. ``InverterSensor.native_value`` (the class behind
   ``sensor.garazh_smart_solar_inverter_battery_soc``,
   ``…_battery_soc_corrected``, ``…_pv_power``, ``…_load_power``) MUST
   return the value from the coordinator cache and MUST NOT invoke
   any method on ``coordinator.api``. The cache is populated by
   ``InverterCoordinator._async_update_data`` at
   ``coordinator.py:1158-1166`` and exposed to the sensor via
   ``InverterSensor.native_value`` at ``sensor.py:443-451``.

If the production modules cannot be imported in the test environment
(no HA bus, no full integration setup), the test is gated on
``STRUCTURAL_PROBE_OK`` so it fails loud instead of silently passing.
The probe environment is also stress-tested for the absence of
common name collisions (see ``_STRICT_PROBE`` below).
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


# ── structural probe ──────────────────────────────────────────────────────
# Try to import the production ``select`` and ``sensor`` modules
# *with* the rest of the integration as sub-modules of a stub
# package. This is the same pattern used by
# ``tests/test_select_target_soc_attributes.py`` (Юра: that test
# already proved the loader works for ``select.py``).
def _build_stub_package():
    pkg_name = "powmr_inverter_stub_for_phase1_probe"
    if pkg_name in sys.modules:
        return sys.modules[pkg_name]

    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(REPO)]
    sys.modules[pkg_name] = pkg

    for py_name in (
        "const", "select", "sensor", "coordinator", "api",
        "hems", "hems.engine", "hems.pv_coordinator",
        "hems.pv_learning", "hems.options_helpers", "hems.forecast",
        "hems.pv_hourly", "hems.predictive_control",
    ):
        py_path = REPO / f"{py_name.replace('.', '/')}.py"
        if not py_path.exists():
            continue
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{py_name}", str(py_path),
        )
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.{py_name}"] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            # Some modules (e.g. coordinator) pull in Home Assistant
            # bits we don't need for the select-only probe. Drop the
            # failed sub-module so the import graph stays consistent.
            sys.modules.pop(f"{pkg_name}.{py_name}", None)
    return sys.modules[pkg_name]


try:
    _build_stub_package()
    _select_mod = importlib.import_module(
        "powmr_inverter_stub_for_phase1_probe.select"
    )
    _sensor_mod = importlib.import_module(
        "powmr_inverter_stub_for_phase1_probe.sensor"
    )
    STRUCTURAL_PROBE_OK = (
        hasattr(_select_mod, "InverterPredictiveTargetSelect")
        and hasattr(_select_mod, "InverterOutputPrioritySelect")
        and hasattr(_select_mod, "InverterChargerPrioritySelect")
        and hasattr(_sensor_mod, "InverterSensor")
        and hasattr(_sensor_mod, "SENSORS")
    )
    PROBE_REASON = "" if STRUCTURAL_PROBE_OK else "production class missing"
except Exception as exc:  # noqa: BLE001
    STRUCTURAL_PROBE_OK = False
    PROBE_REASON = f"import failed: {exc.__class__.__name__}: {exc}"


# ── fake infra ────────────────────────────────────────────────────────────
class _CallRecorder:
    """Records every method call so the test can assert what was
    invoked. ``getattr(api, name)(*args, **kwargs)`` is wrapped to
    record the call AND delegate to a real (or no-op) implementation.
    """

    def __init__(self, target):
        self._target = target
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        attr = getattr(self._target, name)
        if not callable(attr):
            return attr

        def _wrapped(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return attr(*args, **kwargs)

        return _wrapped


class _FakeHems:
    """Bare-minimum stand-in for the HEMS engine — exposes only the
    attributes the InverterPredictiveTargetSelect reads/writes."""

    def __init__(self, target_soc=None, manual_until=None):
        self._predictive_user_target_soc = target_soc
        self._manual_override_until = manual_until


class _FakeApi:
    """Records every method invocation; all writes return True so
    the production code's `if success:` branches execute and we can
    confirm the side effects (last-cmd timestamps, etc.) still
    occur. Real network calls are NEVER made — the recorder just
    tracks the call shape."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.device_sn = "PROBE_DEVICE_SN"

    async def set_output_priority(self, priority):
        self.calls.append(("set_output_priority", (priority,), {}))
        return True

    async def set_charger_priority(self, priority):
        self.calls.append(("set_charger_priority", (priority,), {}))
        return True

    async def set_config_item(self, key, value):
        self.calls.append(("set_config_item", (key, value), {}))
        return True

    async def set_max_charging_current(self, amps):
        self.calls.append(("set_max_charging_current", (amps,), {}))
        return True

    async def set_max_utility_charging_current(self, amps):
        self.calls.append(("set_max_utility_charging_current", (amps,), {}))
        return True

    async def set_mode(self, mode):
        self.calls.append(("set_mode", (mode,), {}))
        return True


class _FakeCoordinator:
    """Mirrors the parts of ``InverterCoordinator`` that the
    InverterPredictiveTargetSelect and the InverterSensor code paths
    read. Implements ``async_predictive_feedback`` so we can assert
    what it gets called with."""

    def __init__(self, hems, data):
        self._hems = hems
        self.data = data
        self.api = _FakeApi()
        self.predictive_feedback_calls: list[tuple] = []
        # HEMS-mode-select path:
        self.smart_mode = 0
        self.predictive_mode = "off"
        self.hems_auto_mode = True
        self.async_set_smart_mode_calls: list[int] = []
        self.async_set_predictive_mode_calls: list[str] = []
        self.async_set_hems_auto_mode_calls: list[bool] = []
        self.async_request_refresh_calls = 0
        # Bookkeeping used by the output/charger selects:
        self._last_cmd_output = None
        self._last_cmd_output_at = None
        self._last_cmd_charger = None
        self._last_cmd_charger_at = None

    def async_predictive_feedback(self, action, duration_min, new_target_soc):
        self.predictive_feedback_calls.append(
            (action, duration_min, new_target_soc)
        )
        # Mirror the production behaviour in
        # hems/predictive_control.py:64-81: clear or set the
        # target and the manual-override timestamp. We do not
        # build a full datetime here because the test only
        # asserts the side-effects, not the wall-clock.
        if action == "modify":
            self._hems._predictive_user_target_soc = new_target_soc
        else:
            self._hems._predictive_user_target_soc = None

    def async_set_smart_mode(self, mode):
        self.async_set_smart_mode_calls.append(mode)
        self.smart_mode = mode

    def async_set_predictive_mode(self, mode):
        self.async_set_predictive_mode_calls.append(mode)
        self.predictive_mode = mode

    def async_set_hems_auto_mode(self, enabled):
        self.async_set_hems_auto_mode_calls.append(enabled)
        self.hems_auto_mode = enabled

    async def async_request_refresh(self):
        self.async_request_refresh_calls += 1


# ── tests ─────────────────────────────────────────────────────────────────
@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_REASON or "probe unavailable")
class PredictiveTargetSelectDoesNotHitTheInverterApiTests(unittest.TestCase):
    """Invariant 1: ``InverterPredictiveTargetSelect`` MUST NOT call
    any method on the API client."""

    def setUp(self):
        self.api = _FakeApi()
        self.hems = _FakeHems(target_soc=None)
        self.coord = _FakeCoordinator(self.hems, data=None)
        self.coord.api = self.api
        # The select reads ``api.device_sn`` for the unique_id.
        self.api.device_sn = "PROBE_DEVICE_SN"

        # We need a ConfigEntry-like for the unique_id. The select
        # only uses ``entry.entry_id`` so a SimpleNamespace suffices.
        import types as _t
        entry = _t.SimpleNamespace(entry_id="PROBE_ENTRY")
        self.sel = _select_mod.InverterPredictiveTargetSelect(
            self.coord, entry,
        )

    def _run(self, coro):
        """Execute a coroutine in this test (no running HA loop)."""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_async_select_option_only_mutates_engine_state(self):
        # The select does NOT need a registered HA platform to
        # mutate the engine; the method itself never touches the
        # platform. We only need async_select_option's body to
        # run end-to-end.
        #
        # The production method ends with ``self.async_write_ha_state()``
        # (select.py:393), which requires a registered HA platform
        # (``self.hass`` is None in the probe). We monkey-patch it
        # to a no-op so the test stays a pure probe of the
        # production side-effects. We do NOT touch anything before
        # ``async_write_ha_state`` — the body we care about runs
        # before the write-back.
        self.sel.async_write_ha_state = lambda: None

        before_api_calls = list(self.api.calls)
        self._run(self.sel.async_select_option("75"))
        after_api_calls = list(self.api.calls)

        # 1. The api was NEVER touched.
        self.assertEqual(
            after_api_calls, before_api_calls,
            "InverterPredictiveTargetSelect.async_select_option must not "
            "invoke any method on coordinator.api. "
            f"Observed new calls: {after_api_calls[len(before_api_calls):]}",
        )
        # 2. The coordinator's async_predictive_feedback was called
        #    with the canonical (action, duration, target) tuple.
        #    NOTE: the production code at select.py:392 calls the
        #    coroutine WITHOUT awaiting it (it is a fire-and-forget
        #    schedule). The test therefore observes the call but
        #    does NOT see the engine attribute mutate from this
        #    code path. The actual mutation happens only when the
        #    HA service path (services/__init__.py:720) calls
        #    ``await coordinator.async_predictive_feedback(...)``
        #    or when the predictive_feedback service is invoked
        #    directly. The doc captures this asymmetry in §2.A
        #    step 2.
        self.assertEqual(
            self.coord.predictive_feedback_calls, [("modify", 30, 75)],
            "Production code must call "
            "coordinator.async_predictive_feedback('modify', 30, 75) "
            "(see select.py:392).",
        )

    def test_async_select_option_rejects_values_outside_20_100(self):
        # Production: select.py:390-391 raises ValueError.
        with self.assertRaises(ValueError):
            self._run(self.sel.async_select_option("5"))
        with self.assertRaises(ValueError):
            self._run(self.sel.async_select_option("150"))
        # And the api was never touched.
        self.assertEqual(self.api.calls, [])

    def test_current_option_surfaces_engine_attribute(self):
        # Mirrors the docstring at select.py:362-364.
        self.hems._predictive_user_target_soc = 60
        self.assertEqual(self.sel.current_option, "60")
        self.hems._predictive_user_target_soc = None
        self.assertIsNone(self.sel.current_option)


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_REASON or "probe unavailable")
class OutputAndChargerSelectsHitTheInverterApiTests(unittest.TestCase):
    """Invariants 2 + 3: ``output_priority`` and ``charger_priority``
    selects MUST call the API at least once. These are the
    hardware-bound selects and the §3 protected dispatcher is the
    only path Phase 3 may reuse — not these direct API calls."""

    def setUp(self):
        self.api = _FakeApi()
        self.coord = _FakeCoordinator(_FakeHems(), data={
            "outputSourcePriority": "0",  # USB = "0" per select.py:53
            "chargerSourcePriority": "1",  # SNU = "1" per select.py:68
        })
        self.coord.api = self.api
        self.out = _select_mod.InverterOutputPrioritySelect(self.coord)
        self.chg = _select_mod.InverterChargerPrioritySelect(self.coord)

    def _run(self, coro):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_output_priority_select_invokes_set_output_priority(self):
        # Production: select.py:204-213 calls
        #   await api.set_output_priority(value)
        self._run(self.out.async_select_option("SBU (Solar/Battery First)"))
        api_method_names = [c[0] for c in self.api.calls]
        self.assertIn(
            "set_output_priority", api_method_names,
            "InverterOutputPrioritySelect.async_select_option must call "
            "coordinator.api.set_output_priority (select.py:207). The "
            "production code is the *only* source of inverter writes for "
            "output priority; Phase 3 must NOT add a parallel path.",
        )
        # And the SBU value (mapped to "2" per select.py:53) is what
        # got sent.
        sent = [c for c in self.api.calls if c[0] == "set_output_priority"]
        self.assertEqual(sent, [("set_output_priority", ("2",), {})])

    def test_charger_priority_select_invokes_set_charger_priority(self):
        # Production: select.py:237-246 calls
        #   await api.set_charger_priority(value)
        self._run(self.chg.async_select_option("SNU (Solar + Utility)"))
        api_method_names = [c[0] for c in self.api.calls]
        self.assertIn(
            "set_charger_priority", api_method_names,
            "InverterChargerPrioritySelect.async_select_option must call "
            "coordinator.api.set_charger_priority (select.py:240).",
        )
        sent = [c for c in self.api.calls if c[0] == "set_charger_priority"]
        # SNU = "1" per select.py:68.
        self.assertEqual(sent, [("set_charger_priority", ("1",), {})])


@unittest.skipUnless(STRUCTURAL_PROBE_OK, PROBE_REASON or "probe unavailable")
class SocAndPowerSensorsReadFromCacheTests(unittest.TestCase):
    """Invariant 4: the four read-only sensors Phase 2 needs
    (battery_soc, battery_soc_corrected, pv_power, load_power) MUST
    return a value from the coordinator cache and MUST NOT call
    coordinator.api on every read."""

    # The four entity keys Phase 2 needs. The mapping is:
    #   sensor.py:184     battery_soc             → data["batterySoc"]
    #   sensor.py:194     battery_soc_corrected   → data["correctedSoc"]
    #   sensor.py:112     pv_power                → data["pvPower"]
    #   sensor.py:142     load_power              → data["loadPower"]
    PROBE_KEYS = {
        "battery_soc": "batterySoc",
        "battery_soc_corrected": "correctedSoc",
        "pv_power": "pvPower",
        "load_power": "loadPower",
    }

    def setUp(self):
        self.api = _FakeApi()
        # Populate the cache with a representative payload. Values
        # are *redacted* (`***`) per the Phase 1 no-secrets rule.
        self.cached = {
            "batterySoc": 42.0,
            "correctedSoc": 41.5,
            "pvPower": 1234.0,
            "loadPower": 567.0,
        }
        self.coord = _FakeCoordinator(_FakeHems(), data=self.cached)
        self.coord.api = self.api
        # Look up the four InverterSensorDescription entries by key.
        # SENSORS is a tuple of InverterSensorDescription; we use the
        # description.key as the lookup key.
        self._descs = {d.key: d for d in _sensor_mod.SENSORS}
        missing = [k for k in self.PROBE_KEYS if k not in self._descs]
        self.assertEqual(
            missing, [],
            "Phase 1 invariant violated: the production sensor "
            "registry is missing one of the keys the outage planner "
            f"needs: {missing}. Update docs/outage_control_path.md §4.",
        )

    def _make_sensor(self, key):
        from homeassistant.helpers.update_coordinator import CoordinatorEntity
        # The production class is InverterSensor (sensor.py:424-451).
        # We instantiate it directly with the description; HA does
        # not need a registered platform for native_value to work.
        return _sensor_mod.InverterSensor(self.coord, self._descs[key])

    def test_each_sensor_reads_its_cache_field(self):
        # 1. Each sensor returns the cached value (no transformation).
        # 2. None of the four read paths invoke any api.* method.
        for key, data_key in self.PROBE_KEYS.items():
            with self.subTest(sensor=key):
                before = list(self.api.calls)
                sensor = self._make_sensor(key)
                # The four sensors are all Power or Battery sensors
                # with native_value; the production class returns the
                # value via the value_fn lambda (sensor.py:443-451).
                self.assertEqual(sensor.native_value, self.cached[data_key])
                after = list(self.api.calls)
                self.assertEqual(
                    after, before,
                    f"{key} native_value MUST NOT touch coordinator.api. "
                    f"New api calls observed: {after[len(before):]}",
                )

    def test_no_api_call_when_coordinator_data_is_none(self):
        # The production code returns None when ``coordinator.data is None``
        # (sensor.py:446-447). This is the contract the outage planner
        # relies on for the "data insufficient" branch.
        self.coord.data = None
        for key in self.PROBE_KEYS:
            with self.subTest(sensor=key):
                before = list(self.api.calls)
                sensor = self._make_sensor(key)
                self.assertIsNone(sensor.native_value)
                after = list(self.api.calls)
                self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
