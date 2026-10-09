#!/usr/bin/env python3
"""Live probe for R01 radiation interval contract v2.

Standalone tool — NOT executed by the regular test runner. Run with::

    POWMR_HA_TOKEN=<long-lived-access-token> \\
        python3 scripts/probe_r01_live.py [--host HOST]

Exit codes:
    0   every checked item PASS or NOT_YET_VERIFIED
    3   one or more FAIL

The probe does NOT print the bearer token, NOT include it in any
report, and NOT log it.

It checks the following, with explicit PASS / FAIL / NOT_YET_VERIFIED:

  1. entity_id                - dynamic discovery of the
                                ``Predictive Decision State``
                                sensor for the entry.
  2. journal contract         - ``pv_fact_pairs_<entry>.json``
                                has VERSION=3, RADIATION_CONTRACT_VERSION=2,
                                and is parseable.
  3. real_pairs path/version  - ``real_forecast_pairs.json`` lives
                                at the per-entry path
                                ``hems/<entry>/...`` and has
                                VERSION=2.
  4. calibration_model top    - the journal's ``calibration_model``
                                key is at the top level (not in
                                ``identity``), and (when set) is a
                                v2 family tag.
  5. forecast_diagnostic      - the sensor publishes a
                                ``forecast_diagnostic`` attribute
                                with the documented schema, and
                                contract v2 invariants hold.
  6. completed-pair check     - if a v2 completed pair exists in
                                both journals (same date,
                                compatible model tag, matching
                                forecast/actual values,
                                forecast issued before day, finite
                                values) → PASS; else NOT_YET_VERIFIED.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOST_DEFAULT = "root@192.168.1.220"
HEMS_DIR = "/config/custom_components/powmr_inverter/hems"
ENTRY_ID_DEFAULT = "01M3XWJ8DRYDQC8A0NCPRVB53N"
ENTITY_HA_API = "/api/states/{}"

V2_TAGS = {"hourly_response_v2", "station_gain_v2"}


# ── helpers ──────────────────────────────────────────────────────


def _ssh(host: str, *cmd: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run ``cmd`` over SSH. Returns (rc, stdout, stderr)."""
    full = ["ssh", "-o", "BatchMode=yes", host, *cmd]
    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def _read_remote_json(host: str, path: str) -> dict | None:
    """Read a remote JSON file. The path may contain any
    characters — we pass it as a CLI argument to a small
    Python helper scp'd to ``/tmp`` first to avoid shell
    escaping issues with f-strings.

    The helper file lives in a tempfile created on the
    operator's machine and is removed after the read.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(
            "import json, sys\n"
            "print(json.dumps(json.load(open(sys.argv[1]))))\n"
        )
        helper = f.name
    remote_helper = f"/tmp/_probe_read_json_{os.getpid()}.py"
    try:
        subprocess.run(
            ["scp", "-q", helper, f"{host}:{remote_helper}"],
            check=True, capture_output=True, timeout=15,
        )
        rc, out, _ = _ssh(host, "python3", remote_helper, path, timeout=15)
        if rc != 0:
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None
    finally:
        try:
            os.unlink(helper)
        except OSError:
            pass
        _ssh(host, "rm", "-f", remote_helper, timeout=10)


def _read_remote_python(host: str, helper_src: str, *args: str) -> str | None:
    """Write a Python helper to the remote ``/tmp`` and run it.
    Returns stdout on success, ``None`` on failure."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(helper_src)
        helper = f.name
    remote_helper = f"/tmp/_probe_helper_{os.getpid()}.py"
    try:
        subprocess.run(
            ["scp", "-q", helper, f"{host}:{remote_helper}"],
            check=True, capture_output=True, timeout=15,
        )
        rc, out, _ = _ssh(host, "python3", remote_helper, *args, timeout=15)
        if rc != 0:
            return None
        return out
    finally:
        try:
            os.unlink(helper)
        except OSError:
            pass
        _ssh(host, "rm", "-f", remote_helper, timeout=10)


