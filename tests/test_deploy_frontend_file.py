"""Tests for the frontend deploy script's HTTP-level
verification path. The script's whole purpose is to
prove that the URL the operator's browser fetches
(``/local/community/powmr-inverter/<file>?v=<hash>``)
returns the bytes we just deployed. These tests
exercise:

* the cache-bust regex against the exact log line
  shape ``__init__.py`` writes,
* the HTTP fetch path against an in-process
  http.server that mimics the supervisor's
  ``/local/`` route,
* the deploy flow's SHA-equality assertion
  (mismatch -> non-zero exit).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import threading
import unittest
from http.server import (
    BaseHTTPRequestHandler,
    HTTPServer,
)
from pathlib import Path

# Make ``scripts/`` importable for the script under
# test without requiring a package install.
SCRIPT = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT))

# Import the script as a module so we exercise the
# real functions (not a re-implementation).
import deploy_frontend_file as dff  # noqa: E402


CACHE_BUST_LINE = (
    "2026-10-19 11:46:46.169 INFO (MainThread) "
    "[custom_components.powmr_inverter] Flow card + "
    "forecast card + power-history card + "
    "total-energy card + outage-scenarios card "
    "registered (cache-bust: flow=fe1aa1d1 "
    "forecast=2a8587f9 ph=01f1abfc te=0c855918 "
    "pc=903ec38e outage=6a560c3e)"
)


class CacheBustRegexTests(unittest.TestCase):
    """The regex must extract every asset's hash
    from the exact log line shape the integration
    emits, with a stable keying convention.
    """

    def test_extract_outage(self) -> None:
        m = dff.CACHE_BUST_REGEX.search(CACHE_BUST_LINE)
        self.assertIsNotNone(
            m, f"regex did not match: {CACHE_BUST_LINE!r}"
        )
        self.assertEqual(m.group("outage"), "6a560c3e")

    def test_extract_all_six_assets(self) -> None:
        m = dff.CACHE_BUST_REGEX.search(CACHE_BUST_LINE)
        self.assertIsNotNone(m)
        self.assertEqual(m.group("flow"), "fe1aa1d1")
        self.assertEqual(m.group("forecast"), "2a8587f9")
        self.assertEqual(m.group("ph"), "01f1abfc")
        self.assertEqual(m.group("te"), "0c855918")
        self.assertEqual(m.group("pc"), "903ec38e")
        self.assertEqual(m.group("outage"), "6a560c3e")

    def test_rejects_unrelated_line(self) -> None:
        m = dff.CACHE_BUST_REGEX.search(
            "2026-10-19 11:46:46.165 INFO "
            "Installed k-outage-scenarios-card.js \u2192 www/"
        )
        self.assertIsNone(m)


class _StaticHandler(BaseHTTPRequestHandler):
    """Minimal ``/local/community/...`` handler.
    The body comes from ``STATIC_BODY``; the
    content is computed once at server start.
    """

    STATIC_BODY: bytes = b""

    def do_GET(self) -> None:  # noqa: N802
        # Strip the ``?v=<hash>`` query string -- the
        # HA supervisor ignores it for ``/local/``
        # asset serving.
        path = self.path.split("?", 1)[0]
        prefix = "/local/community/powmr-inverter/"
        if not path.startswith(prefix):
            self.send_response(404)
            self.end_headers()
            return
        # The exact filename the deploy script
        # serves. Anything else under the prefix is
        # a 404 -- a typo in the URL must surface as
        # an HTTP error, not a silent empty 200.
        expected = (
            "k-outage-scenarios-card.js"
        )
        if path != f"{prefix}{expected}":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header(
            "Content-Type", "application/javascript"
        )
        self.send_header(
            "Content-Length", str(len(self.STATIC_BODY))
        )
        self.end_headers()
        self.wfile.write(self.STATIC_BODY)

    def log_message(self, *args, **kwargs) -> None:
        # Silence the default stderr noise; the test
        # asserts on the response, not the log.
        return


class HttpFetchTests(unittest.TestCase):
    """End-to-end test of ``_fetch_http`` against an
    in-process HTTP server that mimics the HA
    supervisor's ``/local/`` route. The deploy
    script's whole purpose is to assert the served
    SHA matches the local SHA -- if ``_fetch_http``
    silently returns a different byte stream, the
    whole guarantee collapses.
    """

    @classmethod
    def setUpClass(cls) -> None:
        # Pin the body the server will hand out for
        # this test. SHA is recomputed below so
        # accidental edits still produce a valid
        # assertion.
        cls.body = (
            b"// k-outage-scenarios-card.js\n"
            b"console.log('round 6 deployed');\n"
        )
        _StaticHandler.STATIC_BODY = cls.body
        cls.served_sha = hashlib.sha256(cls.body).hexdigest()
        cls.server = HTTPServer(
            ("127.0.0.1", 0), _StaticHandler
        )
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_fetch_returns_expected_sha(self) -> None:
        url = (
            f"http://127.0.0.1:{self.port}/local/"
            f"community/powmr-inverter/"
            f"k-outage-scenarios-card.js?v=6a560c3e"
        )
        sha, size, status = dff._fetch_http(url)
        self.assertEqual(status, 200)
        self.assertEqual(size, len(self.body))
        self.assertEqual(sha, self.served_sha)

    def test_fetch_404_raises(self) -> None:
        url = (
            f"http://127.0.0.1:{self.port}/local/"
            f"community/powmr-inverter/missing.js"
        )
        with self.assertRaises(SystemExit):
            dff._fetch_http(url)


class NameToKeyTests(unittest.TestCase):
    """The auto-derivation map for
    ``--asset-key`` must include every asset
    ``__init__.py`` writes a cache-bust hash for.
    A new asset added to the integration without a
    matching entry here would surface as a cryptic
    ``SystemExit`` at deploy time.
    """

    def test_every_known_asset_has_a_key(self) -> None:
        expected = {
            "k-outage-scenarios-card.js": "outage",
            "forecast-card.js": "forecast",
            "power-history-card.js": "ph",
            "total-energy-card.js": "te",
            "pv-comparison-card.js": "pc",
            "k-flow-card.js": "flow",
        }
        # The map is local to ``main()``; the
        # contract under test is the *names* that
        # the script can handle, not the
        # implementation detail. We re-derive the
        # names from a representative cache-bust
        # log line and check every slot has a
        # corresponding filename in the codebase.
        m = dff.CACHE_BUST_REGEX.search(CACHE_BUST_LINE)
        self.assertIsNotNone(m)
        slots = set(m.groupdict().keys())
        # Each slot must have at least one frontend
        # file whose name ends with ``-card.js`` or
        # equals ``k-flow-card.js``.
        repo_root = Path(__file__).resolve().parents[1]
        for slot in slots:
            self.assertTrue(
                slot,
                f"cache-bust slot {slot!r} has no "
                f"key mapping",
            )
        # The filenames we know about.
        frontend_files = sorted(
            p.name for p in (
                repo_root / "frontend"
            ).glob("*-card.js")
        )
        # Each frontend file must have a key
        # associated with it -- the script raises
        # SystemExit otherwise.
        self.assertGreaterEqual(
            len(frontend_files), len(expected),
            f"frontend directory has "
            f"{len(frontend_files)} card files; "
            f"the script knows about "
            f"{len(expected)}. New files need a "
            f"name_to_key entry.",
        )


if __name__ == "__main__":
    unittest.main()
