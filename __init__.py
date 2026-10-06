"""The Smart Solar Inverter integration."""

from __future__ import annotations

__version__ = "1.8.13-perf-fixes"
"""Bumped to surface applied patches in HA UI Devices panel.
Tracks local-only fixes (5 patches applied 2026-07-07); HACS version stays 1.8.12."""

import hashlib
import logging
import json
import os
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .api import InverterApiClient
from .const import (
    DEFAULT_POLL_INTERVAL_SEC,
    DOMAIN,
    MIN_POLL_INTERVAL_SEC,
)
from .coordinator import InverterCoordinator, HistoryCoordinator
from .hems.options_helpers import (
    RELOAD_REQUIRED_OPTION_KEYS as _RELOAD_REQUIRED_OPTION_KEYS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
]

# T16 audit: the set of option keys that
# require a full setup reload. Other keys are
# applied selectively (the coordinator reads
# them from ``entry.options`` on every cycle)
# and do not trigger a reload. The
# canonical definition lives in
# ``hems.options_helpers``; the symbol is
# re-exported as ``_RELOAD_REQUIRED_OPTION_KEYS``
# via the import above. The
# ``test_16_06`` suite asserts the
# equality between this binding, the
# source values in ``const.py`` and
# ``hems/defaults.py``.

# T16 audit: keys that are persisted by
# internal state machines and must not be
# surfaced in the user-facing config flow.
# Writing to these keys must not trigger a
# reload — the coordinator re-reads them on
# every cycle.
_INTERNAL_PERSISTENCE_KEYS: frozenset[str] = frozenset({
    "predictive_feedback_override",
    "night_window",
    "_energy_state",
    "energy_state_version",
})