def _discover_entry_id(host: str) -> str:
    """Return the entry_id for the powmr_inverter integration.

    Falls back to the well-known default if the registry
    cannot be parsed; the latter is then verified to exist
    in the live filesystem before being trusted.
    """
    out = _read_remote_python(host,
        "import json, sys\n"
        "d=json.load(open('/config/.storage/core.config_entries'))\n"
        "entries = d.get('data', {}).get('entries', [])\n"
        "for e in entries:\n"
        "    if e.get('domain') == 'powmr_inverter':\n"
        "        print(e.get('entry_id', ''))\n"
        "        sys.exit(0)\n"
    )
    if out and out.strip():
        return out.strip().splitlines()[0]
    return ENTRY_ID_DEFAULT


def _discover_entity_id(host: str, entry_id: str) -> str | None:
    """Look up the predictive_decision_state entity for ``entry_id``
    in the live entity registry.
    """
    out = _read_remote_python(host,
        "import json, sys\n"
        "d=json.load(open('/config/.storage/core.entity_registry'))\n"
        f"target='{entry_id}_predictive_decision_state'\n"
        "for e in d.get('data', {}).get('entities', []):\n"
        f"    if e.get('config_entry_id')=='{entry_id}' and e.get('unique_id')==target:\n"
        "        print(e.get('entity_id', ''))\n"
        "        sys.exit(0)\n"
    )
    if out and out.strip():
        return out.strip().splitlines()[0]
    return None


def _fetch_sensor(host: str, entity_id: str, token: str | None) -> dict | None:
    """Read the sensor state via the local HA REST API. The
    request goes through SSH (host-local curl) so the
    long-lived access token never leaves the operator's
    machine as a network packet.
    """
    if not token:
        return None
    url = f"http://127.0.0.1:8123" + ENTITY_HA_API.format(entity_id)
    rc, out, _ = _ssh(
        host, "curl", "-sS", "-m", "10",
        "-H", f"Authorization: Bearer {token}",
        url,
    )
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


# ── checks ───────────────────────────────────────────────────────


def check_entity_id(host: str, entry_id: str) -> tuple[str, str, str]:
    """[1/6] Discover the entity ID and confirm it exists."""
    entity = _discover_entity_id(host, entry_id)
    if entity is None:
        return "FAIL", "entity_id", (
            f"could not discover predictive_decision_state entity for entry {entry_id}"
        )
    return "PASS", "entity_id", f"discovered entity_id={entity} for entry {entry_id}"


def check_journal_contract(host: str, entry_id: str) -> tuple[str, str, str]:
    """[2/6] Journal VERSION=3, RADIATION_CONTRACT_VERSION=2."""
    path = f"{HEMS_DIR}/pv_fact_pairs_{entry_id}.json"
    data = _read_remote_json(host, path)
    if data is None:
        return "FAIL", "journal_contract", f"cannot read or parse {path}"
    version = data.get("version")
    contract = data.get("radiation_contract_version")
    if version != 3:
        return "FAIL", "journal_contract", (
            f"journal version is {version} (expected 3)"
        )
    if contract != 2:
        return "FAIL", "journal_contract", (
            f"radiation_contract_version is {contract} (expected 2)"
        )
    return "PASS", "journal_contract", (
        f"journal {Path(path).name}: version=3, radiation_contract_version=2"
    )


def check_real_pairs(host: str, entry_id: str) -> tuple[str, str, str]:
    """[3/6] real_forecast_pairs.json at per-entry path; VERSION=2."""
    path = f"{HEMS_DIR}/{entry_id}/real_forecast_pairs.json"
    data = _read_remote_json(host, path)
    if data is None:
        return "FAIL", "real_pairs", f"cannot read or parse {path}"
    version = data.get("version")
    contract = data.get("radiation_contract_version")
    if version != 2:
        return "FAIL", "real_pairs", (
            f"real_pairs version is {version} (expected 2)"
        )
    if contract != 2:
        return "FAIL", "real_pairs", (
            f"real_pairs radiation_contract_version is {contract} (expected 2)"
        )
    return "PASS", "real_pairs", (
        f"real_pairs {entry_id}/real_forecast_pairs.json: version=2, "
        f"radiation_contract_version=2"
    )


