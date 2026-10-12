"""Unit tests for scripts/patch_lovelace_ai_cards.py (v2, WS-based).

These tests exercise the *library parts* of the patcher that don't need
a live Home Assistant: card scanning, entity-id substitution, duplicate
detection, backup readback, secret redaction, etc. The full HTTP / WS
loop is covered by ``probe_r01_live.py`` / ``probe_r03_live.py``.
"""
from __future__ import annotations

import importlib
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import the script as a module — runs ``scripts/`` path-searchable.
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

# Clean any cached import so we get the on-disk version.
sys.modules.pop("patch_lovelace_ai_cards", None)
mod = importlib.import_module("patch_lovelace_ai_cards")
# Aliases for readability inside the tests.
ALL_TITLES = mod.ALL_CARD_TITLES
CARD_RECOMMENDATION = mod.CARD_RECOMMENDATION
CARD_LEARNING = mod.CARD_LEARNING
CARD_SOC = mod.CARD_SOC


# --------------------------------------------------------------------------- #
# Small fixtures — minimal Lovelace-shaped configs.
# --------------------------------------------------------------------------- #


def _minimal_dashboard_with_three_cards(
    *, add_duplicate_soc: bool = False,
) -> dict[str, object]:
    """A minimal storage-mode dashboard config carrying our three target
    cards (and a 4th SOC copy when ``add_duplicate_soc`` is True)."""
    soc = {
        "type": "markdown",
        "title": CARD_SOC,
        "content": "old SOC content",
    }
    cards: list[dict[str, object]] = [
        {"type": "entities", "title": CARD_RECOMMENDATION,
         "entities": ["sensor.smart_solar_inverter_predictive_decision_state"]},
        {"type": "markdown", "title": CARD_LEARNING,
         "content": "old learning content"},
        soc,
    ]
    if add_duplicate_soc:
        cards.append({"type": "markdown", "title": CARD_SOC,
                      "content": "SECOND SOC"})
    return {
        "views": [
            {"title": "HEMS", "sections": [{"type": "grid", "cards": cards}]},
            # Nested stacks to stress the recursive scanner.
            {"title": "Other", "sections": [{
                "type": "grid",
                "cards": [
                    {"type": "horizontal-stack",
                     "cards": [{"type": "markdown", "title": "Other card",
                                "content": "ignore me"}]},
                ],
            }]},
        ],
    }


# --------------------------------------------------------------------------- #
# test_extract_card_titles_finds_all_three
# --------------------------------------------------------------------------- #


class TestExtractCardTitles(unittest.TestCase):
    def test_extract_card_titles_finds_all_three(self) -> None:
        config = _minimal_dashboard_with_three_cards()
        titles = sorted(
            card["title"] for card in mod._iter_cards(config)
            if card.get("title") in ALL_TITLES
        )
        self.assertEqual(titles, sorted(ALL_TITLES))

    def test_extract_card_titles_finds_nested_cards(self) -> None:
        """The walker must descend into nested stacks/sections so that a
        duplicate buried inside ``horizontal-stack`` is still detected."""
        config = {
            "views": [{"sections": [{
                "cards": [{"type": "horizontal-stack", "cards": [
                    {"type": "markdown", "title": CARD_SOC, "content": "x"},
                ]}],
            }]},
            {"sections": [{"cards": [
                {"type": "markdown", "title": CARD_SOC, "content": "y"},
            ]}]}],
        }
        titles = [
            card["title"] for card in mod._iter_cards(config)
            if card.get("title") == CARD_SOC
        ]
        self.assertEqual(len(titles), 2)


# --------------------------------------------------------------------------- #
# test_replace_card_body_idempotent
# --------------------------------------------------------------------------- #


