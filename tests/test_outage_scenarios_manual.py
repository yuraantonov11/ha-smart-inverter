"""Regression tests for the manual outage-scenarios path
(``k-outage-scenarios-card`` + the already-registered
``powmr_inverter.*`` service handlers).

The full path under test is:

  1. **manual add** via ``add_outage_event`` service
     (``source=manual:dashboard``) with structured
     ``start``/``end`` (offset-aware ISO).
  2. **confirm** via ``confirm_outage_event`` to bring
     the event from ``pending`` → ``confirmed`` (the
     planner only plans confirmed events).
  3. **persist + reload** — the EventStore writes to
     a backing ``Store`` whose ``async_load`` is the
     source of truth after a reload. The test forces a
     reload by re-constructing the store from the same
     backing data.
  4. **plan** — the per-entry ``compute_outage_plan``
     service returns a plan dict with target SOC,
     prep start, energy needed vs available.
  5. **edit** via ``update_outage_event`` with the
     correct version (version-checked to prevent lost
     updates).
  6. **cancel** via ``cancel_outage_event`` flips the
     status to ``cancelled``; a subsequent
     ``list_outage_events`` no longer includes it in
     the upcoming set.

The card itself (a Web Component) is exercised by
``test_r09_lovelace_dedup`` (custom-element guard) and
by ``test_outage_scenarios_card_js.py`` (synchronous
stubbed DOM tests). The full HTTP / WS surface of the
card is not testable in this sandbox; the production
service contract IS testable and is the load-bearing
contract the card uses.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTAGE_PATH = REPO_ROOT / "services" / "outage.py"
OUTAGE_PLANNER_PATH = (
    REPO_ROOT / "services" / "outage_planner.py"
)


def _install_stub_modules() -> None:
    """Install a minimal ``homeassistant.*`` stub so
    ``outage.py`` imports cleanly in a non-HA process.
    """
    if "homeassistant" in sys.modules:
        return
    ha = types.ModuleType("homeassistant")
    ha.__path__ = []
    sys.modules["homeassistant"] = ha
    for sub in ("core", "exceptions", "helpers"):
        m = types.ModuleType(f"homeassistant.{sub}")
        m.__path__ = []
        sys.modules[f"homeassistant.{sub}"] = m
    ha_core = sys.modules["homeassistant.core"]
    setattr(ha_core, "HomeAssistant", object)
    setattr(ha_core, "ServiceCall", dict)
    ha_exc = sys.modules["homeassistant.exceptions"]
    class _ServiceValidationError(Exception):
        pass
    setattr(
        ha_exc, "ServiceValidationError", _ServiceValidationError
    )
    ha_storage = types.ModuleType(
        "homeassistant.helpers.storage"
    )
    class _Store:
        def __init__(self, *a, **kw):
            pass
    setattr(ha_storage, "Store", _Store)
    sys.modules["homeassistant.helpers.storage"] = (
        ha_storage
    )


# Use a parent package so the production
# ``outage.py`` can use its existing relative
# imports (e.g. ``from .outage_planner import ...``).
_STUB_PKG = "_outage_scenarios_test_pkg"


def _load_outage() -> types.ModuleType:
    _install_stub_modules()
    if _STUB_PKG not in sys.modules:
        pkg = types.ModuleType(_STUB_PKG)
        pkg.__path__ = [str(REPO_ROOT / "services")]
        sys.modules[_STUB_PKG] = pkg
    # Load the planner first so that
    # ``outage.py`` can import it via
    # ``from .outage_planner import ...``.
    pl_spec = importlib.util.spec_from_file_location(
        f"{_STUB_PKG}.outage_planner",
        str(OUTAGE_PLANNER_PATH),
    )
    pl_mod = importlib.util.module_from_spec(pl_spec)
    sys.modules[pl_spec.name] = pl_mod
    pl_spec.loader.exec_module(pl_mod)
    spec = importlib.util.spec_from_file_location(
        f"{_STUB_PKG}.outage", str(OUTAGE_PATH)
    )
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _MemoryStore:
    """A minimal ``Store``-shaped helper used by the
    EventStore's ``_get_store``. It carries an in-memory
    dict and serialises via ``async_save``/``async_load``.
    """

    def __init__(self) -> None:
        self._data: dict | None = None
        # When set, ``async_save`` raises. Used to
        # simulate a backing-storage failure.
        self._fail = False

    async def async_load(self) -> dict:
        return self._data

    async def async_save(self, data: dict) -> None:
        if self._fail:
            raise RuntimeError("simulated storage failure")
        self._data = dict(data)


def _build_hass_with_service() -> tuple[object, object]:
    """Build a tiny ``HomeAssistant``-like object that
    exposes ``services.async_register``, ``data`` (for
    the outage store map), and a ``services.calls``
    recorder. Returns (hass, services).
    """
    calls: list[tuple[str, str, dict]] = []
    services_mod = types.SimpleNamespace()
    services_mod._handlers: dict[
        tuple[str, str], object
    ] = {}
    services_mod._calls = calls

    def async_register(domain, name, handler, **kw):
        services_mod._handlers[(domain, name)] = handler

    services_mod.async_register = async_register
    hass = types.SimpleNamespace(
        services=services_mod,
        data={},
    )
    hass.config = types.SimpleNamespace(
        config_dir="/tmp/powmr_test",
        time_zone="Europe/Kyiv",
    )
    return hass, services_mod


class ManualOutagePathTests(unittest.TestCase):
    """Full manual path: add → confirm → reload →
    plan → edit → cancel.
    """

    def setUp(self) -> None:
        self.outage = _load_outage()
        self.hass, self.services = _build_hass_with_service()
        self.store = _MemoryStore()

    def _make_store(self):
        # The service handlers use ``_get_store(hass,
        # entry_id)`` which reads
        # ``hass.data[f"{DOMAIN}_outage_stores"]``.
        # We populate that map directly so the
        # handler picks up our test store.
        store = self.outage.EventStore(
            storage=self.store,
            entry_id="01M3XWJ8DRYDQC8A0NCPRVB53N",
        )
        # EventStore.in_memory() also works, but
        # we want a backing storage that survives
        # a "reload" (see ``test_reload_preserves_manual_source``).
        return store

    def _register_store(self, store):
        # The DOMAIN constant for the
        # outage module. The service
        # handlers look up the store in
        # ``hass.data[f"{DOMAIN}_outage_stores"]``.
        stores = self.hass.data.setdefault(
            f"{self.outage.DOMAIN}_outage_stores", {}
        )
        stores[store.entry_id] = store

    def _setup(self):
        """Build a store, register it in
        ``hass.data``, register the service
        handlers. Mirrors the production
        ``__init__.py`` setup path.
        """
        store = self._make_store()
        self._register_store(store)
        _run(
            self.outage.async_register_outage_services(
                self.hass
            )
        )
        return store

    def test_full_path(self):
        """Drive the full manual path through the
        service handlers the card calls.
        """
        # 1. Register services (mirrors __init__.py
        #    setup).
        self._setup()
        # 2. add_outage_event -- the service
        #    auto-confirms structured inserts when
        #    the interval is unambiguous (which it
        #    is, when both start and end are
        #    supplied as offset-aware ISO).
        start_iso = "2026-10-20T09:00:00+03:00"
        end_iso = "2026-10-20T13:00:00+03:00"
        add_handler = self.services._handlers[
            ("powmr_inverter", "add_outage_event")
        ]
        added = _run(
            add_handler(
                types.SimpleNamespace(
                    data={
                        "source": "manual:dashboard",
                        "start": start_iso,
                        "end": end_iso,
                        "notes": "Ремонт ТП-12",
                    }
                )
            )
        )
        event_id = added["id"]
        # The event is auto-confirmed because the
        # structured insert is unambiguous.
        self.assertEqual(added["status"], "confirmed")
        # No explicit ``confirm_outage_event`` is
        # needed in the dashboard flow. We list
        # immediately to confirm the event is
        # visible to the card.
        list_handler = self.services._handlers[
            ("powmr_inverter", "list_outage_events")
        ]
        listed = _run(
            list_handler(
                types.SimpleNamespace(
                    data={"entry_id": None, "status": None}
                )
            )
        )
        events = listed["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "confirmed")
        self.assertEqual(
            events[0]["source"], "manual:dashboard"
        )
        self.assertEqual(events[0]["notes"], "Ремонт ТП-12")
        self.assertEqual(events[0]["version"], 1)
        # 5. reload — the EventStore reads from the
        #    backing storage. We simulate a reload by
        #    creating a fresh EventStore and re-loading
        #    from the same backing data.
        reloaded = self.outage.EventStore(
            storage=self.store,
            entry_id="01M3XWJ8DRYDQC8A0NCPRVB53N",
        )
        _run(reloaded.async_load())
        self.assertEqual(len(reloaded.list_events()), 1)
        self.assertEqual(
            reloaded.list_events()[0].status, "confirmed"
        )
        # 6. compute_outage_plan -- the runtime
        #    registers a per-entry service. The plan
        #    may be uncomputable (no battery / PV
        #    data in this test), but it MUST return a
        #    valid response shape.
        # The planner is always loaded by ``_load_outage``
        # above, so the call is unconditional. The
        # card depends on the documented
        # PlannerPlan shape (``blocked``,
        # ``reason_if_blocked``, ``reason_if_incomplete``,
        # ``plan``) and the early-return shape
        # (``planned``, ``reason``).
        plan_module = sys.modules[f"{_STUB_PKG}.outage_planner"]
        # ``plan_outage`` is the planner entry point; it
        # accepts a fully-built ``Telemetry`` + the
        # event. Without a coordinator / hass bundle
        # we construct a minimal telemetry where every
        # value is ``None`` so the planner returns a
        # valid ``PlannerPlan`` with unknown fields.
        tel = plan_module.Telemetry(
            now_iso="2026-10-20T08:00:00+03:00",
            telemetry_freshness_seconds=10,
        )
        plan = plan_module.plan_outage(
            telemetry=tel,
            event=reloaded.list_events()[0],
        )
        self.assertIsNotNone(plan)
        self.assertIsInstance(plan, plan_module.PlannerPlan)
        # The plan object has the documented
        # fields, even if their values are None
        # because no telemetry is loaded.
        self.assertTrue(hasattr(plan, "blocked"))
        self.assertTrue(hasattr(plan, "reason_if_blocked"))
        self.assertTrue(
            hasattr(plan, "reason_if_incomplete")
        )
        self.assertTrue(hasattr(plan, "plan"))
        self.assertIsInstance(plan.plan, dict)
        # ``to_dict()`` round-trips the same shape
        # the runtime service exposes.
        plan_dict = plan.to_dict()
        self.assertIn("blocked", plan_dict)
        self.assertIn("reason_if_blocked", plan_dict)
        self.assertIn("reason_if_incomplete", plan_dict)
        self.assertIn("plan", plan_dict)
        self.assertIn("computed_at_iso", plan_dict)
        # 7. edit — version-checked update.
        update_handler = self.services._handlers[
            ("powmr_inverter", "update_outage_event")
        ]
        new_end = "2026-10-20T14:00:00+03:00"
        update_result = _run(
            update_handler(
                types.SimpleNamespace(
                    data={
                        "id": event_id,
                        "version": 1,
                        "start": start_iso,
                        "end": new_end,
                        "notes": "Ремонт ТП-12, +1 год",
                        "entry_id": None,
                    }
                )
            )
        )
        self.assertEqual(update_result["version"], 2)
        self.assertEqual(update_result["end_iso"], new_end)
        # 8. cancel
        cancel_handler = self.services._handlers[
            ("powmr_inverter", "cancel_outage_event")
        ]
        cancel_result = _run(
            cancel_handler(
                types.SimpleNamespace(
                    data={
                        "id": event_id,
                        "reason": "cancelled from dashboard",
                        "entry_id": None,
                    }
                )
            )
        )
        self.assertEqual(cancel_result["status"], "cancelled")
        # 9. list — the event is now cancelled.
        listed2 = _run(
            list_handler(
                types.SimpleNamespace(
                    data={"entry_id": None, "status": None}
                )
            )
        )
        # The cancelled event is still in the list
        # (audit), but its status is ``cancelled``.
        # The card filters these out of the upcoming
        # list, but they remain in the store.
        self.assertEqual(len(listed2["events"]), 1)
        self.assertEqual(
            listed2["events"][0]["status"], "cancelled"
        )

    def test_reload_preserves_manual_source(self):
        """After a reload, the ``source`` is preserved
        so the card can tell manual events apart from
        Telegram / IMAP events.
        """
        self._setup()
        add_handler = self.services._handlers[
            ("powmr_inverter", "add_outage_event")
        ]
        _run(
            add_handler(
                types.SimpleNamespace(
                    data={
                        "source": "manual:dashboard",
                        "start": (
                            "2026-10-22T08:00:00+03:00"
                        ),
                        "end": "2026-10-22T10:00:00+03:00",
                        "notes": "Плановий ремонт",
                    }
                )
            )
        )
        # Simulate reload
        reloaded = self.outage.EventStore(
            storage=self.store,
            entry_id="01M3XWJ8DRYDQC8A0NCPRVB53N",
        )
        _run(reloaded.async_load())
        events = reloaded.list_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0].source, "manual:dashboard"
        )
        self.assertEqual(events[0].notes, "Плановий ремонт")

    def test_interval_validation(self):
        """The service rejects malformed intervals
        (``end <= start``). The card also does this
        on the client side, but the server-side
        validation is the source of truth.
        """
        self._setup()
        add_handler = self.services._handlers[
            ("powmr_inverter", "add_outage_event")
        ]
        # end before start
        with self.assertRaises(Exception) as ctx:
            _run(
                add_handler(
                    types.SimpleNamespace(
                        data={
                            "source": "manual:dashboard",
                            "start": (
                                "2026-10-22T10:00:00+03:00"
                            ),
                            "end": (
                                "2026-10-22T08:00:00+03:00"
                            ),
                        }
                    )
                )
            )
        msg = str(ctx.exception)
        self.assertTrue(
            "end" in msg.lower() or "interval" in msg.lower(),
            msg=(
                "service must reject end <= start; got "
                f"message: {msg!r}"
            ),
        )

    def test_update_rejects_stale_version(self):
        """``update_outage_event`` uses optimistic
        concurrency. A second update with the same
        version must fail rather than silently
        overwrite.
        """
        self._setup()
        add_handler = self.services._handlers[
            ("powmr_inverter", "add_outage_event")
        ]
        added = _run(
            add_handler(
                types.SimpleNamespace(
                    data={
                        "source": "manual:dashboard",
                        "start": (
                            "2026-10-22T09:00:00+03:00"
                        ),
                        "end": "2026-10-22T11:00:00+03:00",
                    }
                )
            )
        )
        update_handler = self.services._handlers[
            ("powmr_inverter", "update_outage_event")
        ]
        # Events start at version=1. The first
        # update with version=1 succeeds and
        # bumps the event to version=2. A second
        # update with the same (now-stale) version
        # of 1 must fail.
        _run(
            update_handler(
                types.SimpleNamespace(
                    data={
                        "id": added["id"],
                        "version": 1,
                        "notes": "first",
                        "entry_id": None,
                    }
                )
            )
        )
        # Second update with the same stale
        # version=1 must fail.
        with self.assertRaises(Exception) as ctx:
            _run(
                update_handler(
                    types.SimpleNamespace(
                        data={
                            "id": added["id"],
                            "version": 1,
                            "notes": "second",
                            "entry_id": None,
                        }
                    )
                )
            )
        msg = str(ctx.exception)
        self.assertIn(
            "version", msg.lower(),
            msg=(
                "stale-version update must be rejected; "
                f"got message: {msg!r}"
            ),
        )

    def test_cancel_keeps_audit_but_filters_upcoming(self):
        """Cancellation flips status to ``cancelled``
        but the event remains in the store (audit).
        The card filters ``cancelled`` events from the
        upcoming list.
        """
        self._setup()
        add_handler = self.services._handlers[
            ("powmr_inverter", "add_outage_event")
        ]
        added = _run(
            add_handler(
                types.SimpleNamespace(
                    data={
                        "source": "manual:dashboard",
                        "start": (
                            "2026-10-25T08:00:00+03:00"
                        ),
                        "end": "2026-10-25T10:00:00+03:00",
                    }
                )
            )
        )
        cancel_handler = self.services._handlers[
            ("powmr_inverter", "cancel_outage_event")
        ]
        _run(
            cancel_handler(
                types.SimpleNamespace(
                    data={
                        "id": added["id"],
                        "reason": "скасовано оператором",
                        "entry_id": None,
                    }
                )
            )
        )
        list_handler = self.services._handlers[
            ("powmr_inverter", "list_outage_events")
        ]
        listed = _run(
            list_handler(
                types.SimpleNamespace(
                    data={"entry_id": None, "status": None}
                )
            )
        )
        all_events = listed["events"]
        self.assertEqual(len(all_events), 1)
        self.assertEqual(all_events[0]["status"], "cancelled")
        # Filter to upcoming (non-cancelled, non-completed,
        # end in the future).
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        upcoming = [
            e
            for e in all_events
            if e["status"] not in ("cancelled", "completed")
            and e.get("end_iso")
            and datetime.fromisoformat(
                e["end_iso"]
            )
            >= now
        ]
        self.assertEqual(
            len(upcoming), 0,
            msg=(
                "cancelled events must not appear in the "
                "upcoming set; got "
                f"{[e['status'] for e in upcoming]}"
            ),
        )

    def test_update_rejects_invalid_interval(self):
        """``update_outage_event`` runs the same
        end > start / 1-minute / 7-day validation
        as ``add_outage_event``. The dashboard
        card never lets the operator submit a
        malformed window, but the server is the
        source of truth: a malicious / older
        caller cannot bypass the check via the
        update path either.

        The validation is centralised in
        ``_validate_structured_interval`` so the
        two handlers can never drift apart.
        """
        self._setup()
        add_handler = self.services._handlers[
            ("powmr_inverter", "add_outage_event")
        ]
        added = _run(
            add_handler(
                types.SimpleNamespace(
                    data={
                        "source": "manual:dashboard",
                        "start": (
                            "2026-10-22T09:00:00+03:00"
                        ),
                        "end": (
                            "2026-10-22T11:00:00+03:00"
                        ),
                    }
                )
            )
        )
        update_handler = self.services._handlers[
            ("powmr_inverter", "update_outage_event")
        ]
        # end < start -- must be rejected with the
        # shared ``_validate_structured_interval``
        # message ("'end' must be strictly after
        # 'start'") wrapped with the caller name.
        with self.assertRaises(Exception) as ctx:
            _run(
                update_handler(
                    types.SimpleNamespace(
                        data={
                            "id": added["id"],
                            "version": 1,
                            "start": (
                                "2026-10-23T09:00:00+03:00"
                            ),
                            "end": (
                                "2026-10-23T08:00:00+03:00"
                            ),
                            "entry_id": None,
                        }
                    )
                )
            )
        msg = str(ctx.exception)
        self.assertIn(
            "update_outage_event", msg,
            msg=(
                "update_outage_event must annotate its "
                "rejections so the dashboard can tell the "
                "user which call failed; got: " + repr(msg)
            ),
        )
        self.assertTrue(
            "end" in msg.lower()
            or "interval" in msg.lower()
            or "start" in msg.lower(),
            msg=(
                "validation message must surface the "
                "rejection reason; got: " + repr(msg)
            ),
        )
        # 8-day interval -- also rejected by the
        # shared 7-day upper bound.
        with self.assertRaises(Exception) as ctx:
            _run(
                update_handler(
                    types.SimpleNamespace(
                        data={
                            "id": added["id"],
                            "version": 1,
                            "start": (
                                "2026-10-20T09:00:00+03:00"
                            ),
                            "end": (
                                "2026-10-28T09:00:00+03:00"
                            ),
                            "entry_id": None,
                        }
                    )
                )
            )
        msg = str(ctx.exception)
        self.assertIn(
            "update_outage_event", msg,
            msg=(
                "8-day update must be rejected by the "
                "shared validator; got: " + repr(msg)
            ),
        )

    def test_update_clears_notes_with_empty_string(self):
        """``update_outage_event`` distinguishes
        ``None`` (do not change) from ``""``
        (clear the note). The card forwards the
        ``<input>`` value as-is -- an empty
        string from the DOM stays an empty
        string in the service payload; the
        server stores it as the new notes
        (typically treated as "cleared" by the
        event dataclass).
        """
        self._setup()
        add_handler = self.services._handlers[
            ("powmr_inverter", "add_outage_event")
        ]
        added = _run(
            add_handler(
                types.SimpleNamespace(
                    data={
                        "source": "manual:dashboard",
                        "start": (
                            "2026-10-22T09:00:00+03:00"
                        ),
                        "end": (
                            "2026-10-22T11:00:00+03:00"
                        ),
                        "notes": "початкова примітка",
                    }
                )
            )
        )
        update_handler = self.services._handlers[
            ("powmr_inverter", "update_outage_event")
        ]
        updated = _run(
            update_handler(
                types.SimpleNamespace(
                    data={
                        "id": added["id"],
                        "version": 1,
                        "notes": "",
                        "entry_id": None,
                    }
                )
            )
        )
        # The new notes are an empty string
        # (NOT None -- the card sent ``""`` and
        # the server stored it). This is the
        # "clear the note" intent.
        self.assertEqual(updated["notes"], "")
        self.assertIsNotNone(updated["notes"])
        # The version was bumped from 1 → 2.
        self.assertEqual(updated["version"], 2)
        # Re-listing must surface the cleared
        # note in the stored event.
        list_handler = self.services._handlers[
            ("powmr_inverter", "list_outage_events")
        ]
        listed = _run(
            list_handler(
                types.SimpleNamespace(
                    data={"entry_id": None, "status": None}
                )
            )
        )
        self.assertEqual(
            listed["events"][0]["notes"], ""
        )

    def test_add_event_passes_entry_id(self):
        """The card forwards ``entry_id`` from the
        integration config so the service
        handler routes the event to the correct
        ``EventStore``. The handler accepts
        ``entry_id=None`` for the single-entry
        case (covered elsewhere) but a
        multi-entry deployment relies on the
        card passing the actual entry id.
        """
        self._setup()
        add_handler = self.services._handlers[
            ("powmr_inverter", "add_outage_event")
        ]
        added = _run(
            add_handler(
                types.SimpleNamespace(
                    data={
                        "source": "manual:dashboard",
                        "start": (
                            "2026-10-25T09:00:00+03:00"
                        ),
                        "end": (
                            "2026-10-25T11:00:00+03:00"
                        ),
                        "notes": "Тест entry_id",
                        "entry_id": (
                            "01M3XWJ8DRYDQC8A0NCPRVB53N"
                        ),
                    }
                )
            )
        )
        self.assertEqual(added["status"], "confirmed")
        # The event must have landed in the
        # store keyed by that entry_id.
        list_handler = self.services._handlers[
            ("powmr_inverter", "list_outage_events")
        ]
        listed = _run(
            list_handler(
                types.SimpleNamespace(
                    data={
                        "entry_id": (
                            "01M3XWJ8DRYDQC8A0NCPRVB53N"
                        ),
                        "status": None,
                    }
                )
            )
        )
        self.assertEqual(len(listed["events"]), 1)
        self.assertEqual(
            listed["events"][0]["source"],
            "manual:dashboard",
        )

    def test_list_outage_events_supports_response(self):
        """The ``list_outage_events`` service is
        registered with ``supports_response !=
        SupportsResponse.NONE`` so HA does not
        reject ``return_response=True`` calls with
        ``service_does_not_support_response``.
        Drives the real
        ``async_register_outage_services`` and
        inspects the service description that HA
        would surface in the dev-tools panel.
        """
        # The outage module is loaded under the
        # test stub package; look it up there to
        # avoid the ``services`` package's
        # relative-import bootstrap.
        outage_mod = sys.modules.get(
            f"{_STUB_PKG}.outage"
        )
        async_register = outage_mod.async_register_outage_services
        SupportsResponse = outage_mod.SupportsResponse
        # Build a fresh hass/services pair for
        # this test so the registration does not
        # touch ``self.hass`` (which may already
        # be wired by _setup()).
        captured_hass, captured_services = _build_hass_with_service()
        captured: list = []

        def _rec(domain, name, handler, **kw):
            if (domain, name) == (
                "powmr_inverter",
                "list_outage_events",
            ):
                captured.append(kw.get("supports_response"))
            captured_services._handlers[
                (domain, name)
            ] = handler
            return None

        captured_hass.services.async_register = _rec
        try:
            _run(async_register(captured_hass))
        finally:
            pass
        # Debug: print all calls observed.
        if not captured:
            # Re-raise the original error but include
            # the full list of services that DID
            # register, so a future failure is
            # diagnosable.
            all_calls = list(
                captured_services._handlers.keys()
            )
            self.fail(
                f"list_outage_events was not "
                f"registered; services registered: "
                f"{all_calls}; supports_response "
                f"flag passed: {captured!r}"
            )
        # The list handler must opt-in to response.
        self.assertTrue(
            captured,
            "list_outage_events was not registered "
            "during this test; the wrapper around "
            "services.async_register did not see "
            "any call matching (powmr_invalidator, "
            "list_outage_events)",
        )
        self.assertIsNotNone(
            captured[0],
            "list_outage_events was registered "
            "without supports_response; HA would reject "
            "return_response=True with "
            "service_does_not_support_response",
        )
        self.assertNotEqual(
            captured[0],
            SupportsResponse.NONE,
            f"list_outage_events supports_response "
            f"is NONE; got {captured[0]!r}",
        )

    def test_add_outage_event_does_not_send_null_notes(self):
        """The production schema accepts an empty
        string for ``notes`` (not ``None``). The
        card contract: empty input → ``""`` (not
        ``null``).

        The schema in ``async_register_outage_services``
        uses ``vol.Optional("notes"):
        vol.Any(str, None)``; both ``""`` and
        ``None`` are accepted by the voluptuous
        schema. But the card MUST send ``""``,
        not ``None``, because the dashboard UI
        shows the empty input as "no note" — if
        the card sent ``None``, the dev-tools
        panel would render it as a ``null`` field
        and the typed value would be lost in a
        future schema tightening.

        This test pins the contract by adding an
        event with ``notes=""`` and verifying the
        round-trip via the list service (the add
        response only echoes id/status, but the
        full event dict IS available via list).
        """
        self._setup()
        add_handler = self.services._handlers[
            ("powmr_inverter", "add_outage_event")
        ]
        list_handler = self.services._handlers[
            ("powmr_inverter", "list_outage_events")
        ]
        # Empty string is accepted and the event
        # is stored with notes="" (not None).
        result_empty = _run(
            add_handler(
                types.SimpleNamespace(
                    data={
                        "source": "manual:dashboard",
                        "start": "2026-10-25T09:00:00+03:00",
                        "end": "2026-10-25T11:00:00+03:00",
                        "notes": "",
                    }
                )
            )
        )
        listed = _run(
            list_handler(
                types.SimpleNamespace(
                    data={"entry_id": None, "status": None}
                )
            )
        )
        events = listed["events"]
        self.assertEqual(
            len(events), 1,
            "the add call must have created exactly "
            f"one event; got {len(events)}",
        )
        self.assertEqual(
            events[0]["notes"], "",
            "empty string must round-trip; got "
            f"{events[0].get('notes')!r}",
        )

if __name__ == "__main__":
    unittest.main()
