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
        hass = _make_hass(self.tmp, existing)
        _setup_storage_with_views(self.tmp, existing)
        _run(_ensure_outages_view(hass, _FakeEntry()))
        # The patcher called ``async_save`` exactly
        # once.
        self.assertEqual(
            len(hass._saved), 1,
            f"async_save was not called exactly "
            f"once; called {len(hass._saved)} times",
        )
        saved = hass._saved[0]
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
        hass = _make_hass(self.tmp, existing)
        _setup_storage_with_views(self.tmp, existing)
        _run(_ensure_outages_view(hass, _FakeEntry()))
        # No save -- the view is already there.
        self.assertEqual(
            hass._saved, [],
            f"async_save was called when the view "
            f"was already present; called "
            f"{len(hass._saved)} times",
        )

    def test_preserves_five_existing_views_verbatim(self):
        """The patcher MUST NOT mutate, reorder, or
        re-encode the existing five views. The
        saved config's ``views`` slice ``[:-1]``
        is byte-identical (in the JSON sense) to
        the input.
        """
        existing = self._existing_views()
        hass = _make_hass(self.tmp, existing)
        _setup_storage_with_views(self.tmp, existing)
        _run(_ensure_outages_view(hass, _FakeEntry()))
        saved = hass._saved[0]
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
        """
        existing = self._existing_views()
        hass = _make_hass(self.tmp, existing)
        on_disk_path = _setup_storage_with_views(
            self.tmp, existing
        )
        _run(_ensure_outages_view(hass, _FakeEntry()))
        # Find the backup.
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
        hass = _make_hass(self.tmp, existing)
        on_disk_path = _setup_storage_with_views(
            self.tmp, existing
        )
        with open(on_disk_path, "rb") as f:
            before = f.read()
        # No-op case: the view is already present
        # -- pre-seed a matching view.
        existing.append(
            {
                "title": "Відключення",
                "path": "powmr-outages",
            }
        )
        _run(_ensure_outages_view(hass, _FakeEntry()))
        with open(on_disk_path, "rb") as f:
            after = f.read()
        self.assertEqual(
            before, after,
            "on-disk JSON was modified even though "
            "the view was already present",
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


if __name__ == "__main__":
    unittest.main()
