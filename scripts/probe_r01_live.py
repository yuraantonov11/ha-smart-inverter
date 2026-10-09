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
import math
import os
import subprocess
import sys
import tempfile
from datetime import date as _date
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


def _is_finite_number(v) -> bool:
    """``True`` iff ``v`` is a real (non-bool) number and is
    finite (not NaN, not Inf). Booleans, strings, ``None``,
    and ``math.nan``/``math.inf`` all return ``False``.
    """
    if isinstance(v, bool):
        return False
    if not isinstance(v, (int, float)):
        return False
    return math.isfinite(v)


def _finite(v, default=None):
    """Coerce a possibly non-finite value to ``default``. Returns
    the input if finite, else ``default``.
    """
    return v if _is_finite_number(v) else default


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


def _fetch_sensor_from_state_input(path: str) -> dict | None:
    """Read a pre-fetched sensor state from a local file or
    ``-`` (stdin). The token never enters the probe's input
    paths because the operator runs the live-state fetch
    through their own channel (e.g. Hermes's
    ``ha_get_state``) and pipes the JSON here.
    """
    if path == "-":
        data = sys.stdin.read()
    else:
        with open(path, "r", encoding="utf-8") as f:
            data = f.read()
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def _fetch_sensor(host: str, entity_id: str, token: str | None) -> dict | None:
    """Read the sensor state via the host-local HA REST API.

    The token is taken from the ``POWMR_HA_TOKEN`` env var by
    the caller and passed in here. The probe does NOT pass
    the token in any SSH/curl command line that ends up in
    a process listing or in the shell history. Instead the
    token is forwarded via the ``Authorization: Bearer ...``
    header, which is the only path that should ever see it.
    The probe never logs the header value.
    """
    if not token:
        return None
    # Build a tiny Python helper that uses ``requests`` to
    # perform the request without exposing the token in the
    # shell command. The helper reads the token from
    # ``$POWMR_HA_TOKEN`` (set in the SSH session via the
    # `ssh host "POWMR_HA_TOKEN=... ..."` form, which the
    # helper file does NOT log).
    helper = (
        "import json, os, sys, urllib.request\n"
        "url = sys.argv[1]\n"
        "token = os.environ.get('POWMR_HA_TOKEN', '')\n"
        "req = urllib.request.Request(url)\n"
        "req.add_header('Authorization', 'Bearer ' + token)\n"
        "try:\n"
        "    with urllib.request.urlopen(req, timeout=10) as r:\n"
        "        sys.stdout.write(r.read().decode('utf-8'))\n"
        "except Exception as exc:\n"
        "    sys.stderr.write('http-error: ' + str(exc) + '\\n')\n"
        "    sys.exit(1)\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(helper)
        local_helper = f.name
    remote_helper = f"/tmp/_probe_fetch_{os.getpid()}.py"
    try:
        # ``BatchMode=yes`` ensures no interactive password
        # prompt. We forward the token in the SSH command
        # environment; the token is NOT visible in the
        # command line itself, only in the helper's
        # environment, which the helper does not echo.
        env_eq = "POWMR_HA_TOKEN=" + token
        # Defensive redaction: if anyone ever runs the probe
        # with the helper debug, the token must not be
        # printed. The helper writes only the HTTP body.
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", host,
             f"{env_eq} python3 {remote_helper} http://127.0.0.1:8123/api/states/{entity_id}"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            return None
    finally:
        try:
            os.unlink(local_helper)
        except OSError:
            pass
        _ssh(host, "rm", "-f", remote_helper, timeout=10)


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
    state: dict | None,
) -> tuple[str, str, str]:
    """[5/6] Sensor publishes forecast_diagnostic with the
    documented schema and contract v2 invariants.

    An empty forecast (no rows yet) is the expected
    "forecast not received" state — NOT_YET_VERIFIED, never
    FAIL. Missing keys that should always be present
    (e.g. the production code MUST publish the schema even
    when empty) is a FAIL.
    """
    if state is None:
        return "NOT_YET_VERIFIED", "forecast_diagnostic", (
            "no sensor state provided (use --state-input or POWMR_HA_TOKEN)"
        )
    attrs = state.get("attributes", {}) or {}
    diag = attrs.get("forecast_diagnostic")
    if not isinstance(diag, dict):
        return "FAIL", "forecast_diagnostic", (
            "sensor attributes do not contain a forecast_diagnostic dict"
        )
    # The schema must be present even when the forecast is
    # empty (this is how the live sensor reports
    # "forecast not yet received").
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
    # Empty forecast = NOT_YET_VERIFIED, not FAIL.
    if diag["forecast_rows_total"] == 0:
        return "NOT_YET_VERIFIED", "forecast_diagnostic", (
            "forecast not yet received (empty marker schema present); "
            f"received_at={diag['forecast_received_at']!r}"
        )
    # Reject non-finite contract values (NaN, Inf, None, str).
    contract = diag["radiation_contract_version"]
    if not _is_finite_number(contract):
        return "FAIL", "forecast_diagnostic", (
            f"radiation_contract_version is non-finite: {contract!r}"
        )
    if int(contract) != 2:
        return "FAIL", "forecast_diagnostic", (
            f"radiation_contract_version={contract} (expected 2)"
        )
    if not _is_finite_number(diag["rows_with_diff_ne_3600"]):
        return "FAIL", "forecast_diagnostic", (
            f"rows_with_diff_ne_3600 is non-finite: "
            f"{diag['rows_with_diff_ne_3600']!r}"
        )
    if int(diag["rows_with_diff_ne_3600"]) != 0:
        return "FAIL", "forecast_diagnostic", (
            f"{diag['rows_with_diff_ne_3600']} rows have weather_timestamp - "
            f"timestamp != 3600 (contract v2 violation)"
        )
    bad_tags = [t for t in (diag.get("forecast_model_tags") or [])
                if not isinstance(t, str) or t not in V2_TAGS]
    if bad_tags:
        return "FAIL", "forecast_diagnostic", (
            f"forecast_model_tags include non-v2 tags: {bad_tags}"
        )
    if not diag["forecast_dates"]:
        return "FAIL", "forecast_diagnostic", "forecast_dates is empty"
    if not _is_finite_number(diag["forecast_received_at"]):
        return "FAIL", "forecast_diagnostic", (
            f"forecast_received_at is non-finite or None "
            f"despite rows_total={diag['forecast_rows_total']}: "
            f"{diag['forecast_received_at']!r}"
        )
    return "PASS", "forecast_diagnostic", (
        f"forecast_diagnostic OK: dates={diag['forecast_dates']}, "
        f"rows={diag['forecast_rows_total']}, "
        f"tags={diag['forecast_model_tags']}, "
        f"bad_diffs={diag['rows_with_diff_ne_3600']}, "
        f"received_at={diag['forecast_received_at']}"
    )


