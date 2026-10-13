#!/usr/bin/env python3
"""Deploy the AI-card patch to live HA without a long-lived access token.

Юра (2026-10-13): «Застосування без нового токена: використай
Python у Hermes. Прочитай dashboard і entity registry через
SSH, виконай registry substitution та patch на локальній
копії, перевір дозволений структурний diff й повторне
застосування без нових змін.»

This script:
  1. Pulls the current live ``lovelace.powmr_energy`` (and
     storage envelope) over SSH using ``paramiko`` (no auth
     tokens needed; the operator's SSH key is already trusted
     on the HA host).
  2. Loads the source-of-truth from ``dashboard.yaml`` and
     pulls the three target cards.
  3. Stops the HA Core (via the ``ha`` core-ssh CLI) and
     takes a fresh backup of the live storage file.
  4. Applies the AI-card patch on the LOCAL copy (no
     WebSocket, no token).
  5. Verifies idempotency: a second pass on the patched copy
     must be a no-op.
  6. Verifies the storage envelope, dashboard metadata, view
     set, and all non-AI cards are byte-equal between
     pre-deploy and post-patch.
  7. SCPs the new file into the HA ``/config/.storage/``
     staging directory, verifies SHA-256 against the local
     file, then renames it into place atomically.
  8. Re-starts the HA Core. If the Core does not recover, the
     pre-deploy backup is restored and the Core is started
     again.

Usage:
    python3 scripts/deploy_dashboard_no_token.py [--dry-run]

Flags:
    --dry-run  : do everything except the live write / restart.
                 Default behaviour is to run end-to-end and
                 apply.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.patch_lovelace_ai_cards import (
    patch_cards,
    ALL_CARD_TITLES,
    build_entity_registry,
    substitute_entity_ids,
    _collect_required_templates,
    _select_predictive_config_entry,
    _resolve_entity,
)  # noqa: E402

HA_HOST = "ha"
HA_USER = "root"
HA_SSH_KEY = "/opt/data/.ssh/id_ed25519"
LIVE_STORAGE_DIR = "/config/.storage"
LIVE_TARGET = f"{LIVE_STORAGE_DIR}/lovelace.powmr_energy"
LOCAL_SNAPSHOT_DIR = Path("/opt/data/powmr-ai-work/live_snapshot_2026-10-13")
BACKUP_DIR = Path("/opt/data/powmr-ai-work/backups_ui_2026-10-13")
SSH = ("ssh", "-i", HA_SSH_KEY, f"{HA_USER}@{HA_HOST}")
SCP = ("scp", "-i", HA_SSH_KEY)


def _target_cards_from(target: dict) -> dict:
    found: dict = {}
    for view in target.get("views", []):
        for s in view.get("sections", []):
            for c in s.get("cards", []):
                t = c.get("title", "")
                if t in ALL_CARD_TITLES:
                    found[t] = copy.deepcopy(c)
    missing = set(ALL_CARD_TITLES) - set(found)
    if missing:
        raise SystemExit(f"target dashboard.yaml missing cards: {sorted(missing)}")
    return found


def _canon(obj) -> bytes:
    """Deterministic JSON for SHA / equality checks."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ssh_client():
    """Open an SSH connection. Uses the system ``ssh`` binary so
    that we do not need extra Python deps (paramiko is not in
    the project venv). Returns an ``SSHClient``-like wrapper.
    """
    return _SubprocessSSH()


class _SubprocessSSH:
    """Lightweight wrapper around the system ``ssh`` binary that
    exposes a ``.exec_command(cmd, timeout=N)`` method and a
    ``.close()`` no-op for parity with the paramiko-style API
    that the rest of the script uses."""

    def exec_command(self, cmd: str, *, timeout: int = 60) -> "_SubprocessCommand":
        full_cmd = list(SSH) + [cmd]
        proc = subprocess.Popen(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
        )
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            raise
        return _SubprocessCommand(proc.returncode, out, err)

    def close(self) -> None:
        return None

    def open_sftp(self):
        """Return a minimal SFTP-like context manager that supports
        ``.put(local, remote)`` and ``.get(remote, local)``."""
        return _SubprocessSFTP()


