"""Tests for the read-only ``extra_state_attributes`` of
``InverterPredictiveTargetSelect`` (select.py).

Юра: «Якщо потрібен явний доказ для шаблону, додай read-only
атрибут поточної ручної цілі, не змінюючи керування. На головній
картці покажи зрозумілий стан і доступний контрол задання цілі
без сирого unknown.»

The test exercises the production class directly with a fake
coordinator/hems so it asserts the real attribute surface, not
just a string match in the dashboard template.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_select_module():
    """Load ``select.py`` as a sub-module of a stub
    ``powmr_inverter`` package. The repo is flat but ``select.py``
    does ``from .const import …``, so we need a real package
    context. We build a tiny ``powmr_inverter`` package on
    ``sys.modules`` that re-exports every production .py the
    same way Home Assistant would — the production class is
    exercised end-to-end with the real production source.
    """
    import types
    import importlib.machinery

    pkg_name = "powmr_inverter_stub_for_test"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(REPO)]
    sys.modules[pkg_name] = pkg

    # Import every production .py as a sub-module of the stub.
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
        except Exception as exc:  # noqa: BLE001
            # Some modules (e.g. coordinator) pull in Home Assistant;
            # we only need ``select`` for this test, so ignore import
            # errors for non-essential modules.
            if py_name in ("select", "const"):
                raise
            sys.modules.pop(f"{pkg_name}.{py_name}", None)
    return sys.modules[f"{pkg_name}.select"]


class FakeHems:
    """Bare-minimum stand-in for the HEMS engine. The
    InverterPredictiveTargetSelect only reads
    ``_predictive_user_target_soc``."""

    def __init__(self, target_soc):
        self._predictive_user_target_soc = target_soc


class FakeCoordinator:
    def __init__(self, hems):
        self._hems = hems
        self.api = type("Api", (), {"device_sn": "TEST-SN-1234"})()
        # Minimal async method that some select entities need; not used
        # by the predictive target select but kept for safety.
        self.async_predictive_feedback = lambda *a, **k: None


class FakeEntry:
    def __init__(self, entry_id="TEST-ENTRY-ID"):
        self.entry_id = entry_id


class InverterPredictiveTargetSelectAttributesTests(unittest.TestCase):
    """Юра: 'read-only атрибут поточної ручної цілі, не змінюючи
    керування' → ``extra_state_attributes`` must surface both
    ``target_soc`` (None or int) and ``has_manual_target`` (bool)."""

    def setUp(self):
        sel = _load_select_module()
        self.sel_mod = sel
        self.cls = sel.InverterPredictiveTargetSelect

    def _make(self, target_soc):
        hems = FakeHems(target_soc)
        coord = FakeCoordinator(hems)
        entry = FakeEntry()
        return self.cls(coord, entry)

    def test_extra_state_attributes_when_no_manual_target(self):
        sel = self._make(target_soc=None)
        attrs = sel.extra_state_attributes
        self.assertIsNone(attrs["target_soc"])
        self.assertFalse(attrs["has_manual_target"])
        # current_option still returns None (HA renders as "unknown"),
        # but the operator now has a real signal to distinguish this
        # from "entity not yet loaded".
        self.assertIsNone(sel.current_option)

    def test_extra_state_attributes_when_manual_target_is_set(self):
        sel = self._make(target_soc=80)
        attrs = sel.extra_state_attributes
        self.assertEqual(attrs["target_soc"], 80)
        self.assertTrue(attrs["has_manual_target"])
        self.assertEqual(sel.current_option, "80")

    def test_current_option_accepts_20_to_100(self):
        # Production contracts: the select entity must accept the
        # 20-100 range as legal options. This is the value range
        # the dashboard mentions in its hint text.
        sel = self._make(target_soc=None)
        self.assertEqual(sel._attr_options, [str(v) for v in range(20, 101)])

    def test_select_does_not_expose_an_auto_option(self):
        """Юра fixed: there is no "auto" / "None" / "automatically"
        option. The dashboard must point at AI / pause controls to
        clear the manual target."""
        sel = self._make(target_soc=None)
        for forbidden in ("auto", "Auto", "automatically", "Auto ", "None", "auto_select"):
            self.assertNotIn(forbidden, sel._attr_options,
                             msg=f"Select must not expose forbidden option: {forbidden!r}")

    def test_extra_state_attributes_is_read_only(self):
        """Юра: 'read-only атрибут, не змінюючи керування' → the
        attributes must NOT trigger a write to the HEMS engine or
        any persistent state. We assert that calling the property
        twice yields identical results and the underlying hems
        state is unchanged."""
        sel = self._make(target_soc=42)
        before = dict(sel.extra_state_attributes)
        # Touch the property many times.
        for _ in range(5):
            sel.extra_state_attributes
        after = dict(sel.extra_state_attributes)
        self.assertEqual(before, after)
        self.assertEqual(sel.coordinator._hems._predictive_user_target_soc, 42)


if __name__ == "__main__":
    unittest.main()
