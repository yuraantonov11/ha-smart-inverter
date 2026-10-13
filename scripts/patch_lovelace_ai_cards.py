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


_INTEGRATION_DOMAIN = "powmr_inverter"


def _predictive_suffix(
    entry: dict[str, Any], config_entry_id: str | None = None,
) -> str | None:
    config_entry_id = entry.get("config_entry_id") or config_entry_id
    unique_id = entry.get("unique_id") or ""
    if not config_entry_id:
        return None
    marker = f"{config_entry_id}_predictive_"
    if not unique_id.startswith(marker):
        return None
    return unique_id[len(marker):]


def _iter_target_entity_ids(config: dict[str, Any]) -> Iterable[str]:
    for _, card in _iter_cards_with_path(config):
        if card.get("title") not in ALL_CARD_TITLES:
            continue
        yield from _iter_entity_values(card)


def _iter_entity_values(node: Any) -> Iterable[str]:
    if isinstance(node, list):
        for item in node:
            yield from _iter_entity_values(item)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key == "entity" and isinstance(value, str):
                yield value
            else:
                yield from _iter_entity_values(value)


def _select_predictive_config_entry(
    entries: list[dict[str, Any]], config: dict[str, Any],
    required_templates: set[str],
) -> str:
    """Choose the one PowMr entry owning the AI entities, failing closed on ambiguity."""
    required_suffixes = {
        template[len(prefix):]
        for template in required_templates
        for prefix in _TEMPLATE_ENTITY_PREFIXES
        if template.startswith(prefix)
    }
    candidates: dict[str, set[str]] = {}
    entity_owner: dict[str, str] = {}
    for entry in entries:
        if entry.get("platform") != _INTEGRATION_DOMAIN:
            continue
        entry_id = entry.get("config_entry_id")
        suffix = _predictive_suffix(entry)
        if not entry_id or suffix is None:
            continue
        if entry.get("entity_id"):
            entity_owner[entry["entity_id"]] = entry_id
        if suffix in required_suffixes:
            candidates.setdefault(entry_id, set()).add(suffix)

    referenced_owners = {
        entity_owner[entity]
        for entity in _iter_target_entity_ids(config)
        if entity in entity_owner
    }
    if referenced_owners:
        candidates = {entry_id: suffixes for entry_id, suffixes in candidates.items()
                     if entry_id in referenced_owners}
    candidate_ids = sorted(candidates)
    if len(candidate_ids) == 1:
        return candidate_ids[0]
    if not candidate_ids:
        missing = ", ".join(sorted(required_suffixes)) or "required AI entities"
        raise SystemExit(
            "entity_registry/list returned no PowMr config entry owning "
            f"the required AI entities ({missing}); refusing to save"
        )
    raise SystemExit(
        "entity registry is ambiguous: multiple PowMr config entries could "
        f"own these AI cards ({', '.join(candidate_ids)}); refusing to save"
    )