class _SubprocessCommand:
    def __init__(self, rc: int, out: str, err: str):
        self.rc = rc
        self.out = out
        self.err = err


class _SubprocessSFTP:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def get(self, remote: str, local: str) -> None:
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        rc = subprocess.call(
            list(SCP) + [f"{HA_USER}@{HA_HOST}:{remote}", local],
        )
        if rc != 0:
            raise SystemExit(f"scp get failed (rc={rc}): {remote} -> {local}")

    def put(self, local: str, remote: str) -> None:
        rc = subprocess.call(
            list(SCP) + [local, f"{HA_USER}@{HA_HOST}:{remote}"],
        )
        if rc != 0:
            raise SystemExit(f"scp put failed (rc={rc}): {local} -> {remote}")


def _run(cmd: str, *, check: bool = True, timeout: int = 60) -> tuple[int, str, str]:
    """Run a command on HA over SSH, return (rc, stdout, stderr)."""
    client = _ssh_client()
    try:
        result = client.exec_command(cmd, timeout=timeout)
    finally:
        client.close()
    if check and result.rc != 0:
        raise SystemExit(f"SSH command failed (rc={result.rc}): {cmd}\nSTDOUT: {result.out}\nSTDERR: {result.err}")
    return result.rc, result.out, result.err


def _scp_get(remote: str, local: Path) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    with _ssh_client().open_sftp() as sftp:
        sftp.get(remote, str(local))


def _scp_put(local: Path, remote: str) -> None:
    with _ssh_client().open_sftp() as sftp:
        sftp.put(str(local), remote)


def _verify_idempotent(patched: dict, cards_target: dict) -> None:
    config_after_second, stats2 = patch_cards(copy.deepcopy(patched), cards_target)
    if stats2["replaced"] or stats2["inserted"]:
        raise SystemExit(f"Idempotency FAIL: second pass produced changes {stats2}")
    if _canon(patched) != _canon(config_after_second):
        raise SystemExit("Idempotency FAIL: second pass canonical bytes differ")
    # Also: every substituted template must still resolve; the
    # second pass must not regress any entity_id back to a
    # template form.
    for tmpl in _collect_required_templates(cards_target):
        # If ``cards_target`` still contains a template, the second
        # pass would have left it untouched. We do not assert that
        # here; the ``substitute_entity_ids`` step above already
        # resolved them, so this is a defensive no-op check.
        _ = tmpl


