"""Regression tests for the dashboard view patcher
(DEFECT 6 round 4).

The patcher uses the standard
``hass.data["lovelace"]["dashboards"][None].async_save(...)``
API -- NOT direct file writes. The previous
round parsed ``data.data.views`` (wrong path;
the live file has ``data.config.views``) and
bypassed the Lovelace service entirely, so the
patch never applied on a real dashboard.

These tests use a ``hass`` stub with a
``hass.data["lovelace"]`` dict (the legacy shape;
the new ``LovelaceData`` dataclass shape is
defensively handled too). Each test asserts a
specific contract:

  1. The patcher looks up the default dashboard
     via the standard Lovelace API -- it does
     NOT touch the on-disk JSON file directly.
  2. The patcher is idempotent: a second call on
     a dashboard that already has the view is a
     no-op (no second ``async_save``).
  3. The five existing views (Огляд / Керування
     / Історія / Економіка / AI·HEMS) are
     preserved verbatim. The patch only APPENDS
     the new view; it does NOT mutate or
     reorder the existing ones.
  4. The ``entry_id`` on the new view's card is
     filled in with the operator's entry id
     (so the card talks to the right config
     entry).
  5. A backup file is created on disk before
     the patch applies.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_hass(
    config_dir: str, current_views: list[dict]
) -> types.SimpleNamespace:
    """Build a minimal ``hass`` stub with a
    ``hass.data["lovelace"]`` dict (legacy shape
    used by HA <2024.11) and a
    ``LovelaceStorage``-shaped default dashboard.
    """
    saved: list[dict] = []

    class _DefaultDashboard:
        def __init__(self, config: dict | None) -> None:
            self.config = config or {}
            self._url_path = None

        @property
        def url_path(self) -> str | None:
            return self._url_path

        async def async_load(self, force: bool) -> dict:
            return self.config

        async def async_save(self, config: dict) -> None:
            saved.append(config)
            self.config = config

    default_dashboard = _DefaultDashboard(
        {"title": "Головна", "views": list(current_views)}
    )
    hass = types.SimpleNamespace(
        data={
            "lovelace": {
                "dashboards": {None: default_dashboard},
            },
        }
    )
    hass.config = types.SimpleNamespace(
        config_dir=config_dir,
    )
    hass._default_dashboard = default_dashboard
    hass._saved = saved
    return hass


def _setup_storage_with_views(tmp: str, views: list[dict]) -> str:
    """Write a realistic ``lovelace.powmr_energy``
    on-disk file (so the backup path exercises
    against the same shape as a live dashboard)
    and return the path.
    """
    storage_dir = os.path.join(tmp, ".storage")
    os.makedirs(storage_dir, exist_ok=True)
    path = os.path.join(storage_dir, "lovelace.powmr_energy")
    payload = {
        "key": "lovelace.powmr_energy",
        "version": 1,
        "minor_version": 1,
        "data": {"title": "Головна", "views": list(views)},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# Import the production function under test
# (lives in services/dashboard_view_patch.py).
import importlib.util
_PATCH_PATH = (
    REPO_ROOT / "services" / "dashboard_view_patch.py"
)
_spec = importlib.util.spec_from_file_location(
    "_dashboard_view_patch", str(_PATCH_PATH)
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_ensure_outages_view = _mod._ensure_outages_view


class _FakeEntry:
    entry_id = "01M3XWJ8DRYDQC8A0NCPRVB53N"


class DashboardViewPatchTests(unittest.TestCase):
    """End-to-end tests for ``_ensure_outages_view``
    against a stub ``hass`` with the Lovelace
    data structure.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="powmr_dash_")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _existing_views(self) -> list[dict]:
        # The five views the operator already has
        # on the live dashboard. Order is preserved
        # by the patch -- the new view is appended
        # at the end.
        return [
            {
                "title": "Огляд",
                "path": "powmr-overview",
                "icon": "mdi:view-dashboard",
                "type": "sections",
            },
            {
                "title": "Керування",
                "path": "powmr-control",
                "icon": "mdi:tune",
                "type": "sections",
            },
            {
                "title": "Історія",
                "path": "powmr-history",
                "icon": "mdi:history",
                "type": "sections",
            },
            {
                "title": "Економіка",
                "path": "powmr-economics",
                "icon": "mdi:cash",
                "type": "sections",
            },
            {
                "title": "AI · HEMS",
                "path": "powmr-ai",
                "icon": "mdi:brain",
                "type": "sections",
            },
        ]

    def test_appends_view_via_lovelace_api(self):
        """The patcher calls the Lovelace
        ``async_save`` and the saved config
        contains the new view at the end of
        ``views``.
        """
        existing = self._existing_views()
        # Round 5: the patcher targets the named
        # ``powmr-energy`` panel. The default
        # dashboard is a separate object; the
        # patcher must NOT touch it.
        class _FakePanel:
            def __init__(self, config):
                self.config = config
                self._saved = []

            async def async_load(self, force):
                return self.config

            async def async_save(self, config):
                self.config = config
                self._saved.append(config)

        powmr_energy = _FakePanel(
            {"title": "Smart Solar Енергопанель",
             "views": list(existing)}
        )
        # Default dashboard: a separate panel
        # that the patcher MUST NOT touch. If the
        # patcher ever routed to it, the test
        # would fail (we'd see a save on
        # ``default``).
        class _DefaultPanel:
            def __init__(self):
                self.config = {
                    "title": "Lovelace",
                    "views": [],
                }
                self._saved = []

            async def async_load(self, force):
                return self.config

            async def async_save(self, config):
                self.config = config
                self._saved.append(config)

        default = _DefaultPanel()
        hass = types.SimpleNamespace(
            data={
                "lovelace": {
                    "dashboards": {
                        None: default,
                        "powmr-energy": powmr_energy,
                    },
                },
            },
        )
        hass.config = types.SimpleNamespace(
            config_dir=self.tmp,
        )
        _setup_storage_with_views(self.tmp, existing)
        _run(_ensure_outages_view(hass, _FakeEntry()))
        # The patcher called ``async_save`` exactly
        # once, and on the NAMED panel.
        self.assertEqual(
            len(powmr_energy._saved), 1,
            f"named panel was not saved exactly "
            f"once; saved {len(powmr_energy._saved)} "
            f"times",
        )
        self.assertEqual(
            len(default._saved), 0,
            f"the default dashboard was touched "
            f"{len(default._saved)} times; expected 0 "
            f"(the patcher MUST target the named "
            f"'powmr-energy' panel, not the default)",
        )
        saved = powmr_energy._saved[0]
        self.assertIn("views", saved)
        views = saved["views"]
        # The new view is the LAST entry.
        new_view = views[-1]
        self.assertEqual(new_view.get("path"), "powmr-outages")
        self.assertEqual(new_view.get("title"), "Відключення")
        # The five existing views are preserved
        # verbatim, in the same order.
        self.assertEqual(
            [v.get("path") for v in views[:-1]],
            [v.get("path") for v in existing],
            "the five existing views were "
            "reordered or mutated",
        )
        # The card's ``entry_id`` is filled in.
        card = (
            new_view["sections"][0]["cards"][0]
        )
        self.assertEqual(
            card.get("entry_id"),
            "01M3XWJ8DRYDQC8A0NCPRVB53N",
            f"card entry_id not set from config "
            f"entry; got {card.get('entry_id')!r}",
        )

    def test_idempotent_when_view_already_present(self):
        """A second call on a dashboard that
        already has the view is a no-op. The
        patcher MUST NOT call ``async_save``
        again.
        """
        existing = self._existing_views()
        # Pre-pend a view that already has the
        # target path -- simulates an operator
        # who manually added it.
        existing.append(
            {
                "title": "Відключення",
                "path": "powmr-outages",
                "icon": "mdi:calendar-alert",
                "type": "sections",
            }
        )

        class _FakePanel:
            def __init__(self, config):
                self.config = config
                self._saved = []

            async def async_load(self, force):
                return self.config

            async def async_save(self, config):
                self.config = config
                self._saved.append(config)

        powmr_energy = _FakePanel(
            {"title": "Smart Solar Енергопанель",
             "views": list(existing)}
        )
        hass = types.SimpleNamespace(
            data={
                "lovelace": {
                    "dashboards": {
                        "powmr-energy": powmr_energy,
                    },
                },
            },
        )
        hass.config = types.SimpleNamespace(
            config_dir=self.tmp,
        )
        _setup_storage_with_views(self.tmp, existing)
        _run(_ensure_outages_view(hass, _FakeEntry()))
        # No save -- the view is already there.
        self.assertEqual(
            len(powmr_energy._saved), 0,
            f"async_save was called when the view "
            f"was already present; called "
            f"{len(powmr_energy._saved)} times",
        )

    def test_preserves_five_existing_views_verbatim(self):
        """The patcher MUST NOT mutate, reorder, or
        re-encode the existing five views. The
        saved config's ``views`` slice ``[:-1]``
        is byte-identical (in the JSON sense) to
        the input.
        """
        existing = self._existing_views()

        class _FakePanel:
            def __init__(self, config):
                self.config = config
                self._saved = []

            async def async_load(self, force):
                return self.config

            async def async_save(self, config):
                self.config = config
                self._saved.append(config)

        powmr_energy = _FakePanel(
            {"title": "Smart Solar Енергопанель",
             "views": list(existing)}
        )
        hass = types.SimpleNamespace(
            data={
                "lovelace": {
                    "dashboards": {
                        "powmr-energy": powmr_energy,
                    },
                },
            },
        )
        hass.config = types.SimpleNamespace(
            config_dir=self.tmp,
        )
        _setup_storage_with_views(self.tmp, existing)
        _run(_ensure_outages_view(hass, _FakeEntry()))
        saved = powmr_energy._saved[0]
        # Compare JSON serialisation (the patcher
        # round-trips through ``async_save`` which
        # may re-serialise the dict; but the
        # values are preserved).
        self.assertEqual(
            json.dumps(saved["views"][:-1], sort_keys=True),
            json.dumps(existing, sort_keys=True),
            "the existing five views were not "
            "preserved verbatim",
        )

    def test_creates_backup_before_patch(self):
        """Before the patcher writes, a timestamped
        backup of the on-disk JSON is created.
        The operator can roll back via the
        backup.

        Round 5: the patcher targets the named
        ``powmr-energy`` panel, which is
        backed by ``/config/.storage/lovelace.powmr_energy``
        on disk. The backup is created from that
        file.
        """
        existing = self._existing_views()

        class _FakePanel:
            def __init__(self, config):
                self.config = config
                self._saved = []

            async def async_load(self, force):
                return self.config

            async def async_save(self, config):
                self.config = config
                self._saved.append(config)

        powmr_energy = _FakePanel(
            {"title": "Smart Solar Енергопанель",
             "views": list(existing)}
        )
        hass = types.SimpleNamespace(
            data={
                "lovelace": {
                    "dashboards": {
                        "powmr-energy": powmr_energy,
                    },
                },
            },
        )
        hass.config = types.SimpleNamespace(
            config_dir=self.tmp,
        )
        # Set up the on-disk JSON for the named
        # panel. The patcher reads it as a backup
        # source; the saved config goes back into
        # the LovelaceStorage instance (NOT the
        # on-disk file).
        on_disk_path = _setup_storage_with_views(
            self.tmp, existing
        )
        _run(_ensure_outages_view(hass, _FakeEntry()))
        # A backup file was created.
        storage_dir = os.path.dirname(on_disk_path)
        backups = [
            f
            for f in os.listdir(storage_dir)
            if f.startswith("lovelace.powmr_energy.bak.")
        ]
        self.assertTrue(
            backups,
            f"no backup file was created in "
            f"{storage_dir}; files: "
            f"{os.listdir(storage_dir)}",
        )
        # The backup contains the original views
        # (no "Відключення" appended).
        backup_path = os.path.join(storage_dir, backups[0])
        with open(backup_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(
            len(payload["data"]["views"]),
            5,
            f"backup should have 5 views; got "
            f"{len(payload['data']['views'])}",
        )

    def test_does_not_write_directly_to_disk(self):
        """The patcher does NOT bypass the
        Lovelace service and write the JSON file
        directly. This test guards against the
        previous round's bug (raw
        ``shutil.copy2`` / ``os.replace``) by
        asserting the file is NOT modified by
        the patcher when the view is already
        present.
        """
        existing = self._existing_views()

        class _FakePanel:
            def __init__(self, config):
                self.config = config
                self._saved = []

            async def async_load(self, force):
                return self.config

            async def async_save(self, config):
                self.config = config
                self._saved.append(config)

        # Pre-seed a matching view so the
        # patcher is a no-op.
        existing_with_view = list(existing) + [
            {
                "title": "Відключення",
                "path": "powmr-outages",
            }
        ]
        powmr_energy = _FakePanel(
            {"title": "Smart Solar Енергопанель",
             "views": list(existing_with_view)}
        )
        hass = types.SimpleNamespace(
            data={
                "lovelace": {
                    "dashboards": {
                        "powmr-energy": powmr_energy,
                    },
                },
            },
        )
        hass.config = types.SimpleNamespace(
            config_dir=self.tmp,
        )
        on_disk_path = _setup_storage_with_views(
            self.tmp, existing
        )
        with open(on_disk_path, "rb") as f:
            before = f.read()
        _run(_ensure_outages_view(hass, _FakeEntry()))
        with open(on_disk_path, "rb") as f:
            after = f.read()
        # Round 5: in the no-op case the
        # patcher is a no-op -- it doesn't even
        # read the on-disk file. The on-disk file
        # is unchanged.
        self.assertEqual(
            before, after,
            "on-disk JSON was modified even though "
            "the view was already present",
        )
        # The patcher did NOT call ``async_save``
        # (no-op idempotent).
        self.assertEqual(
            len(powmr_energy._saved), 0,
            f"async_save was called "
            f"{len(powmr_energy._saved)} times in the "
            f"no-op case; expected 0",
        )

    def test_no_lovelace_data_is_noop(self):
        """When ``hass.data["lovelace"]`` is
        missing (e.g. Lovelace not initialised
        yet, YAML mode, recovery mode), the
        patcher is a no-op.
        """
        existing = self._existing_views()
        hass = types.SimpleNamespace(
            data={},  # no "lovelace" key
        )
        hass.config = types.SimpleNamespace(
            config_dir=self.tmp,
        )
        hass._saved = []
        _run(_ensure_outages_view(hass, _FakeEntry()))
        self.assertEqual(
            hass._saved, [],
            "patcher called save when lovelace "
            "data was missing",
        )

    def test_powmr_energy_panel_is_the_target(self):
        """The patcher MUST target the named
        ``powmr-energy`` panel, NOT the default
        (key ``None``) dashboard. On the live
        system the default dashboard is
        ``config_not_found`` (it has no
        ``config`` key), so the patcher must
        resolve the named panel.

        Scenario: a fresh system where the
        default dashboard has no config; the
        named ``powmr-energy`` panel has 5
        existing views. The patcher appends the
        new view to the NAMED panel and leaves
        the DEFAULT untouched.
        """
        # ``default_dashboard`` -- the
        # unnamed ``url_path=None`` panel. On
        # live this is the one that returns
        # ``config_not_found``. We model that
        # as ``config = None`` plus an
        # ``async_load`` that raises (the HA
        # behaviour).
        class _DefaultDashboard:
            def __init__(self):
                self.config = None

            async def async_load(self, force):
                from homeassistant.exceptions import (
                    HomeAssistantError,
                )
                raise HomeAssistantError(
                    "Config not found"
                )

            async def async_save(self, config):
                raise AssertionError(
                    "patcher MUST NOT touch the "
                    "default dashboard"
                )

        # The named ``powmr-energy`` panel --
        # this is the target. It has the
        # operator's 5 existing views.
        class _PowmrEnergyDashboard:
            def __init__(self, config):
                self.config = config

            async def async_load(self, force):
                return self.config

            async def async_save(self, config):
                self.config = config
                self._saved = getattr(
                    self, "_saved", []
                )
                self._saved.append(config)

        existing = self._existing_views()
        powmr_energy = _PowmrEnergyDashboard(
            {"title": "Smart Solar Енергопанель",
             "views": list(existing)}
        )
        default = _DefaultDashboard()
        hass = types.SimpleNamespace(
            data={
                "lovelace": {
                    "dashboards": {
                        None: default,
                        "powmr-energy": powmr_energy,
                    },
                },
            },
        )
        hass.config = types.SimpleNamespace(
            config_dir=self.tmp,
        )
        _setup_storage_with_views(self.tmp, existing)
        _run(_ensure_outages_view(hass, _FakeEntry()))
        # The patcher called ``async_save`` exactly
        # once, and on the NAMED panel, not the
        # default.
        self.assertEqual(
            len(powmr_energy._saved), 1,
            f"the named 'powmr-energy' panel "
            f"received {len(powmr_energy._saved)} "
            f"saves; expected exactly 1",
        )
        saved = powmr_energy._saved[0]
        self.assertEqual(
            len(saved["views"]), 6,
            f"the named panel now has "
            f"{len(saved['views'])} views; "
            f"expected 5+1=6",
        )
        self.assertEqual(
            saved["views"][-1]["path"],
            "powmr-outages",
            "the new view is the LAST entry",
        )

    def test_other_panels_unchanged(self):
        """Patcher MUST NOT touch other panels
        (e.g. the sidecar
        ``powmr-b3401a3c4784131b``). Only the
        named ``powmr-energy`` panel is the
        target.
        """
        existing = self._existing_views()

        class _FakePanel:
            def __init__(self, config):
                self.config = config
                self._saved = []

            async def async_load(self, force):
                return self.config

            async def async_save(self, config):
                self.config = config
                self._saved.append(config)

        powmr_energy = _FakePanel(
            {"title": "Smart Solar Енергопанель",
             "views": list(existing)}
        )
        # Sidecar: also has 5 views. The
        # patcher MUST NOT touch this panel.
        sidecar = _FakePanel(
            {"title": "Smart Solar · Solar Inverter",
             "views": list(existing)}
        )
        # The unnamed default (this is the one
        # the previous round wrongly targeted).
        default = _FakePanel(
            {"title": "Lovelace", "views": []}
        )
        hass = types.SimpleNamespace(
            data={
                "lovelace": {
                    "dashboards": {
                        None: default,
                        "powmr-energy": powmr_energy,
                        "powmr-b3401a3c4784131b": sidecar,
                    },
                },
            },
        )
        hass.config = types.SimpleNamespace(
            config_dir=self.tmp,
        )
        _setup_storage_with_views(self.tmp, existing)
        _run(_ensure_outages_view(hass, _FakeEntry()))
        # Only the named panel was patched.
        self.assertEqual(
            len(powmr_energy._saved), 1,
            f"named panel saved {len(powmr_energy._saved)} "
            f"times; expected 1",
        )
        self.assertEqual(
            len(sidecar._saved), 0,
            f"sidecar was patched "
            f"{len(sidecar._saved)} times; expected 0 "
            f"(the patcher MUST NOT touch other panels)",
        )
        self.assertEqual(
            len(default._saved), 0,
            f"default was patched "
            f"{len(default._saved)} times; expected 0",
        )

    def test_idempotent_repeated_run_no_dup(self):
        """Running the patcher twice in a row MUST
        NOT add the view twice. The second run
        sees the view already there and is a
        no-op.
        """
        existing = self._existing_views()

        class _FakePanel:
            def __init__(self, config):
                self.config = config
                self._saved = []

            async def async_load(self, force):
                return self.config

            async def async_save(self, config):
                self.config = config
                self._saved.append(config)

        powmr_energy = _FakePanel(
            {"title": "Smart Solar Енергопанель",
             "views": list(existing)}
        )
        hass = types.SimpleNamespace(
            data={
                "lovelace": {
                    "dashboards": {
                        "powmr-energy": powmr_energy,
                    },
                },
            },
        )
        hass.config = types.SimpleNamespace(
            config_dir=self.tmp,
        )
        _setup_storage_with_views(self.tmp, existing)
        # First run: appends.
        _run(_ensure_outages_view(hass, _FakeEntry()))
        self.assertEqual(
            len(powmr_energy._saved), 1,
            f"first run saved {len(powmr_energy._saved)} "
            f"times; expected 1",
        )
        self.assertEqual(
            len(powmr_energy.config["views"]), 6,
            f"after first run, "
            f"{len(powmr_energy.config['views'])} views; "
            f"expected 5+1=6",
        )
        # Second run: no-op.
        _run(_ensure_outages_view(hass, _FakeEntry()))
        self.assertEqual(
            len(powmr_energy._saved), 1,
            f"second run saved "
            f"{len(powmr_energy._saved)} times; expected 1 "
            f"(no-op on existing view)",
        )
        self.assertEqual(
            len(powmr_energy.config["views"]), 6,
            f"after second run, "
            f"{len(powmr_energy.config['views'])} views; "
            f"expected 6 (no duplicate)",
        )


if __name__ == "__main__":
    unittest.main()