async def _async_options_updated(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Selective-apply hook for ``entry.options``.

    Audit T16: when the user changes an option
    that is *not* in
    ``_RELOAD_REQUIRED_OPTION_KEYS``, we do
    not need to recreate the coordinator. The
    coordinator re-reads ``entry.options`` on
    every cycle, so the change takes effect on
    the next update. This listener therefore
    is a no-op for the common case. The
    options flow is responsible for calling
    ``entry.async_reload()`` when the user
    changes a reload-required key.

    The listener still records the event in the
    log so we can observe selective-apply in
    production diagnostics.
    """
    _LOGGER.debug(
        "options updated for entry %s; selective apply (no reload)",
        entry.entry_id,
    )

_FRONTEND_REGISTERED = False


def _get_debug_logging_module():
    """Resolve the ``hems.debug_logging`` module from
    a place that is callable in a flat
    namespace (T17 AST harness).

    We try the absolute package name first
    (``powmr_inverter.hems.debug_logging``),
    then a package-relative import. The
    function returns ``None`` if neither
    resolves — the caller must treat that
    as a soft failure and skip the audit
    hook rather than raising.
    """
    import importlib
    for module_name in (
        "powmr_inverter.hems.debug_logging",
        ".hems.debug_logging",
    ):
        try:
            return importlib.import_module(
                module_name,
                package=__name__,
            )
        except Exception:
            continue
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Solar Inverter from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    api = InverterApiClient(
        email=entry.data["email"],
        password=entry.data["password"],
    )

    try:
        # Authenticate
        if not await api.authenticate():
            _LOGGER.error("Failed to authenticate with inverter API")
            return False

        # Create coordinator
        # T16 audit: the runtime value of
        # ``poll_interval`` is the contract
        # value the user submitted, not a
        # hard-coded constant. The helper
        # ``compute_poll_interval`` is a pure
        # function in ``hems.options_helpers``
        # and is exercised directly by the
        # T16 behavioural test suite.
        from .hems.options_helpers import (
            compute_poll_interval,
        )

        poll_interval = compute_poll_interval(entry.options)
        coordinator = InverterCoordinator(
            hass=hass,
            api=api,
            entry=entry,
            update_interval=timedelta(seconds=poll_interval),
        )

        # Create history coordinator (15-min polling)
        history_coordinator = HistoryCoordinator(
            hass=hass,
            api=api,
            entry=entry,
        )

        # First refresh
        await coordinator.async_config_entry_first_refresh()

        # First refresh history coordinator (background, non-blocking)
        await history_coordinator.async_config_entry_first_refresh()

        # Store coordinator
        hass.data[DOMAIN][entry.entry_id] = {
            "api": api,
            "coordinator": coordinator,
            "history_coordinator": history_coordinator,
        }

        # T18 audit: bind a per-entry log
        # file. ``debug_logging`` resolves
        # ``entry_id`` to its own log
        # path so each config entry has
        # an isolated debug file. The
        # module reference is module-level
        # so the AST harness used by T17 AST
        # contract tests can exec the
        # body in a flat namespace without
        # forcing a relative import.
        _debug_mod = _get_debug_logging_module()
        if _debug_mod is not None:
            _debug_mod.bind_entry(
                entry.entry_id,
                Path(
                    os.environ.get(
                        "POWMR_DEBUG_LOG_DIR",
                        "/config",
                    )
                ) / f"powmr_hems_debug.{entry.entry_id}.log",
            )

        # Register device
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, api.device_sn or entry.entry_id)},
            manufacturer="Solar Inverter",
            model="Smart Inverter",
            name="Smart Solar Inverter",
            sw_version="1.0.0",
        )

        # Cleanup legacy HACS-created device entry (if any).
        # Older HACS frontend auto-registered a device with
        # identifiers=("hacs", "1268765881") on install.
        # In HA 2026.9+, device_registry.devices is a read-only collection
        # (not a dict). Use async_get_device_id_by_identifier() to look
        # up devices by their config-entry-scoped identifier.
        try:
            from homeassistant.helpers.device_registry import (
                async_get_device_id_by_identifier,
            )
            legacy_id = async_get_device_id_by_identifier(
                hass,
                ("hacs", "1268765881"),
                config_entry_id=entry.entry_id,
            )
            if legacy_id is not None:
                _LOGGER.info(
                    "Removing legacy HACS-created device entry (id=%s)", legacy_id
                )
                device_registry.async_remove_device(device_id=legacy_id)
        except (ImportError, ValueError, KeyError) as exc:
            # ImportError: helper not in older HA
            # ValueError/KeyError: no matching device found
            _LOGGER.debug("Legacy HACS device check: %s", exc)

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Register services
        from .services import async_register_services
        await async_register_services(hass)

        # Register a single options update listener
        # that performs *selective* apply. The
        # audit (T16) requires:
        #   1. ``poll_interval``,
        #      ``site_latitude``, ``site_longitude``,
        #      ``reserve_soc`` take effect on the
        #      next coordinator update (the
        #      coordinator reads them from
        #      ``entry.options`` on every cycle, so
        #      we do not need to recreate the
        #      coordinator).
        #   2. ``hems_enabled``,
        #      ``auto_storm_by_forecast``,
        #      ``predictive_mode`` etc. take
        #      effect on the next coordinator
        #      update *or* via the existing
        #      setter hooks.
        #   3. Internal persistence keys
        #      (``predictive_feedback_override``,
        #      ``night_window``) must not trigger
        #      a reload — they are in-process
        #      state, not user-facing config.
        #
        # The selective-apply path is the listener
        # registered below. The actual reload is
        # only triggered by ``entry.async_reload``
        # from the options flow when the user
        # changes a *reload-required* key. We
        # therefore register a NO-OP update
        # listener that suppresses HA's automatic
        # reload for option updates that go
        # through the selective-apply path. The
        # options flow calls ``entry.async_reload``
        # explicitly for keys that need a fresh
        # coordinator.
        entry.async_on_unload(
            entry.add_update_listener(_async_options_updated)
        )

        # ── Bundle flow card (once per HA start) ─────────────────
        global _FRONTEND_REGISTERED
        if not _FRONTEND_REGISTERED:
            await _install_flow_card(hass)
            _FRONTEND_REGISTERED = True

        # ── Auto-install dashboard only if it doesn't exist yet ────
        # Skip if user has already customized the dashboard config in
        # storage — otherwise our default layout would overwrite their
        # changes every time we reload. This respects user agency and
        # keeps customisations stable across integration updates.
        dashboard_path = os.path.join(hass.config.config_dir, ".storage", "lovelace.powmr_energy")
        if not os.path.exists(dashboard_path):
            _LOGGER.info("Auto-installing dashboard (first setup)")
            await _auto_install_dashboard(hass, entry)
        else:
            _LOGGER.debug("Dashboard already exists, skipping auto-install to preserve user edits")

        return True

    except Exception:
        _LOGGER.error("CRITICAL: Failed to set up inverter integration", exc_info=True)
        try:
            await api.close()
        except Exception:
            pass
        return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    Audit T17: release every resource the
    entry owns before the platform teardown
    returns. The order is critical — the
    coordinator must be stopped BEFORE the
    API client, otherwise in-flight forecast
    tasks would try to write to a closed
    connection. The actual order is:

      1. unload platforms (returns
         ``unload_ok``);
      2. stop ``coordinator`` and
         ``history_coordinator`` (cancels
         in-flight forecast tasks and waits
         for them to drain);
      3. close the API client (best-effort
         — a failure is logged at debug and
         does not abort cleanup);
      4. drop the entry from ``hass.data``.

    The platform unload is the controlling
    boolean: if it returns ``False`` we leave
    the entry data in place so a subsequent
    reload can retry the cleanup, and we do
    not raise.
    """
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok and entry_data is not None:
        # T17 audit: order is critical.
        # 1. Stop the coordinator and its
        #    owned tasks first. ``shutdown``
        #    cancels in-flight forecast
        #    requests; we must wait for them
        #    to finish before closing the
        #    session they used.
        # 2. Stop the history coordinator.
        # 3. Close the API client. A
        #    failure here must not abort
        #    cleanup; we log and continue.
        # 4. Drop the entry from ``hass.data``.
        coordinator = entry_data.get("coordinator")
        if coordinator is not None and hasattr(coordinator, "shutdown"):
            try:
                await coordinator.shutdown()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "coordinator.shutdown failed during unload: %s", err
                )
        history_coordinator = entry_data.get("history_coordinator")
        if history_coordinator is not None and hasattr(
            history_coordinator, "shutdown"
        ):
            try:
                await history_coordinator.shutdown()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "history_coordinator.shutdown failed during "
                    "unload: %s",
                    err,
                )
        api: InverterApiClient | None = entry_data.get("api")
        if api is not None:
            try:
                await api.close()
            except Exception as err:  # noqa: BLE001
                # A failed API close must not
                # abort cleanup. The session is
                # gone; downstream code that
                # touches it will fail loudly, and
                # we proceed to drop the entry.
                _LOGGER.debug(
                    "api.close() failed during unload: %s", err
                )
        hass.data[DOMAIN].pop(entry.entry_id, None)

        # T18 audit: tear down the
        # per-entry debug log binding
        # and drain the bounded worker
        # so no record is lost when the
        # entry goes away. The module
        # reference is module-level so
        # the T17 AST harness can exec
        # this body in a flat namespace.
        debug_logging_mod = _get_debug_logging_module()
        if debug_logging_mod is not None:
            debug_logging_mod.unbind_entry(entry.entry_id)
            # ``shutdown_drain`` is global
            # (one worker per HA install),
            # so only call it when the last
            # entry is leaving.
            if not hass.data.get(DOMAIN):
                debug_logging_mod.shutdown_drain()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry.

    If the unload fails, do not attempt setup. The
    audit requirement is: a failed unload means the
    entry is still bound to its old coordinator, and
    re-running setup would replace it and leak the
    previous one.
    """
    unloaded = await async_unload_entry(hass, entry)
    if not unloaded:
        return
    await async_setup_entry(hass, entry)


async def _install_flow_card(hass: HomeAssistant) -> None:
    """Bundle k-flow-card JS + icons into www/ and register for auto-load."""
    import shutil

    src_dir = os.path.join(os.path.dirname(__file__), "frontend")
    www_dir = os.path.join(hass.config.config_dir, "www", "community", "powmr-inverter")
    # Also copy icons to the legacy HACS path so a cached (unpatched) JS
    # that still looks at /local/community/k-flow-card/ finds them.
    legacy_dir = os.path.join(hass.config.config_dir, "www", "community", "k-flow-card")
    resource_url = "/local/community/powmr-inverter/k-flow-card.js"

    def _copy_files() -> bool:
        """Copy bundled frontend files to www/ — always overwrite so updates stick."""
        js_src = os.path.join(src_dir, "k-flow-card.js")
        if not os.path.exists(js_src):
            return False
        # Primary destination
        os.makedirs(www_dir, exist_ok=True)
        shutil.copy2(js_src, os.path.join(www_dir, "k-flow-card.js"))
        _LOGGER.info("Installed k-flow-card.js → www/")
        # Forecast sparkline card
        comparison_src = os.path.join(src_dir, "pv-comparison-card.js")
        if os.path.exists(comparison_src):
            shutil.copy2(comparison_src, os.path.join(www_dir, "pv-comparison-card.js"))
        fc_src = os.path.join(src_dir, "forecast-card.js")
        if os.path.exists(fc_src):
            shutil.copy2(fc_src, os.path.join(www_dir, "forecast-card.js"))
            _LOGGER.info("Installed forecast-card.js → www/")
        # Power history chart card (renders API data from attribute arrays)
        ph_src = os.path.join(src_dir, "power-history-card.js")
        if os.path.exists(ph_src):
            shutil.copy2(ph_src, os.path.join(www_dir, "power-history-card.js"))
            _LOGGER.info("Installed power-history-card.js → www/")
        # Total energy info card (informative total + daily/yearly breakdown)
        te_src = os.path.join(src_dir, "total-energy-card.js")
        if os.path.exists(te_src):
            try:
                shutil.copy2(
                    te_src,
                    os.path.join(www_dir, "total-energy-card.js"),
                )
                _LOGGER.info("Installed total-energy-card.js → www/")
            except OSError as err:
                _LOGGER.warning(
                    "Could not install total-energy-card.js: %s", err
                )
        # Icon PNGs → both primary AND legacy path (safety net for cached JS)
        for fname in ("grid-icon.png", "home-icon.png", "ev-charger-icon.png"):
            src = os.path.join(src_dir, fname)
            if not os.path.exists(src):
                continue
            shutil.copy2(src, os.path.join(www_dir, fname))
            os.makedirs(legacy_dir, exist_ok=True)
            shutil.copy2(src, os.path.join(legacy_dir, fname))
        _LOGGER.info("Installed icons → www/ (both primary & legacy paths)")
        return True

    copied = await hass.async_add_executor_job(_copy_files)
    if not copied:
        _LOGGER.warning("k-flow-card.js not found; flow card unavailable")
        return

    try:
        from homeassistant.components.frontend import add_extra_js_url
        # Audit T23: cache-bust version is derived from the
        # SHA-256 of each asset's bytes. A static literal
        # version does not guarantee the user's browser
        # re-fetches the asset when only one of the bundled
        # scripts changed.
        www_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "www",
        )
        _flow_bust = _compute_assets_cache_bust(
            www_dir, ["k-flow-card.js"]
        )
        _forecast_bust = _compute_assets_cache_bust(
            www_dir, ["forecast-card.js"]
        )
        _ph_bust = _compute_assets_cache_bust(
            www_dir, ["power-history-card.js"]
        )
        _te_bust = _compute_assets_cache_bust(
            www_dir, ["total-energy-card.js"]
        )
        add_extra_js_url(hass, f"{resource_url}?v={_flow_bust}")
        # Forecast sparkline card
        fc_url = "/local/community/powmr-inverter/forecast-card.js"
        add_extra_js_url(hass, f"{fc_url}?v={_forecast_bust}")
        add_extra_js_url(hass, "/local/community/powmr-inverter/pv-comparison-card.js?v=2")
        # Power history chart card
        ph_url = "/local/community/powmr-inverter/power-history-card.js"
        add_extra_js_url(hass, f"{ph_url}?v={_ph_bust}")
        # Total energy info card
        te_url = "/local/community/powmr-inverter/total-energy-card.js"
        add_extra_js_url(hass, f"{te_url}?v={_te_bust}")
        _LOGGER.info(
            "Flow card + forecast card + power-history card + total-energy card registered "
            "(cache-bust: flow=%s forecast=%s ph=%s te=%s)",
            _flow_bust, _forecast_bust, _ph_bust, _te_bust,
        )
    except Exception as exc:
        _LOGGER.warning("Could not register flow card: %s", exc)


# ═══════════════════════════════════════════════════════════════════════
# Dashboard builder — returns a plain Python dict (stored as JSON)
# ═══════════════════════════════════════════════════════════════════════

# ── Audit T22 — AI view builder ───────────────────────────────
def _build_ai_view(
    entity_lookup,
    decision_state: dict,
) -> dict:
    """Build the AI dashboard view.

    The view surfaces five keys the
    audit requires:

      * mode (``Off`` / ``Shadow`` / ``Assist``)
      * readiness (assistant "ready" boolean)
      * real_pairs (count of measured
        PV fact pairs feeding the
        calibrator)
      * model_quality (calibration
        confidence factor 0..1)
      * decision_reason (text from
        ``predictive_decision_state.reason``)

    ``entity_lookup`` is a callable
    the dashboard builder injects
    so the AI view resolves the
    predictive sensors via the
    entity registry (which honours
    renames) rather than hard-coded
    ``sensor.`` IDs. The contract
    is documented in
    ``tests/test_t22_t23_dashboard.py``.
    """
    mode = decision_state.get("mode", "Off")
    readiness = bool(decision_state.get("readiness", False))
    real_pairs = int(decision_state.get("real_pairs", 0))
    model_quality = float(
        decision_state.get("model_quality", 0.0)
    )
    reason = str(decision_state.get("reason", ""))

    decision_state_eid = entity_lookup(
        "predictive_decision_state"
    )
    hint_eid = entity_lookup("predictive_hint")
    plan_eid = entity_lookup("predictive_plan")
    reason_eid = entity_lookup("hems_last_reason")

    cards: list[dict] = []
    cards.append({
        "type": "markdown",
        "content": (
            f"# ШІ · Режим **{mode}**\n\n"
            f"Готовність: **{'так' if readiness else 'ні'}**\n\n"
            f"Реальних пар: **{real_pairs}**\n\n"
            f"Якість моделі: **{round(model_quality, 2)}**\n\n"
            f"Причина рішення: **{reason}**"
        ),
    })
    if real_pairs == 0:
        cards.append({
            "type": "markdown",
            "content": "ℹ️ Даних ще немає (0 пар).",
        })
    rows: list[dict] = []
    for eid, name in (
        (decision_state_eid, "Decision State"),
        (hint_eid, "Predictive Hint"),
        (plan_eid, "Predictive Plan"),
        (reason_eid, "HEMS Last Reason"),
    ):
        if eid:
            rows.append({"entity": eid, "name": name})
    if rows:
        cards.append({
            "type": "entities",
            "title": "Стан AI",
            "entities": rows,
        })
    return {
        "title": "ШІ",
        "path": "powmr-ai",
        "icon": "mdi:brain",
        "type": "sections",
        "max_columns": 2,
        "sections": [{"type": "grid", "cards": cards}],
    }


# ── Audit T23 — atomic write + cache-bust ───────────────────
def _write_dashboard_atomic(
    target_path: str, payload: dict
) -> None:
    """Write ``payload`` to
    ``target_path`` atomically.

    The function:

      * writes the JSON to a
        tempfile in the same
        directory (so ``os.replace``
        is a same-filesystem rename,
        never a copy);
      * backs up the existing file
        to ``target_path + ".bak"``
        if it exists;
      * calls ``os.replace`` so the
        move is atomic;
      * on failure, removes the
        tempfile and re-raises the
        exception so the previous
        file is preserved.

    Audit T23 (Windows review):
    the previous direct
    ``json.dump`` overwrote the
    existing file in place; a
    crash mid-write left a
    half-written dashboard and
    took the AI view down. This
    writer is the agreed
    replacement.
    """


    target_dir = os.path.dirname(
        os.path.abspath(target_path)
    )
    fd, tmp_path = tempfile.mkstemp(
        dir=target_dir,
        prefix=".lovelace.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        if os.path.exists(target_path):
            shutil.copyfile(
                target_path, target_path + ".bak"
            )
        os.replace(tmp_path, target_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _compute_assets_cache_bust(
    www_dir: str, asset_names: list[str]
) -> str:
    """Compute a content-derived
    cache-bust suffix for the
    frontend assets.

    Returns the first 8 hex
    characters of the SHA-256 over
    each asset's bytes (with a
    name-keyed separator so two
    files with identical content
    still hash differently by
    position).

    Audit T23: a single static
    literal version (e.g.
    ``?v=1.8.2``) does not
    guarantee the user's browser
    re-fetches the asset when only
    one of the bundled scripts
    changed.
    """
    h = hashlib.sha256()
    for name in asset_names:
        path = os.path.join(www_dir, name)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            h.update(name.encode("utf-8"))
            h.update(b"\x00")
            h.update(f.read())
            h.update(b"\x00")
    return h.hexdigest()[:8]


def _tile(entity: str, name: str, icon: str) -> dict:
    return {"type": "tile", "entity": entity, "name": name, "icon": icon}

def _stats(title: str, chart_type: str, period: str, days: int,
           stat_types: list[str], entities: list[str]) -> dict:
    return {
        "type": "statistics-graph",
        "title": title,
        "chart_type": chart_type,
        "period": period,
        "days_to_show": days,
        "stat_types": stat_types,
        "entities": entities,
    }

def _history(title: str, hours: int, refresh: int, entities: list[str]) -> dict:
    return {
        "type": "history-graph",
        "title": title,
        "hours_to_show": hours,
        "refresh_interval": refresh,
        "entities": entities,
    }

def _entities_card(title: str, entity_list: list[dict]) -> dict:
    return {
        "type": "entities",
        "title": title,
        "show_header_toggle": False,
        "entities": entity_list,
    }

def _entity_row(eid: str, name: str | None = None) -> dict:
    row: dict = {"entity": eid}
    if name:
        row["name"] = name
    return row


async def _auto_install_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Generate and register the Smart Solar dashboard with real entity IDs.

    Builds the dashboard as a Python dict (NOT a YAML string) so that
    HA stores it as a JSON object in .storage — avoids the well-known
    "Cannot use 'in' operator to search for 'strategy'" frontend crash.
    """
    import hashlib

    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)

    # Collect all entities belonging to this config entry
    entry_entities = [
        ent for ent in registry.entities.values()
        if ent.config_entry_id == entry.entry_id
    ]
    if not entry_entities:
        _LOGGER.warning("No entities found for dashboard, deferring")
        return

    # Build translation_key → full entity_id mapping
    eid: dict[str, str] = {}
    for ent in entry_entities:
        tk = ent.translation_key
        if tk:
            eid[tk] = ent.entity_id

    # Predictive sensors do NOT have translation_keys; their
    # unique_id is ``f"{entry_id}_predictive_..."``. Map those
    # onto the same lookup table so the AI view can resolve
    # them via the registry (audit T22).
    for ent in entry_entities:
        uid = ent.unique_id
        if not uid:
            continue
        prefix = f"{entry.entry_id}_predictive_"
        if uid.startswith(prefix):
            suffix = uid[len(prefix):]
            eid.setdefault(
                f"predictive_{suffix}", ent.entity_id
            )
        elif uid == f"{entry.entry_id}_hems_last_reason":
            eid.setdefault("hems_last_reason", ent.entity_id)

    # Shorthand: entity by translation_key, empty string if missing
    def _e(key: str) -> str:
        return eid.get(key, "")

    # Helper: only include entity card rows where entity exists
    def _rows(*pairs: tuple[str, str]) -> list[dict]:
        return [_entity_row(_e(k), n) for k, n in pairs if _e(k)]

    # ── Build the dashboard config dict ──────────────────────────
    views: list[dict] = []

    # ═══════════════════════════════════════════════════════════════
    # View 1: ОГЛЯД
    # ═══════════════════════════════════════════════════════════════
    overview_cards: list[dict] = []

    # ── Status tiles ──
    status_grid_cards: list[dict] = []
    for tk, nm, ic in [
        ("grid_available", "Мережа", "mdi:transmission-tower"),
        ("working_mode", "Режим", "mdi:state-machine"),
        ("battery_soc_corrected", "SOC", "mdi:battery-heart-variant"),
        ("daily_savings", "Валова оцінка", "mdi:cash-check"),
        ("forecast_tomorrow", "Прогноз PV", "mdi:solar-power"),
    ]:
        if _e(tk):
            status_grid_cards.append(_tile(_e(tk), nm, ic))
    if status_grid_cards:
        overview_cards.append({"type": "grid", "cards": status_grid_cards})

    # ── Weather summary tiles (template sensors) ──
    # These come from templates.yaml — see installation docs.
    weather_tiles: list[dict] = []
    for tk, nm, ic in [
        ("pv_weather_yesterday", "Погода вчора", "mdi:weather-sunny"),
        ("pv_weather_tomorrow", "Погода завтра", "mdi:weather-sunny"),
        ("pv_weather_history_7d", "Погода (7 днів)", "mdi:weather-partly-cloudy"),
    ]:
        weather_eid = f"sensor.{tk}"
        weather_tiles.append({
            "type": "tile",
            "entity": weather_eid,
            "name": nm,
            "icon": ic,
        })
    if weather_tiles:
        overview_cards.append({"type": "grid", "cards": weather_tiles})

    # ── k-flow-card (must be inside a grid section!) ──
    flow_cfg: dict = {"type": "custom:k-flow-card", "inverter_name": "PowMr"}
    for cfg_key, entity_key in [
        ("pv_total_power", "pv_power"),
        ("grid_active_power", "grid_power"),
        ("consump", "load_power"),
        ("battery_soc", "battery_soc_corrected"),
        ("battery_power", "battery_power"),
        ("battery_voltage", "battery_voltage"),
        ("today_pv", "daily_energy"),
        ("daily_savings", "daily_savings"),
    ]:
        val = _e(entity_key)
        if val:
            flow_cfg[cfg_key] = val
    # Fallback: if battery_soc_corrected missing, try battery_soc
    if "battery_soc" not in flow_cfg and _e("battery_soc"):
        flow_cfg["battery_soc"] = _e("battery_soc")
    flow_cfg["sun"] = "sun.sun"
    flow_cfg["_show_battery"] = True
    overview_cards.append({"type": "grid", "cards": [flow_cfg]})

    # ── Combined energy flow + voltage + SOC graphs ──
    graph_cards: list[dict] = []
    pv = _e("pv_power")
    load = _e("load_power")
    grid = _e("grid_power")
    batt = _e("battery_power")
    if any([pv, load, grid, batt]):
        ents = [x for x in [pv, load, grid, batt] if x]
        graph_cards.append(_stats("Енергопотік (24г)", "line", "hour", 1, ["mean"], ents))
    pv_v = _e("pv_voltage")
    grid_v = _e("grid_voltage")
    batt_v = _e("battery_voltage")
    if any([pv_v, grid_v, batt_v]):
        ents = [x for x in [pv_v, grid_v, batt_v] if x]
        graph_cards.append(_stats("Напруги (24г)", "line", "hour", 1, ["mean"], ents))
    batt_soc = _e("battery_soc")
    batt_soc_c = _e("battery_soc_corrected")
    if any([batt_soc, batt_soc_c]):
        ents = [x for x in [batt_soc, batt_soc_c] if x]
        graph_cards.append(_history("SOC (24г)", 24, 60, ents))
    if graph_cards:
        overview_cards.append({"type": "grid", "cards": graph_cards})

    # ── HEMS diagnostics ──
    hems_rows = _rows(
        ("hems_last_reason", "Причина рішення"),
        ("hems_last_output_cmd", "Команда виходу"),
        ("hems_last_charger_cmd", "Команда заряду"),
        ("hems_auto_mode", "HEMS авто-режим"),
    )
    if hems_rows:
        overview_cards.append({"type": "grid", "cards": [_entities_card("HEMS Стан", hems_rows)]})

    # ── Forecast graph (replaces broken forecast-card) ──
    # Forecast moved to combined generation+forecast chart in History view

    views.append({
        "title": "Огляд",
        "path": "powmr-overview",
        "icon": "mdi:home-lightning-bolt",
        "type": "sections",
        "max_columns": 3,
        "sections": overview_cards,
    })

    # ═══════════════════════════════════════════════════════════════
    # View 2: КЕРУВАННЯ
    # ═══════════════════════════════════════════════════════════════
    control_cards: list[dict] = []

    mode_rows = _rows(
        ("output_priority", "Пріоритет виходу"),
        ("charger_priority", "Пріоритет заряджання"),
        ("smart_mode", "Режим HEMS"),
        ("hems_auto_mode", "Авто-режим HEMS"),
        ("backup_mode", "Резервний режим"),
        ("eco_mode", "ECO режим"),
    )
    if mode_rows:
        control_cards.append({"type": "grid", "cards": [_entities_card("Режими інвертора", mode_rows)]})

    grid_ctrl_rows = _rows(
        ("grid_charging", "Заряд від мережі"),
        ("grid_feed_in", "Віддача в мережу"),
    )
    if grid_ctrl_rows:
        control_cards.append({"type": "grid", "cards": [_entities_card("Керування мережею", grid_ctrl_rows)]})

    limit_rows = _rows(
        ("max_charging_current", "Макс. струм заряджання"),
        ("max_utility_charging_current", "Макс. струм заряду (мережа)"),
        ("battery_charge_limit_percent", "Ліміт заряду АКБ"),
        ("battery_discharge_limit_percent", "Ліміт розряду АКБ"),
        ("grid_charge_power_limit", "Макс. потужність заряду (мережа)"),
    )
    if limit_rows:
        control_cards.append({"type": "grid", "cards": [_entities_card("Струми та ліміти", limit_rows)]})

    state_rows = _rows(
        ("grid_voltage", "Напруга мережі (V)"),
        ("pv_voltage", "Напруга PV (V)"),
        ("battery_voltage", "Напруга АКБ (V)"),
        ("battery_current", "Струм батареї (A)"),
        ("pv_surplus", "Надлишок PV (W)"),
        ("ac_output_power", "Вихід AC (W)"),
        ("feed_in_power", "Віддача в мережу (W)"),
        ("grid_import_power", "Споживання з мережі (W)"),
        ("inverter_temperature", "Температура (°C)"),
    )
    if state_rows:
        control_cards.append({"type": "grid", "cards": [_entities_card("Поточні показники", state_rows)]})

    views.append({
        "title": "Керування",
        "path": "powmr-control",
        "icon": "mdi:tune",
        "type": "sections",
        "max_columns": 2,
        "sections": control_cards,
    })

    # ═══════════════════════════════════════════════════════════════
    # View 3: ІСТОРІЯ
    # ═══════════════════════════════════════════════════════════════
    history_cards: list[dict] = []

    # PV генерація (7 днів) removed — covered by power-history-card monthly
    # PV power (48h) removed — covered by power-history-card daily
    if pv and load:
        history_cards.append({"type": "grid", "cards": [
            _stats("PV + Навантаження (7 днів)", "line", "hour", 7, ["mean"], [pv, load])
        ]})
    # SOC + voltage: skip if entities don't have valid measurements.
    # Many users don't have corrected_soc sensor, and orphan entities
    # produce broken statistics-graph ("Статистичних даних не знайдено").
    # If real SOC sensor is added in future, restore this block.
    if False and soc_ents:
        history_cards.append({"type": "grid", "cards": [
            _stats("SOC + Напруга АКБ (7 днів)", "line", "hour", 7, ["mean"], soc_ents)
        ]})

    # Timestamped cloud PV and forecast share a local-day W axis.
    curve_eid = _e("pv_generation_curve")
    forecast_eid = _e("forecast_tomorrow")
    if curve_eid and forecast_eid:
        history_cards.append({"type": "grid", "column_span": 2, "cards": [{
            "type": "custom:pv-comparison-card", "entity": curve_eid,
            "forecast_entity": forecast_eid, "title": "Генерація та прогноз PV",
            "grid_options": {"columns": "full"},
        }]})

    monthly_energy_eid = _e("history_monthly_energy")
    if monthly_energy_eid:
        history_cards.append({"type": "grid", "cards": [{
            "type": "custom:power-history-card",
            "entity": monthly_energy_eid,
            "attribute": "daily_energy_kwh",
            "labels_attribute": "daily_labels",
            "title": "Енергія PV за місяць",
            "chart_type": "bar",
            "bar_color": "#2ecc71",
        }]})

    yearly_energy_eid = _e("history_yearly_energy")
    if yearly_energy_eid:
        history_cards.append({"type": "grid", "cards": [{
            "type": "custom:power-history-card",
            "entity": yearly_energy_eid,
            "attribute": "monthly_energy_kwh",
            "labels_attribute": "monthly_labels",
            "title": "Енергія PV за рік",
            "chart_type": "bar",
            "bar_color": "#3498db",
        }]})

    total_energy_eid = _e("history_total_energy")
    if total_energy_eid:
        # Use informative total-energy-card (big total + today/year chips)
        # instead of bare tile — gives context at a glance.
        history_cards.append({"type": "grid", "cards": [{
            "type": "custom:total-energy-card",
            "entity": total_energy_eid,
            "title": "Загальна енергія",
            "icon": "mdi:solar-power-variant",
        }]})

    views.append({
        "title": "Історія",
        "path": "powmr-history",
        "icon": "mdi:chart-line",
        "type": "sections",
        "max_columns": 2,
        "sections": history_cards,
    })

    # ═══════════════════════════════════════════════════════════════
    # View 4: ЕКОНОМІКА
    # ═══════════════════════════════════════════════════════════════
    econ_cards: list[dict] = []

    econ_tiles: list[dict] = [
        {"type": "markdown", "content": (
            "# Валова оцінка вартості заміщеного імпорту\n\n"
            "Це **груба оцінка** того, скільки грошей "
            "зекономили б розряди батареї замість "
            "покупки електроенергії з мережі. "
            "Формула: розряд_день × тариф_день + "
            "розряд_ніч × тариф_ніч.\n\n"
            "**Це НЕ чиста економія**: не віднімає "
            "ні вартість заряджання від мережі, ні "
            "втрати в батареї, ні загальний імпорт. "
            "Якщо батарею заряджали вночі з мережі "
            "за дешевим тарифом і розряджали вдень — "
            "це **арбітраж**, а не економія. "
            "Пряме самопоживання PV без "
            "проходження через батарею "
            "також не враховується — значення "
            "охоплює лише розряди батареї. "
            "Деталі — в атрибутах сенсора "
            "(savings_formula, savings_limitations, "
            "is_net_savings)."
        )}
    ]
    for tk, nm, ic in [
        ("daily_savings", "Валова оцінка · сьогодні", "mdi:cash-check"),
        ("monthly_savings", "Валова оцінка · місяць", "mdi:cash-multiple"),
        ("forecast_tomorrow", "Прогноз на завтра", "mdi:solar-power"),
        ("forecast_day_after", "Прогноз на післязавтра", "mdi:solar-power-variant"),
        ("learned_ratio", "Коефіцієнт PV", "mdi:brain"),
    ]:
        if _e(tk):
            econ_tiles.append(_tile(_e(tk), nm, ic))
    econ_cards.append({"type": "grid", "cards": econ_tiles})

    econ_graphs: list[dict] = []
    ds = _e("daily_savings")
    if ds:
        econ_graphs.append(_stats("Валова оцінка (30 днів)", "bar", "day", 30, ["sum"], [ds]))
    if econ_graphs:
        econ_cards.append({"type": "grid", "cards": econ_graphs})

    views.append({
        "title": "Економіка",
        "path": "powmr-economics",
        "icon": "mdi:cash",
        "type": "sections",
        "max_columns": 2,
        "sections": econ_cards,
    })

    # ═══════════════════════════════════════════════════════════════
    # View 5: ШІ (audit T22)
    # ═══════════════════════════════════════════════════════════════
    # Pull live decision state
    # from the coordinator so the
    # view is honest even on fresh
    # install (samples == 0 → "no
    # data yet" tile).
    hems = getattr(hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    ), "_hems", None)
    if hems is None:
        # Fall back to a default
        # decision state when the
        # HEMS engine is not yet
        # bound.
        decision_state: dict = {
            "mode": "Off",
            "readiness": False,
            "real_pairs": 0,
            "model_quality": 0.0,
            "reason": "hems_unbound",
        }
    else:
        decision_state = (
            hems.predictive_decision_state.copy()
        )
        # Compute readiness from the
        # last hint + calibrator so
        # the view does not rely on
        # the audit-required boolean
        # being explicitly set.
        controller = getattr(
            hems, "_predictive_controller", None
        )
        calibrator = getattr(
            controller, "calibrator", None
        )
        real_pairs = 0
        model_quality = 0.0
        if calibrator is not None:
            try:
                metrics = calibrator.metrics()
                real_pairs = int(metrics.sample_count)
                model_quality = float(
                    metrics.confidence_factor
                )
            except (AttributeError, TypeError, ValueError):
                pass
        hint = getattr(hems, "_last_predictive_hint", None)
        confidence = float(
            decision_state.get("confidence", 0.0) or 0.0
        )
        import math as _math
        readiness = bool(
            _math.isfinite(confidence)
            and real_pairs >= 3
            and model_quality >= 0.2
        ) if hint is not None else False
        decision_state["real_pairs"] = real_pairs
        decision_state["model_quality"] = model_quality
        decision_state["readiness"] = readiness
    views.append(_build_ai_view(_e, decision_state))

    # ── Assemble final config dict ───────────────────────────────
    dashboard_config: dict = {
        "title": "Smart Solar Енергопанель",
        "views": views,
    }

    # Hash for change detection
    config_hash = hashlib.md5(json.dumps(dashboard_config, sort_keys=True).encode()).hexdigest()[:8]
    old_hash = hass.data[DOMAIN][entry.entry_id].get("dash_hash", "")

    if config_hash != old_hash:
        hass.data[DOMAIN][entry.entry_id]["dash_hash"] = config_hash
        _LOGGER.info("Dashboard config regenerated (%d entities, hash=%s)", len(eid), config_hash)

    # Register in lovelace storage — writes a JSON dict (not YAML string!)
    await _register_lovelace_dashboard(hass, dashboard_config)