def build_entity_registry(
    entries: list[dict[str, Any]],
    *,
    config_entry_id: str,
) -> dict[str, str]:
    """Build a predictive unique-ID suffix map for exactly one entry."""
    by_translation_key: dict[str, str] = {}
    by_predictive_suffix: dict[str, str] = {}
    for entry in entries:
        entry_config_id = entry.get("config_entry_id")
        if entry_config_id and entry_config_id != config_entry_id:
            continue
        if entry.get("platform") not in (None, _INTEGRATION_DOMAIN):
            continue
        entity_id = entry.get("entity_id")
        if not entity_id:
            continue
        translation_key = entry.get("translation_key")
        if translation_key:
            by_translation_key[translation_key] = entity_id
        suffix = _predictive_suffix(entry, config_entry_id)
        if suffix:
            by_predictive_suffix[suffix] = entity_id
        else:
            unique_id = entry.get("unique_id") or ""
            if unique_id.startswith("predictive_"):
                by_predictive_suffix[unique_id[len("predictive_"):]] = entity_id
    merged = dict(by_predictive_suffix)
    merged.update(by_translation_key)
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
        # Standard TLS verification by default: the operator's
        # ``certifi`` / system trust store decides which CA bundle
        # is consulted. ``--insecure-transport`` downgrades to
        # plain ``ws://`` and never affects the ``wss://`` case.
        # The previous version set ``cert_reqs=ssl.CERT_NONE``
        # unconditionally for ``wss://`` — that is a real
        # man-in-the-middle vulnerability: any attacker on the
        # network path between the operator's machine and HA
        # could terminate the TLS connection with a forged
        # certificate and inject their own Lovelace config. The
        # 0cb14f2 audit review (Юра п.4) explicitly forbids
        # that workaround.
        sslopt: dict[str, Any] = {}
        self._ws = websocket.create_connection(
            ws_url, timeout=self.timeout, sslopt=sslopt
        )
        # Correct HA WebSocket handshake:
        #   1. Server sends ``{"type": "auth_required"}``.
        #   2. We send ``{"type": "auth", "access_token": <token>}``.
        #   3. Server replies ``{"type": "auth_ok"}`` (or
        #      ``{"type": "auth_invalid", "message": …}``).
        # The previous version skipped step 1 and sent the
        # ``auth`` frame immediately on connect — that works
        # with some websocket-server libraries but HA expects
        # the explicit ``auth_required`` sentinel first; without
        # it the auth response is ambiguous and the script
        # could not tell a valid ``auth_ok`` apart from a
        # late-arriving ``auth_required``.
        first = self._recv_json()
        if first.get("type") != "auth_required":
            raise LovelaceWSError(
                f"unexpected first WebSocket message (expected "
                f"auth_required, got {first.get('type')!r})"
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
# The 0cb14f2 audit review (Юра п.4) explicitly forbids a REST
# fallback to ``/api/lovelace/config``. That endpoint returns only
# dashboard metadata (id, url_path, mode), NOT the config dict —
# a successful read would silently mask a failed write because
# the next ``save_config`` step would then operate on whatever
# was in the local cache, and the operator's "patched N cards"
# message would lie. The WebSocket path is the ONLY supported
# writer; if the connection fails the operator is told to
# investigate the WebSocket failure rather than fall through to
# a half-supported REST read.
# --------------------------------------------------------------------------- #




# --------------------------------------------------------------------------- #
# Dashboard patching
# --------------------------------------------------------------------------- #


def _canonicalise(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def patch_cards(
    config: dict[str, Any],
    expected: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Idempotently insert-or-replace the three target cards.

    The 0cb14f2 / 34f4f71 audit review (Юра п.4) requires that a
    missing target card (e.g. the new ``AI · SOC-стан`` card) is
    inserted once, and a second run does not duplicate it.

    Insertion strategy: locate the first existing target card
    and insert the missing ones immediately after it inside
    the same view's ``cards`` list. If NO target card exists
    yet, fall back to a structural anchor — the ``HEMS`` view
    title — and append at the end of its ``cards`` list. If
    even that anchor is missing, the new card is appended to
    the first view's ``cards`` list (and the caller's pre-flight
    ``_find_duplicate_card_titles`` check still aborts on any
    real duplicate).
    """
    stats: dict[str, list[str]] = {
        "matched": [], "replaced": [], "already_up_to_date": [],
        "inserted": [],
    }
    seen_titles: set[str] = set()
    # Pass 1: replace existing target cards. Capture insertion
    # context from the first match — view + parent-cards list +
    # index — so Pass 2 can splice the missing ones there.
    insertion_ctx: list[dict[str, Any]] | None = None
    for card in _iter_cards(config):
        if card.get("title") not in expected:
            continue
        seen_titles.add(card["title"])
        if insertion_ctx is None:
            insertion_ctx = _find_cards_list_for_card(config, card)
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
    # Pass 2: insert any target card not present yet, immediately
    # after the first existing target. If no target exists,
    # fall back to a ``HEMS`` view, then to the first view.
    for title, body in expected.items():
        if title in seen_titles:
            continue
        cards_list = _resolve_cards_list(config, insertion_ctx)
        if cards_list is None:
            raise SystemExit(
                f"cannot insert {title!r}: dashboard has no view "
                f"with a 'cards' list and no ``HEMS`` anchor"
            )
        if any(c.get("title") == title for c in cards_list):
            # Defensive: a second pass would never run, but if a
            # stale card slipped in via the operator's own edit,
            # we MUST NOT duplicate.
            continue
        cards_list.append(body)
        seen_titles.add(title)
        stats["inserted"].append(title)
    return config, stats


def _find_cards_list_for_card(
    config: dict[str, Any], card: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Walk the config tree and return the parent ``cards`` list
    for the given card. Returns ``None`` if the card is not
    nested in a ``cards`` list (rare, defensive).
    """
    def _walk(node: Any) -> list[dict[str, Any]] | None:
        if isinstance(node, list):
            if any(c is card for c in node):
                return node
            for item in node:
                found = _walk(item)
                if found is not None:
                    return found
        elif isinstance(node, dict):
            for value in node.values():
                found = _walk(value)
                if found is not None:
                    return found
        return None
    return _walk(config)


def _resolve_cards_list(
    config: dict[str, Any],
    ctx: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Resolve the destination ``cards`` list for a new card.

    Priority: the context captured during Pass 1 (insert right
    after the first existing target) → the ``HEMS`` view's
    ``cards`` list → the first view's ``cards`` list → None.

    The live dashboard may name the view ``AI · HEMS`` (path
    ``powmr-ai``) instead of just ``HEMS`` (path ``powmr-hems``).
    We match by substring on the title so the same patcher works
    against both the source-of-truth (``dashboard.yaml`` in
    develop) and a previously-deployed live dashboard.
    """
    if ctx is not None:
        return ctx
    for view in config.get("views", []):
        if "HEMS" in view.get("title", "") and "cards" in view:
            return view["cards"]
    for view in config.get("views", []):
        if "cards" in view:
            return view["cards"]
    return None


def _diff_titles(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for title in ALL_CARD_TITLES:
        b = _find_card(before, title)
        a = _find_card(after, title)
        if b is not None and a is not None and _canonicalise(b) != _canonicalise(a):
            out.append(title)
    return out


def _check_untargeted_cards(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[str]:
    """Return structural paths of non-target cards whose canonical
    form changed after the patch. Empty list = clean readback.

    The 0cb14f2 audit review (Юра п.4) requires structural-path
    comparison, not by title. Two cards with the SAME title in
    different view paths are now distinguished — a drift in
    either path is reported. Only the target cards (the three
    ``ALL_CARD_TITLES``) are excluded; the rest of the dashboard
    is checked verbatim.
    """
    before_others: dict[str, str] = {}
    after_others: dict[str, str] = {}
    for path, card in _iter_cards_with_path(before):
        if card.get("title") in ALL_CARD_TITLES:
            continue
        before_others[path] = _canonicalise(card)
    for path, card in _iter_cards_with_path(after):
        if card.get("title") in ALL_CARD_TITLES:
            continue
        after_others[path] = _canonicalise(card)
    return [
        path for path in (set(before_others) | set(after_others))
        if before_others.get(path) != after_others.get(path)
    ]


def _iter_cards_with_path(
    node: Any, prefix: str = "config",
) -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield ``(structural_path, card)`` pairs for every card
    in the dashboard config. The path is dot-separated:
    ``config.views.0.sections.1.cards.2`` — operators can grep
    the readback diff for an exact location.
    """
    if isinstance(node, list):
        for i, item in enumerate(node):
            yield from _iter_cards_with_path(item, f"{prefix}.{i}")
    elif isinstance(node, dict):
        if node.get("type") in _CARD_TYPES and "title" in node:
            yield prefix, node
        for k, v in node.items():
            yield from _iter_cards_with_path(v, f"{prefix}.{k}")


_CARD_TYPES: frozenset[str] = frozenset({
    "entity", "entities", "glance", "button", "conditional",
    "iframe", "map", "markdown", "picture", "picture-entity",
    "picture-glance", "sensor", "history-graph", "statistic",
    "weather-forecast", "energy-date-selection", "energy-graph",
    "energy-sources-table", "energy-distribution", "energy-usage-table",
    "tile", "grid", "vertical-stack", "horizontal-stack", "heading",
    "divider", "section", "custom:", "custom:energy-flow-card",
    "custom:k-flow-card", "custom:pv-comparison-card", "k-flow-card",
    "energy-flow-card", "mini-graph", "tile",
})


def _readback_target_card_match(
    before: dict[str, Any], after: dict[str, Any],
) -> list[str]:
    """Return the structural paths of target cards whose
    canonical content in the readback does NOT match the
    expected card body. The 0cb14f2 audit review (Юра п.4)
    requires the readback to confirm the EXACT expected content,
    not just the presence of the card.
    """
    expected = load_expected_cards()
    # Resolve entity_ids the same way main() does — the
    # readback must see the same post-resolution config.
    registry = _make_minimal_registry(before, after)
    expected_resolved, _ = substitute_entity_ids(
        {k: dict(v) for k, v in expected.items()}, registry,
    )
    mismatches: list[str] = []
    for path, card in _iter_cards_with_path(after):
        title = card.get("title")
        if title not in expected_resolved:
            continue
        want = _canonicalise(expected_resolved[title])
        got = _canonicalise(card)
        if got != want:
            mismatches.append(path)
    return mismatches


def _make_minimal_registry(
    before: dict[str, Any], after: dict[str, Any],
) -> dict[str, str]:
    """Build a registry from the template names already present
    in the dashboard — used by the readback content check so
    that the expected config is resolved the same way as
    during the actual patch. Templates with no entity_id
    match in the dashboard are passed through unchanged.
    """
    registry: dict[str, str] = {}
    for path, card in _iter_cards_with_path(before):
        ent = card.get("entity")
        if isinstance(ent, str) and ent.startswith("sensor.smart_solar_inverter_"):
            registry.setdefault(
                "predictive_decision_state", ent,
            )
    for path, card in _iter_cards_with_path(after):
        ent = card.get("entity")
        if isinstance(ent, str) and ent.startswith("sensor.smart_solar_inverter_"):
            registry.setdefault(
                "predictive_decision_state", ent,
            )
        if isinstance(ent, str) and ent.startswith(
            "select.smart_solar_inverter_"
        ):
            registry.setdefault("predictive_target_soc", ent)
    return registry


def _target_card_mismatches(
    config: dict[str, Any], expected: dict[str, dict[str, Any]],
) -> list[str]:
    actual: dict[str, list[dict[str, Any]]] = {}
    for _, card in _iter_cards_with_path(config):
        title = card.get("title")
        if title in expected:
            actual.setdefault(title, []).append(card)
    mismatches = []
    for title, wanted in expected.items():
        cards = actual.get(title, [])
        if len(cards) != 1:
            mismatches.append(f"{title!r} (found {len(cards)})")
        elif _canonicalise(cards[0]) != _canonicalise(wanted):
            mismatches.append(title)
    return mismatches


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


def _collect_required_templates(
    expected: dict[str, dict[str, Any]],
) -> set[str]:
    """Return the set of entity_id template strings that MUST
    resolve against the registry before the patcher saves
    anything. Anything else (chinese card entity_ids, foreign
    card sensors, ...) is left untouched.

    Templates that are referenced by the three target cards
    (Recommendation / Learning / SOC-стан) MUST be resolved —
    a missing entry would silently leave the live dashboard
    with ``sensor.smart_solar_inverter_…`` references that do
    not exist in the operator's HA, breaking the AI view at
    runtime.
    """
    templates: set[str] = set()
    for body in expected.values():
        for tmpl in _iter_entity_id_templates(body):
            templates.add(tmpl)
    return templates


def _iter_entity_id_templates(node: Any) -> Iterable[str]:
    """Yield every ``smart_solar_inverter_…`` template entity_id
    referenced anywhere in the node tree (entity keys and
    markdown content). These are the tokens the registry MUST
    resolve before the patcher can save.
    """
    if isinstance(node, list):
        for item in node:
            yield from _iter_entity_id_templates(item)
    elif isinstance(node, dict):
        for k, v in node.items():
            if k == "entity" and isinstance(v, str):
                if any(v.startswith(prefix) for prefix in _TEMPLATE_ENTITY_PREFIXES):
                    yield v
            yield from _iter_entity_id_templates(v)
    elif isinstance(node, str):
        for prefix in _TEMPLATE_ENTITY_PREFIXES:
            idx = 0
            while True:
                idx = node.find(prefix, idx)
                if idx < 0:
                    break
                # A real entity such as sensor.garazh_smart_solar_... contains
                # the template prefix as a substring. Only recognize a token
                # at a non-identifier boundary, never inside an entity ID.
                if idx > 0 and (node[idx - 1].isalnum() or node[idx - 1] == "_"):
                    idx += len(prefix)
                    continue
                # Find the end of the suffix: non-alphanumeric
                # / non-underscore after the prefix.
                end = idx + len(prefix)
                while end < len(node) and (
                    node[end].isalnum() or node[end] == "_"
                ):
                    end += 1
                yield node[idx:end]
                idx = end


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if not args.ha_url or not args.ha_token:
        print(HELP_TEXT)
        return 0

    expected = load_expected_cards()
    required_templates = _collect_required_templates(expected)

    # Coord audit (Юра п.4): the WebSocket is opened ONCE
    # and held open through registry, substitution, save,
    # and readback. Closing before any of these steps leaves
    # ``_ws = None`` and the next verb raises
    # ``LovelaceWSError("WebSocket not connected")`` — which
    # the previous version silently swallowed and saved
    # template-style entity_ids (``sensor.smart_solar_inverter_…``)
    # to the live dashboard. That is the bug 6bbbd25
    # reproduced.
    ws = LovelaceWSClient(
        args.ha_url, args.ha_token, args.timeout, args.insecure_transport,
    )
    try:
        try:
            ws.connect()
        except LovelaceWSError as exc:
            raise SystemExit(
                f"WebSocket connect failed: {exc}. The previous REST "
                f"fallback to /api/lovelace/config was removed in the "
                f"0cb14f2 audit review (Юра п.4): that endpoint returns "
                f"only dashboard metadata, NOT the config dict, so it "
                f"could mask a real write with a stale cache and the "
                f"user-visible 'patches three cards' message would lie. "
                f"Investigate the WebSocket failure before retrying."
            ) from exc

        # ---- discover the storage dashboard --------------------
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

        pre_counts = _find_duplicate_card_titles(config, ALL_CARD_TITLES)
        for title, count in pre_counts.items():
            if count > 1:
                raise SystemExit(
                    f"Duplicate card title {title!r} (count={count}). "
                    "The script refuses to patch a dashboard with multiple "
                    "copies of the target card."
                )

        # ---- entity registry (must succeed before substitution) ----
        # The previous version caught ``LovelaceWSError`` here and
        # used an empty registry, which silently left template
        # entity_ids in the saved config. The 6bbbd25 review
        # (Юра п.4) requires a hard stop: if the registry query
        # fails OR if any required template cannot be resolved,
        # we MUST NOT save. The WebSocket stays open through
        # this check, so the readback we are about to do
        # against ``ws`` sees the same connection state.
        try:
            registry_entries = ws.list_entity_registry()
        except LovelaceWSError as exc:
            raise SystemExit(
                f"entity_registry/list failed: {exc}. The patcher "
                f"refuses to save without a registry because the live "
                f"dashboard would keep template-style entity_ids "
                f"that the operator's HA cannot resolve. Re-run "
                f"after the registry query is healthy."
            ) from exc
        config_entry_id = _select_predictive_config_entry(
            registry_entries, config, required_templates,
        )
        registry = build_entity_registry(
            registry_entries, config_entry_id=config_entry_id,
        )

        # ---- substitution: every required template MUST resolve ----
        unresolved = sorted(
            tmpl for tmpl in required_templates
            if _resolve_entity(tmpl, registry) == tmpl
        )
        if unresolved:
            # Coord audit (Юра п.4): a single unresolved entity
            # is a HARD STOP. We do NOT save anything; we do
            # NOT close the connection until the error message
            # has been printed (the caller will close via the
            # ``finally`` below).
            raise SystemExit(
                "refusing to save — these required entity_id templates "
                "did not resolve against the entity registry:\n  "
                + "\n  ".join(unresolved)
                + "\nThe patcher does NOT save a dashboard that contains "
                "template entity_ids the operator's HA cannot resolve "
                "(they would render as 'unavailable' on every AI view)."
            )
        # Apply the substitution to BOTH the live config AND the
        # expected card bodies — the post-substitution
        # ``expected`` is what the readback will compare against.
        # Resolve only canonical target cards. Unrelated dashboard content
        # stays byte-for-byte as read from HA.
        expected, _entity_report = substitute_entity_ids(expected, registry)

        # ---- patch, then sanity-check, then backup, then save ----
        config_snapshot_before = json.loads(json.dumps(config))
        new_config, stats = patch_cards(config, expected)

        post_counts = _find_duplicate_card_titles(new_config, ALL_CARD_TITLES)
        for title, count in post_counts.items():
            if count > 1:
                raise SystemExit(
                    f"Duplicate target card {title!r} AFTER patch "
                    f"(count={count})."
                )

        # Coord audit (Юра п.4): the "replaced + inserted +
        # already_up_to_date" branches each separately short-
        # circuit the save path. The pre-patch snapshot is
        # always written as the operator's recovery artifact,
        # even when nothing changed, so they have a byte-exact
        # fallback to ``patch_lovelace_ai_cards.py --backup-only``.
        if not (stats["replaced"] or stats["already_up_to_date"]
                or stats["inserted"]):
            print("No target cards were found in the dashboard JSON.")
            return 1

        # ---- pre-write foreign-content check (before any disk write)
        # After substitution, the new_config must still contain
        # NO template entity_ids (defence-in-depth: catches a
        # future ``_resolve_entity`` regression). The
        # ``_substitute_in_string`` walker also handles nested
        # ``content`` strings, so this check covers everything.
        target_cards = {
            title: card for _, card in _iter_cards_with_path(new_config)
            if (title := card.get("title")) in ALL_CARD_TITLES
        }
        leftover = sorted(_iter_entity_id_templates(target_cards))
        if leftover:
            raise SystemExit(
                "refusing to save — template entity_ids survived "
                f"substitution: {leftover!r}. The patcher is broken."
            )

        # ---- backup
        backup_root = REPO / "scripts" / ".lovelace_backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        iso = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_root / f"lovelace.{args.dashboard}.{iso}.bak"
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
        if not (stats["replaced"] or stats["inserted"]):
            print("Already up to date. Nothing to PUT.")
            return 0
        if args.dry_run:
            print(
                f"Dry run — would PUT {len(stats['replaced'])} replaced, "
                f"{len(stats['inserted'])} inserted: "
                f"replaced={stats['replaced']!r} "
                f"inserted={stats['inserted']!r}"
            )
            return 0

        if url_path is None:
            raise SystemExit(
                "Cannot PUT a non-storage-mode default dashboard from this "
                "CLI; the WebSocket fallback is the only supported writer."
            )

        # Recheck freshness immediately before the write. The WS stays open
        # through registry resolution, backup, save, and readback.
        latest = ws.get_config(url_path)["config"]
        if _canonicalise(latest) != _canonicalise(config_snapshot_before):
            raise SystemExit(
                "ABORT: dashboard config changed while preparing the patch; "
                "nothing was saved. Re-run against the current dashboard."
            )

        # ---- save + readback on the SAME ws (no close/reopen).
        ws.save_config(url_path, new_config)
        readback = ws.get_config(url_path)["config"]
        # Coord audit (Юра п.4): two-part readback check.
        foreign_paths = _check_untargeted_cards(
            config_snapshot_before, readback,
        )
        content_mismatches = _target_card_mismatches(readback, expected)
        if foreign_paths:
            raise SystemExit(
                f"READBACK mismatch — non-target cards mutated at: "
                f"{foreign_paths!r}"
            )
        if content_mismatches:
            raise SystemExit(
                f"READBACK content mismatch — target card content "
                f"differs from expected at: {content_mismatches!r}"
            )
    finally:
        ws.close()

    print(
        f"Patched {len(stats['replaced'])} card(s), "
        f"inserted {len(stats['inserted'])} card(s) at "
        f"url_path={url_path!r}: replaced={stats['replaced']!r} "
        f"inserted={stats['inserted']!r}"
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
