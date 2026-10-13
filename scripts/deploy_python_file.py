#!/usr/bin/env python3
"""Deploy a single Python source file from this repo to the live
HA host, with fresh backup, sha256 verification, atomic rename,
HA Core restart, and rollback on failure.

Юра (2026-10-13): «Розгорни змінений select.py зі свіжим
backup і перевіркою хешу. Самої зміни dashboard недостатньо для
появи target_soc / has_manual_target.»

Usage:
    python3 scripts/deploy_python_file.py select.py
    python3 scripts/deploy_python_file.py select.py --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

HA_HOST = "ha"
HA_USER = "root"
HA_SSH_KEY = "/opt/data/.ssh/id_ed25519"
REMOTE_COMPONENT_DIR = "/config/custom_components/powmr_inverter"
BACKUP_DIR = Path("/opt/data/powmr-ai-work/backups_ui_2026-10-13")
SSH = ("ssh", "-i", HA_SSH_KEY, f"{HA_USER}@{HA_HOST}")
SCP = ("scp", "-i", HA_SSH_KEY)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(cmd: str, *, check: bool = True, timeout: int = 60) -> tuple:
    proc = subprocess.Popen(
        list(SSH) + [cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise
    if check and proc.returncode != 0:
        raise SystemExit(
            f"SSH command failed (rc={proc.returncode}): {cmd}\n"
            f"STDOUT: {out}\nSTDERR: {err}"
        )
    return proc.returncode, out, err


def _scp_put(local: Path, remote: str) -> None:
    rc = subprocess.call(
        list(SCP) + [str(local), f"{HA_USER}@{HA_HOST}:{remote}"],
    )
    if rc != 0:
        raise SystemExit(f"scp put failed (rc={rc}): {local} -> {remote}")


def _scp_get(remote: str, local: Path) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    rc = subprocess.call(
        list(SCP) + [f"{HA_USER}@{HA_HOST}:{remote}", str(local)],
    )
    if rc != 0:
        raise SystemExit(f"scp get failed (rc={rc}): {remote} -> {local}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("relpath", help="path relative to repo root, e.g. select.py")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-restart", action="store_true",
                        help="don't restart HA Core after the file swap")
    args = parser.parse_args()

    local = REPO / args.relpath
    if not local.exists():
        raise SystemExit(f"local file not found: {local}")
    local_sha = _sha(local)
    remote = f"{REMOTE_COMPONENT_DIR}/{args.relpath}"
    print(f"[local] {local} SHA: {local_sha}")

    rc, out, _ = _run(f"sha256sum {shlex.quote(remote)}", timeout=15)
    remote_sha = out.split()[0]
    print(f"[live]  {remote} SHA: {remote_sha}")

    if remote_sha == local_sha:
        print("Live SHA matches local — no deploy needed.")
        return 0

    if args.dry_run:
        print("[dry-run] skipping stop / backup / write / start")
        return 0

    # Stop HA Core.
    print("[step 1] stopping HA Core")
    rc, out, err = _run("ha core stop", check=False, timeout=180)
    if rc != 0:
        raise SystemExit(f"ha core stop failed (rc={rc}): {err}")
    for _ in range(60):
        # ``ha core info`` returns a JSON-like metadata block.
        # After stop, ``boot`` may flip to false; we poll the
        # HTTP endpoint (which is on the Supervisor bridge, not
        # localhost) to be sure.
        rc, out, _ = _run(
            "curl -sS -o /dev/null -w '%{http_code}' "
            "http://172.30.32.1:8123/ 2>/dev/null || echo 000",
            check=False, timeout=10,
        )
        code = out.strip()
        if code == "000":
            print(f"        core stopped: HTTP 000 on bridge")
            break
        time.sleep(1)
    else:
        print("        core stop timed out (still serving HTTP)")

    # Fresh backup of the live file (post-stop).
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_remote = f"{remote}.{ts}.predeploy"
    backup_local = BACKUP_DIR / f"{args.relpath}.{ts}.predeploy"
    print(f"[step 2] pre-deploy backup: {backup_local}")
    _run(f"cp -- {shlex.quote(remote)} {shlex.quote(backup_remote)}")
    _scp_get(backup_remote, backup_local)

    # Stage the new file, verify SHA, atomic rename.
    stage_remote = f"{remote}.{ts}.staged"
    print(f"[step 3] staging: {stage_remote}")
    _scp_put(local, stage_remote)
    rc, out, _ = _run(f"sha256sum {shlex.quote(stage_remote)}", timeout=15)
    staged_sha = out.split()[0]
    if staged_sha != local_sha:
        _run("ha core start", check=False, timeout=180)
        raise SystemExit(
            f"Staged SHA mismatch: local={local_sha} remote={staged_sha}. "
            f"Live file untouched. Core restarted."
        )
    print(f"        SHA verified: {staged_sha[:16]}…")
    print(f"[step 4] atomic rename: {stage_remote} -> {remote}")
    _run(f"mv -- {shlex.quote(stage_remote)} {shlex.quote(remote)}")
    rc, out, _ = _run(f"sha256sum {shlex.quote(remote)}", timeout=15)
    live_after_sha = out.split()[0]
    if live_after_sha != local_sha:
        # Restore the backup and start Core.
        _run(f"cp -- {shlex.quote(backup_remote)} {shlex.quote(remote)}", check=False)
        _run("ha core start", check=False, timeout=180)
        raise SystemExit(
            f"Post-rename SHA mismatch: live={live_after_sha} local={local_sha}. "
            f"Restored pre-deploy backup. Core restarted."
        )
    print(f"        live SHA verified: {live_after_sha[:16]}…")

    if args.skip_restart:
        print("[step 5] --skip-restart; leaving Core stopped")
        return 0

    # Start HA Core.
    print("[step 5] starting HA Core")
    _run("ha core start", check=False, timeout=180)
    for _ in range(60):
        rc, out, _ = _run(
            "curl -sS -o /dev/null -w '%{http_code}' "
            "http://172.30.32.1:8123/ 2>/dev/null || echo 000",
            check=False, timeout=10,
        )
        code = out.strip()
        if code == "200" or code == "302":
            print(f"        core running: HTTP {code}")
            break
        time.sleep(1)
    else:
        # Rollback.
        _run(f"cp -- {shlex.quote(backup_remote)} {shlex.quote(remote)}", check=False)
        _run("ha core restart", check=False, timeout=180)
        raise SystemExit(
            f"Core did not come back. Restored pre-deploy backup. "
            f"Core restart issued."
        )

    # Final readback.
    print("[step 6] final readback")
    rc, out, _ = _run(f"sha256sum {shlex.quote(remote)}", timeout=15)
    final_sha = out.split()[0]
    if final_sha != local_sha:
        _run(f"cp -- {shlex.quote(backup_remote)} {shlex.quote(remote)}", check=False)
        _run("ha core restart", check=False, timeout=180)
        raise SystemExit(
            f"Post-deploy readback SHA mismatch: live={final_sha} local={local_sha}. "
            f"Restored pre-deploy backup. Core restarted."
        )
    print(f"        final live SHA: {final_sha[:16]}…")
    print("DEPLOY SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
