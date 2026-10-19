"""Playwright-based browser tests for the manual
outage-scenarios card (``k-outage-scenarios-card``).

The card is a Web Component loaded by the harness at
``tests/harness/k-outage-card-harness.html``. The harness
publishes a stub ``hass`` on ``window.__harness`` that
records every ``callService`` invocation and returns the
canned payload the test seeds.

These tests are fully offline -- no live HA, no auth,
no real network beyond the local static server.

Coverage matrix:

  * ``test_empty_note`` -- add with cleared notes
    forwards ``notes: null`` (per the contract) and
    the start/end are offset-aware ISO in HA TZ.
  * ``test_list_renders_two_events`` -- the card
    renders 2 pre-seeded events with status pill,
    time range and action buttons.
  * ``test_plan_calls_compute_and_renders_fields`` --
    the "Оновити план" button triggers the per-entry
    suffixed ``<entry>_compute_outage_plan`` service
    and the per-field labels render.
  * ``test_edit_changes_end_iso`` -- clicking
    "Редагувати", changing end, clicking "Зберегти"
    forwards the new end + the current ``version``.
  * ``test_cancel_triggers_cancel_outage_event`` --
    clicking "Скасувати подію" fires
    ``cancel_outage_event``.
  * ``test_timezones_drive_display`` -- the same
    event's start_iso renders in different HA TZs
    (Europe/Kyiv, Europe/Berlin, UTC, Asia/Tokyo);
    the rendered string uses the HA TZ, NOT the
    browser TZ.
  * ``test_viewports_360_and_1280_no_clipping`` --
    the form fields and event list are readable at
    both 360 px and 1280 px; a full-page screenshot
    is captured for each.

The fixture boots a real Chromium and serves the
harness over a tiny localhost HTTP server so the
``<script src="../../frontend/...">`` actually
fetches the production card source -- the same path
HA's frontend uses.
"""
from __future__ import annotations

import http.server
import os
import shutil
import socketserver
import tempfile
import threading
import time
import unittest
from pathlib import Path

from playwright.sync_api import (
    sync_playwright,
    BrowserContext,
    Page,
    Playwright,
)


REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / "frontend"
HARNESS = REPO / "tests" / "harness" / "k-outage-card-harness.html"
ARTIFACTS = REPO / "tests" / "_artifacts"


# =============================================================================
# Static server + browser fixture
# =============================================================================


class _StaticServer:
    """Serve the harness HTML + the repo's
    ``frontend/`` folder over localhost so the
    ``<script src="../../frontend/...">`` tag in
    the harness loads the production card source.

    The server's root is the repo so the relative
    ``../../frontend/k-outage-scenarios-card.js``
    path resolves correctly.
    """

    def __init__(self) -> None:
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self._port = s.getsockname()[1]
        s.close()
        self._httpd: socketserver.TCPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def start(self) -> None:
        class Handler(
            http.server.SimpleHTTPRequestHandler
        ):
            def log_message(self, *_args):
                pass

        class _H(Handler):
            def __init__(self, *args, **kwargs):
                super().__init__(
                    *args,
                    directory=str(REPO),
                    **kwargs,
                )

        self._httpd = socketserver.TCPServer(
            ("127.0.0.1", self._port), _H
        )
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()


