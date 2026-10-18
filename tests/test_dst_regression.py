"""Regression tests for the DST handling in
``_localInputToHaIso`` (DEFECT 4 round 4).

The card converts the operator's wall-clock input
(``YYYY-MM-DDTHH:MM`` from a ``datetime-local``
field) into an offset-aware ISO string. The
conversion MUST respect the HA timezone and
MUST NOT silently save a different hour when
the typed wall-clock is non-existent (DST
spring forward) or ambiguous (DST autumn
fallback).

The previous round (round 3) silently nudged
non-existent wall-clocks forward by one hour,
which the operator rejected: "не зберігай іншу
годину як введену користувачем". This file pins
the new contract:

  * Non-existent wall-clock: the helper returns
    ``{ iso: localValue, reason: "non_existent" }``
    (the typed wall-clock is preserved verbatim;
    the caller's interval validator surfaces
    the error to the operator).
  * Ambiguous wall-clock: the helper returns
    the LATER (standard-time) offset's ISO with
    ``{ reason: "ambiguous" }``.
  * Unambiguous wall-clock: standard conversion.

Tests run in the same Node runtime as the
production card (the ``.js`` file is loaded
into a JSDOM-like shim via Node's ``vm``
module). Each test asserts a specific edge
case.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JS_PATH = REPO_ROOT / "frontend" / "k-outage-scenarios-card.js"


_DST_HARNESS_TPL = textwrap.dedent(
    """
    'use strict';
    // Stub the DOM primitives the card class
    // extends. We only need them to evaluate
    // the source; the helper under test only
    // uses ``Intl.DateTimeFormat`` / ``Date`` /
    // ``Math``, not DOM.
    globalThis.HTMLElement = class HTMLElement {
        constructor() {
            this._shadow = {
                mode: 'open',
                innerHTML: '',
                querySelector: () => null,
                querySelectorAll: () => [],
            };
            this.shadowRoot = null;
            this.attachShadow = (opts) => {
                this.shadowRoot = this._shadow;
                return this._shadow;
            };
        }
    };
    globalThis.customElements = {
        get: () => undefined,
        define: () => undefined,
    };
    // Load the production card source and
    // extract just the helper we want to test
    // (``_localInputToHaIso`` is a prototype
    // method, so we instantiate a class and
    // call it through a stub).
    const fs = require('fs');
    const code = fs.readFileSync(%(js_path)s, 'utf8');
    // Strip the customElements.define call so
    // we don't pollute the global registry.
    const stripped = code.replace(
        /if\\s*\\(!customElements\\.get\\([^)]+\\)\\)\\s*\\{[\\s\\S]*?\\}\\s*\\n?/m,
        ''
    );
    // The card source uses a ``class``
    // declaration which has block scope and
    // does not leak to ``globalThis``. We
    // append a line that re-exports the
    // class to ``globalThis`` so the test
    // harness can instantiate it.
    const exportLine = 'globalThis.KOutageScenariosCard = KOutageScenariosCard;';
    // Use ``(0, eval)`` so the code runs in the
    // global scope and the class declaration
    // (``KOutageScenariosCard``) becomes a
    // global binding.
    (0, eval)(stripped + String.fromCharCode(10) + exportLine);
    // Build a minimal instance.
    const card = new globalThis.KOutageScenariosCard();
    card._haTz = process.env.HA_TZ || 'Europe/Kyiv';
    const out = card._localInputToHaIso(
        process.env.LOCAL_VALUE,
        process.env.HA_TZ || 'Europe/Kyiv'
    );
    console.log(JSON.stringify({
        iso: out.iso,
        reason: out.reason,
    }));
    """
)


def _run_node(harness: str) -> dict:
    """Run the harness script under Node and
    return the parsed JSON output.
    """
    env = os.environ.copy()
    # Force the Node helper to use a known
    # timezone so the test result is stable
    # across hosts.
    proc = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"node harness failed: {proc.stderr}"
        )
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError(
            f"node harness produced no output; "
            f"stderr={proc.stderr!r}"
        )
    return json.loads(out)


class DstRegressionTests(unittest.TestCase):
    """Regression tests for ``_localInputToHaIso``
    against the production card source.
    """

    def _resolve(self, local_value: str, tz: str) -> dict:
        harness = (
            _DST_HARNESS_TPL
            % {
                "js_path": repr(str(JS_PATH)),
            }
        )
        env = os.environ.copy()
        env["HA_TZ"] = tz
        env["LOCAL_VALUE"] = local_value
        proc = subprocess.run(
            ["node", "-e", harness],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
            timeout=15,
        )
        if proc.returncode != 0:
            self.fail(
                f"node harness failed: {proc.stderr}"
            )
        return json.loads(proc.stdout.strip())

    def test_kyiv_no_dst_artefacts(self):
        """Europe/Kyiv has no DST since 2014.
        ``2026-10-25T02:30`` is a valid wall-clock
        in Kyiv. The helper must return an
        offset-aware ISO that round-trips back
        to the same wall-clock in Kyiv.
        """
        import datetime as _dt
        from zoneinfo import ZoneInfo
        out = self._resolve("2026-10-25T02:30", "Europe/Kyiv")
        # The conversion preserves the typed
        # wall-clock: parse the returned ISO and
        # format it back in Kyiv -- must be
        # ``2026-10-25T02:30``.
        iso = out["iso"]
        if not (iso.endswith("Z") or "+" in iso[10:] or "-" in iso[10:]):
            self.fail(
                f"Europe/Kyiv 02:30 must return an "
                f"offset-aware ISO; got iso={iso!r}"
            )
        iso_parsed = iso.replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(iso_parsed)
        local = dt.astimezone(ZoneInfo("Europe/Kyiv"))
        self.assertEqual(
            local.strftime("%Y-%m-%dT%H:%M"),
            "2026-10-25T02:30",
            f"Europe/Kyiv 2026-10-25T02:30 must "
            f"round-trip to the same wall-clock; "
            f"got iso={out!r} -> local="
            f"{local.isoformat()!r}",
        )
        self.assertIn(
            out["reason"], (None, "ambiguous"),
            f"Kyiv has no DST; the typed wall-clock "
            f"must not be marked non_existent; got "
            f"reason={out['reason']!r}",
        )

    def test_berlin_autumn_ambiguous(self):
        """In Europe/Berlin, ``2026-10-25T02:30``
        is ambiguous: clocks fall back from
        03:00 CEST to 02:00 CET, so the same
        wall-clock exists in both offsets.
        The helper must pick the LATER
        (standard-time, CET, ``+01:00``) offset
        and surface ``reason: "ambiguous"``.
        """
        out = self._resolve("2026-10-25T02:30", "Europe/Berlin")
        import datetime as _dt
        from zoneinfo import ZoneInfo
        iso = out["iso"].replace("Z", "+00:00")
        parsed = _dt.datetime.fromisoformat(iso)
        local_dt = parsed.astimezone(ZoneInfo("Europe/Berlin"))
        # CET = ``+01:00`` in late October (after
        # the autumn DST end). The picked offset
        # should be +01:00, NOT +02:00 (CEST).
        offset_min = local_dt.utcoffset().total_seconds() / 60
        self.assertEqual(
            offset_min, 60,
            f"Berlin ambiguous 02:30 must pick the "
            f"later (CET, +01:00) offset; got "
            f"offset={offset_min}min (iso={out!r})",
        )
        self.assertEqual(
            out["reason"], "ambiguous",
            f"Berlin 02:30 on the DST-end day must "
            f"be marked ambiguous; got "
            f"reason={out['reason']!r}",
        )

    def test_berlin_spring_non_existent(self):
        """In Europe/Berlin, ``2026-03-29T02:30``
        is non-existent: clocks jump from 02:00
        CET to 03:00 CEST, so the wall-clock
        02:30 is skipped. The helper must NOT
        silently nudge to 03:30. The previous
        round did exactly that; the contract
        now is: the typed wall-clock is
        preserved verbatim and the reason is
        ``"non_existent"``.
        """
        out = self._resolve("2026-03-29T02:30", "Europe/Berlin")
        # The returned ``iso`` MUST be the typed
        # wall-clock verbatim (per operator
        # requirement: never save a different
        # hour). The reason MUST be
        # ``"non_existent"`` so the form's
        # validator surfaces the error.
        self.assertEqual(
            out["iso"], "2026-03-29T02:30",
            f"Berlin non-existent 02:30 must NOT be "
            f"silently nudged to 03:30; the helper "
            f"must return the typed wall-clock "
            f"verbatim; got iso={out['iso']!r}",
        )
        self.assertEqual(
            out["reason"], "non_existent",
            f"Berlin non-existent 02:30 must be "
            f"marked non_existent; got "
            f"reason={out['reason']!r}",
        )

    def test_ny_spring_non_existent(self):
        """In America/New_York, ``2026-03-08T02:30``
        is non-existent: clocks jump from 02:00
        EST to 03:00 EDT. The helper must not
        nudge; the typed wall-clock is
        preserved.
        """
        out = self._resolve(
            "2026-03-08T02:30", "America/New_York"
        )
        self.assertEqual(
            out["iso"], "2026-03-08T02:30",
            f"NY non-existent 02:30 must NOT be "
            f"silently nudged; got iso={out['iso']!r}",
        )
        self.assertEqual(
            out["reason"], "non_existent",
            f"NY non-existent 02:30 must be marked "
            f"non_existent; got reason="
            f"{out['reason']!r}",
        )

    def test_ny_autumn_ambiguous(self):
        """In America/New_York, ``2026-11-01T01:30``
        is ambiguous: clocks fall back from
        02:00 EDT to 01:00 EST, so 01:30 exists
        twice. The helper picks EST (+01:00? no,
        EST is -05:00). Must pick the LATER
        offset (the post-fallback one, EST).
        """
        out = self._resolve(
            "2026-11-01T01:30", "America/New_York"
        )
        import datetime as _dt
        from zoneinfo import ZoneInfo
        iso = out["iso"].replace("Z", "+00:00")
        parsed = _dt.datetime.fromisoformat(iso)
        local_dt = parsed.astimezone(
            ZoneInfo("America/New_York")
        )
        offset_min = local_dt.utcoffset().total_seconds() / 60
        self.assertEqual(
            offset_min, -300,
            f"NY ambiguous 01:30 must pick EST "
            f"(-05:00); got offset={offset_min}min "
            f"(iso={out!r})",
        )
        self.assertEqual(
            out["reason"], "ambiguous",
            f"NY ambiguous 01:30 must be marked "
            f"ambiguous; got reason={out['reason']!r}",
        )

    def test_unambiguous_normal_day(self):
        """On a day far from any DST transition,
        the conversion is unambiguous for any
        timezone. Verify a few canonical cases
        produce an offset-aware ISO with
        ``reason: null`` and the round-trip
        wall-clock in the target timezone
        matches the input.
        """
        cases = [
            # (tz, wall)
            ("Europe/Kyiv", "2026-06-15T14:30"),
            ("UTC", "2026-06-15T14:30"),
            ("America/New_York", "2026-06-15T14:30"),
        ]
        for tz, wall in cases:
            with self.subTest(tz=tz, wall=wall):
                out = self._resolve(wall, tz)
                # The helper must return an
                # offset-aware ISO.
                self.assertTrue(
                    "+" in out["iso"][10:]
                    or out["iso"].endswith("Z"),
                    f"{tz} {wall}: helper returned "
                    f"a non-offset-aware ISO; "
                    f"got {out!r}",
                )
                # The ISO's UTC instant, when
                # formatted in the target TZ, must
                # round-trip back to the typed
                # wall-clock. The helper MUST
                # produce the correct offset.
                import datetime as _dt
                try:
                    from zoneinfo import ZoneInfo
                    target_tz = ZoneInfo(tz)
                except Exception:
                    target_tz = None
                iso = out["iso"].replace("Z", "+00:00")
                parsed = _dt.datetime.fromisoformat(iso)
                if target_tz is not None:
                    local_dt = parsed.astimezone(target_tz)
                else:
                    # Fallback: trust the ISO's
                    # offset.
                    if "+" in iso[10:]:
                        sign = iso[19]
                        hh = int(iso[20:22])
                        mm = (
                            int(iso[23:25])
                            if len(iso) > 23
                            else 0
                        )
                        total_min = (hh * 60 + mm) * (
                            -1 if sign == "-" else 1
                        )
                    else:
                        total_min = 0
                    tz_off = _dt.timezone(
                        _dt.timedelta(minutes=total_min)
                    )
                    local_dt = parsed.astimezone(tz_off)
                self.assertEqual(
                    local_dt.strftime("%Y-%m-%dT%H:%M"),
                    wall,
                    f"{tz} {wall}: ISO {iso!r} "
                    f"round-trips to "
                    f"{local_dt.isoformat()!r} in "
                    f"{tz!r}, expected {wall!r}",
                )
                self.assertIsNone(
                    out["reason"],
                    f"{tz} {wall}: expected no DST "
                    f"reason; got {out['reason']!r}",
                )


if __name__ == "__main__":
    unittest.main()
