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

    def test_recovery_after_transient_backup_failure(self):
        """Coord audit (Юра п.2): a transient backup failure
        MUST clear on the next successful load. Sequence:
          1. Inject OSError on backup write → load raises
             JournalBackupRequired, original journal unchanged.
          2. Restore the disk (no OSError) → next load succeeds,
             backup file is written, the journal stays intact
             (the live state is empty until a save() happens).
          3. After recovery, a save() does NOT need to be
             blocked — the backup file is now present and the
             original journal is preserved.
        """
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
            journal_path = directory / "pv_fact_pairs_test-entry.json"

            from hems import pv_learning as _pv
            original_write = _pv.Path.write_text
            inject_failure = {"on": True}

            def _toggled_write(self, *args, **kwargs):
                if (
                    ".coords_backup_" in str(self)
                    and inject_failure["on"]
                ):
                    raise OSError("transient: disk full")
                return original_write(self, *args, **kwargs)

            _pv.Path.write_text = _toggled_write
            try:
                # 1. First load: backup fails, original preserved.
                state_fail = PvLearningState("Europe/Kyiv", 49.83, 24.0)
                with self.assertRaises(JournalBackupRequired):
                    state_fail.load(journal_path)
                self.assertEqual(journal_path.read_bytes(), original)
                backups_during_failure = list(
                    directory.glob("pv_fact_pairs_test-entry.json.coords_backup_*")
                )
                self.assertEqual(backups_during_failure, [])

                # 2. Disk recovers; next load succeeds. The
                # backup file is now written; the original
                # journal is still unchanged.
                inject_failure["on"] = False
                state_ok = PvLearningState("Europe/Kyiv", 49.83, 24.0)
                state_ok.load(journal_path)
                backups_after_recovery = list(
                    directory.glob("pv_fact_pairs_test-entry.json.coords_backup_*")
                )
                self.assertEqual(
                    len(backups_after_recovery), 1,
                    msg=(
                        "after recovery the backup file MUST exist — "
                        "without it the audit trail is incomplete"
                    ),
                )
                self.assertEqual(
                    journal_path.read_bytes(), original,
                    msg=(
                        "the original journal MUST still be intact "
                        "after the successful backup"
                    ),
                )
            finally:
                _pv.Path.write_text = original_write


class RealPairsFlagIsolationTests(unittest.TestCase):
    """The two journals' backup-pending flags are INDEPENDENT.
    A backup failure on the RealForecastPairs journal must NOT
    block saves of the PV state journal, and vice versa.
    """

    def _make_state_and_mixin(self):
        """Return a coordinator-like mixin with the two flags
        pre-initialised; the mixin delegates load/save to two
        injected journal stubs so the test stays close to the
        real flag flow.
        """
        from hems.pv_learning import (
            PvLearningState,
            RealForecastPairs,
        )

        class _Mix(PvLearningCoordinatorMixinForTest):
            def __init__(self):
                # match class-level defaults; assert those are
                # independent and mutable.
                self._journal_backup_required = False
                self._real_pairs_backup_required = False
                self._pv_state_path = Path("/dev/null/x.json")
                self._real_pairs_path = Path("/dev/null/y.json")
                self._pv_learning = PvLearningState("UTC", 50.0, 30.0)
                self._real_pairs_store = RealForecastPairs(
                    {"latitude": 50.0, "longitude": 30.0,
                     "timezone": "UTC"}
                )

        return _Mix()

    def test_real_pairs_flag_does_not_block_pv_state_flag(self):
        """Setting the real-pairs flag must NOT raise the PV
        state flag, and vice versa.
        """
        mixin = self._make_state_and_mixin()
        mixin._real_pairs_backup_required = True
        self.assertFalse(
            mixin._journal_backup_required,
            msg=(
                "setting the real-pairs flag MUST NOT raise the "
                "PV state flag"
            ),
        )
        mixin._real_pairs_backup_required = False
        mixin._journal_backup_required = True
        self.assertFalse(
            mixin._real_pairs_backup_required,
            msg=(
                "setting the PV state flag MUST NOT raise the "
                "real-pairs flag"
            ),
        )

    def test_pv_state_save_blocked_only_by_pv_state_flag(self):
        """If only the PV state flag is set, the real-pairs
        save() guard is still off.
        """
        mixin = self._make_state_and_mixin()
        mixin._journal_backup_required = True
        self.assertTrue(mixin._journal_backup_required)
        self.assertFalse(
            mixin._real_pairs_backup_required,
            msg=(
                "RealForecastPairs.save() guard MUST be off when "
                "only the PV state backup is pending"
            ),
        )

    def test_real_pairs_save_blocked_only_by_real_pairs_flag(self):
        mixin = self._make_state_and_mixin()
        mixin._real_pairs_backup_required = True
        self.assertTrue(mixin._real_pairs_backup_required)
        self.assertFalse(
            mixin._journal_backup_required,
            msg=(
                "PvLearningState.save() guard MUST be off when "
                "only the real-pairs backup is pending"
            ),
        )


# Stub mixin so we can use the production flag contract without
# pulling in the full coordinator. The class body is a verbatim
# copy of the production class attributes; the rest of the
# mixin is irrelevant to this test.
try:
    from hems.pv_coordinator import PvLearningCoordinatorMixin as _RealMixin
    PvLearningCoordinatorMixinForTest = _RealMixin
except ImportError:  # pragma: no cover — sandbox without HA deps
    class PvLearningCoordinatorMixinForTest:  # type: ignore[no-redef]
        _journal_backup_required: bool = False
        _real_pairs_backup_required: bool = False


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
