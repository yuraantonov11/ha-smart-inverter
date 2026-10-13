#!/usr/bin/env python3
"""Local dry-run for the AI-card patcher.

Reads the live lovelace.powmr_energy backup, simulates the
patcher's logic, and prints what would change. Does NOT touch
the live HA host.

Usage:
    python3 tests/dry_run_patcher.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yaml
from scripts.patch_lovelace_ai_cards import (
    CARD_LEARNING, CARD_RECOMMENDATION, CARD_SOC,
    ALL_CARD_TITLES, _iter_cards, patch_cards, _canonicalise,
)


def main() -> int:
    target = yaml.safe_load((REPO / "dashboard.yaml").read_text(encoding="utf-8"))
    backup = Path("/opt/data/powmr-ai-work/backups_ui_2026-10-13/lovelace.powmr_energy.pre")
    live = json.loads(backup.read_text(encoding="utf-8"))
    if "data" in live and "config" in live["data"]:
        live = live["data"]["config"]
    # The patcher reads from live and writes to live, replacing
    # only the cards whose title is in ALL_CARD_TITLES.
    config_snapshot_before = json.loads(json.dumps(live))
    config, stats = patch_cards(live, _target_cards_from(target))
    # Diff: which cards changed title-wise?
    print("=== DRY-RUN RESULT ===")
    print(f"matched (cards already present, content matched): {len(stats['matched'])}")
    for t in stats["matched"]:
        print(f"  - {t}")
    print(f"replaced (cards whose content diverged): {len(stats['replaced'])}")
    for t in stats["replaced"]:
        print(f"  - {t}")
    print(f"already up to date: {len(stats['already_up_to_date'])}")
    for t in stats["already_up_to_date"]:
        print(f"  - {t}")
    print(f"inserted (cards newly added): {len(stats['inserted'])}")
    for t in stats["inserted"]:
        print(f"  - {t}")
    # Other-cards invariant: cards whose title is NOT in ALL_CARD_TITLES
    # must be byte-equal in config and config_snapshot_before.
    # We must only compare real cards, not view titles or section
    # titles. _iter_cards yields any node with a 'title' field; a real
    # card has a 'type' field too.
    def _is_real_card(c):
        return "type" in c
    before_cards = [c for c in _iter_cards(config_snapshot_before) if _is_real_card(c)]
    after_cards = [c for c in _iter_cards(config) if _is_real_card(c)]
    # Group by title (skipping AI cards which are EXPECTED to change).
    before_by_title: dict[str, list] = {}
    for c in before_cards:
        if c.get("title") in ALL_CARD_TITLES:
            continue
        before_by_title.setdefault(c.get("title", "?"), []).append(_canonicalise(c))
    after_by_title: dict[str, list] = {}
    for c in after_cards:
        if c.get("title") in ALL_CARD_TITLES:
            continue
        after_by_title.setdefault(c.get("title", "?"), []).append(_canonicalise(c))
    unchanged = 0
    changed = 0
    for title, before_list in before_by_title.items():
        after_list = after_by_title.get(title, [])
        if sorted(before_list) == sorted(after_list):
            unchanged += len(before_list)
        else:
            changed += 1
            print(f"  CHANGED non-AI card: {title!r}")
    print()
    print(f"non-AI cards unchanged: {unchanged}")
    print(f"non-AI cards changed: {changed}")
    if changed != 0:
        print("FAIL: patcher would touch non-AI cards!")
        return 1
    # views invariant: the same set of view titles.
    before_views = [v.get("title", "?") for v in config_snapshot_before.get("views", [])]
    after_views = [v.get("title", "?") for v in config.get("views", [])]
    if before_views == after_views:
        print(f"views invariant: {before_views}")
    else:
        print(f"FAIL: views changed! before={before_views} after={after_views}")
        return 1
    # Dashboard URL path / metadata / key — unchanged.
    for key in ("key", "url_path", "title", "type", "show_in_sidebar"):
        if key in config_snapshot_before:
            if config.get(key) != config_snapshot_before.get(key):
                print(f"FAIL: dashboard metadata {key!r} changed")
                return 1
    print("dashboard metadata / URL / key / view set: unchanged")
    return 0


def _target_cards_from(target: dict) -> dict:
    """Extract the three target cards from the source-of-truth."""
    found: dict = {}
    for view in target.get("views", []):
        for s in view.get("sections", []):
            for c in s.get("cards", []):
                t = c.get("title", "")
                if t in ALL_CARD_TITLES:
                    # Deep-copy so the patcher can mutate freely.
                    import copy
                    found[t] = copy.deepcopy(c)
    missing = set(ALL_CARD_TITLES) - set(found)
    if missing:
        raise SystemExit(f"target dashboard.yaml is missing cards: {sorted(missing)}")
    return found


if __name__ == "__main__":
    raise SystemExit(main())