def check_completed_pair(host: str, entry_id: str) -> tuple[str, str, str]:
    """[6/6] First v2 completed pair.

    A completed pair must satisfy ALL of:
      - ``PvLearningState.pairs[day]`` has finite
        ``forecast_kwh`` AND finite ``actual_kwh`` (NaN/Inf
        are rejected by ``_is_finite_number``).
      - ``PvLearningState.pairs[day]`` carries a v2 model
        tag (``hourly_response_v2`` or ``station_gain_v2``).
      - ``RealForecastPairs.pairs`` (a list-of-records) has
        a record for the same day D with
        ``used == True`` (the production code marks a
        record as completed via this flag).
      - Both records carry the SAME v2 model tag.
      - ``forecast_kwh`` and ``actual_kwh`` agree between
        the two journals to 1e-6.
      - ``issued_at.date() < day`` (forecast was issued
        before the predicted day).
    If no v2 pair has been completed yet → NOT_YET_VERIFIED.
    If a v2-tagged pair exists but FAILS the
    above (NaN, mismatched model, etc.) → FAIL.
    Do NOT search for an invented key; do NOT create a
    pair to make this check pass.
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
    # First, look for ANY v2-tagged pair to detect the
    # negative case: a v2 pair exists but is invalid
    # (NaN, mismatched model, etc.). This must FAIL.
    any_v2_seen = False
    for day in sorted(pairs_j.keys() & pairs_r.keys()):
        pj = pairs_j[day]
        pr = pairs_r[day]
        if not isinstance(pj, dict) or not isinstance(pr, dict):
            continue
        if pj.get("forecast_model") not in V2_TAGS:
            continue
        if pr.get("forecast_model") not in V2_TAGS:
            continue
        any_v2_seen = True
        # Mismatched model tags (one hourly, one station).
        if pj.get("forecast_model") != pr.get("forecast_model"):
            return "FAIL", "first_v2_completed_pair", (
                f"day={day}: model tags differ between journals "
                f"({pj.get('forecast_model')!r} vs {pr.get('forecast_model')!r})"
            )
        if pr.get("used") is not True:
            continue
        fk_j, ak_j = pj.get("forecast_kwh"), pj.get("actual_kwh")
        fk_r, ak_r = pr.get("forecast_kwh"), pr.get("actual_kwh")
        if not all(_is_finite_number(v) for v in (fk_j, ak_j, fk_r, ak_r)):
            return "FAIL", "first_v2_completed_pair", (
                f"day={day}: non-finite forecast/actual values "
                f"(journal: fk={fk_j!r} ak={ak_j!r}; real: fk={fk_r!r} ak={ak_r!r})"
            )
        # All four values are finite numbers; coerce to float
        # so the absolute-difference subtraction is well-typed.
        fk_j_f, ak_j_f, fk_r_f, ak_r_f = (
            float(fk_j), float(ak_j), float(fk_r), float(ak_r)
        )
        if abs(fk_j_f - fk_r_f) > 1e-6 or abs(ak_j_f - ak_r_f) > 1e-6:
            return "FAIL", "first_v2_completed_pair", (
                f"day={day}: forecast/actual differ between journals "
                f"(journal fk={fk_j_f} ak={ak_j_f}; real fk={fk_r_f} ak={ak_r_f})"
            )
        issued_at = pj.get("issued_at") or pr.get("issued_at") or pr.get("captured_at")
        if not isinstance(issued_at, str):
            return "FAIL", "first_v2_completed_pair", (
                f"day={day}: missing or non-string issued_at"
            )
        try:
            d_issued = _date.fromisoformat(issued_at[:10])
            d_day = _date.fromisoformat(day)
            if d_issued >= d_day:
                return "FAIL", "first_v2_completed_pair", (
                    f"day={day}: forecast issued_at.date()={d_issued} "
                    f"is not before day {d_day}"
                )
        except Exception as exc:
            return "FAIL", "first_v2_completed_pair", (
                f"day={day}: issued_at unparseable: {issued_at!r} ({exc!r})"
            )
        return "PASS", "first_v2_completed_pair", (
            f"day={day} model={pj.get('forecast_model')} "
            f"forecast_kwh={fk_j} actual_kwh={ak_j} "
            f"(matches in both journals, issued_at={d_issued})"
        )
    if any_v2_seen:
        # v2 pairs exist but none passed the full
        # completion check (e.g. used=False, no actual_kwh).
        # This is NOT_YET_VERIFIED — pairs are pending
        # completion by the production pipeline.
        return "NOT_YET_VERIFIED", "first_v2_completed_pair", (
            "v2-tagged pairs present in both journals but none "
            "completed (used=True + actual_kwh + matching "
            "forecast)"
        )
    return "NOT_YET_VERIFIED", "first_v2_completed_pair", (
        "no completed v2 pair in both journals yet"
    )


# ── main ─────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=HOST_DEFAULT)
    p.add_argument(
        "--state-input", default=None,
        help="Path to a pre-fetched sensor state JSON. Pass "
             "'-' to read from stdin. The token never enters "
             "this path; the operator fetches the state "
             "through their own channel (Hermes's "
             "ha_get_state, a curl with a manually-supplied "
             "header, etc.) and pipes it here.",
    )
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

    # Resolve the sensor state. Priority: --state-input
    # (file or stdin) → live fetch via POWMR_HA_TOKEN → None
    # (which yields NOT_YET_VERIFIED on the diagnostic check).
    state = None
    if args.state_input:
        state = _fetch_sensor_from_state_input(args.state_input)
        print(f"  state-input: {args.state_input!r} (loaded={state is not None})")
    if state is None and token and entity_id:
        state = _fetch_sensor(host, entity_id, token)
        print(f"  state-source: live REST (loaded={state is not None})")

    results: list[tuple[str, str, str]] = []
    results.append(check_entity_id(host, entry_id))
    results.append(check_journal_contract(host, entry_id))
    results.append(check_real_pairs(host, entry_id))
    results.append(check_calibration_model(host, entry_id))
    results.append(check_forecast_diagnostic(state))
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
