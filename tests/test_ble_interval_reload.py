"""The interval must be changeable without restarting HA, and the change must
not cost a cycle or a BLE connect.

Covers three contracts that were all broken at some point:

1. A live interval change applies without a restart, and the session survives.
2. The time of the real last attempt is preserved: after a change the cycle
   waits for ``last_attempt + interval`` instead of forgetting the stamp or
   letting the provider reject the next read.
3. A locally skipped cycle is recorded as a skip -- no error, no recovery.

The integration is loaded under its real package name so relative imports
resolve to the same module objects production uses. Loading it as a bare module
gave it no package context, produced a second copy of the provider module and
forced a class-name comparison that production does not need.
"""
import asyncio
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
import powmr_inverter.telemetry_sources.rwb1_ble as _ble  # noqa: E402

# Proves the runner and the provider share ONE exception type.
assert bd.is_rate_limited(_ble.Rwb1BleRateLimited("read skipped")), (
    "runner and provider must share one exception type"
)

#: Shared clock the fake provider reads, so rate limits are deterministic.
NOW = [1000.0]


class _Obs:
    def __init__(self, value):
        self.value = value


class _Frame:
    profile_id = "rwb1-ble-fee7-21w-le-v1"
    fields = {
        "grid_voltage": _Obs(235.0),
        "battery_voltage": _Obs(53.1),
        "battery_soc": _Obs(88.0),
    }
    received_at = None


class _Provider:
    """Records the interval/budget the runner set.

    Constructor arguments mirror ``_build_provider``, and ``_last_attempt_at``
    is stamped on every read so the rescheduler has a real anchor.
    """

    def __init__(self, min_interval=None, response_timeout=None):
        self.closed = False
        self.reads = 0
        self.fail_next_read = None
        self._min_interval = min_interval
        self._response_timeout = response_timeout
        self._last_attempt_at = None
        self._clock = lambda: NOW[0]

    async def read(self):
        self.reads += 1
        self._last_attempt_at = self._clock()
        if self.fail_next_read is not None:
            err = self.fail_next_read
            self.fail_next_read = None
            raise err
        return _Frame()

    async def close(self):
        self.closed = True


async def _noop(*a, **k):
    return None


def hass_for(tmpdir):
    return types.SimpleNamespace(
        config=types.SimpleNamespace(config_dir=str(tmpdir))
    )


def build(hass, tmpdir):
    """A runner wired like production except for the two offline seams."""
    entry = types.SimpleNamespace(options={}, data={}, entry_id="t")
    runner = object.__new__(bd.BleDiagnosticRunner)
    runner.hass = hass
    runner.entry = entry
    runner.address = "D4:27:87:E4:F9:4F"
    runner.dtu_id = "50659432846709074818"
    runner.interval = 10.0
    runner.response_timeout = 12.0
    runner.enabled = True
    runner._provider = None
    runner._dropped_sessions = 0
    runner._lock = asyncio.Lock()
    runner._task = None
    runner._stop = asyncio.Event()
    runner.state = {
        "cycle": 0,
        "session_built": 0,
        "session_reused": 0,
        "proxy": {},
        "seconds_since_previous_success": None,
        "response_timeout_s": None,
        "interval_s": None,
        "interval_change": None,
        "skipped_cycles": 0,
        "rescheduled_delay_s": None,
        "last_disconnect": None,
    }
    runner._resolve_device = lambda: object()
    runner._proxy_info = lambda: {}
    runner._push = _noop
    # Drive the wait from the fake clock so tests never sleep for real.
    runner._clock = lambda: NOW[0]

    async def _fake_sleep(seconds):
        NOW[0] += seconds
        await asyncio.sleep(0)

    runner._sleeper = _fake_sleep
    provider = _Provider(
        min_interval=runner.interval, response_timeout=runner.response_timeout
    )
    runner._build_provider = lambda device, client_class: provider
    bd._resolve_client_class = lambda: type("BleakClientWithServiceCache", (), {})
    return runner, provider


class LiveIntervalChangeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        NOW[0] = 1000.0

    async def test_interval_and_budget_change_without_a_restart(self):
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / bd.CONF_FILENAME
            conf.write_text("enabled=1\ninterval=10\nresponse_timeout=12\n")
            runner, provider = build(hass_for(td), td)

            state = await runner.read_once()
            self.assertIsNone(state["last_error"], state.get("last_error"))
            self.assertEqual(runner.interval, 10.0)
            self.assertEqual(provider._min_interval, 10.0)

            conf.write_text("enabled=1\ninterval=5\nresponse_timeout=8\n")

            state = await runner.read_once()
            self.assertIsNone(state["last_error"], state.get("last_error"))
            self.assertEqual(runner.interval, 5.0, "interval did not reload")
            self.assertEqual(runner.response_timeout, 8.0, "budget did not reload")
            self.assertEqual(provider._min_interval, 5.0)
            self.assertEqual(provider._response_timeout, 8.0)
            self.assertEqual(provider.reads, 2)
            self.assertFalse(provider.closed, "session dropped for a config change")

    async def test_a_conf_change_never_reopens_the_session(self):
        """A config tweak must not cost a BLE connect -- that was the whole bug."""
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / bd.CONF_FILENAME
            conf.write_text("enabled=1\ninterval=10\nresponse_timeout=12\n")
            runner, provider = build(hass_for(td), td)
            await runner.read_once()
            for value in (9, 8, 7, 6, 5):
                conf.write_text(
                    f"enabled=1\ninterval={value}\nresponse_timeout=12\n"
                )
                state = await runner.read_once()
                self.assertIsNone(state["last_error"], state.get("last_error"))
                NOW[0] += value
            self.assertFalse(provider.closed)
            self.assertEqual(provider.reads, 6)
            self.assertEqual(runner.state["session_built"], 1)
            self.assertEqual(runner.interval, 5.0)

    async def test_a_broken_conf_does_not_corrupt_the_running_interval(self):
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / bd.CONF_FILENAME
            conf.write_text("enabled=1\ninterval=10\nresponse_timeout=12\n")
            runner, provider = build(hass_for(td), td)
            await runner.read_once()
            conf.write_text(
                "enabled=1\ninterval=not-a-number\nresponse_timeout=12\n"
            )
            state = await runner.read_once()
            self.assertIsNone(state["last_error"])
            self.assertEqual(runner.interval, 10.0, "garbage conf changed the interval")

    async def test_interval_change_waits_out_the_previous_attempt(self):
        """The real last-attempt time is kept and the cycle waits it out.

        Forgetting the stamp let a read through early; keeping it against the
        new, larger floor made the provider reject the next read. Both cost a
        cycle and produced a phantom error.
        """
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / bd.CONF_FILENAME
            conf.write_text("enabled=1\ninterval=5\nresponse_timeout=12\n")
            runner, provider = build(hass_for(td), td)
            provider._min_interval = 5.0

            state = await runner.read_once()
            self.assertIsNone(state["last_error"])
            self.assertEqual(state["skipped_cycles"], 0)

            last = provider._last_attempt_at
            self.assertIsNotNone(last, "the attempt time must be recorded")

            # Raise the interval and put the clock just after that attempt, so
            # the new window has not elapsed yet.
            conf.write_text("enabled=1\ninterval=30\nresponse_timeout=12\n")
            NOW[0] = last + 1.0

            state = await runner.read_once()
            self.assertEqual(runner.interval, 30.0)
            self.assertIsNone(state["last_error"], state.get("last_error"))
            self.assertEqual(state["skipped_cycles"], 0, "the change wasted a cycle")
            self.assertEqual(state["status"], bd.STATUS_OK)
            self.assertGreaterEqual(
                state["rescheduled_delay_s"],
                28.0,
                "the cycle did not wait out the window",
            )
            self.assertFalse(provider.closed, "a reschedule dropped the session")

    async def test_a_shortened_interval_is_not_delayed(self):
        """Lowering the interval must not hold the cycle back."""
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / bd.CONF_FILENAME
            conf.write_text("enabled=1\ninterval=60\nresponse_timeout=12\n")
            runner, provider = build(hass_for(td), td)
            provider._min_interval = 60.0
            await runner.read_once()

            conf.write_text("enabled=1\ninterval=5\nresponse_timeout=12\n")
            NOW[0] += 2.0  # only 2 s since the last attempt

            state = await runner.read_once()
            self.assertIsNone(state["last_error"], state.get("last_error"))
            self.assertEqual(state["status"], bd.STATUS_OK)
            # The *new* interval still governs the gap from the last attempt:
            # 5 s requested, 2 s elapsed, so 3 s remained and were waited out.
            self.assertAlmostEqual(state["rescheduled_delay_s"], 3.0, places=3)
            self.assertEqual(provider.reads, 2)
            self.assertEqual(state["skipped_cycles"], 0)
            self.assertGreaterEqual(NOW[0], 1005.0, "the wait did not happen")


class RateLimitSkipTests(unittest.IsolatedAsyncioTestCase):
    """A locally skipped cycle is not a device failure and starts no recovery."""

    def setUp(self):
        NOW[0] = 2000.0

    async def test_rate_limited_read_is_recorded_as_skipped_not_failed(self):
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / bd.CONF_FILENAME
            conf.write_text("enabled=1\ninterval=10\nresponse_timeout=12\n")
            runner, provider = build(hass_for(td), td)

            await runner.read_once()
            provider.fail_next_read = _ble.Rwb1BleRateLimited(
                "read skipped: minimum interval of 10s not elapsed"
            )
            state = await runner.read_once()

            self.assertEqual(state["status"], bd.STATUS_SKIPPED)
            self.assertEqual(state["skipped_cycles"], 1)
            self.assertIsNone(state["last_error"])
            self.assertFalse(provider.closed, "a skip must not drop the session")
            self.assertIsNotNone(runner._provider)

    async def test_a_real_device_timeout_is_not_mistaken_for_a_skip(self):
        """The skip path must not swallow genuine device failures."""
        with tempfile.TemporaryDirectory() as td:
            conf = Path(td) / bd.CONF_FILENAME
            conf.write_text("enabled=1\ninterval=10\nresponse_timeout=12\n")
            runner, provider = build(hass_for(td), td)

            await runner.read_once()
            provider.fail_next_read = _ble.Rwb1BleTimeout(
                "no complete response before the deadline"
            )
            state = await runner.read_once()

            self.assertNotEqual(state["status"], bd.STATUS_SKIPPED)
            self.assertEqual(state["skipped_cycles"], 0)
            self.assertIsNotNone(state["last_error"])
            self.assertIn("no complete response", state["last_error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)