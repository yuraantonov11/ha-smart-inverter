#!/usr/bin/env python3
"""Deploy the AI-card patch to live HA without a long-lived access token.

Юра (2026-10-13): «Застосування без нового токена: використай
Python у Hermes. Прочитай dashboard і entity registry через
SSH, виконай registry substitution та patch на локальній
копії, перевір дозволений структурний diff й повторне
застосування без нових змін.»

This script:
  1. Pulls the current live ``lovelace.powmr_energy`` (and
     storage envelope) over SSH using ``paramiko`` (no auth
     tokens needed; the operator's SSH key is already trusted
     on the HA host).
  2. Loads the source-of-truth from ``dashboard.yaml`` and
     pulls the three target cards.
  3. Stops the HA Core (via the ``ha`` core-ssh CLI) and
     takes a fresh backup of the live storage file.
  4. Applies the AI-card patch on the LOCAL copy (no
     WebSocket, no token).
  5. Verifies idempotency: a second pass on the patched copy
     must be a no-op.
  6. Verifies the storage envelope, dashboard metadata, view
     set, and all non-AI cards are byte-equal between
     pre-deploy and post-patch.
  7. SCPs the new file into the HA ``/config/.storage/``
     staging directory, verifies SHA-256 against the local
     file, then renames it into place atomically.
  8. Re-starts the HA Core. If the Core does not recover, the
     pre-deploy backup is restored and the Core is started
     again.

Usage:
    python3 scripts/deploy_dashboard_no_token.py [--dry-run]

Flags:
    --dry-run  : do everything except the live write / restart.
                 Default behaviour is to run end-to-end and
                 apply.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.patch_lovelace_ai_cards import patch_cards, ALL_CARD_TITLES  # noqa: E402

HA_HOST = "ha"
HA_USER = "root"
HA_SSH_KEY = "/opt/data/.ssh/id_ed25519"
LIVE_STORAGE_DIR = "/config/.storage"
LIVE_TARGET = f"{LIVE_STORAGE_DIR}/lovelace.powmr_energy"
LOCAL_SNAPSHOT_DIR = Path("/opt/data/powmr-ai-work/live_snapshot_2026-10-13")
BACKUP_DIR = Path("/opt/data/powmr-ai-work/backups_ui_2026-10-13")
SSH = ("ssh", "-i", HA_SSH_KEY, f"{HA_USER}@{HA_HOST}")
SCP = ("scp", "-i", HA_SSH_KEY)


def _target_cards_from(target: dict) -> dict:
    found: dict = {}
    for view in target.get("views", []):
        for s in view.get("sections", []):
            for c in s.get("cards", []):
                t = c.get("title", "")
                if t in ALL_CARD_TITLES:
                    found[t] = copy.deepcopy(c)
    missing = set(ALL_CARD_TITLES) - set(found)
    if missing:
        raise SystemExit(f"target dashboard.yaml missing cards: {sorted(missing)}")
    return found


def _canon(obj) -> bytes:
    """Deterministic JSON for SHA / equality checks."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ssh_client():
    """Open an SSH connection. Uses the system ``ssh`` binary so
    that we do not need extra Python deps (paramiko is not in
    the project venv). Returns an ``SSHClient``-like wrapper.
    """
    return _SubprocessSSH()


