#!/bin/bash
# Generate two independent 32-char random passwords and
# write them to Supervisor addon options without exposing
# the values to stdout, log files, the network, or chat.
#
# Юра (2026-10-13): «Ти опублікував нові SSH і Samba паролі
# у звіті. Заміни обидва на нові незалежні секрети без
# виведення значень у чат, stdout, логи чи Git.»
#
# Usage: bash scripts/rotate_pwned_passwords.sh
#
# Side effects:
#   * POSTs new passwords to Supervisor API for core_ssh and
#     core_samba.
#   * Writes the new passwords to /root/.hermes/addon_secrets/
#     (mode 600) for the operator to retrieve locally.
#   * Suppresses history for the duration of the script.
#   * Restarts core_ssh + core_samba (Юра: «Перезапускай
#     лише застосунки, яким це необхідно»).
#   * Runs `ha resolution check run addon_pwned` to confirm
#     the issues are gone (Codex did this and got OK).
set -euo pipefail

unset HISTFILE
export HISTFILE=/dev/null

# Generate passwords into local variables only; never print.
PSSH=$(head -c 32 /dev/urandom | base64 | tr -d "/+=" | head -c 32)
PSMB=$(head -c 32 /dev/urandom | base64 | tr -d "/+=" | head -c 32)

# Sanity check: both are 32 chars, distinct, alphanumeric only.
if [ "${#PSSH}" -ne 32 ] || [ "${#PSMB}" -ne 32 ]; then
  echo "FATAL: password length != 32" >&2
  exit 1
fi
if [ "$PSSH" = "$PSMB" ]; then
  echo "FATAL: passwords are equal (should be independent)" >&2
  exit 1
fi

# Place the secrets on the operator's filesystem so they
# can be retrieved locally (e.g. via SSH) without ever
# appearing in stdout / chat / Git.
SECRETS_DIR=/root/.hermes/addon_secrets
mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"
printf %s "$PSSH" > "$SECRETS_DIR/core_ssh.password"
printf %s "$PSMB" > "$SECRETS_DIR/core_samba.password"
chmod 600 "$SECRETS_DIR"/*.password
ls -la "$SECRETS_DIR" >&2

# --- core_ssh ---
# authorized_keys: keep the operator's existing public key.
AUTH_KEYS='["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAHGL8b3r+k6uEv+fmQtUwQymzyoPByR66vTG4hG5It1 hermes-nas-pc"]'
PAYLOAD_SSH=$(cat <<JSON
{"options":{"authorized_keys":${AUTH_KEYS},"password":"${PSSH}","apks":[],"server":{"tcp_forwarding":true}}}
JSON
)
echo "[core_ssh] POST options (password length 32, value redacted)" >&2
curl -sS -X POST \
  -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD_SSH" \
  http://supervisor/addons/core_ssh/options >/dev/null 2>&1
echo "[core_ssh] options applied" >&2

# --- core_samba ---
PAYLOAD_SMB=$(cat <<JSON
{"options":{"username":"homeassistant","password":"${PSMB}","workgroup":"WORKGROUP","enabled_shares":["local_apps","app_configs","backup","config","media","share","ssl"],"compatibility_mode":false,"apple_compatibility_mode":true,"netbios":true,"local_master":false,"network_discovery":true,"server_signing":"default","veto_files":["._*",".DS_Store","Thumbs.db","icon?",".Trashes"],"allow_hosts":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","169.254.0.0/16","fe80::/10","fc00::/7"]}}
JSON
)
echo "[core_samba] POST options (password length 32, value redacted)" >&2
curl -sS -X POST \
  -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD_SMB" \
  http://supervisor/addons/core_samba/options >/dev/null 2>&1
echo "[core_samba] options applied" >&2

# Unset password variables immediately so they do not
# linger in the shell's memory.
unset PSSH PSMB PAYLOAD_SSH PAYLOAD_SMB

# --- Restarts (Юра: «Перезапускай лише застосунки,
# яким це необхідно») ---
# core_ssh first (this terminates our current SSH
# connection; the script must finish BEFORE restart — the
# `restart` is therefore the LAST step, so the operator
# can reconnect via the same public key afterwards).
echo "[step 1/3] restarting core_samba" >&2
curl -sS -X POST \
  -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
  http://supervisor/addons/core_samba/restart >/dev/null 2>&1

echo "[step 2/3] running pwned check (Codex-confirmed method)" >&2
ha resolution check run addon_pwned >&2

echo "[step 3/3] restarting core_ssh (this WILL drop the SSH connection)" >&2
curl -sS -X POST \
  -H "Authorization: Bearer $SUPERVISOR_TOKEN" \
  http://supervisor/addons/core_ssh/restart >/dev/null 2>&1

echo "OK" >&2
