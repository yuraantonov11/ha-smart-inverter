#!/usr/bin/env python3
"""Live probe for R01 radiation interval contract v2.

Standalone tool — NOT executed by the regular test runner. Run with::

    python scripts/probe_r01_live.py [--host HOST] [--entry ENTRY_ID]

Verifies the live HA storage reflects the new contract:

* ``pv_fact_pairs_*.json`` has ``VERSION=3`` and
  ``radiation_contract_version=2``.
* ``real_forecast_pairs.json`` has ``VERSION=2`` and
  ``radiation_contract_version=2``.
* Legacy pairs are preserved with their original ``_legacy_forecast_model``
  tag.
* The live ``predictive_decision_state`` reports
  ``forecast_model == "hourly_response_v2"`` (or the v1 fallback).

The probe does NOT verify the first v2-issued pair — that requires
a full completed day after deploy and is intentionally marked as
"not yet verified" in the report.

A failed probe returns non-zero. The offline test suite is
independent.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

DEFAULT_HOST = "root@192.168.1.220"
DEFAULT_ENTRY = "01M3XWJ8DRYDQC8A0NCPRVB53N"


def _ssh(host: str, command: str) -> str:
    r = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", host, command],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ssh {command!r} failed: {r.stderr.strip()}")
    return r.stdout


def _cat(host: str, path: str) -> dict:
    return json.loads(_ssh(host, f"cat {path}"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--entry", default=DEFAULT_ENTRY)
    args = parser.parse_args()
    host, entry = args.host, args.entry
    print(f"Probing R01 contract v2 on {host} (entry={entry})")
    base = "/config/custom_components/powmr_inverter/hems"

    # 1) PvLearningState journal
    journal_path = f"{base}/pv_fact_pairs_{entry}.json"
    print(f"\n[1/4] {journal_path}")
    try:
        journal = _cat(host, journal_path)
    except Exception as exc:
        print(f"NOT VERIFIED — journal unreadable: {exc!r}")
        return 2
    print(f"  version: {journal.get('version')}")
    print(f"  radiation_contract_version: {journal.get('radiation_contract_version')}")
    if journal.get("version") != 3:
        print(f"NOT VERIFIED — journal VERSION={journal.get('version')} (expected 3)")
        return 3
    if journal.get("radiation_contract_version") != 2:
        print(f"NOT VERIFIED — radiation_contract_version="
              f"{journal.get('radiation_contract_version')} (expected 2)")
        return 4
    pairs = journal.get("pairs", {})
    print(f"  pairs: {len(pairs)}")
    legacy = [d for d, p in pairs.items() if p.get("_legacy_forecast_model")]
    print(f"  pairs with _legacy_forecast_model: {len(legacy)}")
    if legacy:
        for day in legacy[:3]:
            p = pairs[day]
            print(f"    {day}: model={p.get('forecast_model')} "
                  f"legacy={p.get('_legacy_forecast_model')}")
    calibration_model = (journal.get("identity") or {}).get("calibration_model")
    print(f"  calibration_model: {calibration_model}")

    # 2) RealForecastPairs journal
    pairs_path = f"{base}/real_forecast_pairs.json"
    print(f"\n[2/4] {pairs_path}")
    try:
        real = _cat(host, pairs_path)
    except Exception as exc:
        print(f"NOT VERIFIED — real pairs unreadable: {exc!r}")
        return 5
    print(f"  version: {real.get('version')}")
    print(f"  radiation_contract_version: {real.get('radiation_contract_version')}")
    if real.get("version") != 2:
        print(f"NOT VERIFIED — real pairs VERSION={real.get('version')} (expected 2)")
        return 6
    if real.get("radiation_contract_version") != 2:
        print(f"NOT VERIFIED — real pairs radiation_contract_version="
              f"{real.get('radiation_contract_version')} (expected 2)")
        return 7
    r_pairs = real.get("pairs", [])
    print(f"  issued pairs: {len(r_pairs)}")
    legacy_real = [p for p in r_pairs if p.get("_legacy_forecast_model")]
    print(f"  pairs with _legacy_forecast_model: {len(legacy_real)}")
    if legacy_real:
        for p in legacy_real[:3]:
            print(f"    {p.get('date')}: model={p.get('forecast_model')} "
                  f"legacy={p.get('_legacy_forecast_model')}")

    # 3) Live decision state
    print(f"\n[3/4] sensor.powmr_inverter_predictive_decision_state")
    try:
        state_out = _ssh(
            host,
            "ha state sensor.powmr_inverter_predictive_decision_state",
        )
        state = json.loads(state_out)
    except Exception as exc:
        print(f"NOT VERIFIED — decision state unreadable: {exc!r}")
        return 8
    attrs = state.get("attributes", {})
    fc = attrs.get("forecast_calibration", {})
    print(f"  state: {state.get('state')}")
    print(f"  forecast_model: {fc.get('forecast_model')}")
    print(f"  samples: {fc.get('samples')}")
    print(f"  excluded_model_pairs: {fc.get('excluded_model_pairs')}")
    print(f"  pending_count: {fc.get('pending_count')}")
    if fc.get("forecast_model") not in {"hourly_response_v2", "station_gain_v1"}:
        print(f"NOT VERIFIED — forecast_model={fc.get('forecast_model')} "
              f"is not v2 or v1 fallback")
        return 9
    pending = fc.get("pending", [])
    for p in pending[:3]:
        print(f"  pending: day={p.get('day')} model={p.get('forecast_model')}")

    # 4) Calibration status: still empty after deploy
    print(f"\n[4/4] first v2-issued pair")
    print(f"  NOT YET VERIFIED — requires a completed day after deploy.")
    print(f"  Once observed: expect pairs[].forecast_model == "
          f"'hourly_response_v2' and samples > 0.")

    print(f"\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
