"""HEMS Engine — core decision logic for inverter control.

Ported from Flutter HemsAlgorithmService (1034 lines).
Manages output priority, charger priority, battery keepalive,
manual override, anti-flapping, circuit breaker, and all smart modes.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as _tz
from datetime import datetime as _dt
from enum import IntEnum
from typing import Any

# Canonical UTC alias used by the dispatcher helper below.
_UTC = _tz.utc


def _as_utc(dt: datetime) -> datetime:
    """Promote a naive ``datetime`` to UTC; pass through aware.

    Naive datetimes are interpreted as the SYSTEM's local wall
    clock (the standard Python convention), then converted to
    UTC. The previous implementation used ``dt.replace(tzinfo=
    _UTC)``, which labels the LOCAL value as if it were already
    UTC, producing a 3-hour skew on EEST (Codex reproduced
    the bug -- ``_evaluation_hold`` skipped the active circuit
    breaker on Europe/Kyiv because ``_blocked_until`` was set
    to a UTC-aware 08:01 while naive ``now`` was promoted to
    ``08:01`` UTC instead of the correct 11:01 EEST).
    """
    if not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is None:
        # ``astimezone()`` with no argument attaches the
        # system's local tz to a naive dt, then ``astimezone(
        # _UTC)`` converts to UTC. On EEST the local tz is
        # UTC+3 so ``datetime(2026, 10, 14, 11, 0)`` becomes
        # ``datetime(2026, 10, 14, 8, 0, tzinfo=UTC)``.
        return dt.astimezone().astimezone(_UTC)
    return dt.astimezone(_UTC)


def _tz_compare(a: datetime, b: datetime) -> int:
    """Compare two datetimes regardless of tz-awareness.

    Both are promoted to UTC. Returns negative if ``a < b``,
    positive if ``a > b``, zero if equal. Used by the HEMS
    engine's guards so naive ``now`` from the coordinator and
    aware ``_blocked_until`` from the dispatcher compare
    correctly (Codex reproduced the 3-hour skew bug).
    """
    a_utc = _as_utc(a)
    b_utc = _as_utc(b)
    if a_utc < b_utc:
        return -1
    if a_utc > b_utc:
        return 1
    return 0


def _tz_diff_seconds(a: datetime, b: datetime) -> float:
    """``a - b`` in seconds, regardless of tz-awareness."""
    a_utc = _as_utc(a)
    b_utc = _as_utc(b)
    return (a_utc - b_utc).total_seconds()

# Alias for the common ``datetime.now(tz=...)`` default. The
# ``can_dispatch_outage_decision`` method below uses it as the
# fallback tz when an engine timestamp happens to be naive.
_UTC = _tz.utc

from .tuning import HemsTuningService, HemsTunables
from . import debug_logging

_LOGGER = logging.getLogger(__name__)


# Normalize output/charger display strings to numeric for comparison
_OUTPUT_DISPLAY_TO_NUM = {"USB": "0", "SBU": "2", "Line Mode": "0", "Battery Mode": "2"}
_CHARGER_DISPLAY_TO_NUM = {"CSO": "0", "SNU": "1", "OSO": "2", "UTO": "3"}

def _normalize_setting(value: str | int | None, labels: dict[str, str]) -> str | None:
    """Accept display labels and numeric API codes at the engine boundary."""
    if isinstance(value, str):
        key = value.strip()
    elif type(value) is int:
        key = str(value)
    else:
        return None
    return labels.get(key, key)


def _normalize_output(value: str | int | None) -> str | None:
    return _normalize_setting(value, _OUTPUT_DISPLAY_TO_NUM)


def _normalize_charger(value: str | int | None) -> str | None:
    return _normalize_setting(value, _CHARGER_DISPLAY_TO_NUM)


def _coerce_tariff_fallback(value: Any, *, default: float) -> float:
    """R05: validate a tariff-fallback rate.

    Contract:
      - ``None``           → engine has not been bound
                             to a coordinator (cold
                             start / test fixture).
                             Returns ``default``.
      - ``0.0``            → valid operator input
                             (free electricity, e.g.
                             solar surplus). PRESERVED
                             — NOT replaced by
                             ``default``. This is the
                             explicit fix for the
                             ``... or default`` pattern
                             which silently swallowed
                             valid zero.
      - finite, ``0 ≤ x``  → operator's choice.
      - NaN / Inf / bool /
        string / negative /
        too-large          → rejected. Returns
                             ``default``.

    The "too-large" bound is 50 UAH/kWh — well above
    the documented Ukraine peak of 4.32 day/2.16 night
    plus any reasonable margin. The lower bound is
    exactly 0 (not -0): tariffs cannot be negative.
    """
    n = _finite_number(value)
    if n is None:
        return default
    if n < 0 or n > 50:
        return default
    return n


def _finite_number(value: Any) -> float | None:
    """Unknown/nonfinite telemetry must never masquerade as a full battery.

    Returns ``None`` for anything that is not a real (non-bool)
    number. Strings, even parseable ones like ``"230"``, are
    refused: the production sources emit real floats, and a
    string is the symptom of a corrupted HA option or a
    bug in a future contributor's code. Rejecting parseable
    strings at the gate keeps the failure mode obvious.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _forecast_energy(value: Any) -> float | None:
    number = _finite_number(value)
    return number if number is not None and number >= 0 else None


def _hint_soc_target(hint: Any, attribute: str) -> float | None:
    number = _finite_number(getattr(hint, attribute, None))
    return number if number is not None and 0 <= number <= 100 else None


class SmartMode(IntEnum):
    ADAPTIVE = 0
    ARBITRAGE = 1
    STORM = 2


class OutputPriority:
    USB = "0"  # Grid first
    SBU = "2"  # Solar/Battery first


class ChargerPriority:
    SNU = "1"  # Solar + Utility
    OSO = "2"  # Solar only


class _Reason:
    """Machine-readable reason codes for control writes."""

    MANUAL_OVERRIDE = "manual_override_hold"
    DEDUP_OUTPUT = "dedup_skip_output"
    DEDUP_CHARGER = "dedup_skip_charger"
    DWELL_LOCK = "dwell_lock"
    RESERVE_PROTECTION = "reserve_soc_protection"
    NIGHT_USB = "night_window_usb"
    TARIFF_DEFER = "tariff_expensive_defer"
    NIGHT_CHEAP_NOW = "night_charge_deficit_cheap_now"
    NIGHT_NO_CHEAP = "night_charge_no_cheap_window"
    CHARGER_DAY_SOLAR = "charger_day_solar_only"
    SURPLUS_SBU = "surplus_enter_sbu"
    EVENING_PROTECT = "evening_reserve_protection"
    EVENING_BATTERY_USE = "evening_battery_use"
    FORECAST_DEFICIT_LOW = "day_forecast_deficit_low_soc"
    FORECAST_OK = "day_forecast_ok"
    HOLD = "hold_current_state"
    KEEPALIVE_START = "keepalive_start"
    KEEPALIVE_END = "keepalive_end"
    GRID_OUTAGE_PRECHARGE = "grid_outage_precharge"
    STORM_AUTO = "storm_auto_forecast"
    STORM_GRID_OUTAGE = "storm_grid_outage"
    EMERGENCY_STALE = "emergency_stale_data"
    EMERGENCY_LOW_SOC = "emergency_low_soc_daytime"


@dataclass
class HemsDecision:
    """Result of one HEMS evaluation cycle."""

    output_priority: str | None = None  # OutputUSB or OutputSBU
    charger_priority: str | None = None  # ChargerSNU or ChargerOSO
    reason: str = ""
    skip: bool = False
    buzzer_off: bool = False  # Acoustic comfort


@dataclass
class BatteryKeepaliveState:
    """Tracks battery keepalive timing."""

    last_activity_at: datetime | None = None
    in_progress: bool = False

    # Constants (port from Dart)
    INTERVAL_HOURS: int = 2
    DURATION_SEC: int = 90
    MIN_SOC: float = 22.0
    POWER_INACTIVE_THRESHOLD: float = 50.0


@dataclass
class DispatcherResult:
    """Result of one ``InverterCoordinator._execute_hems_command`` call.

    Three orthogonal facts about the dispatch:

      * ``dispatched`` — the dispatcher made the call no immediate
        exception. ``False`` when the HEMS guard pre-flight refused
        the write (so ``api`` was never touched), or when the
        coordinator had no ``api`` attribute, or when the cycle
        was a no-op (all channels match the current state).
      * ``api_accepted`` — every individual ``api.set_*`` call
        returned a truthy success value (HTTP 2xx or the fake
        transport's success sentinel). ``False`` if any single
        write was rejected. Also ``False`` for a no-op because
        no api call was made.
      * ``readback_valid`` — a follow-up GET confirmed the same
        value persisted on the device. ``False`` on readback
        mismatch OR when no readback could be performed
        (typically when ``dispatched`` is already ``False``).

    Additional flags:
      * ``readback_skipped`` -- the readback was intentionally
        skipped because the api lacks ``fetch_device_configs``
        or the cycle was a no-op.
      * ``no_op`` -- the cycle was an all-channel no-op: the
        engine's proposal matched the current state across every
        channel the dispatcher controls, so no api call was
        needed. ``dispatched`` is ``False`` and ``api_accepted``
        is ``False``; this is the canonical way to distinguish
        "we did not need to write" from "we tried to write and
        it succeeded".

    The convenience property ``success`` is the strict AND of
    ``dispatched`` / ``api_accepted`` / ``readback_valid`` so the
    runtime's contract becomes a single boolean:
    ``executed = result.success``. Anything else must surface
    ``result.error`` as the audit-log reason.
    """

    dispatched: bool = False
    api_accepted: bool = False
    readback: dict | None = None
    readback_valid: bool = False
    readback_skipped: bool = False
    no_op: bool = False
    error: str | None = None

    @property
    def success(self) -> bool:
        """Strict AND of dispatched / api_accepted / readback_valid.

        ``readback_skipped`` is informational: a skipped readback
        (``api_accepted`` only) is treated as success because the
        canonical production path could not perform the readback.

        ``no_op`` is a separate state and is NOT success: the
        dispatcher did not apply anything to the device. Callers
        that need to distinguish "skipped" from "applied" must
        check ``no_op`` directly.
        """
        if self.no_op:
            return False
        if self.readback_skipped:
            return bool(self.dispatched and self.api_accepted)
        return bool(self.dispatched and self.api_accepted and self.readback_valid)