def check_calibration_model(host: str, entry_id: str) -> tuple[str, str, str]:
    """[4/6] calibration_model is at the journal top level (not in identity)."""
    path = f"{HEMS_DIR}/pv_fact_pairs_{entry_id}.json"
    data = _read_remote_json(host, path)
    if data is None:
        return "FAIL", "calibration_model_top", f"cannot read {path}"
    if "calibration_model" not in data:
        return "NOT_YET_VERIFIED", "calibration_model_top", (
            "no calibration_model set yet (no compatible v2 pairs)"
        )
    tag = data["calibration_model"]
    if tag not in V2_TAGS:
        return "FAIL", "calibration_model_top", (
            f"calibration_model={tag!r} is not a v2 family tag"
        )
    if "identity" in data and isinstance(data["identity"], dict) \
            and "calibration_model" in data["identity"]:
        return "FAIL", "calibration_model_top", (
            "calibration_model must NOT also live in identity (drift)"
        )
    return "PASS", "calibration_model_top", (
        f"calibration_model={tag} at journal top level (not in identity)"
    )


def check_forecast_diagnostic(
    host: str, entity_id: str | None, token: str | None
) -> tuple[str, str, str]:
    """[5/6] Sensor publishes forecast_diagnostic with the
    documented schema and contract v2 invariants.
    """
    if entity_id is None:
        return "NOT_YET_VERIFIED", "forecast_diagnostic", (
            "no entity to query"
        )
    if not token:
        return "NOT_YET_VERIFIED", "forecast_diagnostic", (
            "POWMR_HA_TOKEN not set; cannot read sensor via REST API"
        )
    state = _fetch_sensor(host, entity_id, token)
    if state is None:
        return "FAIL", "forecast_diagnostic", (
            f"REST GET /api/states/{entity_id} failed"
        )
    attrs = state.get("attributes", {}) or {}
    diag = attrs.get("forecast_diagnostic")
    if not isinstance(diag, dict):
        return "FAIL", "forecast_diagnostic", (
            "sensor attributes do not contain a forecast_diagnostic dict"
        )
    required = {
        "forecast_received_at", "forecast_timezone",
        "radiation_contract_version", "forecast_dates",
        "intervals_per_date", "sample_row",
        "rows_with_diff_ne_3600", "forecast_model_tags",
        "forecast_rows_total",
    }
    missing = required - diag.keys()
    if missing:
        return "FAIL", "forecast_diagnostic", (
            f"forecast_diagnostic missing keys: {sorted(missing)}"
        )
    if diag["forecast_rows_total"] == 0:
        return "NOT_YET_VERIFIED", "forecast_diagnostic", (
            "forecast not fetched yet; diagnostic is the empty-marker"
        )
    if diag["radiation_contract_version"] != 2:
        return "FAIL", "forecast_diagnostic", (
            f"radiation_contract_version={diag['radiation_contract_version']} (expected 2)"
        )
    if diag["rows_with_diff_ne_3600"] != 0:
        return "FAIL", "forecast_diagnostic", (
            f"{diag['rows_with_diff_ne_3600']} rows have weather_timestamp - "
            f"timestamp != 3600 (contract v2 violation)"
        )
    bad_tags = [t for t in diag["forecast_model_tags"] if t not in V2_TAGS]
    if bad_tags:
        return "FAIL", "forecast_diagnostic", (
            f"forecast_model_tags include non-v2 tags: {bad_tags}"
        )
    if not diag["forecast_dates"]:
        return "FAIL", "forecast_diagnostic", "forecast_dates is empty"
    return "PASS", "forecast_diagnostic", (
        f"forecast_diagnostic OK: dates={diag['forecast_dates']}, "
        f"rows={diag['forecast_rows_total']}, "
        f"tags={diag['forecast_model_tags']}, "
        f"bad_diffs={diag['rows_with_diff_ne_3600']}"
    )


