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
    hass: HomeAssistant,
    entry: ConfigEntry,
    dashboard_config: dict,
) -> None:
    """Auto-register the dashboard in storage mode.

    Writes dashboard metadata to .storage/lovelace_dashboards
    and the actual dashboard config to .storage/lovelace.powmr_energy.
    The config is stored as a JSON object — NOT a YAML string — which
    avoids the "Cannot use 'in' operator to search for 'strategy'" crash.

    Audit T23 round 6 (R6.1):
    ``hass.config_entries.async_entries(DOMAIN)``
    returns a ``list``, not a
    generator. The previous code
    called ``.__next__()`` on the
    list and raised
    ``AttributeError: 'list'
    object has no attribute
    '__next__'``. The helper now
    takes the ``entry`` as an
    explicit argument so it does
    not need to iterate
    ``async_entries`` at all.

    Audit T23 round 5 (D1): when
    the integration dashboard is
    already registered (the
    ``.storage/lovelace.powmr_energy``
    file exists), this helper
    MUST NOT overwrite it unless
    ``hass.data[DOMAIN][entry_id]
    ["dashboard_migration_opt_in"]``
    is True. The user opts in
    via the
    ``powmr_inverter.migrate_dashboard``
    service. The migration is
    one-shot — after the
    migration, the flag is reset
    to False so subsequent
    reloads preserve the new
    state.

    Two entries on one HA
    install share the
    lovelace_dashboards file;
    each entry's
    ``hass.data[DOMAIN][entry_id]``
    can opt-in independently.
    The helper is called per
    entry.
    """
    DASHBOARD_URL = "powmr-energy"
    DASHBOARD_TITLE = "Smart Solar Енергопанель"
    DASHBOARD_ID = "powmr_energy"
    config_dir = hass.config.config_dir
    dashboards_storage = os.path.join(config_dir, ".storage", "lovelace_dashboards")

    # Audit R7.4: per-entry
    # dashboard file names use
    # a full-content hash of the
    # entry_id, NOT a truncated
    # prefix. Two entry IDs
    # whose first 8 characters
    # collides after ``[:8]``
    # must still get distinct
    # files.
    import hashlib as _hashlib_d
    entry_hash = _hashlib_d.md5(
        entry.entry_id.encode("utf-8")
    ).hexdigest()[:16]
    sidecar_id = f"powmr_energy_{entry_hash}"
    sidecar_path = os.path.join(
        config_dir,
        ".storage",
        f"lovelace.{sidecar_id}",
    )
    # The first opt-in wins the
    # canonical main dashboard;
    # subsequent entries write
    # to their own sidecar.
    main_path = os.path.join(
        config_dir, ".storage", f"lovelace.{DASHBOARD_ID}"
    )
    sidecar_glob = os.path.join(
        config_dir,
        # Audit R7.4: sidecar
        # files use
        # ``lovelace.powmr_energy_<md5>``.
        # The glob must include
        # both the legacy
        # ``.`` separator (round 6
        # before R7.4) and the
        # new ``_`` separator
        # so entries that migrated
        # with round 6 are still
        # detected.
        ".storage",
        "lovelace.powmr_energy*",
    )
    import glob as _glob
    # ``all_sidecars`` includes
    # OUR sidecar so we can
    # detect a previous registration
    # for the SAME ``entry_id``.
    # Audit round 4: reload and
    # migration must NOT create a
    # duplicate dashboard for the
    # same ``entry_id``. The previous
    # implementation excluded our
    # own sidecar (``p != sidecar_path``)
    # and computed ``is_first_opt_in``
    # from "any other sidecar exists";
    # that meant every setup after
    # the canonical ``main_path``
    # existed (legacy migration, R7)
    # was treated as a *new* entry and
    # silently produced a *second*
    # dashboard pointing at the same
    # inverter. We now:
    # 1. Read the persistent binding
    #    from
    #    ``entry.options["lovelace_dashboard_url_path"]``
    #    — if it matches our
    #    ``sidecar_path`` or
    #    ``main_path`` we reuse it
    #    and skip the create step.
    # 2. If a sidecar for OUR
    #    ``entry_id`` already exists
    #    on disk, reuse it
    #    (idempotent registration).
    # 3. Only when ``main_path``
    #    exists AND belongs to a
    #    *different* ``entry_id`` do
    #    we write a separate sidecar.
    all_sidecars = sorted(
        p for p in _glob.glob(sidecar_glob)
        if not p.endswith(".bak")
    )
    own_sidecar_exists = os.path.exists(sidecar_path)

    # Per-entry persistent binding
    # stored in ``entry.options``.
    # The setup path writes this
    # once; subsequent setups reuse
    # it verbatim.
    persisted_path = entry.options.get(
        "lovelace_dashboard_url_path"
    )

    # Round 4 follow-up
    # (R10.6): ``entry.options``
    # is a read-only
    # ``MappingProxyType`` in HA
    # 2026.10.0b0. Direct
    # item assignment raises
    # ``TypeError``. We
    # accumulate the binding
    # to persist in
    # ``must_persist_binding``
    # and route the write
    # through
    # ``hass.config_entries.async_update_entry``
    # below.
    must_persist_binding: str | None = None
    # Defaults for the
    # resolve phase.
    # R10.6: every branch
    # below MUST overwrite
    # these before the
    # write phase.
    dashboard_content_storage: str = ""
    active_id: str = ""
    active_url: str = ""
    active_title: str = (
        f"Smart Solar · {entry.title or entry.entry_id[:8]}"
    )
    is_first_opt_in: bool = False

    if own_sidecar_exists:
        # Idempotent: this
        # ``entry_id`` already
        # has its sidecar
        # registered. Reuse it.
        dashboard_content_storage = sidecar_path
        active_id: str = sidecar_id
        active_url: str = f"powmr-{entry_hash}"
        active_title: str = (
            f"Smart Solar · {entry.title or entry.entry_id[:8]}"
        )
        is_first_opt_in = False
        # Persist the binding so
        # we never create a
        # second dashboard even
        # if the disk file is
        # deleted out-of-band.
        # R10.6: ``entry.options``
        # is a read-only
        # ``MappingProxyType`` —
        # route through
        # ``async_update_entry``.
        if persisted_path != active_url:
            must_persist_binding = active_url
    elif persisted_path is not None:
        # We have a binding from
        # a prior entry.options.
        # R10.6: if the binding
        # no longer matches a
        # registered dashboard,
        # we MUST NOT fall
        # through to ``main``
        # (that would overwrite
        # another entry's user
        # content). The
        # ``binding_is_stale``
        # branch below routes
        # the request to a
        # fresh sidecar instead.
        match_id: str | None = None
        match_title = ""
        binding_is_stale = False
        try:
            existing_payload = (
                await hass.async_add_executor_job(
                    lambda: _read_metadata_snapshot(
                        dashboards_storage
                    )
                )
            )
            for it in existing_payload.get(
                "data", {}
            ).get("items", []):
                if it.get("url_path") == persisted_path:
                    match_id = it.get("id")
                    match_title = it.get("title", "") or (
                        entry.title or entry.entry_id[:8]
                    )
                    break
        except Exception:
            match_id = None
            binding_is_stale = True
        if match_id is not None:
            content_path = os.path.join(
                config_dir,
                ".storage",
                f"lovelace.{match_id}",
            )
            if os.path.exists(content_path):
                # Live binding —
                # reuse the
                # dashboard.
                dashboard_content_storage = content_path
                active_id = match_id
                active_url = persisted_path
                active_title = match_title
                is_first_opt_in = False
            else:
                # Stale binding:
                # url_path is in
                # metadata but the
                # content file is
                # gone. R10.6: do
                # NOT touch
                # ``main``; route
                # to a fresh
                # sidecar.
                binding_is_stale = True
        else:
            # Stale binding:
            # ``persisted_path``
            # does not match any
            # registered
            # dashboard. R10.6:
            # do NOT touch
            # ``main``; route to
            # a fresh sidecar.
            binding_is_stale = True
        if binding_is_stale:
            # R10.6: pick a
            # sidecar to avoid
            # overwriting
            # another entry's
            # main.
            dashboard_content_storage = sidecar_path
            active_id = sidecar_id
            active_url = f"powmr-{entry_hash}"
            active_title = (
                f"Smart Solar · {entry.title or entry.entry_id[:8]}"
            )
            is_first_opt_in = False
            if persisted_path != active_url:
                must_persist_binding = active_url
    else:
        # No binding. R10.6:
        # if ``main`` does NOT
        # exist yet (truly
        # fresh install) write
        # ``main`` and record
        # the binding. If
        # ``main`` already
        # exists (legacy /
        # another entry),
        # create a sidecar
        # instead and record
        # the sidecar's
        # binding.
        if not os.path.exists(main_path):
            is_first_opt_in = True
            dashboard_content_storage = main_path
            active_id = DASHBOARD_ID
            active_url = DASHBOARD_URL
            active_title = DASHBOARD_TITLE
            if persisted_path != active_url:
                must_persist_binding = active_url
        else:
            dashboard_content_storage = sidecar_path
            active_id = sidecar_id
            active_url = f"powmr-{entry_hash}"
            active_title = (
                f"Smart Solar · {entry.title or entry.entry_id[:8]}"
            )
            is_first_opt_in = False
            if persisted_path != active_url:
                must_persist_binding = active_url

    # R10.6: persist the
    # binding via
    # ``hass.config_entries.async_update_entry``.
    # ``entry.options`` is a
    # read-only
    # ``MappingProxyType`` in
    # HA 2026.10.0b0; direct
    # ``entry.options[...] = ...``
    # raises ``TypeError``.
    if must_persist_binding is not None:
        await hass.config_entries.async_update_entry(
            entry,
            options={
                **dict(entry.options),
                "lovelace_dashboard_url_path":
                    must_persist_binding,
            },
        )

    # Audit T23 round 6: the opt-in
    # flag is read PER ENTRY from
    # ``hass.data[DOMAIN][entry.entry_id]``.
    # No ``async_entries(...)``,
    # no ``.__next__()``.
    bundle = hass.data.get(DOMAIN, {}).get(
        entry.entry_id, {}
    )
    opt_in = bool(bundle.get("dashboard_migration_opt_in", False))

    # Step 1: Check if already registered AND not opt-in
    already_registered = False
    try:
        def _check() -> bool:
            return os.path.exists(dashboard_content_storage)

        already_registered = await hass.async_add_executor_job(_check)
        if already_registered and not opt_in:
            # D1: existing user dashboard
            # is preserved byte-for-byte.
            _LOGGER.debug(
                "Dashboard %s already registered; "
                "no opt-in, leaving existing "
                "dashboard untouched",
                DASHBOARD_URL,
            )
            return
    except Exception as exc:
        _LOGGER.debug("Dashboard check failed: %s", exc)

    # Step 2: Read the existing
    # ``lovelace_dashboards``
    # metadata snapshot. We
    # capture the existing
    # payload BEFORE we touch
    # either the content file
    # or the metadata file so we
    # can roll back atomically
    # on either write failure.
    # Audit R7.5 rejects dangling
    # registration — the
    # metadata MUST NOT list a
    # dashboard whose content
    # file does not exist on
    # disk.
    existing_payload = await hass.async_add_executor_job(
        lambda: _read_metadata_snapshot(dashboards_storage)
    )
    items = existing_payload.get(
        "data", {}
    ).get("items", [])

    # Audit R7.3: register
    # THIS entry's sidecar
    # dashboard in the
    # Lovelace metadata with a
    # unique ``url_path`` and
    # ``id`` so the sidecar is
    # actually navigable. The
    # canonical main dashboard
    # is registered with
    # ``DASHBOARD_ID`` and
    # ``DASHBOARD_URL``; a
    # sidecar uses ``active_id``
    # / ``active_url`` derived
    # from the entry's content
    # hash.
    target_id = active_id
    target_url = active_url
    target_title = active_title
    already_listed = any(
        item.get("id") == target_id
        for item in items
    )

    # Step 3 (audit R7.5):
    # write the content file
    # FIRST. If the content
    # write raises, no metadata
    # is touched and the caller
    # sees a clean failure with
    # no dangling registration.
    # The previous code wrote
    # metadata first then
    # content, which left a
    # dangling registration on
    # content failure — the
    # sidebar would show a
    # dashboard that 404s.
    if not already_registered or opt_in:
        await _update_dashboard_content(
            hass,
            dashboard_content_storage,
            dashboard_config,
            dashboard_id=active_id,
        )
    else:
        # D1: already registered
        # AND no opt-in. Leave
        # everything untouched.
        return

    # Step 4: only after the
    # content write succeeded,
    # append the metadata entry.
    # If this raises, remove the
    # freshly written content
    # file so the dashboard
    # does not appear in the
    # sidebar without a
    # resolvable content
    # payload. Re-raise so the
    # service handler sees
    # ``ok=False``.
    try:
        if not already_listed:
            items.append({
                "id": target_id,
                "icon": "mdi:solar-power",
                "title": target_title,
                "show_in_sidebar": True,
                "require_admin": False,
                "mode": "storage",
                "url_path": target_url,
            })
            existing_payload["data"]["items"] = items

            def _write_metadata() -> None:
                _write_dashboards_metadata_atomic(
                    dashboards_storage, existing_payload
                )

            await hass.async_add_executor_job(_write_metadata)
            _LOGGER.info(
                "✅ Dashboard '%s' (id=%s, url=%s) "
                "registered atomically",
                target_title, target_id, target_url,
            )
        else:
            _LOGGER.debug(
                "Dashboard id=%s already listed; "
                "metadata not modified",
                target_id,
            )
    except Exception as exc:
        # Roll back so we do not
        # leave a dangling
        # dashboard in the
        # Lovelace sidebar.
        # Audit T22 round 9
        # (R9.2): ``_write_dashboard_atomic``
        # already keeps a
        # ``target_path + ".bak"``
        # copy of the PREVIOUS
        # content before the
        # ``os.replace``. The
        # rollback MUST restore
        # the previous content
        # from the ``.bak`` if
        # the target already
        # existed before this
        # call. Removing the
        # target unconditionally
        # would clobber an
        # already-registered
        # sidecar that we just
        # tried to update.
        # Only when the target
        # is a brand-new file
        # (no ``.bak`` was ever
        # created because the
        # file did not exist
        # before) do we delete
        # the freshly written
        # content.
        try:
            await hass.async_add_executor_job(
                lambda: _rollback_dashboard_content(
                    dashboard_content_storage,
                    # Audit R10.1: the
                    # caller already
                    # knows whether the
                    # target existed
                    # before this
                    # operation from
                    # ``already_registered``.
                    # The presence of
                    # ``.bak`` is NOT a
                    # reliable indicator
                    # (a stale ``.bak``
                    # from an earlier
                    # failure, or a
                    # missing ``.bak``
                    # after a disk wipe,
                    # would otherwise
                    # mislead the
                    # rollback).
                    bool(already_registered),
                )
            )
        except OSError as rollback_exc:
            _LOGGER.error(
                "Content rollback failed after "
                "metadata write raised: %s",
                rollback_exc,
            )
        _LOGGER.error(
            "Dashboard auto-register failed "
            "(metadata write raised): %s",
            exc,
        )
        raise

    if already_registered and opt_in:
        _LOGGER.info(
            "Dashboard %s migrated "
            "(opt-in honoured)",
            target_url,
        )
        # R6.5: the migration
        # is one-shot. Reset
        # the opt-in flag so
        # the next reload does
        # NOT silently
        # overwrite again.
        hass.data.setdefault(
            DOMAIN, {}
        ).setdefault(
            entry.entry_id, {}
        )["dashboard_migration_opt_in"] = False
    elif not already_listed:
        _LOGGER.info(
            "✅ Dashboard '%s' content "
            "written (first install)",
            target_title,
        )


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
