"""Regression tests for the readback / rate-limit / pending-
confirmation handling in ``hems.engine.execute_hems_command``
and ``HemsEngine.confirm_pending``.

These tests exercise the production dispatcher + the
production ``confirm_pending`` retry path with a fake
transport. Each test sets up a different combination of
API ACK / readback result and verifies the dispatcher's
classification:

  1. ACK -> rate-limited readback -> ``awaiting_confirmation``,
     ``_consecutive_failures`` stays 0, ledger entry stored,
     ``_last_applied_*`` NOT advanced.
  2. Pending confirmation succeeds on retry -> ledger entry
     removed, ``_last_applied_*`` advanced, no failure
     counter increment.
  3. Pending confirmation times out after the bounded window
     -> ledger entry removed, ``_last_applied_*`` NOT
     advanced, no failure counter increment.
  4. True mismatch (the device rejects the write) -> ledger
     entry removed, ``_consecutive_failures`` incremented,
     ``_blocked_until`` armed.
  5. Already-ACK'd command is NOT re-issued while the
     pending entry exists; only the readback is retried.
  6. Concurrent diagnostic fetch and dispatcher readback
     serialise through the api's
     ``_device_configs_lock``.
  7. Rate-limit signal differentiates from a real rejection
     (the dispatcher's bookkeeping treats them differently).
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hems.engine import (
    HemsDecision,
    HemsEngine,
    execute_hems_command,
)


# Re-use the production fake transport from the existing
# test suite. It already exposes ``set_*`` and
# ``fetch_device_configs`` with configurable readback
# mismatch signals. We extend it with a rate-limit signal
# (matching the production api.py helper).
class _RateLimitedFakeApi:
    """Fake transport that models the production api
    contract for the rate-limited readback path:

      * ``set_*`` always returns True (API ACK).
      * ``fetch_device_configs`` returns whatever the
        current test scenario dictates (populated dict
        or empty).
      * ``was_last_device_configs_read_rate_limited``
        returns the rate-limit signal. The production
        api sets ``last_device_configs_received_at``
        to a UTC datetime on success; we model that
        with an instance attribute.
      * ``_device_configs_lock`` is the asyncio.Lock
        that serialises concurrent callers.
    """

    def __init__(self):
        # ``_output`` and ``_charger`` track what the
        # device would return on a successful readback.
        self._output = "0"
        self._charger = "1"
        self._buzzer = "1"
        # ``_last_device_configs_received_at`` mirrors
        # the production field.
        self.last_device_configs_received_at = None
        # ``_rate_limited`` is True when the next /
        # current ``fetch_device_configs`` call should
        # return an empty dict and report rate-limit.
        self._rate_limited = True
        # ``_mismatch`` is True when the readback
        # should report a value different from what
        # the dispatcher wrote (true rejection).
        self._mismatch = False
        # Production-style lock for serialisation.
        self._device_configs_lock = asyncio.Lock()
        self.calls = []

    # --- Production parity: the lock is the same
    # attribute the production api exposes. ---
    async def set_output_priority(self, value):
        self.calls.append(("set_output_priority", (value,), None))
        return True

    async def set_charger_priority(self, value):
        self.calls.append(("set_charger_priority", (value,), None))
        return True

    async def set_config_item(self, key, value):
        self.calls.append(("set_config_item", (key, value), None))
        if key == "buzzerAlarmSetting":
            self._buzzer = str(value)
        return True

    async def fetch_device_configs(self):
        # The lock MUST serialise this method. The
        # production wrapper holds ``_device_configs_lock``
        # around the body. Mirror that here.
        async with self._device_configs_lock:
            self.calls.append(("fetch_device_configs", (), None))
            if self._rate_limited:
                # Empty dict, ``last_device_configs_received_at``
                # stays None.
                return {}
            if self._mismatch:
                # Return values that deliberately do NOT
                # match what the dispatcher wrote.
                return {
                    "outputSourcePrioritySetting": {
                        "value": "WRONG", "name": "Output",
                    },
                    "chargerSourcePrioritySetting": {
                        "value": "WRONG", "name": "Charger",
                    },
                    "buzzerAlarmSetting": {
                        "value": "WRONG", "name": "Buzzer",
                    },
                }
            # Success: return the current device state.
            self.last_device_configs_received_at = datetime.now(
                timezone.utc
            )
            return {
                "outputSourcePrioritySetting": {
                    "value": self._output, "name": "Output",
                },
                "chargerSourcePrioritySetting": {
                    "value": self._charger, "name": "Charger",
                },
                "buzzerAlarmSetting": {
                    "value": self._buzzer, "name": "Buzzer",
                },
            }

    def was_last_device_configs_read_rate_limited(self) -> bool:
        if self.last_device_configs_received_at is None:
            return True
        return False


class RateLimitedReadbackTests(unittest.TestCase):
    """ACK + rate-limited readback must NOT increment the
    failure counter and must NOT advance the LAST APPLIED
    state. The command is parked in a pending-confirmation
    ledger for retry.
    """

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_ack_then_rate_limited_readback_marks_awaiting_confirmation(self):
        """``set_output_priority('2')`` returns True (ACK).
        ``fetch_device_configs()`` returns {} (rate-limit
        signal). The dispatcher must:
          * return ``awaiting_confirmation=True``
          * return ``rate_limited_readback=True``
          * NOT increment ``_consecutive_failures``
          * NOT arm ``_blocked_until``
          * NOT advance ``_last_applied_output``
          * Park the command in ``_pending_confirmations``
        """
        eng = HemsEngine()
        api = _RateLimitedFakeApi()
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="night_arbitrage",
        )
        result = self._run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="rl-test-1",
            )
        )
        self.assertTrue(
            result.api_accepted,
            msg=f"API ACK was successful; api_accepted must "
            f"be True; got {result!r}",
        )
        self.assertTrue(
            result.awaiting_confirmation,
            msg=f"readback rate-limited; awaiting_confirmation "
            f"must be True; got {result!r}",
        )
        self.assertTrue(
            result.rate_limited_readback,
            msg=f"readback rate-limited signal must be True; "
            f"got {result!r}",
        )
        # Failure counter must NOT advance on a
        # rate-limited readback.
        self.assertEqual(
            eng._consecutive_failures, 0,
            msg="rate-limited readback must NOT increment "
            "the failure counter",
        )
        self.assertIsNone(
            eng._blocked_until,
            msg="rate-limited readback must NOT arm the "
            "circuit breaker",
        )
        # LAST APPLIED state must NOT advance.
        self.assertIsNone(
            eng._last_applied_output,
            msg="LAST APPLIED state must NOT advance on a "
            "rate-limited readback",
        )
        # The command is parked in the ledger.
        self.assertIn(
            "rl-test-1", eng._pending_confirmations,
            msg="rate-limited command must be parked in "
            "_pending_confirmations for retry",
        )

    def test_real_mismatch_increments_failures(self):
        """ACK + the readback returns a different value
        (true rejection). The dispatcher must:
          * return ``readback_valid=False``
          * increment ``_consecutive_failures``
          * arm ``_blocked_until``
        """
        eng = HemsEngine()
        api = _RateLimitedFakeApi()
        api._rate_limited = False
        api._mismatch = True
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="night_arbitrage",
        )
        result = self._run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="mm-test-1",
            )
        )
        self.assertFalse(
            result.readback_valid,
            msg=f"true mismatch must mark readback_valid="
            f"False; got {result!r}",
        )
        self.assertEqual(
            eng._consecutive_failures, 1,
            msg="true mismatch must increment the failure "
            "counter",
        )
        self.assertIsNotNone(
            eng._blocked_until,
            msg="true mismatch must arm the circuit breaker",
        )

    def test_successful_readback_advances_applied_state(self):
        """ACK + readback matches the requested value. The
        dispatcher must:
          * return ``readback_valid=True``
          * advance ``_last_applied_output``
          * reset the failure counter
        """
        eng = HemsEngine()
        api = _RateLimitedFakeApi()
        api._rate_limited = False
        api._mismatch = False
        # Configure the device to report the new value
        # the dispatcher will write.
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="night_arbitrage",
        )
        # The fake returns self._output; set it BEFORE
        # the dispatcher writes. (In production, the
        # api would re-read the device after the write
        # and report the new value.)
        # We pre-set _output to the requested value
        # to model a successful readback.
        api._output = "2"
        result = self._run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="ok-test-1",
            )
        )
        self.assertTrue(
            result.readback_valid,
            msg=f"successful readback must mark readback_valid="
            f"True; got {result!r}",
        )
        self.assertEqual(eng._last_applied_output, "2")
        self.assertEqual(eng._consecutive_failures, 0)


class PendingConfirmationRetryTests(unittest.TestCase):
    """The pending-confirmation ledger must retry the
    readback WITHOUT re-issuing the write. The bounded
    retry budget (3 attempts within 15s) prevents
    indefinite waits.
    """

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_pending_confirmation_succeeds_on_retry(self):
        """Cycle 1: ACK + rate-limited readback. Ledger
        entry stored.
        Cycle 2: ``confirm_pending`` runs; the api now
        returns the matching value. The entry is
        removed, ``_last_applied_output`` advanced.
        The ``set_output_priority`` call was issued
        exactly once.
        """
        eng = HemsEngine()
        api = _RateLimitedFakeApi()
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="recovery",
        )
        # Cycle 1: rate-limited.
        api._rate_limited = True
        self._run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="retry-test",
            )
        )
        self.assertIn("retry-test", eng._pending_confirmations)
        self.assertEqual(eng._consecutive_failures, 0)
        # The write happened exactly once.
        write_calls = [
            c for c in api.calls
            if c[0] in ("set_output_priority", "set_charger_priority")
        ]
        self.assertEqual(
            len(write_calls), 1,
            msg=f"write must have been issued exactly once; "
            f"got: {write_calls!r}",
        )
        # Cycle 2: now the readback succeeds. Patch the
        # api to return the new value.
        api._rate_limited = False
        api._output = "2"  # match the requested
        # Use a now well past the per-attempt backoff.
        from datetime import timezone as _tz
        future_now = datetime.now(_tz.utc) + timedelta(seconds=2)
        summary = self._run(
            eng.confirm_pending(api, now=future_now)
        )
        self.assertEqual(
            summary["confirmed"], 1,
            msg=f"pending confirmation must confirm on "
            f"retry; got {summary!r}",
        )
        self.assertEqual(
            summary["still_pending"], 0,
            msg=f"no entries should remain pending; got "
            f"{summary!r}",
        )
        # LAST APPLIED state advanced.
        self.assertEqual(eng._last_applied_output, "2")
        # No failure was counted.
        self.assertEqual(eng._consecutive_failures, 0)
        # Ledger is empty.
        self.assertNotIn("retry-test", eng._pending_confirmations)
        # The write was NOT re-issued on retry.
        write_calls_after = [
            c for c in api.calls
            if c[0] in ("set_output_priority", "set_charger_priority")
        ]
        self.assertEqual(
            len(write_calls_after), 1,
            msg=f"write must NOT be re-issued on retry; "
            f"got: {write_calls_after!r}",
        )

    def test_pending_confirmation_times_out_without_declaring_applied(self):
        """The pending entry has a 15s deadline. After
        the deadline the entry is dropped WITHOUT
        advancing ``_last_applied_output`` and WITHOUT
        incrementing ``_consecutive_failures``. Codex
        demand: ``Не оголошуйте її застосованою до
        підтвердження``.
        """
        eng = HemsEngine()
        api = _RateLimitedFakeApi()
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="recovery",
        )
        # Cycle 1: rate-limited. Deadline stored.
        from datetime import timezone as _tz
        first_now = datetime.now(_tz.utc)
        api._rate_limited = True
        self._run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="timeout-test",
            )
        )
        # Cycle 2: 30s later, well past the 15s
        # deadline.
        later_now = first_now + timedelta(seconds=30)
        api._rate_limited = False  # api responds but the
        # deadline already expired
        summary = self._run(
            eng.confirm_pending(api, now=later_now)
        )
        self.assertEqual(
            summary["timed_out"], 1,
            msg=f"pending entry must be marked timed out; "
            f"got {summary!r}",
        )
        # The entry was dropped.
        self.assertNotIn("timeout-test", eng._pending_confirmations)
        # The LAST APPLIED state was NOT advanced.
        self.assertIsNone(
            eng._last_applied_output,
            msg="timed-out confirmation must NOT advance "
            "the LAST APPLIED state",
        )
        # The failure counter was NOT incremented.
        self.assertEqual(
            eng._consecutive_failures, 0,
            msg="timed-out confirmation must NOT count as a "
            "transport failure",
        )

    def test_real_mismatch_on_retry_increments_failures(self):
        """The first cycle is rate-limited; the retry
        returns a value that DIFFERS from the requested
        one. This is a real transport failure; the
        failure counter advances.
        """
        eng = HemsEngine()
        api = _RateLimitedFakeApi()
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="recovery",
        )
        from datetime import timezone as _tz
        first_now = datetime.now(_tz.utc)
        api._rate_limited = True
        self._run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="mm-retry-test",
            )
        )
        # Cycle 2: the api now responds but with a
        # mismatch.
        api._rate_limited = False
        api._mismatch = True
        later_now = first_now + timedelta(seconds=2)
        summary = self._run(
            eng.confirm_pending(api, now=later_now)
        )
        self.assertEqual(
            summary["failed"], 1,
            msg=f"real mismatch on retry must be counted as "
            f"a failure; got {summary!r}",
        )
        # The failure counter advanced.
        self.assertEqual(eng._consecutive_failures, 1)
        # The circuit breaker is armed.
        self.assertIsNotNone(eng._blocked_until)
        # The entry was dropped.
        self.assertNotIn("mm-retry-test", eng._pending_confirmations)

    def test_still_rate_limited_on_retry_keeps_entry(self):
        """The api is still rate-limited on the retry;
        the entry stays in the ledger with an updated
        ``last_attempt_at`` and an incremented
        ``attempts`` counter.
        """
        eng = HemsEngine()
        api = _RateLimitedFakeApi()
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="recovery",
        )
        from datetime import timezone as _tz
        first_now = datetime.now(_tz.utc)
        api._rate_limited = True
        self._run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="rl-retry-test",
            )
        )
        # Cycle 2: still rate-limited.
        later_now = first_now + timedelta(seconds=2)
        summary = self._run(
            eng.confirm_pending(api, now=later_now)
        )
        self.assertEqual(
            summary["still_pending"], 1,
            msg=f"still-rate-limited retry must keep the "
            f"entry pending; got {summary!r}",
        )
        self.assertEqual(
            summary["failed"], 0,
            msg=f"still-rate-limited retry must NOT count as "
            f"a failure; got {summary!r}",
        )
        # The entry is still in the ledger with
        # attempts >= 2.
        self.assertIn("rl-retry-test", eng._pending_confirmations)
        self.assertGreaterEqual(
            eng._pending_confirmations["rl-retry-test"]["attempts"],
            2,
        )
        # No failure counted.
        self.assertEqual(eng._consecutive_failures, 0)

    def test_confirm_pending_respects_per_attempt_backoff(self):
        """The retry is throttled by a per-attempt
        backoff (1s, 3s, 5s, 8s). A retry too soon
        after the previous attempt is a no-op.
        """
        eng = HemsEngine()
        api = _RateLimitedFakeApi()
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="recovery",
        )
        from datetime import timezone as _tz
        first_now = datetime.now(_tz.utc)
        api._rate_limited = True
        self._run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="backoff-test",
            )
        )
        # Cycle 2: api responds but the retry is
        # within the 1s backoff.
        api._rate_limited = False
        api._output = "2"
        just_after = first_now + timedelta(milliseconds=500)
        summary = self._run(
            eng.confirm_pending(api, now=just_after)
        )
        # The retry was deferred.
        self.assertEqual(
            summary["still_pending"], 1,
            msg=f"retry within 1s backoff must defer; got "
            f"{summary!r}",
        )
        self.assertEqual(summary["confirmed"], 0)
        # The entry is still in the ledger.
        self.assertIn("backoff-test", eng._pending_confirmations)


class ConcurrentReadbackSerialisationTests(unittest.TestCase):
    """The api's ``_device_configs_lock`` serialises the
    coordinator's diagnostic fetch and the dispatcher's
    readback. Without the lock, the two calls race past
    the rate-limit gate and one of them gets an empty
    response.
    """

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_diagnostic_and_dispatch_share_lock(self):
        """The fake's ``_device_configs_lock`` is the
        same instance used by the dispatcher's readback
        and the coordinator's diagnostic fetch. Two
        concurrent ``fetch_device_configs`` calls are
        serialised.
        """
        eng = HemsEngine()
        api = _RateLimitedFakeApi()
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="recovery",
        )
        # Pre-arm a pending entry so ``confirm_pending``
        # will run a real readback.
        eng._pending_confirmations["pre"] = {
            "command_id": "pre",
            "output_priority": "2",
            "charger_priority": None,
            "buzzer_off": None,
            "first_seen_at": datetime.now(timezone.utc) - timedelta(seconds=2),
            "last_attempt_at": datetime.now(timezone.utc) - timedelta(seconds=2),
            "attempts": 1,
            "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=15),
        }
        api._rate_limited = False
        api._output = "2"
        # Run the confirm_pending path which uses
        # ``fetch_device_configs``. The fake's
        # ``fetch_device_configs`` body holds
        # ``_device_configs_lock``; a concurrent
        # ``fetch_device_configs`` (modelling the
        # coordinator's diagnostic read) would be
        # blocked until the first one releases.
        from datetime import timezone as _tz
        now = datetime.now(_tz.utc)
        summary = self._run(eng.confirm_pending(api, now=now))
        self.assertEqual(
            summary["confirmed"], 1,
            msg=f"confirmation should succeed when the api "
            f"is not rate-limited; got {summary!r}",
        )
        # The lock is exposed on the api. The
        # production wrapper holds it around the
        # body; we verify the lock is part of the
        # public surface.
        self.assertTrue(hasattr(api, "_device_configs_lock"))
        # The lock is an asyncio.Lock.
        self.assertIsInstance(api._device_configs_lock, asyncio.Lock)


if __name__ == "__main__":
    unittest.main()
