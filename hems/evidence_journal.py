"""Persisted, privacy-filtered incident evidence for each HA entry.

All file work is performed by the bounded writer thread. The synchronous
``record_event`` boundary only validates, serializes, and enqueues allowlisted
fields, so it is safe to call from Home Assistant's event loop.
"""
from __future__ import annotations

import json
import logging
import math
import os
import queue
import re
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..const import EASUN_SENSOR_META

_LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = 1
SESSION_ID = str(uuid.uuid4())
MAX_QUEUE = 1024
NORMAL_FSYNC_SECONDS = 5.0
MAX_FILE_SIZE_BYTES = 8 * 1024 * 1024
RETENTION_DAYS = 30

_EVENT_TYPES = frozenset({"cycle", "hems_cycle", "command_intent", "command_request", "command_result", "command_readback", "export"})
_OUTCOMES = frozenset({
    "fresh_success", "empty_response", "malformed_response", "timeout",
    "reauth_required", "auth_failure", "offline", "cached_fallback",
    "fallback_no_cache", "poll_error", "decision", "held", "skipped",
    "request_sent", "api_accepted", "api_rejected", "api_error", "observed_match",
    "observed_mismatch", "unconfirmed", "exported",
})
_STATE_KEYS = frozenset({
    "raw_soc", "corrected_soc", "battery_voltage_v", "battery_current_a",
    "battery_power_w", "pv_power_w", "load_power_w", "grid_power_w",
    "grid_voltage_v", "source", "availability", "age_since_last_success_s",
    "cached", "fallback", "stale", "current_output_mode", "current_charger_mode",
    "target_output_mode", "target_charger_mode", "current_mode", "target_mode",
    "effective_hems_auto", "predictive_mode", "reserve_soc", "decision_reason",
    "active_holds", "alarms", "alarms_source", "alarms_fetched_at",
    "thresholds", "thresholds_source", "thresholds_fetched_at",
})
_NUMERIC_STATE_KEYS = frozenset({
    "raw_soc", "corrected_soc", "battery_voltage_v", "battery_current_a",
    "battery_power_w", "pv_power_w", "load_power_w", "grid_power_w",
    "grid_voltage_v", "age_since_last_success_s", "reserve_soc",
})
_BOOL_STATE_KEYS = frozenset({"cached", "fallback", "stale", "effective_hems_auto"})
_MODES = frozenset({"adaptive", "arbitrage", "storm", "off", "shadow", "assist", "unknown"})
_SETTING_CODES = frozenset({"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"})
_SOURCES = frozenset({"cloud_primary", "cloud_fallback", "cache", "unavailable", "device"})
_AVAILABILITY = frozenset({"online", "offline", "unknown"})
_HOLDS = frozenset({
    "manual_override", "circuit_breaker", "keepalive_in_progress", "forced_charge",
    "soc_unknown", "hems_disabled", "hems_auto_off", "inverter_offline",
    "reserve_soc_protection", "invalid_telemetry",
})
_ERROR_CLASSES = frozenset({
    "OSError", "PermissionError", "FileNotFoundError", "FileExistsError",
    "QueueFull",
    "TimeoutError", "JSONDecodeError", "ClientError", "TokenExpiredError",
    "InverterOfflineError", "InverterAuthError", "ValueError", "TypeError",
    "RuntimeError", "unknown",
})
_COMMAND_KEYS = frozenset({
    "outputSourcePrioritySetting", "chargerSourcePrioritySetting", "buzzerAlarmSetting",
    "setMaxChargingCurrent", "setUtilityMaxChargingCurrent", "acChargingSwitch",
    "batteryPowerLimitingSetting", "batteryChargeLimit", "gridConnectedPowers",
})
_ALARM_KEYS = frozenset(
    key for key, metadata in EASUN_SENSOR_META.items()
    if metadata.get("kind") == "binary" and metadata.get("device_class") == "problem"
)
_ALARM_SOURCES = frozenset({"cloud_primary", "cloud_fallback"})
# Explicitly mirror voltage/charge-related number entity config keys. Do not
# persist the vendor's complete config response or labels/display strings.
_THRESHOLD_KEYS = frozenset({
    "setBatteryCVChargeVoltage", "setBatteryFloatChargingVoltage",
    "LowBatteryCutOffVoltageSetting", "setBatteryEqualizationVoltage",
    "setBatteryEqualizationTime", "batteryEqualizationIntervalSetting",
    "comebackUtilityModeVolSBUPriority", "comebackBatteryModeVolSBUPriority",
})

