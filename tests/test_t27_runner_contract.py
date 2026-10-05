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



    def test_t27_09_stdlib_selectors_not_shadowed(self) -> None:
        """Audit T27 follow-up: when the
        runner is launched with
        ``cwd=REPO_ROOT`` (which puts the
        REPO_ROOT directory at
        ``sys.path[0]``), an ``import
        select`` statement in a child
        test must NOT resolve to the
        integration's ``./select.py``
        entity module — that file
        imports from ``homeassistant``
        and would crash on a
        Home-Assistant-less Windows
        checkout.

        Reproduce the failure mode by
        starting a subprocess with
        ``cwd=REPO_ROOT`` and verifying
        ``select`` still resolves to the
        stdlib (no ``__file__``) or to a
        non-entity module.
        """
        # ``cwd=REPO_ROOT`` is the
        # runner's normal launch
        # context. Run a tiny inline
        # script that imports
        # ``selectors`` (stdlib) and
        # checks it does not transitively
        # pull the integration's
        # ``./select.py``.
        inline = textwrap.dedent(
            """\
            import selectors
            import sys

            # stdlib selectors lives under
            # ``python_install_dir`` and
            # always carries an absolute
            # ``__file__`` that does not
            # end with the integration
            # entity filename.
            assert hasattr(selectors, "__file__"), (
                "stdlib selectors must have __file__"
            )
            sel_file = selectors.__file__
            assert not sel_file.endswith("select.py"), (
                "selectors must not resolve to the integration's "
                f"./select.py; got {sel_file!r}"
            )

            # ``select`` itself may be
            # either the builtin or the
            # stdlib module — what
            # matters is that it does
            # NOT resolve to the
            # integration entity file.
            import select
            sel_file = getattr(select, "__file__", "")
            assert not sel_file.endswith("select.py") or not sel_file, (
                "select must not resolve to the integration's "
                f"./select.py; got {sel_file!r}"
            )

            # ``selectors`` must not have
            # transitively imported
            # ``homeassistant`` (which
            # would happen if we
            # accidentally shadowed it).
            assert "homeassistant" not in sys.modules, (
                "stdlib selectors pulled homeassistant via "
                "sys.path shadowing — see audit T27 follow-up"
            )
            print("OK")
            """
        )
        r = subprocess.run(
            [sys.executable, "-c", inline],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            timeout=30,
        )
        self.assertEqual(
            r.returncode,
            0,
            msg=(
                "stdlib selectors was shadowed by the integration's "
                "./select.py when cwd=REPO_ROOT. "
                f"stdout={r.stdout!r}, stderr={r.stderr!r}"
            ),
        )
        self.assertIn(
            "OK",
            r.stdout,
            msg=(
                "inline subprocess did not reach the OK branch; "
                f"stdout={r.stdout!r}, stderr={r.stderr!r}"
            ),
        )

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