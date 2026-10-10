"""Regression coverage for persistent manual-override mismatches."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hems.engine import HemsEngine, SmartMode


NOW = datetime(2026, 10, 10, 12, 0)


class ManualOverrideRearmTests(unittest.TestCase):
    def _assert_channel_does_not_rearm_old_mismatch(self, channel: str) -> None:
        engine = HemsEngine()
        if channel == "output":
            engine._last_cmd_output = "2"
            engine._last_cmd_output_at = NOW
            actual_output, actual_charger = "0", None
        elif channel == "charger":
            engine._last_cmd_charger = "1"
            engine._last_cmd_charger_at = NOW
            actual_output, actual_charger = None, "2"
        else:
            raise AssertionError(f"unknown test channel: {channel}")

        first_detection = NOW + timedelta(seconds=31)
        self.assertTrue(
            engine.detect_manual_override(actual_output, actual_charger, first_detection)
        )
        first_hold_until = first_detection + timedelta(
            minutes=engine.tun.manual_override_hold_min
        )
        self.assertEqual(engine._manual_override_until, first_hold_until)

        after_expiry = first_hold_until + timedelta(seconds=1)
        self.assertFalse(
            engine.detect_manual_override(actual_output, actual_charger, after_expiry)
        )
        self.assertEqual(engine._manual_override_until, first_hold_until)

        # A later HEMS command is a new mismatch episode, even when its target
        # and the observed inverter mode are unchanged.
        if channel == "output":
            engine._last_cmd_output_at = after_expiry
        else:
            engine._last_cmd_charger_at = after_expiry
        new_detection = after_expiry + timedelta(seconds=31)
        self.assertTrue(
            engine.detect_manual_override(actual_output, actual_charger, new_detection)
        )
        self.assertEqual(
            engine._manual_override_until,
            new_detection + timedelta(minutes=engine.tun.manual_override_hold_min),
        )

    def test_return_to_commanded_modes_allows_a_new_observed_mode_override(self) -> None:
        engine = HemsEngine()
        engine._last_cmd_output = "2"
        engine._last_cmd_charger = "1"
        engine._last_cmd_output_at = NOW
        engine._last_cmd_charger_at = NOW
        original_detection = NOW + timedelta(seconds=31)
        original_hold_until = NOW.replace(minute=5, second=31)

        self.assertTrue(engine.detect_manual_override("0", "2", original_detection))
        self.assertEqual(engine._manual_override_until, original_hold_until)

        # Returning both channels to their commanded modes ends the mismatch
        # episode even though the current hold remains in effect.
        self.assertFalse(
            engine.detect_manual_override("2", "1", NOW + timedelta(minutes=1))
        )
        self.assertEqual(engine._manual_override_until, original_hold_until)
        after_expiry = original_hold_until + timedelta(seconds=1)
        self.assertFalse(engine.detect_manual_override("2", "1", after_expiry))

        # A distinct observed output mode after the old hold is a new mismatch
        # episode; the observation alone does not identify who or what caused it.
        new_detection = after_expiry + timedelta(seconds=31)
        self.assertTrue(engine.detect_manual_override("1", "1", new_detection))
        self.assertEqual(
            engine._manual_override_until,
            new_detection + timedelta(minutes=engine.tun.manual_override_hold_min),
        )

    def test_simultaneous_mismatches_are_processed_without_sequential_rearm(self) -> None:
        engine = HemsEngine()
        engine._last_cmd_output = "2"
        engine._last_cmd_charger = "1"
        engine._last_cmd_output_at = NOW
        engine._last_cmd_charger_at = NOW
        actual_output, actual_charger = "0", "2"

        first_detection = NOW + timedelta(seconds=31)
        expected_hold_until = NOW.replace(minute=5, second=31)
        with self.assertLogs("hems.engine", level="INFO") as detection_logs:
            self.assertTrue(
                engine.detect_manual_override(actual_output, actual_charger, first_detection)
            )
        detection_messages = " ".join(detection_logs.output).lower()
        self.assertIn("observed output command-readback mismatch", detection_messages)
        self.assertIn("observed charger command-readback mismatch", detection_messages)
        self.assertNotIn("manual", detection_messages)
        self.assertIn("2 commanded → 0 observed", detection_messages)
        self.assertIn("1 commanded → 2 observed", detection_messages)
        self.assertEqual(engine._manual_override_until, expected_hold_until)

        # Repeated coordinator polls during the hold must remain held and must
        # not leave the second channel waiting to arm a sequential hold.
        for poll_time in (
            NOW.replace(minute=1),
            NOW.replace(minute=2),
            NOW.replace(minute=3),
            NOW.replace(minute=4),
            NOW.replace(minute=5, second=30),
        ):
            self.assertTrue(
                engine.detect_manual_override(actual_output, actual_charger, poll_time)
            )

        with self.assertLogs("hems.engine", level="INFO") as hold_logs:
            held = engine.evaluate(
                smart_mode=SmartMode.ARBITRAGE,
                hems_auto=True,
                soc=60.0,
                pv_power=1000.0,
                grid_power=0.0,
                battery_power=0.0,
                load_power=500.0,
                grid_voltage=230.0,
                grid_available=True,
                current_output=actual_output,
                current_charger=actual_charger,
                now=NOW.replace(minute=5),
            )
        hold_message = " ".join(hold_logs.output).lower()
        self.assertIn("observed command-readback mismatch hold", hold_message)
        self.assertNotIn("manual", hold_message)
        self.assertTrue(held.skip)
        self.assertEqual(held.reason, "manual_override_hold")

        after_expiry = NOW.replace(minute=5, second=32)
        self.assertFalse(
            engine.detect_manual_override(actual_output, actual_charger, after_expiry)
        )
        self.assertEqual(engine._manual_override_until, expected_hold_until)

        resumed = engine.evaluate(
            smart_mode=SmartMode.ARBITRAGE,
            hems_auto=True,
            soc=60.0,
            pv_power=1000.0,
            grid_power=0.0,
            battery_power=0.0,
            load_power=500.0,
            grid_voltage=230.0,
            grid_available=True,
            current_output=actual_output,
            current_charger=actual_charger,
            now=after_expiry,
        )
        self.assertFalse(resumed.skip)
        self.assertEqual(resumed.reason, "arbitrage_day_sbu")
        self.assertEqual(resumed.output_priority, "2")

    def test_persistent_output_and_charger_mismatches_do_not_rearm(self) -> None:
        for channel in ("output", "charger"):
            with self.subTest(channel=channel):
                self._assert_channel_does_not_rearm_old_mismatch(channel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
