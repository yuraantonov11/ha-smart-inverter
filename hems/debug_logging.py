"""HEMS debug logging utility.

Writes a structured log of every HEMS evaluation cycle to a file the
user can inspect after the day is over. The production path uses the
existing INFO/DEBUG loggers as before, but the frequent per-cycle
chatter goes here to keep the main log clean.

The log file is at /config/powmr_hems_debug.log and rotates daily at
midnight (HA timezone). Total size budget: keep last 3 days so the log
never grows without bound.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_LOG_PATH = Path("/config/powmr_hems_debug.log")
_ROTATE_AT: dict[str, str] = {}  # filename -> date string when we last rotated

_LOGGER = logging.getLogger(__name__)


def _maybe_rotate() -> None:
    """Roll the log file at midnight (HA local time)."""
    today = datetime.now().strftime("%Y-%m-%d")
    last = _ROTATE_AT.get(str(_LOG_PATH))
    if last == today:
        return
    if last is None:
        _ROTATE_AT[str(_LOG_PATH)] = today
        return
    try:
        old = _LOG_PATH.with_name(f"powmr_hems_debug.{last}.log")
        if _LOG_PATH.exists() and not old.exists():
            _LOG_PATH.rename(old)
    except OSError as exc:
        _LOGGER.debug("Could not rotate HEMS debug log: %s", exc)
    try:
        cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        for p in _LOG_PATH.parent.glob("powmr_hems_debug.*.log"):
            tag = p.name.replace("powmr_hems_debug.", "").replace(".log", "")
            if tag < cutoff:
                p.unlink()
    except OSError:
        pass
    _ROTATE_AT[str(_LOG_PATH)] = today


def _write_line(path: Path, line: str) -> None:
    """Synchronous file write — run from a worker thread."""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\\n")


def log_evaluation(
    *,
    timestamp: datetime,
    inputs: dict[str, Any],
    decision: Any,
    applied: dict[str, Any] | None,
    skip_reason: str | None = None,
) -> None:
    """Append one evaluation to the debug log.

    The file write runs in a daemon thread so we never block HA''s
    event loop, even when the log file is large.
    """
    try:
        _maybe_rotate()
        payload = {
            "ts": timestamp.isoformat(timespec="seconds"),
            "smart_mode": inputs.get("smart_mode"),
            "soc": inputs.get("soc"),
            "pv_w": inputs.get("pv_power"),
            "load_w": inputs.get("load_power"),
            "grid_w": inputs.get("grid_power"),
            "batt_w": inputs.get("battery_power"),
            "grid_v": inputs.get("grid_voltage"),
            "grid_ok": inputs.get("grid_available"),
            "hour": timestamp.hour,
            "current_output": inputs.get("current_output"),
            "current_charger": inputs.get("current_charger"),
            "reserve_soc": inputs.get("reserve_soc"),
            "forecast_today_kwh": inputs.get("forecast_today_kwh"),
            "forecast_tomorrow_kwh": inputs.get("forecast_tomorrow_kwh"),
            "decision": {
                "reason": getattr(decision, "reason", None),
                "output_priority": getattr(decision, "output_priority", None),
                "charger_priority": getattr(decision, "charger_priority", None),
                "buzzer_off": getattr(decision, "buzzer_off", None),
                "skip": getattr(decision, "skip", None),
            },
            "applied": applied,
            "skip_reason": skip_reason,
        }
        line = json.dumps(payload, ensure_ascii=False, default=str)
        # Fire-and-forget thread so the event loop is never blocked.
        threading.Thread(
            target=_write_line, args=(_LOG_PATH, line), daemon=True
        ).start()
    except Exception as exc:
        # Never let logging break the HEMS loop.
        _LOGGER.debug("Failed to write HEMS debug log: %s", exc)


def _read_recent_sync(path: Path, limit: int) -> list[dict[str, Any]]:
    """Synchronous reader — run from a thread pool."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - 65536))
            tail = fh.read().decode("utf-8", errors="replace")
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        out: list[dict[str, Any]] = []
        for ln in reversed(lines[-limit:]):
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out
    except OSError:
        return []


def read_recent(limit: int = 200) -> list[dict[str, Any]]:
    """Return the last `limit` entries (newest first) from the debug log.

    Sync API: the actual file I/O runs in a thread pool so the HA event
    loop isn''t blocked when sensor extra_state_attributes calls us.
    """
    if not _LOG_PATH.exists():
        return []
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_read_recent_sync, _LOG_PATH, limit)
            return fut.result(timeout=1.0)
    except Exception:
        return []


def daily_summary(date_str: str | None = None) -> dict[str, int]:
    """Count decisions, commands sent, and skips for the given day."""
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    out = {"decisions": 0, "commands_sent": 0, "skipped": 0}
    if not _LOG_PATH.exists():
        return out
    try:
        with _LOG_PATH.open("r", encoding="utf-8") as fh:
            for ln in fh:
                if date_str not in ln:
                    continue
                try:
                    row = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                out["decisions"] += 1
                applied = row.get("applied") or {}
                if applied.get("output_priority") or applied.get("charger_priority"):
                    out["commands_sent"] += 1
                else:
                    out["skipped"] += 1
    except OSError:
        pass
    return out
