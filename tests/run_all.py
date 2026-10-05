#!/usr/bin/env python3
"""T27 — one reproducible test runner.

Audit T27: there must be a single
runner that covers all Python test
suites and the JS suite on Windows
and Linux. Any missing runner or
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

Audit T27 follow-up (Windows-safe isolation)
============================================
A Windows checkout of this integration
ships the entity file
``./select.py`` (a ``SelectEntity``
subclass). When a child test is
launched with ``cwd=REPO_ROOT``,
``sys.path[0]`` resolves to
``REPO_ROOT`` and the stdlib
``select`` module — *not* a builtin
on Windows — gets shadowed by
``./select.py``. ``./select.py`` then
imports ``homeassistant``, and the
child explodes with::

    ModuleNotFoundError: No module named 'homeassistant'

The audit's reproducer is
``import stdlib selectors`` pulling
HA via the shadow. The fix is to
launch child tests through a
``-c`` wrapper that:

  1. Injects ``REPO_ROOT`` into
     ``sys.path`` at position 0 so
     ``import hems`` /
     ``import coordinator`` /
     ``import conftest`` resolve
     normally.
  2. Deletes any module from
     ``sys.modules`` whose ``__file__``
     resolves into ``REPO_ROOT``. This
     is the defensive unshadow step:
     even if the integration entity
     files somehow get imported first,
     the wrapper strips them out so
     stdlib ``select`` and ``selectors``
     fall back to their real location.
  3. ``runpy.run_path(suite, run_name="__main__")`` — the suite
     runs as the ``__main__`` module,
     so its ``if __name__ == "__main__"``
     blocks execute.

The previous attempt used
``cwd=REPO_ROOT/tests`` plus a
``PYTHONPATH`` prepend. On Linux that
works because ``select`` is a
builtin; on Windows it is not, and
the ``PYTHONPATH`` entry shadows it.
The wrapper approach removes that
ambiguity entirely.
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
    """Run one Python test file via
    a ``-c`` wrapper that:

      * prepends ``REPO_ROOT`` to
        ``sys.path``;
      * strips any module from
        ``sys.modules`` whose origin
        resolves into ``REPO_ROOT``
        (defensive unshadow);
      * invokes the suite through
        ``runpy.run_path(suite,
        run_name="__main__")``.

    The wrapper is the audit's
    Windows-safe isolation layer.
    """
    # Build the wrapper. The wrapper
    # lives in a string so it cannot
    # be shadowed by the integration
    # entity files; the entity files
    # only get a chance to import if
    # some downstream code does so
    # *after* the wrapper has run.
    suite_rel = suite.resolve()
    suite_dir = suite_rel.parent
    wrapper = (
        "import sys, runpy\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        # Some test files import helpers
        # from ``tests/`` directly (e.g.
        # ``pv_test_support``). Add the
        # test directory to ``sys.path``
        # so those imports resolve.
        f"sys.path.insert(0, {str(suite_dir)!r})\n"
        # Defensive unshadow: any module
        # already loaded from
        # ``REPO_ROOT`` is removed so a
        # later ``import <name>`` falls
        # back to the stdlib location.
        # This protects against the
        # audit's repro:
        # ``./select.py`` shadowing the
        # stdlib ``select`` on Windows.
        "_repo_root = "
        f"{str(REPO_ROOT)!r}\n"
        "for _name, _mod in list(sys.modules.items()):\n"
        "    _f = getattr(_mod, '__file__', None)\n"
        "    if _f and _f.startswith(_repo_root):\n"
        "        sys.modules.pop(_name, None)\n"
        f"sys.argv[0] = {str(suite_rel)!r}\n"
        f"runpy.run_path({str(suite_rel)!r}, run_name='__main__')\n"
    )
    try:
        r = subprocess.run(
            [py, "-I", "-c", wrapper],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(suite_dir),
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
            wanted = {
                name.strip()
                for name in args.only.split(",")
                if name.strip()
            }
            suites = [s for s in suites if s.name in wanted]
            if not suites:
                print(
                    f"FATAL: --only matched 0 of {len(suites)} suites; "
                    f"requested={sorted(wanted)}",
                    file=sys.stderr,
                )
                return 3
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