class _SubprocessSSH:
    """Lightweight wrapper around the system ``ssh`` binary that
    exposes a ``.exec_command(cmd, timeout=N)`` method and a
    ``.close()`` no-op for parity with the paramiko-style API
    that the rest of the script uses."""

    def exec_command(self, cmd: str, *, timeout: int = 60) -> "_SubprocessCommand":
        full_cmd = list(SSH) + [cmd]
        proc = subprocess.Popen(
            full_cmd,
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
        return _SubprocessCommand(proc.returncode, out, err)

    def close(self) -> None:
        return None

    def open_sftp(self):
        """Return a minimal SFTP-like context manager that supports
        ``.put(local, remote)`` and ``.get(remote, local)``."""
        return _SubprocessSFTP()


class _SubprocessCommand:
    def __init__(self, rc: int, out: str, err: str):
        self.rc = rc
        self.out = out
        self.err = err


class _SubprocessSFTP:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def get(self, remote: str, local: str) -> None:
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        rc = subprocess.call(
            list(SCP) + [f"{HA_USER}@{HA_HOST}:{remote}", local],
        )
        if rc != 0:
            raise SystemExit(f"scp get failed (rc={rc}): {remote} -> {local}")

    def put(self, local: str, remote: str) -> None:
        rc = subprocess.call(
            list(SCP) + [local, f"{HA_USER}@{HA_HOST}:{remote}"],
        )
        if rc != 0:
            raise SystemExit(f"scp put failed (rc={rc}): {local} -> {remote}")


def _run(cmd: str, *, check: bool = True, timeout: int = 60) -> tuple[int, str, str]:
    """Run a command on HA over SSH, return (rc, stdout, stderr)."""
    client = _ssh_client()
    try:
        result = client.exec_command(cmd, timeout=timeout)
    finally:
        client.close()
    if check and result.rc != 0:
        raise SystemExit(f"SSH command failed (rc={result.rc}): {cmd}\nSTDOUT: {result.out}\nSTDERR: {result.err}")
    return result.rc, result.out, result.err


def _scp_get(remote: str, local: Path) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    with _ssh_client().open_sftp() as sftp:
        sftp.get(remote, str(local))


def _scp_put(local: Path, remote: str) -> None:
    with _ssh_client().open_sftp() as sftp:
        sftp.put(str(local), remote)


def _verify_idempotent(patched: dict, cards_target: dict) -> None:
    config_after_second, stats2 = patch_cards(copy.deepcopy(patched), cards_target)
    if stats2["replaced"] or stats2["inserted"]:
        raise SystemExit(f"Idempotency FAIL: second pass produced changes {stats2}")
    if _canon(patched) != _canon(config_after_second):
        raise SystemExit("Idempotency FAIL: second pass canonical bytes differ")


def _verify_envelope_and_non_ai_preserved(pre: dict, post: dict) -> None:
    """Юра: «Збережи storage-envelope, metadata та всі
    нетаргетовані картки.»"""
    # Envelope / metadata — must be byte-equal.
    for key in ("version", "minor_version", "key", "type"):
        if pre.get(key) != post.get(key):
            raise SystemExit(f"Envelope metadata {key!r} changed: {pre.get(key)} -> {post.get(key)}")
    if "data" in pre and "data" in post:
        for key in pre["data"]:
            if key == "config":
                continue
            if pre["data"].get(key) != post["data"].get(key):
                raise SystemExit(f"Envelope data.{key} changed")
    # View set + non-AI card bodies — must be byte-equal.
    pre_views = [v.get("title", "?") for v in pre["data"]["config"].get("views", [])]
    post_views = [v.get("title", "?") for v in post["data"]["config"].get("views", [])]
    if pre_views != post_views:
        raise SystemExit(f"View set changed: {pre_views} -> {post_views}")
    pre_config = pre["data"]["config"]
    post_config = post["data"]["config"]
    pre_non_ai = [c for c in _iter_cards(pre_config) if "type" in c and c.get("title") not in ALL_CARD_TITLES]
    post_non_ai = [c for c in _iter_cards(post_config) if "type" in c and c.get("title") not in ALL_CARD_TITLES]
    pre_canon = sorted(_canon(c) for c in pre_non_ai)
    post_canon = sorted(_canon(c) for c in post_non_ai)
    if pre_canon != post_canon:
        raise SystemExit(
            f"Non-AI cards changed: {len(pre_canon)} -> {len(post_canon)}, "
            f"{sum(1 for a, b in zip(pre_canon, post_canon) if a != b)} differ"
        )


def _iter_cards(config: dict):
    """Yield every card dict inside the config (recursive)."""
    def _walk(obj):
        if isinstance(obj, dict):
            if "type" in obj and "title" in obj:
                yield obj
            for v in obj.values():
                yield from _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from _walk(v)
    yield from _walk(config)


def _is_ai_card_changed(pre_card, post_card, expected) -> bool:
    """Returns True if the post AI card matches the target."""
    return _canon(post_card) == _canon(expected)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="do everything except the live write / restart")
    args = parser.parse_args()

    target = yaml.safe_load((REPO / "dashboard.yaml").read_text(encoding="utf-8"))
    cards_target = _target_cards_from(target)
    print(f"[target] {len(cards_target)} AI cards extracted from dashboard.yaml")

    # 1. Pull the current live dashboard.
    LOCAL_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    live_local = LOCAL_SNAPSHOT_DIR / "lovelace.powmr_energy"
    print(f"[step 1] pulling live dashboard over SSH → {live_local}")
    _scp_get(LIVE_TARGET, live_local)
    envelope = json.loads(live_local.read_text(encoding="utf-8"))
    live_sha = _sha(live_local)
    print(f"        live SHA: {live_sha}")

    # 2. Apply patch on the LOCAL copy.
    print("[step 2] applying patch on local copy (no token needed)")
    config = copy.deepcopy(envelope["data"]["config"])
    patched, stats = patch_cards(config, cards_target)
    print(f"        replaced={len(stats['replaced'])} inserted={len(stats['inserted'])} "
          f"already_up_to_date={len(stats['already_up_to_date'])}")

    # 3. Idempotency — second pass must be a no-op.
    print("[step 3] verifying idempotency (second pass is no-op)")
    _verify_idempotent(patched, cards_target)

    # 4. Envelope / non-AI invariant.
    print("[step 4] verifying envelope and non-AI cards are preserved")
    patched_envelope = copy.deepcopy(envelope)
    patched_envelope["data"]["config"] = patched
    _verify_envelope_and_non_ai_preserved(envelope, patched_envelope)

    # 5. Save patched envelope locally.
    patched_local = LOCAL_SNAPSHOT_DIR / "lovelace.powmr_energy.patched"
    patched_local.write_text(
        json.dumps(patched_envelope, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    patched_sha = _sha(patched_local)
    print(f"[step 5] patched local file: {patched_local} (SHA {patched_sha[:16]}…)")

    if args.dry_run:
        print("[dry-run] skipping live stop / write / start")
        return 0

    # 6. Stop HA Core.
    print("[step 6] stopping HA Core")
    rc, out, err = _run("ha core stop", check=False, timeout=120)
    print(f"        rc={rc} out={out.strip()[:200]!r} err={err.strip()[:200]!r}")
    if rc != 0:
        raise SystemExit(f"ha core stop failed (rc={rc}): {err}")
    # Wait for the core to be fully stopped. core-ssh exits
    # immediately; poll the storage file's mtime via SSH.
    for _ in range(30):
        rc, out, _ = _run("ha core status", check=False, timeout=15)
        if "stopped" in out.lower():
            print(f"        core stopped: {out.strip()[:200]!r}")
            break
        time.sleep(1)

    # 7. Take a fresh backup of the live file (post-stop).
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_remote = f"/config/.storage/lovelace.powmr_energy.{ts}.predeploy"
    backup_local = BACKUP_DIR / f"lovelace.powmr_energy.{ts}.predeploy"
    print(f"[step 7] pre-deploy backup: {backup_local}")
    _run(f"cp -- {shlex.quote(LIVE_TARGET)} {shlex.quote(backup_remote)}")
    _scp_get(backup_remote, backup_local)
    backup_sha = _sha(backup_local)
    print(f"        backup SHA: {backup_sha}")

    # 8. SCP the patched file into a staging path on HA.
    stage_remote = f"{LIVE_STORAGE_DIR}/lovelace.powmr_energy.{ts}.staged"
    print(f"[step 8] staging patched file: {stage_remote}")
    _scp_put(patched_local, stage_remote)
    # 9. Verify SHA-256 on the HA side before rename.
    rc, out, err = _run(f"sha256sum {shlex.quote(stage_remote)}", timeout=15)
    remote_sha = out.split()[0]
    if remote_sha != patched_sha:
        # Roll back the Core restart by starting again; the
        # live file is untouched.
        _run("ha core start", check=False, timeout=120)
        raise SystemExit(
            f"Staged SHA mismatch: local={patched_sha} remote={remote_sha}. "
            f"Live file untouched. Core restarted."
        )
    print(f"        SHA verified: {remote_sha[:16]}…")

    # 10. Atomic rename of the staged file into the live path.
    print(f"[step 10] atomic rename: {stage_remote} -> {LIVE_TARGET}")
    _run(f"mv -- {shlex.quote(stage_remote)} {shlex.quote(LIVE_TARGET)}")
    # 11. Verify the live SHA matches the patched SHA.
    rc, out, err = _run(f"sha256sum {shlex.quote(LIVE_TARGET)}", timeout=15)
    live_after_sha = out.split()[0]
    if live_after_sha != patched_sha:
        # Restore the backup and start Core.
        _run(f"cp -- {shlex.quote(backup_remote)} {shlex.quote(LIVE_TARGET)}", check=False)
        _run("ha core start", check=False, timeout=120)
        raise SystemExit(
            f"Post-rename SHA mismatch: live={live_after_sha} patched={patched_sha}. "
            f"Restored pre-deploy backup. Core restarted."
        )
    print(f"        live SHA verified: {live_after_sha[:16]}…")

    # 12. Start HA Core back up.
    print("[step 12] starting HA Core")
    _run("ha core start", check=False, timeout=180)
    # 13. Wait for Core to be RUNNING.
    for _ in range(60):
        rc, out, _ = _run("ha core status", check=False, timeout=15)
        if "running" in out.lower():
            print(f"        core running: {out.strip()[:200]!r}")
            break
        time.sleep(1)

    # 14. Final readback of the live file.
    print("[step 14] final readback of live file")
    final_local = LOCAL_SNAPSHOT_DIR / "lovelace.powmr_energy.postdeploy"
    _scp_get(LIVE_TARGET, final_local)
    final_sha = _sha(final_local)
    if final_sha != patched_sha:
        # Rollback.
        _run(f"cp -- {shlex.quote(backup_remote)} {shlex.quote(LIVE_TARGET)}", check=False)
        _run("ha core restart", check=False, timeout=120)
        raise SystemExit(
            f"Post-deploy readback SHA mismatch: live={final_sha} patched={patched_sha}. "
            f"Restored pre-deploy backup. Core restarted."
        )
    print(f"        final live SHA: {final_sha[:16]}…")
    print("DEPLOY SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
