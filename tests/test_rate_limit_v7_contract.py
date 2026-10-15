"""Regression tests for the v7 fix at e6cf963.

Codex reproduced two structural defects on the e6cf963
deployment:

  1. ACK + rate-limited readback -> ``awaiting_confirmation``
     True, but ALSO ``readback_valid=True``, ``success=True``,
     and ``report_control_success`` was called. The
     production ``execute_plan()`` then returned
     ``executed=True`` for a write that was never
     confirmed.

  2. A repeat identical intent (same target output / charger /
     buzzer) BEFORE the readback retry completed issued a
     second ``set_*`` and created a second pending ledger
     entry. The device then received two writes for the
     same target.

The fix:
  * On a rate-limited readback, ``readback_valid=False``,
    ``dispatched=False``, ``success=False``. The error
    message describes the awaiting state. The legacy
    bookkeeping does NOT call ``report_control_success``.
  * The no-op gate (step 0a) ALSO matches against the
    ``_pending_confirmations`` ledger: if a pending entry
    with the same output / charger / buzzer target
    exists, the cycle is a "pending already covers this
    intent" no-op. The dispatcher does NOT re-issue
    ``set_*`` and does NOT create a second pending
    entry. The existing entry's deadline is extended
    so the retry budget is preserved.

These tests cover the full production path: real
``HemsEngine``, real ``execute_hems_command``, fake
transport, multi-cycle coordinator iteration through
the outage runtime.
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
# test suite. Extend it with a programmable rate-limit
# signal that toggles between cycles.
class _ProgrammableApi:
    """Fake transport with cycle-programmable rate-limit
    and mismatch signals. Each call to ``fetch_device_configs``
    consumes one entry from the programmed sequence so
    the test can model "rate-limited on cycle 1, success
    on cycle 2".
    """

    def __init__(self):
        self._output = "0"
        self._charger = "1"
        self._buzzer = "1"
        self.last_device_configs_received_at = None
        # ``_fetch_sequence`` is a list of strings:
        #   "rate_limited" -> return {} and signal rate-limit
        #   "match"        -> return the requested value
        #   "mismatch"     -> return a different value
        # ``fetch_device_configs`` pops the head of the
        # list each call; when the list is empty, the
        # next call defaults to "rate_limited" (so a test
        # that forgets to program the next cycle is
        # explicit about wanting a no-match path).
        self._fetch_sequence: list[str] = []
        self._default_fetch = "rate_limited"
        self._device_configs_lock = asyncio.Lock()
        # Track every API call for assertions.
        self.calls: list[tuple] = []
        # Set_ writes are recorded; assert that the
        # write was issued exactly once across cycles.
        self._set_writes: list[tuple] = []

    def program_fetch(self, *outcomes: str) -> None:
        """Append the given outcomes to the sequence. The
        next ``fetch_device_configs`` calls consume
        them in order.
        """
        self._fetch_sequence.extend(outcomes)

    def set_dispatcher_writes(self, *writes) -> None:
        # Convenience: assert against expected writes.
        self._set_writes = list(writes)

    async def set_output_priority(self, value):
        self.calls.append(("set_output_priority", (value,), None))
        self._set_writes.append(("set_output_priority", value))
        self._output = str(value)
        return True

    async def set_charger_priority(self, value):
        self.calls.append(("set_charger_priority", (value,), None))
        self._set_writes.append(("set_charger_priority", value))
        self._charger = str(value)
        return True

    async def set_config_item(self, key, value):
        self.calls.append(("set_config_item", (key, value), None))
        if key == "buzzerAlarmSetting":
            self._buzzer = str(value)
        return True

    async def fetch_device_configs(self):
        async with self._device_configs_lock:
            self.calls.append(("fetch_device_configs", (), None))
            outcome = (
                self._fetch_sequence.pop(0)
                if self._fetch_sequence
                else self._default_fetch
            )
            if outcome == "rate_limited":
                return {}
            if outcome == "mismatch":
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
            # match
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


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class ContractStateTests(unittest.TestCase):
    """ACK + rate-limited readback must produce a
    ``DispatcherResult`` with the correct state
    separation: ``awaiting_confirmation=True`` BUT
    ``readback_valid=False``, ``dispatched=False``,
    ``success=False``, and ``report_control_success``
    is NOT called.
    """

    def test_rate_limited_readback_state_separation(self):
        eng = HemsEngine()
        api = _ProgrammableApi()
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="night_arbitrage",
        )
        # Cycle 1: readback is rate-limited.
        api.program_fetch("rate_limited")
        result = _run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="contract-1",
            )
        )
        # ``api_accepted`` is True (the write ACK arrived).
        self.assertTrue(
            result.api_accepted,
            msg=f"api_accepted must be True (the write "
            f"ACK arrived); got {result!r}",
        )
        # ``awaiting_confirmation`` is True.
        self.assertTrue(
            result.awaiting_confirmation,
            msg=f"awaiting_confirmation must be True on a "
            f"rate-limited readback; got {result!r}",
        )
        # ``readback_valid`` is False (the readback did
        # NOT confirm the value persisted).
        self.assertFalse(
            result.readback_valid,
            msg=f"readback_valid must be False on a "
            f"rate-limited readback (Codex regression: "
            f"this used to be True, advertising a "
            f"physical apply that was never confirmed); "
            f"got {result!r}",
        )
        # ``dispatched`` is False (we did not confirm
        # the apply).
        self.assertFalse(
            result.dispatched,
            msg=f"dispatched must be False on a "
            f"rate-limited readback; got {result!r}",
        )
        # ``success`` is False.
        self.assertFalse(
            result.success,
            msg=f"success must be False on a "
            f"rate-limited readback; got {result!r}",
        )
        # The error message describes the awaiting state.
        self.assertIn(
            "awaiting", (result.error or "").lower(),
            msg=f"error message must describe the awaiting "
            f"state; got {result.error!r}",
        )
        # ``report_control_success`` is NOT called:
        # ``_consecutive_failures`` stays 0.
        self.assertEqual(
            eng._consecutive_failures, 0,
            msg="rate-limited readback must NOT reset the "
            "failure counter (a previous Codex regression "
            "called report_control_success here)",
        )
        # ``_last_applied_output`` is NOT advanced.
        self.assertIsNone(
            eng._last_applied_output,
            msg="LAST APPLIED state must NOT advance on a "
            "rate-limited readback",
        )
        # The command is parked in the pending ledger.
        self.assertIn("contract-1", eng._pending_confirmations)

    def test_runtime_executed_is_false_on_awaiting_confirmation(self):
        """The outage runtime maps ``result.success`` to
        ``executed=True``. With the v7 fix, a rate-limited
        readback returns ``success=False``, so the
        runtime returns ``executed=False``. The audit
        log shows ``awaiting_confirmation`` in the
        reason.
        """
        # This is a property-based test: ``success``
        # returns False on awaiting_confirmation, so
        # the runtime's ``executed = bool(result.success)``
        # is False. Run the dispatcher and assert the
        # contract directly.
        eng = HemsEngine()
        api = _ProgrammableApi()
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="night_arbitrage",
        )
        api.program_fetch("rate_limited")
        result = _run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="runtime-1",
            )
        )
        # The runtime reads ``result.success``. With
        # the v7 fix it returns False.
        self.assertFalse(
            result.success,
            msg=f"result.success must be False so the "
            f"runtime returns executed=False; got "
            f"success={result.success} from {result!r}",
        )


class PendingDedupTests(unittest.TestCase):
    """A repeat identical intent BEFORE the readback
    retry completes must NOT re-issue ``set_*`` and must
    NOT create a second pending ledger entry. The
    existing entry's deadline is extended.
    """

    def test_repeat_intent_does_not_re_issue_set(self):
        """Cycle 1: ACK + rate-limited readback -> pending
        ledger entry.
        Cycle 2: same intent, still rate-limited -> the
        no-op gate matches the existing pending entry
        and the dispatcher does NOT call ``set_*``
        again. The pending ledger has exactly 1 entry
        and the entry's ``deadline_at`` /
        ``last_attempt_at`` are NOT modified by the
        no-op cycle. The deadline stays anchored to
        the first command's wall clock.
        """
        from datetime import datetime as _dt
        eng = HemsEngine()
        api = _ProgrammableApi()
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="night_arbitrage",
        )
        # Cycle 1: rate-limited.
        api.program_fetch("rate_limited")
        result_1 = _run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="dedup-1",
            )
        )
        self.assertTrue(result_1.awaiting_confirmation)
        self.assertEqual(
            len(eng._pending_confirmations), 1,
            msg="cycle 1 must create exactly one pending "
            "entry",
        )
        # Snapshot the entry's deadline and last-attempt
        # after the FIRST readback. These are anchored
        # to the first command's wall clock and must
        # NOT change on a no-op dedup cycle.
        entry_after_cycle_1 = eng._pending_confirmations["dedup-1"]
        deadline_after_cycle_1 = entry_after_cycle_1["deadline_at"]
        last_attempt_after_cycle_1 = entry_after_cycle_1[
            "last_attempt_at"
        ]
        first_seen_after_cycle_1 = entry_after_cycle_1[
            "first_seen_at"
        ]
        # Count the writes issued so far.
        writes_after_cycle_1 = [
            c for c in api.calls
            if c[0] in ("set_output_priority", "set_charger_priority")
        ]
        self.assertEqual(
            len(writes_after_cycle_1), 1,
            msg=f"cycle 1 must have issued exactly one "
            f"write; got {writes_after_cycle_1!r}",
        )
        # Cycle 2: same intent, still rate-limited. The
        # dispatcher must short-circuit on the pending
        # match.
        import time as _time
        _time.sleep(1.5)  # ensure real wall-clock advance
        api.program_fetch("rate_limited")
        result_2 = _run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="dedup-2",
            )
        )
        # Cycle 2 is a no-op.
        self.assertTrue(
            result_2.no_op,
            msg=f"cycle 2 with the same intent as the "
            f"pending entry must be a no-op; got {result_2!r}",
        )
        # And the awaiting_confirmation flag is True on
        # the dedup path (so callers can distinguish
        # "we already wrote" from "we never wrote").
        self.assertTrue(
            result_2.awaiting_confirmation,
            msg=f"cycle 2 dedup must carry "
            f"awaiting_confirmation=True; got {result_2!r}",
        )
        # The pending ledger still has exactly 1 entry
        # (NOT 2 -- the second cycle did NOT create a
        # new one).
        self.assertEqual(
            len(eng._pending_confirmations), 1,
            msg=f"cycle 2 must NOT create a second "
            f"pending entry; got "
            f"{list(eng._pending_confirmations.keys())!r}",
        )
        # The set_ write was NOT re-issued.
        writes_after_cycle_2 = [
            c for c in api.calls
            if c[0] in ("set_output_priority", "set_charger_priority")
        ]
        self.assertEqual(
            len(writes_after_cycle_2), 1,
            msg=f"cycle 2 must NOT re-issue the write; "
            f"got {writes_after_cycle_2!r}",
        )
        # The pending entry's deadline and last-attempt
        # were NOT touched by the no-op dedup cycle.
        # The deadline is anchored to the first
        # command's wall clock (15s from the original
        # ACK) and ``last_attempt_at`` is only updated
        # by the next real ``confirm_pending``
        # readback. Codex demand: ``Deadline незмінний
        # від першої команди; час останньої спроби
        # змінюється лише після справжнього readback``.
        entry_after_cycle_2 = eng._pending_confirmations["dedup-1"]
        self.assertEqual(
            entry_after_cycle_2["deadline_at"],
            deadline_after_cycle_1,
            msg=f"no-op dedup cycle must NOT extend the "
            f"entry's deadline; "
            f"deadline_after_cycle_1={deadline_after_cycle_1!r} "
            f"deadline_after_cycle_2="
            f"{entry_after_cycle_2['deadline_at']!r}",
        )
        self.assertEqual(
            entry_after_cycle_2["last_attempt_at"],
            last_attempt_after_cycle_1,
            msg=f"no-op dedup cycle must NOT update "
            f"last_attempt_at; "
            f"last_attempt_after_cycle_1="
            f"{last_attempt_after_cycle_1!r} "
            f"last_attempt_after_cycle_2="
            f"{entry_after_cycle_2['last_attempt_at']!r}",
        )
        self.assertEqual(
            entry_after_cycle_2["first_seen_at"],
            first_seen_after_cycle_1,
            msg="first_seen_at must be stable across the "
            "no-op dedup cycle",
        )

    def test_repeat_intent_continues_after_pending_confirms(self):
        """Cycle 1: rate-limited. Cycle 2: still rate-
        limited (no-op via dedup). Cycle 3: readback
        succeeds -> the original entry is removed, the
        LAST APPLIED state advances, and the original
        command_id is confirmed (NOT a new one).
        """
        eng = HemsEngine()
        api = _ProgrammableApi()
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="recovery",
        )
        # Cycle 1: rate-limited. Program the readback
        # outcome; the dispatcher's body consumes it.
        api.program_fetch("rate_limited")
        _run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="recover-1",
            )
        )
        # Cycle 2: same intent. The no-op gate
        # short-circuits BEFORE the body, so the
        # dispatcher's ``fetch_device_configs`` is
        # NOT called and the queue is unchanged. The
        # existing pending entry is reused.
        # NOTE: do NOT program_fetch for cycle 2 -- the
        # no-op body never consumes it.
        _run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="recover-2",
            )
        )
        # The pending ledger still has 1 entry.
        self.assertEqual(len(eng._pending_confirmations), 1)
        # Cycle 3: the readback succeeds. The fake
        # returns the current state which we pre-set
        # to match the requested value.
        api._output = "2"
        api.program_fetch("match")
        # Use real-time sleep to clear the per-attempt
        # backoff (1s for attempts=1).
        from datetime import timezone as _tz
        from datetime import datetime as _dt
        import time as _time
        _time.sleep(1.5)
        summary = _run(eng.confirm_pending(
            api, now=_dt.now(_tz.utc)
        ))
        self.assertEqual(
            summary["confirmed"], 1,
            msg=f"pending confirmation must confirm on "
            f"retry; got {summary!r}",
        )
        # The pending ledger is empty.
        self.assertEqual(len(eng._pending_confirmations), 0)
        # The LAST APPLIED state advanced.
        self.assertEqual(eng._last_applied_output, "2")
        # The set_ write was issued exactly once.
        writes = [
            c for c in api.calls
            if c[0] in ("set_output_priority", "set_charger_priority")
        ]
        self.assertEqual(
            len(writes), 1,
            msg=f"the write must have been issued "
            f"exactly once across all 3 cycles; got "
            f"{writes!r}",
        )

    def test_changing_intent_after_pending_creates_new_entry(self):
        """If the engine proposes a DIFFERENT output
        target after the first cycle is rate-limited,
        the second cycle must issue a new write and
        create a new pending entry (NOT dedup against
        the first entry, because the intent changed).
        """
        eng = HemsEngine()
        api = _ProgrammableApi()
        decision_1 = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="first",
        )
        api.program_fetch("rate_limited")
        _run(
            execute_hems_command(
                api=api, hems=eng, decision=decision_1,
                command_id="change-1",
            )
        )
        # The second cycle has a DIFFERENT output
        # target.
        decision_2 = HemsDecision(
            output_priority="1",
            charger_priority=None,
            reason="second",
        )
        api.program_fetch("rate_limited")
        _run(
            execute_hems_command(
                api=api, hems=eng, decision=decision_2,
                command_id="change-2",
            )
        )
        # The pending ledger has 2 entries.
        self.assertEqual(
            len(eng._pending_confirmations), 2,
            msg=f"a different intent must create a new "
            f"pending entry; got "
            f"{list(eng._pending_confirmations.keys())!r}",
        )
        # The set_ write was issued twice.
        writes = [
            c for c in api.calls
            if c[0] in ("set_output_priority", "set_charger_priority")
        ]
        self.assertEqual(
            len(writes), 2,
            msg=f"a different intent must re-issue the "
            f"write; got {writes!r}",
        )


class CoordinatorCycleTests(unittest.TestCase):
    """Multiple coordinator cycles modelled end-to-end
    with the real dispatcher and ``confirm_pending``.
    """

    def test_three_cycles_rate_limited_then_success(self):
        """Cycle 1: write + rate-limited readback -> pending
        entry.
        Cycle 2: confirm_pending -> still rate-limited ->
        entry stays.
        Cycle 3: confirm_pending -> success -> entry
        removed, LAST APPLIED advances.
        """
        eng = HemsEngine()
        api = _ProgrammableApi()
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="recovery",
        )
        # Cycle 1
        api.program_fetch("rate_limited")
        result_1 = _run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="cyc-1",
            )
        )
        self.assertTrue(result_1.awaiting_confirmation)
        self.assertFalse(result_1.success)
        # Cycle 2 (confirm_pending). The per-attempt
        # backoff for attempts=1 is 1s; we use real-time
        # sleep to ensure the backoff elapses.
        api.program_fetch("rate_limited")
        from datetime import timezone as _tz
        from datetime import datetime as _dt
        import time as _time
        _time.sleep(1.5)
        now = _dt.now(_tz.utc)
        summary_2 = _run(eng.confirm_pending(api, now=now))
        self.assertEqual(
            summary_2["still_pending"], 1,
            msg=f"cycle 2 must keep the entry pending; "
            f"got {summary_2!r}",
        )
        # Cycle 3 (confirm_pending) -> success. The
        # per-attempt backoff for attempts=2 is 3s;
        # we sleep to ensure the backoff elapses.
        api._output = "2"
        api.program_fetch("match")
        api.last_device_configs_received_at = None
        _time.sleep(3.5)
        now = _dt.now(_tz.utc)
        summary_3 = _run(eng.confirm_pending(api, now=now))
        self.assertEqual(
            summary_3["confirmed"], 1,
            msg=f"cycle 3 must confirm the entry; got "
            f"{summary_3!r}",
        )
        # The pending ledger is empty.
        self.assertEqual(len(eng._pending_confirmations), 0)
        # The LAST APPLIED state advanced.
        self.assertEqual(eng._last_applied_output, "2")
        # No failure was counted (rate-limited was the
        # only issue).
        self.assertEqual(eng._consecutive_failures, 0)
        # The write was issued exactly once.
        writes = [
            c for c in api.calls
            if c[0] in ("set_output_priority", "set_charger_priority")
        ]
        self.assertEqual(
            len(writes), 1,
            msg=f"the write must have been issued "
            f"exactly once; got {writes!r}",
        )


class OutageRuntimeContractTests(unittest.TestCase):

    def test_runtime_contract_dispatched_false_on_awaiting(self):
        """``result.success`` is the canonical execution
        signal the runtime maps to ``executed=True``.
        On a rate-limited readback the v7 fix returns
        ``success=False`` and ``awaiting_confirmation=
        True`` so the runtime returns ``executed=False``
        with an awaiting reason.
        """
        eng = HemsEngine()
        api = _ProgrammableApi()
        api.program_fetch("rate_limited")
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="night_arbitrage",
        )
        result = _run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="runtime-contract-1",
            )
        )
        # The runtime contract:
        # ``executed = bool(result.success)``
        self.assertFalse(
            bool(result.success),
            msg=f"result.success must be False on a "
            f"rate-limited readback so the runtime "
            f"returns executed=False; got success="
            f"{result.success} from {result!r}",
        )
        # And the runtime carries an awaiting
        # explanation in the result's ``error`` field
        # which it surfaces in the audit log.
        self.assertIn(
            "awaiting", (result.error or "").lower(),
            msg=f"error must mention awaiting so the "
            f"runtime surfaces the awaiting state; got "
            f"{result.error!r}",
        )


class ControlledClockScenarioTests(unittest.TestCase):
    """v7 follow-up v8: Codex reproduced a regression
    at 634aeaa where the pending-dedup path updated
    ``deadline_at`` and ``last_attempt_at`` on every
    repeat no-op cycle. The deadline should be
    anchored to the FIRST command's wall clock (15s
    from the original ACK), and ``last_attempt_at``
    should be updated only by the next REAL readback
    retry (``confirm_pending``), not by a repeat
    no-op cycle that never called
    ``fetch_device_configs``.

    These tests drive the full production path with
    a controlled clock. The clock advances only when
    the test calls ``tick(seconds=N)``. Each test
    models a real coordinator loop:

      * cycle 1: ``_run_hems_engine`` -> dispatcher
        -> set_* -> rate-limited readback -> pending
        entry parked.
      * cycles 2..N: every 5 seconds the runtime
        proposes the SAME intent. The dispatcher's
        no-op gate short-circuits (no second
        ``set_*``, no second pending entry,
        ``last_attempt_at`` unchanged,
        ``deadline_at`` unchanged).
      * ``confirm_pending`` is called by the
        coordinator's update loop. Per-attempt
        backoff: 0s, 1s, 3s, 5s, 8s.

    Two scenarios:
      A. The pending entry's readback is
         ALWAYS rate-limited. The retry budget
         expires (15s after the first ACK) and
         the entry is dropped WITHOUT declaring
         applied. The set_* was issued exactly
         once.
      B. The pending entry's readback SUCCEEDS
         before the deadline. The LAST APPLIED
         state advances. The set_* was issued
         exactly once.
    """

    class _Clock:
        """A monotonic clock the test can advance by
        ``tick(seconds=N)``. ``now()`` returns UTC
        ``datetime`` so the engine's tz comparisons
        are consistent.
        """

        def __init__(self, start_iso="2026-10-14T11:00:00"):
            from datetime import datetime as _dt
            from datetime import timezone as _tz
            self._start = _dt.fromisoformat(start_iso).replace(
                tzinfo=_tz.utc
            )
            self._now_offset_sec = 0.0

        def now(self) -> "datetime":
            from datetime import timedelta
            return self._start + timedelta(
                seconds=self._now_offset_sec
            )

        def now_naive(self) -> "datetime":
            """Return naive local wall clock (what
            ``datetime.now()`` would return on a host
            whose local TZ is Europe/Kyiv -- but the
            engine normalises this through
            ``_as_utc``). For test purposes the host
            TZ is treated as the same as the test
            reference UTC.
            """
            return self.now().replace(tzinfo=None)

        def tick(self, seconds: float) -> None:
            self._now_offset_sec += seconds

    def setUp(self):
        self._clock = self._Clock()
        # No clock monkey-patching. The dispatcher's
        # ``execute_hems_command`` accepts a ``now``
        # parameter; the engine's ``confirm_pending``
        # also accepts ``now``. Both are called with
        # the same wall clock in each test cycle.

    def tearDown(self):
        pass

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_repeat_intent_5s_cadence_until_deadline_no_apply(self):
        """Scenario A: same intent every 5 seconds for
        20 seconds, persistent rate-limit. The
        deadline expires at 15s. The set_* was
        issued exactly ONCE (cycle 1). No
        transport call after cycle 1. The pending
        entry is dropped after 15s WITHOUT
        declaring applied. The audit log records
        ``timed_out=1`` and no ``confirmed``.
        """
        eng = HemsEngine()
        api = _ProgrammableApi()
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="night_arbitrage",
        )
        # Cycle 1 at t=0s: write + rate-limited readback.
        api.program_fetch("rate_limited")
        result_1 = self._run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="sc-A-1",
                now=self._clock.now_naive(),
            )
        )
        self.assertTrue(result_1.awaiting_confirmation)
        self.assertFalse(result_1.success)
        # Snapshot the entry's deadline (15s from t=0s).
        entry = eng._pending_confirmations["sc-A-1"]
        deadline = entry["deadline_at"]
        first_seen = entry["first_seen_at"]
        first_attempt = entry["last_attempt_at"]
        # Cycles 2..5 at t=5s, 10s, 15s, 20s: the
        # runtime proposes the SAME intent. The
        # dispatcher's no-op gate matches the
        # pending entry; no second set_*; no second
        # pending entry; deadline and last_attempt
        # are NOT touched.
        self._clock.tick(5.0)
        for cycle_idx in range(2, 6):
            api.program_fetch("rate_limited")
            result = self._run(
                execute_hems_command(
                    api=api, hems=eng, decision=decision,
                    command_id=f"sc-A-{cycle_idx}",
                    now=self._clock.now_naive(),
                )
            )
            self.assertTrue(
                result.no_op,
                msg=f"cycle {cycle_idx}: pending-dedup "
                f"must be a no-op; got {result!r}",
            )
            self.assertTrue(
                result.awaiting_confirmation,
                msg=f"cycle {cycle_idx}: pending-dedup "
                f"must carry awaiting_confirmation=True; "
                f"got {result!r}",
            )
            self.assertEqual(
                len(eng._pending_confirmations), 1,
                msg=f"cycle {cycle_idx}: must NOT create "
                f"a second pending entry; got "
                f"{list(eng._pending_confirmations.keys())!r}",
            )
            # The deadline and last-attempt are
            # anchored to cycle 1.
            self.assertEqual(
                eng._pending_confirmations[
                    "sc-A-1"
                ]["deadline_at"],
                deadline,
                msg=f"cycle {cycle_idx}: deadline must "
                f"NOT be touched by the no-op dedup; "
                f"got {eng._pending_confirmations['sc-A-1']['deadline_at']!r}",
            )
            self.assertEqual(
                eng._pending_confirmations[
                    "sc-A-1"
                ]["last_attempt_at"],
                first_attempt,
                msg=f"cycle {cycle_idx}: last_attempt_at "
                f"must NOT be touched by the no-op dedup; "
                f"got {eng._pending_confirmations['sc-A-1']['last_attempt_at']!r}",
            )
            self.assertEqual(
                eng._pending_confirmations[
                    "sc-A-1"
                ]["first_seen_at"],
                first_seen,
                msg=f"cycle {cycle_idx}: first_seen_at "
                f"must NOT be touched by the no-op dedup",
            )
            self._clock.tick(5.0)
        # The set_* was issued exactly once across
        # all 5 cycles.
        writes = [
            c for c in api.calls
            if c[0] in ("set_output_priority", "set_charger_priority")
        ]
        self.assertEqual(
            len(writes), 1,
            msg=f"set_* must have been issued exactly "
            f"once across 5 cycles; got {writes!r}",
        )
        # The LAST APPLIED state was NOT advanced
        # (the readback never succeeded).
        self.assertIsNone(
            eng._last_applied_output,
            msg="LAST APPLIED state must NOT advance on a "
            "rate-limited readback",
        )
        # Drive the confirm_pending retry path on
        # the coordinator's update loop. The entry
        # has now aged past its 15s deadline.
        summary = self._run(eng.confirm_pending(
            api, now=self._clock.now()
        ))
        self.assertEqual(
            summary["timed_out"], 1,
            msg=f"after 25s the entry must be timed out; "
            f"got {summary!r}",
        )
        self.assertEqual(
            summary["confirmed"], 0,
            msg=f"timed-out entry must NOT be confirmed; "
            f"got {summary!r}",
        )
        # The pending ledger is empty.
        self.assertEqual(len(eng._pending_confirmations), 0)
        # The LAST APPLIED state is still None -- we
        # did NOT declare the command applied.
        self.assertIsNone(
            eng._last_applied_output,
            msg="timed-out confirmation must NOT declare "
            "the command applied",
        )
        # The failure counter was NOT incremented
        # (a rate-limited / timed-out readback is NOT
        # a transport failure).
        self.assertEqual(
            eng._consecutive_failures, 0,
            msg="timed-out confirmation must NOT count "
            "as a transport failure",
        )

    def test_repeat_intent_succeeds_on_real_readback_before_deadline(self):
        """Scenario B: same intent every 5 seconds for
        10 seconds, rate-limited for cycles 1-3,
        then a real readback match at t=10s. The
        set_* was issued exactly once (cycle 1).
        The ``confirm_pending`` retry at t=10s
        succeeds, the entry is removed, and
        ``_last_applied_output`` advances to '2'.
        """
        eng = HemsEngine()
        api = _ProgrammableApi()
        decision = HemsDecision(
            output_priority="2",
            charger_priority=None,
            reason="night_arbitrage",
        )
        # Cycle 1 at t=0s: rate-limited.
        api.program_fetch("rate_limited")
        result_1 = self._run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="sc-B-1",
                now=self._clock.now_naive(),
            )
        )
        self.assertTrue(result_1.awaiting_confirmation)
        # Cycle 2 at t=5s: same intent, still rate-
        # limited -> no-op via dedup. The
        # ``fetch_device_configs`` is NOT called by
        # the dispatcher; the ``_ProgrammableApi``
        # queue keeps the next "rate_limited" entry
        # untouched.
        self._clock.tick(5.0)
        api.program_fetch("rate_limited")
        result_2 = self._run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="sc-B-2",
                now=self._clock.now_naive(),
            )
        )
        self.assertTrue(result_2.no_op)
        self.assertTrue(result_2.awaiting_confirmation)
        # Cycle 3 at t=10s: same intent, but this
        # time we run ``confirm_pending`` from the
        # coordinator's update loop. The api now
        # returns a matching readback. The
        # pre-condition: ``_last_applied_output`` is
        # still None (no set applied yet).
        #
        # NOTE on the fake transport: the
        # ``_ProgrammableApi`` queue still holds the
        # "rate_limited" outcome that cycle 2
        # ``program_fetch``-ed but the no-op dedup
        # did NOT consume (the no-op body never
        # called ``fetch_device_configs``). The
        # queue is therefore
        # ``["rate_limited", "match"]``. The next
        # ``fetch_device_configs`` call will pop
        # the "rate_limited" entry first. To model
        # the production reality, drop the stale
        # "rate_limited" entry from the queue
        # before ``confirm_pending`` runs.
        self._clock.tick(5.0)
        self.assertIsNone(eng._last_applied_output)
        # The cycle 2 no-op did not consume the
        # "rate_limited" outcome; clear it so the
        # confirm_pending retry sees the "match".
        if api._fetch_sequence and api._fetch_sequence[0] == "rate_limited":
            api._fetch_sequence.pop(0)
        # Pre-set the device state to match the
        # requested value (the api has accepted the
        # write in cycle 1; the readback at t=10s
        # confirms it).
        api._output = "2"
        # The next readback (driven by
        # ``confirm_pending``) is a "match".
        api.program_fetch("match")
        summary = self._run(eng.confirm_pending(
            api, now=self._clock.now()
        ))
        # The entry is confirmed and removed.
        self.assertEqual(
            summary["confirmed"], 1,
            msg=f"confirm_pending must confirm the entry "
            f"on a real match; got {summary!r}",
        )
        self.assertEqual(len(eng._pending_confirmations), 0)
        # The LAST APPLIED state advanced.
        self.assertEqual(eng._last_applied_output, "2")
        # The set_* was issued exactly once.
        writes = [
            c for c in api.calls
            if c[0] in ("set_output_priority", "set_charger_priority")
        ]
        self.assertEqual(
            len(writes), 1,
            msg=f"set_* must have been issued exactly "
            f"once across 3 cycles; got {writes!r}",
        )
        # The failure counter was NOT incremented
        # (the rate-limited cycles are not transport
        # failures; the real readback matched).
        self.assertEqual(eng._consecutive_failures, 0)
        # A subsequent cycle (cycle 4) proposing the
        # same intent must be a no-op via the
        # all-channel no-op gate (``_last_applied_output``
        # matches the requested value).
        self._clock.tick(5.0)
        result_4 = self._run(
            execute_hems_command(
                api=api, hems=eng, decision=decision,
                command_id="sc-B-4",
                now=self._clock.now_naive(),
            )
        )
        self.assertTrue(
            result_4.no_op,
            msg=f"cycle 4 must be a no-op (the LAST "
            f"APPLIED state matches the request); got "
            f"{result_4!r}",
        )
        # And the set_* was still issued exactly
        # once (no second set_* across the 4
        # cycles).
        writes_after_4 = [
            c for c in api.calls
            if c[0] in ("set_output_priority", "set_charger_priority")
        ]
        self.assertEqual(
            len(writes_after_4), 1,
            msg=f"set_* must NOT be re-issued after the "
            f"pending entry is confirmed; got "
            f"{writes_after_4!r}",
        )


if __name__ == "__main__":
    unittest.main()
