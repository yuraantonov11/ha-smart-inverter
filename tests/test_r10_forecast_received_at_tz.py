"""R10.7 — forecast_received_at time-zone contract.

Юра reproduced the production bug: live sensor
``forecast_received_at`` carried the local wall clock
labelled as UTC, off by the site's UTC offset
(``+10800`` seconds for Europe/Kyiv in summer).

Root cause:
  - ``hems/pv_coordinator.py::_maybe_refresh_forecast``
    stamped ``_forecast_last_received_at = now`` where
    ``now`` was a local-naive ``datetime``.
  - ``sensor.py::_received_at_iso`` then did
    ``last_at.replace(tzinfo=_tz.utc)`` — that does
    NOT convert; it just attaches a UTC label. The
    wall clock ``22:51:23`` plus UTC offset
    zero gives ``22:51:23+00:00`` — wrong by the
    site offset.

R10.7 fix (2 layers):
  - Layer A (production): the success branch of
    ``_maybe_refresh_forecast`` stamps the value
    as ``_pv_local_now().astimezone(timezone.utc)``
    — a UTC-aware ``datetime``. The ``now``
    parameter contract (local-naive) is preserved
    so other timers that depend on it are
    unaffected.
  - Layer B (sensor): ``_received_at_iso`` renders
    aware values via ``astimezone(_tz.utc)``;
    naive (legacy) values return ``None`` (audit
    probe NOT_YET_VERIFIED) rather than mis-label.

This suite exercises both layers against the
real production sources.

Layer A test: the production line of code in
``hems/pv_coordinator.py`` is exactly the
UTC-aware stamping contract (regex-anchored
pin). If a future refactor removes the
``astimezone(timezone.utc)`` call, this test
fails loudly.

Layer B test: the real ``_received_at_iso`` is
called with synthetic
``_forecast_last_received_at`` values:
  - aware Kyiv summer → 19:51:23+00:00 (off -3h)
  - aware Kyiv winter → 20:51:23+00:00 (off -2h)
  - naive legacy    → ``None`` (audit probe)
  - explicit UTC    → rendered unchanged
  - None            → ``None``
"""
from __future__ import annotations

import importlib.util
import re
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo


REPO = Path(__file__).resolve().parents[1]
KYIV = ZoneInfo("Europe/Kyiv")

# Package shim so the integration's relative
# imports resolve.
_PKG_NAME = "powmr_inverter_pkg_for_test_r10"
if _PKG_NAME not in sys.modules:
    _pkg = types.ModuleType(_PKG_NAME)
    _pkg.__path__ = [str(REPO)]
    sys.modules[_PKG_NAME] = _pkg


def _load(name):
    full = f"{_PKG_NAME}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    rel = name.replace(".", "/") + ".py"
    spec = importlib.util.spec_from_file_location(full, REPO / rel)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


class TestR10ProductionLineContract(unittest.TestCase):
    """Layer A: pin the production code line that
    stamps the receive time. Any future refactor
    that removes the ``astimezone(timezone.utc)``
    conversion fails this test."""

    def test_success_branch_stamps_utc_aware(self):
        text = (REPO / "hems/pv_coordinator.py").read_text()
        # The stamp is the
        # ``_forecast_last_received_at`` assignment
        # inside the success branch of
        # ``_maybe_refresh_forecast``. We require
        # the right-hand side to be a
        # ``.astimezone(timezone.utc)`` of a
        # ``_pv_local_now()`` value (possibly
        # bound to a local first).
        pattern = re.compile(
            r"_forecast_last_received_at\s*=\s*"
            r"(?:#[^\n]*\n\s*)*"
            r"(?:local_completion\s*=\s*self\._pv_local_now\([^)]*\)\s*\n\s*)?"
            r"(?:self\._pv_local_now\([^)]*\)|local_completion)"
            r"\s*\.astimezone\(\s*timezone\.utc\s*\)",
            re.MULTILINE,
        )
        self.assertRegex(
            text, pattern,
            "production must stamp "
            "_forecast_last_received_at as "
            "_pv_local_now().astimezone(timezone.utc) "
            "— a UTC-aware datetime, not the raw "
            "naive 'now' argument",
        )

    def test_now_argument_unchanged(self):
        # Юра's standing rule: don't change the
        # ``now`` parameter contract globally.
        # Other timers depend on it. We assert
        # the production code does NOT assign
        # the raw ``now`` argument to
        # ``_forecast_last_received_at``.
        text = (REPO / "hems/pv_coordinator.py").read_text()
        bad = re.compile(
            r"self\._forecast_last_received_at\s*=\s*now\b"
        )
        self.assertNotRegex(
            text, bad,
            "production must not assign raw `now` "
            "to _forecast_last_received_at — that "
            "is the original timezone bug",
        )


class TestR10SensorRendersCorrectUTC(unittest.TestCase):
    """Layer B: the real ``_received_at_iso`` is
    called with synthetic stamp values. The
    sensor is the production
    ``PredictiveDecisionStateSensor`` with
    ``__new__`` to bypass HA bootstrap."""

    def _build_sensor(self, last_at):
        mod = _load("sensor")
        sensor = mod.PredictiveDecisionStateSensor.__new__(
            mod.PredictiveDecisionStateSensor,
        )
        sensor.coordinator = MagicMock()
        sensor.coordinator._forecast_last_received_at = last_at
        return sensor

    def test_aware_kyiv_summer_renders_utc_minus_3h(self):
        # 22:51:23 Kyiv summer (UTC+3) → 19:51:23+00:00.
        aware = datetime(2026, 6, 15, 22, 51, 23, tzinfo=KYIV)
        sensor = self._build_sensor(aware)
        out = sensor._received_at_iso()
        self.assertEqual(out, "2026-06-15T19:51:23+00:00")

    def test_aware_kyiv_winter_renders_utc_minus_2h(self):
        # 22:51:23 Kyiv winter (UTC+2) → 20:51:23+00:00.
        aware = datetime(2026, 1, 15, 22, 51, 23, tzinfo=KYIV)
        sensor = self._build_sensor(aware)
        out = sensor._received_at_iso()
        self.assertEqual(out, "2026-01-15T20:51:23+00:00")

    def test_naive_legacy_returns_none(self):
        # Naive (legacy) value: the sensor MUST
        # NOT pretend it is UTC. It returns
        # ``None`` so the audit probe reports
        # NOT_YET_VERIFIED rather than a
        # mis-labelled time.
        naive = datetime(2026, 6, 15, 22, 51, 23)
        sensor = self._build_sensor(naive)
        self.assertIsNone(sensor._received_at_iso())

    def test_explicit_utc_renders_unchanged(self):
        aware_utc = datetime(
            2026, 6, 15, 19, 51, 23, tzinfo=timezone.utc
        )
        sensor = self._build_sensor(aware_utc)
        self.assertEqual(
            sensor._received_at_iso(),
            "2026-06-15T19:51:23+00:00",
        )

    def test_none_returns_none(self):
        sensor = self._build_sensor(None)
        self.assertIsNone(sensor._received_at_iso())


if __name__ == "__main__":
    unittest.main()
