"""Regression checks for decision guards, fresh hints and normalized telemetry."""
import ast
import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from hems.engine import HemsDecision, HemsEngine, SmartMode
from hems.tuning import HemsTuningService

NOW = datetime(2026, 10, 3, 12)


def evaluate(engine, **overrides):
    values = dict(smart_mode=SmartMode.ADAPTIVE, hems_auto=True, soc=60.,
                  pv_power=1000., grid_power=0., battery_power=0., load_power=500.,
                  grid_voltage=230., grid_available=True, current_output='0',
                  current_charger='1', now=NOW, forecast_tomorrow_kwh=2.)
    values.update(overrides)
    return engine.evaluate(**values)


def prime(engine, mode='assist'):
    engine.predictive_tuning.predictive_mode = mode
    engine._last_forecast_today_kwh = 2.
    engine._hourly_pv_forecast = [100.] * 24
    engine._hourly_radiation = [100.] * 24
    engine._hourly_weather_codes = [0] * 24
    engine._tariff_schedule = [4.32] * 24
    engine._consumption_history = [[500.] * 24] * 7
    hint = SimpleNamespace(target_soc_morning=60., target_soc_evening=70., confidence=1.)
    plan = SimpleNamespace(generated_at=NOW)
    engine._predictive_controller = SimpleNamespace(suggest=Mock(return_value=hint),
        decide=Mock(return_value=(HemsDecision(), plan)), last_plan=plan)
    return hint, plan


