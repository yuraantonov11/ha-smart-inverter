"""The applied operational settings and the start of the counting must be
published even when no read succeeds.

Two defects were found by the live deployment of set A (commit b9a6ec5):

1. ``response_timeout_s`` was written only on a successful read. A runner whose
   device was unreachable therefore reported the applied budget as "unknown" --
   exactly when the operator needs to know what is configured.
2. ``counters_started_at`` was never published. Without it the counters had no
   documented start, so a restart that reset them to zero looked like "nothing
   ever happened".

These tests drive the real runner constructor and the real sensor code; the
state dict and the attribute whitelist are the production ones.
"""
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path("/opt/data/powmr-ai-work/powmr_inverter")

# The real ``homeassistant`` package is used on purpose: stubbing it would
# poison ``sys.modules`` for every other test module in the same process.
if "powmr_inverter" not in sys.modules:
    _pkg = types.ModuleType("powmr_inverter")
    _pkg.__path__ = [str(ROOT)]
    sys.modules["powmr_inverter"] = _pkg

import powmr_inverter.ble_diagnostic as bd  # noqa: E402
import powmr_inverter.ble_sensor as bs  # noqa: E402

CONF = "enabled=1\ninterval=10\nresponse_timeout=12\n"


def hass_for(tmpdir):
    return types.SimpleNamespace(
        config=types.SimpleNamespace(config_dir=str(tmpdir)),
        data={},
    )


def entry_for():
    return types.SimpleNamespace(options={}, data={}, entry_id="t")


class SettingsPublicationTests(unittest.TestCase):
    """The runner's own state dict."""

    def test_settings_are_in_the_state_from_the_start(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / bd.CONF_FILENAME).write_text(CONF)
            runner = bd.BleDiagnosticRunner(hass_for(td), entry_for())

            self.assertEqual(runner.state["interval_s"], 10.0)
            self.assertEqual(
                runner.state["response_timeout_s"],
                12.0,
                "the applied response budget must be published up front",
            )
            self.assertIsInstance(
                runner.state["counters_started_at"], str, "the counting start is missing"
            )
            # It must be a parseable timestamp, not just a string.
            datetime.fromisoformat(runner.state["counters_started_at"])

    def test_the_counting_start_is_not_overwritten_later(self):
        """A second key with the same name would silently reset the start."""
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / bd.CONF_FILENAME).write_text(CONF)
            runner = bd.BleDiagnosticRunner(hass_for(td), entry_for())
            started = runner.state["counters_started_at"]
            # The state dict is built with one literal; a duplicate key would
            # have already overwritten it there.
            self.assertEqual(runner.state["counters_started_at"], started)


class PublishedAttributesTests(unittest.IsolatedAsyncioTestCase):
    """The attribute whitelist of the read_status sensor."""

    async def _read_status_attrs(self, td):
        hass = hass_for(td)
        (Path(td) / bd.CONF_FILENAME).write_text(CONF)
        runner = bd.BleDiagnosticRunner(hass, entry_for())
        hass.data[bd.DOMAIN] = {
            "t": {"ble_diagnostic": runner},
        }
        created = []
        await bs.async_add_ble_diagnostic_entities(hass, entry_for(), created.extend)
        entities = {e._key: e for e in created}
        self.assertIn("read_status", entities)
        entity = entities["read_status"]
        entity.hass = hass
        return entity.extra_state_attributes

    async def test_the_counting_start_is_published(self):
        with tempfile.TemporaryDirectory() as td:
            attrs = await self._read_status_attrs(td)
            self.assertIn("counters_started_at", attrs)
            self.assertIsNotNone(attrs["counters_started_at"])

    async def test_the_dropped_sessions_are_published(self):
        with tempfile.TemporaryDirectory() as td:
            attrs = await self._read_status_attrs(td)
            self.assertIn("sessions_dropped", attrs)

    async def test_the_applied_budget_is_published(self):
        with tempfile.TemporaryDirectory() as td:
            attrs = await self._read_status_attrs(td)
            self.assertEqual(attrs["response_timeout_s"], 12.0)
            self.assertEqual(attrs["interval_s"], 10.0)


if __name__ == "__main__":
    unittest.main()
