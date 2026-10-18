"""Regression tests for the edit form persistence
(DEFECT 5 round 4).

When the operator has the inline edit form open
(``form--inline``) and the events list re-renders
(e.g. on a ``set hass`` that triggers a list
refresh, or on a plan call response), the
in-progress input values and the focus position
MUST survive. The previous round (round 3) called
``slot.innerHTML = ...`` unconditionally, which
discarded the in-progress DOM state and reset the
focus to the body.

These tests load the production card into a
harness page (the same one used by
``test_k_outage_scenarios_card_browser.py``),
drive the edit form, force a list re-render, and
assert that:

  1. The partially-typed text in the notes input
     is preserved (the field shows the operator's
     in-progress text, not the original event
     notes from the data store).
  2. The focus is still on the notes input --
     not the body, not the event row, not
     anywhere else.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = (
    "/opt/data/cache/scratch/codex-review-browsers-20261018"
)

from test_k_outage_scenarios_card_browser import (  # noqa: E402
    _wait_for_card,
)


class EditFormPersistenceTests(unittest.TestCase):
    """Edit form must survive a list re-render.

    We do NOT inherit from the existing
    ``KOutageScenariosCardBrowserTests`` because
    that class' ``setUpClass`` starts a
    ``sync_playwright`` session which collides
    with the asyncio loop pytest runs in. We
    set up the browser locally in ``setUp`` /
    ``tearDown``.
    """

    def setUp(self) -> None:
        # Lazy import the harness fixture from
        # the existing module. Importing the
        # class would trigger its setUpClass --
        # we import the helper functions only.
        from test_k_outage_scenarios_card_browser import (
            _Fixture,
            _wait_for_card,
        )
        # Bind ``_wait_for_card`` to the class so
        # the test methods can use it without
        # importing the symbol themselves.
        self.__class__._wait_for_card = _wait_for_card
        self._fx = _Fixture()
        self._fx.start_browser()

    def tearDown(self) -> None:
        # ``_Fixture.stop`` calls ``_playwright.stop``
        # which raises if the page or context was
        # already closed. The exception is from
        # Playwright, not the test; we just
        # swallow it.
        try:
            self._fx.stop()
        except Exception:
            pass

    def test_edit_notes_preserved_across_list_rerender(self):
        """The operator opens the edit form for an
        event, types a partial note, and the
        events list re-renders. The typed value
        MUST be preserved (not replaced by the
        original ``notes`` from the data store).
        """
        page, ctx, errors = self._fx.new_page()
        try:
            page.goto(self._fx.harness_url, wait_until="load")
            _wait_for_card(page)
            # Pre-seed the harness with one event.
            page.evaluate(
                """() => {
                    window.__harness.listResponse = {
                        context: {id: 'x'},
                        response: {events: [{
                            id: 'evt-edit-persist',
                            start_iso: '2026-10-21T09:00:00+03:00',
                            end_iso: '2026-10-21T11:00:00+03:00',
                            status: 'confirmed',
                            version: 1,
                            notes: 'оригінальна примітка',
                        }]}
                    };
                    window.__harness.calls.length = 0;
                    const card = document.querySelector(
                        'k-outage-scenarios-card');
                    card.hass = window.__harness.makeHass();
                }"""
            )
            page.wait_for_function(
                """() => window.__card.shadowRoot.querySelector(
                    'button[data-action="edit"]'
                ) != null"""
            )
            # Open the edit form.
            page.evaluate(
                """() => {
                    window.__card.shadowRoot.querySelector(
                        'button[data-action="edit"]'
                    ).click();
                }"""
            )
            # Wait for the edit form to mount.
            page.wait_for_function(
                """() => window.__card.shadowRoot.querySelector(
                    '.form--inline input[data-field="notes"]'
                ) != null"""
            )
            # Type a partial note. We change the value
            # and dispatch the ``change`` event so the
            # card's change handler picks it up. The
            # value is DIFFERENT from the original
            # ``"оригінальна примітка"`` -- if the
            # card re-renders and clobbers the input,
            # the field will revert to the original.
            page.evaluate(
                """() => {
                    const inp = window.__card.shadowRoot.querySelector(
                        '.form--inline input[data-field="notes"]');
                    inp.focus();
                    inp.value = 'НОВА';
                    inp.dispatchEvent(
                        new Event('change', {bubbles: true}));
                }"""
            )
            # Sanity check: the input now shows НОВА.
            v0 = page.evaluate(
                """() => {
                    const inp = window.__card.shadowRoot.querySelector(
                        '.form--inline input[data-field="notes"]');
                    return inp.value;
                }"""
            )
            self.assertEqual(
                v0, "НОВА",
                f"after typing, the notes input must "
                f"contain 'НОВА'; got {v0!r}",
            )
            # Force a list re-render.
            page.evaluate(
                """() => {
                    window.__harness.calls.length = 0;
                    const card = document.querySelector(
                        'k-outage-scenarios-card');
                    card._refresh();
                }"""
            )
            # The refresh is async. Wait for the
            # ``list_outage_events`` call to be
            # recorded.
            page.wait_for_function(
                """() => window.__harness.calls.some(
                    c => c.service === 'list_outage_events'
                )"""
            )
            # Now check the input value.
            v1 = page.evaluate(
                """() => {
                    const inp = window.__card.shadowRoot.querySelector(
                        '.form--inline input[data-field="notes"]');
                    if (!inp) return null;
                    return inp.value;
                }"""
            )
            self.assertIsNotNone(
                v1,
                "edit form must still be in the DOM "
                "after a list re-render",
            )
            self.assertEqual(
                v1, "НОВА",
                f"DEFECT 5 round 4: the notes input was "
                f"clobbered by the list re-render; the "
                f"operator's in-progress text was "
                f"replaced with the stored value; got "
                f"{v1!r}",
            )
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    def test_edit_focus_preserved_across_list_rerender(self):
        """The operator opens the edit form, focuses
        the notes input, and the list re-renders.
        The focus MUST stay on the notes input
        (``shadowRoot.activeElement``).
        """
        page, ctx, errors = self._fx.new_page()
        try:
            page.goto(self._fx.harness_url, wait_until="load")
            _wait_for_card(page)
            page.evaluate(
                """() => {
                    window.__harness.listResponse = {
                        context: {id: 'x'},
                        response: {events: [{
                            id: 'evt-focus',
                            start_iso: '2026-10-21T09:00:00+03:00',
                            end_iso: '2026-10-21T11:00:00+03:00',
                            status: 'confirmed',
                            version: 1,
                            notes: 'orig',
                        }]}
                    };
                    window.__harness.calls.length = 0;
                    const card = document.querySelector(
                        'k-outage-scenarios-card');
                    card.hass = window.__harness.makeHass();
                }"""
            )
            page.wait_for_function(
                """() => window.__card.shadowRoot.querySelector(
                    'button[data-action="edit"]'
                ) != null"""
            )
            page.evaluate(
                """() => {
                    window.__card.shadowRoot.querySelector(
                        'button[data-action="edit"]'
                    ).click();
                }"""
            )
            page.wait_for_function(
                """() => window.__card.shadowRoot.querySelector(
                    '.form--inline input[data-field="notes"]'
                ) != null"""
            )
            # Focus the notes input.
            page.evaluate(
                """() => {
                    const inp = window.__card.shadowRoot.querySelector(
                        '.form--inline input[data-field="notes"]');
                    inp.focus();
                }"""
            )
            # Force a list re-render.
            page.evaluate(
                """() => {
                    const card = document.querySelector(
                        'k-outage-scenarios-card');
                    card._refresh();
                }"""
            )
            page.wait_for_function(
                """() => window.__harness.calls.some(
                    c => c.service === 'list_outage_events'
                )"""
            )
            # Check that focus is still on the notes
            # input (via ``shadowRoot.activeElement``
            # because shadow-DOM activeElement
            # semantics).
            focused = page.evaluate(
                """() => {
                    const root = document.querySelector(
                        'k-outage-scenarios-card').shadowRoot;
                    const ae = root.activeElement;
                    if (!ae) return null;
                    return ae.getAttribute
                        && ae.getAttribute('data-field');
                }"""
            )
            self.assertEqual(
                focused, "notes",
                f"DEFECT 5 round 4: focus was lost "
                f"during the list re-render; the "
                f"shadow-DOM active element is now "
                f"{focused!r}, expected 'notes'",
            )
        finally:
            try:
                ctx.close()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
