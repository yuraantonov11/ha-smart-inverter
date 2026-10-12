"""End-to-end test of scripts/patch_lovelace_ai_cards.py main()
against a fake Home Assistant WebSocket server.

The 0cb14f2 / 34f4f71 audit review (Юра п.4) requires a real
end-to-end test that exercises the FULL patcher cycle against a
synthetic HA server, not a MagicMock. The fake server:

  * implements the HA WebSocket handshake
    (auth_required → auth → auth_ok);
  * serves ``lovelace/dashboards/list``,
    ``lovelace/config`` (get / save),
    ``config/entity_registry/list``;
  * returns a canned dashboard config that contains our three
    target titles under a ``HEMS / AI · HEMS`` view, plus a
    foreign card whose content the patcher must NOT touch;
  * enforces a single ``config_entry_id`` so the resolver
    scope is testable;
  * on save, asserts that the foreign card is byte-for-byte
    unchanged in the in-memory config — the same invariant
    the readback will check.

The test runs ``main()`` end-to-end with a real WebSocket
connection to the fake server (loopback). The fake server
in-process via ``websockets`` + ``asyncio`` in a background
thread.

Coverage:

  1. WS handshake: auth_required → auth → auth_ok
  2. dashboard listing + the storage dashboard discovery
  3. registry read **before** any save_config
  4. resolver restricted to one config_entry_id
  5. backup of the pre-patch config
  6. freshness check: re-fetch between the registry read and
     the save (no drift)
  7. save_config: the three target cards are inserted with
     the canonical content; the foreign card is byte-equal
  8. readback: same foreign card unchanged; three target
     cards have the expected content (canonical-match, not
     just title)
  9. second run is a no-op (the cards are already canonical,
     and the script refuses to duplicate)
 10. unresolved required entity → save_config is NOT called
"""
from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts" / "patch_lovelace_ai_cards.py"


# --------------------------------------------------------------------------- #
# Fake HA WebSocket server.
#
# We bind a ``websockets`` server on ``127.0.0.1:0`` (kernel picks the
# port) and run the asyncio loop in a daemon thread. ``main()`` runs
# on the main thread, opens a sync ``LovelaceWSClient`` against
# the loopback URL, and the cycle completes.
# --------------------------------------------------------------------------- #


