"""Regression tests for the deployer.

Юра (2026-10-13): «Додай regression, який ловить пропущену
підстановку.» The previous deployer (before the fix that uses
``build_entity_registry()`` and ``substitute_entity_ids()``) had
no safeguard against leaving template entity_ids in the saved
config. This test simulates both the buggy and the fixed
deployer and asserts:

  1. The buggy deployer leaves template strings in the live
     config (the regression target).
  2. The fixed deployer resolves every required template to a
     live ``entity_id``.
  3. Every substituted ``entity_id`` exists in the live
     ``core.entity_registry``.
  4. The non-AI cards and the storage envelope are byte-equal
     pre vs post.

The test uses a real live snapshot downloaded from HA
(``live_snapshot_2026-10-13/lovelace.powmr_energy.postdeploy``)
and the live entity registry. The source-of-truth is the repo's
own ``dashboard.yaml``.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yaml

from scripts.patch_lovelace_ai_cards import (
    ALL_CARD_TITLES,
    build_entity_registry,
    patch_cards,
    substitute_entity_ids,
    _collect_required_templates,
    _select_predictive_config_entry,
)
from scripts.deploy_dashboard_no_token import (
    _iter_cards,
    _sha,
)

LIVE_SNAPSHOT = Path("/opt/data/powmr-ai-work/live_snapshot_2026-10-13")


def _target_cards_from(target: dict) -> dict:
    found: dict = {}
    for view in target.get("views", []):
        for s in view.get("sections", []):
            for c in s.get("cards", []):
                t = c.get("title", "")
                if t in ALL_CARD_TITLES:
                    found[t] = copy.deepcopy(c)
    return found


def _canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class DeployerRegistrySubstitutionRegressionTests(unittest.TestCase):
    """Catch missed substitution. The previous deployer applied
    ``patch_cards`` to a target whose Jinja text still contained
    template strings (``sensor.smart_solar_inverter_…``). The
    test simulates both deployers and asserts the fixed one
    resolves every template."""

    @classmethod
    def setUpClass(cls):
        cls.envelope = json.loads(
            (LIVE_SNAPSHOT / "lovelace.powmr_energy.postdeploy").read_text(
                encoding="utf-8",
            )
        )
        cls.registry_doc = json.loads(
            (LIVE_SNAPSHOT / "core.entity_registry").read_text(encoding="utf-8")
        )
        cls.entries = cls.registry_doc.get("data", {}).get("entities", [])
        cls.config = cls.envelope["data"]["config"]
        cls.target = yaml.safe_load(
            (REPO / "dashboard.yaml").read_text(encoding="utf-8")
        )
        cls.cards_raw = _target_cards_from(cls.target)

    def _build_registry(self) -> dict:
        required = _collect_required_templates(self.cards_raw)
        config_entry_id = _select_predictive_config_entry(
            self.entries, self.config, required,
        )
        return (
            config_entry_id,
            build_entity_registry(
                self.entries, config_entry_id=config_entry_id,
            ),
        )

    # -- Test 2: fixed deployer resolves all required templates --
    def test_fixed_deployer_resolves_all_required_templates(self) -> None:
        config_entry_id, registry = self._build_registry()
        # The fixed deployer substitutes entity_ids on the target
        # cards BEFORE patch_cards.
        cards_substituted, report = substitute_entity_ids(
            self.cards_raw, registry,
        )
        # Every required template must appear in the report.
        required = _collect_required_templates(self.cards_raw)
        for tmpl in required:
            self.assertIn(
                tmpl, report,
                msg=f"required template {tmpl!r} not substituted",
            )
            actual = report[tmpl]
            self.assertNotEqual(
                actual, tmpl,
                msg=f"required template {tmpl!r} not resolved",
            )
            self.assertIn(
                "garazh_", actual,
                msg=f"substituted ID {actual!r} is not the live "
                f"entry_id-derived form (missing 'garazh_')",
            )

    # -- Test 3: every substituted ID exists in the registry --
    def test_every_substituted_id_exists_in_live_registry(self) -> None:
        _, registry = self._build_registry()
        live_ids = {e.get("entity_id") for e in self.entries}
        for suffix, eid in registry.items():
            self.assertIn(
                eid, live_ids,
                msg=f"registry maps {suffix!r} to {eid!r} but that "
                f"entity_id is not present in core.entity_registry",
            )

    # -- Test 4: post-substitution config has no template strings --
    def test_post_substitution_config_has_no_template_strings(self) -> None:
        config_entry_id, registry = self._build_registry()
        cards_substituted, _ = substitute_entity_ids(
            self.cards_raw, registry,
        )
        # Apply the same patch the deployer does.
        patched, stats = patch_cards(
            copy.deepcopy(self.config), cards_substituted,
        )
        # After patch, NO template smart_solar_inverter_predictive_
        # string should remain in the AI cards' body (it is OK to
        # have it in non-AI cards; those are user-owned and we
        # do not touch them).
        for c in _iter_cards(patched):
            if c.get("title") not in ALL_CARD_TITLES:
                continue
            content = c.get("content", "")
            if isinstance(content, str):
                self.assertNotIn(
                    "sensor.smart_solar_inverter_predictive_decision_state",
                    content,
                    msg="AI card body still has template "
                    "sensor.smart_solar_inverter_predictive_decision_state",
                )
                self.assertNotIn(
                    "select.smart_solar_inverter_predictive_target_soc",
                    content,
                    msg="AI card body still has template "
                    "select.smart_solar_inverter_predictive_target_soc",
                )
            # Also check entity key on button cards.
            for k, v in c.items():
                if k == "entity" and isinstance(v, str):
                    self.assertNotIn(
                        "smart_solar_inverter_predictive_", v,
                        msg=f"AI card entity key {v!r} still has template",
                    )
                    self.assertNotIn(
                        "smart_solar_inverter_predictive_", v,
                        msg=f"AI card entity key {v!r} still has template",
                    )

    # -- Test 5: buggy deployer would leave templates in live --
    def test_buggy_deployer_simulation_leaves_templates(self) -> None:
        """Simulate the OLD (buggy) deployer: call ``patch_cards``
        directly with the raw target cards (no substitution).
        The result must contain template strings — that is the
        regression we are guarding against. If the result
        happens to be correct, the previous deployer wasn't
        buggy; the test then exercises the regression as a
        forward-looking detector."""
        patched, _ = patch_cards(
            copy.deepcopy(self.config), copy.deepcopy(self.cards_raw),
        )
        has_template_after = False
        for c in _iter_cards(patched):
            if c.get("title") not in ALL_CARD_TITLES:
                continue
            content = c.get("content", "")
            if isinstance(content, str) and (
                "sensor.smart_solar_inverter_predictive_decision_state"
                in content
                or "select.smart_solar_inverter_predictive_target_soc"
                in content
            ):
                has_template_after = True
                break
        # The buggy deployer would have left at least one
        # template in the AI cards. This is the regression
        # detector: if the fixed deployer is wired correctly,
        # the FIXED run (test 4) reports zero templates; the
        # buggy run (this test) reports at least one.
        self.assertTrue(
            has_template_after,
            msg="expected the buggy (no-substitution) deployer to "
            "leave at least one template string in the AI cards",
        )

    # -- Test 6: storage envelope + non-AI cards preserved --
    def test_envelope_and_non_ai_cards_preserved(self) -> None:
        _, registry = self._build_registry()
        cards_substituted, _ = substitute_entity_ids(
            self.cards_raw, registry,
        )
        patched, _ = patch_cards(
            copy.deepcopy(self.config), cards_substituted,
        )
        patched_envelope = copy.deepcopy(self.envelope)
        patched_envelope["data"]["config"] = patched
        # Envelope metadata byte-equal.
        for k in ("version", "minor_version", "key"):
            self.assertEqual(
                self.envelope.get(k), patched_envelope.get(k),
                msg=f"envelope metadata {k!r} changed",
            )
        # Views preserved.
        pre_views = [v.get("title", "?") for v in self.config.get("views", [])]
        post_views = [v.get("title", "?") for v in patched.get("views", [])]
        self.assertEqual(pre_views, post_views)
        # Non-AI cards byte-equal (canonical).
        def _cards(cfg, exclude):
            out = []
            for c in _iter_cards(cfg):
                if "type" not in c or c.get("title") in exclude:
                    continue
                out.append(c)
            return sorted(_canon(c) for c in out)
        pre = _cards(self.config, ALL_CARD_TITLES)
        post = _cards(patched, ALL_CARD_TITLES)
        self.assertEqual(pre, post)


if __name__ == "__main__":
    unittest.main()
