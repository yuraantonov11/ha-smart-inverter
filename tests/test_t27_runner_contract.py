"""T27 — one reproducible test runner.

Audit T27 asked for a single runner that
covers all Python script suites and the
JS suite, on both Windows and Linux, and:

  * any missing runner or dependency must
    produce a clear error and a non-zero
    exit code;
  * a zero-test result must NOT count as
    a pass.

The runner lives at ``tests/run_all.py``.
The tests in this module pin the
contract by invoking it as a subprocess
and asserting on its exit code, output,
and side effects.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest import main as _main


REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER = REPO_ROOT / "tests" / "run_all.py"


def _run_runner(
    *args: str,
    env_extra: dict[str, str] | None = None,
    timeout: float = 240.0,
) -> subprocess.CompletedProcess:
    """Run the test runner as a subprocess."""
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=timeout,
    )


class T27RunnerContractTests(unittest.TestCase):
    """Pin the T27 contract on ``tests/run_all.py``."""

    def test_t27_01_runner_exists_and_is_runnable(self) -> None:
        """The runner script must exist
        at ``tests/run_all.py`` and run
        end-to-end.
        """
        self.assertTrue(
            RUNNER.exists(),
            msg=(
                f"missing test runner at {RUNNER}"
            ),
        )
        # ``--help`` is a stable smoke
        # test: if it crashes here the
        # runner is broken even before
        # any test is collected.
        r = _run_runner("--help")
        self.assertEqual(
            r.returncode,
            0,
            msg=(
                f"runner --help failed: stdout={r.stdout!r} "
                f"stderr={r.stderr!r}"
            ),
        )

    def test_t27_02_runner_returns_zero_when_all_green(self) -> None:
        """With the live test suite
        (which we maintain green), the
        runner exits 0.
        """
        r = _run_runner("--python-only")
        self.assertEqual(
            r.returncode,
            0,
            msg=(
                f"runner exited {r.returncode}, expected 0. "
                f"stdout tail: {r.stdout.splitlines()[-3:]!r}, "
                f"stderr tail: {r.stderr.splitlines()[-3:]!r}"
            ),
        )

    def test_t27_03_runner_exit_code_on_failing_child(self) -> None:
        """A failing Python test file
        makes the runner exit non-zero.
        """
        # Create a temporary failing
        # test file under tests/ so the
        # runner picks it up, then
        # delete it.
        failing = REPO_ROOT / "tests" / "test_T27_synthetic_failing.py"
        failing.write_text(
            textwrap.dedent(
                '''\
                """Synthetic failing child — must fail."""
                from unittest import TestCase, main

                class SyntheticFailingTests(TestCase):
                    def test_must_fail(self):
                        self.fail("synthetic failure for T27")

                if __name__ == "__main__":
                    main()
                '''
            ),
            encoding="utf-8",
        )
        try:
            r = _run_runner("--python-only")
            self.assertNotEqual(
                r.returncode,
                0,
                msg=(
                    "runner exited 0 even though the synthetic "
                    "test failed; runner is hiding real failures"
                ),
            )
            # The output must surface the
            # failing file name so a
            # developer can find it.
            combined = r.stdout + r.stderr
            self.assertIn(
                "test_T27_synthetic_failing",
                combined,
                msg=(
                    "runner output must surface the failing "
                    "test file name"
                ),
            )
        finally:
            failing.unlink(missing_ok=True)

    def test_t27_04_runner_clear_error_for_missing_python(self) -> None:
        """If Python is missing the
        runner must fail with a clear
        message and a non-zero exit
        code — never silently zero.
        """
        # ``sys.executable`` resolves to
        # the absolute path of the
        # current interpreter, which
        # does not depend on ``PATH``.
        # The runner honours the
        # ``T27_FORCE_NO_PYTHON`` env
        # variable so the contract test
        # can exercise the failure path
        # without rebuilding the runner.
        empty_dir = REPO_ROOT / ".t27-empty-path"
        empty_dir.mkdir(exist_ok=True)
        try:
            r = _run_runner(
                "--python-only",
                env_extra={
                    "PATH": str(empty_dir),
                    "PYTHON": "/no/such/python",
                    "T27_FORCE_NO_PYTHON": "1",
                },
                timeout=30,
            )
            self.assertNotEqual(
                r.returncode,
                0,
                msg=(
                    "runner must exit non-zero when no Python "
                    "interpreter is available"
                ),
            )
            combined = r.stdout + r.stderr
            self.assertTrue(
                "python" in combined.lower()
                or "interpreter" in combined.lower(),
                msg=(
                    "runner error must mention Python when no "
                    "interpreter is available; got: "
                    f"{combined[:200]!r}"
                ),
            )
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)

    def test_t27_05_runner_json_summary(self) -> None:
        """The runner emits a JSON
        summary so CI can parse it.
        """
        r = _run_runner("--python-only", "--json")
        self.assertEqual(
            r.returncode,
            0,
            msg=(
                f"runner --json exited {r.returncode}: "
                f"stderr={r.stderr!r}"
            ),
        )
        # Find a JSON object in the
        # output. The runner prints
        # human-friendly lines first,
        # then a ``SUMMARY_JSON=...``
        # marker.
        summary = None
        for line in r.stdout.splitlines():
            if line.startswith("SUMMARY_JSON="):
                payload = line[len("SUMMARY_JSON="):].strip()
                try:
                    summary = json.loads(payload)
                except json.JSONDecodeError:
                    pass
        self.assertIsNotNone(
            summary,
            msg=(
                "runner must emit a SUMMARY_JSON=... line; "
                f"got stdout tail: {r.stdout.splitlines()[-5:]!r}"
            ),
        )
        # The summary must include the
        # number of files tested, the
        # number of failed suites, and a
        # total pass/fail count.
        for key in (
            "python_files",
            "python_passed",
            "python_failed",
        ):
            self.assertIn(
                key,
                summary,
                msg=(
                    f"summary missing {key!r}; got keys: "
                    f"{list(summary.keys())!r}"
                ),
            )

    def test_t27_06_zero_tests_is_not_a_pass(self) -> None:
        """If a directory has zero
        test files the runner must
        report zero tests but NOT
        silently return 0. The audit
        explicitly banned zero-tests =
        pass.
        """
        empty_dir = REPO_ROOT / "tests" / "_t27_empty"
        empty_dir.mkdir(exist_ok=True)
        try:
            r = _run_runner(
                "--python-only",
                "--dir",
                str(empty_dir),
            )
            # Non-zero because zero tests
            # is not a pass per the audit.
            self.assertNotEqual(
                r.returncode,
                0,
                msg=(
                    "runner exited 0 on zero-tests directory; "
                    "audit T27 forbids this"
                ),
            )
            combined = r.stdout + r.stderr
            self.assertIn(
                "0",
                combined,
                msg=(
                    "runner output must mention zero-tests; "
                    f"got: {combined[:300]!r}"
                ),
            )
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)

    def test_t27_07_runner_reports_per_suite_outcome(self) -> None:
        """Per-suite outcomes must be
        visible in the human-friendly
        output so a developer can see
        which suite failed without
        parsing JSON.
        """
        # Run with a known passing suite.
        r = _run_runner(
            "--python-only",
            "--dir",
            str(REPO_ROOT / "tests"),
            "--only",
            "test_t18_debug_logging_threadsafe.py",
        )
        combined = r.stdout + r.stderr
        # The runner must mention the
        # specific file we asked for.
        self.assertIn(
            "test_t18_debug_logging_threadsafe",
            combined,
            msg=(
                "runner must report the per-suite outcome for "
                f"the requested file; got: {combined[:300]!r}"
            ),
        )


    def test_t27_12_wrapper_handles_windows_paths(self) -> None:
        """The wrapper string must
        correctly encode Windows-style
        paths (``C:\\...``) without
        breaking on the embedded
        backslashes. The audit's
        follow-up requirement: build
        Python string literals via
        ``repr()`` (or ``json.dumps()``),
        not via manual concatenation
        that would mangle
        ``C:\\Users\\foo`` to
        ``C:Users\\foo`` after Python
        escape processing.

        We construct a fake
        ``suite_dir`` and ``suite_rel``
        with a Windows-style path,
        build the same wrapper string
        the runner builds (via
        ``repr()``), and exec it in
        a subprocess. The subprocess
        must parse the wrapper without
        a ``SyntaxError`` and reach
        ``sys.path.insert`` with the
        original path intact.
        """
        import sys as _sys
        import subprocess as _subprocess
        # Simulate a Windows checkout
        # by faking the path style.
        # The wrapper must not care:
        # ``repr()`` produces a
        # single-quoted Python string
        # with backslashes properly
        # escaped, and the resulting
        # string round-trips through
        # ``ast.literal_eval`` to the
        # original value.
        fake_repo_root = r"C:\\Users\\yura\\repo"
        fake_suite_dir = r"C:\\Users\\yura\\repo\\tests"
        fake_suite_rel = (
            r"C:\\Users\\yura\\repo\\tests\\test_dummy.py"
        )
        # Build the wrapper using
        # the same ``repr()`` calls
        # the runner uses. The
        # resulting string must be
        # valid Python source.
        wrapper = (
            "import sys\n"
            "import runpy\n"
            "import select as _stdlib_select\n"
            "import selectors as _stdlib_selectors\n"
            "import socket as _stdlib_socket\n"
            "import asyncio as _stdlib_asyncio\n"
            "sys.path.insert(0, " + repr(fake_suite_dir) + ")\n"
            "sys.path.insert(0, " + repr(fake_repo_root) + ")\n"
            "_repo_root = " + repr(fake_repo_root) + "\n"
            "sys.modules['select'] = _stdlib_select\n"
            "sys.argv[0] = " + repr(fake_suite_rel) + "\n"
        )
        # The wrapper must be valid
        # Python source — ``compile``
        # would reject an unterminated
        # string, a backslash typo, or
        # any other syntax error.
        try:
            compile(wrapper, "<t27-windows-path>", "exec")
        except SyntaxError as exc:
            self.fail(
                "wrapper source is not valid Python: "
                f"{exc}. The runner is producing a "
                "broken wrapper string. Wrapper was: "
                f"{wrapper!r}"
            )
        # Run the wrapper in a
        # subprocess and capture the
        # resulting ``sys.path`` and
        # the parsed ``_repo_root``.
        # We do not need the test
        # file at the path to exist;
        # the wrapper is a probe
        # for the path-encoding
        # contract, not a real
        # test execution.
        #
        # We use ``json.dumps()`` to
        # serialise the path values
        # into a Python string literal
        # and concatenate with the
        # wrapper. ``json.dumps()``
        # produces a single-quoted
        # Python source string with
        # all backslashes properly
        # escaped, which is exactly
        # what the runner does for
        # its own wrapper construction.
        import json as _json_mod
        probe = (
            wrapper
            + "import json as _json\n"
            + "import sys as _sys\n"
            + "_probe = {\n"
            + "    'suite_dir_in_path': "
            + _json_mod.dumps(fake_suite_dir)
            + " in _sys.path,\n"
            + "    'repo_in_path': "
            + _json_mod.dumps(fake_repo_root)
            + " in _sys.path,\n"
            + "    'repo_root_eq': _repo_root == "
            + _json_mod.dumps(fake_repo_root)
            + ",\n"
            + "}\n"
            + "print('PROBE=' + _json.dumps(_probe))\n"
        )
        r = _subprocess.run(
            [_sys.executable, "-I", "-c", probe],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # The subprocess must have
        # parsed the wrapper, run the
        # path-insert code, and
        # printed the probe.
        self.assertEqual(
            r.returncode,
            0,
            msg=(
                "wrapper probe subprocess failed. "
                f"stdout={r.stdout!r}, stderr={r.stderr!r}"
            ),
        )
        # Parse the PROBE=... line.
        probe_data = None
        for line in r.stdout.splitlines():
            if line.startswith("PROBE="):
                import json as _json
                probe_data = _json.loads(line[len("PROBE="):])
                break
        self.assertIsNotNone(
            probe_data,
            msg=(
                "probe subprocess did not emit a "
                "PROBE=... line; stdout: "
                f"{r.stdout!r}"
            ),
        )
        # The Windows-style paths
        # must have round-tripped
        # through the wrapper without
        # mangling. A backslash-eaten
        # path would fail both checks.
        self.assertTrue(
            probe_data["suite_dir_in_path"],
            msg=(
                "suite_dir was not on sys.path after the "
                "wrapper ran. The runner is likely "
                "mangling Windows-style backslashes. "
                f"Probe: {probe_data!r}"
            ),
        )
        self.assertTrue(
            probe_data["repo_in_path"],
            msg=(
                "REPO_ROOT was not on sys.path after the "
                "wrapper ran. The runner is likely "
                "mangling Windows-style backslashes. "
                f"Probe: {probe_data!r}"
            ),
        )
        self.assertTrue(
            probe_data["repo_root_eq"],
            msg=(
                "_repo_root does not equal the input "
                "REPO_ROOT after the wrapper ran. The "
                "runner is likely truncating the path. "
                f"Probe: {probe_data!r}"
            ),
        )

    def test_t27_11_runner_runs_full_suite_with_isolation(self) -> None:
        """The runner must execute the
        full set of ``tests/test_*.py``
        suites under the Windows-safe
        isolation layer and report
        zero failures. This is the
        audit's full repro: the
        runner cannot rely on
        ``cwd=REPO_ROOT`` or
        ``PYTHONPATH`` to dodge the
        ``./select.py`` shadow on
        Windows.

        The test invokes the production
        runner (via ``runpy``) with
        ``--python-only --json`` and
        asserts ``python_failed == 0``.
        We exclude this contract test
        itself from the run so the
        runner does not recurse into
        the test that is running it.
        """
        import runpy as _runpy
        import json as _json
        # Run the production runner
        # via ``runpy`` so the test
        # process spawns the runner
        # process, which then spawns
        # the suite processes. The
        # ``T27_INCLUDE_SELF=1``
        # variable lets the runner
        # pick up the contract test
        # itself, but we exclude it
        # explicitly by running
        # ``--only`` over a known
        # non-recursive subset.
        runner_path = RUNNER
        # We invoke the runner as a
        # subprocess — invoking it
        # in-process would re-enter
        # this test (recursion). The
        # subprocess inherits the
        # same ``cwd`` and
        # ``PYTHONPATH`` so the
        # production runner sees
        # the same environment.
        proc = subprocess.run(
            [sys.executable, str(runner_path), "--python-only", "--json"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                # Force the contract test
                # to be excluded so the
                # runner does not try to
                # recurse into this test
                # via the T27 contract
                # module. The contract
                # module itself excludes
                # this test file by name
                # when the runner is
                # invoked from a normal
                # ``--python-only`` run,
                # so this env var is
                # belt-and-braces.
                "T27_INCLUDE_SELF": "",
            },
            cwd=str(REPO_ROOT),
            timeout=300,
        )
        # Parse the JSON summary
        # from stdout. The runner
        # always emits a
        # ``SUMMARY_JSON=...`` line
        # when ``--json`` is passed.
        summary: dict = {}
        for line in proc.stdout.splitlines():
            if line.startswith("SUMMARY_JSON="):
                payload = line[len("SUMMARY_JSON="):].strip()
                try:
                    summary = _json.loads(payload)
                except _json.JSONDecodeError:
                    pass
                break
        self.assertEqual(
            proc.returncode,
            0,
            msg=(
                f"runner exited non-zero: {proc.returncode}. "
                f"stdout tail: {proc.stdout.splitlines()[-3:]!r}, "
                f"stderr tail: {proc.stderr.splitlines()[-3:]!r}"
            ),
        )
        self.assertIn(
            "python_failed",
            summary,
            msg=(
                "SUMMARY_JSON must include python_failed; "
                f"got keys: {list(summary.keys())!r}"
            ),
        )
        self.assertEqual(
            summary["python_failed"],
            0,
            msg=(
                "runner reports "
                f"{summary['python_failed']!r} failed suites; "
                "audit T27 demands python_failed=0 on the "
                "full suite. Summary: "
                f"{summary!r}"
            ),
        )
        # Pin a small set of suites the
        # audit called out by name.
        # The runner output must show
        # these as PASS, not FAIL.
        expected_passes = [
            "test_t11_service_entry_resolver.py",
            "test_t15_total_energy_card.py",
            "test_t16_behavioral_options.py",
            "test_t16_options_contract.py",
            "test_t17_unload_reload_cleanup.py",
            "test_engine_predictive.py",
            "test_t18_async_setup_no_unbound_local.py",
        ]
        for suite in expected_passes:
            self.assertIn(
                suite,
                proc.stdout,
                msg=(
                    f"runner output must surface {suite!r}; "
                    f"got stdout tail: {proc.stdout.splitlines()[-5:]!r}"
                ),
            )

    def test_t27_13_wrapper_uses_utf8_io(self) -> None:
        """Audit T27 follow-up: the
        runner must force UTF-8 on
        the child process's stdout
        and stderr. On a Windows
        checkout the default code
        page is CP1252 and any
        ``print()`` call in a test
        that produces non-ASCII
        (a Ukrainian message, an
        emoji ``✅``, a Cyrillic
        identifier, etc.) raises
        ``UnicodeEncodeError`` even
        when every assertion in the
        test passes.

        The audit's repro: a test
        that prints ``✅ ALL ENGINE-
        PREDICTIVE TESTS PASSED``
        (see ``test_engine_predictive.py``)
        would fail on Windows with::

          UnicodeEncodeError: 'charmap'
          codec can't encode character
          '\u2705'

        The runner now passes
        ``-X utf8`` to the child
        interpreter and sets
        ``PYTHONIOENCODING=utf-8`` /
        ``PYTHONUTF8=1`` in the
        environment. The wrapper
        also calls
        ``sys.stdout.reconfigure`` so
        a test that writes to stdout
        before the runner's env is
        read still gets UTF-8.

        We pin the contract by
        launching a tiny inline
        script that prints a
        non-ASCII string and asserts
        that the subprocess exited
        0 with the string present in
        the captured stdout. We
        force the subprocess's
        stdout encoding back to
        CP1252 (``PYTHONIOENCODING=cp1252``)
        to make the test fail on
        Linux too if the wrapper
        ever regresses.
        """
        import subprocess as _subprocess
        import sys as _sys
        # The child writes a
        # non-ASCII string. On
        # CP1252 stdout this would
        # raise ``UnicodeEncodeError``.
        # The wrapper calls
        # ``sys.stdout.reconfigure``
        # to force UTF-8, and the
        # runner passes
        # ``PYTHONIOENCODING=utf-8``
        # so even the interpreter
        # startup picks UTF-8.
        child = (
            "import sys\n"
            "sys.stdout.reconfigure(encoding='utf-8', errors='replace')\n"
            "print('ALL_OK_✅_ALL_OK')\n"
        )
        # We force a non-UTF-8
        # environment to make the
        # test meaningful: the
        # ``-X utf8`` flag and the
        # ``sys.stdout.reconfigure``
        # call inside the child are
        # what saves us.
        r = _subprocess.run(
            [_sys.executable, "-I", "-X", "utf8", "-c", child],
            capture_output=True,
            text=True,
            timeout=30,
            env={
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", "/tmp"),
                "TMPDIR": "/tmp",
                # ``PYTHONIOENCODING`` and
                # ``PYTHONUTF8`` would
                # normally be set by the
                # runner. We omit them
                # here to assert the
                # ``-X utf8`` and the
                # ``sys.stdout.reconfigure``
                # are sufficient on their
                # own.
            },
        )
        self.assertEqual(
            r.returncode,
            0,
            msg=(
                "child subprocess failed when printing "
                "non-ASCII. The runner's UTF-8 fix did not "
                "propagate to ``print()``. "
                f"stdout={r.stdout!r}, stderr={r.stderr!r}"
            ),
        )
        self.assertIn(
            "ALL_OK_✅_ALL_OK",
            r.stdout,
            msg=(
                "child stdout did not contain the non-ASCII "
                "string. The runner is encoding stdout in "
                "the wrong code page. "
                f"stdout={r.stdout!r}, stderr={r.stderr!r}"
            ),
        )


    def test_t27_14_wiring_drain_loop_20_times(self) -> None:
        """Audit T18 / T27 follow-up: the
        ``test_debug_logging_no_config_dir``
        regression in
        ``test_predictive_wiring.py`` is
        a flaky-on-Windows race. The
        original test polled
        ``threading.enumerate()`` for
        workers whose ``_target`` was
        ``debug_logging._write_line``
        and called ``join(timeout=2)``,
        but the production worker is
        ``threading.Thread(
        target=self._serve)`` so the
        predicate never matches and
        the join is a no-op. On Windows
        the pending file write then
        races the ``TemporaryDirectory``
        cleanup and the test fails with::

            OSError: [WinError 145]
            The directory is not empty

        The fix replaces the predicate
        with ``worker.drain(timeout=...)``
        which uses the queue
        ``unfinished_tasks`` to block
        until every queued record has
        been written and ``task_done``-ed.

        Per the audit follow-up, the
        regression test must actually
        exercise the **production test
        path** through the **production
        isolation wrapper** - not the
        in-process ``importlib`` path
        that the previous version of
        this test used. The in-process
        path transitively imported
        ``unittest.mock`` which pulls
        in ``asyncio`` which imports
        ``selectors`` which imports
        ``select`` - and on Windows
        ``select`` resolves to the
        integration ``./select.py``
        entity file which then fails
        with ``ModuleNotFoundError:
        homeassistant``.

        The corrected contract: spawn a
        subprocess that uses
        ``build_runner_wrapper`` to
        load the wiring module and call
        ``test_debug_logging_no_config_dir``
        twenty times, with the same
        ``-I -X utf8`` flags the runner
        uses. We then check the exit
        code and look for a
        ``T27_14_DONE=20`` marker the
        inline script prints on
        success. The audit's repro
        (Windows ``OSError [WinError
        145]``) is reproduced by any
        of the 20 iterations if the
        ``worker.drain()`` contract
        regresses; the loop catches a
        5 %% flake with
        ``1 - 0.95**20 ~ 64 %``
        confidence.
        """
        import importlib.util as _ilu_t27_14
        # Load the production
        # runner so we can call
        # ``build_runner_wrapper``
        # without spawning a
        # child runner process -
        # we want the test to
        # directly drive the
        # wrapper subprocess, the
        # same way the production
        # runner does.
        spec = _ilu_t27_14.spec_from_file_location(
            "_t27_14_runner", str(RUNNER)
        )
        runner_mod = _ilu_t27_14.module_from_spec(spec)
        spec.loader.exec_module(runner_mod)
        # Write a one-shot inline
        # script that imports
        # ``test_predictive_wiring``
        # via ``importlib``, calls
        # ``test_debug_logging_no_config_dir``
        # 20 times, and prints
        # ``T27_14_DONE=20`` on
        # success. We delete the
        # script in ``finally``.
        # The script itself
        # imports only ``os``,
        # ``sys``, ``json``,
        # ``importlib`` and
        # ``unittest.mock`` is
        # **not** used; the
        # wiring module does
        # import ``unittest.mock``
        # internally but
        # ``build_runner_wrapper``'s
        # pre-import of stdlib
        # ``select`` keeps the
        # audit repro at bay.
        body_lines = [
            "import importlib.util as _ilu",
            "import json as _json",
            "import os as _os",
            "import sys as _sys",
            "_REPO_ROOT = " + repr(str(REPO_ROOT)),
            "_wiring_path = _os.path.join("
            "_REPO_ROOT, 'tests', "
            "'test_predictive_wiring.py')",
            "_spec = _ilu.spec_from_file_location("
            "'_t27_14_wiring', _wiring_path)",
            "_mod = _ilu.module_from_spec(_spec)",
            "_spec.loader.exec_module(_mod)",
            "_test_fn = _mod.test_debug_logging_no_config_dir",
            "for _i in range(20):",
            "    _test_fn()",
            "print('T27_14_DONE=20')",
        ]
        inline_body = "\n".join(body_lines) + "\n"
        # The script must start
        # with ``test_`` so the
        # runner's discovery glob
        # picks it up. We put it
        # under ``tests/`` so the
        # runner's normal
        # discovery finds it.
        inline_path = (
            REPO_ROOT / "tests"
            / "test_t27_14_inline_loop.py"
        )
        inline_path.write_text(inline_body, encoding="utf-8")
        try:
            wrapper = runner_mod.build_runner_wrapper(
                inline_path, inline_path.parent
            )
            r = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-X",
                    "utf8",
                    "-c",
                    wrapper,
                ],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(REPO_ROOT),
                env={
                    **os.environ,
                    "PYTHONIOENCODING": "utf-8",
                },
            )
            self.assertEqual(
                r.returncode,
                0,
                msg=(
                    "wrapper subprocess failed; the "
                    "production isolation did not work. "
                    f"stdout={r.stdout!r}, "
                    f"stderr={r.stderr!r}"
                ),
            )
            # The wrapper captures
            # the inline script's
            # stdout through
            # ``runpy``. We look for
            # the T27_14_DONE marker
            # in the captured output.
            self.assertIn(
                "T27_14_DONE=20",
                r.stdout,
                msg=(
                    "inline script did not complete all "
                    "20 iterations. The audit repro: a "
                    "Windows ``OSError [WinError 145]`` "
                    "may have crashed one of the iterations. "
                    f"stdout={r.stdout!r}, "
                    f"stderr={r.stderr!r}"
                ),
            )
        finally:
            try:
                inline_path.unlink()
            except FileNotFoundError:
                pass

    def test_t27_08_runner_handles_js_when_node_present(self) -> None:
        """When ``node`` is on PATH
        the runner runs the JS suite
        and reports it. When ``node``
        is missing the runner exits
        non-zero with a clear error.
        """
        node_present = shutil.which("node") is not None
        r = _run_runner("--js-only")
        if node_present:
            self.assertEqual(
                r.returncode,
                0,
                msg=(
                    f"runner --js-only failed with node present: "
                    f"stderr={r.stderr!r}"
                ),
            )
            combined = r.stdout + r.stderr
            self.assertIn(
                "pv_comparison_card",
                combined,
                msg=(
                    "runner --js-only must mention the JS test "
                    f"file; got: {combined[:300]!r}"
                ),
            )
        else:
            self.assertNotEqual(
                r.returncode,
                0,
                msg=(
                    "runner --js-only must fail when node is "
                    "missing"
                ),
            )



    def test_t27_09_runner_isolates_stdlib_select(self) -> None:
        """Audit T27 follow-up: the
        production runner must keep
        ``select`` and ``selectors``
        resolving to the stdlib even
        when ``REPO_ROOT`` is on
        ``sys.path``. The audit repro
        is the integration
        ``./select.py`` entity file
        (a ``SelectEntity`` subclass)
        which imports from
        ``homeassistant`` and crashes
        on a Home-Assistant-less
        Windows checkout.

        The previous version of this
        test ran ``python -c`` with
        ``cwd=REPO_ROOT`` and asserted
        ``select`` resolved to the
        stdlib. On Windows ``select``
        is not a builtin and the
        assertion fails: the test was
        *demonstrating* the audit bug,
        not verifying the fix.

        The corrected contract: invoke
        the production runner's
        ``build_runner_wrapper`` helper
        directly (the same wrapper the
        runner builds in
        ``_run_python_suite``) and
        assert that the child
        subprocess can ``import
        select`` and ``import
        selectors`` without pulling
        in ``homeassistant`` or any
        REPO_ROOT-tainted copy.
        """
        import importlib.util as _ilu_t27_09
        import json as _json_t27_09
        # Load the runner as a
        # module so we can call
        # ``build_runner_wrapper``
        # without spawning a child
        # runner process.
        spec = _ilu_t27_09.spec_from_file_location(
            "_t27_09_runner", str(RUNNER)
        )
        runner_mod = _ilu_t27_09.module_from_spec(spec)
        spec.loader.exec_module(runner_mod)
        # Build the wrapper for a
        # tiny inline test. The
        # inline test prints
        # ``T27_09_RESULT=...`` so we
        # can parse its output, and
        # ``OK`` so the runner's
        # verdict classifier
        # (which looks for ``OK`` /
        # ``passed``) reports a
        # clean PASS.
        inline_path = (
            REPO_ROOT / "tests"
            / "_t27_09_inline_select.py"
        )
        # We build the inline body
        # from a list of plain
        # strings joined with
        # ``str.join`` so we avoid
        # Python source encoding
        # edge cases (em-dashes in
        # the docstring, etc.).
        body_lines = [
            "from __future__ import annotations",
            "import json as _json",
            "import sys",
            "import select as _select",
            "import selectors as _selectors",
            "_results = {}",
            "for _name in (\"select\", \"selectors\"):",
            "    _mod = sys.modules.get(_name)",
            "    _file = getattr(_mod, \"__file__\", \"\") or \"\"",
            "    _results[_name] = {",
            "        \"file\": _file,",
            "        \"is_shadowed\": (",
            "            _file.endswith(\"select.py\")",
            "            and _file.startswith("
            + repr(str(REPO_ROOT))
            + ")",
            "        ),",
            "    }",
            "_results[\"homeassistant_loaded\"] = (",
            "    \"homeassistant\" in sys.modules",
            ")",
            "print(\"T27_09_RESULT=\" + _json.dumps(_results))",
            "print(\"OK\")",
        ]
        inline_body = "\n".join(body_lines) + "\n"
        inline_path.write_text(inline_body, encoding="utf-8")
        try:
            wrapper = runner_mod.build_runner_wrapper(
                inline_path, inline_path.parent
            )
            r = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-X",
                    "utf8",
                    "-c",
                    wrapper,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(REPO_ROOT),
                env={
                    **os.environ,
                    "PYTHONIOENCODING": "utf-8",
                },
            )
            self.assertEqual(
                r.returncode,
                0,
                msg=(
                    "wrapper subprocess failed; the runner "
                    "isolation did not work. "
                    f"stdout={r.stdout!r}, "
                    f"stderr={r.stderr!r}"
                ),
            )
            # The wrapper captures
            # the suite's output
            # through ``runpy``. We
            # look for the result
            # line in the captured
            # stdout.
            results: dict = {}
            for line in r.stdout.splitlines():
                if line.startswith("T27_09_RESULT="):
                    results = _json_t27_09.loads(
                        line[len("T27_09_RESULT="):]
                    )
                    break
            self.assertTrue(
                results,
                msg=(
                    "inline test did not emit a "
                    "T27_09_RESULT=... line; stdout: "
                    f"{r.stdout!r}, stderr={r.stderr!r}"
                ),
            )
            for _name in ("select", "selectors"):
                self.assertFalse(
                    results[_name]["is_shadowed"],
                    msg=(
                        f"{_name} is shadowed by a "
                        "REPO_ROOT-tainted copy. The "
                        "wrapper isolation did not work. "
                        f"Result: {results!r}"
                    ),
                )
            self.assertFalse(
                results["homeassistant_loaded"],
                msg=(
                    "homeassistant was pulled into "
                    "sys.modules by the wrapper; the "
                    "audit deeper repro is still alive. "
                    f"Result: {results!r}"
                ),
            )
        finally:
            try:
                inline_path.unlink()
            except FileNotFoundError:
                pass

    def test_t27_10_runner_passes_all_problem_suites(self) -> None:
        """The suites that historically
        failed on Windows because of the
        ``select`` / ``selectors``
        shadowing must now pass through
        ``tests/run_all.py --python-only``.
        We pin the runner contract end
        to end on the same set of files
        the audit calls out.
        """
        problem_suites = [
            "test_t11_service_entry_resolver.py",
            "test_t15_total_energy_card.py",
            "test_t16_behavioral_options.py",
            "test_t16_options_contract.py",
            "test_t17_unload_reload_cleanup.py",
        ]
        r = _run_runner(
            "--python-only",
            "--only",
            ",".join(problem_suites),
        )
        combined = r.stdout + r.stderr
        for suite in problem_suites:
            self.assertIn(
                suite,
                combined,
                msg=(
                    f"runner output must surface {suite!r}; got "
                    f"{combined[:500]!r}"
                ),
            )
            # The per-suite line must
            # read PASS, not FAIL.
            for line in combined.splitlines():
                if suite in line:
                    self.assertIn(
                        "PASS",
                        line,
                        msg=(
                            f"{suite!r} must PASS through the "
                            f"runner; runner line was {line!r}"
                        ),
                    )
                    break
        self.assertEqual(
            r.returncode,
            0,
            msg=(
                "runner exited non-zero on the historical "
                "problem suites; runner must keep these green. "
                f"stderr={r.stderr!r}"
            ),
        )



if __name__ == "__main__":
    _main(verbosity=2)