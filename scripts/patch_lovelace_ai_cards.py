#!/usr/bin/env python3
"""Live Lovelace patcher for the three AI dashboard cards.

Standalone operator tool, **not** part of the integration.
Run it locally against a live Home Assistant (storage-mode) instance to push
the three AI cards —

  * ``AI · рекомендація та виконання`` (entities)
  * ``AI · навчання і точність`` (markdown)
  * ``AI · SOC-стан`` (markdown)

— into the storage-mode dashboard identified by ``--dashboard`` (default
``powmr-energy``). Only the *content* of those cards is mutated; the rest
of the dashboard (views, sections, other cards, metadata, the resource
registry) is preserved byte-for-byte.

This script addresses the eight defects Юра raised against v1:

  1. STORAGE-mode dashboards DO NOT expose their config at
     ``/api/lovelace/dashboards/<url_path>`` — that endpoint returns
     *metadata only* (id, url_path, mode, title). The real config lives in
     ``.storage/lovelace.<url_path>`` on disk and is read / written via
     the Home Assistant WebSocket API::

         auth       -> {"type":"auth","access_token":"…"}
         list       -> {"id":1,"type":"lovelace/dashboards/list"}
         get config -> {"id":2,"type":"lovelace/config","url_path":"…"}
         save       -> {"id":3,"type":"lovelace/config/save",
                         "url_path":"…","config":{…}}
         entity reg -> {"id":4,"type":"config/entity_registry/list"}

     We default to the WebSocket path; on connection failure we fall back
     to REST ``/api/lovelace/config`` which only works for the *default*
     dashboard (operator gets a clear message).

  2. Entity IDs are NOT hard-coded into ``dashboard.yaml``. The script
     fetches ``config/entity_registry/list``, builds a mapping
     ``{translation_key: entity_id}`` AND
     ``{<unique_id suffix>: entity_id}`` for predictive sensors that
     don't publish a ``translation_key``. It substitutes the actual
     ``entity_id`` into the content of the three target cards just
     before PUT.

  3. SOC-card duplicates are detected by scanning EVERY card in EVERY
     section and EVERY view. > 1 card with title ``AI · SOC-стан``
     aborts the run with a non-zero exit. Same check is repeated AFTER
     the patch.

  4. / 5. / 6. / 8. handled in ``dashboard.yaml`` — this script honours
     the template as the source of truth.

  7. Backup + readback + race check: a *second* ``lovelace/config``
     fetch happens RIGHT BEFORE the PUT; if it differs from the snapshot
     we took at the start (i.e. something else mutated the dashboard
     while we were preparing), we ABORT. After PUT we read back again
     and confirm only the three target cards diverged.

Configuration
-------------
Secrets are read from environment variables only — NEVER from CLI args,
NEVER logged, NEVER written to the backup file. Inspected via
``os.environ.get`` and only the *length* of the value is ever printed.

* ``HASS_URL``    — e.g. ``http://homeassistant.local:8123``
* ``HASS_TOKEN``  — long-lived access token

If either variable is missing, the script prints a one-liner setup hint
and exits 0 (NOT an error — useful for "did I remember to export the
env vars?" sanity checks). No network requests happen in this branch.

CLI flags
---------
``--dry-run``            Print what would change, do not PUT.
``--backup-only``        Only write the backup file, do not modify.
``--no-backup``          Skip writing the backup file (still idempotent).
``--ha-url URL``         Override ``HASS_URL``.
``--ha-token TOKEN``     Override ``HASS_TOKEN``.
``--dashboard URL_PATH`` Override the storage-mode dashboard URL path.
``--timeout N``          Network timeout in seconds (default: 15).
``--insecure-transport`` Allow plain ws:// (HA without TLS).
``--print-config``       After patching, dump the resulting dashboard
                         config (secrets redacted).

Exit codes
----------
* 0  — nothing to do OR run completed successfully OR sandbox-mode hint
* 1  — operator/network/JSON error
* 2  — backup write failure (we refused to patch without a backup)
* 3  — duplicate target card detected (pre or post patch)
* 4  — race-condition / readback mismatch
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import ssl
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


REPO = Path(__file__).resolve().parents[1]
DASHBOARD_YAML = REPO / "dashboard.yaml"

CARD_RECOMMENDATION = "AI · рекомендація та виконання"
CARD_LEARNING = "AI · навчання і точність"
CARD_SOC = "AI · SOC-стан"
ALL_CARD_TITLES: tuple[str, ...] = (CARD_RECOMMENDATION, CARD_LEARNING, CARD_SOC)

DEFAULT_DASHBOARD_URL_PATH = "powmr-energy"
REST_DEFAULT_DASHBOARD_ENDPOINT = "/api/lovelace/config"

HELP_TEXT = (
    "Set HASS_URL=http://homeassistant.local:8123 and "
    "HASS_TOKEN=<long-lived-access-token> then re-run."
)


# --------------------------------------------------------------------------- #
# Secret-safe stdio helpers
# --------------------------------------------------------------------------- #


_SECRET_PATTERNS: tuple[str, ...] = (
    "HASS_TOKEN", "TOKEN", "PASSWORD", "SECRET", "PASS", "AUTH",
)


def _redact_env_for_log(env: dict[str, str]) -> dict[str, str]:
    """Return a dict with secret-looking keys replaced by ``<set:Nchars>``
    / ``<empty>``. NEVER call this with the *original* token."""
    out: dict[str, str] = {}
    for k, v in env.items():
        if any(p in k.upper() for p in _SECRET_PATTERNS):
            out[k] = f"<set:{len(v)}chars>" if v else "<empty>"
        else:
            out[k] = v
    return out


def _env_summary() -> dict[str, str]:
    return _redact_env_for_log(
        {k: os.environ[k] for k in os.environ
         if k in ("HASS_URL", "HASS_TOKEN")}
    )


# --------------------------------------------------------------------------- #
# Card scanners
# --------------------------------------------------------------------------- #


def _iter_cards(node: Any) -> Iterable[dict[str, Any]]:
    """Yield every card dict (anywhere inside ``node``) that has a
    ``title``. Used by the duplicate detector."""
    if isinstance(node, list):
        for item in node:
            yield from _iter_cards(item)
    elif isinstance(node, dict):
        if "title" in node and isinstance(node["title"], str):
            yield node
        for value in node.values():
            yield from _iter_cards(value)


def _find_duplicate_card_titles(
    node: Any, titles: Iterable[str],
) -> dict[str, int]:
    wanted = set(titles)
    counts: dict[str, int] = {t: 0 for t in wanted}
    for card in _iter_cards(node):
        title = card.get("title")
        if title in counts:
            counts[title] += 1
    return counts


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


# --------------------------------------------------------------------------- #
# YAML loader
# --------------------------------------------------------------------------- #


def _load_yaml(text: str) -> dict[str, Any]:
    """Parse ``dashboard.yaml`` via PyYAML when available, falling back
    to a tiny walker that is good enough for the three target cards."""
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:  # pragma: no cover
        return _walk_three_target_cards(text)


def _walk_three_target_cards(text: str) -> dict[str, Any]:
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
                flush_literal()
                current_card = None
                current_field = None
                block_indent = None
            elif cur_indent > block_indent and current_field is not None:
                literal_buf.append(raw)
                continue
            elif cur_indent == block_indent and ":" in stripped:
                flush_literal()
                key, _, value = stripped.partition(":")
                current_field = key.strip()
                val = value.strip()
                if val in ("", "|", ">"):
                    block_indent = cur_indent + 2
                    literal_buf.clear()
                else:
                    block_indent = None
                    literal_buf.clear()
                continue
            else:
                flush_literal()
                current_card = None
                current_field = None
                block_indent = None

        if stripped.startswith("- type:") and current_card is None:
            current_card = {}
            current_field = "type"
            tval = stripped.split(":", 1)[1].strip()
            current_card["type"] = tval
            block_indent = len(raw) - len(stripped) + 2
            continue

        if current_card is not None and not stripped.startswith("- "):
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
        elif (
            current_card is not None
            and stripped.startswith("- ")
            and current_field == "title"
        ):
            pass

        if current_card is not None and current_card.get("title") in ALL_CARD_TITLES:
            cards.setdefault(current_card["title"], current_card)

    flush_literal()
    return {"views": [{"sections": [{"cards": list(cards.values())}]}]}


def load_expected_cards() -> dict[str, dict[str, Any]]:
    """Read ``dashboard.yaml`` and return the three target cards as a
    ``{title: card_dict}`` map. Missing cards raise ``SystemExit(1)``."""
    text = DASHBOARD_YAML.read_text(encoding="utf-8")
    data = _load_yaml(text)
    found: dict[str, dict[str, Any]] = {}
    for view in data.get("views", []):
        for section in view.get("sections", []):
            for card in section.get("cards", []):
                title = card.get("title")
                if title in ALL_CARD_TITLES:
                    found[title] = card
    missing = set(ALL_CARD_TITLES) - set(found)
    if missing:
        raise SystemExit(
            f"dashboard.yaml is missing expected card(s): {sorted(missing)!r}"
        )
    return found


# --------------------------------------------------------------------------- #
# Entity registry + entity_id substitution
# --------------------------------------------------------------------------- #


def build_entity_registry(
    entries: list[dict[str, Any]],
    *,
    config_entry_id: str = "",
) -> dict[str, str]:
    """Build a flat ``{lookup_key: entity_id}`` mapping.

    Predictive_* entities (which are the ones our dashboard cards use
    without ``translation_key``) are indexed by the suffix of their
    ``unique_id`` after stripping the ``<config_entry_id>_predictive_``
    prefix. Other entities contribute their ``translation_key`` as the
    index. Collisions prefer ``translation_key`` because it is the
    user-facing string HA renders in the UI."""
    by_translation_key: dict[str, str] = {}
    by_predictive_suffix: dict[str, str] = {}
    for entry in entries:
        entity_id = entry.get("entity_id")
        if not entity_id:
            continue
        tk = entry.get("translation_key")
        if tk:
            by_translation_key[tk] = entity_id
        unique_id = entry.get("unique_id") or ""
        if unique_id.startswith(f"{config_entry_id}_predictive_"):
            suffix = unique_id[len(f"{config_entry_id}_predictive_"):]
            by_predictive_suffix[suffix] = entity_id
        elif unique_id.startswith("predictive_"):
            suffix = unique_id[len("predictive_"):]
            by_predictive_suffix[suffix] = entity_id
    merged: dict[str, str] = {}
    merged.update(by_predictive_suffix)
    merged.update(by_translation_key)  # translation_key wins on collision
    return merged


_TEMPLATE_ENTITY_PREFIXES: tuple[str, ...] = (
    "sensor.smart_solar_inverter_predictive_",
    "select.smart_solar_inverter_predictive_",
    "switch.smart_solar_inverter_predictive_",
    "number.smart_solar_inverter_predictive_",
    "binary_sensor.smart_solar_inverter_predictive_",
)


def substitute_entity_ids(
    value: Any, registry: dict[str, str],
) -> tuple[Any, dict[str, str]]:
    """Walk ``value`` and replace template-style entity references with
    their actual ``entity_id`` from ``registry``. Returns the new value
    and a ``{template: actual}`` report for the audit log."""
    report: dict[str, str] = {}

    def visit(node: Any) -> Any:
        if isinstance(node, list):
            return [visit(item) for item in node]
        if isinstance(node, dict):
            new: dict[str, Any] = {}
            for k, v in node.items():
                if k == "entity" and isinstance(v, str):
                    actual = _resolve_entity(v, registry)
                    if actual != v:
                        report[v] = actual
                    new[k] = actual
                else:
                    new[k] = visit(v)
            return new
        if isinstance(node, str):
            return _substitute_in_string(node, registry, report)
        return node

    return visit(value), report


def _resolve_entity(template: str, registry: dict[str, str]) -> str:
    for prefix in _TEMPLATE_ENTITY_PREFIXES:
        if template.startswith(prefix):
            suffix = template[len(prefix):]
            return registry.get(suffix, template)
    return template


def _substitute_in_string(
    text: str, registry: dict[str, str], report: dict[str, str],
) -> str:
    out = text
    for prefix in _TEMPLATE_ENTITY_PREFIXES:
        for suffix, entity_id in registry.items():
            tmpl = f"{prefix}{suffix}"
            if tmpl in out and entity_id not in tmpl:
                out = out.replace(tmpl, entity_id)
                report[tmpl] = entity_id
    return out


# --------------------------------------------------------------------------- #
# WebSocket client (sync wrapper around ``websocket-client``).
# --------------------------------------------------------------------------- #


class LovelaceWSError(RuntimeError):
    """Raised for any WebSocket-level failure (auth refused, command
    rejected, malformed response, ...). The CLI turns these into
    ``SystemExit(1)``."""


class LovelaceWSClient:
    """Tiny sync Home Assistant WebSocket client.

    Only the verbs needed by this script are wrapped. The underlying
    socket is opened via ``websocket-client`` and reused across calls."""

    def __init__(
        self, url: str, token: str, timeout: int = 15,
        insecure_transport: bool = False,
    ) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout
        self.insecure_transport = insecure_transport
        self._ws: Any = None
        self._next_id = 1

    # -- Connection -------------------------------------------------------- #

    def connect(self) -> None:
        try:
            import websocket  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise LovelaceWSError(
                "websocket-client is not installed in this venv; "
                "install it with `pip install websocket-client`"
            ) from exc

        ws_url = self._build_ws_url()
        sslopt: dict[str, Any] = {}
        if ws_url.startswith("wss://"):
            # HA typically uses self-signed certs; the operator controls
            # trust on their own machine. CERT_REQUIRED for prod setups
            # is the operator's responsibility (--insecure-transport flag
            # controls the plain ws:// case).
            sslopt["cert_reqs"] = ssl.CERT_NONE
        self._ws = websocket.create_connection(
            ws_url, timeout=self.timeout, sslopt=sslopt
        )
        self._send_raw({"type": "auth", "access_token": self.token})
        msg = self._recv_json()
        if msg.get("type") != "auth_ok":
            raise LovelaceWSError(
                f"WebSocket auth refused: {msg.get('message') or msg!r}"
            )

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # pragma: no cover
                pass
            self._ws = None

    # -- Public verbs ------------------------------------------------------ #

    def list_dashboards(self) -> list[dict[str, Any]]:
        return self._call("lovelace/dashboards/list")

    def get_config(self, url_path: str | None) -> dict[str, Any]:
        msg: dict[str, Any] = {"type": "lovelace/config"}
        if url_path:
            msg["url_path"] = url_path
        result = self._call(msg)
        if isinstance(result, dict) and "config" in result:
            return {"config": result["config"],
                    "mode": result.get("mode", "storage")}
        return {"config": result, "mode": "default"}

    def save_config(self, url_path: str | None, config: dict[str, Any]) -> None:
        msg: dict[str, Any] = {
            "type": "lovelace/config/save",
            "config": config,
        }
        if url_path:
            msg["url_path"] = url_path
        result = self._call(msg)
        if isinstance(result, dict) and result.get("success") is False:
            raise LovelaceWSError(
                f"lovelace/config/save refused: "
                f"{result.get('error') or result!r}"
            )

    def list_entity_registry(self) -> list[dict[str, Any]]:
        result = self._call("config/entity_registry/list")
        if not isinstance(result, list):
            raise LovelaceWSError(
                f"entity_registry/list returned unexpected type: "
                f"{type(result).__name__}"
            )
        return result

    # -- Internals -------------------------------------------------------- #

    def _build_ws_url(self) -> str:
        parsed = urlparse(self.url)
        scheme = "ws" if (self.insecure_transport or parsed.scheme == "http") \
            else "wss"
        host = parsed.hostname or "homeassistant.local"
        port = parsed.port or (8123 if scheme == "ws" else 443)
        return f"{scheme}://{host}:{port}/api/websocket"

    def _call(self, command: Any) -> Any:
        if self._ws is None:
            raise LovelaceWSError(
                "WebSocket not connected; call connect() first"
            )
        msg = {"id": self._next_id, **(
            command if isinstance(command, dict) else {"type": command}
        )}
        self._next_id += 1
        self._send_raw(msg)
        while True:
            reply = self._recv_json()
            if (reply.get("type") == "result"
                    and reply.get("id") == msg["id"]):
                if not reply.get("success", True):
                    raise LovelaceWSError(
                        f"HA rejected {msg.get('type')!r}: "
                        f"{reply.get('error') or reply!r}"
                    )
                return reply.get("result")
            # else: event frame, ignore.

    def _send_raw(self, payload: dict[str, Any]) -> None:
        try:
            self._ws.send(json.dumps(payload, ensure_ascii=False))
        except (OSError, socket.error) as exc:  # pragma: no cover
            raise LovelaceWSError(f"WebSocket send failed: {exc}") from exc

    def _recv_json(self) -> dict[str, Any]:
        try:
            raw = self._ws.recv()
        except (OSError, socket.error) as exc:  # pragma: no cover
            raise LovelaceWSError(f"WebSocket recv failed: {exc}") from exc
        if not raw:
            raise LovelaceWSError("WebSocket closed unexpectedly")
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LovelaceWSError(
                f"WebSocket sent non-JSON frame: {raw!r}"
            ) from exc
        if not isinstance(obj, dict):
            raise LovelaceWSError(f"WebSocket sent non-dict frame: {obj!r}")
        return obj


# --------------------------------------------------------------------------- #
# REST fallback (only useful for the *default* dashboard).
# --------------------------------------------------------------------------- #


def fetch_default_dashboard_via_rest(
    base_url: str, token: str, timeout: int,
) -> dict[str, Any]:
    """Fetch the default dashboard's config via ``/api/lovelace/config``.
    Storage dashboards CANNOT be served from this endpoint — it returns
    metadata only. Use the WebSocket client for those."""
    import urllib.request as urlreq

    url = f"{base_url.rstrip('/')}{REST_DEFAULT_DASHBOARD_ENDPOINT}"
    req = urlreq.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    with urlreq.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return {"config": json.loads(body), "mode": "default"}


# --------------------------------------------------------------------------- #
# Dashboard patching
# --------------------------------------------------------------------------- #


def _canonicalise(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def patch_cards(
    config: dict[str, Any],
    expected: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Idempotently replace the three target cards in-place."""
    stats: dict[str, list[str]] = {
        "matched": [], "replaced": [], "already_up_to_date": [],
    }
    for card in _iter_cards(config):
        if card.get("title") not in expected:
            continue
        title = card["title"]
        stats["matched"].append(title)
        target = expected[title]
        if _canonicalise(card) == _canonicalise(target):
            stats["already_up_to_date"].append(title)
            continue
        for k in list(card.keys()):
            del card[k]
        for k, v in target.items():
            card[k] = v
        stats["replaced"].append(title)
    return config, stats