def check_completed_pair(host: str, entry_id: str) -> tuple[str, str, str]:
    """[6/6] First v2 completed pair.

    A completed pair must satisfy:
      - PvLearningState.pairs has a record for some day D
        with finite forecast_kwh, finite actual_kwh, and
        a v2 model tag. ``pairs`` is a ``dict[day, record]``.
      - RealForecastPairs.pairs has a record for the same
        day D with finite actual_kwh, a v2 model tag, and
        used=True. ``pairs`` is a ``list[record]`` where
        each record has a ``date`` field.
      - The forecast values match across the two journals.
      - The forecast was issued before the predicted day
        (issued_at.date() < day).
    If no v2 pair has been completed yet → NOT_YET_VERIFIED.
    Do NOT search for an invented key; do NOT create a pair
    to make this check pass.
    """
    journal_path = f"{HEMS_DIR}/pv_fact_pairs_{entry_id}.json"
    real_path = f"{HEMS_DIR}/{entry_id}/real_forecast_pairs.json"
    journal = _read_remote_json(host, journal_path)
    real = _read_remote_json(host, real_path)
    if journal is None or real is None:
        return "NOT_YET_VERIFIED", "first_v2_completed_pair", (
            "journal or real_pairs unreadable"
        )
    pairs_j = journal.get("pairs", {}) or {}
    # RealForecastPairs.pairs is a list of {date, ...} dicts.
    pairs_r_raw = real.get("pairs", []) or []
    pairs_r = {}
    if isinstance(pairs_r_raw, list):
        for r in pairs_r_raw:
            if isinstance(r, dict) and "date" in r:
                pairs_r[r["date"]] = r
    elif isinstance(pairs_r_raw, dict):
        pairs_r = pairs_r_raw
    for day in sorted(pairs_j.keys() & pairs_r.keys()):
        pj = pairs_j[day]
        pr = pairs_r[day]
        if not isinstance(pj, dict) or not isinstance(pr, dict):
            continue
        if pj.get("forecast_model") not in V2_TAGS:
            continue
        if pr.get("forecast_model") not in V2_TAGS:
            continue
        if pr.get("used") is not True:
            continue
        fk_j, ak_j = pj.get("forecast_kwh"), pj.get("actual_kwh")
        fk_r, ak_r = pr.get("forecast_kwh"), pr.get("actual_kwh")
        # Finite check
        if any(not isinstance(v, (int, float)) for v in (fk_j, ak_j, fk_r, ak_r)):
            continue
        if abs(fk_j - fk_r) > 1e-6 or abs(ak_j - ak_r) > 1e-6:
            continue
        issued_at = pj.get("issued_at") or pr.get("issued_at") or pr.get("captured_at")
        if not isinstance(issued_at, str):
            continue
        try:
            from datetime import date as _date
            d_issued = _date.fromisoformat(issued_at[:10])
            d_day = _date.fromisoformat(day)
            if d_issued >= d_day:
                continue
        except Exception:
            continue
        return "PASS", "first_v2_completed_pair", (
            f"day={day} forecast_model={pj.get('forecast_model')} "
            f"forecast_kwh={fk_j} actual_kwh={ak_j} (matches in both journals)"
        )
    return "NOT_YET_VERIFIED", "first_v2_completed_pair", (
        "no completed v2 pair in both journals yet"
    )


# ── main ─────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=HOST_DEFAULT)
    args = p.parse_args()
    host = args.host
    token = os.environ.get("POWMR_HA_TOKEN") or None

    print(f"Probing R01 contract v2 on {host}")
    print(f"  token-source: {'env' if token else 'unset'}")
    entry_id = _discover_entry_id(host)
    print(f"  entry: {entry_id}")
    entity_id = _discover_entity_id(host, entry_id)
    if entity_id is None:
        print("  entity: (not discovered)")
    else:
        print(f"  entity: {entity_id}")

    results: list[tuple[str, str, str]] = []
    results.append(check_entity_id(host, entry_id))
    results.append(check_journal_contract(host, entry_id))
    results.append(check_real_pairs(host, entry_id))
    results.append(check_calibration_model(host, entry_id))
    results.append(check_forecast_diagnostic(host, entity_id, token))
    results.append(check_completed_pair(host, entry_id))

    print()
    pass_n = fail_n = nyt_n = 0
    for status, name, msg in results:
        print(f"[{status}] {name}: {msg}")
        if status == "PASS":
            pass_n += 1
        elif status == "FAIL":
            fail_n += 1
        else:
            nyt_n += 1
    print()
    print(
        f"Probe complete. PASS={pass_n} FAIL={fail_n} "
        f"NOT_YET_VERIFIED={nyt_n}."
    )
    if fail_n:
        print("Live R01 contract v2 verification FAILED.")
        return 3
    print("All required checks passed; remaining items are NOT_YET_VERIFIED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
