#!/usr/bin/env python3
"""Live probe for R03 forecast pairs state.

This is a **standalone** tool — it is NOT executed by the regular test
runner. Run it manually with::

    python scripts/probe_r03_live.py [--host HOST] [--entry ENTRY_ID]

The probe reports the current ``pv_fact_pairs_*.json`` and
``real_forecast_pairs.json`` for the given HA host. If the SSH
connection fails or the JSON is malformed, the probe reports
"NOT VERIFIED" and exits with a non-zero status.

A failed probe must NEVER cause the test suite to fail. The test
suite (``tests/test_r03_forecast_pairs.py``) is offline-only.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_HOST = "root@192.168.1.220"
DEFAULT_ENTRY = "01M3XWJ8DRYDQC8A0NCPRVB53N"


def _ssh(host: str, path: str) -> str | None:
    """Run ``cat <path>`` on the remote host. Returns the file contents
    or None on failure.
    """
    try:
        result = subprocess.run(
            ["ssh", host, f"cat {path}"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"NOT VERIFIED: SSH failed: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(
            f"NOT VERIFIED: ssh returned {result.returncode}: "
            f"{result.stderr.strip()}",
            file=sys.stderr,
        )
        return None
    return result.stdout


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=DEFAULT_HOST,
                   help="SSH target (default: %(default)s)")
    p.add_argument("--entry", default=DEFAULT_ENTRY,
                   help="Config-entry id (default: %(default)s)")
    args = p.parse_args()

    print(f"=== R03 live probe ===")
    print(f"Host: {args.host}")
    print(f"Entry: {args.entry}")

    # Probe 1: pv_fact_pairs_*.json (PvLearningState journal).
    pairs_path = f"/config/custom_components/powmr_inverter/hems/pv_fact_pairs_{args.entry}.json"
    pairs_text = _ssh(args.host, pairs_path)
    if pairs_text is None:
        print("NOT VERIFIED: pairs journal unreachable")
        return 2
    try:
        pairs = json.loads(pairs_text)
    except json.JSONDecodeError as exc:
        print(f"NOT VERIFIED: pairs journal is not valid JSON: {exc}")
        return 2
    n_snapshots = len(pairs.get("snapshots", {}))
    n_pairs = len(pairs.get("pairs", {}))
    cal_model = pairs.get("calibration_model")
    print(f"\n--- {pairs_path} ---")
    print(f"snapshots: {n_snapshots}")
    for day, snap in sorted(pairs.get("snapshots", {}).items()):
        print(f"  {day}: forecast_kwh={snap.get('forecast_kwh')}, "
              f"model={snap.get('forecast_model')}, "
              f"issued_at={snap.get('issued_at')}")
    print(f"pairs: {n_pairs}")
    for day, pair in sorted(pairs.get("pairs", {}).items()):
        print(f"  {day}: used={pair.get('used')}, "
              f"actual_kwh={pair.get('actual_kwh')}, "
              f"model={pair.get('forecast_model')}")
    print(f"calibration_model: {cal_model}")

    # Probe 2: real_forecast_pairs.json (RealForecastPairs journal).
    rfp_path = (f"/config/custom_components/powmr_inverter/hems/"
                f"{args.entry}/real_forecast_pairs.json")
    rfp_text = _ssh(args.host, rfp_path)
    if rfp_text is None:
        print("\nNOT VERIFIED: real_forecast_pairs.json unreachable")
        return 2
    try:
        rfp = json.loads(rfp_text)
    except json.JSONDecodeError as exc:
        print(f"NOT VERIFIED: real_forecast_pairs.json is not valid JSON: {exc}")
        return 2
    print(f"\n--- {rfp_path} ---")
    print(f"version: {rfp.get('version')}")
    print(f"identity: {rfp.get('identity')}")
    n_rfp_pairs = len(rfp.get("pairs", []))
    print(f"pairs: {n_rfp_pairs}")
    for row in rfp.get("pairs", []):
        print(f"  {row.get('date')}: used={row.get('used')}, "
              f"actual_kwh={row.get('actual_kwh')}, "
              f"forecast_kwh={row.get('forecast_kwh')}, "
              f"model={row.get('forecast_model')}, "
              f"captured_at={row.get('captured_at')}")

    print("\n=== Probe complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