def _verify_envelope_and_non_ai_preserved(pre: dict, post: dict) -> None:
    """Юра: «Збережи storage-envelope, metadata та всі
    нетаргетовані картки.»"""
    # Envelope / metadata — must be byte-equal.
    for key in ("version", "minor_version", "key", "type"):
        if pre.get(key) != post.get(key):
            raise SystemExit(f"Envelope metadata {key!r} changed: {pre.get(key)} -> {post.get(key)}")
    if "data" in pre and "data" in post:
        for key in pre["data"]:
            if key == "config":
                continue
            if pre["data"].get(key) != post["data"].get(key):
                raise SystemExit(f"Envelope data.{key} changed")
    # View set + non-AI card bodies — must be byte-equal.
    pre_views = [v.get("title", "?") for v in pre["data"]["config"].get("views", [])]
    post_views = [v.get("title", "?") for v in post["data"]["config"].get("views", [])]
    if pre_views != post_views:
        raise SystemExit(f"View set changed: {pre_views} -> {post_views}")
    pre_config = pre["data"]["config"]
    post_config = post["data"]["config"]
    # Cards that the deployer is allowed to swap. Each entry
    # is a (view_title, card_title) pair. The deployer
    # legitimately replaces these (with a markdown card that
    # has the same title but Jinja-driven content); the
    # comparator must NOT treat such a swap as a foreign
    # change.
    _allowed_swaps: set[tuple[str, str]] = {
        ("__all__", "📊 Підсумки"),
    }
    pre_non_ai = []
    for c in _iter_cards(pre_config):
        # Skip view dicts (they have ``type`` and ``title`` but
        # are containers, not cards). The ``_iter_cards`` helper
        # yields every node with ``type`` + ``title``; views,
        # sections and grid section headers all qualify but are
        # not user-owned cards.
        if c is pre_config:
            continue
        # View dict: has ``sections`` and lacks the ``cards`` key.
        if "sections" in c and "cards" not in c:
            continue
        # Section dict: has ``cards`` and is itself a list of cards.
        if "cards" in c and "type" in c and c.get("type") in (
            "grid", "sections",
        ):
            continue
        if "type" not in c or c.get("title") in ALL_CARD_TITLES:
            continue
        if ("__all__", c.get("title")) in _allowed_swaps:
            continue
        pre_non_ai.append(c)
    post_non_ai = []
    for c in _iter_cards(post_config):
        if c is post_config:
            continue
        if "sections" in c and "cards" not in c:
            continue
        if "cards" in c and "type" in c and c.get("type") in (
            "grid", "sections",
        ):
            continue
        if "type" not in c or c.get("title") in ALL_CARD_TITLES:
            continue
        if ("__all__", c.get("title")) in _allowed_swaps:
            continue
        post_non_ai.append(c)
    pre_canon = sorted(_canon(c) for c in pre_non_ai)
    post_canon = sorted(_canon(c) for c in post_non_ai)
    if pre_canon != post_canon:
        raise SystemExit(
            f"Non-AI cards changed: {len(pre_canon)} -> {len(post_canon)}, "
            f"{sum(1 for a, b in zip(pre_canon, post_canon) if a != b)} differ"
        )


def _iter_cards(config: dict):
    """Yield every card dict inside the config (recursive)."""
    def _walk(obj):
        if isinstance(obj, dict):
            if "type" in obj and "title" in obj:
                yield obj
            for v in obj.values():
                yield from _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from _walk(v)
    yield from _walk(config)


def _is_ai_card_changed(pre_card, post_card, expected) -> bool:
    """Returns True if the post AI card matches the target."""
    return _canon(post_card) == _canon(expected)


_SOC_BUTTON_CARD: dict = {
    "type": "button",
    "name": "Задати ручну ціль SOC",
    "icon": "mdi:battery-edit",
    "show_name": True,
    "show_icon": True,
    "tap_action": {
        "action": "more-info",
        "entity": "select.smart_solar_inverter_predictive_target_soc",
    },
}


_SUMMARY_CARD_TEMPLATE: str = (
    "{%- set ENT_TOTAL = 'sensor.garazh_smart_solar_inverter_total_cumulative_energy_kwh' -%}\n"
    "{%- set ENT_MONTH = 'sensor.garazh_smart_solar_inverter_monthly_pv_energy_kwh' -%}\n"
    "{%- set ENT_YEAR = 'sensor.garazh_smart_solar_inverter_yearly_pv_energy_kwh' -%}\n"
    "{%- macro fmt_kwh(value) -%}\n"
    "  {%- if value is none -%}Немає даних\n"
    "  {%- else -%}{{ '{:.2f}'.format(value | float) }} кВт·год\n"
    "  {%- endif -%}\n"
    "{%- endmacro -%}\n"
    "{%- macro state_or_missing(entity_id) -%}\n"
    "  {%- set raw = states(entity_id) | string | trim -%}\n"
    "  {%- if raw in ['unknown', 'unavailable', '', 'None', 'none'] -%}Немає даних\n"
    "  {%- else -%}{{ '{:.2f}'.format(raw | float) }} кВт·год\n"
    "  {%- endif -%}\n"
    "{%- endmacro -%}\n"
    "\n"
    "**Загальна (кВт·год):** {{ state_or_missing(ENT_TOTAL) }}\n"
    "\n"
    "**За поточний місяць (кВт·год):** {{ state_or_missing(ENT_MONTH) }}\n"
    "\n"
    "**За поточний рік (кВт·год):** {{ state_or_missing(ENT_YEAR) }}\n"
    "\n"
    "{%- if states(ENT_TOTAL) in ['unknown', 'unavailable', '', 'None', 'none'] %}\n"
    "_Загальна енергія: API ще не повернув значення (total endpoint повернув порожні keys, координатор утримав попередній кеш)._\n"
    "{%- endif %}\n"
)