class _Fixture:
    """Boot a real Chromium once for the whole suite.

    Each test gets a fresh ``BrowserContext`` (and a
    fresh stub ``__harness`` / ``__card`` state) so
    they don't share cached card state.
    """

    def __init__(self) -> None:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        self._server = _StaticServer()
        self._server.start()
        self._playwright: Playwright | None = None
        self._browser = None

    @property
    def base(self) -> str:
        return self._server.base

    @property
    def harness_url(self) -> str:
        return (
            self._server.base
            + "/tests/harness/k-outage-card-harness.html"
        )

    def start_browser(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

    def new_page(self) -> tuple[Page, BrowserContext, list[str]]:
        assert self._browser is not None
        ctx = self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            timezone_id="Europe/Kyiv",
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on(
            "pageerror",
            lambda exc: errors.append(str(exc)),
        )
        return page, ctx, errors

    def stop(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._server.stop()


def _wait_for_card(page: Page) -> None:
    """Block until the upgrade is complete and the
    card's shadow root is available.
    """
    page.wait_for_function(
        """() => {
            const el = window.__card;
            return Boolean(el && el.shadowRoot);
        }""",
        timeout=5000,
    )


def _seed_list(page: Page, events: list[dict]) -> None:
    page.evaluate(
        """(events) => {
            window.__harness.listResponse = {
                response: { events: events },
            };
            // Trigger a re-render of the card so it
            // picks up the seeded events on the next
            // refresh.
            window.__harness.calls.length = 0;
            window.__card.hass = window.__harness.makeHass();
        }""",
        events,
    )
    page.wait_for_function(
        """() => window.__harness.calls.some(
              c => c.service === 'list_outage_events')""",
        timeout=5000,
    )
    # Let the async refresh complete.
    page.wait_for_timeout(50)


def _seed_compute_plan(
    page: Page, response: dict, shape: str = "wrapped"
) -> None:
    page.evaluate(
        """({resp, shape}) => {
            window.__harness.computePlanResponse = {
                response: resp,
            };
            window.__harness.responseShape = shape;
        }""",
        {"resp": response, "shape": shape},
    )


# =============================================================================
# Tests
# =============================================================================


class KOutageScenariosCardBrowserTests(unittest.TestCase):
    """7 isolated, offline browser tests for the
    manual-scenario card.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._fx = _Fixture()
        cls._fx.start_browser()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._fx.stop()

    # -----------------------------------------------------------------
    # 1. empty_note
    # -----------------------------------------------------------------
    def test_empty_note(self) -> None:
        """Cleared notes field forwards ``notes: null``
        in the service payload and start/end are
        offset-aware ISO in HA TZ (Europe/Kyiv =
        +03:00, no DST in October).
        """
        page, ctx, errors = self._fx.new_page()
        self.addCleanup(ctx.close)
        page.goto(self._fx.harness_url, wait_until="load")
        _wait_for_card(page)

        # Sanity: custom element registered, no JS
        # errors so far.
        self.assertTrue(
            page.evaluate(
                """() => Boolean(
                    customElements.get('k-outage-scenarios-card'))"""
            ),
            "card not registered",
        )

        # Set form values through the input change
        # event so the card's internal ``this._form``
        # state updates exactly as it would on a real
        # operator interaction.
        page.evaluate(
            """() => {
                const card = window.__card;
                const inputs = card.shadowRoot.querySelectorAll(
                    "input[data-field]");
                const setInput = (field, value) => {
                    for (const inp of inputs) {
                        if (inp.getAttribute('data-field') === field
                            && !inp.closest('.form--inline')) {
                            inp.value = value;
                            inp.dispatchEvent(
                                new Event('change', { bubbles: true }));
                            return;
                        }
                    }
                };
                setInput('start', '2026-10-20T09:00');
                setInput('end',   '2026-10-20T13:00');
                setInput('notes', '');
            }"""
        )
        # Clear recorded calls (the list call from
        # set hass) so we only see the upcoming add.
        page.evaluate(
            "() => { window.__harness.calls.length = 0; }"
        )
        # Click "Зберегти й підтвердити".
        page.evaluate(
            """() => {
                const card = window.__card;
                const btn = card.shadowRoot.querySelector(
                    "button[data-action='add']");
                btn.click();
            }"""
        )
        # Wait for the call to be recorded.
        page.wait_for_function(
            """() => window.__harness.calls.some(
                  c => c.service === 'add_outage_event')""",
            timeout=5000,
        )
        page.wait_for_timeout(50)

        recorded = page.evaluate(
            """() => {
                const calls = window.__harness.calls.filter(
                    c => c.service === 'add_outage_event');
                return calls[0] ? calls[0].data : null;
            }"""
        )
        self.assertIsNotNone(
            recorded, "add_outage_event was not called"
        )
        # The card's contract is: empty input forwards
        # ``""`` (empty string), NOT null. The
        # production server schema accepts a string
        # ``notes``; null is rejected as
        # ``expected str @ data['notes']``. The
        # previous (wrong) contract of "null for
        # empty input" was a bug.
        self.assertEqual(
            recorded.get("notes"), "",
            f"empty notes must be empty string; "
            f"got {recorded.get('notes')!r}",
        )
        # entry_id flows through.
        self.assertEqual(
            recorded.get("entry_id"),
            "01M3XWJ8DRYDQC8A0NCPRVB53N",
            f"entry_id not forwarded in add payload; "
            f"data={recorded!r}",
        )
        # source is the manual dashboard source.
        self.assertEqual(
            recorded.get("source"), "manual:dashboard"
        )
        # start / end are offset-aware ISO. The
        # browser is at Europe/Kyiv in the test
        # context, the card converts the typed
        # ``YYYY-MM-DDTHH:MM`` (09:00 / 13:00 wall
        # clock) to ISO. Both spellings are
        # acceptable: ``…+03:00`` (Kyiv) and
        # ``…Z`` (UTC). Compare instants.
        from datetime import datetime, timezone
        start_iso = recorded.get("start")
        start_dt = datetime.fromisoformat(
            start_iso
        ).astimezone(timezone.utc)
        expected_start = datetime.fromisoformat(
            "2026-10-20T09:00:00+03:00"
        ).astimezone(timezone.utc)
        self.assertEqual(
            start_dt, expected_start,
            f"start must be offset-aware ISO representing "
            f"09:00 Kyiv; got {start_iso!r} "
            f"(parsed: {start_dt.isoformat()})",
        )
        end_iso = recorded.get("end")
        end_dt = datetime.fromisoformat(
            end_iso
        ).astimezone(timezone.utc)
        expected_end = datetime.fromisoformat(
            "2026-10-20T13:00:00+03:00"
        ).astimezone(timezone.utc)
        self.assertEqual(
            end_dt, expected_end,
            f"end must be offset-aware ISO representing "
            f"13:00 Kyiv; got {end_iso!r} "
            f"(parsed: {end_dt.isoformat()})",
        )
        # No page errors.
        self.assertEqual(
            errors, [],
            f"page errors during empty-note add: {errors}",
        )

    # -----------------------------------------------------------------
    # 2. list
    # -----------------------------------------------------------------
    def test_list_renders_two_events(self) -> None:
        """The card renders 2 pre-seeded events with
        status pill, time range and action buttons.
        """
        page, ctx, errors = self._fx.new_page()
        self.addCleanup(ctx.close)
        page.goto(self._fx.harness_url, wait_until="load")
        _wait_for_card(page)
        now_iso = "2026-10-20T08:00:00.000Z"
        events = [
            {
                "id": "evt-A",
                "start_iso": "2026-10-21T09:00:00+03:00",
                "end_iso": "2026-10-21T13:00:00+03:00",
                "status": "confirmed",
                "source": "manual:dashboard",
                "version": 1,
                "tz_name": "Europe/Kyiv",
                "notes": "ТП-12",
                "created_at_iso": now_iso,
                "updated_at_iso": now_iso,
            },
            {
                "id": "evt-B",
                "start_iso": "2026-10-22T08:00:00+03:00",
                "end_iso": "2026-10-22T10:00:00+03:00",
                "status": "pending",
                "source": "manual:dashboard",
                "version": 1,
                "tz_name": "Europe/Kyiv",
                "notes": None,
                "created_at_iso": now_iso,
                "updated_at_iso": now_iso,
            },
        ]
        _seed_list(page, events)

        snapshot = page.evaluate(
            """() => {
                const root = window.__card.shadowRoot;
                const rows = Array.from(
                    root.querySelectorAll('.event'));
                return {
                    rowCount: rows.length,
                    times: rows.map(
                        r => r.querySelector('.event__time')
                            ?.textContent.trim()),
                    statuses: rows.map(
                        r => r.querySelector('.event__status')
                            ?.textContent.trim()),
                    statusClasses: rows.map(
                        r => Array.from(
                            r.querySelector('.event__status')
                                ?.classList || [])
                            .join(' ')),
                    buttons: rows.map(
                        r => Array.from(
                            r.querySelectorAll('button'))
                            .map(b => b.textContent.trim())),
                    actions: rows.map(
                        r => Array.from(
                            r.querySelectorAll(
                                'button[data-action]'))
                            .map(b => ({
                                action: b.getAttribute(
                                    'data-action'),
                                id: b.getAttribute('data-id'),
                            }))),
                    notesLines: rows.map(
                        r => r.querySelector('.event__notes')
                            ?.textContent.trim() || null),
                };
            }"""
        )
        self.assertEqual(
            snapshot["rowCount"], 2,
            f"expected 2 events, got {snapshot!r}",
        )
        # A and B both rendered.
        self.assertEqual(
            len(snapshot["times"]), 2,
            f"time ranges missing: {snapshot!r}",
        )
        for t in snapshot["times"]:
            self.assertIn(
                "→", t,
                f"rendered time range missing arrow: {t!r}",
            )
        # Status pills: A confirmed, B pending. The
        # labels are "Підтверджено" / "Очікує
        # підтвердження" and the class includes the
        # status itself for CSS styling.
        self.assertEqual(
            snapshot["statuses"][0], "Підтверджено"
        )
        self.assertEqual(
            snapshot["statuses"][1], "Очікує підтвердження"
        )
        self.assertIn(
            "event__status--confirmed",
            snapshot["statusClasses"][0],
        )
        self.assertIn(
            "event__status--pending",
            snapshot["statusClasses"][1],
        )
        # Notes: A shows "ТП-12", B has no notes
        # line.
        self.assertEqual(
            snapshot["notesLines"][0], "ТП-12"
        )
        self.assertIsNone(snapshot["notesLines"][1])
        # Action buttons: each row has Edit, Cancel
        # ("Скасувати подію") and Refresh plan
        # ("Оновити план").
        for buttons in snapshot["actions"]:
            actions = {b["action"] for b in buttons}
            self.assertEqual(
                actions,
                {"edit", "cancel", "plan"},
                f"missing buttons per row: {buttons!r}",
            )
        # No page errors from the rendering.
        self.assertEqual(errors, [], f"errors: {errors}")

    # -----------------------------------------------------------------
    # 3. plan
    # -----------------------------------------------------------------
    def test_plan_calls_compute_and_renders_fields(self) -> None:
        """Clicking "Оновити план" triggers the
        per-entry-suffixed compute service and the
        per-field labels are rendered.
        """
        page, ctx, errors = self._fx.new_page()
        self.addCleanup(ctx.close)
        page.goto(self._fx.harness_url, wait_until="load")
        _wait_for_card(page)
        now_iso = "2026-10-20T08:00:00.000Z"
        events = [
            {
                "id": "evt-plan",
                "start_iso": "2026-10-21T09:00:00+03:00",
                "end_iso": "2026-10-21T13:00:00+03:00",
                "status": "confirmed",
                "source": "manual:dashboard",
                "version": 1,
                "tz_name": "Europe/Kyiv",
                "notes": None,
                "created_at_iso": now_iso,
                "updated_at_iso": now_iso,
            }
        ]
        plan_payload = {
            "planned": True,
            "blocked": False,
            "plan": {
                "attainable_target_soc_pct": 80,
                "energy_needed_kwh": 4.0,
                "energy_available_kwh": 6.5,
                "energy_reserve_kwh": 0.5,
                "needs_charging_from_grid_kwh": 0.0,
                "grid_charge_hours_per_hour": 0.0,
                "prep_start_iso": "2026-10-21T06:00:00+03:00",
            },
            "reason_if_blocked": None,
            "reason_if_incomplete": None,
        }
        _seed_compute_plan(page, plan_payload, shape="wrapped")
        _seed_list(page, events)

        # Click "Оновити план".
        page.evaluate(
            """() => {
                const btn = window.__card.shadowRoot.querySelector(
                    "button[data-action='plan']");
                btn.click();
            }"""
        )
        page.wait_for_function(
            """() => window.__harness.calls.some(c =>
                c.service.indexOf('_compute_outage_plan') !== -1)""",
            timeout=5000,
        )
        page.wait_for_timeout(50)

        # Verify the service name suffix matches
        # the configured entry_id.
        recorded = page.evaluate(
            """() => {
                const calls = window.__harness.calls.filter(c =>
                    c.service.indexOf('_compute_outage_plan') !== -1);
                return calls[0];
            }"""
        )
        self.assertEqual(
            recorded["service"],
            "01M3XWJ8DRYDQC8A0NCPRVB53N_compute_outage_plan",
            f"per-entry service suffix wrong: {recorded!r}",
        )
        self.assertEqual(
            recorded["data"].get("event_id"), "evt-plan"
        )
        self.assertTrue(
            recorded["data"].get("force_recompute")
        )

        # Verify the rendered plan row labels.
        rows = page.evaluate(
            """() => {
                const root = window.__card.shadowRoot;
                const labels = Array.from(
                    root.querySelectorAll('.plan__label'));
                const values = Array.from(
                    root.querySelectorAll('.plan__row > span:nth-child(2)'));
                return labels.map((l, i) => ({
                    label: l.textContent.trim(),
                    value: values[i]?.textContent.trim(),
                }));
            }"""
        )
        labels = [r["label"] for r in rows]
        self.assertIn(
            "Цільовий SOC на початок", labels,
            f"target-SOC row missing: {rows!r}",
        )
        self.assertIn(
            "Коли починати підготовку", labels,
            f"prep-start row missing: {rows!r}",
        )
        self.assertIn(
            "Доступна енергія vs потреба", labels,
            f"energy row missing: {rows!r}",
        )
        # Specific values from the canned payload.
        by_label = {r["label"]: r["value"] for r in rows}
        self.assertEqual(by_label["Цільовий SOC на початок"], "80 %")
        self.assertIn("вистачає", by_label["Доступна енергія vs потреба"])
        self.assertEqual(errors, [], f"errors: {errors}")

    # -----------------------------------------------------------------
    # 4. edit
    # -----------------------------------------------------------------
    def test_edit_changes_end_iso(self) -> None:
        """Edit flow: change end time, click save,
        verify the update_outage_event payload uses
        the new ISO in HA TZ and the current version.
        """
        page, ctx, errors = self._fx.new_page()
        self.addCleanup(ctx.close)
        page.goto(self._fx.harness_url, wait_until="load")
        _wait_for_card(page)
        now_iso = "2026-10-20T08:00:00.000Z"
        events = [
            {
                "id": "evt-edit",
                "start_iso": "2026-10-21T09:00:00+03:00",
                "end_iso": "2026-10-21T11:00:00+03:00",
                "status": "confirmed",
                "source": "manual:dashboard",
                "version": 3,
                "tz_name": "Europe/Kyiv",
                "notes": "стара нотатка",
                "created_at_iso": now_iso,
                "updated_at_iso": now_iso,
            }
        ]
        _seed_list(page, events)
        # Click "Редагувати".
        page.evaluate(
            """() => {
                const btn = window.__card.shadowRoot.querySelector(
                    "button[data-action='edit']");
                btn.click();
            }"""
        )
        # Wait for the inline form to render.
        page.wait_for_function(
            """() => Boolean(
                window.__card.shadowRoot.querySelector(
                    '.form--inline input[data-field="end"]'))""",
            timeout=2000,
        )
        # Change the end input + clear notes for
        # extra coverage.
        page.evaluate(
            """() => {
                const root = window.__card.shadowRoot;
                const endInp = root.querySelector(
                    '.form--inline input[data-field="end"]');
                endInp.value = '2026-10-21T15:30';
                endInp.dispatchEvent(
                    new Event('change', { bubbles: true }));
                const noteInp = root.querySelector(
                    '.form--inline input[data-field="notes"]');
                noteInp.value = 'нова нотатка';
                noteInp.dispatchEvent(
                    new Event('change', { bubbles: true }));
            }"""
        )
        # Clear the call log so we only see the
        # upcoming save call.
        page.evaluate(
            "() => { window.__harness.calls.length = 0; }"
        )
        # Click "Зберегти".
        page.evaluate(
            """() => {
                window.__card.shadowRoot.querySelector(
                    "button[data-action='save-edit']").click();
            }"""
        )
        page.wait_for_function(
            """() => window.__harness.calls.some(
                  c => c.service === 'update_outage_event')""",
            timeout=5000,
        )
        page.wait_for_timeout(50)
        recorded = page.evaluate(
            """() => {
                const c = window.__harness.calls.find(
                    c => c.service === 'update_outage_event');
                return c ? c.data : null;
            }"""
        )
        self.assertIsNotNone(
            recorded, "update_outage_event was not called"
        )
        self.assertEqual(recorded.get("id"), "evt-edit")
        # version is forwarded as a number (the
        # cardinality attr is rendered as a string,
        # but the card parses it via Number()).
        self.assertEqual(
            int(recorded.get("version", 0)), 3
        )
        # End represents "15:30 Kyiv" (the user
        # typed ``2026-10-21T15:30``); the card
        # converts to ISO. The exact string may
        # be either ``…+03:00`` or ``…Z`` -- both
        # are valid representations of the same
        # instant. Assert by parsing both values as
        # datetimes.
        end_iso = recorded.get("end")
        self.assertIsNotNone(end_iso)
        from datetime import datetime, timezone
        # Strip the timezone for direct comparison
        # (we're verifying the instant is correct,
        # not the spelling).
        end_dt = datetime.fromisoformat(end_iso).astimezone(
            timezone.utc
        )
        expected_dt = datetime.fromisoformat(
            "2026-10-21T15:30:00+03:00"
        ).astimezone(timezone.utc)
        self.assertEqual(
            end_dt, expected_dt,
            f"end must be offset-aware ISO representing "
            f"15:30 Kyiv; got {end_iso!r} "
            f"(parsed: {end_dt.isoformat()})",
        )
        # Start was preserved (the user did not
        # touch the start input); the typed default
        # is "09:00 Kyiv" which is "06:00 UTC".
        # The card may emit either spelling; the
        # instant must match.
        start_iso = recorded.get("start")
        from datetime import datetime, timezone
        start_dt = datetime.fromisoformat(
            start_iso
        ).astimezone(timezone.utc)
        expected_start = datetime.fromisoformat(
            "2026-10-21T09:00:00+03:00"
        ).astimezone(timezone.utc)
        self.assertEqual(
            start_dt, expected_start,
            f"start must be 09:00 Kyiv; got "
            f"{start_iso!r} (parsed: {start_dt.isoformat()})",
        )
        # Notes are the new string -- not null,
        # not the old "стара нотатка".
        self.assertEqual(
            recorded.get("notes"), "нова нотатка"
        )
        # entry_id is forwarded.
        self.assertEqual(
            recorded.get("entry_id"),
            "01M3XWJ8DRYDQC8A0NCPRVB53N",
        )
        self.assertEqual(errors, [], f"errors: {errors}")

    # -----------------------------------------------------------------
    # 5. cancel
    # -----------------------------------------------------------------
    def test_cancel_triggers_cancel_outage_event(self) -> None:
        """Clicking "Скасувати подію" forwards the
        event id (and entry_id) to
        ``cancel_outage_event``.
        """
        page, ctx, errors = self._fx.new_page()
        self.addCleanup(ctx.close)
        page.goto(self._fx.harness_url, wait_until="load")
        _wait_for_card(page)
        now_iso = "2026-10-20T08:00:00.000Z"
        events = [
            {
                "id": "evt-cancel",
                "start_iso": "2026-10-21T09:00:00+03:00",
                "end_iso": "2026-10-21T11:00:00+03:00",
                "status": "confirmed",
                "source": "manual:dashboard",
                "version": 1,
                "tz_name": "Europe/Kyiv",
                "notes": None,
                "created_at_iso": now_iso,
                "updated_at_iso": now_iso,
            }
        ]
        _seed_list(page, events)
        page.evaluate(
            "() => { window.__harness.calls.length = 0; }"
        )
        page.evaluate(
            """() => {
                const btn = window.__card.shadowRoot.querySelector(
                    "button[data-action='cancel']");
                btn.click();
            }"""
        )
        page.wait_for_function(
            """() => window.__harness.calls.some(
                  c => c.service === 'cancel_outage_event')""",
            timeout=5000,
        )
        page.wait_for_timeout(50)
        recorded = page.evaluate(
            """() => {
                const c = window.__harness.calls.find(
                    c => c.service === 'cancel_outage_event');
                return c ? c.data : null;
            }"""
        )
        self.assertIsNotNone(
            recorded, "cancel_outage_event was not called"
        )
        self.assertEqual(
            recorded.get("id"), "evt-cancel"
        )
        self.assertEqual(
            recorded.get("entry_id"),
            "01M3XWJ8DRYDQC8A0NCPRVB53N",
        )
        self.assertEqual(errors, [], f"errors: {errors}")

    # -----------------------------------------------------------------
    # 6. timezones
    # -----------------------------------------------------------------
    def test_timezones_drive_display(self) -> None:
        """The same event's ``start_iso`` renders in
        different timezones depending on
        ``hass.config.time_zone``. The card uses
        ``Intl.DateTimeFormat`` with the HA TZ, NOT
        the browser's. We launch the page with the
        browser zone kept at Europe/Kyiv and flip
        the HA TZ only.
        """
        now_iso = "2026-10-20T08:00:00.000Z"
        # An event whose UTC start is "13:00 UTC".
        # Different TZs render it differently:
        #   Europe/Kyiv    -> 16:00
        #   Europe/Berlin  -> 15:00 (+2 DST end-Oct)
        #   UTC            -> 13:00
        #   Asia/Tokyo     -> 22:00
        event_iso = "2026-10-20T13:00:00+00:00"
        events = [
            {
                "id": "evt-tz",
                "start_iso": event_iso,
                "end_iso": "2026-10-20T15:00:00+00:00",
                "status": "confirmed",
                "source": "manual:dashboard",
                "version": 1,
                "tz_name": "UTC",
                "notes": None,
                "created_at_iso": now_iso,
                "updated_at_iso": now_iso,
            }
        ]

        expected_substrings = {
            "Europe/Kyiv":  "16:00",
            "Europe/Berlin": "15:00",
            "UTC":          "13:00",
            "Asia/Tokyo":   "22:00",
        }
        seen: dict[str, str] = {}
        for tz in expected_substrings:
            page, ctx, errors = self._fx.new_page()
            try:
                # Force the browser timezone so we
                # can isolate the HA TZ effect. We
                # set it to UTC explicitly -- HA TZs
                # like Asia/Tokyo would otherwise pull
                # the browser clock away from the
                # HA TZ we are testing.
                ctx.close()
                # Recreate context with a different
                # browser TZ each test so the card's
                # ``Intl`` calls honour the card's
                # explicit ``timeZone`` (which is the
                # HA TZ).
                ctx = self._fx._browser.new_context(  # noqa: SLF001
                    viewport={"width": 1280, "height": 720},
                    timezone_id="UTC",
                )
                page = ctx.new_page()
                page.goto(
                    self._fx.harness_url, wait_until="load"
                )
                _wait_for_card(page)
                # Override the configured HA TZ.
                page.evaluate(
                    "(tz) => { window.__harness.timeZone = tz; }",
                    tz,
                )
                # Re-attach hass so the card picks
                # the new TZ up; the harness's
                # ``makeHass`` reads ``timeZone``
                # lazily on every call.
                _seed_list(page, events)
                # Read the rendered time range text.
                text = page.evaluate(
                    """() => window.__card.shadowRoot
                        .querySelector('.event__time')
                        ?.textContent.trim()"""
                )
                self.assertIsNotNone(
                    text, f"event not rendered for {tz}"
                )
                seen[tz] = text
                # The text MUST contain the expected
                # HA-TZ-formatted time. The browser
                # zone is UTC for this test, so a
                # browser-zone render would show
                # 13:00 for this event.
                self.assertIn(
                    expected_substrings[tz], text,
                    f"event time for HA TZ={tz} must "
                    f"contain {expected_substrings[tz]!r}; "
                    f"rendered: {text!r} (browser TZ is UTC)",
                )
                self.assertEqual(errors, [], f"errors: {errors}")
            finally:
                ctx.close()
        # Also assert that the Kyiv rendering
        # does NOT show the Tokyo-formatted hour.
        self.assertNotIn(
            "22:00", seen.get("Europe/Kyiv", ""),
            "Kyiv render must not use Tokyo hours",
        )

    # -----------------------------------------------------------------
    # 7. viewport
    # -----------------------------------------------------------------
    def test_viewports_360_and_1280_no_clipping(self) -> None:
        """At 360 px (mobile) the form fields and
        event list are still visible (the card
        uses a grid that fits), no horizontal
        scrollbar on the body. At 1280 px (desktop)
        the same elements render with whitespace to
        spare. A full-page screenshot is captured
        for each viewport.
        """
        now_iso = "2026-10-20T08:00:00.000Z"
        events = [
            {
                "id": f"evt-{i}",
                "start_iso": f"2026-10-21T0{i}:00:00+03:00",
                "end_iso": f"2026-10-21T0{i + 2}:00:00+03:00",
                "status": "confirmed",
                "source": "manual:dashboard",
                "version": 1,
                "tz_name": "Europe/Kyiv",
                "notes": (
                    "Тривала нотатка що перевіряє "
                    "перенесення на вузькому екрані"
                    if i == 0
                    else None
                ),
                "created_at_iso": now_iso,
                "updated_at_iso": now_iso,
            }
            for i in range(1, 3)
        ]

        for width in (360, 1280):
            page, ctx, errors = self._fx.new_page()
            try:
                page.set_viewport_size(
                    {"width": width, "height": 720}
                )
                page.goto(
                    self._fx.harness_url, wait_until="load"
                )
                _wait_for_card(page)
                _seed_list(page, events)
                shot = ARTIFACTS / (
                    "k_outage_360.png"
                    if width == 360
                    else "k_outage_1280.png"
                )
                page.screenshot(path=str(shot), full_page=True)
                # Sanity: file exists and is non-empty.
                self.assertTrue(
                    shot.exists(),
                    f"screenshot missing: {shot}",
                )
                self.assertGreater(
                    shot.stat().st_size, 0,
                    f"screenshot is empty: {shot}",
                )

                # Inspect DOM measurements.
                geom = page.evaluate(
                    """() => {
                        const root = window.__card.shadowRoot;
                        const start = root.querySelector(
                            'input[data-field="start"]');
                        const end = root.querySelector(
                            'input[data-field="end"]');
                        const notes = root.querySelector(
                            'input[data-field="notes"]');
                        const rows = root.querySelectorAll('.event');
                        const cardRect = window.__card
                            .getBoundingClientRect();
                        const documentScroll = {
                            scrollW: document.documentElement
                                .scrollWidth,
                            clientW: document.documentElement
                                .clientWidth,
                        };
                        return {
                            cardWidth: cardRect.width,
                            cardHeight: cardRect.height,
                            startVisible: Boolean(start)
                                && start.getBoundingClientRect()
                                    .width > 0,
                            endVisible: Boolean(end)
                                && end.getBoundingClientRect()
                                    .width > 0,
                            notesVisible: Boolean(notes)
                                && notes.getBoundingClientRect()
                                    .width > 0,
                            rowCount: rows.length,
                            anyRowOverflows: Array.from(rows)
                                .some(r => r.scrollWidth >
                                    r.clientWidth),
                            documentScroll: documentScroll,
                        };
                    }"""
                )
                # Card must not be wider than the
                # viewport (after laying out); the
                # test container is wider than the
                # card, so a card whose intrinsic
                # width fits within the viewport is
                # the right outcome.
                self.assertLessEqual(
                    geom["cardWidth"], width + 1,
                    f"card overflows viewport at "
                    f"{width}px: {geom!r}",
                )
                self.assertGreater(
                    geom["cardWidth"], 100,
                    f"card collapsed at {width}px: {geom!r}",
                )
                # Both form fields and notes are
                # visible (have a non-zero width).
                self.assertTrue(
                    geom["startVisible"],
                    f"start input not visible at {width}px: "
                    f"{geom!r}",
                )
                self.assertTrue(
                    geom["endVisible"],
                    f"end input not visible at {width}px: "
                    f"{geom!r}",
                )
                self.assertTrue(
                    geom["notesVisible"],
                    f"notes input not visible at {width}px: "
                    f"{geom!r}",
                )
                # Two events rendered.
                self.assertEqual(
                    geom["rowCount"], 2,
                    f"expected 2 events, got {geom!r}",
                )
                # No horizontal overflow on the
                # event rows.
                self.assertFalse(
                    geom["anyRowOverflows"],
                    f"event row overflows its container "
                    f"at {width}px: {geom!r}",
                )
                # The whole body must NOT generate a
                # horizontal scrollbar.
                self.assertLessEqual(
                    geom["documentScroll"]["scrollW"],
                    geom["documentScroll"]["clientW"] + 1,
                    f"horizontal scrollbar at {width}px: "
                    f"{geom!r}",
                )
                self.assertEqual(
                    errors, [], f"errors at {width}px: {errors}"
                )
            finally:
                ctx.close()

    # -----------------------------------------------------------------
    # 8. callService arg order (Defect 1)
    # -----------------------------------------------------------------
    def test_call_service_arg_order(self) -> None:
        """Every ``hass.callService`` call the card
        makes for the manual scenario (add, list,
        update, cancel, plan) uses the correct
        positional argument order:

            callService(domain, service, data,
                        target, notifyOnError,
                        returnResponse)

        The 5th argument is ``notifyOnError`` (NOT
        ``returnResponse``); the 6th is
        ``returnResponse``. The card must set
        ``returnResponse`` in the 6th position to
        actually receive the handler's return value
        (HA wraps it as
        ``{context, response}``).
        """
        page, ctx, errors = self._fx.new_page()
        self.addCleanup(ctx.close)
        page.goto(self._fx.harness_url, wait_until="load")
        _wait_for_card(page)

        # The card calls ``list_outage_events`` on
        # every ``set hass``. The harness already
        # records every call with positional args.
        # We trigger a fresh ``set hass`` to ensure
        # at least one list call is captured.
        page.evaluate(
            """() => {
                window.__harness.calls.length = 0;
                const card = document.querySelector(
                    'k-outage-scenarios-card');
                card.hass = window.__harness.makeHass();
            }"""
        )
        # Wait for the debounced refresh to fire.
        page.wait_for_function(
            """() => window.__harness.calls.some(
                c => c.service === 'list_outage_events'
            )""",
            timeout=4000,
        )
        # The list call MUST be in the call log
        # with the 6th arg = true and the 5th
        # arg = false. The harness records
        # positional slots by name (the
        # ``argNotifyOnError`` / ``argReturnResponse``
        # fields) and ALSO the raw ``args`` array.
        list_call = page.evaluate(
            """() => {
                const c = window.__harness.calls.find(
                    x => x.service === 'list_outage_events');
                if (!c) return null;
                return {
                    domain: c.domain,
                    service: c.service,
                    data: c.data,
                    target: c.argTarget,
                    notifyOnError: c.argNotifyOnError,
                    returnResponse: c.argReturnResponse,
                };
            }"""
        )
        self.assertIsNotNone(
            list_call,
            "list_outage_events was not called; "
            f"recorded services: "
            f"{page.evaluate('() => window.__harness.calls.map(c => c.service)')}",
        )
        self.assertEqual(
            list_call["service"], "list_outage_events"
        )
        # Defect 1: 5th arg must be notifyOnError,
        # 6th must be returnResponse.
        self.assertFalse(
            list_call["notifyOnError"],
            f"5th arg (notifyOnError) must be False "
            f"or undefined; got "
            f"{list_call['notifyOnError']!r}",
        )
        self.assertIs(
            list_call["returnResponse"], True,
            f"6th arg (returnResponse) must be True; "
            f"got {list_call['returnResponse']!r}",
        )

    # -----------------------------------------------------------------
    # 8b. Non-existent wall-clock blocks the save
    #     (Defect 4 round 5).
    # -----------------------------------------------------------------
    def test_dst_blocks_save_kyiv(self) -> None:
        """A non-existent wall-clock in
        Europe/Kyiv (no DST since 2014, but the
        round 4 helper still flags it) MUST
        block the add_outage_event call. The
        card must surface an inline error and
        NOT call the service.

        We drive the form with a wall-clock
        that the round 4 helper would mark
        ``non_existent`` in a TZ with DST. For
        Kyiv (no DST) the helper is permissive,
        so we use a TZ with active DST for the
        non-existent test; this test asserts
        that when ``reason === 'non_existent'``
        the save is blocked regardless of the
        host TZ. The test sets
        ``hass.config.time_zone = 'Europe/Berlin'``
        to exercise the DST path.
        """
        page, ctx, errors = self._fx.new_page()
        self.addCleanup(ctx.close)
        page.goto(self._fx.harness_url, wait_until="load")
        _wait_for_card(page)
        # Override the harness TZ to Europe/Berlin
        # (DST active). The card reads
        # ``hass.config.time_zone`` on first
        # ``set hass``.
        page.evaluate(
            """() => {
                window.__harness.timeZone = 'Europe/Berlin';
                const card = document.querySelector(
                    'k-outage-scenarios-card');
                card.hass = window.__harness.makeHass();
            }"""
        )
        page.wait_for_timeout(300)
        # Type a non-existent wall-clock. Berlin
        # spring forward 2026-03-29: 02:00 -> 03:00,
        # so 02:30 does not exist.
        page.evaluate(
            """() => {
                const card = document.querySelector(
                    'k-outage-scenarios-card');
                const start = card.shadowRoot.querySelector(
                    'input[data-field="start"]');
                const end = card.shadowRoot.querySelector(
                    'input[data-field="end"]');
                start.value = '2026-03-29T02:30';
                end.value = '2026-03-29T05:00';
                start.dispatchEvent(new Event('change', {bubbles: true}));
                end.dispatchEvent(new Event('change', {bubbles: true}));
                window.__harness.calls.length = 0;
            }"""
        )
        # Click the Add button.
        page.evaluate(
            """() => {
                const card = document.querySelector(
                    'k-outage-scenarios-card');
                const btn = card.shadowRoot.querySelector(
                    'button[data-action="add"]');
                btn.click();
            }"""
        )
        page.wait_for_timeout(500)
        # The card MUST NOT have called the
        # service. ``add_outage_event`` is the
        # service under test.
        calls = page.evaluate(
            "() => window.__harness.calls.filter("
            "c => c.service === 'add_outage_event'"
            ")"
        )
        self.assertEqual(
            len(calls), 0,
            f"DEFECT 4 round 5: the card called "
            f"add_outage_event for a non-existent "
            f"wall-clock; {len(calls)} calls recorded",
        )
        # The card MUST surface an inline error
        # that mentions "не існує" (the
        # operator-facing text).
        err_text = page.evaluate(
            """() => {
                const card = document.querySelector(
                    'k-outage-scenarios-card');
                const errBlock = card.shadowRoot.querySelector(
                    '[data-notice="error"]');
                return errBlock ? errBlock.textContent : '';
            }"""
        )
        self.assertIn(
            "не існує", err_text,
            f"inline error must mention 'не існує' "
            f"for a non-existent wall-clock; got "
            f"{err_text!r}",
        )

    def test_dst_does_not_block_save_utc(self) -> None:
        """In UTC (no DST), the helper always
        returns a valid ISO and the save is
        NOT blocked. We drive the form with a
        wall-clock that the round 4 helper
        flags as ``reason: 'ambiguous'`` -- in
        UTC there is no DST so all wall-clocks
        are unambiguous and the save is
        allowed.
        """
        page, ctx, errors = self._fx.new_page()
        self.addCleanup(ctx.close)
        page.goto(self._fx.harness_url, wait_until="load")
        _wait_for_card(page)
        # Override the harness TZ to UTC.
        page.evaluate(
            """() => {
                window.__harness.timeZone = 'UTC';
                const card = document.querySelector(
                    'k-outage-scenarios-card');
                card.hass = window.__harness.makeHass();
            }"""
        )
        page.wait_for_timeout(300)
        # Type a wall-clock that the helper
        # accepts as valid. UTC has no DST, so
        # ``reason`` is always ``null``.
        page.evaluate(
            """() => {
                const card = document.querySelector(
                    'k-outage-scenarios-card');
                const start = card.shadowRoot.querySelector(
                    'input[data-field="start"]');
                const end = card.shadowRoot.querySelector(
                    'input[data-field="end"]');
                start.value = '2026-10-25T14:30';
                end.value = '2026-10-25T16:00';
                start.dispatchEvent(new Event('change', {bubbles: true}));
                end.dispatchEvent(new Event('change', {bubbles: true}));
                window.__harness.calls.length = 0;
            }"""
        )
        page.evaluate(
            """() => {
                const card = document.querySelector(
                    'k-outage-scenarios-card');
                const btn = card.shadowRoot.querySelector(
                    'button[data-action="add"]');
                btn.click();
            }"""
        )
        # Wait for the add call to be recorded.
        page.wait_for_function(
            """() => window.__harness.calls.some(
                c => c.service === 'add_outage_event'
            )""",
            timeout=4000,
        )
        calls = page.evaluate(
            "() => window.__harness.calls.filter("
            "c => c.service === 'add_outage_event'"
            ")"
        )
        self.assertEqual(
            len(calls), 1,
            f"UTC must not block valid wall-clocks; "
            f"got {len(calls)} add_outage_event calls",
        )
        # The inline error block MUST be empty
        # (no ``reason: 'non_existent'`` error).
        err_text = page.evaluate(
            """() => {
                const card = document.querySelector(
                    'k-outage-scenarios-card');
                const errBlock = card.shadowRoot.querySelector(
                    '[data-notice="error"]');
                return errBlock ? errBlock.textContent : '';
            }"""
        )
        self.assertNotIn(
            "не існує", err_text,
            f"UTC must not surface a 'non-existent' "
            f"error for a valid wall-clock; got "
            f"{err_text!r}",
        )

    # -----------------------------------------------------------------
    # 9. Input persistence across set hass (Defect 5)
    # -----------------------------------------------------------------
    def test_input_persistence_across_set_hass(self) -> None:
        """While the operator is typing in the form,
        telemetry updates fire ``set hass`` every
        few seconds. The card must not rebuild the
        form DOM (which would drop the in-progress
        text and the focus).

        The contract: after the operator types in
        the notes field, calling
        ``card.hass = newHass`` (which is what HA
        does on every telemetry tick) preserves
        the typed value AND the focus on the notes
        input.
        """
        page, ctx, errors = self._fx.new_page()
        self.addCleanup(ctx.close)
        page.goto(self._fx.harness_url, wait_until="load")
        _wait_for_card(page)

        # Focus the notes input and type a partial
        # word.
        page.evaluate(
            """() => {
                const card = document.querySelector(
                    'k-outage-scenarios-card');
                const inp = card.shadowRoot.querySelector(
                    'input[data-field="notes"]');
                inp.focus();
                inp.value = 'Ремонт ТП';
                inp.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }"""
        )
        # Sanity: the form input has the typed value.
        value_before = page.evaluate(
            """() => {
                const card = document.querySelector(
                    'k-outage-scenarios-card');
                return card.shadowRoot.querySelector(
                    'input[data-field="notes"]').value;
            }"""
        )
        self.assertEqual(
            value_before, "Ремонт ТП",
            f"typed value not in DOM; got {value_before!r}",
        )
        # Simulate a telemetry tick by calling
        # set hass with a new (slightly different)
        # hass object.
        page.evaluate(
            """() => {
                const card = document.querySelector(
                    'k-outage-scenarios-card');
                const h = window.__harness.makeHass();
                h.states = Object.assign({}, h.states, {
                    'sensor.test_telemetry_1':
                        {state: '1', attributes: {}, last_changed: '', last_updated: ''}
                });
                card.hass = h;
                return true;
            }"""
        )
        # Defect 5: the form input must still hold
        # the typed value, NOT be reset to "".
        value_after = page.evaluate(
            """() => {
                const card = document.querySelector(
                    'k-outage-scenarios-card');
                return card.shadowRoot.querySelector(
                    'input[data-field="notes"]').value;
            }"""
        )
        self.assertEqual(
            value_after, "Ремонт ТП",
            f"DEFECT 5: typed notes value lost after "
            f"set hass; got {value_after!r} (expected 'Ремонт ТП')",
        )
        # And the focus must still be on the
        # notes input (not jumped to the body).
        # NOTE: ``document.activeElement`` for a
        # shadow-DOM input always points to the
        # shadow host. The actually-focused input
        # is at ``shadowRoot.activeElement``.
        focused_field = page.evaluate(
            """() => {
                const card = document.querySelector(
                    'k-outage-scenarios-card');
                const ae = card.shadowRoot.activeElement;
                if (!ae) return null;
                return ae.getAttribute
                    && ae.getAttribute('data-field');
            }"""
        )
        self.assertEqual(
            focused_field, "notes",
            f"DEFECT 5: focus lost on set hass; "
            f"active field is {focused_field!r}",
        )


if __name__ == "__main__":
    unittest.main()
