"""Authenticated SMB shares listing for the core_samba addon.

Юра (2026-10-13): «Запусти Samba, перевір startup-лог і
автентифіковане читання списку shares. Відкритого порту
недостатньо.»

Reads the new password from
``/data/.hermes/addon_secrets/core_samba.password`` (mode 600,
root-only) on the HA host, then connects to the samba
server over SMB and lists the shares that the
``homeassistant`` user can see. The password is never
written to stdout, logs, or Git.
"""
from __future__ import annotations

import os
import sys

HA_HOST = "192.168.1.220"
USERNAME = "homeassistant"
EXPECTED_SHARES = {
    "local_apps", "app_configs", "backup", "config", "media",
    "share", "ssl",
}


def main() -> int:
    password = os.environ.get("PSMB")
    if not password:
        print("FATAL: set PSMB env var to the samba password", file=sys.stderr)
        return 1
    if len(password) != 32:
        print(f"FATAL: password length {len(password)} != 32", file=sys.stderr)
        return 1

    from smbclient import scandir  # noqa: E402
    print(f"connecting to smb://{HA_HOST}:445 as {USERNAME} (password length 32, value redacted)")
    try:
        # ``scandir`` needs a specific share in the path. We
        # probe each expected share with an authenticated
        # listing. A successful listing for every share means
        # the new password is accepted and every share is
        # served. As a side effect we also assert that the
        # expected share names are configured — anything we
        # can list is, by construction, in ``enabled_shares``.
        ok_shares: set[str] = set()
        for share in sorted(EXPECTED_SHARES):
            try:
                for _ in scandir(
                    f"\\\\{HA_HOST}\\{share}",
                    username=USERNAME, password=password,
                ):
                    pass  # only need to confirm listing succeeds
                ok_shares.add(share)
            except Exception as exc:
                print(
                    f"FAIL: share {share!r} listing raised "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                return 2
    except Exception as exc:
        print(f"FAIL: unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"shares accessible to {USERNAME}: {sorted(ok_shares)}")
    missing = EXPECTED_SHARES - ok_shares
    if missing:
        print(f"FAIL: missing expected shares: {sorted(missing)}", file=sys.stderr)
        return 3
    print("OK: authenticated listing succeeded for every expected share")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
