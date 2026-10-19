"""Idempotent dashboard-view patcher (DEFECT 6).

The "Відключення" view is part of the
``_auto_install_dashboard`` output. On a FRESH
install, ``_auto_install_dashboard`` runs and
includes the view -- this hook is a no-op.
On an EXISTING custom dashboard, the operator
already has 5 (or N) views. We append the
"Відключення" view without disturbing the
others.

Idempotency contract:

* On every entry setup, look up the default
  dashboard via the Lovelace API.
* If the "Відключення" view is already present
  (by path or title) -- no-op.
* If the lookup fails (no Lovelace component,
  YAML mode, recovery mode, etc.) -- log and
  no-op. The operator's data is never
  clobbered.
* If the view is missing -- make a timestamped
  backup of the on-disk JSON (belt-and-braces;
  ``async_save`` itself uses HA's storage
  helper which has its own atomicity), append
  the view to the in-memory config, and
  ``async_save`` the result. The five existing
  views are preserved verbatim.

The previous round (round 3) parsed
``data.data.views`` (wrong -- the live file has
``data.config.views``) and bypassed the
Lovelace service entirely, so the patch never
applied on a real dashboard. This module uses
the standard
``hass.data["lovelace"]["dashboards"][None]``
``LovelaceStorage`` API (NOT direct file
writes).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone


_LOGGER = logging.getLogger(__name__)


# The view dict the operator's dashboard gets
# when the patch applies. ``entry_id`` is
# filled in by ``_ensure_outages_view`` at
# call time so the patch matches the
# operator's entry.
_OUTAGES_VIEW_DICT: dict = {
    "title": "Відключення",
    "path": "powmr-outages",
    "icon": "mdi:calendar-alert",
    "type": "sections",
    "max_columns": 2,
    "sections": [
        {
            "type": "grid",
            "cards": [
                {
                    "type": "custom:k-outage-scenarios-card",
                    "entry_id": None,
                }
            ],
        }
    ],
}


async def _ensure_outages_view(hass, entry) -> None:
    """Append the Відключення view to the
    operator's existing dashboard if it is not
    already there, using the standard Lovelace
    API.

    ``entry`` is a ``ConfigEntry``-shaped object
    that exposes ``.entry_id``.
    """
    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is None:
        _LOGGER.debug(
            "dashboard patcher: lovelace not "
            "loaded; skipping the Відключення patch"
        )
        return
    # Modern HA: ``hass.data["lovelace"]`` is a
    # ``LovelaceData`` dataclass with a
    # ``dashboards`` mapping keyed by url_path.
    # Legacy: it's a plain dict. Handle both.
    dashboards = getattr(lovelace_data, "dashboards", None)
    if dashboards is None and isinstance(lovelace_data, dict):
        dashboards = lovelace_data.get("dashboards")
    if not dashboards:
        _LOGGER.debug(
            "dashboard patcher: lovelace dashboards "
            "not available; skipping the patch"
        )
        return
    # The visible operator panel is
    # ``dashboards["powmr-energy"]`` -- this is
    # the standalone Smart Solar dashboard the
    # operator sees in the sidebar. Other panels
    # in ``dashboards`` (e.g. ``powmr-b3401a3c4784131b``
    # -- a per-entry sidecar that the operator
    # does not interact with) MUST stay unchanged.
    # The default dashboard (key ``None``) is
    # ``config_not_found`` on this live system
    # because HA's storage mode keeps the
    # operator's content in a named storage
    # file (``lovelace.powmr_energy``), not the
    # unnamed default. The patcher must resolve
    # the named panel, not the default.
    default_dashboard = dashboards.get("powmr-energy")
    if default_dashboard is None:
        _LOGGER.debug(
            "dashboard patcher: no 'powmr-energy' "
            "panel registered; skipping the patch "
            "(the operator may have renamed it)"
        )
        return
    # YAML-mode dashboards do not support
    # ``async_save`` (raises HomeAssistantError).
    # Storage-mode dashboards have a working
    # ``async_save``.
    async_save = getattr(default_dashboard, "async_save", None)
    if async_save is None:
        _LOGGER.debug(
            "dashboard patcher: dashboard has no "
            "async_save (likely YAML mode); skipping"
        )
        return
    # Read the current config. The
    # ``LovelaceStorage`` instance caches the
    # parsed config in its ``.config`` attribute
    # (set by ``__init__``); ``async_load`` is
    # only needed on a fresh read. We prefer the
    # cached ``.config`` because it bypasses the
    # ``ConfigNotFound`` exception path that
    # ``async_load`` raises when the config dict
    # is ``None`` (e.g. in auto-gen mode on a
    # fresh install).
    current_config = getattr(
        default_dashboard, "config", None
    )
    if current_config is None:
        try:
            current_config = await default_dashboard.async_load(False)
        except Exception as exc:  # noqa: BLE001
            # ``LovelaceStorage.async_load`` raises
            # ``ConfigNotFound`` when the storage
            # has no ``config`` key. The exception
            # string is empty (``str(exc) == ""``)
            # on this HA version -- log the
            # exception class name as a fallback.
            _LOGGER.debug(
                "dashboard patcher: "
                "dashboard.async_load failed (%r); "
                "skipping the patch",
                exc,
            )
            return
    if not isinstance(current_config, dict):
        return
    views = current_config.get("views")
    if not isinstance(views, list):
        return
    # Idempotency: if any view already has the
    # right path or title, no-op. Do NOT
    # reorder or duplicate.
    for v in views:
        if not isinstance(v, dict):
            continue
        if v.get("path") == "powmr-outages":
            return
        if v.get("title") == "Відключення":
            return
    # Make a timestamped backup of the on-disk
    # JSON before ``async_save`` (which uses
    # the storage helper's own atomic
    # write-then-rename but we want a human
    # readable snapshot too).
    storage_dir = os.path.join(
        hass.config.config_dir, ".storage"
    )
    dashboard_path = os.path.join(
        storage_dir, "lovelace.powmr_energy"
    )
    backup_path = None
    if os.path.exists(dashboard_path):
        backup_path = "{}.bak.{}".format(
            dashboard_path,
            datetime.now(timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ"
            ),
        )
        try:
            shutil.copy2(dashboard_path, backup_path)
        except OSError as exc:
            _LOGGER.warning(
                "dashboard patcher: could not back up "
                "dashboard JSON to %s (%s); skipping "
                "the Відключення patch",
                backup_path,
                exc,
            )
            return
    # Build the new view, fill in the
    # per-entry ``entry_id`` so the card talks
    # to the operator's config entry.
    view_to_add = json.loads(json.dumps(_OUTAGES_VIEW_DICT))
    view_to_add["sections"][0]["cards"][0]["entry_id"] = (
        entry.entry_id
    )
    # Append the view. The five existing views
    # are preserved verbatim (we copy
    # ``current_config`` and only add the new
    # entry at the end of ``views``).
    new_config = {
        **current_config,
        "views": list(views) + [view_to_add],
    }
    try:
        await async_save(new_config)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning(
            "dashboard patcher: dashboard.async_save "
            "failed (%s); the operator's dashboard is "
            "unchanged",
            exc,
        )
        return
    _LOGGER.info(
        "dashboard patcher: appended the "
        "Відключення view to the default dashboard "
        "via the Lovelace API (backup: %s, existing "
        "views preserved: %d)",
        backup_path or "(no on-disk file)",
        len(views),
    )