class TestReplaceCardBodyIdempotent(unittest.TestCase):
    def test_replace_card_body_idempotent(self) -> None:
        """Running ``patch_cards`` twice must produce the same final
        dashboard and not duplicate anything."""
        config = _minimal_dashboard_with_three_cards()
        expected = mod.load_expected_cards()
        # Substitute placeholder template names first, since expected
        # uses them too.
        cfg_a = json.loads(json.dumps(config))
        cfg_a, stats1 = mod.patch_cards(cfg_a, expected)
        # Run again — the second run should report everything
        # "already_up_to_date", i.e. zero ``replaced``.
        cfg_b = json.loads(json.dumps(config))
        # Apply once to make cfg_b == cfg_a baseline.
        cfg_b, _ = mod.patch_cards(cfg_b, expected)
        cfg_b2, stats2 = mod.patch_cards(cfg_b, expected)
        self.assertEqual(sorted(stats1["replaced"]), sorted(ALL_TITLES))
        self.assertEqual(stats2["replaced"], [])
        self.assertEqual(
            sorted(stats2["already_up_to_date"]),
            sorted(ALL_TITLES),
        )
        self.assertEqual(
            json.dumps(cfg_a, sort_keys=True),
            json.dumps(cfg_b2, sort_keys=True),
        )


# --------------------------------------------------------------------------- #
# test_no_duplicate_soc_card_after_patch
# --------------------------------------------------------------------------- #


class TestNoDuplicateSocCard(unittest.TestCase):
    def test_no_duplicate_soc_card_after_patch(self) -> None:
        """If the starting dashboard already carries 2× AI · SOC-стан
        cards, ``_find_duplicate_card_titles`` must report
        ``{CARD_SOC: 2}`` so the CLI aborts."""
        config = _minimal_dashboard_with_three_cards(add_duplicate_soc=True)
        counts = mod._find_duplicate_card_titles(config, ALL_TITLES)
        self.assertEqual(counts[CARD_SOC], 2)
        # Recommendation + Learning must still be unique.
        self.assertEqual(counts[CARD_RECOMMENDATION], 1)
        self.assertEqual(counts[CARD_LEARNING], 1)


# --------------------------------------------------------------------------- #
# test_unknown_reason_rendered_as_diagnostic_code
# + test_compound_reason_split_into_code_and_data
# (The dashboard-side Jinja2 rendering is covered by
#  test_ui_readability.py; here we re-assert the TEXT the script
#  injects into the template includes the macro call.)
# --------------------------------------------------------------------------- #


class TestReasonRenderingContract(unittest.TestCase):
    def test_unknown_reason_rendered_as_diagnostic_code(self) -> None:
        """The dashboard.yaml must call the new ``render_reason`` macro
        for both reason and execution_reason — NOT the old
        ``REASON_LABELS.get(reason, 'Дані недоступні')`` fallback."""
        text = (REPO / "dashboard.yaml").read_text(encoding="utf-8")
        self.assertIn("render_reason(reason)", text)
        self.assertIn("render_reason(execution_reason)", text)
        self.assertNotIn(
            "REASON_LABELS.get(reason, 'Дані недоступні') if reason is not none",
            text,
            msg="Old fallback replaced with the diagnostic-aware macro",
        )
        self.assertNotIn(
            "REASON_LABELS.get(execution_reason, 'Дані недоступні') "
            "if execution_reason is not none",
            text,
        )
        # And the macro must surface unknown codes clearly.
        self.assertIn("Невідомий reason:", text)

    def test_compound_reason_split_into_code_and_data(self) -> None:
        """The render_reason macro must split ``code: details`` into
        the translated head + a labelled ``деталі:`` suffix."""
        text = (REPO / "dashboard.yaml").read_text(encoding="utf-8")
        # The split lives in the macro body.
        self.assertIn("split(':', 1)", text)
        self.assertIn("деталі:", text)
        # And the macro handles the unknown-but-compound branch.
        self.assertIn("Невідомий reason: {{ head }}", text)


# --------------------------------------------------------------------------- #
# test_entity_id_registry_substitution
# --------------------------------------------------------------------------- #


