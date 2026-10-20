"""Bounded diagnostic capture of allow-listed numeric fields.

TEMPORARY, DIAGNOSTIC ONLY. Purpose: obtain simultaneous ground truth for
RWB1 block-mapping research without touching polling or control.

Design constraints (Юра):
  * Only an explicit allow-list of CONCRETE raw field names. Never the
    whole ``deviceAttributeState`` / ``payload`` JSON.
  * Numeric fields only.
  * No credentials, no device SN, no session id, no token, no account id,
    no MAC, no broker address. The writer refuses any key not on the
    allow-list, so a future raw-field rename cannot silently leak a new
    identifier into the file.
  * Bounded: fixed record cap AND byte cap. When either is reached the
    writer stops and records that fact once. No rotation, no deletion.
  * Polling frequency and inverter control are untouched: this is a pure
    observer called after the response has already been parsed.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

# ── ALLOW LIST ────────────────────────────────────────────────────────
# Exact raw field names as produced by api.py. Nothing else is written.
# Source: api.py lines 1152-1209 (`_val(...)` calls) and the returned
# dictionary at 1213-1251.
ALLOWED_FIELDS: frozenset[str] = frozenset({
    # raw SOC (NOT corrected_soc, which is derived)
    "batterySoc",
    "batteryCapacity",
    # battery
    "batteryVoltage",
    "batteryCurrent",
    "batteryPower",
    "batteryChargingCurrent",
    "batteryDischargeCurrent",
    # PV
    "pvInputPower",
    "generationPower",
    "solarPower",
    "pvPower",
    "pvVoltage",
    "solarVoltage",
    "pvInputVoltage",
    # load
    "acOutputActivePower",
    "loadPower",
    "outputPower",
    "acOutputPower",
    "loadPercent",
    "loadPercentage",
    # grid
    "gridPower",
    "acInputPower",
    "gridVoltage",
    "acInputVoltage",
    "gridPowerDirection",
    "feedInPower",
    "gridImportPower",
    "nominalAcVoltage",
    "nominalAcCurrent",
    "ratedActivePower",
    "ratedApparentPower",
    "acOutputRatingApparentPower",
    "outputApparentPower",
    "outputFrequency",
    # temperature
    "ntcMaximumTemperature",
    "radiatorTemperature",
    "invTemperature",
    "temperature",
    # modes / status (kept as strings, they are small and non-identifying)
    "workingMode",
    "deviceMode",
    "outputSourcePriority",
    "chargerSourcePriority",
})

# Fields written with their unit so downstream mapping does not have to
# guess. Units below are the ones the integration itself uses; they are
# NOT verified against vendor documentation.
UNIT_HINTS: dict[str, str] = {
    "batterySoc": "%",
    "batteryCapacity": "%",
    "batteryVoltage": "V",
    "batteryCurrent": "A",
    "batteryPower": "W",
    "batteryChargingCurrent": "A",
    "batteryDischargeCurrent": "A",
    "pvInputPower": "W",
    "generationPower": "W",
    "solarPower": "W",
    "pvPower": "W",
    "pvVoltage": "V",
    "pvInputVoltage": "V",
    "solarVoltage": "V",
    "acOutputActivePower": "W",
    "loadPower": "W",
    "outputPower": "W",
    "acOutputPower": "W",
    "loadPercent": "%",
    "loadPercentage": "%",
    "gridPower": "W",
    "acInputPower": "W",
    "gridVoltage": "V",
    "acInputVoltage": "V",
    "feedInPower": "W",
    "gridImportPower": "W",
    "gridPowerDirection": "dir",
    "nominalAcVoltage": "V",
    "nominalAcCurrent": "A",
    "ratedActivePower": "W",
    "ratedApparentPower": "VA",
    "acOutputRatingApparentPower": "VA",
    "outputApparentPower": "VA",
    "outputFrequency": "Hz",
    "ntcMaximumTemperature": "degC",
    "radiatorTemperature": "degC",
    "invTemperature": "degC",
    "temperature": "degC",
}

MAX_RECORDS = 20000
MAX_BYTES = 8 * 1024 * 1024

_lock = threading.Lock()
_state = {"records": 0, "bytes": 0, "stopped": False, "path": None}


def _diag_path() -> Path:
    return Path("/config/powmr_inverter_diag/field_samples.jsonl")


def reset() -> None:
    """Testing hook: clear counters so each test starts fresh."""
    with _lock:
        _state.update({"records": 0, "bytes": 0, "stopped": False, "path": None})


def status() -> dict:
    with _lock:
        return dict(_state)


def _unwrap(value):
    """Raw cloud fields arrive as ``{"value": X}`` (api.py:_val reads
    ``item.get("value")`` when the item is a dict). Scalars also occur.
    Return the numeric/string payload, or None.
    """
    if isinstance(value, dict):
        return value.get("value")
    return value


def _sanitise(raw_fields: dict) -> dict:
    """Keep only allow-listed, JSON-safe values. Rejects everything else."""
    out: dict = {}
    for key, raw in raw_fields.items():
        if key not in ALLOWED_FIELDS:
            continue
        value = _unwrap(raw)
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            if value != value or value in (float("inf"), float("-inf")):
                continue
            out[key] = round(float(value), 4)
        elif isinstance(value, str) and len(value) <= 32:
            # Only the mode/status strings are allowed through; they are
            # short codes, not identifiers.
            out[key] = value
    return out


def record(raw_fields: dict, received_at: datetime | None = None) -> bool:
    """Append one bounded diagnostic record. Returns True if written."""
    if not raw_fields:
        return False
    with _lock:
        if _state["stopped"]:
            return False
        path = _state["path"] or str(_diag_path())
        _state["path"] = path
    fields = _sanitise(raw_fields)
    if not fields:
        return False
    rec = {
        "t": (received_at or datetime.now(timezone.utc)).isoformat(),
        "f": fields,
    }
    line = json.dumps(rec, separators=(",", ":"), sort_keys=True) + "\n"
    blob = line.encode("utf-8")
    with _lock:
        if _state["records"] >= MAX_RECORDS:
            _state["stopped"] = True
            return False
        if _state["bytes"] + len(blob) > MAX_BYTES:
            _state["stopped"] = True
            return False
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(p, "ab") as fh:
                fh.write(blob)
        except OSError:
            return False
        _state["records"] += 1
        _state["bytes"] += len(blob)
        return True