#!/bin/bash
# Build the JSON payload from the current addon options
# (kept in memory in the parent script) plus a new password
# read from the secret file. The password is interpolated
# into the JSON via shell variable expansion of $PSMB; the
# variable itself is unset at the end and never printed.
set -euo pipefail
PSMB="$(cat /data/.hermes/addon_secrets/core_samba.password)"
cat > /tmp/core_samba_options.json <<JSON
{
  "options": {
    "username": "homeassistant",
    "password": "${PSMB}",
    "workgroup": "WORKGROUP",
    "enabled_shares": ["local_apps", "app_configs", "backup", "config", "media", "share", "ssl"],
    "compatibility_mode": false,
    "apple_compatibility_mode": true,
    "netbios": true,
    "local_master": false,
    "network_discovery": true,
    "server_signing": "default",
    "veto_files": ["._*", ".DS_Store", "Thumbs.db", "icon?", ".Trashes"],
    "allow_hosts": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "fe80::/10", "fc00::/7"]
  }
}
JSON
unset PSMB
# POST with the JSON file as the request body. The password
# value is in the file but never appears on the command line.
curl -sS -X POST \
  -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/core_samba_options.json \
  -o /tmp/core_samba_post.json \
  -w "post_http=%{http_code}\n" \
  http://supervisor/addons/core_samba/options
echo "post response (truncated, no secret):"
head -c 200 /tmp/core_samba_post.json
echo
# Sanity: the response must be {"result":"ok",...}.
RESULT=$(jq -r '.result' /tmp/core_samba_post.json 2>/dev/null || echo "parse_error")
echo "post result: ${RESULT}"
# Readback: confirm the new password length is 32.
sleep 1
curl -sS -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
  -o /tmp/core_samba_readback.json \
  -w "readback_http=%{http_code}\n" \
  http://supervisor/addons/core_samba/info
echo "readback (no secret):"
jq '{state: .data.state, password_len: ((.data.options.password // "") | length), username: .data.options.username, workgroup: .data.options.workgroup, enabled_shares: .data.options.enabled_shares, allow_hosts: .data.options.allow_hosts, compatibility_mode: .data.options.compatibility_mode, apple_compatibility_mode: .data.options.apple_compatibility_mode, netbios: .data.options.netbios, local_master: .data.options.local_master, network_discovery: .data.options.network_discovery, server_signing: .data.options.server_signing, veto_files: .data.options.veto_files}' /tmp/core_samba_readback.json
# Start the addon.
echo "starting core_samba"
curl -sS -X POST \
  -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
  -o /tmp/core_samba_start.json \
  -w "start_http=%{http_code}\n" \
  http://supervisor/addons/core_samba/start
head -c 200 /tmp/core_samba_start.json
echo