class TestEntityIdRegistrySubstitution(unittest.TestCase):
    def test_entity_id_registry_substitution(self) -> None:
        """Registry entries are flattened to ``{suffix: entity_id}`` and
        used to rewrite template-style names in the dashboard config."""
        # Use the production registry keys: HA's predictive_* entities
        # don't get a ``translation_key``, so we rely on the unique_id
        # suffix fallback.
        entries = [
            {"entity_id": "sensor.smart_solar_inverter_predictive_decision_state",
             "translation_key": None,
             "unique_id": "abcdef_predictive_decision_state"},
            {"entity_id": "select.smart_solar_inverter_predictive_target_soc",
             "translation_key": None,
             "unique_id": "abcdef_predictive_target_soc"},
            {"entity_id": "sensor.smart_solar_inverter_other",
             "translation_key": "other_state",
             "unique_id": "abcdef_other"},
        ]
        registry = mod.build_entity_registry(entries, config_entry_id="abcdef")
        # Both predictive suffixes resolve.
        self.assertIn("decision_state", registry)
        self.assertIn("target_soc", registry)
        self.assertEqual(
            registry["decision_state"],
            "sensor.smart_solar_inverter_predictive_decision_state",
        )
        # translation_key wins on collision.
        self.assertIn("other_state", registry)

    def test_substitute_entity_ids_rewrites_entities_and_string_content(self) -> None:
        """The substitution walks both ``entity: …`` keys AND full-card
        markdown ``content`` strings."""
        registry = {
            "decision_state": "sensor.actual_predictive_decision_state",
            "target_soc": "select.actual_predictive_target_soc",
        }
        cfg = {
            "type": "entities",
            "title": "X",
            "entities": [
                {"entity": "sensor.smart_solar_inverter_predictive_decision_state"},
                "sensor.smart_solar_inverter_predictive_decision_state",
            ],
            "content": (
                "Talk about sensor.smart_solar_inverter_predictive_decision_state"
                " and select.smart_solar_inverter_predictive_target_soc."
            ),
        }
        new_cfg, report = mod.substitute_entity_ids(cfg, registry)
        self.assertEqual(
            new_cfg["entities"][0]["entity"],
            "sensor.actual_predictive_decision_state",
        )
        # String items are also rewritten via the markdown content walker.
        self.assertEqual(
            new_cfg["entities"][1],
            "sensor.actual_predictive_decision_state",
        )
        self.assertIn("sensor.actual_predictive_decision_state", new_cfg["content"])
        self.assertIn("select.actual_predictive_target_soc", new_cfg["content"])
        # Report maps template -> actual for diagnostics.
        self.assertIn(
            "sensor.smart_solar_inverter_predictive_decision_state",
            report,
        )


# --------------------------------------------------------------------------- #
# test_no_secret_in_stdout
# --------------------------------------------------------------------------- #


class TestNoSecretInStdout(unittest.TestCase):
    """Even when ``HASS_TOKEN`` is set in env, the script must not write
    the token value to stdout or stderr."""

    def test_help_does_not_leak_env(self) -> None:
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            with patch.object(sys, "argv", ["patch_lovelace_ai_cards.py",
                                              "--help"]):
                try:
                    mod.main([])
                except SystemExit:
                    pass
        self.assertNotIn("verysecrettoken_DO_NOT_LEAK",
                         buf_out.getvalue())
        self.assertNotIn("verysecrettoken_DO_NOT_LEAK",
                         buf_err.getvalue())

    def test_no_token_in_redact_function(self) -> None:
        """``_redact_env_for_log`` must replace secret keys with
        ``<set:N>chars>`` / ``<empty>`` markers, never the raw value."""
        env = {"HASS_TOKEN": "abcdef0123456789", "HASS_URL": "http://x"}
        redacted = mod._redact_env_for_log(env)
        self.assertNotIn("abcdef0123456789", redacted["HASS_TOKEN"])
        self.assertTrue(redacted["HASS_TOKEN"].startswith("<set:"))
        self.assertEqual(redacted["HASS_URL"], "http://x")

    def test_scrub_in_dump_strips_bearer_header(self) -> None:
        """``--print-config`` must redact any bearer / token-looking
        string encountered mid-dump."""
        payload = {"Authorization": "Bearer abcdef0123456789",
                   "title": "x", "entities": ["sensor.thing"]}
        scrubbed = mod._scrub_secrets_in_dump(payload)
        self.assertNotIn("abcdef0123456789", json.dumps(scrubbed))
        self.assertIn("bearer <redacted>", json.dumps(scrubbed).lower())

    def test_env_summary_does_not_expose_token(self) -> None:
        """Even with ``HASS_TOKEN`` set, ``_env_summary`` must only print
        the length of the value."""
        os.environ["HASS_TOKEN"] = "verysecrettoken_X_2026"
        try:
            summary = mod._env_summary()
            self.assertIn("HASS_TOKEN", summary)
            self.assertNotIn("verysecrettoken_X_2026", summary["HASS_TOKEN"])
            # Length encoded as <set:Nchars>.
            self.assertTrue(summary["HASS_TOKEN"].startswith("<set:"))
            self.assertIn("chars>", summary["HASS_TOKEN"])
        finally:
            os.environ.pop("HASS_TOKEN", None)