class TestEngineRefactor(unittest.TestCase):
    def setUp(self):
        self.log = patch('hems.engine.debug_logging.log_evaluation').start()
        self.addCleanup(patch.stopall)

    def test_display_usb_retains_recovery_hysteresis(self):
        numeric = evaluate(HemsEngine(), soc=40., pv_power=0., current_output='0', current_charger='2')
        display = evaluate(HemsEngine(), soc=40., pv_power=0., current_output='USB', current_charger='OSO')
        self.assertEqual(display, numeric)
        self.assertEqual(display.reason, 'hysteresis_recovery')
        self.assertEqual(display.charger_priority, '1')

    def test_integer_priorities_are_supported(self):
        numeric = evaluate(HemsEngine(), soc=40., pv_power=0., current_output='0', current_charger='2')
        self.assertEqual(evaluate(HemsEngine(), soc=40., pv_power=0., current_output=0, current_charger=2), numeric)

    def test_shadow_and_off_have_identical_commands(self):
        for mode in SmartMode:
            for hour in (0, 6, 7, 12, 17, 23):
                with self.subTest(mode=mode, hour=hour):
                    shadow, off = HemsEngine(), HemsEngine()
                    prime(shadow, 'shadow')
                    args = dict(smart_mode=mode, now=NOW.replace(hour=hour))
                    self.assertEqual(evaluate(shadow, **args), evaluate(off, **args))

    def test_invalid_telemetry_never_implies_a_full_battery(self):
        for field in ('soc', 'pv_power', 'load_power', 'grid_power', 'battery_power', 'grid_voltage', 'reserve_soc'):
            for value in (float('nan'), float('inf'), float('-inf'), 'invalid', True):
                with self.subTest(field=field, value=value):
                    e = HemsEngine()
                    prime(e)
                    result = evaluate(e, **{field: value})
                    self.assertTrue(result.skip)
                    self.assertEqual(result.reason, 'invalid_telemetry')
                    self.assertIsNone(result.output_priority)
                    self.assertIsNone(e._last_realtime_at)
                    self.assertFalse(e._predictive_controller.suggest.called)
                    self.assertEqual(e.tuning._recent_surplus, [])

    def test_missing_soc_is_unknown(self):
        self.assertTrue(evaluate(HemsEngine(), soc=None).skip)

    def test_invalid_optional_forecast_is_unknown(self):
        for fc in (float('inf'), float('nan'), -1., 'invalid'):
            e = HemsEngine()
            prime(e)
            result = evaluate(e, pv_power=0., forecast_tomorrow_kwh=fc)
            self.assertIsNone(e._last_predictive_hint)
            self.assertIsNone(result.output_priority)
            self.assertNotEqual(result.reason, 'forecast_good_use_battery')

    def test_offline_does_not_refresh_last_sample_or_write(self):
        e = HemsEngine()
        evaluate(e)
        result = evaluate(e, now=NOW+timedelta(minutes=5), is_online=False)
        self.assertTrue(result.skip)
        self.assertEqual(result.reason, 'inverter_offline')
        self.assertEqual(e._last_realtime_at, NOW)

    def test_stale_offline_data_never_drives_a_command(self):
        e = HemsEngine()
        evaluate(e)
        result = evaluate(e, now=NOW+timedelta(minutes=31), is_online=False, soc=10.)
        self.assertTrue(result.skip)
        self.assertEqual(result.reason, 'emergency_stale_data')
        self.assertIsNone(result.output_priority)

    def test_fresh_data_after_gap_resumes_normally(self):
        e = HemsEngine()
        evaluate(e)
        result = evaluate(e, now=NOW+timedelta(hours=1))
        self.assertFalse(result.skip)
        self.assertEqual(e._last_realtime_at, NOW+timedelta(hours=1))

    def test_off_clears_previous_recommendations(self):
        e = HemsEngine()
        prime(e)
        evaluate(e)
        self.assertIsNotNone(e._last_predictive_hint)
        e.predictive_tuning.predictive_mode = 'off'
        evaluate(e)
        self.assertIsNone(e._last_predictive_hint)
        self.assertIsNone(e._last_predictive_plan)
        self.assertIsNone(e._last_predictive_inputs)
        self.assertIsNone(e._predictive_controller.last_plan)

    def test_all_early_holds_clear_previous_recommendations(self):
        for reason in ('disabled', 'manual', 'circuit', 'offline', 'invalid', 'unknown_mode'):
            with self.subTest(reason=reason):
                e = HemsEngine()
                prime(e)
                evaluate(e)
                kwargs = {}
                if reason == 'disabled': kwargs['hems_auto'] = False
                elif reason == 'manual': e.arm_manual_override(NOW)
                elif reason == 'circuit': e.report_control_failure(NOW)
                elif reason == 'offline': kwargs['is_online'] = False
                elif reason == 'invalid': kwargs['soc'] = float('nan')
                else: kwargs['smart_mode'] = -1
                result = evaluate(e, **kwargs)
                self.assertTrue(result.skip)
                self.assertIsNone(e._last_predictive_hint)
                self.assertIsNone(e._last_predictive_plan)

    def test_planner_exception_clears_previous_hint(self):
        e = HemsEngine()
        prime(e)
        evaluate(e)
        e._predictive_controller.suggest.side_effect = ValueError('bad forecast')
        result = evaluate(e, now=NOW+timedelta(minutes=30))
        self.assertFalse(result.skip)
        self.assertIsNone(e._last_predictive_hint)
        self.assertIsNone(e._last_predictive_inputs)

    def test_plan_exception_keeps_only_current_hint(self):
        e = HemsEngine()
        hint, _ = prime(e)
        evaluate(e)
        e._predictive_controller.decide.side_effect = ValueError('bad plan')
        evaluate(e)
        self.assertIs(e._last_predictive_hint, hint)
        self.assertIsNone(e._last_predictive_plan)

    def test_missing_forecast_clears_previous_hint(self):
        e = HemsEngine()
        prime(e)
        evaluate(e)
        evaluate(e, forecast_tomorrow_kwh=None)
        self.assertIsNone(e._last_predictive_hint)

    def test_incomplete_hourly_forecast_blocks_planner(self):
        e = HemsEngine()
        prime(e)
        e._hourly_pv_forecast = [100.]
        evaluate(e)
        self.assertIsNone(e._last_predictive_hint)
        self.assertFalse(e._predictive_controller.suggest.called)

    def test_live_samples_during_manual_hold_stay_fresh(self):
        e = HemsEngine()
        e.arm_manual_override(NOW)
        now = NOW+timedelta(minutes=1)
        self.assertTrue(evaluate(e, now=now).skip)
        self.assertEqual(e._last_realtime_at, now)

    def test_reserve_floor_applies_to_arbitrage_as_well_as_adaptive(self):
        for mode in (SmartMode.ADAPTIVE, SmartMode.ARBITRAGE):
            for hour in (0, 12):
                e = HemsEngine()
                hint, _ = prime(e)
                hint.target_soc_morning = 0.
                result = evaluate(e, smart_mode=mode, now=NOW.replace(hour=hour), soc=42.,
                                  reserve_soc=40., current_output='SBU', current_charger='OSO')
                self.assertEqual((result.output_priority, result.charger_priority), ('0', '1'))
                self.assertEqual(result.reason, 'reserve_soc_protection')

    def test_storm_still_has_priority_over_hint(self):
        e = HemsEngine()
        prime(e)
        result = evaluate(e, smart_mode=SmartMode.STORM, current_output='SBU', current_charger='OSO')
        self.assertEqual((result.output_priority, result.charger_priority), ('0', '1'))
        self.assertEqual(result.reason, 'storm_mode')

    def test_manual_override_still_precedes_reserve_and_storm(self):
        for mode in SmartMode:
            e = HemsEngine()
            e.arm_manual_override(NOW)
            result = evaluate(e, smart_mode=mode, soc=10.)
            self.assertTrue(result.skip)
            self.assertEqual(result.reason, 'manual_override_hold')

    def test_circuit_breaker_backoff_unchanged(self):
        e = HemsEngine()
        for delay in (5, 12, 25, 45, 45):
            e.report_control_failure(NOW)
            self.assertEqual(e._blocked_until, NOW+timedelta(seconds=delay))
            self.assertEqual(evaluate(e, soc=10.).reason, 'circuit_breaker')
        e.report_control_success()
        self.assertFalse(evaluate(e).skip)

    def test_first_failure_is_logged_and_subsequent_logs_throttled(self):
        e = HemsEngine()
        with patch('hems.engine._LOGGER') as log:
            e.report_control_failure(NOW)
            e.report_control_failure(NOW+timedelta(seconds=10))
            self.assertEqual(log.warning.call_count, 1)

    def test_manual_hold_is_logged_and_throttled(self):
        e = HemsEngine()
        e.arm_manual_override(NOW)
        with patch('hems.engine._LOGGER') as log:
            evaluate(e)
            evaluate(e, now=NOW+timedelta(seconds=5))
            self.assertEqual(log.info.call_count, 1)

    def test_early_hold_is_in_decision_trace(self):
        evaluate(HemsEngine(), hems_auto=False)
        self.assertEqual(self.log.call_count, 1)
        self.assertEqual(self.log.call_args.kwargs['skip_reason'], 'hems_auto_off')

    def test_logging_failure_never_breaks_control(self):
        self.log.side_effect = RuntimeError('disk unavailable')
        self.assertFalse(evaluate(HemsEngine()).skip)
        self.assertTrue(evaluate(HemsEngine(), hems_auto=False).skip)

    def test_dedup_still_suppresses_repeated_command(self):
        e = HemsEngine()
        first = evaluate(e)
        again = evaluate(e, now=NOW+timedelta(seconds=5))
        self.assertEqual(first.output_priority, '2')
        self.assertIsNone(again.output_priority)
        self.assertIsNone(again.charger_priority)

    def test_dwell_blocks_sbu_but_allows_usb_safety(self):
        e = HemsEngine()
        e._last_output_switch_at = NOW
        self.assertIsNone(evaluate(e).output_priority)
        result = evaluate(e, soc=10., current_output='SBU', current_charger='OSO')
        self.assertEqual((result.output_priority, result.charger_priority), ('0', '1'))

    def test_buzzer_uses_evaluation_clock(self):
        self.assertFalse(evaluate(HemsEngine(), now=NOW).buzzer_off)
        self.assertTrue(evaluate(HemsEngine(), now=NOW.replace(hour=23)).buzzer_off)

    def test_solar_penalty_uses_evaluation_clock(self):
        tuning = HemsTuningService()
        self.assertEqual(tuning.compute_adaptive_pv_surplus(now=NOW), 300.)
        self.assertEqual(tuning.compute_adaptive_pv_surplus(now=NOW.replace(hour=18)), 450.)

    def test_failed_output_can_retry_after_backoff(self):
        e = HemsEngine()
        self.assertEqual(evaluate(e).output_priority, '2')
        e.report_control_failure(NOW)
        self.assertFalse(e.detect_manual_override('USB', 'SNU', NOW+timedelta(seconds=31)))
        self.assertEqual(evaluate(e, now=NOW+timedelta(seconds=6)).output_priority, '2')

    def test_partial_failure_keeps_successful_output(self):
        e = HemsEngine()
        evaluate(e)
        e.report_control_failure(NOW, output_failed=False, charger_failed=True)
        self.assertEqual(e._last_cmd_output, '2')
        self.assertEqual(e._last_output_switch_at, NOW)
        self.assertIsNone(e._last_cmd_charger)
        self.assertFalse(e.detect_manual_override('SBU', 'SNU', NOW+timedelta(seconds=31)))
        retry = evaluate(e, now=NOW+timedelta(seconds=6), current_output='SBU')
        self.assertIsNone(retry.output_priority)
        self.assertEqual(retry.charger_priority, '2')

    def test_failed_switch_restores_previous_dwell(self):
        e = HemsEngine()
        evaluate(e)
        e.report_control_success()
        evaluate(e, soc=10., current_output='SBU', current_charger='OSO', now=NOW+timedelta(minutes=1))
        e.report_control_failure(NOW+timedelta(minutes=1), output_failed=True, charger_failed=False)
        self.assertEqual(e._last_output_switch_at, NOW)
        self.assertEqual(e._last_cmd_charger, '1')

    def test_coordinator_reports_failed_channels_and_exceptions(self):
        source = Path(__file__).resolve().parents[1]/'coordinator.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'InverterCoordinator')
        method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_execute_hems_command')
        ns = {'HemsDecision': HemsDecision, 'datetime': SimpleNamespace(now=lambda: NOW), '_LOGGER': Mock()}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), ns)
        for failed_channel in ('output', 'charger', 'exception', 'neither'):
            with self.subTest(failed_channel=failed_channel):
                e = HemsEngine()
                decision = evaluate(e)
                api = SimpleNamespace(set_output_priority=AsyncMock(return_value=failed_channel != 'output'),
                    set_charger_priority=AsyncMock(return_value=failed_channel != 'charger'),
                    set_config_item=AsyncMock(return_value=True))
                if failed_channel == 'exception':
                    api.set_output_priority.side_effect = OSError('offline')
                c = SimpleNamespace(_hems=e, api=api)
                asyncio.run(ns['_execute_hems_command'](c, decision))
                self.assertEqual(e._last_cmd_output, None if failed_channel in ('output', 'exception') else '2')
                self.assertEqual(e._last_cmd_charger, None if failed_channel in ('charger', 'exception') else '2')
                self.assertEqual(e._consecutive_failures, 0 if failed_channel == 'neither' else 1)

    def test_invalid_morning_target_cannot_stop_charging(self):
        for mode in (SmartMode.ADAPTIVE, SmartMode.ARBITRAGE):
            for value in (-1., float('-inf'), float('nan'), float('inf'), 101.):
                e = HemsEngine()
                hint, _ = prime(e)
                hint.target_soc_morning = value
                result = evaluate(e, smart_mode=mode, now=NOW.replace(hour=23), pv_power=0.,
                                  current_charger='OSO')
                self.assertEqual(result.charger_priority, '1')


if __name__ == '__main__':
    unittest.main(verbosity=2)
