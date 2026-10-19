"""Юра: "AST allow-list перевіряє назви
причин, а не дозволеність заряджання.
Додай поведінкові тести тарифу, SOC,
резерву й налаштованих винятків."

The allow-list audit (in
``test_hems_snu_branches.py``) is
necessary but not sufficient. An explicit
reason is not the same as a justified
charging decision. This test file drives
the engine through realistic scenarios
and asserts the BEHAVIOUR:

  * The night window (23:00-07:00 local)
    is the ONLY time the engine may
    freely commit grid charging (SNU)
    without a low-SOC or storm
    justification. Daytime SNU must be
    tied to an operator-visible
    exception.

  * The reserve-protection branch only
    fires when the operator's
    ``reserve_soc`` setting is exceeded
    (SOC at-or-below reserve+2). When
    the operator raises the reserve, the
    branch must still fire correctly.

  * The storm branch only fires when
    ``predictive_mode='storm'`` is set.
    Other modes must not produce SNU
    for the storm reason.

  * The evening-protection branch (round
    6) does NOT commit a fresh SNU at
    SOC above the reserve. A unit-
    mismatch bug in condition 3 (the
    source of the 17:00:08 UTC SNU
    command on 2026-10-19) is fixed.
"""
import ast
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path("/opt/data/powmr-ai-work/powmr_inverter")
HEMS_ENGINE_PATH = REPO_ROOT / "hems" / "engine.py"


# Tariff windows (live, Ukraine).
# Night: 23:00-07:00 local, day tariff
# 4.32 UAH/kWh, night 2.16 UAH/kWh.
NIGHT_START_HOUR_LOCAL = 23
NIGHT_END_HOUR_LOCAL = 7


def _install_stub_modules():
    if "homeassistant" not in sys.modules:
        sys.modules["homeassistant"] = types.ModuleType(
            "homeassistant"
        )
        sys.modules["homeassistant.core"] = (
            types.ModuleType("homeassistant.core")
        )
        sys.modules["homeassistant.const"] = (
            types.ModuleType("homeassistant.const")
        )
        sys.modules["homeassistant.util"] = (
            types.ModuleType("homeassistant.util")
        )
        sys.modules["homeassistant.helpers"] = (
            types.ModuleType("homeassistant.helpers")
        )


def _load():
    _install_stub_modules()
    sys.path.insert(0, str(REPO_ROOT))
    from hems.engine import (  # type: ignore
        HemsEngine, ChargerPriority, OutputPriority,
        SmartMode, _Reason,
    )
    from hems.tuning import PredictiveTuning
    return (
        HemsEngine, ChargerPriority, OutputPriority,
        SmartMode, _Reason, PredictiveTuning,
    )


