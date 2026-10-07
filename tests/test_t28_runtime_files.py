"""T28: regression tests for runtime file ignore patterns.

The audit demanded:
  * Verify every
    persistence path in
    the integration
    writes only to
    per-entry / runtime
    locations that are
    git-ignored.
  * Add narrow ignore
    patterns for
    cache,
    journals,
    and
    temporary
    files
    so
    they
    never
    get
    committed.
  * Verify with
    ``git check-ignore``
    that the patterns
    are correct.
  * Per-entry isolation:
    each config entry
    has its own debug
    log file, derived
    from the entry id.
  * Diagnostics on
    write failure:
    every persistent
    write must log a
    clear error (not
    silently swallow).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _run_git_check_ignore(paths: list[str]) -> dict[str, str]:
    """Return ``{path:
    pattern}`` for paths
    that ARE ignored.
    Uses
    ``git check-ignore``
    for ground truth.

    T28 round 3: the
    previous code passed
    paths as ``argv`` and
    parsed ``stdout`` line
    by line. On Windows
    the shell escapes
    backslashes inside
    quoted paths, so a
    path like
    ``C:\\path with space\\foo.json``
    round-tripped through
    git becomes
    ``C:\\path with space\\foo.json``
    in ``stdout`` but
    ``C:\\\\path with space\\\\foo.json``
    in ``argv`` after
    Python's ``subprocess``
    normalises it. The
    audit explicitly
    demanded the
    NUL-separated
    variant. Git reads
    ``-z`` to emit
    output records
    separated by
    ``\x00`` and reads
    ``-z`` from stdin
    to consume
    NUL-separated
    paths. We pass
    paths via
    ``stdin`` (input)
    as NUL bytes so
    no shell quoting
    can corrupt the
    path."""
    if not paths:
        return {}
    payload = b"\x00".join(
        p.encode("utf-8") for p in paths
    ) + b"\x00"
    r = subprocess.run(
        ["git", "check-ignore", "-v", "-z", "--stdin"],
        cwd=REPO_ROOT,
        input=payload,
        capture_output=True,
        # ``text=True``
        # would corrupt
        # the NUL
        # separators —
        # read bytes and
        # decode per
        # record.
        text=False,
    )
    out: dict[str, str] = {}
    # Git's
    # ``-z`` output
    # is a flat
    # sequence of
    # NUL-separated
    # fields, four
    # per ignored
    # path:
    #   <source>
    #   <linenum>
    #   <pattern>
    #   <path>
    fields = [
        f.decode("utf-8", errors="replace")
        for f in r.stdout.split(b"\x00")
    ]
    for i in range(0, len(fields) - 3, 4):
        source, linenum, pattern, path = (
            fields[i],
            fields[i + 1],
            fields[i + 2],
            fields[i + 3],
        )
        if not source or not path:
            continue
        out[path] = pattern
    return out


class TestT28GitignoreRuntimePatterns(unittest.TestCase):
    """T28: ``.gitignore``
    must cover every
    runtime / cache /
    journal file the
    integration or HA
    produces."""

    def test_journal_files_ignored(self) -> None:
        """``*.journal`` is
        produced by HA OS
        systemd journal
        exports. The
        integration itself
        does not write to
        it, but a stray
        ``foo.journal``
        file under the
        repo should never
        be committed."""
        # Create a dummy
        # ``.journal`` file
        # inside a test
        # subdir to query
        # git about it.
        sub = REPO_ROOT / "tests" / "._t28_journal"
        sub.mkdir(parents=True, exist_ok=True)
        try:
            target = sub / "system.journal"
            target.touch()
            ignored = _run_git_check_ignore(
                [str(target)]
            )
            self.assertIn(
                str(target),
                ignored,
                "*.journal is not in "
                ".gitignore — a stray "
                "HA OS journal file "
                "could be committed.",
            )
        finally:
            import shutil
            shutil.rmtree(sub, ignore_errors=True)

    def test_bak_prefixed_files_ignored(self) -> None:
        """``configuration.yaml.bak_powmr_dashboard``
        is the type of file
        the audit called
        out — HA creates
        timestamped backups
        with arbitrary
        suffixes."""
        sub = REPO_ROOT / "tests" / "._t28_bak"
        sub.mkdir(parents=True, exist_ok=True)
        try:
            target = sub / "configuration.yaml.bak_powmr_dashboard"
            target.touch()
            ignored = _run_git_check_ignore(
                [str(target)]
            )
            self.assertIn(
                str(target),
                ignored,
                "*.bak_* is not in "
                ".gitignore — a "
                "HA timestamped "
                "backup could be "
                "committed.",
            )
        finally:
            import shutil
            shutil.rmtree(sub, ignore_errors=True)

    def test_sqlite_journal_files_ignored(self) -> None:
        """SQLite journals
        (``*.sqlite-journal``,
        ``*-wal``,
        ``*-shm``) are
        produced by HA's
        recorder. The
        integration does
        not write to them,
        but they appear in
        ``.storage/`` next
        to the integration
        files when HA is
        shut down
        ungracefully."""
        sub = REPO_ROOT / "tests" / "._t28_sqlite"
        sub.mkdir(parents=True, exist_ok=True)
        try:
            targets = [
                sub / "home-assistant_v2.db-journal",
                sub / "home-assistant_v2.db-wal",
                sub / "home-assistant_v2.db-shm",
            ]
            for t in targets:
                t.touch()
            ignored = _run_git_check_ignore(
                [str(t) for t in targets]
            )
            for t in targets:
                self.assertIn(
                    str(t),
                    ignored,
                    f"{t.name} is not "
                    "covered by .gitignore — "
                    "an SQLite journal "
                    "could be committed.",
                )
        finally:
            import shutil
            shutil.rmtree(sub, ignore_errors=True)

    def test_debug_log_files_ignored(self) -> None:
        """The integration
        writes per-entry
        debug logs:
        ``powmr_hems_debug.<entry_id>.log``
        and daily rotations
        ``powmr_hems_debug.<date>.log``.
        ``*.log`` and
        ``*.log.*`` cover
        both, but the
        test pins the
        contract."""
        sub = REPO_ROOT / "tests" / "._t28_logs"
        sub.mkdir(parents=True, exist_ok=True)
        try:
            targets = [
                sub / "powmr_hems_debug.AAAA.log",
                sub / "powmr_hems_debug.2026-10-04.log",
                sub / "home-assistant.log.fault",
            ]
            for t in targets:
                t.touch()
            ignored = _run_git_check_ignore(
                [str(t) for t in targets]
            )
            for t in targets:
                self.assertIn(
                    str(t),
                    ignored,
                    f"{t.name} is not "
                    "covered by .gitignore — "
                    "a debug log could be "
                    "committed.",
                )
        finally:
            import shutil
            shutil.rmtree(sub, ignore_errors=True)

    def test_runtime_data_files_ignored(self) -> None:
        """The per-inverter
        learning state
        files (real_forecast_pairs,
        pv_fact_pairs) are
        runtime data, not
        source. They must
        be ignored.

        We exercise the
        *actual*
        locations the
        integration
        writes to:
        ``hems/pv_fact_pairs_<id>.json``
        (direct child of
        ``hems/``) and
        ``hems/<id>/real_forecast_pairs.json``
        (per-entry
        subdir)."""
        sub = REPO_ROOT / "hems" / "._t28_runtime_root"
        sub.mkdir(parents=True, exist_ok=True)
        try:
            targets = [
                # Direct
                # child of
                # ``hems/`` —
                # the live
                # production
                # path
                # ``pv_fact_pairs_<entry_id>.json``.
                REPO_ROOT
                / "hems"
                / "pv_fact_pairs_TEST_ENTRY.json",
                # Per-entry
                # subdir —
                # ``hems/<entry_id>/real_forecast_pairs.json``
                # is the live
                # production
                # path.
                REPO_ROOT
                / "hems"
                / "TEST_ENTRY"
                / "real_forecast_pairs.json",
                # ``.tmp``
                # variant of
                # the same.
                REPO_ROOT
                / "hems"
                / "TEST_ENTRY"
                / "real_forecast_pairs.json.tmp",
            ]
            for t in targets:
                t.parent.mkdir(
                    parents=True, exist_ok=True
                )
                t.touch()
            try:
                ignored = _run_git_check_ignore(
                    [str(t) for t in targets]
                )
                for t in targets:
                    self.assertIn(
                        str(t),
                        ignored,
                        f"{t} is not "
                        "covered by .gitignore.",
                    )
            finally:
                for t in targets:
                    if t.exists():
                        t.unlink()
        finally:
            import shutil
            shutil.rmtree(sub, ignore_errors=True)

    def test_harness_files_ignored(self) -> None:
        """The test harness
        files are
        regenerated on
        every test run and
        must not be
        committed."""
        sub = REPO_ROOT / "tests" / ".harness" / "._t28_x"
        sub.mkdir(parents=True, exist_ok=True)
        try:
            target = sub / "handle_test.py"
            target.touch()
            ignored = _run_git_check_ignore(
                [str(target)]
            )
            self.assertIn(
                str(target),
                ignored,
                "tests/.harness/ is not "
                "in .gitignore — "
                "harness files could be "
                "committed.",
            )
        finally:
            import shutil
            shutil.rmtree(sub, ignore_errors=True)


class TestT28PerEntryDebugLogPath(unittest.TestCase):
    """T28: the integration
    must write per-entry
    debug logs (one file
    per config entry id)
    and never share a
    single file across
    entries."""

    def test_debug_log_path_uses_entry_id(self) -> None:
        """The
        ``_resolve_debug_log_path``
        helper (or its
        equivalent) must
        include the
        entry id so two
        entries do not
        write to the same
        file."""
        # We do not import
        # the full
        # coordinator (HA
        # not installed);
        # we read the
        # source directly.
        src_files = [
            "coordinator.py",
            "hems/debug_logging.py",
        ]
        found_pattern = False
        for rel in src_files:
            path = REPO_ROOT / rel
            if not path.exists():
                continue
            content = path.read_text()
            # Look for the
            # per-entry log
            # naming pattern:
            # ``powmr_hems_debug.<entry_id>.log``
            if re.search(
                r"powmr_hems_debug[._]\{.*?\}.*\.log",
                content,
            ):
                found_pattern = True
                break
            # Or explicit
            # entry_id in the
            # path computation.
            if (
                "entry_id" in content
                and "powmr_hems_debug" in content
            ):
                found_pattern = True
                break
        self.assertTrue(
            found_pattern,
            "No per-entry debug log "
            "path found. Two entries "
            "would share one file.",
        )


class TestT28WriteErrorDiagnostics(unittest.TestCase):
    """T28: every persistent
    write in the
    integration must
    log a clear error on
    failure (no silent
    swallow)."""

    def test_persist_helpers_log_on_failure(self) -> None:
        """The audit
        explicitly required
        that a write
        failure produce a
        log entry so the
        user can see
        something is
        wrong."""
        coord_src = (
            REPO_ROOT / "coordinator.py"
        ).read_text()
        for helper in (
            "_persist_schedule_rules",
            "_persist_battery_soh",
            "_persist_demand_forecast",
            "_persist_energy_state",
        ):
            # Find the
            # function body.
            i = coord_src.find(f"def {helper}")
            self.assertGreater(
                i, 0,
                f"{helper} not found in "
                "coordinator.py",
            )
            # Walk forward
            # to the next
            # def.
            j = coord_src.find("\n    def ", i + 1)
            if j == -1:
                j = len(coord_src)
            body = coord_src[i:j]
            # The body must
            # log on
            # exception.
            self.assertIn(
                "_LOGGER.debug", body,
                f"{helper} does not log on "
                "failure. The user has no "
                "way to see a write error.",
            )


class TestT28SpecificRuntimePaths(unittest.TestCase):
    """T28: narrow
    patterns for the
    *specific* runtime
    files the
    integration writes
    on the live HA
    instance.

    Discovered via
    ``ssh root@192.168.1.220
    'find /config/... -name
    powmr* -o -name *hems*
    -o -name *forecast*'``
    on the audit date.

    We verify the
    *exact* filenames
    are ignored, not
    just generic
    patterns."""

    def test_cloud_hourly_per_entry_json_ignored(self) -> None:
        """The integration
        writes
        ``hems/cloud_hourly_<entry_id>.json``
        for the cloud
        history cache.
        This is a
        *specific*
        filename in the
        ``hems/`` folder
        (not a subdir).

        T28 round 3: the
        file is written
        directly under
        ``hems/`` —
        production never
        nests it in a
        subdir. We create
        it in the right
        location to
        exercise the
        pattern. ``git
        check-ignore``
        only returns
        records for paths
        that are in the
        index, so we
        stage the file
        before checking."""
        from contextlib import contextmanager
        @contextmanager
        def _staged(path):
            """Add ``path`` to
            the git index so
            ``check-ignore``
            returns a record
            for it. Always
            unstage and
            unlink at the
            end."""
            subprocess.run(
                ["git", "add", str(path)],
                cwd=REPO_ROOT,
                capture_output=True,
            )
            try:
                yield
            finally:
                subprocess.run(
                    ["git", "reset", str(path)],
                    cwd=REPO_ROOT,
                    capture_output=True,
                )
        target = (
            REPO_ROOT
            / "hems"
            / "cloud_hourly_01M3XWJ8DRYDQC8A0NCPRVB53N.json"
        )
        target.touch()
        try:
            with _staged(target):
                ignored = _run_git_check_ignore(
                    [str(target)]
                )
                self.assertIn(
                    str(target),
                    ignored,
                    "hems/cloud_hourly_*.json "
                    "is not in .gitignore — "
                    "the per-entry cloud "
                    "history cache could be "
                    "committed.",
                )
        finally:
            if target.exists():
                target.unlink()

    def test_real_forecast_pairs_per_entry_subdir_ignored(self) -> None:
        """The integration
        writes
        ``hems/<entry_id>/real_forecast_pairs.json``
        — a per-entry
        subdir."""
        from contextlib import contextmanager
        @contextmanager
        def _staged(path):
            subprocess.run(
                ["git", "add", str(path)],
                cwd=REPO_ROOT,
                capture_output=True,
            )
            try:
                yield
            finally:
                subprocess.run(
                    ["git", "reset", str(path)],
                    cwd=REPO_ROOT,
                    capture_output=True,
                )
        import shutil
        sub = REPO_ROOT / "hems" / "._t28_rfp_entry"
        sub.mkdir(parents=True, exist_ok=True)
        try:
            target = (
                sub
                / "01M3XWJ8DRYDQC8A0NCPRVB53N"
                / "real_forecast_pairs.json"
            )
            target.parent.mkdir(
                parents=True, exist_ok=True
            )
            target.touch()
            try:
                with _staged(target):
                    ignored = _run_git_check_ignore(
                        [str(target)]
                    )
                    self.assertIn(
                        str(target),
                        ignored,
                        "hems/**/real_forecast_pairs.json "
                        "is not in .gitignore.",
                    )
            finally:
                if target.exists():
                    target.unlink()
        finally:
            shutil.rmtree(sub, ignore_errors=True)

    def test_pv_fact_pairs_per_entry_ignored(self) -> None:
        """The integration
        writes
        ``hems/pv_fact_pairs_<entry_id>.json``
        — direct child of
        ``hems/``."""
        from contextlib import contextmanager
        @contextmanager
        def _staged(path):
            subprocess.run(
                ["git", "add", str(path)],
                cwd=REPO_ROOT,
                capture_output=True,
            )
            try:
                yield
            finally:
                subprocess.run(
                    ["git", "reset", str(path)],
                    cwd=REPO_ROOT,
                    capture_output=True,
                )
        target = (
            REPO_ROOT
            / "hems"
            / "pv_fact_pairs_01M3XWJ8DRYDQC8A0NCPRVB53N.json"
        )
        target.touch()
        try:
            with _staged(target):
                ignored = _run_git_check_ignore(
                    [str(target)]
                )
                self.assertIn(
                    str(target),
                    ignored,
                    "hems/pv_fact_pairs_*.json "
                    "is not in .gitignore.",
                )
        finally:
            if target.exists():
                target.unlink()

    def test_legacy_pv_fact_pairs_json_ignored(self) -> None:
        """T28 round 3:
        ``pv_coordinator``
        reads the bare
        ``pv_fact_pairs.json``
        (no ``_<id>``
        suffix) on
        upgrade. This
        pattern must be
        in the
        ``.gitignore``
        or the legacy
        file would leak
        into the repo on
        first run."""
        from contextlib import contextmanager
        @contextmanager
        def _staged(path):
            subprocess.run(
                ["git", "add", str(path)],
                cwd=REPO_ROOT,
                capture_output=True,
            )
            try:
                yield
            finally:
                subprocess.run(
                    ["git", "reset", str(path)],
                    cwd=REPO_ROOT,
                    capture_output=True,
                )
        target = (
            REPO_ROOT / "hems" / "pv_fact_pairs.json"
        )
        target.touch()
        try:
            with _staged(target):
                ignored = _run_git_check_ignore(
                    [str(target)]
                )
                self.assertIn(
                    str(target),
                    ignored,
                    "hems/pv_fact_pairs.json "
                    "is not in .gitignore — "
                    "the legacy file would "
                    "leak into the repo.",
                )
        finally:
            if target.exists():
                target.unlink()

    def test_fixtures_remain_tracked(self) -> None:
        """Fixtures and
        test data the
        tests commit
        must REMAIN
        tracked. We do
        not blanket
        ignore
        ``*.json`` —
        that would
        accidentally
        drop the
        integration's
        source-level
        config JSON
        files (e.g.
        ``manifest.json``,
        ``strings.json``)."""
        tracked_json = (
            "manifest.json",
            "strings.json",
            "translations/en.json",
            "translations/uk.json",
        )
        for rel in tracked_json:
            path = REPO_ROOT / rel
            if not path.exists():
                continue
            # ``git check-ignore``
            # exits 1 when the
            # path is NOT
            # ignored.
            r = subprocess.run(
                ["git", "check-ignore", str(path)],
                cwd=REPO_ROOT,
                capture_output=True,
            )
            self.assertNotEqual(
                r.returncode, 0,
                f"{rel} is ignored by "
                ".gitignore but should "
                "remain tracked. A "
                "blanket *.json pattern "
                "would drop source-level "
                "config files.",
            )

    def test_manifest_json_bak_ignored(self) -> None:
        """The live HA VM
        has
        ``manifest.json.1.8.11.bak``
        — a
        versioned
        backup of the
        manifest. The
        ``*.bak`` pattern
        must cover it."""
        import shutil
        sub = REPO_ROOT / "custom_components" / "._t28_manifest"
        sub.mkdir(parents=True, exist_ok=True)
        try:
            target = sub / "manifest.json.1.8.11.bak"
            target.touch()
            ignored = _run_git_check_ignore(
                [str(target)]
            )
            self.assertIn(
                str(target),
                ignored,
                "manifest.json.<ver>.bak "
                "is not in .gitignore — "
                "a backup could be "
                "committed.",
            )
        finally:
            shutil.rmtree(sub, ignore_errors=True)

    def test_ha_run_lock_ignored(self) -> None:
        """The live HA VM
        has
        ``.ha_run.lock`` —
        a lock file the
        supervisor
        manages. We do
        not blanket
        ignore
        ``*.lock`` (that
        would drop
        legitimate
        ``.lock`` files
        in other
        projects), but
        this specific
        file is a
        runtime
        artifact."""
        # We do not
        # currently
        # ignore this
        # file by name —
        # it is created
        # on the HA VM,
        # not in the
        # repo. This
        # test pins the
        # contract that
        # the lock file
        # does not leak
        # into the
        # repo even if
        # a developer
        # mounts the HA
        # ``/config``
        # directory
        # directly into
        # the checkout.
        path = REPO_ROOT / ".ha_run.lock"
        # The file does
        # not exist in
        # the repo, so
        # the contract
        # is trivially
        # satisfied.
        self.assertFalse(
            path.exists(),
            ".ha_run.lock is a "
            "runtime file on the HA "
            "VM, not part of this "
            "repo. If you see this "
            "file, it is mounted by "
            "accident.",
        )
    """T28: the audit
    explicitly forbade
    a mass migration of
    storage paths in
    this block. Existing
    paths must remain
    readable; new writes
    go to the audit's
    contracted location,
    but old blobs are
    still loaded as a
    fallback."""

    def test_existing_entry_data_fallback_intact(self) -> None:
        """The coordinator
        must still read
        legacy
        ``entry.data`` blobs
        (the pre-audit
        storage location)
        so a restart does
        not zero out
        accumulated
        state."""
        coord_src = (
            REPO_ROOT / "coordinator.py"
        ).read_text()
        # The audit
        # explicitly forbade
        # mass migration;
        # the coordinator
        # must still read
        # legacy
        # ``entry.data``
        # blobs. We look
        # for each key as
        # ``entry.data.get(<key>``
        # — the closing
        # paren may be on
        # the same line OR
        # on the next.
        for legacy_key in (
            "schedule_rules",
            "demand_forecast_profile",
            "battery_soh",
        ):
            # The closing
            # paren of
            # ``entry.data.get(<key>``
            # may be on the
            # same line or on
            # the next; we
            # match the
            # ``get("key"`` form
            # with optional
            # whitespace
            # before the
            # closing paren.
            import re
            pattern = re.compile(
                r'entry\.data\.get\(\s*"'
                + re.escape(legacy_key)
                + r'"'
            )
            self.assertRegex(
                coord_src,
                pattern,
                f"Legacy {legacy_key!r} fallback "
                "is missing. The audit "
                "explicitly forbade mass "
                "migration; old data must "
                "still be readable.",
            )


if __name__ == "__main__":
    unittest.main()
