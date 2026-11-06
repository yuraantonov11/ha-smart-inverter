"""Explicit, backed-up migration through HA's live Lovelace storage API."""
from __future__ import annotations

import json
import logging
import hashlib
from urllib.parse import urlsplit
from datetime import datetime, timezone
from pathlib import Path

from .dashboard_redesign import build_redesigned_dashboard

_LOGGER = logging.getLogger(__name__)


async def async_apply_requested_redesign(hass, entry):
    request_file = Path(hass.config.path("powmr_dashboard_redesign.request"))
    if not await hass.async_add_executor_job(request_file.exists):
        return
    panel = None
    current = None
    save_attempted = False
    resource_updates = []
    resources = None
    try:
        request = json.loads(await hass.async_add_executor_job(request_file.read_text, "utf-8"))
        if request.get("entry_id") != entry.entry_id:
            return
        url = entry.options.get("lovelace_dashboard_url_path")
        if not url or request.get("dashboard_path") != url:
            raise ValueError("Redesign request does not match this entry's dashboard binding")
        lovelace = hass.data.get("lovelace")
        dashboards = getattr(lovelace, "dashboards", None)
        if dashboards is None and isinstance(lovelace, dict):
            dashboards = lovelace.get("dashboards")
        panel = (dashboards or {}).get(url)
        if panel is None or not callable(getattr(panel, "async_save", None)):
            raise ValueError("Bound storage dashboard is not available")
        from homeassistant.helpers import entity_registry as er
        registry = er.async_get(hass)
        mapping = {}
        for entity in registry.entities.values():
            if entity.config_entry_id != entry.entry_id:
                continue
            if entity.translation_key:
                mapping[entity.translation_key] = entity.entity_id
            uid = entity.unique_id or ""
            if "_ble_diag_" in uid:
                mapping["ble_" + uid.split("_ble_diag_", 1)[1]] = entity.entity_id
            prefix = entry.entry_id + "_predictive_"
            if uid.startswith(prefix):
                mapping["predictive_" + uid[len(prefix):]] = entity.entity_id
        current = await panel.async_load(False)
        candidate, summary = build_redesigned_dashboard(current, mapping, dashboard_path=url)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = Path(hass.config.path("backups", "smart_solar", f"{url}-{stamp}.json"))

        def save_backup():
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

        await hass.async_add_executor_job(save_backup)
        # Pinned resource URLs otherwise keep serving the previous cached JS.
        resources = getattr(lovelace, "resources", None)
        if resources is not None and callable(getattr(resources, "async_update_item", None)):
            await resources.async_get_info()
            items = list(resources.async_items())
            resource_backup = backup.with_suffix(".resources.json")
            await hass.async_add_executor_job(resource_backup.write_text, json.dumps(items, ensure_ascii=False, indent=2), "utf-8")
            for item in items:
                resource_url = item.get("url", "")
                if not resource_url.startswith("/") or resource_url.startswith("//"):
                    continue
                path = urlsplit(resource_url).path
                allowed = {"k-flow-card.js", "pv-comparison-card.js", "power-history-card.js", "total-energy-card.js", "k-outage-scenarios-card.js", "smart-solar-controls-card.js"}
                if not path.startswith("/local/community/powmr-inverter/") or path.rsplit("/", 1)[-1] not in allowed:
                    continue
                local = Path(hass.config.path("www", "community", "powmr-inverter", path.rsplit("/", 1)[-1]))
                digest = hashlib.sha256(await hass.async_add_executor_job(local.read_bytes)).hexdigest()[:12]
                url_new = f"{path}?v={digest}"
                if url_new != item["url"]:
                    resource_updates.append((item["id"], item["url"]))
                    await resources.async_update_item(item["id"], {"url": url_new})
        if summary["changed"]:
            save_attempted = True
            await panel.async_save(candidate)
            verified = await panel.async_load(False)
            if verified != candidate:
                raise RuntimeError("Lovelace read-back differed")
        result = {"summary": summary, "backup": str(backup), "dashboard_path": url, "completed_at": stamp}
        result_file = Path(hass.config.path("powmr_dashboard_redesign.result.json"))
        await hass.async_add_executor_job(result_file.write_text, json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
        await hass.async_add_executor_job(request_file.unlink)
        _LOGGER.info("Smart Solar dashboard redesigned: %s; backup %s", summary, backup)
    except Exception:
        for item_id, original_url in reversed(resource_updates):
            try:
                await resources.async_update_item(item_id, {"url": original_url})
            except Exception:
                _LOGGER.exception("Could not restore resource %s", item_id)
        if save_attempted and panel is not None and current is not None:
            try:
                await panel.async_save(current)
            except Exception:
                _LOGGER.exception("Dashboard rollback failed; original remains in the backup")
        _LOGGER.exception("Dashboard redesign not completed; request retained for retry")