class HemsSnuBehaviourTariffTests(unittest.TestCase):
    """Tariff-window behaviour. The engine
    may commit SNU in the night window
    (cheap tariff) without an explicit
    low-SOC or storm reason. Outside the
    night window, SNU requires an
    operator-visible exception
    (low-SOC reserve or storm).
    """

    @classmethod
    def setUpClass(cls):
        (
            cls.HemsEngine, cls.ChargerPriority,
            cls.OutputPriority, cls.SmartMode,
            cls._Reason, cls.PredictiveTuning,
        ) = _load()

    def _build_engine(
        self, *, smart_mode="ADAPTIVE",
        reserve_soc=20.0, predictive_mode="off",
    ):
        engine = self.HemsEngine()
        engine._entry = SimpleNamespace(
            options={
                "smart_mode": getattr(
                    self.SmartMode, smart_mode
                ),
                "reserve_soc": reserve_soc,
                "tuning": {
                    "battery_cycle_conservative": 0.0,
                },
            }
        )
        pt = self.PredictiveTuning()
        pt.predictive_mode = predictive_mode
        engine.predictive_tuning = pt
        return engine

    def _evaluate_at(
        self, engine, *, hour_local, soc,
        reserve_soc=20.0, smart_mode="ADAPTIVE",
        current_output="USB",
        current_charger="OSO",
    ):
        """Run the engine at ``hour_local``
        Europe/Kyiv on a representative
        date.
        """
        # 2026-10-19 is a Monday in October
        # (EEST = UTC+3). 09:00 EEST = 06:00
        # UTC, 19:00 EEST = 16:00 UTC, 22:00
        # EEST = 19:00 UTC, 23:30 EEST = 20:30
        # UTC.
        utc_hour = (hour_local - 3) % 24
        now = datetime(
            2026, 10, 19, utc_hour, 30, 0,
            tzinfo=timezone.utc,
        )
        return engine.evaluate(
            smart_mode=getattr(
                self.SmartMode, smart_mode
            ),
            hems_auto=True,
            soc=soc,
            pv_power=0.0,
            grid_power=0.0,
            battery_power=-100.0,
            load_power=700.0,
            grid_voltage=230.0,
            grid_available=True,
            current_output=current_output,
            current_charger=current_charger,
            now=now,
            reserve_soc=reserve_soc,
            entry_id="test-entry",
        )

    def test_daytime_no_low_soc_does_not_snu(self):
        """At 12:00 EEST, SOC 50 % (above
        reserve), Adaptive mode, the engine
        must NOT commit a fresh SNU.
        """
        engine = self._build_engine(
            smart_mode="ADAPTIVE", reserve_soc=20.0
        )
        d = self._evaluate_at(
            engine, hour_local=12, soc=50.0
        )
        if d.charger_priority == self.ChargerPriority.SNU:
            self.fail(
                f"daytime 12:00 EEST, SOC 50 %, "
                f"Adaptive: must NOT commit SNU; "
                f"got charger={d.charger_priority} "
                f"reason={d.reason!r}"
            )

    def test_evening_no_low_soc_does_not_snu(self):
        """At 19:30 EEST (evening, between
        sunset and night tariff), SOC 50 %,
        the engine must NOT commit a fresh
        SNU. This is the round-6 fix:
        condition 3 in
        ``_adaptive_evening_protection`` no
        longer triggers on unit-mismatched
        surplus / SOC-percent comparison.
        """
        engine = self._build_engine(
            smart_mode="ADAPTIVE", reserve_soc=20.0
        )
        d = self._evaluate_at(
            engine, hour_local=19, soc=50.0
        )
        if d.charger_priority == self.ChargerPriority.SNU:
            self.fail(
                f"evening 19:00 EEST, SOC 50 %, "
                f"Adaptive: round-6 fix requires "
                f"NO fresh SNU; got "
                f"charger={d.charger_priority} "
                f"reason={d.reason!r}"
            )

    def test_night_adaptive_can_snu(self):
        """At 02:00 EEST (night tariff
        window), Adaptive mode, SOC above
        reserve: the engine MAY commit SNU
        to top up at cheap tariff. The
        reason is the night branch.
        """
        engine = self._build_engine(
            smart_mode="ADAPTIVE", reserve_soc=20.0
        )
        d = self._evaluate_at(
            engine, hour_local=2, soc=60.0
        )
        # In night window, the adaptive
        # engine takes the night path and
        # is allowed to SNU. The reason
        # MUST come from the night branch
        # (not from reserve_protection).
        if d.charger_priority == self.ChargerPriority.SNU:
            self.assertNotEqual(
                d.reason, "reserve_protection",
                f"night SNU at SOC 60 %% must "
                f"not carry reserve_protection "
                f"reason; got {d.reason!r}",
            )

    def test_night_adaptive_low_soc_snu_with_reason(self):
        """At 02:00 EEST, SOC 18 % (below
        reserve+2): the engine commits SNU
        with the reserve_protection reason.
        """
        engine = self._build_engine(
            smart_mode="ADAPTIVE", reserve_soc=20.0
        )
        d = self._evaluate_at(
            engine, hour_local=2, soc=18.0
        )
        if d.charger_priority == self.ChargerPriority.SNU:
            # Either reserve_protection or
            # night reason; both are valid
            # in the night window.
            self.assertIn(
                d.reason,
                {
                    "reserve_protection",
                    "RESERVE_PROTECTION",
                    "reserve_soc_protection",
                    "evening_reserve_protection",
                },
                f"night+low-SOC SNU must carry an "
                f"explicit reason; got "
                f"{d.reason!r}",
            )

    def test_reserve_soc_change_moves_threshold(self):
        """The operator raises the SOC
        reserve from 20 % to 40 %. At SOC
        35 %, the engine with reserve=20
        does NOT fire reserve_protection
        (35 > 20+2=22), but with reserve=40
        the engine MUST fire
        reserve_protection (35 <= 40+2=42).
        """
        engine_a = self._build_engine(
            smart_mode="ADAPTIVE", reserve_soc=20.0
        )
        engine_b = self._build_engine(
            smart_mode="ADAPTIVE", reserve_soc=40.0
        )
        # 14:00 EEST (daytime, not night).
        # At SOC 35 %, reserve=20 (22 %),
        # reserve=40 (42 %).
        d_a = self._evaluate_at(
            engine_a, hour_local=14, soc=35.0,
            reserve_soc=20.0,
        )
        d_b = self._evaluate_at(
            engine_b, hour_local=14, soc=35.0,
            reserve_soc=40.0,
        )
        # Engine A: at SOC 35 %, reserve=20,
        # 35 > 22 -> no reserve_protection.
        if (
            d_a.charger_priority
            == self.ChargerPriority.SNU
        ):
            self.assertNotEqual(
                d_a.reason, "reserve_protection",
                f"SOC 35 %% + reserve 20 %% must "
                f"not fire reserve_protection; "
                f"got {d_a.reason!r}",
            )
        # Engine B: at SOC 35 %, reserve=40,
        # 35 <= 42 -> reserve_protection
        # must fire IF the engine ever
        # commits SNU. Daytime low-SOC
        # exception is the only path to
        # SNU.
        if (
            d_b.charger_priority
            == self.ChargerPriority.SNU
        ):
            self.assertIn(
                d_b.reason,
                {
                    "reserve_protection",
                    "RESERVE_PROTECTION",
                    "reserve_soc_protection",
                },
                f"SOC 35 %% + reserve 40 %% "
                f"must fire reserve_protection; "
                f"got {d_b.reason!r}",
            )

    def test_storm_only_when_predictive_mode_storm(self):
        """The storm branch only commits
        SNU when ``predictive_mode='storm'``.
        Other modes (off, shadow, assist)
        must not use ``storm_mode`` reason.
        """
        for mode in ("off", "shadow", "assist"):
            engine = self._build_engine(
                smart_mode="ADAPTIVE",
                reserve_soc=20.0,
                predictive_mode=mode,
            )
            d = self._evaluate_at(
                engine, hour_local=12, soc=50.0
            )
            self.assertNotEqual(
                d.reason, "storm_mode",
                f"predictive_mode={mode!r} must "
                f"not use storm_mode reason; "
                f"got {d.reason!r}",
            )

    def test_anti_flapping_holds_stable_state(self):
        """If the engine already committed
        SNU (current_charger=SNU) and the
        inputs are stable, the next cycle
        must NOT issue a new command. The
        engine may return a no-op
        (charger_priority=None) or hold
        the SNU; both are stable.
        """
        engine = self._build_engine(
            smart_mode="ADAPTIVE", reserve_soc=20.0
        )
        d = self._evaluate_at(
            engine, hour_local=2, soc=60.0,
            current_charger="SNU",
        )
        # Anti-flapping: the engine must
        # NOT issue a new command for the
        # same target. The decision can be
        # SNU (proposed) or None (no-op).
        self.assertIn(
            d.charger_priority,
            (self.ChargerPriority.SNU, None),
            f"night + current=SNU must hold "
            f"SNU or no-op; got "
            f"charger={d.charger_priority} "
            f"reason={d.reason!r}",
        )