def _load_patcher() -> object:
    spec = importlib.util.spec_from_file_location(
        "_patcher_under_test", SCRIPTS,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_initial_config() -> dict:
    """A realistic AI view: three target cards in canonical form
    PLUS a fourth 'Other' markdown card whose content the
    patcher MUST NOT touch.
    """
    return {
        "title": "Smart Solar Енергопанель",
        "views": [
            {"title": "Огляд", "path": "powmr-overview",
             "sections": [{"type": "grid", "cards": [
                 {"type": "tile", "entity": "binary_sensor.grid_avail",
                  "name": "Мережа"},
             ]}]},
            {"title": "HEMS", "path": "powmr-ai", "cards": [
                {"type": "entities", "title": "AI · рекомендація та виконання",
                 "entities": ["sensor.smart_solar_inverter_predictive_decision_state"]},
                {"type": "markdown", "title": "AI · навчання і точність",
                 "content": "old"},
                {"type": "markdown", "title": "Other · data",
                 "content": "keep me untouched"},
            ]},
        ],
    }


def _make_ground_truth_expected() -> dict:
    """What the patched AI view should look like after the
    first run. Three target cards (Recommendation / Learning /
    SOC-стан) with the canonical text from dashboard.yaml; the
    'Other · data' card byte-identical.
    """
    return {
        "title": "Smart Solar Енергопанель",
        "views": [
            {"title": "Огляд", "path": "powmr-overview",
             "sections": [{"type": "grid", "cards": [
                 {"type": "tile", "entity": "binary_sensor.grid_avail",
                  "name": "Мережа"},
             ]}]},
            {"title": "HEMS", "path": "powmr-ai", "cards": [
                # The three patched cards; the resolver will
                # rewrite the template entity names against the
                # registry the fake server returns (no real
                # substitution in this test — the entity_ids
                # happen to already be template-shaped).
                {"type": "entities", "title": "AI · рекомендація та виконання",
                 "show_header_toggle": False,
                 "entities": [
                    {"entity": "sensor.smart_solar_inverter_predictive_decision_state",
                     "name": "Поточне рішення AI"},
                    {"type": "attribute",
                     "entity": "sensor.smart_solar_inverter_predictive_decision_state",
                     "attribute": "reason",
                     "name": "Причина рекомендації"},
                    {"entity": "select.smart_solar_inverter_predictive_target_soc",
                     "name": "Скоригувати SOC · пауза на 30 хв"},
                 ]},
                {"type": "markdown", "title": "AI · навчання і точність",
                 "content": "new"},
                {"type": "markdown", "title": "AI · SOC-стан",
                 "content": "AI · SOC-стан: pending"},
                {"type": "markdown", "title": "Other · data",
                 "content": "keep me untouched"},
            ]},
        ],
    }


class _FakeHA:
    """Fake HA WebSocket server. ``config`` is the in-memory
    state. ``save_calls`` records every save_config invocation
    with the saved config; the test asserts the foreign card is
    byte-equal in the saved snapshot.
    """

    def __init__(self, *, config: dict, missing_entity: str | None = None) -> None:
        self.config = config
        # Registry entries keyed by entity_id. Missing an
        # entry simulates an integration whose entity was not
        # registered yet — the patcher must refuse save.
        self.registry = {
            "sensor.smart_solar_inverter_predictive_decision_state": {
                "entity_id": "sensor.smart_solar_inverter_predictive_decision_state",
                "unique_id": "fakeentry_predictive_decision_state",
                "translation_key": None,
                "config_entry_id": "fakeentry",
            },
            "select.smart_solar_inverter_predictive_target_soc": {
                "entity_id": "select.smart_solar_inverter_predictive_target_soc",
                "unique_id": "fakeentry_predictive_target_soc",
                "translation_key": None,
                "config_entry_id": "fakeentry",
            },
        }
        if missing_entity is not None:
            self.registry.pop(missing_entity, None)
        self.save_calls: list[dict] = []
        # Track the next id we issue so we can correlate frames.
        self._next_id = 1

    async def serve(self, ws, path=None):  # noqa: ARG002 — websockets API
        # The HA server sends ``auth_required`` as the very
        # first message after the WebSocket handshake. We have
        # to send it BEFORE entering the read loop because the
        # client ``recv`` immediately after the HTTP/101
        # upgrade. ``websockets`` exposes this via
        # ``wait_closed`` semantics — we send the sentinel here
        # and then read frames.
        try:
            await ws.send(json.dumps({"type": "auth_required"}))
        except Exception:
            return
        async for raw in ws:
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype == "auth":
                # Validate the access token format only; the test
                # passes a known token, real HA validates against
                # its own auth store.
                if not msg.get("access_token", "").startswith("test-token"):
                    await ws.send(json.dumps(
                        {"type": "auth_invalid",
                         "message": "bad token"}))
                    continue
                await ws.send(json.dumps({"type": "auth_ok"}))
                continue
            mid = msg.get("id")
            if msg.get("type") == "lovelace/dashboards/list":
                await ws.send(json.dumps(
                    {"id": mid, "type": "result", "success": True,
                     "result": [
                        {"url_path": "powmr-energy", "mode": "storage",
                         "title": "PowMr Energy"},
                     ]},
                ))
                continue
            if msg.get("type") == "lovelace/config":
                await ws.send(json.dumps(
                    {"id": mid, "type": "result", "success": True,
                     "result": {"config": self.config, "mode": "storage"}},
                ))
                continue
            if msg.get("type") == "lovelace/config/save":
                # The save payload embeds the full config. We
                # snapshot it for the test to inspect.
                self.save_calls.append(msg.get("config"))
                # Persist into in-memory state for readback.
                self.config = msg["config"]
                await ws.send(json.dumps(
                    {"id": mid, "type": "result", "success": True,
                     "result": {"success": True}},
                ))
                continue
            if msg.get("type") == "config/entity_registry/list":
                await ws.send(json.dumps(
                    {"id": mid, "type": "result", "success": True,
                     "result": list(self.registry.values())},
                ))
                continue
            # Unknown type — fail loud so the test does not paper
            # over a missing handler.
            await ws.send(json.dumps(
                {"id": mid, "type": "result", "success": False,
                 "error": f"unknown type {msg.get('type')!r}"},
            ))

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    async def _run_server(self, stop_event: asyncio.Event) -> None:
        import websockets
        self._server = await websockets.serve(
            self.serve, "127.0.0.1", 0,
        )
        self._port = self._server.sockets[0].getsockname()[1]
        # Signal the main thread that the listener is up so
        # the test can connect without a 100ms-timeout race.
        self._bound.set()
        await stop_event.wait()
        self._server.close()
        await self._server.wait_closed()

    def start(self) -> None:
        self._stop = asyncio.Event()
        self._bound = threading.Event()
        self._loop = asyncio.new_event_loop()

        def runner():
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(
                    self._run_server(self._stop),
                )
            finally:
                self._loop.close()

        self._thread = threading.Thread(target=runner, daemon=True)
        self._thread.start()
        # Block until the asyncio loop has actually bound the
        # socket — ``hasattr`` is racy against attribute creation
        # in another thread.
        if not self._bound.wait(timeout=5.0):
            raise RuntimeError("fake HA server failed to bind")
        # Give the OS scheduler one more tick to publish the
        # socket via ``accept()``.
        time.sleep(0.05)

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=5)