_ENTRY_PATHS: dict[str, Path] = {}
_ENTRY_VERSIONS: dict[str, str] = {}
_ENTRY_SEQUENCES: dict[tuple[str, str], int] = {}
_PENDING_COMMANDS: dict[tuple[str, str, str], dict[str, Any]] = {}
_STATE_LOCK = threading.RLock()
_WRITER: "_JournalWriter | None" = None
_WRITER_LOCK = threading.Lock()
_COMMAND_ID: ContextVar[str | None] = ContextVar("powmr_incident_command_id", default=None)


@contextmanager
def command_scope(command_id: str):
    """Propagate a coordinator decision ID through API await boundaries."""
    token = _COMMAND_ID.set(command_id)
    try:
        yield
    finally:
        _COMMAND_ID.reset(token)


def current_command_id() -> str | None:
    """Return the command ID scoped by the current async task, if any."""
    return _COMMAND_ID.get()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    return None


def _safe_integration_version(value: Any) -> str | None:
    """Preserve only bounded, plain-text integration version identifiers."""
    if not isinstance(value, str) or len(value) > 64:
        return None
    return value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}", value) else None


def _finite(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return value


def _safe_alarm_flags(value: Any) -> dict[str, bool]:
    """Keep only recognized problem flags whose raw values are explicit."""
    if not isinstance(value, dict):
        return {}
    safe: dict[str, bool] = {}
    for key in _ALARM_KEYS:
        if key not in value:
            continue
        item = value[key]
        raw_value = item.get("value") if isinstance(item, dict) else item
        if isinstance(raw_value, bool):
            safe[key] = raw_value
        elif isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            if raw_value == 0 or raw_value == 1:
                safe[key] = bool(raw_value)
        elif isinstance(raw_value, str):
            normalized = raw_value.strip().lower()
            if normalized in {"1", "true", "on"}:
                safe[key] = True
            elif normalized in {"0", "false", "off"}:
                safe[key] = False
    return safe


def _safe_threshold_value(value: Any) -> float | None:
    """Parse a finite numeric device setting, excluding labels and booleans."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if len(value) > 32 or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
            return None
        try:
            parsed = float(value)
        except (ValueError, OverflowError):
            return None
        return parsed if math.isfinite(parsed) else None
    try:
        parsed = _finite(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return float(parsed) if parsed is not None else None


def _safe_thresholds(value: Any) -> dict[str, float]:
    """Keep numeric values for a fixed set of supported device thresholds."""
    if not isinstance(value, dict):
        return {}
    safe: dict[str, float] = {}
    for key in _THRESHOLD_KEYS:
        if key not in value:
            continue
        item = value[key]
        raw_value = item.get("value") if isinstance(item, dict) else item
        parsed = _safe_threshold_value(raw_value)
        if parsed is not None:
            safe[key] = parsed
    return safe


def _safe_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    safe: dict[str, Any] = {}
    capture_keys = {
        "alarms", "alarms_source", "alarms_fetched_at",
        "thresholds", "thresholds_source", "thresholds_fetched_at",
    }
    for key, value in state.items():
        if key not in _STATE_KEYS:
            continue
        if key in capture_keys:
            continue
        if key in _NUMERIC_STATE_KEYS:
            parsed = _finite(value)
            if parsed is not None:
                safe[key] = parsed
        elif key in _BOOL_STATE_KEYS:
            if isinstance(value, bool):
                safe[key] = value
        elif key in {"current_output_mode", "current_charger_mode", "target_output_mode", "target_charger_mode"}:
            if value is None:
                safe[key] = None
            elif str(value) in _SETTING_CODES:
                safe[key] = str(value)
        elif key in {"current_mode", "target_mode", "predictive_mode"}:
            if isinstance(value, str) and value in _MODES:
                safe[key] = value
        elif key == "source":
            if isinstance(value, str) and value in _SOURCES:
                safe[key] = value
        elif key == "availability":
            if isinstance(value, str) and value in _AVAILABILITY:
                safe[key] = value
        elif key == "decision_reason":
            if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_:-]{1,80}", value):
                safe[key] = value
        elif key == "active_holds" and isinstance(value, (list, tuple)):
            safe[key] = sorted({item for item in value if isinstance(item, str) and item in _HOLDS})
    alarms = _safe_alarm_flags(state.get("alarms"))
    alarms_source = state.get("alarms_source")
    alarms_fetched_at = _iso(state.get("alarms_fetched_at"))
    if (
        alarms
        and isinstance(alarms_source, str)
        and alarms_source in _ALARM_SOURCES
        and alarms_fetched_at is not None
    ):
        safe["alarms"] = alarms
        safe["alarms_source"] = alarms_source
        safe["alarms_fetched_at"] = alarms_fetched_at

    thresholds = _safe_thresholds(state.get("thresholds"))
    thresholds_source = state.get("thresholds_source")
    thresholds_fetched_at = _iso(state.get("thresholds_fetched_at"))
    if (
        thresholds
        and isinstance(thresholds_source, str)
        and thresholds_source == "device_config"
        and thresholds_fetched_at is not None
    ):
        safe["thresholds"] = thresholds
        safe["thresholds_source"] = "device_config"
        safe["thresholds_fetched_at"] = thresholds_fetched_at
    return safe


def _safe_error(error: Any) -> dict[str, Any] | None:
    if not isinstance(error, dict):
        return None
    raw_class = error.get("class")
    safe_class = raw_class if isinstance(raw_class, str) and raw_class in _ERROR_CLASSES else "unknown"
    code = error.get("code")
    safe_code = _finite(code)
    return {"class": safe_class, "code": safe_code}


def _safe_command_value(value: Any) -> int | float | str | None:
    if value is None:
        return None
    if isinstance(value, str):
        if value in _SETTING_CODES:
            return value
        if len(value) <= 32 and re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
            try:
                return value if math.isfinite(float(value)) else None
            except ValueError:
                return None
        return None
    return _finite(value)


def _safe_command(command: Any) -> dict[str, Any] | None:
    if not isinstance(command, dict):
        return None
    safe: dict[str, Any] = {}
    command_id = command.get("command_id")
    if isinstance(command_id, str) and re.fullmatch(r"[0-9a-fA-F-]{36}", command_id):
        safe["command_id"] = command_id
    key = command.get("key")
    if isinstance(key, str) and key in _COMMAND_KEYS:
        safe["key"] = key
    value = command.get("value")
    if value is None:
        safe["value"] = None
    else:
        safe_value = _safe_command_value(value)
        if safe_value is not None:
            safe["value"] = safe_value
    status = command.get("status")
    if isinstance(status, str) and status in {"intent", "request", "api_accepted", "api_rejected", "api_error", "observed_match", "observed_mismatch", "unconfirmed"}:
        safe["status"] = status
    observed = command.get("observed_value")
    if observed is None:
        safe["observed_value"] = None
    else:
        safe_observed = _safe_command_value(observed)
        if safe_observed is not None:
            safe["observed_value"] = safe_observed
    return safe or None


def configure_entry(entry_id: str, path: Path | str, integration_version: str) -> None:
    """Bind one config entry to its permanent JSONL journal location."""
    with _STATE_LOCK:
        _ENTRY_PATHS[entry_id] = Path(path) / "events.jsonl"
        _ENTRY_VERSIONS[entry_id] = integration_version


def unbind_entry(entry_id: str) -> None:
    """Forget one unloaded HA entry after its journal has been drained."""
    with _STATE_LOCK:
        _ENTRY_PATHS.pop(entry_id, None)
        _ENTRY_VERSIONS.pop(entry_id, None)
        for key in [key for key in _ENTRY_SEQUENCES if key[0] == entry_id]:
            _ENTRY_SEQUENCES.pop(key, None)
        for key in [key for key in _PENDING_COMMANDS if key[0] == entry_id]:
            _PENDING_COMMANDS.pop(key, None)


def _get_writer() -> "_JournalWriter":
    global _WRITER
    if _WRITER is None:
        with _WRITER_LOCK:
            if _WRITER is None:
                _WRITER = _JournalWriter()
    return _WRITER


def record_event(
    entry_id: str,
    event_type: str,
    *,
    outcome: str | None = None,
    cloud_response_received_at: datetime | str | None = None,
    device_measurement_at: datetime | str | None = None,
    ha_state_published_at: datetime | str | None = None,
    state: dict[str, Any] | None = None,
    command: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    critical: bool = False,
) -> dict[str, Any] | None:
    """Queue one sanitized event without doing filesystem I/O."""
    try:
        if event_type not in _EVENT_TYPES:
            event_type = "cycle"
        if outcome not in _OUTCOMES:
            outcome = None
        with _STATE_LOCK:
            path = _ENTRY_PATHS.get(entry_id)
            version = _ENTRY_VERSIONS.get(entry_id)
            if path is None or version is None:
                return None
            sequence_key = (entry_id, SESSION_ID)
            sequence = _ENTRY_SEQUENCES.get(sequence_key, 0) + 1
            _ENTRY_SEQUENCES[sequence_key] = sequence
        event = {
            "schema_version": SCHEMA_VERSION,
            "integration_version": version,
            "entry_id": entry_id,
            "session_id": SESSION_ID,
            "sequence": sequence,
            "event_timestamp": _utc_now().isoformat(timespec="milliseconds"),
            "event_type": event_type,
            "outcome": outcome,
            "cloud_response_received_at": _iso(cloud_response_received_at),
            "device_measurement_at": _iso(device_measurement_at),
            "ha_state_published_at": _iso(ha_state_published_at),
            "state": _safe_state(state),
            "command": _safe_command(command),
            "error": _safe_error(error),
        }
        writer = _get_writer()
        if not writer.enqueue(entry_id, path, event, critical=critical):
            return event
        command_data = event.get("command")
        if isinstance(command_data, dict):
            command_id = command_data.get("command_id")
            key = command_data.get("key")
            status = command_data.get("status")
            if isinstance(command_id, str) and isinstance(key, str):
                pending_key = (entry_id, command_id, key)
                with _STATE_LOCK:
                    if status == "api_accepted" and event_type == "command_result":
                        _PENDING_COMMANDS[pending_key] = {
                            "command": command_data,
                            "accepted_at": event.get("cloud_response_received_at"),
                        }
                    elif status in {"api_rejected", "api_error", "observed_match", "observed_mismatch", "unconfirmed"}:
                        _PENDING_COMMANDS.pop(pending_key, None)
        return event
    except Exception as exc:  # telemetry must not affect inverter control
        _LOGGER.error("incident journal enqueue failure: exception_class=%s", type(exc).__name__)
        return None


def _latest_event_timestamp(path: Path) -> datetime | None:
    """Return the newest valid event timestamp in one bounded JSONL file."""
    latest: datetime | None = None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            stamp = _iso(row.get("event_timestamp"))
            if stamp is None:
                continue
            event_at = datetime.fromisoformat(stamp)
            if latest is None or event_at > latest:
                latest = event_at
    return latest


class _JournalWriter:
    """Single bounded worker for all entry journals."""

    def __init__(self, max_queue: int = MAX_QUEUE) -> None:
        self._queue: queue.PriorityQueue[tuple[int, int, str, Path, dict[str, Any] | None, threading.Event | None, bool]] = queue.PriorityQueue(maxsize=max_queue)
        self._sequence = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._stats_lock = threading.Lock()
        self._stats: dict[str, dict[str, Any]] = {}
        self._pending_paths: dict[Path, str] = {}
        self._pending_record_counts: dict[Path, int] = {}
        self._last_fsync = time.monotonic()
        self._thread = threading.Thread(target=self._serve, name="powmr-incident-journal", daemon=True)
        self._thread.start()

    def _entry_stats(self, entry_id: str) -> dict[str, Any]:
        return self._stats.setdefault(entry_id, {
            "queue_full": 0, "write_errors": 0, "fsync_errors": 0,
            "records_written": 0, "fsyncs": 0, "last_error": None,
        })

    def enqueue(self, entry_id: str, path: Path, event: dict[str, Any], *, critical: bool) -> bool:
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        try:
            # Assign the queue sequence and publish the item atomically with
            # respect to drain barriers; otherwise a barrier can overtake an
            # enqueue that has a sequence but has not reached the queue yet.
            with self._lock:
                self._sequence += 1
                seq = self._sequence
                self._queue.put_nowait((0, seq, entry_id, path, event, None, critical))
            return True
        except queue.Full:
            with self._stats_lock:
                stats = self._entry_stats(entry_id)
                stats["queue_full"] += 1
                stats["last_error"] = {"class": "QueueFull", "code": None}
            _LOGGER.error("incident journal queue full: entry_id=%s", entry_id)
            return False

    def status(self, entry_id: str) -> dict[str, Any]:
        with self._stats_lock:
            stats = dict(self._entry_stats(entry_id))
        stats["queue_depth"] = self._queue.qsize()
        return stats

    def drain(self, timeout: float | None = None) -> bool:
        started = time.monotonic()
        deadline = None if timeout is None else started + timeout
        with self._stats_lock:
            before_errors = sum(v["queue_full"] + v["write_errors"] + v["fsync_errors"] for v in self._stats.values())
        event = threading.Event()
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                return False
            try:
                # Same priority as journal events; its sequence number makes
                # it a true FIFO barrier instead of letting a control item
                # jump ahead of already-enqueued records.
                with self._lock:
                    self._sequence += 1
                    seq = self._sequence
                    self._queue.put_nowait((0, seq, "", Path(), None, event, False))
                break
            except queue.Full:
                time.sleep(0.005)
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        if not event.wait(remaining):
            return False
        with self._stats_lock:
            after_errors = sum(v["queue_full"] + v["write_errors"] + v["fsync_errors"] for v in self._stats.values())
        return after_errors == 0 and after_errors == before_errors

    def _serve(self) -> None:
        while not self._stop.is_set() or self._queue.unfinished_tasks:
            time_until_fsync = max(0.0, NORMAL_FSYNC_SECONDS - (time.monotonic() - self._last_fsync))
            try:
                item = self._queue.get(timeout=min(0.5, time_until_fsync))
            except queue.Empty:
                if time.monotonic() - self._last_fsync >= NORMAL_FSYNC_SECONDS:
                    self._sync_pending()
                continue
            _, _, entry_id, path, event, barrier, critical = item
            try:
                if barrier is not None:
                    self._sync_pending()
                    barrier.set()
                elif event is not None:
                    self._write(path, event, critical=critical)
            finally:
                self._queue.task_done()
            if time.monotonic() - self._last_fsync >= NORMAL_FSYNC_SECONDS:
                self._sync_pending()

    def _write(self, path: Path, event: dict[str, Any], *, critical: bool) -> None:
        entry_id = str(event["entry_id"])
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
            self._rotate_and_prune(path, len(line), int(event["sequence"]))
            with path.open("ab", buffering=0) as handle:
                written = handle.write(line)
                if written != len(line):
                    raise OSError("short journal write")
            self._pending_paths[path] = entry_id
            self._pending_record_counts[path] = self._pending_record_counts.get(path, 0) + 1
            if critical:
                self._sync_path(path, entry_id)
        except OSError as exc:
            self._set_error(entry_id, "write_errors", exc)

    def _rotate_and_prune(self, path: Path, incoming_size: int, sequence: int) -> None:
        now = _utc_now()
        cutoff = now - timedelta(days=RETENTION_DAYS)
        if path.exists():
            rotate = path.stat().st_size + incoming_size > MAX_FILE_SIZE_BYTES
            if not rotate:
                latest_event_at = _latest_event_timestamp(path)
                rotate = latest_event_at is not None and (
                    latest_event_at < cutoff or latest_event_at.date() < now.date()
                )
            if rotate:
                stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
                archive = path.with_name(f"events.{stamp}.{sequence}.jsonl")
                path.replace(archive)
        for archive in path.parent.glob("events.*.jsonl"):
            try:
                latest_event_at = _latest_event_timestamp(archive)
                if archive.stat().st_mtime < cutoff.timestamp() or (
                    latest_event_at is not None and latest_event_at < cutoff
                ):
                    archive.unlink()
            except OSError:
                raise

    def _sync_path(self, path: Path, entry_id: str) -> bool:
        try:
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        except OSError as exc:
            self._set_error(entry_id, "fsync_errors", exc)
            return False
        record_count = self._pending_record_counts.pop(path, 0)
        self._pending_paths.pop(path, None)
        with self._stats_lock:
            stats = self._entry_stats(entry_id)
            stats["fsyncs"] += 1
            stats["records_written"] += record_count
        return True

    def _sync_pending(self) -> None:
        for path, entry_id in list(self._pending_paths.items()):
            self._sync_path(path, entry_id)
        self._last_fsync = time.monotonic()

    def _set_error(self, entry_id: str, counter: str, exc: OSError) -> None:
        code = getattr(exc, "errno", None)
        safe_code = code if isinstance(code, int) and code in {1, 2, 5, 13, 28, 30} else None
        error = {"class": type(exc).__name__ if type(exc).__name__ in _ERROR_CLASSES else "OSError", "code": safe_code}
        with self._stats_lock:
            stats = self._entry_stats(entry_id)
            stats[counter] += 1
            stats["last_error"] = error
        _LOGGER.error("incident journal disk failure: entry_id=%s error_class=%s error_code=%s", entry_id, error["class"], error["code"])


def drain(timeout: float | None = 5.0) -> bool:
    """Wait for pending records and fsyncs; errors make the result false."""
    writer = _WRITER
    return True if writer is None else writer.drain(timeout)


def status(entry_id: str) -> dict[str, Any]:
    """Return sanitized counters suitable for a diagnostic HA attribute."""
    writer = _WRITER
    if writer is None:
        return {"queue_full": 0, "write_errors": 0, "fsync_errors": 0, "records_written": 0, "fsyncs": 0, "last_error": None, "queue_depth": 0}
    return writer.status(entry_id)


def read_records(entry_id: str) -> list[dict[str, Any]]:
    """Read complete valid JSONL events, ignoring a partial/corrupt final line."""
    with _STATE_LOCK:
        path = _ENTRY_PATHS.get(entry_id)
    if path is None:
        return []
    try:
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                try:
                    item = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(item, dict) and item.get("entry_id") == entry_id:
                    rows.append(item)
        return rows
    except OSError:
        return []


def read_jsonl(path: Path | str) -> list[dict[str, Any]]:
    """Read complete JSON objects from one active or rotated journal file."""
    try:
        rows: list[dict[str, Any]] = []
        with Path(path).open("r", encoding="utf-8") as handle:
            for raw in handle:
                try:
                    item = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(item, dict):
                    rows.append(item)
        return rows
    except OSError:
        return []


MAX_EXPORT_WINDOW_SECONDS = 7 * 86400
MAX_EXPORT_RECORDS = 10000
COMMAND_READBACK_TTL_SECONDS = 15 * 60


def _same_setting(expected: Any, observed: Any) -> bool:
    """Compare sanitized setting values without bool/numeric coercion bugs."""
    if isinstance(expected, bool) or isinstance(observed, bool):
        return expected is observed
    if isinstance(expected, (int, float)) and isinstance(observed, (int, float)):
        left = _finite(expected)
        right = _finite(observed)
        return left is not None and right is not None and math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-6)
    if isinstance(expected, str) and isinstance(observed, str):
        if expected in _SETTING_CODES and observed in _SETTING_CODES:
            return expected == observed
        return expected == observed
    return False


def record_readbacks(
    entry_id: str,
    observations: dict[str, Any],
    observed_at: datetime | str | None,
    *,
    cloud_response_received_at: datetime | str | None = None,
) -> int:
    """Correlate fresh, post-ACK device observations to accepted commands.

    Observations at or before the cloud ACK are never treated as readback: a
    poll which began before the write cannot prove that the write took effect.
    """
    observed_stamp = _iso(observed_at)
    observed_dt = datetime.fromisoformat(observed_stamp) if observed_stamp is not None else None
    if not isinstance(observations, dict):
        observations = {}
    now = _utc_now()
    completions: list[tuple[str, dict[str, Any], str, Any, datetime | None]] = []
    with _STATE_LOCK:
        for pending_key, pending in list(_PENDING_COMMANDS.items()):
            pending_entry, command_id, key = pending_key
            if pending_entry != entry_id:
                continue
            accepted_value = pending.get("accepted_at")
            accepted_stamp = _iso(accepted_value)
            if accepted_stamp is None:
                _PENDING_COMMANDS.pop(pending_key, None)
                continue
            accepted_dt = datetime.fromisoformat(accepted_stamp)
            command = pending["command"]
            if (now - accepted_dt).total_seconds() > COMMAND_READBACK_TTL_SECONDS:
                completions.append((command_id, command, "unconfirmed", None, None))
                _PENDING_COMMANDS.pop(pending_key, None)
                continue
            if observed_dt is None or observed_dt <= accepted_dt:
                continue
            if key not in observations:
                continue
            observed_value = observations[key]
            outcome = "observed_match" if _same_setting(command.get("value"), observed_value) else "observed_mismatch"
            completions.append((command_id, command, outcome, observed_value, observed_dt))
            _PENDING_COMMANDS.pop(pending_key, None)

    for command_id, command, outcome, observed_value, measurement_at in completions:
        record_event(
            entry_id,
            "command_readback",
            outcome=outcome,
            cloud_response_received_at=cloud_response_received_at,
            device_measurement_at=measurement_at,
            command={
                "command_id": command_id,
                "key": command.get("key"),
                "value": command.get("value"),
                "observed_value": observed_value,
                "status": outcome,
            },
            critical=False,
        )
    return len(completions)


def _clean_stored_event(row: dict[str, Any], entry_id: str) -> dict[str, Any] | None:
    """Re-apply the journal schema allowlist to data read from disk."""
    event_type = row.get("event_type")
    sequence = row.get("sequence")
    session_id = row.get("session_id")
    if (
        row.get("entry_id") != entry_id
        or not isinstance(event_type, str)
        or event_type not in _EVENT_TYPES
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 1
        or not isinstance(session_id, str)
        or not re.fullmatch(r"[0-9a-fA-F-]{36}", session_id)
    ):
        return None
    event_at = _iso(row.get("event_timestamp"))
    if event_at is None:
        return None
    raw_outcome = row.get("outcome")
    outcome = raw_outcome if isinstance(raw_outcome, str) and raw_outcome in _OUTCOMES else None
    integration_version = _safe_integration_version(row.get("integration_version"))
    return {
        "schema_version": SCHEMA_VERSION,
        "integration_version": integration_version,
        "entry_id": entry_id,
        "session_id": session_id,
        "sequence": sequence,
        "event_timestamp": event_at,
        "event_type": event_type,
        "outcome": outcome,
        "cloud_response_received_at": _iso(row.get("cloud_response_received_at")),
        "device_measurement_at": _iso(row.get("device_measurement_at")),
        "ha_state_published_at": _iso(row.get("ha_state_published_at")),
        "state": _safe_state(row.get("state")),
        "command": _safe_command(row.get("command")),
        "error": _safe_error(row.get("error")),
    }


def export_bundle_sync(
    config_dir: Path | str,
    entry_id: str,
    start: datetime,
    end: datetime,
) -> tuple[Path, int]:
    """Write a time-bounded sanitized incident JSONL bundle under config."""
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise ValueError("start and end must be datetimes")
    if start.tzinfo is None or start.utcoffset() is None or end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("start and end must include a timezone")
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    duration = (end_utc - start_utc).total_seconds()
    if duration <= 0 or duration > MAX_EXPORT_WINDOW_SECONDS:
        raise ValueError("export time range must be positive and no longer than seven days")
    with _STATE_LOCK:
        active_path = _ENTRY_PATHS.get(entry_id)
    if active_path is None:
        raise ValueError("entry journal is not configured")
    config_root = Path(config_dir).resolve()
    journal_root = (config_root / "powmr_inverter_incidents").resolve()
    if not journal_root.is_relative_to(config_root):
        raise ValueError("evidence directory resolves outside the HA config directory")
    entry_dir = active_path.parent.resolve()
    if not entry_dir.is_relative_to(journal_root):
        raise ValueError("entry journal is outside the configured evidence directory")

    records: list[dict[str, Any]] = []
    for source in sorted(entry_dir.glob("events*.jsonl")):
        for row in read_jsonl(source):
            clean = _clean_stored_event(row, entry_id)
            if clean is None:
                continue
            event_at = datetime.fromisoformat(clean["event_timestamp"])
            if start_utc <= event_at <= end_utc:
                records.append(clean)
                if len(records) > MAX_EXPORT_RECORDS:
                    raise ValueError("export record limit exceeded")
    records.sort(key=lambda row: (row["event_timestamp"], row["session_id"], row["sequence"]))

    export_dir = journal_root / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    export_dir = export_dir.resolve()
    if not export_dir.is_relative_to(journal_root):
        raise ValueError("export directory resolves outside the evidence directory")
    safe_entry = re.sub(r"[^A-Za-z0-9_-]", "_", entry_id)[:64] or "entry"
    stamp = _utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    target = export_dir / f"incident_{safe_entry}_{stamp}.jsonl"
    temporary = export_dir / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            for record in records:
                handle.write((json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        try:
            directory_fd = os.open(export_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target, len(records)
