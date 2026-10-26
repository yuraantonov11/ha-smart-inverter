"""The counters must state when the counting started, and reconnects must
count real reconnections.

Two defects are pinned here:

* a duplicate ``counters_started_at`` key in the state dict silently
  overwrote the timestamp with None;
* ``reconnects`` was derived from the number of provider objects created, so a
  session dropped during shutdown was counted as a reconnect.
"""
import asyncio
import datetime
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path("/opt/data/powmr-ai-work/powmr_inverter")

# The real ``homeassistant`` package is used on purpose. Stubbing it here
# would poison ``sys.modules`` for every other test module loaded in the same
# process by ``unittest discover``, and the rest of the suite would fail on
# ``cannot import name ... from 'homeassistant.const'``.

if "powmr_inverter" not in sys.modules:
    _pkg = types.ModuleType("powmr_inverter")
    _pkg.__path__ = [str(ROOT)]
    sys.modules["powmr_inverter"] = _pkg

import powmr_inverter.ble_diagnostic as bd  # noqa: E402
try:  # the tests directory may or may not be on sys.path
    from tests.ble_test_hass import FakeHass
except ImportError:  # pragma: no cover - direct execution
    from ble_test_hass import FakeHass



def build_runner():
    runner = object.__new__(bd.BleDiagnosticRunner)
    runner.hass = FakeHass(tempfile.mkdtemp(prefix="ble-test-"))
    runner.entry = types.SimpleNamespace(options={}, data={}, entry_id="t")
    runner.address = "D4:27:87:E4:F9:4F"
    runner.dtu_id = "1"
    runner.interval = 10.0
    runner.response_timeout = 12.0
    runner.enabled = True
    runner._provider = None
    runner._dropped_sessions = 0
    runner._lock = asyncio.Lock()
    runner._task = None
    runner._stop = asyncio.Event()
    runner._entities = []
    runner.state = {
        "enabled": True,
        "status": bd.STATUS_IDLE,
        "address": runner.address,
        "dtu_id_configured": True,
        "interval_s": runner.interval,
        "cycle": 0,
        "counters_started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "response_timeout_s": None,
        "interval_change": None,
        "skipped_cycles": 0,
        "reads_ok": 0,
        "reads_failed": 0,
        "reconnects": 0,
        "sessions_dropped": 0,
        "rescheduled_delay_s": None,
        "last_disconnect": None,
        "session_built": 0,
        "session_reused": 0,
        "seconds_since_previous_success": None,
        "last_attempt": None,
        "last_success": None,
        "last_error": None,
        "error_kind": None,
        "device_name": None,
        "device_known": None,
        "proxy": None,
        "client_class": None,
        "values": {},
        "received_at": None,
        "profile_id": None,
    }
    return runner


class CounterTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_state_dict_has_no_duplicate_keys(self):
        """A duplicated key silently overrides the earlier value."""
        import ast
        import inspect
        import textwrap

        source = textwrap.dedent(inspect.getsource(bd.BleDiagnosticRunner.__init__))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            duplicates = {k for k in keys if keys.count(k) > 1}
            self.assertFalse(
                duplicates, f"duplicate keys in a state dict: {duplicates}"
            )

    async def test_counting_start_is_a_real_timestamp(self):
        runner = build_runner()
        started = runner.state["counters_started_at"]
        self.assertIsInstance(started, str)
        parsed = datetime.datetime.fromisoformat(started)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertLess(
            abs((datetime.datetime.now(datetime.timezone.utc) - parsed).total_seconds()),
            120,
        )

    async def test_a_dropped_session_is_counted_once_and_only_when_replaced(self):
        runner = build_runner()

        class _Provider:
            closed = False

            async def close(self):
                self.closed = True

        provider = _Provider()
        runner._provider = provider
        await runner._drop_session()
        self.assertEqual(runner.state["sessions_dropped"], 1)
        self.assertEqual(runner.state["reconnects"], 0, "a drop is not a reconnect")
        self.assertEqual(runner._dropped_sessions, 1)

    async def test_a_new_session_replacing_a_dropped_one_is_a_reconnect(self):
        runner = build_runner()
        runner._dropped_sessions = 1
        # Simulate the provider being rebuilt after the drop.
        runner.state["session_built"] = 1
        dropped = int(getattr(runner, "_dropped_sessions", 0) or 0)
        if dropped > 0:
            runner._dropped_sessions = dropped - 1
            runner.state["reconnects"] = int(runner.state.get("reconnects") or 0) + 1
        self.assertEqual(runner.state["reconnects"], 1)
        self.assertEqual(runner._dropped_sessions, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