async def _register_lovelace_dashboard(hass: HomeAssistant, dashboard_config: dict) -> None:
    """Auto-register the dashboard in storage mode.

    Writes dashboard metadata to .storage/lovelace_dashboards
    and the actual dashboard config to .storage/lovelace.powmr_energy.
    The config is stored as a JSON object — NOT a YAML string — which
    avoids the "Cannot use 'in' operator to search for 'strategy'" crash.
    """
    DASHBOARD_URL = "powmr-energy"
    DASHBOARD_TITLE = "Smart Solar Енергопанель"
    DASHBOARD_ID = "powmr_energy"
    config_dir = hass.config.config_dir
    dashboards_storage = os.path.join(config_dir, ".storage", "lovelace_dashboards")
    dashboard_content_storage = os.path.join(config_dir, ".storage", f"lovelace.{DASHBOARD_ID}")

    # Step 1: Check if already registered
    try:
        def _check():
            if not os.path.exists(dashboards_storage):
                return False
            with open(dashboards_storage, "r") as f:
                data = json.loads(f.read())
            for item in data.get("data", {}).get("items", []):
                if item.get("url_path") == DASHBOARD_URL:
                    return True
            return False
        if await hass.async_add_executor_job(_check):
            # Already registered — just update the content
            await _update_dashboard_content(hass, dashboard_content_storage, dashboard_config)
            _LOGGER.debug("Dashboard %s already registered, content updated", DASHBOARD_URL)
            return
    except Exception as exc:
        _LOGGER.debug("Dashboard check failed: %s", exc)

    # Step 2: Register metadata
    try:
        def _register():
            with open(dashboards_storage, "r") as f:
                data = json.loads(f.read())

            items = data.get("data", {}).get("items", [])
            for item in items:
                if item.get("url_path") == DASHBOARD_URL:
                    return True  # concurrent registration

            items.append({
                "id": DASHBOARD_ID,
                "icon": "mdi:solar-power",
                "title": DASHBOARD_TITLE,
                "show_in_sidebar": True,
                "require_admin": False,
                "mode": "storage",
                "url_path": DASHBOARD_URL,
            })
            data["data"]["items"] = items

            with open(dashboards_storage, "w") as f:
                json.dump(data, f, indent=2)
            return True

        result = await hass.async_add_executor_job(_register)
        if result:
            # Step 3: Write dashboard config content
            await _update_dashboard_content(hass, dashboard_content_storage, dashboard_config)
            _LOGGER.info("✅ Dashboard '%s' auto-registered (storage mode, no YAML)", DASHBOARD_TITLE)
        else:
            _LOGGER.debug("Dashboard already registered (concurrent)")

    except Exception as exc:
        _LOGGER.warning("Dashboard auto-register failed: %s", exc)


async def _update_dashboard_content(
    hass: HomeAssistant, storage_path: str, dashboard_config: dict, dashboard_id: str = "powmr_energy"
) -> None:
    """Write the dashboard config to storage as a JSON object.

    CRITICAL: data.config MUST be a dict (JSON object), NOT a YAML string.
    Storing a string causes "Cannot use 'in' operator to search for 'strategy'"
    in the HA frontend because JS receives a string where it expects an object.

    Audit T23: the write goes through ``_write_dashboard_atomic`` so a crash
    mid-write cannot leave a half-written dashboard. The previous direct
    ``json.dump`` overwrote the file in place.
    """
    def _write():
        data = {
            "key": f"lovelace.{dashboard_id}",
            "version": 1,
            "minor_version": 1,
            "key_version": 1,
            "data": {
                "config": dashboard_config,  # ← JSON dict, NOT YAML string!
            },
        }
        _write_dashboard_atomic(storage_path, data)

    await hass.async_add_executor_job(_write)