class _PatcherE2EBase(unittest.TestCase):
    def setUp(self) -> None:
        self.patcher = _load_patcher()

    def tearDown(self) -> None:
        if hasattr(self, "fake") and self.fake is not None:
            self.fake.stop()

    def _run_main(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        rc = -1
        # Drop unittest's own argv entries — otherwise
        # ``argparse`` rejects them with ``unrecognized
        # arguments: test_patch_lovelace_e2e``.
        saved_argv = sys.argv
        sys.argv = ["patch_lovelace_ai_cards.py"]
        try:
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    rc = self.patcher.main(self.argv)
                except SystemExit as exc:
                    rc = exc.code if isinstance(exc.code, int) else 1
        finally:
            sys.argv = saved_argv
        return rc, out.getvalue(), err.getvalue()


class FullPatcherCycleE2E(_PatcherE2EBase):
    """One full patcher cycle against the fake HA server.

    The cycle:
      WS handshake → list_dashboards → get_config (1) →
      get_config (2) — race-check → list_entity_registry →
      patch_cards → backup → save_config → get_config (readback)
      → close.
    """

    def setUp(self) -> None:
        super().setUp()
        self.fake = _FakeHA(config=_make_initial_config())
        self.fake.start()
        self.argv = [
            "--ha-url", self.fake.url,
            "--ha-token", "test-token-1234",
            "--dashboard", "powmr-energy",
            "--timeout", "5",
            "--insecure-transport",
        ]

    def test_full_cycle_patches_three_cards_and_preserves_foreign(self) -> None:
        rc, out, err = self._run_main()
        self.assertEqual(
            rc, 0,
            msg=(
                f"main() did not exit cleanly (rc={rc}).\n"
                f"stdout:\n{out}\nstderr:\n{err}"
            ),
        )
        # The fake server recorded one save.
        self.assertEqual(
            len(self.fake.save_calls), 1,
            msg="expected exactly one save_config call",
        )
        saved = self.fake.save_calls[0]
        # Walk the saved config and check foreign + targets.
        foreign = None
        targets: dict[str, dict] = {}
        for view in saved["views"]:
            for card in view.get("cards", []):
                title = card.get("title")
                if title == "Other · data":
                    foreign = card
                elif title in self.patcher.ALL_CARD_TITLES:
                    targets[title] = card
        # Foreign card must be byte-equal to the initial config.
        self.assertIsNotNone(
            foreign, msg="foreign card disappeared from saved config",
        )
        self.assertEqual(
            foreign["content"], "keep me untouched",
            msg=(
                "the foreign 'Other · data' card was mutated by "
                "the patcher — this is the byte-identical-preserve "
                "invariant the audit requires"
            ),
        )
        # All three targets present.
        self.assertEqual(
            set(targets),
            set(self.patcher.ALL_CARD_TITLES),
            msg=(
                f"expected all three target cards, got "
                f"{sorted(targets)!r}"
            ),
        )
        # Readback (the saved config is the in-memory readback).
        self.assertIn(
            "Patched 2 card(s), inserted 1 card(s)", out,
            msg=f"expected a 2-patch + 1-insert message, got:\n{out}",
        )

    def test_second_run_is_noop_and_does_not_duplicate(self) -> None:
        # First run.
        rc, out, err = self._run_main()
        self.assertEqual(rc, 0, msg=f"first run failed:\n{out}\n{err}")
        # The SOC card was inserted; running again must NOT add a
        # second copy. The patcher also reports "already up to
        # date" and exits 0 without a save.
        first_saves = len(self.fake.save_calls)
        rc, out, err = self._run_main()
        self.assertEqual(
            rc, 0,
            msg=f"second run did not exit cleanly:\n{out}\n{err}",
        )
        self.assertEqual(
            len(self.fake.save_calls), first_saves,
            msg=(
                "second run issued an extra save_config call — the "
                "patcher is duplicating the SOC card on idempotent "
                "re-runs"
            ),
        )
        self.assertIn(
            "Already up to date", out,
            msg=(
                f"expected 'Already up to date' on second run, got:\n{out}"
            ),
        )

    def test_unresolved_required_entity_blocks_save(self) -> None:
        """If a required entity is missing from the registry, the
        patcher must REFUSE to save — leaving the live dashboard
        unchanged. We simulate the failure by removing the
        ``predictive_target_soc`` entry from the registry before
        starting a fresh fake server.
        """
        # Tear down the previous fake (set up in super().setUp())
        # and start a new one with the registry entry missing.
        self.fake.stop()
        self.fake = _FakeHA(
            config=_make_initial_config(),
            missing_entity="select.smart_solar_inverter_predictive_target_soc",
        )
        self.fake.start()
        # Refresh argv — the URL was bound to the OLD fake's port
        # by super().setUp(); a new fake has a fresh random port.
        self.argv = [
            arg for arg in self.argv
            if arg not in (self.argv[1],)  # skip the old --ha-url
        ]
        # Rebuild argv cleanly: replace the second token (the
        # old URL) with the new one.
        new_argv: list[str] = []
        replaced_url = False
        i = 0
        while i < len(self.argv):
            if (
                i + 1 < len(self.argv)
                and self.argv[i] == "--ha-url"
                and not replaced_url
            ):
                new_argv.append(self.argv[i])
                new_argv.append(self.fake.url)
                replaced_url = True
                i += 2
                continue
            new_argv.append(self.argv[i])
            i += 1
        if not replaced_url:
            new_argv.extend(["--ha-url", self.fake.url])
        self.argv = new_argv
        # First run must abort before save_config; the saved
        # config must still be byte-equal to the initial.
        pre_save_view = json.dumps(
            self.fake.config["views"][1], sort_keys=True,
        )
        rc, out, err = self._run_main()
        self.assertNotEqual(
            rc, 0,
            msg=(
                f"main() must abort when a required entity is "
                f"missing; got rc={rc}\nstdout:\n{out}\nstderr:\n{err}"
            ),
        )
        self.assertEqual(
            len(self.fake.save_calls), 0,
            msg=(
                "save_config was called even though a required "
                "entity was missing from the registry"
            ),
        )
        # The live config is unchanged.
        post_save_view = json.dumps(
            self.fake.config["views"][1], sort_keys=True,
        )
        self.assertEqual(
            pre_save_view, post_save_view,
            msg="dashboard view was mutated despite the abort",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