async def execute_hems_command(
    api: Any,
    hems: Any,
    decision: Any,
    command_id: str | None = None,
    *,
    evidence_recorded: bool = False,
    logger: Any = None,
) -> DispatcherResult:
    """Module-level dispatcher body. ``InverterCoordinator._execute_hems_command``
    delegates to this function so the same logic is testable without
    a full coordinator instance.

    Three-phase contract (see ``DispatcherResult``):

      1. Pre-flight: ``hems.can_dispatch_outage_decision(decision)`` MUST
         allow the write. Refusal -> ``api`` is NEVER touched, the
         caller sees ``api_accepted=False`` and ``dispatched=False``.
      2. Dispatch: every individual ``api.set_*`` call must return
         a truthy success value. Any failure -> ``api_accepted=False``.
      3. Readback: when ``api_accepted`` is True, GET the values back
         and compare. A mismatch -> ``readback_valid=False`` with a
         concrete reason in ``result.error``.

    Buzzer contract (DEFECT B): ``decision.buzzer_off`` is checked
    explicitly. ``None`` skips BOTH branches; only ``True`` or
    ``False`` triggers a write.
    """
    cid = command_id or str(uuid.uuid4())
    _log = logger or _LOGGER

    # The engine's report_control_failure / report_control_success
    # # paths use naive ``datetime.now()`` to compute ``_blocked_until``
    # and other guards. The rest of the engine (e.g.
    # ``_evaluation_hold``) compares those fields with a naive ``now``
    # produced by ``coordinator._async_update_data``. If we ever pass
    # an aware ``now`` here, the next HEMS cycle will crash with
    # "can't compare offset-naive and offset-aware datetimes"
    # (Codex reproduced this regression). Pass a NAIVE ``now`` to
    # the engine's bookkeeping so both sides are naive.
    _now_naive = _dt.now()

    # 0a. All-channel no-op detection. Codex reproduced a regression
    # where a ``HemsDecision(skip=True)`` with ``buzzer_off=False``
    # and ``_last_buzzer='1'`` (already ON) had no actual writes to
    # perform but the body still called ``fetch_device_configs()``
    # for readback. The api returned empty (rate-limited), the
    # dispatcher recorded ``readback_valid=False``, and
    # ``report_control_failure`` incremented
    # ``_consecutive_failures``. Five such routine cycles tripped
    # the circuit breaker.
    #
    # The fix: check each channel (output, charger, buzzer)
    # against the engine's last-known state. If none of the
    # channels would actually issue an api.set_* call, the
    # cycle is a no-op. Skip the transport, skip the readback,
    # do NOT record a failure. Mark the result with
    # ``no_op=True`` so the audit log distinguishes "we tried
    # to write" from "we did not need to write".
    _out = getattr(decision, "output_priority", None)
    _chg = getattr(decision, "charger_priority", None)
    _buz = getattr(decision, "buzzer_off", None)
    # Channel write detection: a channel is a real write only
    # when the engine's proposal DIFFERS from the LAST APPLIED
    # state -- NOT from ``_last_cmd_output`` /
    # ``_last_cmd_charger``. The latter is updated by
    # ``_finalize_decision`` BEFORE the dispatcher runs (anti-
    # flapping bookkeeping), so a fresh proposal would already
    # look "applied" to the no-op gate and the dispatcher would
    # never issue the real write. Codex reproduced this on
    # ``current_output=0/1, decision=2/2,
    # _last_cmd_output='2', _last_cmd_charger='2'`` -- the
    # no-op gate returned ``api.calls=[]`` and ``no_op=True``
    # even though the inverter was still in 0/1.
    #
    # The no-op gate MUST compare against the LAST CONFIRMED
    # state of the inverter (``_last_applied_output`` /
    # ``_last_applied_charger``), updated only on the
    # dispatcher's success path with a successful readback. The
    # buzzer channel is special: ``_last_buzzer`` IS already
    # only updated on the dispatcher's success path (see the
    # body below), so the comparison is correct.
    _applied_out = getattr(hems, "_last_applied_output", None)
    _applied_chg = getattr(hems, "_last_applied_charger", None)
    _has_output_write = (
        _out is not None
        and str(_out) != str(_applied_out)
    )
    _has_charger_write = (
        _chg is not None
        and str(_chg) != str(_applied_chg)
    )
    _has_buzzer_write = False
    if _buz is True:
        if getattr(hems, "_last_buzzer", None) != "0":
            _has_buzzer_write = True
    elif _buz is False:
        if getattr(hems, "_last_buzzer", None) != "1":
            _has_buzzer_write = True
    # ``_buz is None`` -> do not touch the buzzer; no write.
    if not (_has_output_write or _has_charger_write or _has_buzzer_write):
        # All-channel no-op: the engine's proposal matches the
        # LAST CONFIRMED applied state across every channel the
        # dispatcher controls. The cycle is a "we are already
        # in target state" no-op.
        #
        # Rules for the no-op path (Yura, 2026-10-14 followup):
        #   * The transport is NEVER touched.
        #   * ``fetch_device_configs`` is NOT called (no
        #     readback; we did not write anything to confirm).
        #   * ``_consecutive_failures`` is NOT incremented.
        #   * ``_blocked_until`` is NOT armed.
        #   * ``confirm_predictive_delivery`` is NOT called --
        #     the AI did not physically apply anything.
        #   * ``readback_valid`` is False (no readback was
        #     performed; we cannot truthfully claim a confirmed
        #     readback).
        #   * The ``no_op`` flag tells the audit log the cycle
        #     was intentionally skipped.
        _log.debug(
            "HEMS: dispatcher no-op (command_id=%s reason=%s); "
            "all channels match current state, no transport call",
            cid, getattr(decision, "reason", ""),
        )
        return DispatcherResult(
            dispatched=False,
            api_accepted=False,
            readback=None,
            readback_valid=False,
            readback_skipped=True,
            no_op=True,
            error=None,
        )

    # 0b. Pre-flight. The HEMS guard is the single source of truth
    # for HARD barriers (circuit breaker, manual pause). A refusal
    # here is a protective hold, not a transport error -- the
    # transport was never contacted and the failure counter is
    # not incremented. We do call ``report_control_failure`` with
    # ``no_op=True`` to clear the pending-commands ledger so the
    # next cycle starts from a clean state.
    try:
        allowed, guard_reason = hems.can_dispatch_outage_decision(decision)
    except AttributeError:
        allowed, guard_reason = True, ""
    if not allowed:
        err = f"hems refused: {guard_reason}"
        _log.info("HEMS: dispatcher refused (command_id=%s, reason=%s)", cid, guard_reason)
        try:
            hems.report_control_failure(
                _now_naive,
                output_failed=bool(_out is not None),
                charger_failed=bool(_chg is not None),
                no_op=True,
            )
        except Exception:
            pass
        return DispatcherResult(
            dispatched=False,
            api_accepted=False,
            readback=None,
            readback_valid=False,
            error=err,
        )

    # 1. Phase-by-phase dispatch. Each ``set_*`` write returns a
    #    truthy success value; we record the boolean per channel.
    if api is None:
        return DispatcherResult(
            dispatched=False,
            api_accepted=False,
            readback=None,
            readback_valid=False,
            error="inverter api unavailable; refusing to dispatch without a transport",
        )

    # NOTE: command_scope is established by the coordinator's
    # ``_execute_hems_command`` wrapper (see coordinator.py:1666).
    # The wrapper calls this function inside
    # ``with evidence_journal.command_scope(cid):``, so we do NOT
    # double-wrap here. Tests that bypass the coordinator must
    # establish their own command_scope.
    output_failed = decision.output_priority is not None
    charger_failed = decision.charger_priority is not None
    api_accepted = True
    legacy_success = True
    api_exception: Exception | None = None
    # Track whether at least one ``api.set_*`` call was issued in
    # this cycle. Readback only runs when a write was actually
    # issued; an empty cycle (``_has_output_write=False and
    # ``_has_charger_write=False and ``_has_buzzer_write=False``)
    # already short-circuited at the no-op gate above, so this
    # flag guards against the ``buzzer_off=False`` /
    # ``_last_buzzer='1'`` case where the proposal asks for a
    # state already in effect.
    _wrote_anything = False
    try:
        # Output priority
        if decision.output_priority is not None:
            ok = await api.set_output_priority(decision.output_priority)
            output_failed = not ok
            if not ok:
                api_accepted = False
                legacy_success = False
                _log.warning(
                    "HEMS: failed to set output -> %s (%s)",
                    decision.output_priority, getattr(decision, "reason", ""),
                )
            else:
                _wrote_anything = True

        # Charger priority
        if decision.charger_priority is not None:
            ok = await api.set_charger_priority(decision.charger_priority)
            charger_failed = not ok
            if not ok:
                api_accepted = False
                legacy_success = False
                _log.warning(
                    "HEMS: failed to set charger -> %s (%s)",
                    decision.charger_priority, getattr(decision, "reason", ""),
                )
            else:
                _wrote_anything = True

        # Buzzer. Explicit None handling (DEFECT B).
        # ``_last_buzzer`` is updated ONLY after the readback
        # confirms the new value persisted on the device
        # (see step 3.5 below). The previous implementation
        # set ``_last_buzzer`` as soon as the API ACK
        # returned, before the readback ran. A readback
        # failure would still leave ``_last_buzzer`` in
        # the new state, so the no-op gate on the next
        # cycle would treat the device as confirmed in
        # the new mode even though the device may have
        # rejected the write. Codex reproduction: writing
        # ``buzzerAlarmSetting='0'`` returned ACK but the
        # readback reported ``'1'`` (mismatch). The
        # previous code updated ``_last_buzzer='0'``
        # anyway.
        _buzzer_off = getattr(decision, "buzzer_off", None)
        if _buzzer_off is None:
            pass  # Leave the acoustic state untouched.
        elif _buzzer_off is True:
            last = getattr(hems, "_last_buzzer", None)
            if last != "0":
                ok = bool(await api.set_config_item("buzzerAlarmSetting", "0"))
                if not ok:
                    api_accepted = False
                    legacy_success = False
                else:
                    _wrote_anything = True
        elif _buzzer_off is False:
            last = getattr(hems, "_last_buzzer", None)
            if last != "1":
                ok = bool(await api.set_config_item("buzzerAlarmSetting", "1"))
                if not ok:
                    api_accepted = False
                    legacy_success = False
                else:
                    _wrote_anything = True

    except Exception as exc:
        _log.error("HEMS: control command failed: %s", exc)
        legacy_success = False
        api_accepted = False
        api_exception = exc

    # 2. Readback. The dispatcher confirms the write persisted on
    #    the device by reading the device's configuration back. The
    #    canonical production path is
    #    ``InverterApiClient.fetch_device_configs()`` (api.py:1434)
    #    which returns a dict keyed by config name. A readback
    #    mismatch is a legitimate failure mode (the API said success
    #    but the device did not persist); we surface it as
    #    ``readback_valid=False`` with a concrete reason.
    #
    #    When the api does NOT expose ``fetch_device_configs`` (older
    #    builds, fakes, or test doubles), the readback is skipped and
    #    ``readback_valid=True`` — we accept the api's own success
    #    signal rather than refusing a write the device likely
    #    accepted. This matches the pre-readback contract that the
    #    engine's ``report_control_success`` was already relying on.
    #
    #    Skip the readback entirely when no write was issued. This
    #    avoids the rate-limit / empty-response race with the
    #    coordinator's diagnostic ``fetch_device_configs`` call
    #    (coordinator.py:1130) and the false-failure mode Codex
    #    reproduced for ``buzzer_off=False`` with
    #    ``_last_buzzer='1'``.
    readback: dict = {}
    readback_valid = True
    readback_skipped = False
    if not _wrote_anything:
        readback_skipped = True
        _log.debug(
            "HEMS: readback skipped (no api.set_* issued this cycle; "
            "command_id=%s reason=%s)",
            cid, getattr(decision, "reason", ""),
        )
    elif api_accepted and api_exception is None:
        if not hasattr(api, "fetch_device_configs"):
            readback_skipped = True
            _log.debug(
                "HEMS: readback skipped (no fetch_device_configs on api); "
                "accepting api-acknowledged success (command_id=%s)",
                cid,
            )
        else:
            try:
                configs = await api.fetch_device_configs()
                if not isinstance(configs, dict) or not configs:
                    raise RuntimeError(
                        "fetch_device_configs returned empty; cannot confirm write"
                    )
                if decision.output_priority is not None:
                    state = configs.get("outputSourcePrioritySetting")
                    if not isinstance(state, dict):
                        raise RuntimeError(
                            f"outputSourcePrioritySetting missing from readback; "
                            f"got keys: {sorted(configs.keys())[:5]!r}"
                        )
                    returned_output = state.get("value")
                    readback["output_priority"] = returned_output
                    if str(returned_output) != str(decision.output_priority):
                        raise RuntimeError(
                            f"output readback mismatch: requested "
                            f"{decision.output_priority!r} but device "
                            f"reports {returned_output!r}"
                        )
                if decision.charger_priority is not None:
                    state = configs.get("chargerSourcePrioritySetting")
                    if not isinstance(state, dict):
                        raise RuntimeError(
                            f"chargerSourcePrioritySetting missing from readback; "
                            f"got keys: {sorted(configs.keys())[:5]!r}"
                        )
                    returned_charger = state.get("value")
                    readback["charger_priority"] = returned_charger
                    if str(returned_charger) != str(decision.charger_priority):
                        raise RuntimeError(
                            f"charger readback mismatch: requested "
                            f"{decision.charger_priority!r} but device "
                            f"reports {returned_charger!r}"
                        )
                # Buzzer readback. The previous implementation
                # never inspected ``buzzerAlarmSetting`` on
                # the readback, so a write that the device
                # silently rejected still looked successful
                # on the audit log. Codex reproduction:
                # ``set_config_item('buzzerAlarmSetting', '0')``
                # returned ACK, readback reported ``'1'``,
                # ``_last_buzzer`` had already been set to
                # ``'0'`` and ``readback_valid`` stayed True.
                _buzzer_off_rb = getattr(decision, "buzzer_off", None)
                if _buzzer_off_rb is True:
                    _expected_buzzer = "0"
                elif _buzzer_off_rb is False:
                    _expected_buzzer = "1"
                else:
                    _expected_buzzer = None
                if _expected_buzzer is not None:
                    state = configs.get("buzzerAlarmSetting")
                    if not isinstance(state, dict):
                        raise RuntimeError(
                            f"buzzerAlarmSetting missing from readback; "
                            f"got keys: {sorted(configs.keys())[:5]!r}"
                        )
                    returned_buzzer = state.get("value")
                    readback["buzzer"] = returned_buzzer
                    if str(returned_buzzer) != str(_expected_buzzer):
                        raise RuntimeError(
                            f"buzzer readback mismatch: requested "
                            f"{_expected_buzzer!r} but device reports "
                            f"{returned_buzzer!r}"
                        )
            except Exception as exc:
                _log.warning(
                    "HEMS: readback failed (command_id=%s, reason=%s)",
                    cid, exc,
                )
                readback_valid = False
                api_exception = exc

    # 3. Build the result.
    if api_exception is not None and not readback:
        error = f"api raised {type(api_exception).__name__}: {api_exception}"
    elif not api_accepted:
        error = "api rejected at least one write; see channels above"
    elif not readback_valid:
        error = (
            f"readback mismatch: requested output="
            f"{decision.output_priority!r} charger="
            f"{decision.charger_priority!r} but device reported "
            f"{readback!r}"
        )
    elif api_exception is not None:
        error = f"api raised {type(api_exception).__name__}: {api_exception}"
    else:
        error = None
    result = DispatcherResult(
        dispatched=api_accepted and readback_valid,
        api_accepted=api_accepted,
        readback=readback or None,
        readback_valid=readback_valid,
        readback_skipped=readback_skipped,
        error=error,
    )

    # 3.5. Record LAST APPLIED state. The no-op gate (step 0a)
    #    compares against ``_last_applied_output`` /
    #    ``_last_applied_charger``, NOT against
    #    ``_last_cmd_output`` / ``_last_cmd_charger``. The
    #    former represents a CONFIRMED apply (readback
    #    confirmed the device is in the new state), the latter
    #    represents a PROPOSAL that ``_finalize_decision`` may
    #    have already recorded before this dispatcher even ran.
    #    Updating ``_last_applied_*`` only on a confirmed apply
    #    is what allows a fresh proposal ``output=2/2`` to
    #    actually issue a write when the inverter is still in
    #    ``0/1`` (Codex reproduced ``api.calls=[]``,
    #    ``no_op=True`` on this exact path).
    if (
        _wrote_anything
        and api_accepted
        and (readback_valid or readback_skipped)
    ):
        if decision.output_priority is not None:
            try:
                hems._last_applied_output = decision.output_priority
            except Exception:
                pass
        if decision.charger_priority is not None:
            try:
                hems._last_applied_charger = decision.charger_priority
            except Exception:
                pass
        # Buzzer: only commit ``_last_buzzer`` after the
        # readback confirmed the new value. A readback
        # failure / mismatch leaves the cache intact so the
        # next cycle will retry.
        _buzzer_off_ok = getattr(decision, "buzzer_off", None)
        if _buzzer_off_ok is True:
            try:
                hems._last_buzzer = "0"
            except Exception:
                pass
        elif _buzzer_off_ok is False:
            try:
                hems._last_buzzer = "1"
            except Exception:
                pass

    # 4. Legacy bookkeeping for the engine's anti-flapping / circuit
    #    breaker state. ``legacy_success`` is the "physical write
    #    was applied and confirmed" boolean. A readback failure OR
    #    a missing/skipped readback is NOT a confirmed apply. The
    #    ``confirm_predictive_delivery`` signal is only True on
    #    confirmed apply (readback passed), False on readback
    #    mismatch (Codex reproduced a regression where readback
    #    failure still triggered confirm_predictive_delivery(True)).
    #
    #    Note: ``readback_skipped`` (canonical production path could
    #    not perform the readback because the api lacks the
    #    ``fetch_device_configs`` method) is treated as confirmed.
    #    The api's own success signal is the only evidence we have
    #    for those builds, and the engine's bookkeeping already
    #    relied on that signal before readback was added.
    if not readback_skipped and not readback_valid:
        # Readback was attempted and the device rejected the
        # value (or the readback raised). Either way, the
        # write is NOT confirmed. This is a real transport
        # failure that must increment the failure counter.
        legacy_success = False
    if legacy_success:
        try:
            hems.report_control_success()
        except Exception:
            pass
    else:
        try:
            hems.report_control_failure(
                _now_naive,
                output_failed=output_failed,
                charger_failed=charger_failed,
            )
        except Exception:
            pass
    if hasattr(hems, "confirm_predictive_delivery"):
        try:
            hems.confirm_predictive_delivery(legacy_success)
        except Exception:
            pass
    return result


