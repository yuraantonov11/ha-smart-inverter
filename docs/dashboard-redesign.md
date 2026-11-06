# Smart Solar dashboard redesign

The daily dashboard has four native Home Assistant views: Огляд, Аналітика,
Автоматика, Система. The existing animated energy-flow card stays first, with
`compact: true` hiding its duplicated readings and daily totals. The interface
inherits the active HA theme; it has no separate theme selector.

The overview identifies diagnostic Bluetooth readings separately from cloud
control data and shows reported/control SOC disagreement. Forecast quality has
a curated detail view. Manual controls keep local drafts and call services only
after Apply; sent commands and telemetry confirmation have different statuses.
Options use six native category forms, preserving all existing keys and the BLE
configuration-file fallback. Charts and outage forms adapt to their containers.

## Existing installations

Updates do not replace customized dashboards automatically. To request one
migration, create `/config/powmr_dashboard_redesign.request` containing:

```json
{"entry_id":"YOUR_CONFIG_ENTRY_ID","dashboard_path":"YOUR_BOUND_DASHBOARD_URL"}
```

The entry and dashboard binding must match exactly. On the next integration
setup, the migration reads and saves through HA's live Lovelace API, backs up the
current configuration and resource URLs under `/config/backups/smart_solar/`,
verifies read-back, and removes the request only after success. A failed save or
verification attempts to restore the original panel and resource URLs. See
`powmr_dashboard_redesign.result.json` for the successful migration result.

Every original integration view remains reachable as a detail subview; unrelated
user views remain unchanged. An idempotent version marker preserves later user
edits. First installation uses the same pure builder. The manual-install YAML
template can be regenerated with `scripts/build_dashboard_template.py`.

This is a presentation change: HEMS algorithms, control SOC, source policies,
battery limits, command dispatcher and stored energy statistics are unchanged.