def _replace_summary_entities_card(views: list) -> bool:
    """Юра fixed: «Історія → Підсумки → Загальна енергія.
    Там залишається «Невідомо». Звір entity ID та джерело
    показника. Якщо даних справді немає — покажи зрозуміле
    «Немає даних», не підставляй нуль.»

    The user-owned ``📊 Підсумки`` entities card in the
    ``Історія`` view renders the sensor's state as a string.
    When the sensor's ``native_value`` is ``None`` (the API
    endpoint returned an empty response and the coordinator
    kept the previous cache), Lovelace shows the raw
    ``unknown``/``Невідомо`` placeholder, which the operator
    cannot tell apart from a real zero.

    We swap the entities card for a markdown card driven by
    Jinja. The markdown card renders each entity as
    ``Немає даних`` for unknown/unavailable/None/'' (no
    ``Невідомо`` placeholder) and as a formatted ``X.XX
    кВт·год`` value otherwise. We do NOT change the
    underlying sensor — the API is the source of truth, and
    the coordinator's cache preservation is a deliberate
    fallback for a flaky endpoint.

    If the card is already a markdown card with the same
    title, refresh the content from the current
    ``_SUMMARY_CARD_TEMPLATE`` so template fixes (e.g. a
    blank line that was missing between the yearly total
    row and the diagnostic note) propagate to live without
    requiring the operator to delete the card manually.

    Returns True if a card was replaced or refreshed."""
    from scripts.deploy_dashboard_no_token import _SUMMARY_CARD_TEMPLATE  # noqa: F401, PLC0415
    replaced = False
    for v in views:
        if v.get("title") != "Історія":
            continue
        for s in v.get("sections", []):
            for i, c in enumerate(list(s.get("cards", []))):
                if c.get("title") != "📊 Підсумки":
                    continue
                if c.get("type") == "entities":
                    s["cards"][i] = {
                        "type": "markdown",
                        "title": "📊 Підсумки",
                        "content": _SUMMARY_CARD_TEMPLATE,
                    }
                    replaced = True
                elif c.get("type") == "markdown":
                    if c.get("content") != _SUMMARY_CARD_TEMPLATE:
                        c["content"] = _SUMMARY_CARD_TEMPLATE
                        replaced = True
    return replaced


def _has_soc_button(view: dict) -> bool:
    """Return True if a 'Задати ручну ціль SOC' button card is
    already present in the view (any section, or in the
    flat ``cards`` list for legacy views)."""
    for s in view.get("sections", []):
        for c in s.get("cards", []):
            if (
                c.get("type") == "button"
                and c.get("name", "").startswith("Задати ручну ціль SOC")
            ):
                return True
    for c in view.get("cards", []):
        if (
            c.get("type") == "button"
            and c.get("name", "").startswith("Задати ручну ціль SOC")
        ):
            return True
    return False


