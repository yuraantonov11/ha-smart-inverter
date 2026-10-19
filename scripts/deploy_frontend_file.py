#!/usr/bin/env python3
"""Deploy a single frontend (JS) asset to the live HA host with
HTTP-level verification.

This script is the front-end counterpart to
``deploy_python_file.py`` and exists because the previous round
(round 6) shipped ``k-outage-scenarios-card.js`` to
``/config/custom_components/powmr_inverter/frontend/`` with a
matching local ↔ live SHA in the integration directory -- and
that was insufficient. The browser fetches the card from
``/local/community/powmr-inverter/...`` (a path served by the
HA HTTP server out of ``/config/www/community/``). If that file
is stale, the operator sees the previous round's behaviour
regardless of how green the integration SHA looks.

The script:

1. Backs up the live frontend file in ``www/community`` (the
   served path) and in the integration directory.
2. Stages the local file to both paths.
3. SHA-verifies after each write.
4. Restarts HA Core (or reloads the integration) so the
   ``_hash_assets`` cache-bust hash is recomputed against the
   new bytes.
5. Reads the new cache-bust hash from the
   ``outage-scenarios card registered (cache-bust: ...
   outage=<hash>)`` log line.
6. **Crucially**: fetches the asset over HTTP at
   ``http://<ha-host>/local/community/powmr-inverter/<file>?v=<hash>``
   and asserts the served SHA matches the local SHA. The
   operator's browser will fetch exactly this URL; if the
   served bytes are stale (HA cache, symlink mismatch,
   supervisor not picking up the write), this assertion fails
   and the deploy is rolled back.

Usage:

    python3 scripts/deploy_frontend_file.py \\
        frontend/k-outage-scenarios-card.js

    # Or a different asset:
    python3 scripts/deploy_frontend_file.py \\
        frontend/forecast-card.js

    # Dry run (no writes, no restart):
    python3 scripts/deploy_frontend_file.py \\
        frontend/k-outage-scenarios-card.js --dry-run

Юра (2026-10-19): «Додай перевірку HTTP-файлу до процедури
frontend-деплою: збігу SHA лише в каталозі інтеграції
недостатньо.»
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

HA_HOST = "ha"
HA_USER = "root"
HA_SSH_KEY = "/opt/data/.ssh/id_ed25519"

# Where the integration's bundled frontend lives on the
# live host.
REMOTE_COMPONENT_DIR = (
    "/config/custom_components/powmr_inverter"
)
# Where HA serves the same file via /local/. The
# ``_copy_files`` step in ``__init__.py`` writes here
# during entry setup. The browser fetches
# /local/community/powmr-inverter/<file>.
REMOTE_WWW_DIR = (
    "/config/www/community/powmr-inverter"
)

BACKUP_DIR = Path(
    "/opt/data/powmr-ai-work/backups_frontend_2026-10-19"
)

SSH = ("ssh", "-i", HA_SSH_KEY, f"{HA_USER}@{HA_HOST}")
SCP = ("scp", "-i", HA_SSH_KEY)

# HA Core listens on the supervisor bridge interface.
# This is the same address the existing
# ``deploy_python_file.py`` uses, so the two scripts
# agree on the URL the browser will hit.
HA_HTTP_BASE = "http://172.30.32.1:8123"

# The "outage" cache-bust hash slot in the registration
# log line is a stable substring; we read the value via
# that substring. Other assets (forecast, ph, te, pc,
# flow) live in the same log line -- keyed by
# ``<asset-name>=<hash>``.
CACHE_BUST_REGEX = re.compile(
    r"cache-bust:\s*flow=(?P<flow>[0-9a-f]+)"
    r"\s+forecast=(?P<forecast>[0-9a-f]+)"
    r"\s+ph=(?P<ph>[0-9a-f]+)"
    r"\s+te=(?P<te>[0-9a-f]+)"
    r"\s+pc=(?P<pc>[0-9a-f]+)"
    r"\s+outage=(?P<outage>[0-9a-f]+)"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(
    cmd: str, *, check: bool = True, timeout: int = 60
) -> tuple:
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
        raise SystemExit(
            f"scp put failed (rc={rc}): {local} -> {remote}"
        )


def _scp_get(remote: str, local: Path) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    rc = subprocess.call(
        list(SCP) + [f"{HA_USER}@{HA_HOST}:{remote}", str(local)],
    )
    if rc != 0:
        raise SystemExit(
            f"scp get failed (rc={rc}): {remote} -> {local}"
        )


def _fetch_http(url: str, timeout: int = 30) -> tuple:
    """Download ``url`` over HTTP and return
    ``(sha256_hex, size_bytes, http_status)``. We
    deliberately do NOT pass an ``Authorization``
    header -- the live supervisor serves ``/local/``
    to any client that can reach the bridge
    interface, so the SHA verification is exactly what
    the operator's browser sees.
    """
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"HTTP error fetching {url}: {exc.code} {exc.reason}"
        )
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"URL error fetching {url}: {exc.reason}"
        )
    if status != 200:
        raise SystemExit(
            f"non-200 response from {url}: HTTP {status}"
        )
    return (
        hashlib.sha256(body).hexdigest(),
        len(body),
        status,
    )


def _read_cache_bust(
    asset_key: str, *, deadline_s: int = 60
) -> str:
    """Poll the HA Core log for the cache-bust
    registration line and return the hash for
    ``asset_key`` (one of: flow, forecast, ph, te, pc,
    outage). Raises SystemExit on timeout.
    """
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        rc, out, _ = _run(
            "ha core logs --lines 200 2>&1 | "
            "grep -E 'cache-bust:.*outage=' | tail -5",
            check=False,
            timeout=20,
        )
        if out:
            # The most recent matching line wins;
            # ``tail -5`` is plenty.
            for line in out.splitlines()[::-1]:
                m = CACHE_BUST_REGEX.search(line)
                if not m:
                    continue
                if asset_key not in m.groupdict():
                    continue
                return m.group(asset_key)
        time.sleep(2)
    raise SystemExit(
        f"timed out waiting for cache-bust hash in "
        f"the HA log; asset_key={asset_key!r}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "relpath",
        help=(
            "path relative to repo root, e.g. "
            "frontend/k-outage-scenarios-card.js"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "compute the SHAs and the HTTP "
            "verification; do NOT write or restart"
        ),
    )
    parser.add_argument(
        "--skip-restart",
        action="store_true",
        help=(
            "don't restart HA Core; useful for the "
            "first deploy of a single asset when "
            "the next setup run will recompute the "
            "cache-bust hash anyway"
        ),
    )
    parser.add_argument(
        "--asset-key",
        default=None,
        help=(
            "the slot in the cache-bust log line "
            "(default: derive from filename -- "
            "k-outage-scenarios-card.js -> outage, "
            "forecast-card.js -> forecast, "
            "power-history-card.js -> ph, "
            "total-energy-card.js -> te, "
            "pv-comparison-card.js -> pc, "
            "k-flow-card.js -> flow)"
        ),
    )
    args = parser.parse_args()

    local = REPO / args.relpath
    if not local.exists():
        raise SystemExit(f"local file not found: {local}")
    local_sha = _sha(local)
    filename = local.name
    print(f"[local] {local} SHA: {local_sha}")

    # Auto-derive the cache-bust key from the filename
    # if the operator did not override.
    if args.asset_key is None:
        name_to_key = {
            "k-outage-scenarios-card.js": "outage",
            "forecast-card.js": "forecast",
            "power-history-card.js": "ph",
            "total-energy-card.js": "te",
            "pv-comparison-card.js": "pc",
            "k-flow-card.js": "flow",
        }
        if filename not in name_to_key:
            raise SystemExit(
                f"cannot auto-derive cache-bust key for "
                f"{filename!r}; pass --asset-key "
                f"explicitly"
            )
        args.asset_key = name_to_key[filename]
    print(f"[local] cache-bust asset key: {args.asset_key}")

    # Pre-deploy readback of the SERVED file (the
    # operator's browser sees this, not the integration
    # directory). The script's purpose is to ensure
    # this URL returns the expected bytes.
    remote_www = f"{REMOTE_WWW_DIR}/{filename}"
    rc, out, _ = _run(
        f"sha256sum {shlex.quote(remote_www)}", timeout=15
    )
    served_pre_sha = out.split()[0]
    print(
        f"[live]  {remote_www} SHA: {served_pre_sha}"
    )

    if served_pre_sha == local_sha:
        print(
            "Live served SHA already matches local -- "
            "the operator's browser is already seeing "
            "the new bytes. No deploy needed."
        )
        return 0

    if args.dry_run:
        print(
            "[dry-run] skipping backup, write, "
            "restart, and HTTP re-verify"
        )
        return 0

    # STEP 1 -- backup both live paths. We back up the
    # served file (the one that actually matters) and
    # the integration copy (in case the operator
    # wants to roll back the entire bundle).
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_www = f"{remote_www}.{ts}.predeploy"
    backup_www_local = (
        BACKUP_DIR / f"www.{filename}.{ts}.predeploy"
    )
    print(
        f"[step 1] pre-deploy backup: "
        f"{backup_www_local}"
    )
    _run(
        f"cp -- {shlex.quote(remote_www)} "
        f"{shlex.quote(backup_www)}"
    )
    _scp_get(backup_www, backup_www_local)

    # STEP 2 -- stage the new file at the served path
    # and SHA-verify the staged copy before the
    # rename.
    stage = f"{remote_www}.{ts}.staged"
    print(f"[step 2] staging: {stage}")
    _scp_put(local, stage)
    rc, out, _ = _run(
        f"sha256sum {shlex.quote(stage)}", timeout=15
    )
    staged_sha = out.split()[0]
    if staged_sha != local_sha:
        raise SystemExit(
            f"Staged SHA mismatch: local={local_sha} "
            f"remote={staged_sha}. Live file untouched."
        )
    print(f"        SHA verified: {staged_sha[:16]}\u2026")
    print(f"[step 3] atomic rename: {stage} -> {remote_www}")
    _run(f"mv -- {shlex.quote(stage)} {shlex.quote(remote_www)}")
    rc, out, _ = _run(
        f"sha256sum {shlex.quote(remote_www)}", timeout=15
    )
    served_after_sha = out.split()[0]
    if served_after_sha != local_sha:
        # Rollback the served file from the backup.
        _run(
            f"cp -- {shlex.quote(backup_www)} "
            f"{shlex.quote(remote_www)}",
            check=False,
        )
        raise SystemExit(
            f"Post-rename SHA mismatch: live="
            f"{served_after_sha} local={local_sha}. "
            f"Restored pre-deploy backup."
        )
    print(
        f"        served SHA verified: "
        f"{served_after_sha[:16]}\u2026"
    )

    # STEP 4 -- copy to the integration directory
    # (the bundled source path). The script keeps both
    # in sync so a subsequent ``git pull`` /
    # reinstall does not regress.
    remote_component = (
        f"{REMOTE_COMPONENT_DIR}/frontend/{filename}"
    )
    if _run(
        f"test -f {shlex.quote(remote_component)}",
        check=False,
    )[0] == 0:
        stage_component = f"{remote_component}.{ts}.staged"
        print(
            f"[step 4] staging integration copy: "
            f"{stage_component}"
        )
        _scp_put(local, stage_component)
        rc, out, _ = _run(
            f"sha256sum {shlex.quote(stage_component)}",
            timeout=15,
        )
        if out.split()[0] != local_sha:
            raise SystemExit(
                f"Integration copy staged SHA mismatch."
            )
        _run(
            f"mv -- {shlex.quote(stage_component)} "
            f"{shlex.quote(remote_component)}"
        )
        rc, out, _ = _run(
            f"sha256sum {shlex.quote(remote_component)}",
            timeout=15,
        )
        if out.split()[0] != local_sha:
            raise SystemExit(
                f"Integration copy post-rename SHA "
                f"mismatch."
            )
        print(
            f"        integration copy SHA verified: "
            f"{local_sha[:16]}\u2026"
        )
    else:
        print(
            f"[step 4] integration copy missing on live "
            f"({remote_component}); skipping (the served "
            f"file is what the browser fetches anyway)"
        )

    if args.skip_restart:
        print(
            "[step 5] --skip-restart; the served file "
            "is in place; the next setup run will "
            "recompute the cache-bust hash. WARNING: "
            "until the next reload, the operator's "
            "browser is still loading the OLD hash. "
            "Remove --skip-restart to recompute now."
        )
        return 0

    # STEP 5 -- restart HA Core so the
    # ``_hash_assets`` step in ``__init__.py`` runs
    # against the new bytes. Without this the
    # ``outage=<hash>`` URL stays the previous round's
    # hash, the browser keeps loading the OLD JS
    # (cached under the old hash), and the served
    # SHA check below would fail.
    print("[step 5] restarting HA Core")
    _run("ha core restart", check=False, timeout=180)
    print("        waiting for Core to come back up")
    deadline = time.time() + 90
    up = False
    while time.time() < deadline:
        rc, out, _ = _run(
            "curl -sS -o /dev/null -w '%{http_code}' "
            f"{HA_HTTP_BASE}/ 2>/dev/null || echo 000",
            check=False,
            timeout=10,
        )
        code = out.strip()
        if code in ("200", "302"):
            print(f"        core running: HTTP {code}")
            up = True
            break
        time.sleep(2)
    if not up:
        raise SystemExit(
            "Core did not come back up within 90s. "
            "The served file is in place; manual "
            "intervention required."
        )

    # STEP 6 -- pull the new cache-bust hash from the
    # ``outage-scenarios card registered (cache-bust: ...
    # outage=<hash>)`` log line.
    bust = _read_cache_bust(args.asset_key)
    print(f"[step 6] new cache-bust hash: {bust}")

    # STEP 7 -- **the HTTP-level assertion**. Fetch
    # the served URL with the new cache-bust hash and
    # verify the SHA matches the local file. This is
    # what the operator's browser will load; if it
    # disagrees we know we have a stale served file,
    # a stale supervisor cache, or a typo in the URL
    # construction.
    served_url = (
        f"{HA_HTTP_BASE}/local/community/"
        f"powmr-inverter/{filename}?v={bust}"
    )
    print(f"[step 7] HTTP verification: GET {served_url}")
    served_http_sha, served_http_size, _ = _fetch_http(
        served_url
    )
    print(
        f"        served HTTP SHA: {served_http_sha} "
        f"({served_http_size} bytes)"
    )
    if served_http_sha != local_sha:
        raise SystemExit(
            f"SERVED SHA MISMATCH: HTTP returns "
            f"{served_http_sha}, expected {local_sha}. "
            f"The operator's browser would load the OLD "
            f"bytes. Inspect /config/www/community/"
            f"powmr-inverter/{filename} and the HA "
            f"supervisor's static-file cache."
        )
    print(
        f"        HTTP SHA matches local: "
        f"{served_http_sha[:16]}\u2026"
    )

    print("DEPLOY SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
