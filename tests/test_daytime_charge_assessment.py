"""Real-engine daytime charging assessment through the production API parser."""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO.parent))

from hems.engine import HemsEngine, SmartMode
from powmr_inverter.api import InverterApiClient


class _FakeVendorApi:
    """Return fixture API payloads through the production realtime parser."""

    def __init__(self) -> None:
        self.parser = InverterApiClient.__new__(InverterApiClient)
        self.calls = 0

    def realtime(self, *, soc: float, pv_w: float, load_w: float, surplus: bool) -> dict:
        self.calls += 1
        fields = {
            "batterySoc": {"value": str(soc)},
            "pvInputPower": {"value": str(pv_w)},
            "acOutputActivePower": {"value": str(load_w / 1000)},
            "batteryVoltage": {"value": "48"},
            "batteryCurrent": {"value": "0"},
            "batteryPower": {"value": "0"},
            "gridPower": {"value": str(abs(load_w - pv_w))},
            "gridPowerDirection": {"value": "-1" if surplus else "1"},
            "gridVoltage": {"value": "230"},
            "outputSourcePriority": {"value": "2"},
            "chargerSourcePriority": {"value": "2"},
        }
        return self.parser._parse_realtime_fields(fields, {"code": 0, "data": {"deviceAttributeState": fields}})


def _evaluate(api: _FakeVendorApi, *, soc: float, surplus: bool, hour: int,
              current_output: str = "0", current_charger: str = "2") -> dict:
    pv_w, load_w = (1800.0, 500.0) if surplus else (300.0, 1200.0)
    telemetry = api.realtime(soc=soc, pv_w=pv_w, load_w=load_w, surplus=surplus)
    engine = HemsEngine()
    decision = engine.evaluate(
        smart_mode=SmartMode.ADAPTIVE,
        hems_auto=True,
        soc=telemetry["batterySoc"],
        pv_power=telemetry["pvPower"],
        grid_power=telemetry["gridPower"],
        battery_power=telemetry["batteryPower"],
        load_power=telemetry["loadPower"],
        grid_voltage=telemetry["gridVoltage"],
        grid_available=True,
        current_output=current_output,
        current_charger=current_charger,
        now=datetime(2026, 10, 12, hour, 0),
        forecast_tomorrow_kwh=0.2,
        tarif_day=4.32,
        tarif_night=2.16,
    )
    return {
        "soc": soc,
        "period": "night" if hour >= 23 or hour < 7 else "day",
        "pv": "surplus" if surplus else "deficit",
        "output": decision.output_priority or current_output,
        "charger": decision.charger_priority or current_charger,
        "output_write": decision.output_priority,
        "charger_write": decision.charger_priority,
        "reason": decision.reason,
        "skip": decision.skip,
    }


class DaytimeChargeProductionAssessment(unittest.TestCase):
    def test_real_engine_matrix_with_fake_vendor_api(self) -> None:
        api = _FakeVendorApi()
        results = []
        for soc in (25, 34, 40, 60):
            for surplus in (True, False):
                # Start daytime policy cases from SBU to isolate their target
                # from the independent 35–45% USB recovery hysteresis.
                results.append(_evaluate(api, soc=soc, surplus=surplus, hour=12,
                                         current_output="2"))
        for soc in (25, 34, 40, 60):
            results.append(_evaluate(api, soc=soc, surplus=False, hour=23))

        # Separate explicit reserve protection and the 35–45% recovery hysteresis.
        results.append(_evaluate(api, soc=21, surplus=False, hour=12))
        results.append(_evaluate(api, soc=40, surplus=False, hour=12,
                                 current_output="0", current_charger="2"))

        actual = [
            (row["soc"], row["period"], row["pv"], row["output"], row["charger"], row["reason"])
            for row in results
        ]
        expected = [
            (25, "day", "surplus", "0", "1", "critical_recovery"),
            (25, "day", "deficit", "0", "1", "critical_recovery"),
            (34, "day", "surplus", "0", "1", "critical_recovery"),
            (34, "day", "deficit", "0", "1", "critical_recovery"),
            (40, "day", "surplus", "2", "2", "surplus_enter_sbu"),
            (40, "day", "deficit", "0", "2", "already_in_target"),
            (60, "day", "surplus", "2", "2", "surplus_enter_sbu"),
            (60, "day", "deficit", "0", "2", "already_in_target"),
            (25, "night", "deficit", "0", "1", "night_window_usb"),
            (34, "night", "deficit", "0", "1", "night_window_usb"),
            (40, "night", "deficit", "0", "1", "night_window_usb"),
            (60, "night", "deficit", "0", "1", "night_window_usb"),
            (21, "day", "deficit", "0", "1", "reserve_soc_protection"),
            (40, "day", "deficit", "0", "1", "hysteresis_recovery"),
        ]
        self.assertEqual(actual, expected)
        self.assertEqual(api.calls, len(results))
        print("DAYTIME_ASSESSMENT_JSON=" + json.dumps(results, separators=(",", ":")))


if __name__ == "__main__":
    unittest.main()