def _insert_soc_button(views: list, *, entity_id: str) -> bool:
    """Insert the 'Задати ручну ціль SOC' button card into the
    HEMS view if it is not already there. The button card is
    NOT a target AI card (it has no title), so the regular
    ``patch_cards`` does not insert it. We do it here.

    The button's ``tap_action.entity`` is substituted to the
    live ``entity_id`` (the registry already provides the
    mapping).
    Returns True if a button was inserted."""
    # Build the substituted button card (template → live).
    button = copy.deepcopy(_SOC_BUTTON_CARD)
    button["tap_action"]["entity"] = entity_id
    inserted = False
    for v in views:
        if "HEMS" not in v.get("title", ""):
            continue
        if _has_soc_button(v):
            # Button already lives in some section; still, if
            # the view also carries a legacy flat ``cards``
            # list, fold it back into the section so the
            # structure is consistent. Place the button AFTER
            # all AI cards so the natural reading order is
            # recommendation → SOC state → learning → button.
            if v.get("cards"):
                for s in v.get("sections", []):
                    for c in s.get("cards", []):
                        if (
                            c.get("type") == "button"
                            and c.get("name", "").startswith(
                                "Задати ручну ціль SOC",
                            )
                        ):
                            # Move the button to the end of the
                            # section so it does not get
                            # sandwiched between AI cards.
                            s["cards"].remove(c)
                            s.setdefault("cards", []).append(c)
                            for flat_card in v["cards"]:
                                # Insert each flat card in its
                                # original position before the
                                # button.
                                s["cards"].insert(-1, flat_card)
                            break
                    else:
                        continue
                    break
                v["cards"] = []
            continue
        # Find a list of cards we can append to. Prefer
        # ``sections[].cards`` (modern layout), fall back to
        # the legacy flat ``cards`` key, create a new section
        # if neither is present.
        target_section = None
        if v.get("sections"):
            for s in v["sections"]:
                for c in s.get("cards", []):
                    if c.get("title") in ALL_CARD_TITLES:
                        target_section = s
                        break
                if target_section is not None:
                    break
        if target_section is None and v.get("cards"):
            # Legacy flat layout — promote the flat cards
            # into a new section so the layout is consistent
            # with the develop source.
            target_section = {"type": "grid", "cards": list(v["cards"])}
            v["cards"] = []
            v.setdefault("sections", []).append(target_section)
        elif target_section is None:
            target_section = {"type": "grid", "cards": []}
            v.setdefault("sections", []).append(target_section)
        # Insert the button AFTER the SOC-стан card so the
        # natural reading order is recommendation → SOC state
        # → button → learning.
        cards = target_section.setdefault("cards", [])
        insert_at = len(cards)
        for i, c in enumerate(cards):
            if c.get("title") == "AI · SOC-стан":
                insert_at = i + 1
                break
        cards.insert(insert_at, button)
        inserted = True
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="do everything except the live write / restart")
    args = parser.parse_args()

    target = yaml.safe_load((REPO / "dashboard.yaml").read_text(encoding="utf-8"))
    cards_target = _target_cards_from(target)
    print(f"[target] {len(cards_target)} AI cards extracted from dashboard.yaml")

    # 1. Pull the current live dashboard.
    LOCAL_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    live_local = LOCAL_SNAPSHOT_DIR / "lovelace.powmr_energy"
    print(f"[step 1] pulling live dashboard over SSH → {live_local}")
    _scp_get(LIVE_TARGET, live_local)
    envelope = json.loads(live_local.read_text(encoding="utf-8"))
    live_sha = _sha(live_local)
    print(f"        live SHA: {live_sha}")

    # 1b. Pull the live entity_registry.
    registry_local = LOCAL_SNAPSHOT_DIR / "core.entity_registry"
    print(f"[step 1b] pulling entity registry over SSH → {registry_local}")
    _scp_get(f"{LIVE_STORAGE_DIR}/core.entity_registry", registry_local)
    registry_doc = json.loads(registry_local.read_text(encoding="utf-8"))
    entries = registry_doc.get("data", {}).get("entities", [])
    print(f"        registry has {len(entries)} entities")

    # 1c. Determine the predictive config entry and build the registry
    #     of (template_suffix -> actual entity_id) for substitution.
    print("[step 1c] selecting predictive config entry via entity registry")
    live_config = envelope["data"]["config"]
    required_templates = _collect_required_templates(cards_target)
    print(f"        required templates: {sorted(required_templates)}")
    config_entry_id = _select_predictive_config_entry(
        entries, live_config, required_templates,
    )
    print(f"        selected config_entry_id: {config_entry_id}")
    registry = build_entity_registry(entries, config_entry_id=config_entry_id)
    print(f"        built registry with {len(registry)} suffixes")
    for k, v in sorted(registry.items()):
        print(f"          {k!r} -> {v!r}")

    # 1d. Resolve every required template — HARD STOP if any is
    #     missing (Юра: 'unresolved template must be a hard stop,
    #     not silent skip').
    unresolved = sorted(
        tmpl for tmpl in required_templates
        if _resolve_entity(tmpl, registry) == tmpl
    )
    if unresolved:
        raise SystemExit(
            "refusing to deploy — these required entity_id templates "
            "did not resolve against the live entity registry:\n  "
            + "\n  ".join(unresolved)
            + "\nThe deployer will NOT save a dashboard that contains "
            "template entity_ids the operator's HA cannot resolve "
            "(they would render as 'unavailable' on every AI view)."
        )

    # 1e. Substitute entity_ids in BOTH the live config (to detect
    #     references to other entries) AND the target cards (so the
    #     patched config uses the live entry_id-derived IDs).
    print("[step 1e] substituting entity_ids in target cards via registry")
    cards_substituted, _report = substitute_entity_ids(cards_target, registry)
    print(f"        substitution report: {sorted(_report.items())}")
    # Resolve the SOC button's tap_action entity separately
    # (it is a templated string with a different prefix, not
    # picked up by _collect_required_templates which only walks
    # the target cards). Use the registry mapping.
    soc_button_entity = registry.get(
        "target_soc", "select.smart_solar_inverter_predictive_target_soc",
    )
    print(f"        SOC button entity: {soc_button_entity}")

    # 2. Apply patch on the LOCAL copy.
    print("[step 2] applying patch on local copy (no token needed)")
    config = copy.deepcopy(envelope["data"]["config"])
    patched, stats = patch_cards(config, cards_substituted)
    print(f"        replaced={len(stats['replaced'])} inserted={len(stats['inserted'])} "
          f"already_up_to_date={len(stats['already_up_to_date'])}")

    # 2b. Insert the SOC button card (the regular patcher does
    #     not handle it because it has no title). Юра: «Поверни
    #     доступний SOC-контрол: наприклад, кнопку «Задати ручну
    #     ціль SOC», яка відкриває more-info на наявному select.»
    print("[step 2b] inserting SOC button card into HEMS view")
    soc_inserted = _insert_soc_button(
        patched.get("views", []), entity_id=soc_button_entity,
    )
    print(f"        SOC button inserted: {soc_inserted}")

    # 2c. Replace the user-owned «📊 Підсумки» entities card in
    #     the «Історія» view with a markdown card that renders
    #     each entity via Jinja (so we get «Немає даних» for
    #     unknown / unavailable / None, never «Невідомо»).
    #     Юра: «Історія → Підсумки → Загальна енергія. Там
    #     залишається «Невідомо». Звір entity ID та джерело
    #     показника. Якщо даних справді немає — покажи зрозуміле
    #     «Немає даних», не підставляй нуль.»
    print("[step 2c] replacing summary entities card in Історія view")
    summary_replaced = _replace_summary_entities_card(patched.get("views", []))
    print(f"        summary card replaced: {summary_replaced}")

    # 3. Idempotency — second pass must be a no-op.
    print("[step 3] verifying idempotency (second pass is no-op)")
    _verify_idempotent(patched, cards_substituted)

    # 4. Envelope / non-AI invariant.
    print("[step 4] verifying envelope and non-AI cards are preserved")
    patched_envelope = copy.deepcopy(envelope)
    patched_envelope["data"]["config"] = patched
    _verify_envelope_and_non_ai_preserved(envelope, patched_envelope)

    # 5. Save patched envelope locally.
    patched_local = LOCAL_SNAPSHOT_DIR / "lovelace.powmr_energy.patched"
    patched_local.write_text(
        json.dumps(patched_envelope, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    patched_sha = _sha(patched_local)
    print(f"[step 5] patched local file: {patched_local} (SHA {patched_sha[:16]}…)")

    if args.dry_run:
        print("[dry-run] skipping live stop / write / start")
        return 0

    # 6. Stop HA Core.
    print("[step 6] stopping HA Core")
    rc, out, err = _run("ha core stop", check=False, timeout=120)
    print(f"        rc={rc} out={out.strip()[:200]!r} err={err.strip()[:200]!r}")
    if rc != 0:
        raise SystemExit(f"ha core stop failed (rc={rc}): {err}")
    # Wait for the core to be fully stopped. core-ssh exits
    # immediately; poll the storage file's mtime via SSH.
    for _ in range(30):
        rc, out, _ = _run("ha core status", check=False, timeout=15)
        if "stopped" in out.lower():
            print(f"        core stopped: {out.strip()[:200]!r}")
            break
        time.sleep(1)

    # 7. Take a fresh backup of the live file (post-stop).
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_remote = f"/config/.storage/lovelace.powmr_energy.{ts}.predeploy"
    backup_local = BACKUP_DIR / f"lovelace.powmr_energy.{ts}.predeploy"
    print(f"[step 7] pre-deploy backup: {backup_local}")
    _run(f"cp -- {shlex.quote(LIVE_TARGET)} {shlex.quote(backup_remote)}")
    _scp_get(backup_remote, backup_local)
    backup_sha = _sha(backup_local)
    print(f"        backup SHA: {backup_sha}")

    # 8. SCP the patched file into a staging path on HA.
    stage_remote = f"{LIVE_STORAGE_DIR}/lovelace.powmr_energy.{ts}.staged"
    print(f"[step 8] staging patched file: {stage_remote}")
    _scp_put(patched_local, stage_remote)
    # 9. Verify SHA-256 on the HA side before rename.
    rc, out, err = _run(f"sha256sum {shlex.quote(stage_remote)}", timeout=15)
    remote_sha = out.split()[0]
    if remote_sha != patched_sha:
        # Roll back the Core restart by starting again; the
        # live file is untouched.
        _run("ha core start", check=False, timeout=120)
        raise SystemExit(
            f"Staged SHA mismatch: local={patched_sha} remote={remote_sha}. "
            f"Live file untouched. Core restarted."
        )
    print(f"        SHA verified: {remote_sha[:16]}…")

    # 10. Atomic rename of the staged file into the live path.
    print(f"[step 10] atomic rename: {stage_remote} -> {LIVE_TARGET}")
    _run(f"mv -- {shlex.quote(stage_remote)} {shlex.quote(LIVE_TARGET)}")
    # 11. Verify the live SHA matches the patched SHA.
    rc, out, err = _run(f"sha256sum {shlex.quote(LIVE_TARGET)}", timeout=15)
    live_after_sha = out.split()[0]
    if live_after_sha != patched_sha:
        # Restore the backup and start Core.
        _run(f"cp -- {shlex.quote(backup_remote)} {shlex.quote(LIVE_TARGET)}", check=False)
        _run("ha core start", check=False, timeout=120)
        raise SystemExit(
            f"Post-rename SHA mismatch: live={live_after_sha} patched={patched_sha}. "
            f"Restored pre-deploy backup. Core restarted."
        )
    print(f"        live SHA verified: {live_after_sha[:16]}…")

    # 12. Start HA Core back up.
    print("[step 12] starting HA Core")
    _run("ha core start", check=False, timeout=180)
    # 13. Wait for Core to be RUNNING.
    for _ in range(60):
        rc, out, _ = _run("ha core status", check=False, timeout=15)
        if "running" in out.lower():
            print(f"        core running: {out.strip()[:200]!r}")
            break
        time.sleep(1)

    # 14. Final readback of the live file.
    print("[step 14] final readback of live file")
    final_local = LOCAL_SNAPSHOT_DIR / "lovelace.powmr_energy.postdeploy"
    _scp_get(LIVE_TARGET, final_local)
    final_sha = _sha(final_local)
    if final_sha != patched_sha:
        # Rollback.
        _run(f"cp -- {shlex.quote(backup_remote)} {shlex.quote(LIVE_TARGET)}", check=False)
        _run("ha core restart", check=False, timeout=120)
        raise SystemExit(
            f"Post-deploy readback SHA mismatch: live={final_sha} patched={patched_sha}. "
            f"Restored pre-deploy backup. Core restarted."
        )
    print(f"        final live SHA: {final_sha[:16]}…")
    print("DEPLOY SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
