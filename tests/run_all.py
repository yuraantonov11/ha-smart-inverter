#!/usr/bin/env python3
"""T27 — one reproducible test runner.

Audit T27: there must be a single
runner that covers all Python test
suites and the JS suite on Windows and
Linux. Any missing runner or
dependency must produce a clear error
and a non-zero exit code. Zero tests
must NOT count as a pass.

Usage::

    python tests/run_all.py            # full run
    python tests/run_all.py --python-only
    python tests/run_all.py --js-only
    python tests/run_all.py --json     # emit SUMMARY_JSON=...
    python tests/run_all.py --dir <path>
    python tests/run_all.py --only <file>

Exit codes:
    0   every discovered suite passed
    1   one or more suites failed
    2   missing required interpreter / runner
    3   zero tests discovered in the target directory
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _python_executable() -> str:
    """Return the Python interpreter
    used to run child tests. Honours
    ``PYTHON`` if set; otherwise uses
    ``sys.executable`` (or the
    override ``_PYTHON_EXECUTABLE_FALLBACK``)
    if it can spawn a working
    interpreter, else ``python3`` /
    ``python``.
    """
    # Audit T27: the runner must
    # surface a clear error when no
    # Python interpreter is
    # available. The
    # ``T27_FORCE_NO_PYTHON`` env
    # variable lets the contract test
    # exercise this branch without
    # rebuilding the runner.
    if os.environ.get("T27_FORCE_NO_PYTHON") == "1":
        raise RuntimeError(
            "no working Python interpreter found on PATH; "
            "T27_FORCE_NO_PYTHON=1 forced the failure path"
        )
    candidates: list[str] = []
    if os.environ.get("PYTHON"):
        candidates.append(os.environ["PYTHON"])
    fallback = os.environ.get("_PYTHON_EXECUTABLE_FALLBACK")
    if fallback is not None:
        candidates.append(fallback)
    if sys.executable:
        candidates.append(sys.executable)
    for name in ("python3", "python"):
        if name not in candidates:
            candidates.append(name)
    for cand in candidates:
        if not cand:
            continue
        try:
            r = subprocess.run(
                [cand, "-c", "import sys; sys.exit(0)"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0:
                return cand
        except (OSError, subprocess.TimeoutExpired):
            continue
    raise RuntimeError(
        "no working Python interpreter found on PATH; "
        "install python3 or set PYTHON=/path/to/python"
    )


def _node_executable() -> str | None:
    """Return ``node`` if it is
    available. Used by ``--js-only``.
    """
    node = shutil.which("node")
    return node


def _discover_python_suites(target: Path) -> list[Path]:
    """Return all Python test files
    under ``target`` that look like
    unittest modules.

    ``test_t27_runner_contract.py`` is
    excluded by default — running the
    contract test through the runner
    re-enters the runner via
    subprocess and creates an
    unbounded recursion. Set the
    environment variable
    ``T27_INCLUDE_SELF=1`` to
    override the exclusion when you
    *want* the contract test to be
    exercised by the runner (CI uses
    this with a short timeout).
    """
    if not target.exists() or not target.is_dir():
        return []
    include_self = os.environ.get("T27_INCLUDE_SELF") == "1"
    return sorted(
        s
        for s in target.glob("test_*.py")
        if include_self
        or s.name not in {"test_t27_runner_contract.py"}
    )


def _run_python_suite(
    py: str, suite: Path, label: str
) -> tuple[bool, str]:
    """Run one Python test file.
    Returns ``(passed, summary)``."""
    try:
        r = subprocess.run(
            [py, str(suite)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, f"{label}: TIMEOUT after 120s"
    # unittest writes its verdict to
    # stdout (text mode) but many of
    # our test files call
    # ``logging.error()`` before the
    # verdict, which goes to stderr.
    # Look at both streams.
    stdout_lines = r.stdout.strip().splitlines() if r.stdout else []
    stderr_lines = r.stderr.strip().splitlines() if r.stderr else []
    # Prefer stdout for the verdict;
    # fall back to stderr.
    verdict = ""
    for line in list(reversed(stdout_lines)) + list(reversed(stderr_lines)):
        lowered = line.lower()
        if (
            "ok" in lowered
            or "passed" in lowered
            or "0 failed" in lowered
            or "all " in lowered and "passed" in lowered
        ):
            verdict = line
            break
    if r.returncode != 0:
        return False, f"{label}: FAIL exit={r.returncode}"
    if verdict:
        return True, f"{label}: PASS"
    return False, (
        f"{label}: FAIL no verdict; "
        f"stdout_tail={stdout_lines[-3:]!r}, "
        f"stderr_tail={stderr_lines[-3:]!r}"
    )


def _run_js_suite() -> tuple[bool, str]:
    """Run the JS suite. ``node`` is
    the only supported runner — the
    audit forbids silently passing
    when it is missing.
    """
    node = _node_executable()
    if node is None:
        return False, (
            "JS suite (test_pv_comparison_card.cjs): "
            "node executable not found on PATH; "
            "install Node.js or skip --js-only"
        )
    js = REPO_ROOT / "tests" / "test_pv_comparison_card.cjs"
    if not js.exists():
        return False, (
            f"JS suite (test_pv_comparison_card.cjs): "
            f"missing {js}"
        )
    r = subprocess.run(
        [node, str(js)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    if r.returncode != 0:
        return False, (
            f"JS suite (test_pv_comparison_card.cjs): "
            f"FAIL exit={r.returncode}"
        )
    return True, "JS suite (test_pv_comparison_card.cjs): PASS"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="run_all.py",
        description="T27 unified test runner",
    )
    parser.add_argument(
        "--python-only",
        action="store_true",
        help="run only the Python suites",
    )
    parser.add_argument(
        "--js-only",
        action="store_true",
        help="run only the JS suite",
    )
    parser.add_argument(
        "--dir",
        default=str(REPO_ROOT / "tests"),
        help="directory to discover Python suites",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="run only this Python suite (filename)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a SUMMARY_JSON=... line at the end",
    )
    args = parser.parse_args()

    summary: dict[str, object] = {
        "python_files": 0,
        "python_passed": 0,
        "python_failed": 0,
        "python_failed_files": [],
        "js_passed": False,
    }

    failed_count = 0

    if not args.js_only:
        try:
            py = _python_executable()
        except RuntimeError as exc:
            print(f"FATAL: {exc}", file=sys.stderr)
            if args.json:
                summary["python_failed_files"].append("__no_python__")  # type: ignore[attr-defined]
                print("SUMMARY_JSON=" + json.dumps(summary, sort_keys=True))
            return 2
        target = Path(args.dir)
        if not target.exists():
            print(
                f"FATAL: tests directory {target} does not exist",
                file=sys.stderr,
            )
            return 2
        suites = _discover_python_suites(target)
        if args.only:
            suites = [s for s in suites if s.name == args.only]
        summary["python_files"] = len(suites)
        if not suites:
            print(
                f"FATAL: 0 Python test files discovered in {target}",
                file=sys.stderr,
            )
            return 3
        for suite in suites:
            label = suite.name
            ok, msg = _run_python_suite(py, suite, label)
            print(msg)
            if ok:
                summary["python_passed"] += 1  # type: ignore[operator]
            else:
                summary["python_failed"] += 1  # type: ignore[operator]
                failed_count += 1
                summary["python_failed_files"].append(label)  # type: ignore[attr-defined]

    if not args.python_only:
        ok, msg = _run_js_suite()
        print(msg)
        summary["js_passed"] = ok
        if not ok:
            failed_count += 1

    if args.json:
        print("SUMMARY_JSON=" + json.dumps(summary, sort_keys=True))

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())