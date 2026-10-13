#!/bin/bash
# Rotate the core_ssh addon's password without exposing the
# new value to stdout, chat, logs, or Git. The new password
# is generated locally, written to the Supervisor addon
# options via curl with --data-binary (read from a
# freshly-written JSON file so the password never appears in
# the command line), and the SSH-KEY auth path is left
# intact. The supervisor middleware is re-validated with a
# short sleep between requests to avoid the 403 race we
# saw earlier.
set -euo pipefail
unset HISTFILE
export HISTFILE=/dev/null

# Generate password in shell variable, never printed.
PSSH=$(head -c 32 /dev/urandom | base64 | tr -d "/+=" | head -c 32)
if [ "${#PSSH}" -ne 32 ]; then
  echo "FATAL: password length != 32" >&2
  exit 1
fi

# Operator's existing authorized_keys list (kept verbatim).
AUTH_KEYS='["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAHGL8b3r+k6uEv+fmQtUwQymzyoPByR66vTG4hG5It1 hermes-nas-pc"]'

# Build the JSON payload in a file, not on the command line.
# The file contains the password but is never echoed.
cat > /tmp/core_ssh_options.json <<JSON
{"options":{"authorized_keys":${AUTH_KEYS},"password":"${PSSH}","apks":[],"server":{"tcp_forwarding":true}}}
JSON
unset PSSH AUTH_KEYS

echo "[core_ssh] POST options (password length 32, value redacted)" >&2
# Sleep first so any in-flight Supervisor middleware is idle.
sleep 2
curl -sS -X POST \
  -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/core_ssh_options.json \
  -o /tmp/core_ssh_post.json \
  -w "post_http=%{http_code}\n" \
  http://supervisor/addons/core_ssh/options
echo "post response (truncated, no secret):"
head -c 200 /tmp/core_ssh_post.json
echo
RESULT=$(jq -r '.result' /tmp/core_ssh_post.json 2>/dev/null || echo "parse_error")
echo "post result: ${RESULT}"

echo "[readback] verifying options (no secret)" >&2
sleep 2
curl -sS -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
  -o /tmp/core_ssh_readback.json \
  -w "readback_http=%{http_code}\n" \
  http://supervisor/addons/core_ssh/info
echo "readback (no secret):"
jq '{authorized_keys_present: ((.data.options.authorized_keys // []) | length > 0), password_len: ((.data.options.password // "") | length), apks: .data.options.apks, server: .data.options.server}' /tmp/core_ssh_readback.json

echo "[restart] core_ssh (will drop this SSH connection)" >&2
sleep 2
curl -sS -X POST \
  -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
  -o /tmp/core_ssh_restart.json \
  -w "restart_http=%{http_code}\n" \
  http://supervisor/addons/core_ssh/restart
head -c 200 /tmp/core_ssh_restart.json
echo
echo OK >&2
