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


def build_runner_wrapper(suite: Path, suite_dir: Path) -> str:
    """Build the ``python -c`` wrapper
    string the runner passes to the
    child interpreter for every
    ``test_*.py`` suite.

    Audit T27 follow-up: this is a
    public seam so the T27 contract
    tests can invoke the *exact*
    wrapper the runner uses without
    spawning a child runner process.
    The wrapper:

      1. Pre-imports the stdlib
         ``select`` / ``selectors`` /
         ``socket`` / ``asyncio``
         modules **before**
         ``REPO_ROOT`` is added to
         ``sys.path`` so they cache in
         ``sys.modules`` and never
         re-scan ``sys.path`` later.
      2. Inserts ``suite_dir`` and
         ``REPO_ROOT`` into
         ``sys.path`` so the suite
         can ``import hems`` /
         ``import pv_test_support``.
      3. Strips any module from
         ``sys.modules`` whose
         ``__file__`` points into
         ``REPO_ROOT`` and whose name
         is in the shadow set.
      4. Re-binds the cached stdlib
         modules at the standard
         names so any later
         ``import select`` returns
         the stdlib module.
      5. Reconfigures
         ``sys.stdout`` / ``sys.stderr``
         to UTF-8 so tests that print
         non-ASCII do not raise
         ``UnicodeEncodeError`` on
         Windows.
      6. Runs the suite via
         ``runpy.run_path`` as
         ``__main__``.

    The function returns a single
    string suitable for
    ``subprocess.run([py, "-c", wrapper])``
    on either Windows or POSIX.
    """
    suite_rel = suite.resolve()
    return (
        "import sys\n"
        "import runpy\n"
        "import select as _stdlib_select\n"
        "import selectors as _stdlib_selectors\n"
        "import socket as _stdlib_socket\n"
        "import asyncio as _stdlib_asyncio\n"
        "sys.path.insert(0, " + repr(str(suite_dir)) + ")\n"
        "sys.path.insert(0, " + repr(str(REPO_ROOT)) + ")\n"
        "_repo_root = " + repr(str(REPO_ROOT)) + "\n"
        "_shadowed = {\n"
        "    'select', 'selectors', 'socket', 'asyncio',\n"
        "}\n"
        "for _name, _mod in list(sys.modules.items()):\n"
        "    _f = getattr(_mod, '__file__', None)\n"
        "    if _f and _f.startswith(_repo_root) and _name in _shadowed:\n"
        "        sys.modules.pop(_name, None)\n"
        "sys.modules['select'] = _stdlib_select\n"
        "sys.modules['selectors'] = _stdlib_selectors\n"
        "sys.modules['socket'] = _stdlib_socket\n"
        "sys.modules['asyncio'] = _stdlib_asyncio\n"
        # Force UTF-8 on the child's
        # stdout and stderr BEFORE
        # ``runpy.run_path`` so any
        # ``print()`` call inside the
        # suite (or in modules the
        # suite imports - for example
        # ``unittest.mock`` which
        # transitively imports
        # ``asyncio``) goes through
        # UTF-8 regardless of the
        # inherited code page. On
        # Windows the default is
        # CP1252 and tests that print
        # non-ASCII (a Ukrainian
        # message in a docstring, an
        # emoji like ``CHECK`` or
        # ``CROSS``, a Cyrillic
        # identifier) would raise
        # ``UnicodeEncodeError``
        # otherwise. We do this *before*
        # the suite runs so even
        # ``asyncio``'s own logging
        # output is UTF-8.
        "try:\n"
        "    sys.stdout.reconfigure(encoding='utf-8', errors='replace')\n"
        "    sys.stderr.reconfigure(encoding='utf-8', errors='replace')\n"
        "except Exception:\n"
        "    pass\n"
        "sys.argv[0] = " + repr(str(suite_rel)) + "\n"
        "runpy.run_path(" + repr(str(suite_rel)) + ", run_name='__main__')\n"
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
    # The audit's Windows repro is
    # ``import select`` resolving to
    # the integration's
    # ``./select.py`` entity file
    # because ``REPO_ROOT`` is on
    # ``sys.path``. The preloader
    # below defeats that by importing
    # the **stdlib** ``select`` (and
    # the modules Python's asyncio /
    # selectors machinery depend on)
    # **before** ``REPO_ROOT`` is
    # added to ``sys.path``. Once
    # cached, subsequent ``import``
    # statements short-circuit and
    # never re-scan ``sys.path``.
    #
    # The unshadow loop we previously
    # relied on is insufficient: it
    # only strips modules that were
    # already loaded by some prior
    # import. A fresh
    # ``import select`` in a child
    # test would still re-scan
    # ``sys.path`` and pick up the
    # integration's ``./select.py``.
    # The preloader closes that gap.
    # Delegate to the public
    # ``build_runner_wrapper``
    # helper so the T27
    # contract tests can invoke
    # the same wrapper the
    # runner uses without
    # spawning a child runner
    # process.
    wrapper = build_runner_wrapper(
        suite_rel, suite_dir
    )

    try:
        r = subprocess.run(
            [
                py,
                "-I",
                "-X",
                "utf8",
                "-c",
                wrapper,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(suite_dir),
            env={
                **os.environ,
                # Belt and braces: even
                # without ``-X utf8``,
                # this forces UTF-8 on
                # the child's I/O. The
                # ``-I`` flag we already
                # pass makes
                # ``PYTHONIOENCODING``
                # ignored unless the
                # variable is set in the
                # environment, which is
                # what this dict does.
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
            },
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