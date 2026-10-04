"""Pytest conftest: make the integration
importable as a package so test files can
do ``from hems.options_helpers import …``
without setting ``PYTHONPATH`` manually.

This conftest is loaded by pytest. unittest
(``python -m unittest tests.test_…``) does
not pick it up automatically, so the
behavioural test file also prepends the
repo root in its own bootstrap.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
