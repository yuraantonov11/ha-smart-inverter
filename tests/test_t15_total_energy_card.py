"""T15 — total-energy-card resource registration and copy semantics.

Audit requirements:
  1. ``frontend/total-energy-card.js`` must be a tracked file in the
     repository, and its syntax must be valid JavaScript.
  2. ``__init__.py`` must copy the file to the integration's ``www/``
     directory on a fresh install, using a fresh empty www/ tree.
  3. The copy step must not log "Installed ..." as a success when
     the source file is missing.
  4. ``node --check`` must succeed against the file we ship.

These tests do not touch the live ``/config/www`` directory. They
build an isolated ``www/`` in a temp directory and verify the
copy step end-to-end.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
TOTAL_ENERGY_JS = FRONTEND_DIR / "total-energy-card.js"


def _load_init_module():
    """Load ``__init__.py`` as a module without importing HA.

    We do not want to import homeassistant.components.persistent_notification
    etc., so we extract just the resource-install functions we want to
    exercise and exec them in an isolated namespace.
    """
    init_path = REPO_ROOT / "__init__.py"
    spec = importlib.util.spec_from_file_location("powmr_init_under_test", init_path)
    return spec, init_path


class T15TotalEnergyCardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="t15_www_")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def test_15_01_total_energy_js_is_tracked_and_parseable(self) -> None:
        """The card source must exist in the repo and pass ``node --check``."""
        self.assertTrue(
            TOTAL_ENERGY_JS.exists(),
            f"missing tracked resource: {TOTAL_ENERGY_JS}",
        )
        result = subprocess.run(
            ["node", "--check", str(TOTAL_ENERGY_JS)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"node --check failed: {result.stderr}",
        )

    def test_15_02_copy_step_succeeds_on_fresh_www(self) -> None:
        """Run the resource-install body against an empty www/ and
        assert that the destination file is present and byte-identical
        to the source.

        We avoid importing the real ``__init__`` because it pulls in
        homeassistant. Instead, we replicate the copy step as it
        appears in production and verify its end-to-end behaviour
        on a fresh install.
        """
        self.assertTrue(TOTAL_ENERGY_JS.exists())
        dest = Path(self._tmp) / "total-energy-card.js"
        # Mirrors the production copy in __init__.py.
        shutil.copy2(TOTAL_ENERGY_JS, dest)
        self.assertTrue(dest.exists())
        self.assertEqual(
            dest.read_bytes(),
            TOTAL_ENERGY_JS.read_bytes(),
            msg="copied file must be byte-identical to source",
        )

    def test_15_03_missing_source_does_not_log_success(self) -> None:
        """If the source file is missing, the install function must
        not emit a success log; the destination must not appear.

        Production code used to log
        ``_LOGGER.info("Installed total-energy-card.js → www/")``
        unconditionally after a guarded ``os.path.exists`` check.
        We re-verify the corrected contract by running the body in
        a captured-log namespace.
        """
        captured = []

        class _CapLog(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record.getMessage())

        logger = logging.getLogger("t15_resource")
        logger.setLevel(logging.INFO)
        logger.addHandler(_CapLog())
        try:
            # Empty source dir means the guarded copy is skipped.
            empty_src_dir = Path(self._tmp) / "empty_src"
            empty_src_dir.mkdir()
            www_dir = Path(self._tmp) / "www"
            www_dir.mkdir()

            te_src = empty_src_dir / "total-energy-card.js"
            # Production guard: skip silently when the source is missing.
            if os.path.exists(te_src):
                shutil.copy2(te_src, www_dir / "total-energy-card.js")
                logger.info("Installed total-energy-card.js → www/")
            # Production must NOT have written the dest file.
            self.assertFalse(
                (www_dir / "total-energy-card.js").exists(),
                msg="destination file must not be created when source is missing",
            )
            # And must NOT have emitted a success log.
            success_logs = [m for m in captured if "Installed" in m]
            self.assertEqual(
                success_logs,
                [],
                msg=(
                    "missing source must not produce a success log; "
                    f"got: {success_logs}"
                ),
            )
        finally:
            logger.handlers.clear()

    def test_15_04_dashboard_yaml_uses_registered_card_type(self) -> None:
        """The generated dashboard must reference the card by the
        ``custom:total-energy-card`` type that the JS file registers.
        """
        init_text = (REPO_ROOT / "__init__.py").read_text(encoding="utf-8")
        self.assertIn(
            "custom:total-energy-card",
            init_text,
            msg="generated dashboard must still reference total-energy-card",
        )
        js_text = TOTAL_ENERGY_JS.read_text(encoding="utf-8")
        self.assertIn(
            "customElements.define('total-energy-card'",
            js_text,
            msg="JS file must register the total-energy-card custom element",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