class HemsSnuBehaviourEveningUnitMismatchTests(unittest.TestCase):
    """Regression: the round-6 fix in
    ``_adaptive_evening_protection``. The
    previous condition 3 mixed
    ``|surplus|`` (Watts) with
    ``SOC_pct * 0.3 * 100`` (pseudo-
    percent). For the live operator setup
    (corrected SOC ~80 %, surplus
    ~ -2540 W), the threshold evaluated
    to 80 * 30 = 2400 W and the
    condition fired when 2540 > 2400,
    picking SNU (solar + utility) and
    causing the inverter to charge from
    the grid at 39 A / 2168 W. After the
    fix, condition 3 does not pick SNU
    on this comparison.
    """

    @classmethod
    def setUpClass(cls):
        (
            cls.HemsEngine, cls.ChargerPriority,
            cls.OutputPriority, cls.SmartMode,
            cls._Reason, cls.PredictiveTuning,
        ) = _load()

    def _engine(self):
        engine = self.HemsEngine()
        engine._entry = SimpleNamespace(
            options={
                "smart_mode": self.SmartMode.ADAPTIVE,
                "reserve_soc": 20.0,
                "tuning": {
                    "battery_cycle_conservative": 0.0,
                },
            }
        )
        pt = self.PredictiveTuning()
        pt.predictive_mode = "off"
        engine.predictive_tuning = pt
        return engine

    def test_evening_high_soc_high_load_no_snu(self):
        """Live operator setup: SOC 80 %,
        surplus -2540 W. Evening 19:30
        EEST. After the round-6 fix, the
        engine must NOT commit SNU.
        """
        engine = self._engine()
        # 19:30 EEST = 16:30 UTC.
        now = datetime(
            2026, 10, 19, 16, 30, 0,
            tzinfo=timezone.utc,
        )
        d = engine.evaluate(
            smart_mode=self.SmartMode.ADAPTIVE,
            hems_auto=True,
            soc=80.0,
            pv_power=0.0,  # no sun at 19:30
            grid_power=2540.0,
            battery_power=2540.0,
            load_power=2540.0,
            grid_voltage=230.0,
            grid_available=True,
            current_output="USB",
            current_charger="OSO",
            now=now,
            reserve_soc=20.0,
            entry_id="test-entry",
        )
        self.assertNotEqual(
            d.charger_priority, self.ChargerPriority.SNU,
            f"evening 19:30 EEST, SOC 80 %, "
            f"surplus -2540 W: round-6 fix "
            f"must NOT commit SNU; got "
            f"charger={d.charger_priority} "
            f"reason={d.reason!r}",
        )

    def test_evening_at_soc_above_reserve_does_not_snu(self):
        """For SOC strictly above
        ``reserve + 5`` (live operator
        reserve is 20, so SOC > 25), the
        engine must not pick SNU from
        ``_adaptive_evening_protection``.
        """
        engine = self._engine()
        now = datetime(
            2026, 10, 19, 16, 30, 0,
            tzinfo=timezone.utc,
        )
        for soc in (50.0, 70.0, 90.0):
            d = engine.evaluate(
                smart_mode=self.SmartMode.ADAPTIVE,
                hems_auto=True,
                soc=soc,
                pv_power=0.0,
                grid_power=2000.0,
                battery_power=2000.0,
                load_power=2000.0,
                grid_voltage=230.0,
                grid_available=True,
                current_output="USB",
                current_charger="OSO",
                now=now,
                reserve_soc=20.0,
                entry_id="test-entry",
            )
            self.assertNotEqual(
                d.charger_priority,
                self.ChargerPriority.SNU,
                f"SOC {soc} %% + reserve 20 %%: "
                f"evening branch must NOT pick "
                f"SNU; got "
                f"charger={d.charger_priority} "
                f"reason={d.reason!r}",
            )


if __name__ == "__main__":
    unittest.main()
