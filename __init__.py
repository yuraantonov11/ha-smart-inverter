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
from typing import NamedTuple

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

    R04+R05: the capacity and tariff caches on
    the coordinator are NOT recomputed on the
    next cycle (they live in
    ``_battery_capacity_kwh``, ``_day_tariff_uah``,
    ``_night_tariff_uah``). For those, we call
    a runtime updater that re-derives the
    capacity and re-builds the tariff schedule
    in place. This is the verified update path
    (no coordinator reload required for these
    two families of options).
    """
    bundle: dict | None = (
        getattr(hass, "data", {}).get(DOMAIN, {}).get(entry.entry_id)
        if hasattr(hass, "data") else None
    )
    coordinator: InverterCoordinator | None = (
        bundle.get("coordinator") if isinstance(bundle, dict) else None
    )
    if coordinator is not None:
        try:
            coordinator.apply_capacity_and_tariff_options(entry.options)
        except Exception as exc:  # pragma: no cover
            _LOGGER.warning(
                "Failed to apply capacity/tariff options for %s: %r",
                entry.entry_id, exc,
            )
    _LOGGER.debug(
        "options updated for entry %s; selective apply (no reload)",
        entry.entry_id,
    )

_FRONTEND_REGISTERED = False

# R10.6 (round 6):
# module-level
# constants for the
# canonical main
# dashboard. Lifted
# from
# ``_register_lovelace_dashboard``
# so
# ``_ensure_dashboard_binding``
# can resolve the
# same path without
# duplicating magic
# strings.
_DASHBOARD_URL = "powmr-energy"
_DASHBOARD_TITLE = "Smart Solar Енергопанель"
_DASHBOARD_ID = "powmr_energy"


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

        # R10.6 (round 6): the binding MUST be persisted on EVERY
        # ``async_setup_entry`` call, not just on the first install
        # path. The previous logic skipped the dashboard path entirely
        # when ``lovelace.powmr_energy`` existed, leaving
        # ``entry.options`` empty for entries that did NOT go through
        # ``_auto_install_dashboard``. We extract the binding
        # resolution into a tiny helper that reuses the same logic as
        # the "already registered" branch in
        # ``_register_lovelace_dashboard`` so a restart with an
        # existing dashboard always records the binding.
        await _ensure_dashboard_binding(hass, entry)

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
        # Audit T23 round 5 (D4):
        # the cache-bust hash is
        # computed against the
        # **installed** asset, not
        # the bundled package
        # www. The previous code
        # hashed the bundled
        # directory which only
        # ever contained a few
        # assets, so the cards
        # that lived only in the
        # copy step got the
        # SHA-256 of the empty
        # string (``e3b0c442...``)
        # in the Windows review.
        # We compute the hash
        # AFTER the copy step and
        # BEFORE registering the
        # extra JS URL.
        installed_www = www_dir

        def _hash_assets() -> dict:
            # Audit T23 round 5
            # follow-up: the
            # synchronous file read
            # in
            # ``_compute_assets_cache_bust``
            # would otherwise block
            # the HA event loop. We
            # run the four hash
            # calls inside a single
            # executor-job closure.
            return {
                "flow": _compute_assets_cache_bust(
                    installed_www, ["k-flow-card.js"]
                ),
                "forecast": _compute_assets_cache_bust(
                    installed_www, ["forecast-card.js"]
                ),
                "ph": _compute_assets_cache_bust(
                    installed_www, ["power-history-card.js"]
                ),
                "te": _compute_assets_cache_bust(
                    installed_www, ["total-energy-card.js"]
                ),
                "pc": _compute_assets_cache_bust(
                    installed_www, ["pv-comparison-card.js"]
                ),
            }

        hashes = await hass.async_add_executor_job(
            _hash_assets
        )
        _flow_bust = hashes["flow"]
        _forecast_bust = hashes["forecast"]
        _ph_bust = hashes["ph"]
        _te_bust = hashes["te"]
        # pv-comparison-card.js has
        # never been hashed because
        # it lived only in the
        # copy step. Audit T23
        # round 5 (D4): when the
        # asset is missing on disk,
        # do NOT silently register
        # it with the empty-hash.
        _pc_bust = hashes["pc"]
        add_extra_js_url(hass, f"{resource_url}?v={_flow_bust}")
        # Forecast sparkline card
        fc_url = "/local/community/powmr-inverter/forecast-card.js"
        add_extra_js_url(hass, f"{fc_url}?v={_forecast_bust}")
        # pv-comparison: only
        # register when the asset
        # exists on disk. The
        # previous ``?v=2`` static
        # literal silently assumed
        # the file was present; if
        # the bundled copy is
        # missing, the user's
        # network panel saw a 404
        # the integration never
        # warned about.
        pc_url = (
            "/local/community/powmr-inverter/"
            "pv-comparison-card.js"
        )
        if os.path.exists(
            os.path.join(
                installed_www, "pv-comparison-card.js"
            )
        ):
            add_extra_js_url(hass, f"{pc_url}?v={_pc_bust}")
        else:
            _LOGGER.warning(
                "pv-comparison-card.js not installed; "
                "skipping frontend registration"
            )
        # Power history chart card
        ph_url = "/local/community/powmr-inverter/power-history-card.js"
        add_extra_js_url(hass, f"{ph_url}?v={_ph_bust}")
        # Total energy info card
        te_url = "/local/community/powmr-inverter/total-energy-card.js"
        add_extra_js_url(hass, f"{te_url}?v={_te_bust}")
        _LOGGER.info(
            "Flow card + forecast card + power-history card + total-energy card registered "
            "(cache-bust: flow=%s forecast=%s ph=%s te=%s pc=%s)",
            _flow_bust, _forecast_bust, _ph_bust, _te_bust, _pc_bust,
        )
    except Exception as exc:
        _LOGGER.warning("Could not register flow card: %s", exc)


# ═══════════════════════════════════════════════════════════════════════
# Dashboard builder — returns a plain Python dict (stored as JSON)
# ═══════════════════════════════════════════════════════════════════════

# ── Audit T22 — AI view builder ───────────────────────────────
def _compute_ai_decision_state(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Read the live engine state
    for the AI view.

    Audit T22 round 5 (D2):
    ``hass.data[DOMAIN][entry_id]``
    is a dict whose ``"coordinator"``
    key holds the actual
    ``InverterCoordinator``. The
    engine lives at
    ``coordinator._hems``.

    Audit T22 round 7 (R7.1):
    the engine is the single
    source of truth. We do NOT
    recompute ``readiness`` /
    ``real_pairs`` /
    ``model_quality`` here —
    ``hems/predictive_control.py``
    publishes them on
    ``predictive_decision_state``
    on every evaluate cycle,
    and the
    ``PredictiveDecisionStateSensor``
    surfaces them through
    ``extra_state_attributes``.
    The helper returns a
    read-only copy of that
    state, so the dashboard
    helper always sees the
    most-recent publish.
    """
    bundle = hass.data.get(DOMAIN, {}).get(
        entry.entry_id, {}
    )
    coordinator = bundle.get("coordinator")
    if coordinator is None:
        return {
            "mode": "Off",
            "readiness": False,
            "real_pairs": 0,
            "model_quality": 0.0,
            "reason": "coordinator_unbound",
        }
    hems = getattr(coordinator, "_hems", None)
    if hems is None:
        return {
            "mode": "Off",
            "readiness": False,
            "real_pairs": 0,
            "model_quality": 0.0,
            "reason": "hems_unbound",
        }
    return hems.predictive_decision_state.copy()


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
    # Audit T22 round 6 (R6.3):
    # the user can change the
    # predictive mode via the
    # ``select.garazh_smart_solar_inverter``
    # entity. The view MUST
    # surface it so the user can
    # change the mode without
    # leaving the dashboard.
    predictive_mode_eid = entity_lookup(
        "predictive_mode"
    )

    cards: list[dict] = []
    # Audit T22 round 7 (R7.2):
    # the previous code emitted a
    # frozen literal
    # ``"ℹ Даних ще немає (0 пар)"``
    # title at generation time.
    # Once the engine accumulated
    # samples the user still saw
    # ``(0 пар)`` until the
    # dashboard was regenerated.
    # The view is reactive — it
    # MUST NOT embed a literal
    # sample count anywhere. The
    # ``attribute: real_pairs``
    # row on the ``predictive_decision_state``
    # sensor (added below) is the
    # single source of truth; the
    # HA frontend re-renders it
    # on every state change.
    rows: list[dict] = []
    # Audit T22 round 7 (R7):
    # the engine publishes
    # ``readiness``, ``real_pairs``,
    # ``model_quality`` and
    # ``reason`` on
    # ``predictive_decision_state``
    # (see
    # ``hems/predictive_control.py``).
    # The AI view surfaces all
    # four as ``attribute`` rows
    # on the
    # ``predictive_decision_state``
    # sensor so the user sees
    # live values that the HA
    # frontend re-renders on
    # every refresh. Decision
    # State itself is also
    # surfaced as a separate
    # entity.
    decision_rows = (
        ("readiness", "Готовність"),
        ("real_pairs", "Реальних пар"),
        ("model_quality", "Якість моделі"),
        ("confidence", "Впевненість"),
        ("reason", "Причина рішення"),
    )
    for attr_key, label in decision_rows:
        if not decision_state_eid:
            break
        # Audit T22 round 8 (R8.1):
        # the ``entities`` card
        # requires ``type:
        # attribute`` to render
        # a sensor attribute as
        # the row's primary
        # value. The previous
        # ``entity + attribute``
        # form silently rendered
        # the sensor's STATE,
        # not the attribute, so
        # every row showed the
        # same value. The
        # ``type: attribute``
        # schema is documented
        # at
        # https://www.home-assistant.io/dashboards/entities/
        # — a row is a dict with
        # ``entity`` and
        # ``attribute`` keys and
        # a ``type: attribute``
        # discriminator so the
        # frontend reads the
        # attribute value, not
        # the state.
        rows.append({
            "type": "attribute",
            "entity": decision_state_eid,
            "attribute": attr_key,
            "name": label,
            "icon": "mdi:brain",
        })
    for eid, name in (
        (hint_eid, "Predictive Hint"),
        (plan_eid, "Predictive Plan"),
        (reason_eid, "HEMS Last Reason"),
    ):
        if not eid:
            continue
        rows.append({"entity": eid, "name": name})
    if predictive_mode_eid:
        rows.append({
            "entity": predictive_mode_eid,
            "name": "Predictive Mode",
        })
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

    Audit T23 round 4: a single
    static literal version (e.g.
    ``?v=1.8.2``) does not
    guarantee the user's browser
    re-fetches the asset when only
    one of the bundled scripts
    changed.

    Audit T23 round 5 (D4):
    ``www_dir`` is the **installed**
    www directory passed by the
    caller
    (``_install_flow_card`` uses
    ``hass.config.config_dir/www/community/powmr-inverter``).
    Hashing the bundled package
    www silently returns the
    SHA-256-of-empty-bytes for
    every asset that is not also
    shipped inside the package.
    """
    h = hashlib.sha256()
    for name in asset_names:
        path = os.path.join(www_dir, name)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            # Audit T23 round 5
            # follow-up: this is a
            # sync I/O helper. The
            # caller
            # (``_install_flow_card``)
            # runs it inside an
            # ``hass.async_add_executor_job``
            # so the event loop is
            # not blocked. We
            # deliberately do NOT
            # touch ``pathlib`` here
            # because the round-trip
            # hash must match the
            # bytes the browser sees.
            data = f.read()
            h.update(name.encode("utf-8"))
            h.update(b"\x00")
            h.update(data)
            h.update(b"\x00")
    return h.hexdigest()[:8]


def _write_dashboards_metadata_atomic(
    storage_path: str, payload: dict
) -> None:
    """Atomic writer for
    ``.storage/lovelace_dashboards``.

    Audit T23 round 5 (D5):
    the previous direct
    ``open(...).write(json.dumps(...))``
    overwrote the metadata file in
    place; a missing parent file
    crashed the registration step,
    and a mid-write crash left a
    half-written file.

    This writer:
      * creates ``storage_path``
        if it does not exist
        (writes the payload via a
        default empty
        ``data.items == []``
        skeleton, atomic);
      * writes the new payload to
        a tempfile in the same
        directory and uses
        ``os.replace`` for the
        atomic move;
      * backs up the existing file
        to ``<storage_path>.bak``
        before replacing.
    """
    target_dir = os.path.dirname(
        os.path.abspath(storage_path)
    )
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=target_dir,
        prefix=".lovelace_dashboards.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        if os.path.exists(storage_path):
            shutil.copyfile(
                storage_path, storage_path + ".bak"
            )
        os.replace(tmp_path, storage_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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
    decision_state = _compute_ai_decision_state(
        hass, entry
    )
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
    await _register_lovelace_dashboard(hass, entry, dashboard_config)


async def _register_lovelace_dashboard(
    hass, entry, dashboard_config
):
    """R10.6 (round 8):
    single-resolver
    registrar.

    Uses
    ``_resolve_dashboard_target``
    to finalise the
    target BEFORE
    capturing state.
    The order is the
    point: target
    finalised →
    state captured →
    decision made →
    file writes →
    binding write.

    The two scenarios
    Юра reproduced in
    round 8 are now
    handled by the
    resolver:

    1. ``B`` has a
       stale wrong
       binding to
       ``powmr-energy``
       (owned by
       ``A``). The
       resolver sets
       ``ownership_conflict=True``
       and re-routes
       to the
       sidecar. With
       ``opt_in=True``
       we write the
       new sidecar.
       ``target_existed_before``
       reflects the
       FINAL target
       (sidecar),
       so rollback
       deletes the
       new file on
       metadata
       failure.

    2. ``B`` has
       a stale
       wrong
       binding to
       ``powmr-energy``,
       ``opt_in=False``.
       The resolver
       re-routes
       to sidecar
       and returns
       ``ownership_conflict=True``.
       The registrar
       sees the
       conflict and
       does NOT
       mutate any
       file. The
       next setup
       run (or an
       explicit
       opt-in) will
       resolve the
       conflict.

    Invariants
    enforced by
    the resolver
    and this
    function:

    * ``target_existed_before``
      is captured
      against the
      FINAL target
      (after
      ownership
      re-route).
    * Content is
      written
      only when
      ``not
      res.target_existed_before
      or
      res.opt_in``
      (i.e. brand-
      new target
      OR user
      opted in to
      migrate).
    * Metadata
      is written
      only after
      the content
      write
      succeeds.
    * Binding is
      written only
      after both
      content and
      metadata
      writes
      succeed.
    * On any
      failure,
      rollback
      uses
      ``res.target_existed_before``
      (the FINAL
      target's
      existence
      flag).
    * ``hass.config_entries.async_update_entry``
      is called
      sync (no
      ``await``).
    """

    res = await _resolve_dashboard_target(
        hass, entry
    )

    # Ownership
    # conflict
    # without opt-in:
    # do NOT touch
    # any file. The
    # helper already
    # cleared B's
    # binding, and
    # we keep A's
    # main + metadata
    # byte-for-byte
    # unchanged.
    if res.ownership_conflict and not res.opt_in:
        # Cross-entry
        # conflict
        # WITHOUT opt-in.
        # The user
        # has not
        # authorised
        # the change.
        # We MUST:
        # 1. Leave
        #    A's main
        #    and
        #    metadata
        #    byte-for-
        #    byte
        #    unchanged.
        # 2. Clear
        #    B's stale
        #    wrong
        #    binding
        #    so B is
        #    not
        #    "confirmed
        #    as owner
        #    of main".
        #    The
        #    binding
        #    is set
        #    to None
        #    (NOT a
        #    non-
        #    existent
        #    sidecar
        #    URL).
        if res.persisted_path is not None:
            hass.config_entries.async_update_entry(
                entry,
                options={
                    **dict(entry.options),
                    "lovelace_dashboard_url_path":
                        None,
                },
            )
            _LOGGER.warning(
                "R10.6 round 8: cross-entry "
                "ownership conflict for "
                "'powmr-energy' (owned by "
                "entry %s); cleared entry "
                "%s's stale binding. "
                "Enable dashboard_migration_opt_in "
                "on the correct entry or "
                "remove the wrong binding "
                "manually.",
                res.other_owner_entry_id,
                entry.entry_id,
            )
        return

    # Re-route
    # to sidecar
    # WITH opt-in:
    # we will write
    # the new
    # sidecar.
    # Without opt-in
    # we don't
    # create the
    # sidecar (the
    # user has not
    # authorised
    # the change).

    # Read the
    # existing
    # metadata
    # payload so we
    # can append to
    # ``items``
    # atomically.
    existing_payload = (
        await hass.async_add_executor_job(
            lambda: _read_metadata_snapshot(
                res.dashboards_storage
            )
        )
    )

    # Resolve
    # write decision.
    write_content = (
        (not res.target_existed_before) or res.opt_in
    )
    # No content
    # write needed:
    # if the target
    # already exists
    # AND no opt-in
    # AND content
    # key matches
    # AND metadata
    # lists it, we
    # can just
    # persist the
    # binding (if
    # needed).
    if not write_content:
        if (
            res.content_key_ok
            and res.metadata_ok
            and res.persisted_path != res.target_url
        ):
            hass.config_entries.async_update_entry(
                entry,
                options={
                    **dict(entry.options),
                    "lovelace_dashboard_url_path":
                        res.target_url,
                },
            )
        return

    # Step A:
    # content write.
    try:
        await _update_dashboard_content(
            hass,
            res.target_path,
            dashboard_config,
            dashboard_id=res.target_id,
        )
    except Exception as exc:
        # Content
        # write
        # failed.
        # Rollback
        # removes
        # the brand-
        # new file
        # (or leaves
        # a previous
        # sidecar
        # intact).
        try:
            await hass.async_add_executor_job(
                lambda: _rollback_dashboard_content(
                    res.target_path,
                    res.target_existed_before,
                )
            )
        except OSError as rb_exc:
            _LOGGER.error(
                "Content rollback failed "
                "after content write "
                "raised: %s",
                rb_exc,
            )
        _LOGGER.error(
            "Dashboard auto-register failed "
            "(content write raised): %s",
            exc,
        )
        raise

    # Step B:
    # metadata
    # write. If
    # this raises,
    # rollback the
    # content write
    # using the
    # FINAL target's
    # ``target_existed_before``
    # (NOT a stale
    # pre-reroute
    # value).
    try:
        if not res.already_listed:
            new_item = {
                "id": res.target_id,
                "icon": "mdi:solar-power",
                "title": res.target_title,
                "show_in_sidebar": True,
                "require_admin": False,
                "mode": "storage",
                "url_path": res.target_url,
            }
            existing_payload["data"]["items"] = (
                res.items + [new_item]
            )

            def _write_metadata():
                _write_dashboards_metadata_atomic(
                    res.dashboards_storage,
                    existing_payload,
                )

            await hass.async_add_executor_job(
                _write_metadata
            )
            _LOGGER.info(
                "✅ Dashboard '%s' (id=%s, "
                "url=%s) registered atomically",
                res.target_title,
                res.target_id,
                res.target_url,
            )
        else:
            # R10.6 (round 9):
            # the target
            # id is
            # already
            # listed in
            # metadata.
            # Check if the
            # existing
            # item's
            # ``url_path``
            # matches the
            # resolved
            # ``target_url``.
            # If not,
            # update the
            # item IN
            # PLACE:
            # overwrite
            # ``url_path``
            # while
            # preserving
            # ``show_in_sidebar``,
            # ``icon``,
            # ``title``,
            # and any
            # other
            # user-
            # customised
            # fields. The
            # previous
            # code
            # silently
            # skipped the
            # metadata
            # write when
            # ``already_listed=True``,
            # leaving the
            # ``url_path``
            # stale and
            # the binding
            # pointing to
            # an
            # unregistered
            # URL.
            existing_item = None
            for _it in res.items:
                if _it.get("id") == res.target_id:
                    existing_item = _it
                    break
            if existing_item is not None and (
                existing_item.get("url_path")
                != res.target_url
            ):
                # Build the
                # patched
                # payload:
                # update only
                # the
                # ``url_path``
                # field of
                # the
                # matching
                # item, keep
                # everything
                # else.
                patched_items = []
                for _it in res.items:
                    if _it.get("id") == res.target_id:
                        _patched = dict(_it)
                        _patched["url_path"] = (
                            res.target_url
                        )
                        patched_items.append(
                            _patched
                        )
                    else:
                        patched_items.append(_it)
                existing_payload["data"]["items"] = (
                    patched_items
                )

                def _write_metadata():
                    _write_dashboards_metadata_atomic(
                        res.dashboards_storage,
                        existing_payload,
                    )

                await hass.async_add_executor_job(
                    _write_metadata
                )
                _LOGGER.info(
                    "✅ Dashboard '%s' (id=%s) "
                    "metadata url_path updated "
                    "from '%s' to '%s'",
                    res.target_title,
                    res.target_id,
                    existing_item.get("url_path"),
                    res.target_url,
                )
    except Exception as exc:
        # R10.6 (round 8):
        # rollback
        # uses the
        # FINAL
        # target's
        # ``target_existed_before``,
        # NOT a
        # pre-reroute
        # value. A
        # brand-new
        # sidecar is
        # DELETED. An
        # existing
        # sidecar is
        # restored
        # byte-for-
        # byte from
        # ``.bak``.
        try:
            await hass.async_add_executor_job(
                lambda: _rollback_dashboard_content(
                    res.target_path,
                    res.target_existed_before,
                )
            )
        except OSError as rb_exc:
            _LOGGER.error(
                "Content rollback failed "
                "after metadata write "
                "raised: %s",
                rb_exc,
            )
        _LOGGER.error(
            "Dashboard auto-register failed "
            "(metadata write raised): %s",
            exc,
        )
        raise

    # Step C:
    # persist
    # binding
    # (sync,
    # no await).
    # Only after
    # BOTH writes
    # succeed.
    if res.persisted_path != res.target_url:
        hass.config_entries.async_update_entry(
            entry,
            options={
                **dict(entry.options),
                "lovelace_dashboard_url_path":
                    res.target_url,
            },
        )

    if res.opt_in and res.target_existed_before:
        # One-shot
        # migration:
        # reset the
        # opt-in flag.
        hass.data.setdefault(
            DOMAIN, {}
        ).setdefault(
            entry.entry_id, {}
        )["dashboard_migration_opt_in"] = False

class _DashboardResolution(NamedTuple):
    target_id: str
    target_url: str
    target_path: str
    target_title: str
    target_existed_before: bool
    content_key_ok: bool
    metadata_ok: bool
    already_listed: bool
    ownership_conflict: bool
    other_owner_entry_id: str | None
    re_routed_to_sidecar: bool
    sidecar_id: str
    sidecar_path: str
    sidecar_url: str
    main_id: str
    main_path: str
    main_url: str
    dashboards_storage: str
    entry_hash: str
    persisted_path: str | None
    opt_in: bool
    items: list
    other_entry_count: int


async def _resolve_dashboard_target(
    hass, entry
):
    """SINGLE resolver. Finalises target BEFORE capturing state."""
    config_dir = hass.config.config_dir
    storage_dir = os.path.join(config_dir, ".storage")
    dashboards_storage = os.path.join(
        storage_dir, "lovelace_dashboards"
    )
    main_path = os.path.join(
        storage_dir, f"lovelace.{_DASHBOARD_ID}"
    )
    _hashlib_d = __import__("hashlib")
    entry_hash = _hashlib_d.md5(
        entry.entry_id.encode("utf-8")
    ).hexdigest()[:16]
    sidecar_id = f"powmr_energy_{entry_hash}"
    sidecar_path = os.path.join(
        storage_dir, f"lovelace.{sidecar_id}"
    )
    sidecar_url = f"powmr-{entry_hash}"

    persisted_path = entry.options.get(
        "lovelace_dashboard_url_path"
    )
    bundle = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    opt_in = bool(
        bundle.get("dashboard_migration_opt_in", False)
    )

    def _collect_state():
        main_exists = os.path.exists(main_path)
        sidecar_exists = os.path.exists(sidecar_path)
        other_owner = None
        other_count = 0
        for e in hass.config_entries.async_entries(DOMAIN):
            if e.entry_id == entry.entry_id:
                continue
            other_count += 1
            if (
                e.options.get(
                    "lovelace_dashboard_url_path"
                )
                == _DASHBOARD_URL
            ):
                other_owner = e.entry_id
        return (
            main_exists, sidecar_exists,
            other_owner, other_count,
        )

    (
        main_exists, sidecar_exists,
        other_owner, other_entry_count,
    ) = await hass.async_add_executor_job(_collect_state)

    ownership_conflict = False
    re_routed_to_sidecar = False

    if sidecar_exists:
        target_id = sidecar_id
        target_url = sidecar_url
        target_path = sidecar_path
        target_title = (
            f"Smart Solar · "
            f"{entry.title or entry.entry_id[:8]}"
        )
        target_existed_before = True
    elif persisted_path == _DASHBOARD_URL:
        if other_owner is not None:
            ownership_conflict = True
            re_routed_to_sidecar = True
            target_id = sidecar_id
            target_url = sidecar_url
            target_path = sidecar_path
            target_title = (
                f"Smart Solar · "
                f"{entry.title or entry.entry_id[:8]}"
            )
            target_existed_before = sidecar_exists
        else:
            target_id = _DASHBOARD_ID
            target_url = _DASHBOARD_URL
            target_path = main_path
            target_title = _DASHBOARD_TITLE
            target_existed_before = main_exists
    elif not main_exists:
        target_id = _DASHBOARD_ID
        target_url = _DASHBOARD_URL
        target_path = main_path
        target_title = _DASHBOARD_TITLE
        target_existed_before = False
    else:
        target_id = sidecar_id
        target_url = sidecar_url
        target_path = sidecar_path
        target_title = (
            f"Smart Solar · "
            f"{entry.title or entry.entry_id[:8]}"
        )
        target_existed_before = sidecar_exists

    # R10.6 (round 9):
    # ownership check
    # is applied to
    # ALL branches
    # (the previous
    # code only
    # checked in the
    # ``persisted_path
    # == _DASHBOARD_URL``
    # branch). If the
    # resolved
    # target is the
    # canonical main
    # AND another
    # powmr_inverter
    # entry already
    # has the main
    # binding, we
    # MUST re-route
    # to the sidecar
    # — regardless
    # of whether the
    # main file
    # exists, the
    # content key
    # matches, or
    # the metadata
    # lists it.
    # "Перевіряй
    # ownership
    # незалежно від
    # існування
    # файла."
    if (
        target_id == _DASHBOARD_ID
        and other_owner is not None
        and other_owner != entry.entry_id
    ):
        ownership_conflict = True
        re_routed_to_sidecar = True
        target_id = sidecar_id
        target_url = sidecar_url
        target_path = sidecar_path
        target_title = (
            f"Smart Solar · "
            f"{entry.title or entry.entry_id[:8]}"
        )
        target_existed_before = sidecar_exists
        _LOGGER.debug(
            "R10.6 round 9: ownership check "
            "forced re-route to sidecar "
            "(main is owned by entry %s, "
            "current entry is %s)",
            other_owner,
            entry.entry_id,
        )

    def _read_key():
        try:
            with open(target_path, "r") as _f:
                data = json.loads(_f.read())
            return (
                data.get("key")
                == f"lovelace.{target_id}"
            )
        except (OSError, ValueError):
            return False

    content_key_ok = await hass.async_add_executor_job(_read_key)

    def _check_metadata():
        return _metadata_lists_dashboard(
            dashboards_storage, target_id, target_url
        )

    metadata_ok = await hass.async_add_executor_job(_check_metadata)

    def _read_items():
        snap = _read_metadata_snapshot(dashboards_storage)
        return snap.get("data", {}).get("items", [])

    items = await hass.async_add_executor_job(_read_items)
    already_listed = any(
        item.get("id") == target_id for item in items
    )

    return _DashboardResolution(
        target_id=target_id,
        target_url=target_url,
        target_path=target_path,
        target_title=target_title,
        target_existed_before=target_existed_before,
        content_key_ok=content_key_ok,
        metadata_ok=metadata_ok,
        already_listed=already_listed,
        ownership_conflict=ownership_conflict,
        other_owner_entry_id=other_owner,
        re_routed_to_sidecar=re_routed_to_sidecar,
        sidecar_id=sidecar_id,
        sidecar_path=sidecar_path,
        sidecar_url=sidecar_url,
        main_id=_DASHBOARD_ID,
        main_path=main_path,
        main_url=_DASHBOARD_URL,
        dashboards_storage=dashboards_storage,
        entry_hash=entry_hash,
        persisted_path=persisted_path,
        opt_in=opt_in,
        items=items,
        other_entry_count=other_entry_count,
    )


async def _ensure_dashboard_binding(hass, entry):
    """R10.6 (round 8): resolver-based binding helper.

    Invariants:
    * Binding persisted ONLY when target existed before AND
      content_key matches AND metadata lists it.
    * On cross-entry ownership conflict, the binding is
      cleared (set to None). Do NOT re-route to a
      non-existent sidecar.
    * No file writes. async_update_entry is sync.
    """
    res = await _resolve_dashboard_target(hass, entry)

    if res.ownership_conflict:
        if res.persisted_path is not None:
            hass.config_entries.async_update_entry(
                entry,
                options={
                    **dict(entry.options),
                    "lovelace_dashboard_url_path": None,
                },
            )
            _LOGGER.warning(
                "R10.6 round 8: cross-entry ownership "
                "conflict for 'powmr-energy' (owned by "
                "entry %s); cleared entry %s's stale "
                "binding. Enable dashboard_migration_opt_in "
                "on the correct entry or remove the wrong "
                "binding manually.",
                res.other_owner_entry_id,
                entry.entry_id,
            )
        return

    if not res.target_existed_before:
        return
    if not res.content_key_ok:
        return
    if not res.metadata_ok:
        return
    if res.persisted_path == res.target_url:
        return

    hass.config_entries.async_update_entry(
        entry,
        options={
            **dict(entry.options),
            "lovelace_dashboard_url_path": res.target_url,
        },
    )



def _is_dashboard_owned_by_other(
    hass: HomeAssistant,
    current_entry_id: str,
    target_url: str,
) -> bool:
    """R10.6 (round 7):
    return True if any
    OTHER
    powmr_inverter config
    entry has its
    ``lovelace_dashboard_url_path``
    binding set to
    ``target_url``. This
    is the
    cross-entry
    ownership check the
    resolver and the
    setup helper share
    so a new entry can
    never claim a
    dashboard that is
    already bound to
    another entry.

    The check is local:
    it iterates
    ``hass.config_entries.async_entries(DOMAIN)``
    and looks at each
    entry's
    ``options`` for the
    binding. The
    current entry is
    excluded.
    """

    try:
        for e in hass.config_entries.async_entries(
            DOMAIN
        ):
            if e.entry_id == current_entry_id:
                continue
            if (
                e.options.get(
                    "lovelace_dashboard_url_path"
                )
                == target_url
            ):
                return True
    except AttributeError:
        # ``hass.config_entries``
        # not in the
        # expected shape
        # (e.g. test
        # harness without
        # ``async_entries``).
        # Treat as "not
        # owned" so the
        # caller can fall
        # back to a
        # sidecar.
        return False
    return False


def _metadata_lists_dashboard(
    dashboards_storage: str,
    target_id: str,
    target_url: str,
) -> bool:
    """R10.6 (round 7):
    return True if the
    Lovelace metadata
    file at
    ``dashboards_storage``
    contains an item
    whose ``id`` AND
    ``url_path`` both
    match the
    expected values. A
    content file on
    disk is not enough
    to claim a
    dashboard — HA only
    knows about
    dashboards that are
    registered in
    ``lovelace_dashboards``.

    A missing or
    corrupt metadata
    file returns False
    (the dashboard is
    not registered yet).
    """

    try:
        with open(dashboards_storage, "r") as f:
            data = json.loads(f.read())
    except (OSError, ValueError):
        return False
    for item in (
        data.get("data", {}).get("items", [])
    ):
        if (
            item.get("id") == target_id
            and item.get("url_path") == target_url
        ):
            return True
    return False


def _read_metadata_snapshot(dashboards_storage: str) -> dict:
    """Read the existing
    ``lovelace_dashboards``
    metadata snapshot, or
    return a fresh empty
    payload when the file
    does not exist.

    Module-level helper so the
    ``async_add_executor_job``
    closure stays small.
    """
    if not os.path.exists(dashboards_storage):
        return {
            "version": 1,
            "minor_version": 1,
            "key_version": 1,
            "data": {"items": []},
        }
    with open(dashboards_storage, "r") as f:
        return json.loads(f.read())


def _rollback_dashboard_content(
    target_path: str, target_existed: bool
) -> None:
    """Roll back the content
    file at ``target_path``
    after a metadata write
    failure.

    Audit T22 round 9 / 10
    (R9.2 → R10.1): the
    rollback MUST be atomic
    so a crash mid-rollback
    does not leave the
    target in a partial
    state. The previous
    round-9 implementation
    used ``shutil.copyfile``
    (non-atomic — a partial
    write could leave the
    target as truncated
    JSON), and removed the
    ``.bak`` in a ``finally``
    even when the copy had
    failed (so the user
    lost BOTH the previous
    content and the
    backup). The round-10
    rewrite:

      * Restores through a
        ``.tmp`` file in the
        same directory
        followed by
        ``os.replace`` so the
        target is replaced in
        a single atomic step.
        The previous content
        on disk is never
        observed in a partial
        state.
      * Keeps the ``.bak``
        when the restore
        FAILS, so a manual
        recovery is still
        possible.
      * Removes the ``.bak``
        only after the
        ``os.replace`` of the
        restored file
        succeeded.
      * Is idempotent: a
        second call when the
        ``.bak`` is no longer
        present is a no-op.
      * Does NOT use the
        presence of ``.bak``
        as the indicator that
        the target existed
        before the call. That
        information is passed
        explicitly via
        ``target_existed``,
        which the caller
        already knows from
        ``already_registered``.
    """
    bak_path = target_path + ".bak"
    if not target_existed:
        # Brand-new file: the
        # current operation
        # created it. There is
        # no previous content
        # to restore. Remove
        # the freshly written
        # target so we do not
        # leave a dangling
        # content file without
        # matching metadata.
        if os.path.exists(target_path):
            try:
                os.unlink(target_path)
            except OSError as exc:
                _LOGGER.error(
                    "Rollback: failed to remove "
                    "freshly written target %s: %s",
                    target_path, exc,
                )
        return
    # target_existed is True.
    # The previous content
    # is in ``.bak`` (if the
    # writer kept it). If
    # the ``.bak`` is
    # missing — for example
    # because the disk was
    # wiped between the
    # failed write and the
    # rollback — we keep the
    # current (new) target on
    # disk rather than
    # deleting it, because
    # the user could be left
    # with no content file
    # at all.
    if not os.path.exists(bak_path):
        _LOGGER.warning(
            "Rollback: %s expected to exist "
            "(target_existed=True) but is missing; "
            "leaving the current target in place",
            bak_path,
        )
        return
    # Atomic restore: write
    # to a sibling temp
    # file, then ``os.replace``
    # over the target. If
    # the copy fails, the
    # ``.bak`` is preserved.
    target_dir = os.path.dirname(
        os.path.abspath(target_path)
    )
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=target_dir,
        prefix=".lovelace.rollback.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(tmp_fd, "wb") as tmp_f:
            with open(bak_path, "rb") as bak_f:
                shutil.copyfileobj(bak_f, tmp_f)
        os.replace(tmp_path, target_path)
    except OSError as exc:
        # Preserve the
        # ``.bak`` for manual
        # recovery. Clean up
        # the temp file if it
        # was created.
        _LOGGER.error(
            "Rollback: failed to restore %s from %s: %s; "
            "preserving the .bak for manual recovery",
            target_path, bak_path, exc,
        )
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return
    # Restore succeeded.
    # The ``.bak`` is now
    # safe to remove: the
    # target holds the same
    # bytes as the ``.bak``
    # used to. If the
    # ``.bak`` removal
    # fails, log and
    # continue — the next
    # ``_write_dashboard_atomic``
    # will overwrite the
    # ``.bak`` anyway.
    try:
        os.unlink(bak_path)
    except OSError as exc:
        _LOGGER.warning(
            "Rollback: failed to remove %s after "
            "successful restore: %s",
            bak_path, exc,
        )


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
        # The ``key`` field MUST
        # match the ``id`` in
        # ``lovelace_dashboards``
        # so HA can pair them.
        # Audit R7.3: the caller
        # passes the same
        # ``dashboard_id`` we
        # use to register the
        # sidecar metadata entry.
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