# --------------------------------------------------------------------------- #
# test_dashboard_yaml_preserves_other_cards
# --------------------------------------------------------------------------- #


class TestDashboardYamlPreservesOtherCards(unittest.TestCase):
    def test_dashboard_yaml_preserves_other_cards(self) -> None:
        """Reading ``dashboard.yaml`` and patching it must only touch
        the three target cards. Every other card's content must be
        byte-identical after ``patch_cards``."""
        config = mod.load_expected_cards()  # expected from yaml
        # Snapshot the other cards from the YAML directly.
        import yaml as _yaml
        full = _yaml.safe_load((REPO / "dashboard.yaml").read_text(encoding="utf-8"))
        others_before: dict[str, str] = {}
        for view in full["views"]:
            for section in view.get("sections", []):
                for card in section.get("cards", []):
                    if card.get("title") not in ALL_TITLES:
                        others_before[card.get("title") or ""] = \
                            mod._canonicalise(card)

        # Take a full clone and patch ONLY the three target cards.
        full_clone = json.loads(json.dumps(full))
        mod.patch_cards(full_clone, config)

        # Now every other card must still canonicalise to the same JSON.
        others_after: dict[str, str] = {}
        for view in full_clone["views"]:
            for section in view.get("sections", []):
                for card in section.get("cards", []):
                    if card.get("title") not in ALL_TITLES:
                        others_after[card.get("title") or ""] = \
                            mod._canonicalise(card)
        # Same set of other-card titles.
        self.assertEqual(
            sorted(others_before),
            sorted(others_after),
            msg="patch added or removed non-target cards",
        )
        # Byte-equal canonical JSON for each preserved card.
        for title, before in others_before.items():
            self.assertEqual(
                before, others_after[title],
                msg=f"Non-target card {title!r} was mutated by patch_cards",
            )


# --------------------------------------------------------------------------- #
# test_no_backup_skip_when_dashboards_changed
# + test_dry_run_does_not_save
# --------------------------------------------------------------------------- #


