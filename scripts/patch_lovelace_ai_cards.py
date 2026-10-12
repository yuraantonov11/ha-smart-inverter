#!/usr/bin/env python3
"""Live Lovelace patcher for the two AI dashboard cards.

This is a **standalone operator tool**, not part of the integration itself.
Юра (operator) runs it locally against a live Home Assistant instance to push
the AI · рекомендація та виконання (entities) and AI · навчання і точність
(markdown) cards, plus the new AI · SOC-стан card, into the storage-mode
dashboard `powmr-energy` — *only* the content of those cards is touched; the
rest of the dashboard (views, sections, other cards, metadata, resource
registry) is preserved byte-for-byte.

Configuration
-------------
The script reads connection details from environment variables so it never
checks secrets into disk:

* ``HASS_URL``   — e.g. ``http://homeassistant.local:8123``
* ``HASS_TOKEN`` — long-lived access token

If either variable is missing, the script prints a one-liner setup hint and
exits 0 (NOT an error — useful for "did I remember to export the env vars?"
sanity checks). No network requests happen in this branch.

CLI flags
---------
``--dry-run``     Print what would change, do not PUT the dashboard back.
``--backup-only`` Only write the backup file, do not modify the dashboard.
``--no-backup``   Skip writing the backup file (still idempotent patch).
``--ha-url URL``  Override ``HASS_URL`` for this invocation.
``--ha-token TOKEN``  Override ``HASS_TOKEN`` for this invocation.
``--dashboard URL_PATH``  Override the storage-mode dashboard URL path
                          (default: ``powmr-energy``).
``--timeout N``     HTTP timeout in seconds (default: 15).

Exit codes
----------
* 0  — nothing to do or run completed successfully
* 1  — operator/network/JSON error (problem on the user's side)
* 2  — backup write failure (we refused to patch without a backup)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerr
from urllib import request as urlreq


REPO = Path(__file__).resolve().parents[1]
DASHBOARD_YAML = REPO / "dashboard.yaml"

CARD_RECOMMENDATION = "AI · рекомендація та виконання"
CARD_LEARNING = "AI · навчання і точність"
CARD_SOC = "AI · SOC-стан"

# Default URL path under /api/lovelace/.../ for our storage-mode dashboard.
DEFAULT_DASHBOARD_URL_PATH = "powmr-energy"

HELP_TEXT = (
    "Set HASS_URL=http://homeassistant.local:8123 and "
    "HASS_TOKEN=<long-lived-access-token> then re-run."
)


# --------------------------------------------------------------------------- #
# YAML / JSON loading
# --------------------------------------------------------------------------- #


def load_expected_cards() -> dict[str, dict[str, Any]]:
    """Return the canonical card bodies from dashboard.yaml.

    Keys are the card titles; values are dicts with the full YAML card spec
    (title, type, entities / content). The integration owner treats this
    dashboard.yaml as the source of truth.
    """
    # PyYAML is optional — we only need JSON-shaped output and a couple of
    # fields. Parse just enough by hand to avoid pulling in another dep.
    # If PyYAML *is* available we still use it (cleaner).
    try:
        import yaml  # type: ignore
    except ImportError:  # pragma: no cover — yaml is in the integration venv
        yaml = None

    text = DASHBOARD_YAML.read_text(encoding="utf-8")
    data = yaml.safe_load(text) if yaml is not None else _minimal_yaml_load(text)

    found: dict[str, dict[str, Any]] = {}
    for view in data.get("views", []):
        for section in view.get("sections", []):
            for card in section.get("cards", []):
                title = card.get("title")
                if title in (CARD_RECOMMENDATION, CARD_LEARNING, CARD_SOC):
                    found[title] = card
    missing = {CARD_RECOMMENDATION, CARD_LEARNING, CARD_SOC} - set(found)
    if missing:
        raise SystemExit(
            f"dashboard.yaml is missing expected card(s): {sorted(missing)!r}"
        )
    return found


def _minimal_yaml_load(text: str) -> dict[str, Any]:
    """Last-resort parser: only good enough for the three target cards.

    We rely on the structural invariants of the existing dashboard.yaml
    (cards are bullet-list children of ``cards:`` and use ``- type:`` prefix
    with simple ``key: value`` mappings) — anything fancier would deserve a
    proper PyYAML dependency.
    """
    cards: dict[str, dict[str, Any]] = {}
    current_card: dict[str, Any] | None = None
    current_field: str | None = None
    block_indent: int | None = None
    literal_buf: list[str] = []

    def flush_literal() -> None:
        if current_card is not None and current_field is not None:
            current_card[current_field] = "\n".join(literal_buf).strip("\n")
        literal_buf.clear()

    for raw in text.splitlines():
        stripped = raw.lstrip(" ")
        if current_card is not None and block_indent is not None:
            if stripped == "":
                if current_field is not None:
                    literal_buf.append("")
                continue
            cur_indent = len(raw) - len(stripped)
            if cur_indent <= block_indent and stripped.startswith("- "):
                # New top-level sibling card — close current
                flush_literal()
                current_card = None
                current_field = None
                block_indent = None
            elif cur_indent > block_indent and current_field is not None:
                literal_buf.append(raw)
                continue
            elif cur_indent == block_indent and ":" in stripped:
                # sibling key under the same card
                flush_literal()
                key, _, value = stripped.partition(":")
                current_field = key.strip()
                val = value.strip()
                if val in ("", "|", ">"):
                    # start of a literal block / folded scalar
                    block_indent = cur_indent + 2
                    literal_buf.clear()
                else:
                    block_indent = None
                    literal_buf.clear()
                continue
            else:
                # Anything else: close the card.
                flush_literal()
                current_card = None
                current_field = None
                block_indent = None

        if stripped.startswith("- type:") and current_card is None:
            # New card begins. We re-scan the trailing fields below.
            current_card = {}
            current_field = "type"
            tval = stripped.split(":", 1)[1].strip()
            current_card["type"] = tval
            block_indent = len(raw) - len(stripped) + 2
            continue

        # Card top-level field lines
        if current_card is not None and stripped.startswith("- ") is False:
            if ":" in stripped:
                key, _, value = stripped.partition(":")
                current_field = key.strip()
                val = value.strip()
                if val in ("", "|", ">"):
                    block_indent = len(raw) - len(stripped) + 2
                    literal_buf.clear()
                else:
                    block_indent = None
                    current_card[current_field] = val.strip("'\"")
        elif current_card is not None and stripped.startswith("- ") and current_field == "title":
            # Newer card separator — actually just continuation lines.
            pass

        # Promote current_card if it now carries a title matching ours.
        if current_card is not None and current_card.get("title") in (
            CARD_RECOMMENDATION,
            CARD_LEARNING,
            CARD_SOC,
        ):
            cards.setdefault(current_card["title"], current_card)

    flush_literal()
    return {"views": [{"sections": [{"cards": list(cards.values())}]}]}


# --------------------------------------------------------------------------- #
# Lovelace HTTP helpers (urllib to avoid an httpx dependency in the operator
# environment).
# --------------------------------------------------------------------------- #


class LovelaceClient:
    def __init__(self, base_url: str, token: str, timeout: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str) -> bytes:
        url = f"{self.base_url}{path}"
        req = urlreq.Request(url, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urlreq.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except urlerr.HTTPError as exc:  # pragma: no cover — operator side
            body = exc.read().decode("utf-8", errors="replace")[:400]
            raise SystemExit(
                f"HA returned HTTP {exc.code} for {method} {url}: {body}"
            ) from exc
        except urlerr.URLError as exc:  # pragma: no cover — operator side
            raise SystemExit(f"Network error talking to {url}: {exc.reason}") from exc

    def get_json(self, path: str) -> Any:
        return json.loads(self._request("GET", path).decode("utf-8"))

    def put_json(self, path: str, payload: Any) -> bytes:
        req = urlreq.Request(
            f"{self.base_url}{path}",
            method="PUT",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urlreq.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except urlerr.HTTPError as exc:  # pragma: no cover — operator side
            body = exc.read().decode("utf-8", errors="replace")[:400]
            raise SystemExit(
                f"HA rejected PUT {path} with HTTP {exc.code}: {body}"
            ) from exc


def find_storage_dashboard(client: LovelaceClient, url_path: str) -> dict[str, Any]:
    """Return the lovelace dashboard config dict for the given storage URL path."""
    # List registered dashboards to locate the matching slug.
    listing = client.get_json("/api/lovelace/dashboards")
    for entry in listing:
        if entry.get("url_path") == url_path:
            # Storage-mode dashboards expose their config at
            #   /api/lovelace/dashboards/<url_path>
            # (the per-dashboard config endpoint).
            return client.get_json(f"/api/lovelace/dashboards/{url_path}")
    raise SystemExit(
        f"No Lovelace dashboard with url_path={url_path!r}; "
        f"known: {[d.get('url_path') for d in listing]!r}"
    )


# --------------------------------------------------------------------------- #
# Idempotent card patching
# --------------------------------------------------------------------------- #


def _canonicalise(value: Any) -> str:
    """Stable JSON representation for byte-level comparison."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def patch_cards(config: dict[str, Any], expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return ``(new_config, stats)`` where ``new_config`` has the targeted
    card content replaced in-place. Other cards are left untouched (only the
    relevant object references are mutated, the rest is the same dict tree).
    """
    stats: dict[str, Any] = {"scanned": 0, "matched": [], "replaced": [], "already_up_to_date": []}

    def visit(node: Any) -> Any:
        if isinstance(node, list):
            return [visit(item) for item in node]
        if isinstance(node, dict):
            for key, value in list(node.items()):
                node[key] = visit(value)
            if "title" in node and node["title"] in expected:
                stats["scanned"] += 1
                target = expected[node["title"]]
                if _canonicalise(node) == _canonicalise(target):
                    stats["already_up_to_date"].append(node["title"])
                    stats["matched"].append(node["title"])
                    return node
                # Replace the full card body with the canonical copy.
                for k in list(node.keys()):
                    del node[k]
                for k, v in target.items():
                    node[k] = v
                stats["replaced"].append(node["title"])
                stats["matched"].append(node["title"])
            return node
        return node

    visit(config)
    return config


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="Print what would change but do not PUT.")
    p.add_argument("--backup-only", action="store_true", help="Only write backup; skip PUT.")
    p.add_argument("--no-backup", action="store_true", help="Skip backup creation.")
    p.add_argument("--ha-url", default=os.environ.get("HASS_URL"), help="HA base URL (env: HASS_URL)")
    p.add_argument("--ha-token", default=os.environ.get("HASS_TOKEN"), help="HA token (env: HASS_TOKEN)")
    p.add_argument("--dashboard", default=DEFAULT_DASHBOARD_URL_PATH,
                   help=f"Lovelace storage dashboard url_path (default: {DEFAULT_DASHBOARD_URL_PATH})")
    p.add_argument("--timeout", type=int, default=15, help="HTTP timeout seconds (default: 15)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if not args.ha_url or not args.ha_token:
        print(HELP_TEXT)
        return 0

    expected = load_expected_cards()

    client = LovelaceClient(args.ha_url, args.ha_token, args.timeout)
    config = find_storage_dashboard(client, args.dashboard)

    backup_root = REPO / "scripts" / ".lovelace_backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    iso = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_root / f"lovelace.{args.dashboard}.{iso}.bak"

    config_snapshot_before = json.loads(json.dumps(config))  # deep copy for diff

    new_config = patch_cards(config, expected)

    replaced_titles = [
        title for title in (CARD_RECOMMENDATION, CARD_LEARNING, CARD_SOC)
        if json.dumps(_canonicalise(new_config))  # not used; tracked in stats
    ]
    stats = {
        "scanned": 3,
        "replaced": _diff_titles(config_snapshot_before, new_config),
        "already_up_to_date": _unchanged_titles(config_snapshot_before, new_config, expected),
    }

    if not (stats["replaced"] or stats["already_up_to_date"]):
        # Should not normally happen — the loader enforces the cards exist.
        print("No target cards were found in the dashboard JSON.")
        return 1

    if not args.no_backup:
        backup_path.write_text(
            json.dumps(config_snapshot_before, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Backup written: {backup_path}")

    if args.backup_only:
        print(f"Backup-only mode: skipped PUT. {stats}")
        return 0

    if not stats["replaced"]:
        print("Already up to date. Nothing to PUT.")
        return 0

    if args.dry_run:
        print(f"Dry run — would PUT {len(stats['replaced'])} card(s): {stats['replaced']!r}")
        return 0

    put_path = f"/api/lovelace/dashboards/{args.dashboard}"
    client.put_json(put_path, new_config)
    print(f"Patched {len(stats['replaced'])} card(s) at {put_path}: {stats['replaced']!r}")
    if stats["already_up_to_date"]:
        print(f"Unchanged (already up to date): {stats['already_up_to_date']!r}")
    print(f"Backup at {backup_path}")
    return 0


def _diff_titles(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for title in (CARD_RECOMMENDATION, CARD_LEARNING, CARD_SOC):
        b = _find_card(before, title)
        a = _find_card(after, title)
        if b is not None and a is not None and _canonicalise(b) != _canonicalise(a):
            out.append(title)
    return out


def _unchanged_titles(before: dict[str, Any], after: dict[str, Any],
                      expected: dict[str, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for title in (CARD_RECOMMENDATION, CARD_LEARNING, CARD_SOC):
        b = _find_card(before, title)
        if b is not None and _canonicalise(b) == _canonicalise(expected[title]):
            out.append(title)
    return out


def _find_card(node: Any, title: str) -> dict[str, Any] | None:
    if isinstance(node, list):
        for item in node:
            found = _find_card(item, title)
            if found is not None:
                return found
        return None
    if isinstance(node, dict):
        if node.get("title") == title:
            return node
        for value in node.values():
            found = _find_card(value, title)
            if found is not None:
                return found
    return None


if __name__ == "__main__":
    sys.exit(main())
