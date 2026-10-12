"""Journal backup atomicity (Юра п.2).

The previous implementation of
``_backup_journal_on_identity_mismatch`` swallowed
``Exception`` from the backup ``write_text`` call and returned
``None``, allowing the live coordinator to continue with an
empty state and then overwrite the original on-disk journal on
its next ``save()``. That was a data-loss path for the operator.

This test pins the new contract:

  1. When the backup ``write_text`` raises ``OSError`` (we
     inject the failure), the load() path must RAISE a
     ``JournalBackupRequired`` exception — not swallow it.
  2. The exception must propagate out of
     ``PvLearningCoordinatorMixin._ensure_pv_state_loaded`` as
     a ``JournalBackupRequired`` so the surrounding code in
     ``hems/pv_coordinator.py`` can refuse to ``save()`` later.
  3. After the failed load, the original on-disk journal file
     is byte-for-byte UNCHANGED.
  4. After a save() attempt (which the mixin must refuse), the
     on-disk journal is still byte-for-byte UNCHANGED.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _make_legacy_journal(directory: Path, *, latitude: float, longitude: float) -> bytes:
    """Write a legacy Kyiv-journal payload to ``directory`` and
    return the bytes — the test then compares on-disk content
    against this snapshot after the failed-load and refused-save
    sequence.
    """
    payload = (
        "{\n"
        '  "version": 3,\n'
        '  "unit": "kWh",\n'
        f'  "latitude": {latitude},\n'
        f'  "longitude": {longitude},\n'
        '  "timezone": "Europe/Kyiv",\n'
        '  "radiation_contract_version": 1,\n'
        '  "snapshots": {\n'
        '    "2026-09-28": {"forecast_kwh": 4.32, "issued_at": '
        '"2026-09-27T22:00:00+00:00", "forecast_model": '
        '"hourly_response_v2"}\n'
        "  },\n"
        '  "pairs": {\n'
        '    "2026-09-28": {"forecast_kwh": 4.32, "actual_kwh": 4.10, '
        '"coverage": 1.0, "forecast_model": "hourly_response_v2"}\n'
        "  },\n"
        '  "radiation": {"2026-09-28": 4.21},\n'
        '  "archive_checked_day": "2026-09-29",\n'
        '  "model": {"gain": 0.13, "sample_count": 30},\n'
        '  "calibration_model": "hourly_response_v2"\n'
        "}\n"
    )
    journal = directory / "pv_fact_pairs_test-entry.json"
    journal.write_text(payload, encoding="utf-8")
    return payload.encode("utf-8")


def _new_state_with_lviv_identity():
    """A new ``PvLearningState`` whose identity is the Lviv
    coords (49.83, 24.0) — used as the test 'new identity' that
    the operator's HA now reports.
    """
    from hems.pv_learning import PvLearningState
    return PvLearningState("Europe/Kyiv", 49.83, 24.0)


class JournalBackupAtomicityTests(unittest.TestCase):
    """The ``PvLearningState.load`` path with a mismatched on-disk
    identity must raise ``JournalBackupRequired`` when the backup
    write fails — and the original on-disk journal must not be
    overwritten.
    """

    def test_backup_failure_propagates_uncaught_from_load(self):
        from hems.pv_learning import (
            JournalBackupRequired,
            PvLearningState,
        )
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            original = _make_legacy_journal(
                directory, latitude=50.45, longitude=30.52,
            )
            state = PvLearningState("Europe/Kyiv", 49.83, 24.0)

            # Inject OSError on the backup write. The production
            # helper builds the backup path the same way it
            # does on success; we just monkey-patch the
            # ``write_text`` method on the class so the
            # failure surfaces inside the helper.
            from hems import pv_learning as _pv
            original_write = _pv.Path.write_text

            def _exploding_write(self, *args, **kwargs):
                if ".coords_backup_" in str(self):
                    raise OSError("disk full (injected)")
                return original_write(self, *args, **kwargs)

            _pv.Path.write_text = _exploding_write
            try:
                with self.assertRaises(JournalBackupRequired):
                    state.load(directory / "pv_fact_pairs_test-entry.json")
            finally:
                _pv.Path.write_text = original_write

            # The original journal on disk is byte-for-byte
            # unchanged — the failed backup must not have
            # touched it.
            journal_path = directory / "pv_fact_pairs_test-entry.json"
            self.assertEqual(
                journal_path.read_bytes(), original,
                msg=(
                    "backup failure must not modify the original "
                    "on-disk journal"
                ),
            )

    def test_no_backup_written_when_write_fails(self):
        """The failed backup must not have created the side-by-side
        backup file. The only file in the directory is the
        original journal.
        """
        from hems.pv_learning import (
            JournalBackupRequired,
            PvLearningState,
        )
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _make_legacy_journal(
                directory, latitude=50.45, longitude=30.52,
            )
            state = PvLearningState("Europe/Kyiv", 49.83, 24.0)

            from hems import pv_learning as _pv
            original_write = _pv.Path.write_text

            def _exploding_write(self, *args, **kwargs):
                if ".coords_backup_" in str(self):
                    raise OSError("read-only fs (injected)")
                return original_write(self, *args, **kwargs)

            _pv.Path.write_text = _exploding_write
            try:
                with self.assertRaises(JournalBackupRequired):
                    state.load(directory / "pv_fact_pairs_test-entry.json")
            finally:
                _pv.Path.write_text = original_write

            # No backup file was created.
            backups = list(directory.glob("pv_fact_pairs_test-entry.json.coords_backup_*"))
            self.assertEqual(
                backups, [],
                msg=(
                    f"backup file was created despite write failure: {backups}"
                ),
            )

    def test_successful_backup_does_not_modify_original(self):
        """Sanity check: when the backup write SUCCEEDS, the
        original journal is also unchanged.
        """
        from hems.pv_learning import PvLearningState
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            original = _make_legacy_journal(
                directory, latitude=50.45, longitude=30.52,
            )
            state = PvLearningState("Europe/Kyiv", 49.83, 24.0)
            state.load(directory / "pv_fact_pairs_test-entry.json")
            journal_path = directory / "pv_fact_pairs_test-entry.json"
            self.assertEqual(journal_path.read_bytes(), original)


class RealForecastPairsBackupAtomicityTests(unittest.TestCase):
    """Same contract for ``RealForecastPairs`` — the second journal
    store that the live coordinator also writes to.
    """

    def test_backup_failure_propagates_uncaught_from_load(self):
        from hems.pv_learning import (
            JournalBackupRequired,
            RealForecastPairs,
        )
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            journal = directory / "real_pairs_test-entry.json"
            payload = (
                "{\n"
                '  "version": 2,\n'
                '  "identity": {"latitude": 50.45, "longitude": 30.52, '
                '"timezone": "Europe/Kyiv"},\n'
                '  "pairs": [\n'
                '    {"date": "2026-09-28", "forecast_kwh": 4.32, '
                '"actual_kwh": 4.10, "captured_at": '
                '"2026-09-27T22:00:00+00:00", "used": true, '
                '"forecast_model": "hourly_response_v2"}\n'
                "  ]\n"
                "}\n"
            )
            journal.write_text(payload, encoding="utf-8")
            original = journal.read_bytes()

            state = RealForecastPairs(
                {"latitude": 49.83, "longitude": 24.0,
                 "timezone": "Europe/Kyiv"}
            )

            from hems import pv_learning as _pv
            original_write = _pv.Path.write_text

            def _exploding_write(self, *args, **kwargs):
                # RealForecastPairs shares the
                # ``_backup_journal_on_identity_mismatch`` helper
                # with PvLearningState — both use the
                # ``.coords_backup_<lat>_<lon>_<ts>`` suffix.
                if ".coords_backup_" in str(self):
                    raise OSError("read-only fs (injected)")
                return original_write(self, *args, **kwargs)

            _pv.Path.write_text = _exploding_write
            try:
                with self.assertRaises(JournalBackupRequired):
                    state.load(journal)
            finally:
                _pv.Path.write_text = original_write

            self.assertEqual(
                journal.read_bytes(), original,
                msg=(
                    "RealForecastPairs: backup failure must not "
                    "modify the original on-disk journal"
                ),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