class TestBackupAndDryRun(unittest.TestCase):
    """The run-helper functions used by ``main`` — exercised by patching
    out the WebSocket / REST layers. We do NOT call ``main()`` directly
    (it would open a network socket); we replace ``LovelaceWSClient``
    with a ``MagicMock`` and inject canned results."""

    def _make_ws_mock(self, *, fetched_config: dict[str, object],
                      second_fetcher_changes: bool = False,
                      save_result: dict[str, object] | None = None,
                      ) -> tuple[dict[str, object], MagicMock]:
        """Return ``(captured, ws_constructor_mock)``. ``captured`` holds
        side-effect artefacts so the test can assert on what
        ``main()`` did."""
        captured: dict[str, object] = {}

        def fake_list_dashboards():
            captured["list_dashboards_called"] = True
            return [{"url_path": "powmr-energy"}]

        def fake_get_config(url_path):
            captured["_get_calls"] = captured.get("_get_calls", 0) + 1
            captured["_last_url_path"] = url_path
            if captured["_get_calls"] == 1 or not second_fetcher_changes:
                return {"config": fetched_config, "mode": "storage"}
            tweaked = json.loads(json.dumps(fetched_config))
            tweaked.setdefault("views", [{}])[0]["title"] = "raced"
            return {"config": tweaked, "mode": "storage"}

        def fake_save_config(url_path, cfg):
            captured["_saved_config"] = cfg
            captured["_saved_path"] = url_path
            return save_result

        def fake_list_entity_registry():
            captured["_entity_registry_called"] = True
            return [
                {"entity_id": "sensor.garazh_smart_solar_inverter_predictive_decision_state",
                 "unique_id": "testentry_predictive_decision_state",
                 "config_entry_id": "testentry", "platform": "powmr_inverter"},
                {"entity_id": "select.garazh_smart_solar_inverter_predictive_target_soc",
                 "unique_id": "testentry_predictive_target_soc",
                 "config_entry_id": "testentry", "platform": "powmr_inverter"},
            ]

        ws_inst = MagicMock()
        ws_inst.connect = MagicMock()
        ws_inst.close = MagicMock()
        ws_inst.list_dashboards.side_effect = fake_list_dashboards
        ws_inst.get_config.side_effect = fake_get_config
        ws_inst.save_config.side_effect = fake_save_config
        ws_inst.list_entity_registry.side_effect = fake_list_entity_registry
        return captured, ws_inst

    def test_no_backup_skip_when_dashboards_changed(self) -> None:
        """If the second ``get_config`` call returns a different
        snapshot, ``main`` must abort with exit code != 0 and must NOT
        call ``save_config``."""
        fetched = _minimal_dashboard_with_three_cards()
        captured, ws_inst = self._make_ws_mock(
            fetched_config=fetched, second_fetcher_changes=True,
        )

        with patch.object(mod, "LovelaceWSClient") as WSMock:
            WSMock.return_value = ws_inst
            with patch.object(sys, "argv",
                              ["patch_lovelace_ai_cards.py",
                               "--dashboard", "powmr-energy",
                               "--ha-url", "http://h", "--ha-token", "t",
                               "--no-backup"]):
                rc = -1  # sentinel
                abort_arg: str | None = None
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    try:
                        rc = mod.main()
                    except SystemExit as exc:
                        rc = exc.code if isinstance(exc.code, int) else 1
                        abort_arg = str(exc)
                # ``main()`` aborted with non-zero and never persisted.
                self.assertNotEqual(rc, 0, msg="main must abort on race condition")
                self.assertIn("ABORT", (abort_arg or ""))
        # save_config must not be called on the abort path.
        self.assertNotIn("_saved_config", captured)
        ws_inst.save_config.assert_not_called()

    def test_dry_run_does_not_save(self) -> None:
        """``--dry-run`` should compute the patch + write a backup but
        never call ``save_config``."""
        fetched = _minimal_dashboard_with_three_cards()
        captured, ws_inst = self._make_ws_mock(fetched_config=fetched)

        with patch.object(mod, "LovelaceWSClient") as WSMock:
            WSMock.return_value = ws_inst
            with patch.object(sys, "argv",
                              ["patch_lovelace_ai_cards.py",
                               "--dashboard", "powmr-energy",
                               "--ha-url", "http://h", "--ha-token", "t",
                               "--dry-run"]):
                buf_out, buf_err = io.StringIO(), io.StringIO()
                with redirect_stdout(buf_out), redirect_stderr(buf_err):
                    rc = mod.main()
        self.assertEqual(rc, 0)
        self.assertIn("Dry run", buf_out.getvalue())
        ws_inst.save_config.assert_not_called()


# --------------------------------------------------------------------------- #
# Bonus: test the WS-client URL builder picks the right scheme.
# --------------------------------------------------------------------------- #


class TestWSClientURL(unittest.TestCase):
    def test_http_url_uses_ws(self) -> None:
        c = mod.LovelaceWSClient("http://home.local:8123", "x")
        self.assertTrue(c._build_ws_url().startswith("ws://"))

    def test_https_url_uses_wss(self) -> None:
        c = mod.LovelaceWSClient("https://home.local", "x")
        self.assertTrue(c._build_ws_url().startswith("wss://"))
        self.assertIn(":443", c._build_ws_url())

    def test_insecure_transport_overrides(self) -> None:
        c = mod.LovelaceWSClient("https://home.local", "x",
                                  insecure_transport=True)
        self.assertTrue(c._build_ws_url().startswith("ws://"))


if __name__ == "__main__":
    unittest.main()