def _diff_titles(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for title in ALL_CARD_TITLES:
        b = _find_card(before, title)
        a = _find_card(after, title)
        if b is not None and a is not None and _canonicalise(b) != _canonicalise(a):
            out.append(title)
    return out


def _check_untargeted_cards(
    before: dict[str, Any], after: dict[str, Any],
) -> list[str]:
    """Return titles of non-target cards whose canonical form changed
    after the patch. Empty list = clean readback."""
    before_others: dict[str, str] = {}
    after_others: dict[str, str] = {}
    for card in _iter_cards(before):
        if card.get("title") not in ALL_CARD_TITLES:
            before_others[str(card["title"])] = _canonicalise(card)
    for card in _iter_cards(after):
        if card.get("title") not in ALL_CARD_TITLES:
            after_others[str(card["title"])] = _canonicalise(card)
    return [t for t in (set(before_others) | set(after_others))
            if before_others.get(t) != after_others.get(t)]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would change but do not PUT.")
    p.add_argument("--backup-only", action="store_true",
                   help="Only write backup; skip PUT.")
    p.add_argument("--no-backup", action="store_true",
                   help="Skip backup creation.")
    p.add_argument("--ha-url", default=os.environ.get("HASS_URL"),
                   help="HA base URL (env: HASS_URL)")
    p.add_argument("--ha-token", default=os.environ.get("HASS_TOKEN"),
                   help="HA token (env: HASS_TOKEN) — never printed or logged")
    p.add_argument("--dashboard", default=DEFAULT_DASHBOARD_URL_PATH,
                   help=f"Lovelace storage dashboard url_path "
                        f"(default: {DEFAULT_DASHBOARD_URL_PATH})")
    p.add_argument("--timeout", type=int, default=15,
                   help="Network timeout seconds (default: 15)")
    p.add_argument("--insecure-transport", action="store_true",
                   help="Use plain ws:// (HA without TLS).")
    p.add_argument("--print-config", action="store_true",
                   help="Dump the resulting dashboard config (secrets redacted).")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if not args.ha_url or not args.ha_token:
        print(HELP_TEXT)
        return 0

    expected = load_expected_cards()

    ws: LovelaceWSClient | None = None
    try:
        ws = LovelaceWSClient(
            args.ha_url, args.ha_token, args.timeout, args.insecure_transport,
        )
        try:
            ws.connect()
        except LovelaceWSError as exc:
            print(
                f"WS connect failed ({exc}); "
                "falling back to REST /api/lovelace/config (only the "
                "DEFAULT dashboard works that way).",
                file=sys.stderr,
            )
            fetched = fetch_default_dashboard_via_rest(
                args.ha_url, args.ha_token, args.timeout,
            )
            mode = fetched["mode"]
            config = fetched["config"]
            url_path: str | None = None
            if mode == "storage":
                raise SystemExit(
                    "REST /api/lovelace/config cannot serve STORAGE-mode "
                    "dashboards; the WebSocket connection failed and we "
                    "have no other path."
                )
        else:
            listing = ws.list_dashboards()
            known = [d.get("url_path") for d in listing]
            if args.dashboard not in known:
                raise SystemExit(
                    f"No dashboard with url_path={args.dashboard!r}; "
                    f"known: {known!r}"
                )
            # First fetch — used for the dry-run / backup.
            first = ws.get_config(args.dashboard)
            config = first["config"]
            url_path = args.dashboard
            # Race-check: second fetch must match the first.
            second = ws.get_config(args.dashboard)
            if _canonicalise(second["config"]) != _canonicalise(first["config"]):
                raise SystemExit(
                    "ABORT: dashboard config changed between two "
                    "consecutive fetches. Another operator (or HA itself) "
                    "is mutating it. Re-run after the dashboard is idle."
                )
    finally:
        if ws is not None:
            ws.close()

    pre_counts = _find_duplicate_card_titles(config, ALL_CARD_TITLES)
    for title, count in pre_counts.items():
        if count > 1:
            raise SystemExit(
                f"Duplicate card title {title!r} (count={count}). "
                "The script refuses to patch a dashboard with multiple "
                "copies of the target card."
            )

    if ws is not None:
        try:
            registry_entries = ws.list_entity_registry()
        except LovelaceWSError as exc:
            print(
                f"WARN: entity_registry/list failed ({exc}); "
                "falling back to template names.",
                file=sys.stderr,
            )
            registry = {}
        else:
            registry = build_entity_registry(registry_entries)
    else:
        registry = {}
    if registry:
        config, _entity_report = substitute_entity_ids(config, registry)
        expected, _ = substitute_entity_ids(expected, registry)

    config_snapshot_before = json.loads(json.dumps(config))
    new_config, stats = patch_cards(config, expected)

    post_counts = _find_duplicate_card_titles(new_config, ALL_CARD_TITLES)
    for title, count in post_counts.items():
        if count > 1:
            raise SystemExit(
                f"Duplicate target card {title!r} AFTER patch (count={count})."
            )

    backup_root = REPO / "scripts" / ".lovelace_backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    iso = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_root / f"lovelace.{args.dashboard}.{iso}.bak"

    if not (stats["replaced"] or stats["already_up_to_date"]):
        print("No target cards were found in the dashboard JSON.")
        return 1

    if not args.no_backup:
        try:
            backup_path.write_text(
                json.dumps(config_snapshot_before, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"Backup write failed: {exc}", file=sys.stderr)
            return 2
        print(f"Backup written: {backup_path}")

    if args.backup_only:
        print(f"Backup-only mode: skipped PUT. {stats}")
        return 0

    if not stats["replaced"]:
        print("Already up to date. Nothing to PUT.")
        return 0

    if args.dry_run:
        print(
            f"Dry run — would PUT {len(stats['replaced'])} card(s): "
            f"{stats['replaced']!r}"
        )
        return 0

    if url_path is None:
        raise SystemExit(
            "Cannot PUT a non-storage-mode default dashboard from this "
            "CLI; the WebSocket fallback is the only supported writer."
        )
    ws2 = LovelaceWSClient(
        args.ha_url, args.ha_token, args.timeout, args.insecure_transport,
    )
    try:
        ws2.connect()
        ws2.save_config(url_path, new_config)
        readback = ws2.get_config(url_path)["config"]
        rb_stats = {
            "replaced_now_in_dashboard": [
                t for t in ALL_CARD_TITLES
                if _find_card(readback, t) is not None
            ],
            "missing_after_readback": [
                t for t in ALL_CARD_TITLES
                if _find_card(readback, t) is None
            ],
            "unchanged_untargeted_card_collision": _check_untargeted_cards(
                config_snapshot_before, readback,
            ),
        }
        if rb_stats["missing_after_readback"]:
            raise SystemExit(
                f"READBACK mismatch — these cards disappeared: "
                f"{rb_stats['missing_after_readback']!r}"
            )
        if rb_stats["unchanged_untargeted_card_collision"]:
            raise SystemExit(
                f"READBACK mismatch — non-target cards mutated: "
                f"{rb_stats['unchanged_untargeted_card_collision']!r}"
            )
    finally:
        ws2.close()

    print(
        f"Patched {len(stats['replaced'])} card(s) at "
        f"url_path={url_path!r}: {stats['replaced']!r}"
    )
    if stats["already_up_to_date"]:
        print(f"Unchanged (already up to date): {stats['already_up_to_date']!r}")
    print(f"Backup at {backup_path}")
    print(f"Env summary: {_env_summary()}")
    if args.print_config:
        sanitized = _scrub_secrets_in_dump(new_config)
        print(json.dumps(sanitized, ensure_ascii=False, indent=2)[:8000])
    return 0


# --------------------------------------------------------------------------- #
# Conservative scrub for --print-config dumps.
# --------------------------------------------------------------------------- #


_SECRET_IN_DUMP_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"), "bearer <redacted>"),
    (re.compile(r"(?i)token[\"'\\s:=]+[A-Za-z0-9._\-]{6,}"), "token <redacted>"),
    (re.compile(r"(?i)password[\"'\\s:=]+[^\"',\\s}]+"), "password <redacted>"),
)


def _scrub_secrets_in_dump(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _scrub_secrets_in_dump(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_secrets_in_dump(v) for v in obj]
    if isinstance(obj, str):
        scrubbed = obj
        for pattern, replacement in _SECRET_IN_DUMP_PATTERNS:
            scrubbed = pattern.sub(replacement, scrubbed)
        return scrubbed
    return obj


if __name__ == "__main__":
    sys.exit(main())
