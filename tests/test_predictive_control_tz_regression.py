"""Regression tests for the predictive_control.py TZ fix and the
stale applied cache reconciliation.

These tests exercise the production path:
  * hems.predictive_control.PredictiveControlEngine.evaluate
  * hems.predictive_control.apply_feedback
  * hems.predictive_control.restore_feedback
  * hems.engine.HemsEngine._reconcile_applied_cache
  * hems.engine.execute_hems_command (buzzer readback)

The earlier deployments at 6003a83 / fc1a2e8 did not touch
predictive_control.py. The coordinator's polling loop crashed
with ``TypeError: can't compare offset-naive and offset-aware
datetimes`` at predictive_control.py:120 on Europe/Kyiv. These
tests pin the contract so the regression cannot return.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make the repo importable for the bare ``from hems...`` imports
# used by ``predictive_control.py`` and ``engine.py``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hems.engine import (
    HemsDecision,
    HemsEngine,
    SmartMode,
    _as_utc,
    _tz_compare,
    execute_hems_command,
)
from hems.predictive_control import (
    PredictiveControlEngine,
    apply_feedback,
    restore_feedback,
)


class PredictiveControlEngineTzTests(unittest.TestCase):
    """Phase 2C+3 follow-up v5: the predictive_control pipeline
    must NOT raise ``TypeError`` when the coordinator passes
    a naive local ``now`` to ``evaluate`` / ``apply_feedback``
    / ``restore_feedback`` on Europe/Kyiv.

    Reproduced on live fc1a2e8 at 12:49 (poll crashed,
    ``success=False``):

      File ".../hems/predictive_control.py", line 120, in evaluate
      TypeError: can't compare offset-naive and offset-aware datetimes
    """

    def _engine(self) -> PredictiveControlEngine:
        return PredictiveControlEngine()

    def test_evaluate_with_naive_local_now_after_arm(self):
        """arm manual override -> evaluate with naive local
        now -> no exception. The previous code did
        ``self._manual_override_until <= now`` (aware vs
        naive) and raised.
        """
        eng = self._engine()
        # Stamp a manual override that expires 1 hour
        # AFTER the test's ``now`` so the hold is
        # unambiguously active.
        naive_local_now = datetime(2026, 10, 14, 11, 0, 0)
        eng._manual_override_until = _as_utc(
            naive_local_now + timedelta(hours=1)
        )
        self.assertIsNotNone(eng._manual_override_until)
        # 11:00 EEST. Must not raise.
        try:
            eng.evaluate(
                smart_mode=SmartMode.ARBITRAGE,
                hems_auto=True,
                soc=60.0,
                pv_power=1000.0,
                grid_power=0.0,
                battery_power=0.0,
                load_power=500.0,
                grid_voltage=230.0,
                grid_available=True,
                current_output="0",
                current_charger="1",
                now=naive_local_now,
            )
        except TypeError as exc:
            self.fail(
                f"evaluate() must not raise TypeError on "
                f"Europe/Kyiv with naive local now; got: {exc}"
            )

    def test_evaluate_active_hold_with_naive_local_now(self):
        """The hold is active 5 minutes into the future
        on Europe/Kyiv. The audit dict must publish the
        expiry. The predictive_user_target_soc must
        remain intact (we are in a hold).
        """
        eng = self._engine()
        # Stamp a manual override that expires 1 hour
        # AFTER the test's ``now`` so the hold is
        # unambiguously active during the call.
        naive_local_now = datetime(2026, 10, 14, 11, 0, 0)
        # The system tz on the test runner is unknown; the
        # safest way to set a UTC-aware deadline that
        # exceeds ``naive_local_now`` is to use
        # ``_as_utc`` on the test's own ``now``.
        eng._manual_override_until = _as_utc(
            naive_local_now + timedelta(hours=1)
        )
        eng._predictive_user_target_soc = 75
        eng.evaluate(
            smart_mode=SmartMode.ARBITRAGE,
            hems_auto=True,
            soc=60.0,
            pv_power=1000.0,
            grid_power=0.0,
            battery_power=0.0,
            load_power=500.0,
            grid_voltage=230.0,
            grid_available=True,
            current_output="0",
            current_charger="1",
            now=naive_local_now,
        )
        # The hold is still active -> target_soc preserved
        self.assertEqual(eng._predictive_user_target_soc, 75)
        # The audit dict must publish the override's expiry
        # in UTC. ``_manual_override_until`` is UTC-aware
        # already; the audit's ``override_pending_until``
        # must NOT be None because the hold is still active.
        self.assertIsNotNone(
            eng.predictive_decision_state.get(
                "override_pending_until"
            ),
            msg="override_pending_until must be set while "
            "the hold is active",
        )

    def test_evaluate_hold_expired_with_naive_local_now(self):
        """The hold has already expired. The audit dict
        must report ``override_pending_until=None`` and
        the predictive target_soc must be cleared.
        """
        eng = self._engine()
        # The default hold is 5 minutes. Set it to a time
        # in the past on Europe/Kyiv.
        past = datetime(2026, 10, 14, 11, 0, 0, tzinfo=timezone.utc)
        eng._manual_override_until = past
        eng._predictive_user_target_soc = 75
        naive_local_now = datetime(2026, 10, 14, 11, 1, 0)  # 1 min later
        eng.evaluate(
            smart_mode=SmartMode.ARBITRAGE,
            hems_auto=True,
            soc=60.0,
            pv_power=1000.0,
            grid_power=0.0,
            battery_power=0.0,
            load_power=500.0,
            grid_voltage=230.0,
            grid_available=True,
            current_output="0",
            current_charger="1",
            now=naive_local_now,
        )
        # Hold expired -> target_soc cleared
        self.assertIsNone(eng._predictive_user_target_soc)
        # The audit dict must report no pending override.
        self.assertIsNone(
            eng.predictive_decision_state.get(
                "override_pending_until"
            )
        )

    def test_apply_feedback_with_naive_local_now_on_kyiv(self):
        """apply_feedback rejects the action and stamps a
        UTC-aware ``_manual_override_until``. The
        previous code stamped a naive value and crashed
        on the next ``evaluate`` (Codex reproduction).
        """
        eng = self._engine()
        naive_local_now = datetime(2026, 10, 14, 11, 0, 0)
        record = apply_feedback(
            eng, "reject", naive_local_now, duration_min=30,
        )
        self.assertEqual(record["action"], "reject")
        # ``_manual_override_until`` is now UTC-aware.
        self.assertIsNotNone(eng._manual_override_until)
        self.assertIsNotNone(
            eng._manual_override_until.tzinfo,
            msg="apply_feedback must stamp a UTC-aware "
            "_manual_override_until",
        )
        # The returned ``until`` is a UTC ISO string.
        self.assertIn("+00:00", record["until"])

    def test_apply_feedback_extends_existing_hold(self):
        """Feedback may extend, never shorten an existing
        physical manual hold. With naive local ``now`` on
        Europe/Kyiv, the comparison uses ``_tz_compare``.
        """
        eng = self._engine()
        # Existing hold: 1 hour in the future (UTC).
        far_future = datetime(2026, 10, 14, 20, 0, 0, tzinfo=timezone.utc)
        eng._manual_override_until = far_future
        # New feedback: 30 minutes from naive local
        # ``now``. The naive now is 11:00 EEST on
        # 2026-10-14; the new hold would be 11:30 EEST.
        # The existing hold is at 20:00 UTC = 23:00
        # EEST, which is LATER than 11:30 EEST. The
        # feedback must extend (not shorten) the hold.
        naive_local_now = datetime(2026, 10, 14, 11, 0, 0)
        apply_feedback(eng, "reject", naive_local_now, duration_min=30)
        # Hold is still at the original far-future
        # deadline (extended, not shortened).
        self.assertEqual(
            eng._manual_override_until, far_future,
            msg="apply_feedback must extend, never shorten",
        )

    def test_restore_feedback_skips_expired(self):
        """restore_feedback with naive local now skips a
        record whose ``until`` is in the past. The
        previous code raised ``TypeError`` when
        comparing aware ``until`` against naive ``now``.
        """
        eng = self._engine()
        record = {
            "action": "reject",
            "target_soc": None,
            "until": "2026-10-14T08:00:00+00:00",  # past
        }
        naive_local_now = datetime(2026, 10, 14, 11, 0, 0)
        # Must not raise.
        restore_feedback(eng, record, naive_local_now)
        # And the hold is not restored (it's expired).
        self.assertIsNone(eng._manual_override_until)

    def test_restore_feedback_applies_unexpired(self):
        """restore_feedback with naive local now applies
        an unexpired record and stamps a UTC-aware
        ``_manual_override_until``.
        """
        eng = self._engine()
        # until = 1 hour in the future (UTC).
        until = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
        record = {
            "action": "reject",
            "target_soc": None,
            "until": until.isoformat(),
        }
        naive_local_now = datetime(2026, 10, 14, 11, 0, 0)
        restore_feedback(eng, record, naive_local_now)
        self.assertIsNotNone(eng._manual_override_until)
        self.assertIsNotNone(eng._manual_override_until.tzinfo)


class AppliedCacheReconciliationTests(unittest.TestCase):
    """Codex reproduction: ``_last_applied_output='2'`` set
    last cycle, fresh ``current_output='0'`` arrives, engine
    proposes '2/2' to recover, dispatcher short-circuits
    with ``api.calls=[]`` and ``no_op=True`` because
    ``_last_applied_output == proposal``. The inverter stays
    in '0/1'.

    The fix: ``_reconcile_applied_cache`` aligns the cache
    with the fresh observed state at the start of each
    cycle, preserving the manual override and the anti-
    flapping dedup window.
    """

    def test_cache_reconciles_with_fresh_observation(self):
        """Cycle 1: dispatcher writes '2/2', cache set.
        Cycle 2: device changed to '0/1' externally.
        Engine's reconciliation sees the divergence and
        updates the cache to '0/1'. The next dispatcher
        cycle sees ``applied='0'`` vs ``proposal='2'`` ->
        real write.
        """
        eng = HemsEngine()
        # Simulate cycle 1: confirmed apply.
        eng._last_applied_output = "2"
        eng._last_applied_charger = "2"
        # Simulate cycle 2: device changed externally.
        naive_now = datetime.now()
        eng._reconcile_applied_cache(
            now=naive_now,
            current_output="0",
            current_charger="1",
        )
        self.assertEqual(eng._last_applied_output, "0")
        self.assertEqual(eng._last_applied_charger, "1")

    def test_cache_preserved_under_manual_override(self):
        """Manual override is active. The reconciliation
        must NOT touch the cache; the user is in control.
        """
        eng = HemsEngine()
        eng._last_applied_output = "2"
        eng._last_applied_charger = "2"
        eng.arm_manual_override()
        naive_now = datetime.now()
        eng._reconcile_applied_cache(
            now=naive_now,
            current_output="0",
            current_charger="1",
        )
        # Cache preserved.
        self.assertEqual(eng._last_applied_output, "2")
        self.assertEqual(eng._last_applied_charger, "2")

    def test_cache_preserved_during_anti_flapping_window(self):
        """A recent proposal may not have been observed
        yet. The reconciliation must NOT touch the cache
        while the dedup window is open.
        """
        eng = HemsEngine()
        eng._last_applied_output = "2"
        eng._last_applied_charger = "2"
        # Stamp a recent proposal (now - 1s). The dedup
        # window default is 30s, so the window is open.
        naive_now = datetime.now()
        eng._last_cmd_output_at = naive_now - timedelta(seconds=1)
        eng._last_cmd_charger_at = naive_now - timedelta(seconds=1)
        eng._reconcile_applied_cache(
            now=naive_now,
            current_output="0",
            current_charger="1",
        )
        # Cache preserved (anti-flapping).
        self.assertEqual(eng._last_applied_output, "2")
        self.assertEqual(eng._last_applied_charger, "2")

    def test_cache_reconciles_after_dedup_window_expires(self):
        """After the dedup window expires, the
        reconciliation runs and the cache is updated to
        match the observed state.
        """
        eng = HemsEngine()
        eng._last_applied_output = "2"
        eng._last_applied_charger = "2"
        # The default dedup window is 300s (5 min).
        # Stamp a proposal well past the dedup window.
        naive_now = datetime.now()
        eng._last_cmd_output_at = naive_now - timedelta(seconds=600)
        eng._last_cmd_charger_at = naive_now - timedelta(seconds=600)
        eng._reconcile_applied_cache(
            now=naive_now,
            current_output="0",
            current_charger="1",
        )
        self.assertEqual(eng._last_applied_output, "0")
        self.assertEqual(eng._last_applied_charger, "1")

    def test_cache_seeds_from_first_observation(self):
        """First observation after a restart: the cache
        is None. The reconciliation must seed the cache
        with the observed value so the no-op gate does
        not fire spuriously.
        """
        eng = HemsEngine()
        # Cache is None.
        self.assertIsNone(eng._last_applied_output)
        self.assertIsNone(eng._last_applied_charger)
        naive_now = datetime.now()
        eng._reconcile_applied_cache(
            now=naive_now,
            current_output="0",
            current_charger="1",
        )
        self.assertEqual(eng._last_applied_output, "0")
        self.assertEqual(eng._last_applied_charger, "1")

    def test_cache_unchanged_when_observed_none(self):
        """No telemetry: the cache must NOT be
        invalidated. We do not know the device state.
        """
        eng = HemsEngine()
        eng._last_applied_output = "2"
        eng._last_applied_charger = "2"
        naive_now = datetime.now()
        eng._reconcile_applied_cache(
            now=naive_now,
            current_output=None,
            current_charger=None,
        )
        self.assertEqual(eng._last_applied_output, "2")
        self.assertEqual(eng._last_applied_charger, "2")

    def test_end_to_end_recovery_via_dispatcher(self):
        """End-to-end: cycle 1 sets the cache, an
        external change shifts the device, cycle 2
        reconciles and the dispatcher issues a real
        write to recover.
        """
        import asyncio
        from tests.test_outage_runtime import FakeApiTransport

        eng = HemsEngine()
        # Cycle 1: confirmed apply. We use the dispatcher's
        # success path indirectly by setting the cache.
        eng._last_applied_output = "2"
        eng._last_applied_charger = "2"
        # Cycle 2: device changed externally.
        naive_now = datetime.now()
        eng._reconcile_applied_cache(
            now=naive_now,
            current_output="0",
            current_charger="1",
        )
        # The cache is now '0/1' (matches observed).
        self.assertEqual(eng._last_applied_output, "0")
        self.assertEqual(eng._last_applied_charger, "1")
        # Now the engine proposes '2/2' again. The
        # dispatcher's no-op gate sees
        # ``_last_applied_output='0'`` vs ``proposal='2'``
        # and the gate is open. The dispatcher issues
        # the write.
        decision = HemsDecision(
            output_priority="2",
            charger_priority="2",
            reason="recovery",
        )
        from datetime import timezone as _tz
        fresh = datetime.now(_tz.utc) - timedelta(seconds=10)
        api = FakeApiTransport(last_success_at=fresh)
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                execute_hems_command(
                    api=api, hems=eng, decision=decision,
                    command_id="recovery-test",
                )
            )
        finally:
            loop.close()
        method_names = [call[0] for call in api.calls]
        self.assertIn(
            "set_output_priority", method_names,
            msg=f"recovery write must reach the transport; "
            f"got: {method_names!r}",
        )
        self.assertIn(
            "set_charger_priority", method_names,
            msg=f"recovery write must reach the transport; "
            f"got: {method_names!r}",
        )
        # And the cache is updated to the applied value.
        self.assertEqual(eng._last_applied_output, "2")
        self.assertEqual(eng._last_applied_charger, "2")
        self.assertTrue(result.success)


class BuzzerReadbackTests(unittest.TestCase):
    """Codex reproduction: ``set_config_item('buzzerAlarmSetting',
    '0')`` returned ACK but the readback reported ``'1'``. The
    previous code updated ``_last_buzzer='0'`` after the ACK
    (before the readback) and the readback never inspected
    ``buzzerAlarmSetting``.

    The fix:
      * ``_last_buzzer`` is updated only after the readback
        confirmed the new value.
      * The readback section inspects ``buzzerAlarmSetting``
        and raises on missing / mismatch.
    """

    def test_real_buzzer_write_updates_cache_on_readback_success(self):
        """API ACK + readback returns the new value:
        ``_last_buzzer`` is updated to the new value.
        """
        import asyncio
        from tests.test_outage_runtime import FakeApiTransport

        eng = HemsEngine()
        eng._last_buzzer = "1"
        decision = HemsDecision(
            output_priority=None,
            charger_priority=None,
            buzzer_off=True,
            reason="test_turn_off",
        )
        from datetime import timezone as _tz
        fresh = datetime.now(_tz.utc) - timedelta(seconds=10)
        api = FakeApiTransport(
            last_success_at=fresh,
            get_buzzer="1",  # device currently in '1'
        )
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                execute_hems_command(
                    api=api, hems=eng, decision=decision,
                    command_id="buzzer-success-test",
                )
            )
        finally:
            loop.close()
        # The set ran, the readback returned '0' (the
        # fake updates _buzzer in set_config_item), so
        # the readback passes and ``_last_buzzer`` is
        # updated to '0'.
        self.assertTrue(result.success)
        self.assertEqual(eng._last_buzzer, "0")

    def test_buzzer_readback_mismatch_does_not_update_cache(self):
        """API ACK + readback returns the OLD value:
        ``_last_buzzer`` must NOT be updated. The
        dispatcher reports the mismatch via
        ``readback_valid=False``.
        """
        import asyncio
        from tests.test_outage_runtime import FakeApiTransport

        eng = HemsEngine()
        eng._last_buzzer = "1"
        decision = HemsDecision(
            output_priority=None,
            charger_priority=None,
            buzzer_off=True,  # requested '0'
            reason="test_mismatch",
        )
        from datetime import timezone as _tz
        fresh = datetime.now(_tz.utc) - timedelta(seconds=10)
        api = FakeApiTransport(
            last_success_at=fresh,
            get_buzzer="1",  # device currently in '1' (will stay)
        )
        # Force a buzzer readback mismatch: the device
        # reports '1' even after we wrote '0'.
        api._readback_mismatch_buzzer = "0"  # requested
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                execute_hems_command(
                    api=api, hems=eng, decision=decision,
                    command_id="buzzer-mismatch-test",
                )
            )
        finally:
            loop.close()
        # The dispatcher reports the mismatch.
        self.assertFalse(
            result.readback_valid,
            msg=f"readback must be invalid on buzzer "
            f"mismatch; got {result!r}",
        )
        # And the cache was NOT updated: the device
        # is still in '1'.
        self.assertEqual(
            eng._last_buzzer, "1",
            msg=f"cache must NOT advance on readback "
            f"mismatch; got {eng._last_buzzer!r}",
        )
        # The next cycle is therefore a real write
        # (the no-op gate sees ``_last_buzzer='1'``
        # and ``buzzer_off=True`` -> ``has_buzzer_write=True``).

    def test_buzzer_readback_missing_does_not_update_cache(self):
        """API ACK + readback returned a dict WITHOUT
        ``buzzerAlarmSetting``: the dispatcher must
        raise on missing and ``_last_buzzer`` must NOT
        be updated.
        """
        import asyncio

        eng = HemsEngine()
        eng._last_buzzer = "1"
        decision = HemsDecision(
            output_priority=None,
            charger_priority=None,
            buzzer_off=True,
            reason="test_missing",
        )

        class _NoBuzzerApi:
            def __init__(self):
                self.calls = []
                self.last_realtime_success_received_at = (
                    datetime.now(timezone.utc) - timedelta(seconds=10)
                )

            async def set_config_item(self, key, value):
                self.calls.append(("set_config_item", (key, value), None))
                return True

            async def fetch_device_configs(self):
                self.calls.append(("fetch_device_configs", (), None))
                # No buzzerAlarmSetting key.
                return {
                    "outputSourcePrioritySetting": {"value": "0"},
                    "chargerSourcePrioritySetting": {"value": "1"},
                }

        api = _NoBuzzerApi()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                execute_hems_command(
                    api=api, hems=eng, decision=decision,
                    command_id="buzzer-missing-test",
                )
            )
        finally:
            loop.close()
        # The dispatcher reports the missing key.
        self.assertFalse(
            result.readback_valid,
            msg=f"readback must be invalid when "
            f"buzzerAlarmSetting is missing; got {result!r}",
        )
        # And the cache is preserved.
        self.assertEqual(eng._last_buzzer, "1")


if __name__ == "__main__":
    unittest.main()