class HemsEngine:
    """Full HEMS decision engine — ported from Dart HemsAlgorithmService.

    Runs every coordinator update cycle (5s). Returns control decisions
    that the coordinator applies via the API.
    """

    def __init__(
        self,
        tunables: HemsTunables | None = None,
        tuning: HemsTuningService | None = None,
    ) -> None:
        self.tun = tunables or HemsTunables()
        self.tuning = tuning or HemsTuningService(self.tun)

        # Anti-flapping state
        self._last_cmd_output: str | None = None
        self._last_cmd_charger: str | None = None
        self._last_cmd_output_at: datetime | None = None
        self._last_cmd_charger_at: datetime | None = None
        # ``_last_applied_output`` / ``_last_applied_charger`` track
        # the LAST CONFIRMED applied state of the inverter. They
        # are updated only on the dispatcher's success path with
        # a successful readback. ``_last_cmd_*`` is updated by
        # ``_finalize_decision`` BEFORE the dispatcher runs (anti-
        # flapping bookkeeping), so it represents a PROPOSAL, not
        # a confirmed apply. The no-op gate MUST use
        # ``_last_applied_*`` so a fresh proposal does not
        # immediately look "already there" (Codex reproduced
        # this regression on a real ``_finalize_decision`` ->
        # real ``dispatcher`` -> ``fake transport`` path).
        self._last_applied_output: str | None = None
        self._last_applied_charger: str | None = None
        self._last_output_switch_at: datetime | None = None
        self._pending_commands: set[str] = set()
        self._pending_output_previous_switch_at: datetime | None = None
        self._manual_override_until: datetime | None = None
        self._last_manual_override_log: datetime | None = None
        self._manual_override_output_mismatch: tuple[datetime, str, str] | None = None
        self._manual_override_charger_mismatch: tuple[datetime, str, str] | None = None

        # Command dedup
        self._command_dedup_window = timedelta(
            seconds=self.tun.command_dedup_window_sec
        )

        # Circuit breaker
        self._consecutive_failures: int = 0
        self._blocked_until: datetime | None = None
        self._last_blocked_log: datetime | None = None

        # Keepalive
        self.keepalive = BatteryKeepaliveState()

        # Dwell
        self._current_dwell_min: int = self.tun.min_mode_hold_min

        # Storm auto-activation
        self._auto_storm_active: bool = False
        self._previous_smart_mode: int | None = None

        # Emergency stale data
        self._last_realtime_at: datetime | None = None

        # R05: configured day/night tariffs. The
        # coordinator writes to these from
        # ``apply_capacity_and_tariff_options`` so the
        # planner fallback path has the operator's
        # rates, not module-level constants. Default
        # to the documented UA rates (4.32 day /
        # 2.16 night UAH/kWh) when the coordinator has
        # not yet bound the engine.
        self._day_tariff_uah: float | None = None
        self._night_tariff_uah: float | None = None
        self._tariff_schedule: list[float] | None = None

        # Last applied buzzer state
        self._last_buzzer: str | None = None

        # Predictive ML planner (mode read from ``predictive_tuning.predictive_mode``)
        from .tuning import PredictiveTuning as _PT
        self.predictive_tuning: _PT = _PT()
        # Convenience booleans kept in lockstep with ``predictive_tuning``.
        # Legacy code (test fixtures, switch.is_on) reads
        # ``_predictive_enabled`` — keep it set to the same value the
        # coordinator computed from entry.options at startup. The
        # coordinator's ``async_set_predictive_mode`` updates both the
        # tuning object AND this attribute so the two cannot diverge.
        self._predictive_enabled: bool = (
            self.predictive_tuning.predictive_mode in ("shadow", "assist")
        )
        self._predictive_mode: str = self.predictive_tuning.predictive_mode
        # Hint/plan attributes are set on the engine when the
        # planner runs (shadow/assist + valid inputs); default them
        # to None so callers can read them unconditionally.
        self._last_predictive_hint: Any | None = None
        self._last_predictive_plan: Any | None = None
        self._last_predictive_inputs: Any | None = None

    # ═══════════════════════════════════════════════════════════════════════
    # PUBLIC API — called by coordinator
    # ═══════════════════════════════════════════════════════════════════════

    def build_forced_decision(
        self,
        reason: str,
        *,
        output_priority: str,
        charger_priority: str,
        buzzer_off: bool = False,
    ) -> HemsDecision:
        """Build a forced decision for service-level overrides.

        T12 follow-up: ``force_grid_charge`` and similar
        service-level overrides need to drive the inverter
        on a fixed output/charger pair without going
        through the regular adaptive plan. We expose this
        as a separate builder so the rest of the engine —
        ``evaluate``, ``_evaluate_adaptive``,
        ``_evaluate_arbitrage``, ``_evaluate_storm``,
        ``detect_manual_override``, hysteresis — is
        untouched. The forced decision still goes through
        ``_execute_hems_command`` in the coordinator and is
        therefore subject to the same manual-override and
        dedup guards as any other decision.

        The decision has ``skip=False`` and the supplied
        ``reason`` so the dashboard surfaces the source
        of the override.
        """
        return HemsDecision(
            output_priority=output_priority,
            charger_priority=charger_priority,
            reason=reason,
            skip=False,
            buzzer_off=buzzer_off,
        )

    def evaluate(
        self,
        *,
        smart_mode: int,
        hems_auto: bool,
        soc: float,
        pv_power: float,
        grid_power: float,
        battery_power: float,
        load_power: float,
        grid_voltage: float,
        grid_available: bool,
        current_output: str | None = None,
        current_charger: str | None = None,
        now: datetime | None = None,
        forecast_tomorrow_kwh: float | None = None,
        forecast_day_after_kwh: float | None = None,
        pv_surplus_w: float | None = None,
        reserve_soc: float | None = None,
        min_operating_soc: float | None = None,
        tarif_day: float = 4.32,
        tarif_night: float = 2.16,
        battery_health_percent: float = 100.0,
        is_online: bool = True,
        soc_unknown: bool = False,
        entry_id: str | None = None,
    ) -> HemsDecision:
        """Run one HEMS evaluation cycle.

        Returns a HemsDecision with the recommended output/charger priorities.
        The coordinator is responsible for applying (or skipping) the commands.
        """
        now = now or datetime.now()
        predictive_mode = self._reset_predictive_state()
        current_output = _normalize_output(current_output)
        current_charger = _normalize_charger(current_charger)
        # 0. Reconcile the LAST APPLIED cache with the fresh
        #    observed state. ``_last_applied_output`` /
        #    ``_last_applied_charger`` record the LAST
        #    CONFIRMED applied state of the inverter (set in
        #    the dispatcher's success path). The device may
        #    have changed externally between cycles (manual
        #    override, grid event, hardware reset, …) so the
        #    cache can drift from the truth. Without
        #    reconciliation, the dispatcher's no-op gate
        #    would see a stale cache and short-circuit a
        #    legitimate new write (Codex reproduction:
        #    ``_last_applied_output='2'`` set last cycle,
        #    fresh ``current_output='0'`` arrives, engine
        #    proposes ``'2'`` to recover, dispatcher
        #    short-circuits with ``api.calls=[]`` and
        #    ``no_op=True``, inverter stays at ``'0'``).
        # The reconciliation:
        #   * Skips when manual override is active (the
        #     user is in control; the cache may legitimately
        #     differ from observed).
        #   * Skips when the anti-flapping dedup window is
        #     open (a recent proposal may not have been
        #     observed yet because the device is still
        #     settling).
        #   * Otherwise, when observed differs from
        #     ``_last_applied_*``, update the cache to
        #     observed. The next ``_finalize_decision`` will
        #     see ``target != applied`` and the dispatcher
        #     will issue a real write.
        self._reconcile_applied_cache(
            now=now,
            current_output=current_output,
            current_charger=current_charger,
        )
        reserve_soc = self.tun.reserve_soc if reserve_soc is None else reserve_soc
        min_operating_soc = self.tun.min_operating_soc if min_operating_soc is None else min_operating_soc
        forecast_tomorrow_kwh = _forecast_energy(forecast_tomorrow_kwh)
        forecast_day_after_kwh = _forecast_energy(forecast_day_after_kwh)
        numeric = {key: _finite_number(value) for key, value in {
            "soc": soc, "pv_power": pv_power, "grid_power": grid_power,
            "battery_power": battery_power, "load_power": load_power,
            "grid_voltage": grid_voltage, "reserve_soc": reserve_soc,
            "min_operating_soc": min_operating_soc,
            # T08 follow-up: the engine reads its
            # efficiency bounds from the inputs dict
            # (validated upstream by
            # ``build_planner_inputs``) so the planner
            # receives the same values the engine
            # uses for its own anti-flapping
            # calculations. The defaults match the
            # documented inverter specification.
            "charge_efficiency": _finite_number(
                getattr(self.tun, "charge_efficiency", 0.85)
            ),
            "discharge_efficiency": _finite_number(
                getattr(self.tun, "discharge_efficiency", 0.90)
            ),
        }.items()}
        inputs = {**numeric, "smart_mode": smart_mode, "grid_available": grid_available,
                  "current_output": current_output, "current_charger": current_charger,
                  "forecast_today_kwh": _forecast_energy(getattr(self, "_last_forecast_today_kwh", None)),
                  "forecast_tomorrow_kwh": forecast_tomorrow_kwh,
                  # T01 follow-up: propagate the unknown-SOC flag
                  # through to the planner path. The baseline
                  # engine still uses ``numeric["soc"]`` (set to 100
                  # when the meter is unreadable) for its own
                  # control math, but the planner reads
                  # ``soc_unknown`` from this dict and treats
                  # ``soc`` as advisory display only.
                  "soc_unknown": bool(soc_unknown)}
        valid_telemetry = (all(value is not None for value in numeric.values())
                           and 0 <= numeric["reserve_soc"] <= 100
                           and 0 <= numeric["min_operating_soc"] <= 100)
        # Valid samples still refresh history during manual/automatic holds.
        # Offline or malformed samples must not advance the freshness clock.
        if is_online and valid_telemetry:
            soc = max(0.0, min(100.0, numeric["soc"]))
            pv_power, grid_power = numeric["pv_power"], numeric["grid_power"]
            battery_power, load_power = numeric["battery_power"], numeric["load_power"]
            grid_voltage, reserve_soc = numeric["grid_voltage"], numeric["reserve_soc"]
            min_operating_soc = numeric["min_operating_soc"]
            inputs["soc"] = soc
            self._last_realtime_at = now
            self._track_battery_activity(battery_power, now)
            self.tuning.update_surplus(pv_power - load_power)
            self._current_dwell_min = self.tuning.compute_adaptive_dwell()

        buzzer_off = self.tuning.should_reduce_buzzer(now=now)
        hold = self._evaluation_hold(hems_auto=hems_auto, smart_mode=smart_mode,
                                     is_online=is_online, valid_telemetry=valid_telemetry,
                                     now=now, buzzer_off=buzzer_off)
        if hold is not None:
            return self._log_decision(hold, now, inputs, entry_id)

        predictive_hint, predictive_plan = self._evaluate_predictive(now, inputs)

        # ── Dispatch by smart mode ────────────────────────────────────
        if smart_mode in (SmartMode.ADAPTIVE, SmartMode.ARBITRAGE) and soc <= reserve_soc + 2:
            decision = HemsDecision(output_priority=OutputPriority.USB,
                                    charger_priority=ChargerPriority.SNU,
                                    reason=_Reason.RESERVE_PROTECTION, buzzer_off=buzzer_off)
        elif smart_mode == SmartMode.ADAPTIVE:
            decision = self._evaluate_adaptive(
                soc=soc,
                pv_power=pv_power,
                grid_power=grid_power,
                battery_power=battery_power,
                load_power=load_power,
                grid_available=grid_available,
                current_output=current_output,
                current_charger=current_charger,
                now=now,
                forecast_tomorrow_kwh=forecast_tomorrow_kwh,
                forecast_day_after_kwh=forecast_day_after_kwh,
                reserve_soc=reserve_soc,
                min_operating_soc=min_operating_soc,
                tarif_day=tarif_day,
                tarif_night=tarif_night,
                buzzer_off=buzzer_off,
                predictive_hint=predictive_hint,
                predictive_plan=predictive_plan,
                predictive_mode=predictive_mode,
            )
        elif smart_mode == SmartMode.ARBITRAGE:
            decision = self._evaluate_arbitrage(
                soc=soc,
                pv_power=pv_power,
                load_power=load_power,
                current_output=current_output,
                current_charger=current_charger,
                now=now,
                reserve_soc=reserve_soc,
                buzzer_off=buzzer_off,
                predictive_hint=predictive_hint,
                predictive_plan=predictive_plan,
                predictive_mode=predictive_mode,
            )
        elif smart_mode == SmartMode.STORM:
            decision = self._evaluate_storm(
                soc=soc,
                current_output=current_output,
                current_charger=current_charger,
                buzzer_off=buzzer_off,
                predictive_hint=predictive_hint,
                predictive_mode=predictive_mode,
            )
        else:
            return HemsDecision(reason="unknown_mode", skip=True, buzzer_off=buzzer_off)

        return self._finalize_decision(decision, now, inputs, entry_id)

    def _reset_predictive_state(self) -> str:
        """No recommendation survives an Off/hold/error evaluation cycle."""
        mode = getattr(self.predictive_tuning, "predictive_mode", "off")
        mode = mode if mode in ("off", "shadow", "assist") else "off"
        self._predictive_mode = self._last_predictive_mode = mode
        self._predictive_enabled = mode != "off"
        self._last_predictive_hint = None
        self._last_predictive_plan = None
        self._last_predictive_inputs = None
        controller = getattr(self, "_predictive_controller", None)
        if controller is not None:
            controller.last_plan = None
        return mode

    def _evaluation_hold(
        self,
        *,
        hems_auto: bool,
        smart_mode: int,
        is_online: bool,
        valid_telemetry: bool,
        now: datetime,
        buzzer_off: bool,
        soc: float | None = None,
        reserve_soc: float | None = None,
        soc_safety_margin: float = 5.0,
    ) -> HemsDecision | None:
        """User control, transport, and safety holds precede
        automatic decisions.

        T12 follow-up: ``soc`` and ``reserve_soc`` are
        optional. When both are supplied, this function
        returns ``reserve_floor`` if the SOC has dropped
        to ``reserve_soc + soc_safety_margin`` or below.
        The forced-grid-charge path uses this to avoid
        draining a battery that is already near the
        configured reserve: the timer keeps running but
        no command is dispatched. This mirrors the
        reserve-SOC guard the engine already enforces in
        ``_evaluate_adaptive`` for the regular plan.

        The other holds — hems_auto_off, circuit breaker,
        manual override, unknown mode, inverter offline,
        invalid telemetry — are unchanged. The audit's
        T12 review specifically asked for the *whole*
        stack of guards to apply to the timed hold, not
        just the four the original commit covered.
        """
        reason = None
        if not hems_auto:
            reason = "hems_auto_off"
        elif self._blocked_until and _tz_compare(now, self._blocked_until) < 0:
            reason = "circuit_breaker"
        elif self._manual_override_until and _tz_compare(now, self._manual_override_until) < 0:
            if self._last_manual_override_log is None or _tz_diff_seconds(now, self._last_manual_override_log) > timedelta(minutes=5).total_seconds():
                remaining = _tz_diff_seconds(self._manual_override_until, now)
                _LOGGER.info("HEMS: observed command-readback mismatch hold (%.0fs remaining)", remaining)
                self._last_manual_override_log = now
            reason = _Reason.MANUAL_OVERRIDE
        elif smart_mode not in (SmartMode.ADAPTIVE, SmartMode.ARBITRAGE, SmartMode.STORM):
            reason = "unknown_mode"
        elif not is_online:
            stale = self._last_realtime_at and now - self._last_realtime_at > timedelta(minutes=30)
            reason = _Reason.EMERGENCY_STALE if stale else "inverter_offline"
        elif not valid_telemetry:
            reason = "invalid_telemetry"
        elif (
            soc is not None
            and reserve_soc is not None
            and soc <= reserve_soc + soc_safety_margin
        ):
            # Reserve-floor guard: refuse to drive the
            # inverter into a state that would drain the
            # battery below the configured reserve. The
            # forced-grid-charge path uses this to wait
            # until the SOC recovers (e.g. via PV) before
            # it starts the timed hold. The timer keeps
            # running.
            reason = "reserve_floor"
        if reason is not None:
            return HemsDecision(reason=reason, skip=True, buzzer_off=buzzer_off)
        return None

    def can_dispatch_outage_decision(
        self, decision: Any = None
    ) -> tuple[bool, str]:
        """Pre-flight guard for ``InverterCoordinator._execute_hems_command``.

        Phase 2C/3 follow-up (Codex DEFECT C): the protected
        dispatcher is the last line of defence, but the runtime
        previously duplicated the same checks with a slightly
        different surface. To guarantee pre-flight and dispatcher
        agree on what is safe, both call this single method.

        The guard only refuses on HARD barriers that would make
        any inverter write unsafe:

          1. Circuit breaker
             (``self._blocked_until`` > ``now``) — set by
             ``report_control_failure`` after a failed write.

          2. Manual override
             (``self._manual_override_until`` > ``now``) — set by
             ``arm_manual_override`` / ``detect_manual_override``.

        Returns ``(True, "")`` when the dispatcher may proceed,
        otherwise ``(False, "<concrete reason>")``.

        The guard does NOT refuse on:
          - "decision has no output/charger payload" -- the
            engine routinely emits ``HemsDecision(skip=True)``
            on cycles where the device is already in the target
            state. The dispatcher routes those to a no-op
            (no API call, no ``report_control_failure``). Treating
            a no-op as a transport failure would trip the circuit
            breaker after five routine cycles (Codex reproduced
            this regression -- 133 failures, all from no-op cycles).
          - "anti-flapping window" -- the engine's
            ``_apply_anti_flapping`` already nulled the
            ``decision.output_priority`` field for a duplicate
            proposal. Re-checking the timestamp here would block
            the just-formed decision against its own proposal
            time and trip the circuit breaker (Codex reproduced
            this regression).
        """
        now = datetime.now(_UTC)
        # 1. Circuit breaker
        blocked_until = getattr(self, "_blocked_until", None)
        if not isinstance(blocked_until, datetime):
            pass
        else:
            blocked_utc = blocked_until if blocked_until.tzinfo else blocked_until.replace(tzinfo=_UTC)
            now_utc = now if now.tzinfo else now.replace(tzinfo=_UTC)
            if blocked_utc > now_utc:
                return (
                    False,
                    f"circuit breaker until {blocked_utc.isoformat()}",
                )
        # 2. Manual override
        manual_until = getattr(self, "_manual_override_until", None)
        if not isinstance(manual_until, datetime):
            pass
        else:
            manual_utc = manual_until if manual_until.tzinfo else manual_until.replace(tzinfo=_UTC)
            now_utc = now if now.tzinfo else now.replace(tzinfo=_UTC)
            if manual_utc > now_utc:
                return (
                    False,
                    f"manual pause until {manual_utc.isoformat()}",
                )
        # No hard barriers -- the dispatcher may attempt the write.
        return (True, "")

    def _evaluate_predictive(self, now: datetime, inputs: dict[str, Any]) -> tuple[Any, Any]:
        """Build current hints/plan; failures leave the baseline engine usable."""
        if not self._predictive_enabled:
            return None, None
        try:
            from .predictive import PredictiveHemsController
            from .telemetry import build_planner_inputs

            hourly_pv = list(getattr(self, "_hourly_pv_forecast", []) or [])
            radiation = list(getattr(self, "_hourly_radiation", []) or [])
            weather = list(getattr(self, "_hourly_weather_codes", []) or [])
            capacity = _finite_number(getattr(self, "_battery_capacity_kwh", 4.8))
            forecast_now = getattr(self, "_planner_forecast_now", now)
            dated_pv = getattr(self, "_dated_hourly_pv_forecast", None)
            if dated_pv is not None:
                if not isinstance(forecast_now, datetime) or forecast_now.tzinfo is None:
                    return None, None
                first = int(forecast_now.replace(minute=0, second=0, microsecond=0).timestamp())
                values = [_finite_number(dated_pv.get(first + h*3600)) for h in range(24)]
                if any(value is None or not 0 <= value <= 20000 for value in values):
                    return None, None
            if (inputs["forecast_tomorrow_kwh"] is None or inputs["forecast_today_kwh"] is None
                    or any(len(series) != 24 for series in (hourly_pv, radiation, weather))
                    or capacity is None or capacity <= 0):
                return None, None
            if not hasattr(self, "_predictive_controller"):
                self._predictive_controller = PredictiveHemsController()
            controller = self._predictive_controller
            # T08 follow-up: propagate the user-
            # configured ``reserve_soc`` from the
            # engine inputs into ``build_planner_inputs``.
            # The previous code path left the planner
            # on the dataclass default (20 %), so a
            # 35 % reserve was silently dropped before
            # any planner call. ``inputs["reserve_soc"]``
            # is set by the coordinator from
            # ``entry.options["reserve_soc"]`` and is
            # already validated by ``evaluate()``.
            planner_reserve = _finite_number(
                inputs.get("reserve_soc")
            )
            if planner_reserve is None or not 0 <= planner_reserve <= 100:
                planner_reserve = 20.0
            # R05: validate the configured day/night rates
            # before passing them as planner fallbacks. The
            # ``_coerce_tariff_fallback`` helper preserves
            # valid zero and rejects NaN/Inf/bool/negative.
            _d = _coerce_tariff_fallback(
                getattr(self, "_day_tariff_uah", None),
                default=4.32,
            )
            _n = _coerce_tariff_fallback(
                getattr(self, "_night_tariff_uah", None),
                default=2.16,
            )
            pi = build_planner_inputs(
                raw={"gridVoltage": inputs["grid_voltage"],
                     # T01 follow-up: when ``soc_unknown`` is set, do
                     # NOT pass a synthetic 100 into the planner
                     # payload. The audit's complaint is exactly
                     # this: the planner used to see 100 % and act
                     # on it. We pass ``None`` for the raw field
                     # and let the explicit ``soc_unknown=True``
                     # override below carry the truth.
                     "batterySoc": (None if inputs.get("soc_unknown") else inputs["soc"]),
                     "pvPower": inputs["pv_power"], "loadPower": inputs["load_power"],
                     "gridPower": inputs["grid_power"], "batteryPower": inputs["battery_power"]},
                now=forecast_now, smart_mode=inputs["smart_mode"],
                forecast_tomorrow_kwh=inputs["forecast_tomorrow_kwh"],
                forecast_today_kwh=inputs["forecast_today_kwh"],
                hourly_pv=hourly_pv, hourly_radiation=radiation, hourly_weather_codes=weather,
                dated_hourly_pv=dated_pv,
                tariff_schedule=list(getattr(self, "_tariff_schedule", []) or []),
                # R05: the coordinator's configured
                # day/night rates become the planner's
                # fallbacks. The validator in
                # ``build_planner_inputs`` may have refused
                # the schedule (returned an empty list),
                # in which case the planner rebuilds the
                # 24-hour day/night schedule from these
                # fallbacks. They are the operator's
                # settings, NOT module-level constants.
                tariff_day_fallback=_d,
                tariff_night_fallback=_n,
                # Audit T19: the dated
                # ``list[tuple[date,
                # list[float], bool]]``
                # shape is the canonical
                # history contract. The
                # fallback to the legacy
                # flat shape is kept for
                # tests and migration
                # safety.
                consumption_history=list(getattr(self, "_consumption_history_with_dates", None) or getattr(self, "_consumption_history", []) or []),
                battery_capacity_kwh=capacity, grid_available=inputs["grid_available"],
                # T01 follow-up: propagate the unknown-SOC flag all the
                # way into PlannerInputs so the predictive controller
                # sees a coherent ``soc=None / soc_unknown=True``
                # state instead of the synthetic 100 % fallback that
                # would otherwise leak into the planner's display
                # fields and any planning math that reads them.
                # The baseline engine still uses ``inputs["soc"]``
                # (=100) for its own non-predictive code paths; the
                # the gate in the coordinator already prevents any
                # downstream inverter write. This flag is the
                # diagnostic truth for the planner.
                soc_unknown=bool(inputs.get("soc_unknown", False)),
                # T08 follow-up: the user-configured
                # reserve reaches the planner here,
                # validated by ``build_planner_inputs``
                # at the boundary.
                reserve_soc=planner_reserve,
            )
            hint = controller.suggest(pi)
            try:
                _, plan = controller.decide(pi)
            except Exception as exc:
                _LOGGER.debug("Predictive plan build skipped: %s", exc)
                plan = None
                controller.last_plan = None
            self._last_predictive_hint = hint
            self._last_predictive_plan = plan
            self._last_predictive_inputs = pi
            return hint, plan
        except Exception as exc:
            _LOGGER.debug("Predictive hint unavailable: %s", exc)
            return None, None

    def _finalize_decision(self, decision: HemsDecision, now: datetime,
                           inputs: dict[str, Any],
                           entry_id: str | None = None) -> HemsDecision:
        """Filter redundant writes, apply dwell/dedup and record the proposal."""
        if not decision.skip:
            for command, current in (("output_priority", inputs["current_output"]),
                                     ("charger_priority", inputs["current_charger"])):
                target = getattr(decision, command)
                if target is not None and target == current:
                    setattr(decision, command, None)
                    if decision.reason == "day_default":
                        decision.reason = "already_in_target"
            previous_switch_at = self._last_output_switch_at
            decision = self._apply_anti_flapping(decision, now)
            if decision.output_priority is not None:
                self._pending_commands.add("output")
                self._pending_output_previous_switch_at = previous_switch_at
                self._last_cmd_output = decision.output_priority
                self._last_cmd_output_at = now
            if decision.charger_priority is not None:
                self._pending_commands.add("charger")
                self._last_cmd_charger = decision.charger_priority
                self._last_cmd_charger_at = now
        return self._log_decision(decision, now, inputs, entry_id)

    def _reconcile_applied_cache(
        self,
        *,
        now: datetime,
        current_output: str | None,
        current_charger: str | None,
    ) -> None:
        """Reconcile ``_last_applied_output`` /
        ``_last_applied_charger`` with the fresh observed
        state of the inverter.

        Background: ``_last_applied_*`` is the LAST CONFIRMED
        applied state of the inverter, set only on the
        dispatcher's success path with a successful readback.
        The device may change externally between cycles
        (manual override, grid event, hardware reset, …) so
        the cache can drift from the truth. Without
        reconciliation, the dispatcher's no-op gate would see
        a stale cache and short-circuit a legitimate new
        write. Codex reproduction at fc1a2e8:

          cycle 1: dispatcher writes '2/2', readback passes,
                   ``_last_applied_output='2'``,
                   ``_last_applied_charger='2'``.
          cycle 2: device is now in '0/1' (manual change).
                   ``current_output='0'``,
                   ``current_charger='1'``. Engine proposes
                   '2/2' to recover. Dispatcher no-op gate:
                   ``'2' != '2'`` (``_last_applied_*``) =>
                   no write. Inverter stays in '0/1'.

        The reconciliation:
          * Skips when manual override is active (the user
            is in control; the cache may legitimately
            differ from observed).
          * Skips when the anti-flapping dedup window is
            open (a recent proposal may not have been
            observed yet).
          * Skips when observed is ``None`` (no telemetry
            from the inverter).
          * Otherwise, when observed differs from
            ``_last_applied_*``, update the cache to
            observed. The next ``_finalize_decision`` will
            see ``target != applied`` and the dispatcher
            will issue a real write.
        """
        # 1. Manual override: preserve the cache.
        if (
            self._manual_override_until is not None
            and _tz_compare(self._manual_override_until, now) > 0
        ):
            return
        # 2. Anti-flapping dedup window: a recent proposal
        #    may not have been observed yet. Do not
        #    reconcile while the window is open.
        _dedup_active = False
        if (
            self._last_cmd_output_at is not None
            and (now - self._last_cmd_output_at) < self._command_dedup_window
        ):
            _dedup_active = True
        if (
            self._last_cmd_charger_at is not None
            and (now - self._last_cmd_charger_at) < self._command_dedup_window
        ):
            _dedup_active = True
        if _dedup_active:
            return
        # 3. Reconcile output channel. ``None`` observed =
        #    no telemetry; do not invalidate the cache.
        if (
            current_output is not None
            and self._last_applied_output is not None
            and str(current_output) != str(self._last_applied_output)
        ):
            self._last_applied_output = str(current_output)
        elif current_output is not None and self._last_applied_output is None:
            # First observation after a restart. Seed the
            # cache from the observed state so the no-op
            # gate does not fire spuriously.
            self._last_applied_output = str(current_output)
        # 4. Reconcile charger channel (symmetric).
        if (
            current_charger is not None
            and self._last_applied_charger is not None
            and str(current_charger) != str(self._last_applied_charger)
        ):
            self._last_applied_charger = str(current_charger)
        elif current_charger is not None and self._last_applied_charger is None:
            self._last_applied_charger = str(current_charger)


    @staticmethod
    def _log_decision(decision: HemsDecision, now: datetime,
                      inputs: dict[str, Any],
                      entry_id: str | None = None) -> HemsDecision:
        """Include early holds in the existing trace; logging cannot stop HEMS."""
        try:
            applied = None
            if decision.skip:
                skip_reason = decision.reason
            elif decision.output_priority is None and decision.charger_priority is None:
                skip_reason = "already_in_target"
            else:
                skip_reason = None
                # Legacy trace field: proposed writes; the coordinator owns
                # delivery and reports transport success/failure separately.
                applied = {"output_priority": decision.output_priority,
                           "charger_priority": decision.charger_priority,
                           "buzzer_off": decision.buzzer_off}
            debug_logging.log_evaluation(timestamp=now, inputs=inputs, decision=decision,
                                         applied=applied, skip_reason=skip_reason,
                                         entry_id=entry_id)
        except Exception:
            pass
        return decision

    def detect_manual_override(
        self,
        actual_output: str | None,
        actual_charger: str | None,
        now: datetime | None = None,
    ) -> bool:
        """Detect a mismatch between observed device modes and last HEMS commands.

        A mismatch's source is unknown to this method. A new mismatch for a
        command older than 30 seconds arms the configured protective hold.
        """
        # Codex reproduced: ``detect_manual_override`` and
        # ``arm_manual_override`` use bare datetime comparisons
        # against ``self._manual_override_until`` (now UTC-aware
        # from the dispatcher) and a naive ``now`` from the
        # coordinator. The naive < aware comparison raises
        # TypeError on Europe/Kyiv after the first failure.
        # Use the tz-safe helpers so the comparison works in
        # both directions.
        now = now or datetime.now()
        hold_active = bool(
            self._manual_override_until
            and _tz_compare(now, self._manual_override_until) < 0
        )
        output_mismatch_active = False
        charger_mismatch_active = False
        new_output_mismatch = False
        new_charger_mismatch = False

        norm_out = _normalize_output(actual_output)
        norm_last = _normalize_output(self._last_cmd_output)
        if norm_out and norm_last and norm_out != norm_last:
            if self._last_cmd_output_at and (now - self._last_cmd_output_at).total_seconds() > 30:
                output_mismatch_active = True
                mismatch = (self._last_cmd_output_at, norm_last, norm_out)
                if mismatch != self._manual_override_output_mismatch:
                    self._manual_override_output_mismatch = mismatch
                    new_output_mismatch = True
        elif norm_out and norm_last:
            self._manual_override_output_mismatch = None

        norm_chg = _normalize_charger(actual_charger)
        norm_last_c = _normalize_charger(self._last_cmd_charger)
        if norm_chg and norm_last_c and norm_chg != norm_last_c:
            if self._last_cmd_charger_at and (now - self._last_cmd_charger_at).total_seconds() > 30:
                charger_mismatch_active = True
                mismatch = (self._last_cmd_charger_at, norm_last_c, norm_chg)
                if mismatch != self._manual_override_charger_mismatch:
                    self._manual_override_charger_mismatch = mismatch
                    new_charger_mismatch = True
        elif norm_chg and norm_last_c:
            self._manual_override_charger_mismatch = None

        # Process both channels before returning for an existing hold or
        # starting one. Otherwise the first mismatch masks the second and it
        # can arm a sequential hold after the first one expires.
        if hold_active:
            return output_mismatch_active or charger_mismatch_active
        if new_output_mismatch or new_charger_mismatch:
            # Stamp ``_manual_override_until`` as a UTC-aware
            # ``datetime`` so the engine's guards compare against
            # ``datetime.now(_UTC)`` consistently regardless of
            # the coordinator's tz. Codex reproduced a TypeError
            # on Europe/Kyiv where a naive ``now`` was compared to
            # the UTC-aware value set by ``arm_manual_override``.
            if isinstance(now, datetime) and now.tzinfo is None:
                now = now.astimezone()  # naive -> local-aware
                now = now.astimezone(_UTC)  # local-aware -> UTC
            self._manual_override_until = now + timedelta(minutes=self.tun.manual_override_hold_min)
            if new_output_mismatch:
                _LOGGER.info(
                    "HEMS: observed output command-readback mismatch (%s commanded → %s observed), hold for %d min",
                    self._last_cmd_output, actual_output, self.tun.manual_override_hold_min,
                )
            if new_charger_mismatch:
                _LOGGER.info(
                    "HEMS: observed charger command-readback mismatch (%s commanded → %s observed), hold for %d min",
                    self._last_cmd_charger, actual_charger, self.tun.manual_override_hold_min,
                )
            return True

        return False

    def arm_manual_override(self, now: datetime | None = None) -> None:
        """Externally arm manual override (e.g. from UI control panel)."""
        now = now or datetime.now()
        # Stamp ``_manual_override_until`` as UTC-aware so the
        # engine's guards compare consistently regardless of
        # the caller's tz. Codex reproduced a TypeError on
        # Europe/Kyiv where a naive ``now`` was compared to a
        # UTC-aware ``_manual_override_until`` left over from a
        # previous arming.
        if isinstance(now, datetime) and now.tzinfo is None:
            now = now.astimezone()
            now = now.astimezone(_UTC)
        self._manual_override_until = now + timedelta(minutes=self.tun.manual_override_hold_min)

    def report_control_failure(
        self, now: datetime | None = None, *,
        output_failed: bool = True, charger_failed: bool = True,
        no_op: bool = False,
    ) -> None:
        """Back off failed writes without treating them as applied commands.

        Partial success preserves the successful channel. Failed/uncertain
        writes cannot cause a false manual override or block a retry via dedup.

        ``no_op=True`` is the special "we did not try to write" mode:
        the guard refused (circuit breaker, manual pause, etc.) OR
        the decision was a no-op (engine decided to skip). In that
        case the function still clears the pending-commands ledger
        so the next cycle can dispatch cleanly, but it does NOT
        increment ``_consecutive_failures`` and does NOT extend
        ``_blocked_until``. Treating a no-op as a transport failure
        would trip the circuit breaker after five routine cycles
        (Codex reproduced this regression -- 133 failures, all
        from no-op cycles).
        """
        # ``_blocked_until`` must be a UTC-aware ``datetime`` so
        # the engine's guards (``can_dispatch_outage_decision``,
        # ``_evaluation_hold``) compare against
        # ``datetime.now(_UTC)`` consistently regardless of the
        # coordinator's tz. The previous implementation accepted
        # a naive ``now`` and treated its wall-clock digits as
        # UTC (``.replace(tzinfo=_tz.utc)``), which produced a
        # 3-hour skew on EEST (Codex reproduced the bug). The
        # fix: when ``now`` is naive, treat it as local time and
        # convert to the SYSTEM's local tz, then to UTC. When
        # ``now`` is None, use ``_dt.now(_UTC)`` directly.
        if now is None:
            now = _dt.now(_UTC)
        elif isinstance(now, datetime) and now.tzinfo is None:
            # Promote naive local to system local tz, then to UTC.
            # The previous ``now.replace(tzinfo=_tz.utc)`` was
            # wrong: it labelled the LOCAL value as if it were
            # already UTC, producing a wall-clock-vs-UTC skew.
            # We delegate to ``datetime.astimezone`` which uses
            # the system tz when called with no argument, so a
            # naive ``now`` is interpreted as the system's
            # local wall clock and converted to UTC correctly.
            now = now.astimezone()  # naive -> local-aware
            now = now.astimezone(_UTC)  # local-aware -> UTC
        if output_failed and "output" in self._pending_commands:
            self._last_cmd_output = self._last_cmd_output_at = None
            self._last_output_switch_at = self._pending_output_previous_switch_at
        if charger_failed and "charger" in self._pending_commands:
            self._last_cmd_charger = self._last_cmd_charger_at = None
        self._pending_commands.clear()
        self._pending_output_previous_switch_at = None

        if no_op:
            # No increment, no backoff. Just clear the ledger so the
            # next cycle starts from a clean state.
            return

        self._consecutive_failures += 1

        # Exponential backoff: 5s, 12s, 25s, 45s
        delays = [5, 12, 25, 45]
        delay_idx = min(self._consecutive_failures - 1, len(delays) - 1)
        delay_sec = delays[delay_idx]

        self._blocked_until = now + timedelta(seconds=delay_sec)
        if self._last_blocked_log is None or now - self._last_blocked_log > timedelta(minutes=2):
            _LOGGER.warning(
                "HEMS: circuit breaker — %d consecutive failures, blocked for %ds",
                self._consecutive_failures, delay_sec,
            )
            self._last_blocked_log = now

    def report_control_success(self) -> None:
        """Report a successful control write — reset circuit breaker."""
        self._consecutive_failures = 0
        self._blocked_until = None
        self._pending_commands.clear()
        self._pending_output_previous_switch_at = None

    def check_keepalive(self, battery_power: float, soc: float, now: datetime | None = None) -> HemsDecision | None:
        """Check if battery keepalive is needed.

        If battery inactive (|power| < 50W) for 2+ hours while on USB mode,
        briefly switch to SBU to wake the BMS.

        Returns a HemsDecision to apply, or None if no keepalive needed.
        """
        now = now or datetime.now()
        self._track_battery_activity(battery_power, now)

        if self.keepalive.in_progress:
            return None

        if soc <= self.keepalive.MIN_SOC:
            return None

        if self.keepalive.last_activity_at is None:
            return None

        inactive_duration = now - self.keepalive.last_activity_at
        if inactive_duration < timedelta(hours=self.keepalive.INTERVAL_HOURS):
            return None

        # Battery has been inactive for 2+ hours → wake it up
        self.keepalive.in_progress = True
        _LOGGER.info(
            "HEMS: battery keepalive — inactive for %.1fh, switching to SBU for %ds",
            inactive_duration.total_seconds() / 3600,
            self.keepalive.DURATION_SEC,
        )

        return HemsDecision(
            output_priority=OutputPriority.SBU,
            reason=_Reason.KEEPALIVE_START,
        )

    def finish_keepalive(self, now: datetime | None = None) -> HemsDecision:
        """Finish keepalive — switch back to USB."""
        now = now or datetime.now()
        self.keepalive.in_progress = False
        self.keepalive.last_activity_at = now
        _LOGGER.info("HEMS: keepalive finished — back to USB")
        return HemsDecision(
            output_priority=OutputPriority.USB,
            reason=_Reason.KEEPALIVE_END,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # PRIVATE — Adaptive mode (full decision tree)
    # ═══════════════════════════════════════════════════════════════════════

    def _evaluate_adaptive(
        self,
        *,
        soc: float,
        pv_power: float,
        grid_power: float,
        battery_power: float,
        load_power: float,
        grid_available: bool,
        current_output: str | None,
        current_charger: str | None,
        now: datetime,
        forecast_tomorrow_kwh: float | None,
        forecast_day_after_kwh: float | None,
        reserve_soc: float,
        min_operating_soc: float,
        tarif_day: float,
        tarif_night: float,
        buzzer_off: bool,
        predictive_hint: Any | None = None,
        predictive_plan: Any | None = None,
        predictive_mode: str = "off",
    ) -> HemsDecision:
        """Full adaptive mode — ported from Dart executeAdaptiveMode.

        Decision flow:
        0. Safety hard floor handled by evaluate() before mode dispatch
        1. Manual override (already handled above)
        2. Night tariff window: USB always, tariff-aware charging
        3. Daytime: charger OSO, critical recovery, surplus detection
        4. Evening protection: 5 conditions
        """
        hour = now.hour
        is_night = hour >= 23 or hour < 7
        surplus = pv_power - load_power

        # ── Step 1: Night window (23:00–07:00) ───────────────────────
        if is_night:
            return self._adaptive_night(
                soc=soc,
                pv_power=pv_power,
                load_power=load_power,
                battery_power=battery_power,
                forecast_tomorrow_kwh=forecast_tomorrow_kwh,
                current_output=current_output,
                current_charger=current_charger,
                now=now,
                reserve_soc=reserve_soc,
                tarif_day=tarif_day,
                tarif_night=tarif_night,
                buzzer_off=buzzer_off,
                predictive_hint=predictive_hint,
                predictive_mode=predictive_mode,
            )

        # ── Step 2: Daytime ───────────────────────────────────────────
        return self._adaptive_day(
            soc=soc,
            pv_power=pv_power,
            load_power=load_power,
            battery_power=battery_power,
            surplus=surplus,
            forecast_tomorrow_kwh=forecast_tomorrow_kwh,
            current_output=current_output,
            current_charger=current_charger,
            now=now,
            reserve_soc=reserve_soc,
            min_operating_soc=min_operating_soc,
            tarif_day=tarif_day,
            tarif_night=tarif_night,
            buzzer_off=buzzer_off,
            predictive_hint=predictive_hint,
            predictive_plan=predictive_plan,
            predictive_mode=predictive_mode,
        )

    def _adaptive_night(
        self,
        *,
        soc: float,
        pv_power: float,
        load_power: float,
        battery_power: float,
        forecast_tomorrow_kwh: float | None,
        current_output: str | None,
        current_charger: str | None,
        now: datetime,
        reserve_soc: float,
        tarif_day: float,
        tarif_night: float,
        buzzer_off: bool,
        predictive_hint: Any | None = None,
        predictive_mode: str = "off",
    ) -> HemsDecision:
        """Night mode — USB output, tariff-aware charging.

        Ported from Dart Adaptive Mode Step 2 (night window).
        """
        # Charger decision: charge from grid at night if cheap
        # Simple heuristic: always SNU at night (cheap tariff)
        charger = ChargerPriority.SNU
        output = OutputPriority.USB

        # If SOC is near full and no load deficit, use OSO (solar only)
        if soc >= 80 and pv_power > load_power:
            charger = ChargerPriority.OSO

        # Assist can stop grid charging once a valid morning target is met.
        # The common reserve floor has priority over this optimization.
        morning_target = _hint_soc_target(predictive_hint, "target_soc_morning")
        if predictive_mode == "assist" and morning_target is not None and soc >= morning_target:
            charger = ChargerPriority.OSO

        return HemsDecision(
            output_priority=output,
            charger_priority=charger,
            reason=_Reason.NIGHT_USB,
            buzzer_off=buzzer_off,
        )

    def _adaptive_day(
        self,
        *,
        soc: float,
        pv_power: float,
        load_power: float,
        battery_power: float,
        surplus: float,
        forecast_tomorrow_kwh: float | None,
        current_output: str | None,
        current_charger: str | None,
        now: datetime,
        reserve_soc: float,
        min_operating_soc: float,
        tarif_day: float,
        tarif_night: float,
        buzzer_off: bool,
        predictive_hint: Any | None = None,
        predictive_plan: Any | None = None,
        predictive_mode: str = "off",
    ) -> HemsDecision:
        """Daytime mode — solar priority when surplus, grid fallback.

        Ported from Dart Adaptive Mode Step 3 (daytime).
        """
        hour = now.hour
        is_evening = hour >= 17

        # Charger: solar only during the day
        charger = ChargerPriority.OSO

        # ── Critical recovery: SOC < 35% → Force USB + SNU until 45% ──
        if soc < 35:
            if current_output != OutputPriority.USB or current_charger != ChargerPriority.SNU:
                _LOGGER.info(
                    "HEMS adaptive: critical recovery SOC=%.1f%% < 35%% → USB+SNU",
                    soc,
                )
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.SNU,
                reason="critical_recovery",
                buzzer_off=buzzer_off,
            )

        # ── Hysteresis: SOC 35-45% + on USB → maintain SNU ──────────
        if 35 <= soc <= 45 and current_output == OutputPriority.USB:
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.SNU,
                reason="hysteresis_recovery",
                buzzer_off=buzzer_off,
            )

        # ── Realtime surplus detection ───────────────────────────────
        pv_threshold = self.tuning.compute_adaptive_pv_surplus(now=now)

        if (
            pv_power > 0
            and soc >= min_operating_soc
            and surplus >= pv_threshold
        ):
            _LOGGER.debug(
                "HEMS adaptive: PV surplus %.0fW ≥ %.0fW → SBU+OSO",
                surplus, pv_threshold,
            )
            return HemsDecision(
                output_priority=OutputPriority.SBU,
                charger_priority=ChargerPriority.OSO,
                reason=_Reason.SURPLUS_SBU,
                buzzer_off=buzzer_off,
            )

        # ── Evening protection ────────────────────────────────────────
        if is_evening:
            return self._adaptive_evening_protection(
                soc=soc,
                surplus=surplus,
                battery_power=battery_power,
                forecast_tomorrow_kwh=forecast_tomorrow_kwh,
                now=now,
                reserve_soc=reserve_soc,
                buzzer_off=buzzer_off,
            )

        # ── Default branch: decide SBU vs USB based on whether
        # running the battery now would actually save anything.
        #
        # Round-trip losses through the inverter are ~10-15%. So draining
        # a fully-charged battery to feed a 700W load for an hour only
        # makes sense if we expect to recharge it cheaply (sun today /
        # tonight at off-peak tariff). Otherwise we waste the SOC and pay
        # to refill it later.
        #
        # Rule:
        #   - PV surplus (PV > load) → SBU+OSO so we store solar in
        #     the battery as we go. Handled above as SURPLUS_SBU.
        #   - Good forecast (tomorrow >= 1.0 kWh) → we'll recharge from
        #     sun, so SBU is fine even on cloudy afternoons.
        #   - SOC near full (>= 80%) and reasonable load (>150 W):
        #     draining saves noticeable grid imports → SBU.
        #   - Otherwise → USB+SNU. Don't touch the battery; let the
        #     charger top it up at off-peak if the forecast is bad.
        soc_healthy = soc >= max(reserve_soc + 5.0, 30.0)
        soc_full = soc >= 80.0
        good_forecast = forecast_tomorrow_kwh is not None and forecast_tomorrow_kwh >= 1.0
        load_significant = load_power >= 150.0

        if soc_full and load_significant and (good_forecast or pv_power > 50.0):
            return HemsDecision(
                output_priority=OutputPriority.SBU,
                charger_priority=ChargerPriority.OSO,
                reason="battery_drives_load",
                buzzer_off=buzzer_off,
            )

        if soc_healthy and good_forecast and load_significant:
            return HemsDecision(
                output_priority=OutputPriority.SBU,
                charger_priority=ChargerPriority.OSO,
                reason="forecast_good_use_battery",
                buzzer_off=buzzer_off,
            )

        # ── Default: don't waste grid energy on a battery that
        # doesn't need it. If we keep output=USB (load from grid),
        # the charger should be OSO (solar-only) unless SOC is low
        # enough that we'd genuinely benefit from topping up. This
        # eliminates the ~15% round-trip loss when SNU charges from
        # grid into a near-full battery.
        if soc >= 90.0:
            # Battery already topped off — don't waste grid power on it.
            # Keep it as a real backup reserve.
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.OSO,
                reason="day_default",
                buzzer_off=buzzer_off,
            )

        # ── SOC healthy but not full — still prefer solar-only charging.
        # Grid assistance (SNU) only makes sense if we expect to use the
        # stored energy soon (good evening load forecast).
        default_decision = HemsDecision(
            output_priority=OutputPriority.USB,
            charger_priority=ChargerPriority.OSO,
            reason="day_default",
            buzzer_off=buzzer_off,
        )

        # ── Predictive ASSIST override: when the planner is in
        # assist mode AND produced a hint AND that hint says we
        # need to be at/above the ML evening target by now AND the
        # SOC is close to (but not necessarily above) that target,
        # we may switch to battery-driven output (SBU+OSO) to use
        # the banked energy for the current load instead of
        # importing from grid.
        #
        # Hard guards — must all be true:
        #   1. predictive_mode == "assist"
        #   2. hint is not None (gate already enforced above)
        #   3. SOC ≥ reserve_soc (already on this branch)
        #   4. ML target evening is reasonable (50..90)
        #   5. SOC is within 5% of ML target — we're already
        #      "full enough", promoting to SBU makes sense
        #   6. forecast_tomorrow_kwh ≥ 1.0 OR pv_power > 250 —
        #      we'll recharge from sun tomorrow or now, so draining
        #      the battery doesn't cost us anything
        #   7. load_significant — there must be load to serve,
        #      otherwise the SBU change wastes a state transition
        if (
            predictive_mode == "assist"
            and predictive_hint is not None
            and getattr(predictive_hint, "target_soc_evening", None) is not None
            and _normalize_output(current_output) in (None, '', '0')
            and load_significant
        ):
            evening_target = _hint_soc_target(predictive_hint, "target_soc_evening")
            if (
                evening_target is not None
                and 50.0 <= evening_target <= 90.0
                and soc >= evening_target - 5.0
                and (good_forecast or pv_power > 250.0)
            ):
                return HemsDecision(
                    output_priority=OutputPriority.SBU,
                    charger_priority=ChargerPriority.OSO,
                    reason="predictive_assist_evening",
                    buzzer_off=buzzer_off,
                )

        return default_decision

    def _adaptive_evening_protection(
        self,
        *,
        soc: float,
        surplus: float,
        battery_power: float,
        forecast_tomorrow_kwh: float | None,
        now: datetime,
        reserve_soc: float,
        buzzer_off: bool,
    ) -> HemsDecision:
        """Evening protection — 5 conditions from Dart.

        1. SOC near reserve → Emergency USB + SNU
        2. Available energy ≤ safety margin → USB
        3. Deficit > 30% of available → USB
        4. SOC ≥ reserve+10 + deficit manageable → SBU
        5. Default: keep USB for safety
        """
        available_energy = soc  # simplified: SOC as percentage of available

        # Condition 1: SOC near reserve → emergency
        if soc <= reserve_soc + 5:
            _LOGGER.info(
                "HEMS adaptive: evening emergency SOC=%.1f%% near reserve → USB+SNU",
                soc,
            )
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.SNU,
                reason=_Reason.EVENING_PROTECT,
                buzzer_off=buzzer_off,
            )

        # Condition 2: Available energy very low
        if available_energy <= 15:
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.SNU,
                reason=_Reason.EVENING_PROTECT,
                buzzer_off=buzzer_off,
            )

        # Condition 3: High deficit (surplus negative, large)
        if surplus < 0 and abs(surplus) > available_energy * 0.3 * 100:
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.SNU,
                reason=_Reason.EVENING_PROTECT,
                buzzer_off=buzzer_off,
            )

        # Condition 4: SOC comfortable, deficit manageable → use battery
        if soc >= reserve_soc + 10 and surplus >= -200:
            return HemsDecision(
                output_priority=OutputPriority.SBU,
                charger_priority=ChargerPriority.OSO,
                reason=_Reason.EVENING_BATTERY_USE,
                buzzer_off=buzzer_off,
            )

        # Condition 5: Default safety → USB but with OSO (solar-only
        # charging). Charging from grid at evening peak rates makes no
        # sense — we'd pay full price for energy we already have in the
        # battery (or will get tomorrow from solar).
        return HemsDecision(
            output_priority=OutputPriority.USB,
            charger_priority=ChargerPriority.OSO,
            reason=_Reason.EVENING_PROTECT,
            buzzer_off=buzzer_off,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # PRIVATE — Night Arbitrage mode
    # ═══════════════════════════════════════════════════════════════════════

    def _evaluate_arbitrage(
        self,
        *,
        soc: float,
        pv_power: float,
        load_power: float,
        current_output: str | None,
        current_charger: str | None,
        now: datetime,
        reserve_soc: float,
        buzzer_off: bool,
        predictive_hint: Any | None = None,
        predictive_plan: Any | None = None,
        predictive_mode: str = "off",
    ) -> HemsDecision:
        """Night Arbitrage mode — charge at night (cheap), discharge at day (expensive).

        Night (23:00-07:00): USB + SNU (charge from grid)
        Daytime: SBU if surplus + SOC ok; charger always OSO
        """
        hour = now.hour
        is_night = hour >= 23 or hour < 7

        if is_night:
            charger = ChargerPriority.SNU
            morning_target = _hint_soc_target(predictive_hint, "target_soc_morning")
            if predictive_mode == "assist" and morning_target is not None and soc >= morning_target:
                charger = ChargerPriority.OSO
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=charger,
                reason="arbitrage_night",
                buzzer_off=buzzer_off,
            )
        else:
            # Daytime: use solar, charge only from solar
            surplus = pv_power - load_power
            if soc >= reserve_soc + 10 and surplus > 0:
                return HemsDecision(
                    output_priority=OutputPriority.SBU,
                    charger_priority=ChargerPriority.OSO,
                    reason="arbitrage_day_sbu",
                    buzzer_off=buzzer_off,
                )
            return HemsDecision(
                output_priority=OutputPriority.USB,
                charger_priority=ChargerPriority.OSO,
                reason="arbitrage_day_usb",
                buzzer_off=buzzer_off,
            )

    # ═══════════════════════════════════════════════════════════════════════
    # PRIVATE — Storm mode
    # ═══════════════════════════════════════════════════════════════════════

    def _evaluate_storm(
        self,
        *,
        soc: float,
        current_output: str | None,
        current_charger: str | None,
        buzzer_off: bool,
        predictive_hint: Any | None = None,
        predictive_mode: str = "off",
    ) -> HemsDecision:
        """Storm mode — maximize backup readiness.

        Force USB + SNU (precharge battery from grid for expected outage).
        Predictive ML CANNOT weaken this — storm always wins over ML.
        """
        # Storm never weakens safety — the storm decision is a hard
        # "charge for the outage". Even if ML hint says "drop charger
        # to OSO", we keep SNU. ML presence is recorded for sensor
        # surfaces but ignored here.
        return HemsDecision(
            output_priority=OutputPriority.USB,
            charger_priority=ChargerPriority.SNU,
            reason="storm_mode",
            buzzer_off=buzzer_off,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # PRIVATE — Anti-flapping
    # ═══════════════════════════════════════════════════════════════════════

    def _apply_anti_flapping(
        self, decision: HemsDecision, now: datetime
    ) -> HemsDecision:
        """Suppress rapid mode switching via dwell time + command dedup."""

        # Command dedup: skip if same command within window
        if decision.output_priority and decision.output_priority == self._last_cmd_output:
            if (
                self._last_cmd_output_at
                and (now - self._last_cmd_output_at) < self._command_dedup_window
            ):
                decision.output_priority = None  # skip
                decision.reason = _Reason.DEDUP_OUTPUT

        if decision.charger_priority and decision.charger_priority == self._last_cmd_charger:
            if (
                self._last_cmd_charger_at
                and (now - self._last_cmd_charger_at) < self._command_dedup_window
            ):
                decision.charger_priority = None  # skip
                decision.reason = _Reason.DEDUP_CHARGER

        # Dwell lock: don't switch output too frequently
        if decision.output_priority and self._last_output_switch_at:
            elapsed = (now - self._last_output_switch_at).total_seconds() / 60
            if elapsed < self._current_dwell_min:
                # Allow USB (safety) to always go through
                if decision.output_priority != OutputPriority.USB:
                    decision.output_priority = None
                    decision.reason = _Reason.DWELL_LOCK
                else:
                    # Reset dwell when switching to USB (safety override)
                    self._last_output_switch_at = now
            else:
                self._last_output_switch_at = now
        elif decision.output_priority:
            self._last_output_switch_at = now

        return decision

    # ═══════════════════════════════════════════════════════════════════════
    # PRIVATE — Battery activity tracking
    # ═══════════════════════════════════════════════════════════════════════

    def _track_battery_activity(self, battery_power: float, now: datetime) -> None:
        """Track battery activity for keepalive timing."""
        if abs(battery_power) > self.keepalive.POWER_INACTIVE_THRESHOLD:
            self.keepalive.last_activity_at = now
