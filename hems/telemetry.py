"""Telemetry mapping for Predictive planner.

Pure functions that translate raw inverter API payloads into a clean
``PlannerInputs`` for ``hems.predictive``. No I/O, no globals — easy
to unit-test.

The previous version silently used ``raw.get('gridVoltage', 230)`` and
the live ``grid_v0`` showed up in the hint while actual voltage was
232 V (source-provenance bug). This module centralises that mapping
and records where it came from so observability sensors can show
exactly what the planner saw.

All fields are explicit: missing values are ``None`` (not 0.0) so the
planner can fall back to conservative defaults instead of silently
treating "no data" as "battery is empty".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .engine import SmartMode, _finite_number


# Real upper/lower bounds for telemetry — anything outside is a
# suspect value and treated as missing.
_VOLTAGE_MIN = 80.0   # below this is clearly a sensor dropout
_VOLTAGE_MAX = 280.0
_POWER_MAX_W = 50_000.0   # 50 kW is huge — anything more is sensor noise
_SOC_MIN = 0.0
_SOC_MAX = 100.0


@dataclass(slots=True, frozen=True)
class TelemetrySource:
    """Where each PlannerInputs field came from.

    ``origin`` is one of ``api``, ``override``, ``fallback``. ``stale``
    is True if the underlying value is older than ``max_age_sec``.
    """

    origin: str
    stale: bool = False
    raw_value: Any = None


@dataclass(slots=True)
class PlannerInputs:
    """Validated planner inputs — guaranteed bounds + provenance.

    Every field has a matching ``*_source`` so the predictive plan
    sensor can show exactly which fields came from real measurements
    and which were conservative fallbacks.

    ``soc_unknown`` is True iff the SOC was absent or invalid in the
    raw sample. The engine must treat this as a hard stop for any
    control command: charging mode, output priority, or backup
    behaviour. ``soc`` retains the conservative fallback value
    (so the engine can still compute display numbers) but downstream
    code is required to consult ``soc_unknown`` before issuing a
    write. T01 hardening.
    """

    now: datetime
    # T01 follow-up: ``soc`` is ``None`` whenever the meter is
    # unreadable. The companion ``soc_unknown`` flag is the
    # canonical source of truth; ``soc`` is kept for backward
    # compatibility but should not be relied on by the planner.
    soc: float | None
    pv_w: float
    load_w: float
    grid_w: float
    batt_w: float
    battery_capacity_kwh: float
    grid_v: float
    grid_ok: bool
    smart_mode: int
    soc_unknown: bool = False

    # Forecasts (None = missing — never substitute zero)
    forecast_today_kwh: float | None = None
    forecast_tomorrow_kwh: float | None = None
    hourly_pv: list[float] = field(default_factory=list)
    dated_hourly_pv: dict[int, float] | None = None
    hourly_radiation: list[float] = field(default_factory=list)
    hourly_weather_codes: list[int | None] = field(default_factory=list)

    # Tariff schedule (24 values UAH/kWh). Empty = unknown.
    tariff_schedule: list[float] = field(default_factory=list)
    # R05: day/night rate fallbacks for the planner to
    # use when ``tariff_schedule`` is empty. These are
    # the operator-configured UAH/kWh rates (not
    # module-level constants).
    tariff_day_fallback: float = 4.32
    tariff_night_fallback: float = 2.16

    # Battery reserve floor in percent. The planner
    # must not let a plan drain the SOC below this
    # value. T08: previously the planner hard-coded
    # 20 % here and 15 % in ``plan_soc_targets``,
    # which silently overrode the user-configured
    # reserve SOC. The default 20 % matches the legacy
    # behaviour for callers that do not set the field.
    # Production callers (coordinator) pass the
    # ``reserve_soc`` entry option through.
    reserve_soc: float = 20.0

    # Charge / discharge round-trip efficiency. The
    # 85 % / 90 % defaults match the existing
    # ``simulate_24h`` constants; T08 exposes them so
    # production can override them when battery or
    # inverter health data justifies a different
    # round-trip figure.
    charge_efficiency: float = 0.85
    discharge_efficiency: float = 0.90

    # Historical consumption (most-recent day last; length <= 30).
    consumption_history: list = field(default_factory=list)
    night_charge_window: tuple[int, int] = (23, 7)

    # Provenance (origin strings only — cheap to copy)
    soc_source: TelemetrySource | None = None
    pv_source: TelemetrySource | None = None
    grid_v_source: TelemetrySource | None = None
    forecast_source: TelemetrySource | None = None
    consumption_source: TelemetrySource | None = None

    def missing_fields(self) -> list[str]:
        """Return names of fields that were missing/fallback.

        Used by the planner to bump the fallback_reason in its
        outputs and the diagnostic attributes.
        """
        out: list[str] = []
        if self.soc_source and self.soc_source.origin == "fallback":
            out.append("soc")
        if self.grid_v_source and self.grid_v_source.origin == "fallback":
            out.append("grid_v")
        if self.forecast_source and self.forecast_source.origin == "fallback":
            out.append("forecast")
        if self.consumption_source and self.consumption_source.origin == "fallback":
            out.append("consumption")
        return out


# ──────────────────────────────────────────────────────────────────
# Sanitisers
# ──────────────────────────────────────────────────────────────────


def _clamp(value: float | None, low: float, high: float, fallback: float) -> tuple[float, bool]:
    """Clamp to [low, high]. Returns (value, is_fallback)."""
    if value is None:
        return fallback, True
    try:
        v = float(value)
    except (TypeError, ValueError):
        return fallback, True
    if v != v:  # NaN
        return fallback, True
    if v < low or v > high:
        return fallback, True
    return v, False


def _clean_forecast_kwh(value: Any) -> tuple[float | None, str]:
    """Return (forecast_kwh, origin).

    Valid range 0–50 kWh/day. Returns (None, 'fallback') for any
    invalid input (None, NaN, negative, out-of-range).
    """
    if value is None:
        return None, "fallback"
    try:
        fv = float(value)
    except (TypeError, ValueError):
        return None, "fallback"
    if fv != fv:  # NaN
        return None, "fallback"
    if fv < 0.0 or fv > 50.0:
        return None, "fallback"
    return fv, "api"


def _sanitize_hourly(values: Any, v_low: float, v_high: float) -> list[float]:
    """Sanitize an hourly array, replacing suspect values with 0."""
    if not values:
        return []
    out: list[float] = []
    for v in values:
        if v is None:
            out.append(0.0)
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            out.append(0.0)
            continue
        if fv != fv:  # NaN
            out.append(0.0)
            continue
        out.append(max(v_low, min(v_high, fv)))
    return out


# ──────────────────────────────────────────────────────────────────
# Public mapping
# ──────────────────────────────────────────────────────────────────


def build_planner_inputs(
    raw: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    smart_mode: int = int(SmartMode.ADAPTIVE),
    corrected_soc: float | None = None,
    forecast_tomorrow_kwh: float | None = None,
    forecast_today_kwh: float | None = None,
    hourly_pv: list[float] | None = None,
    dated_hourly_pv: dict[int, float] | None = None,
    hourly_radiation: list[float] | None = None,
    hourly_weather_codes: list[int | None] | None = None,
    tariff_schedule: list[float] | None = None,
    consumption_history: list | None = None,
    battery_capacity_kwh: float = 4.8,
    grid_available: bool = True,
    max_age_sec: float = 60.0,
    night_charge_window: tuple[int, int] = (23, 7),
    soc_unknown: bool | None = None,
    # T08 follow-up: the user-configured reserve_soc
    # must reach the planner, not be silently
    # replaced by the dataclass default of 20. The
    # coordinator now passes the option through here;
    # the planner reads it from PlannerInputs and
    # ``simulate_24h`` clamps SOC above this value.
    reserve_soc: float | None = None,
    # Efficiency bounds follow physical reality:
    # round-trip efficiency of any inverter is
    # strictly between 0 and 1 (exclusive on both
    # sides), with 0.85 charge and 0.90 discharge as
    # the documented default for this station.
    charge_efficiency: float = 0.85,
    discharge_efficiency: float = 0.90,
    # R05: day/night rate fallbacks for the planner.
    tariff_day_fallback: float = 4.32,
    tariff_night_fallback: float = 2.16,
) -> PlannerInputs:
    """Build a ``PlannerInputs`` from raw API + already-corrected values.

    Args:
        raw: dict from ``InverterApiClient.fetch_realtime_data()``.
        now: clock to stamp on the inputs (defaults to naive UTC now).
        soc_unknown: explicit override for the SOC-unknown flag. The
            function recomputes this from ``corrected_soc`` and
            ``raw["batterySoc"]`` if it is left as ``None``; an
            explicit ``True`` forces the unknown state even if the
            raw payload carries a numeric SOC. This is the path the
            audit requires: the caller that knows the meter is
            unreadable must not be re-overridden by a stale numeric
            value the parser happens to produce.
        smart_mode: 0=Adaptive, 1=Arbitrage, 2=Storm.
        corrected_soc: pre-corrected SOC (after voltage compensation).
            If None, ``raw.get("batterySoc")`` is used with provenance
            marked as ``api`` (uncorrected).
        grid_available: from coordinator's grid-outage detector.
        max_age_sec: how old a raw sample can be before it counts as
            stale (we cannot really tell here without a timestamp, so
            this is a hook for callers to flag suspicious polls).
    """
    raw = raw if isinstance(raw, dict) else {}
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)

    # T08 follow-up: validate the user-configured
    # ``reserve_soc`` at the boundary. NaN, infinity,
    # text, or out-of-range values are rejected with
    # a clear ValueError so the operator sees the
    # failure in the log instead of the planner
    # silently using a default. ``None`` falls back
    # to the documented 20 % sane default.
    if reserve_soc is None:
        reserve_soc_value = 20.0
    else:
        # ``_finite_number`` from ``hems.engine`` is
        # the same validator the T12 path uses, so
        # ``reserve_soc`` cannot be a string,
        # ``True``/``False``, NaN, or infinity.
        checked = _finite_number(reserve_soc)
        if checked is None:
            raise ValueError(
                f"Invalid reserve_soc={reserve_soc!r}; "
                f"must be a finite number"
            )
        if not (0.0 <= checked <= 100.0):
            raise ValueError(
                f"Invalid reserve_soc={checked!r}; "
                f"must be in [0, 100]"
            )
        reserve_soc_value = checked

    # T08 follow-up: efficiency bounds. The
    # physical round-trip efficiency of any inverter
    # is strictly between 0 and 1 (exclusive). A
    # value of 0 would mean "no energy ever reaches
    # the load"; a value of 1 would mean a
    # perpetual-motion machine. We reject both
    # endpoints and any non-numeric input, mirroring
    # the SOC / reserve_soc validation above. The
    # defaults (0.85 / 0.90) match the values the
    # T07 audit verified.
    def _check_efficiency(name, value):
        checked = _finite_number(value)
        if checked is None:
            raise ValueError(
                f"Invalid {name}={value!r}; "
                f"must be a finite number"
            )
        if not (0.0 < checked < 1.0):
            raise ValueError(
                f"Invalid {name}={checked!r}; "
                f"must be strictly between 0 and 1"
            )
        return checked

    charge_eff = _check_efficiency(
        "charge_efficiency", charge_efficiency
    )
    discharge_eff = _check_efficiency(
        "discharge_efficiency", discharge_efficiency
    )

    # ── SOC ────────────────────────────────────────────────────────
    # T01 hardening: distinguish a real SOC reading from a missing or
    # invalid one. The previous code path always fell back to 100%,
    # which both made a critically-empty battery look full and
    # leaked a fabricated value into the engine — including any
    # decision to switch charger / output priority. We now mark
    # ``soc_unknown=True`` so the coordinator can refuse to send
    # commands while the meter is unreadable.
    soc_value: Any
    if soc_unknown is None:
        soc_unknown = False
    if corrected_soc is not None:
        soc_value = float(corrected_soc)
        soc_origin = "api"
    else:
        soc_value = raw.get("batterySoc")
        soc_origin = "api"

    if soc_value is None:
        soc_unknown = True
        soc_value = None
    else:
        try:
            soc_value = float(soc_value)
        except (TypeError, ValueError):
            soc_unknown = True
            soc_value = None
        else:
            if soc_value != soc_value or math.isinf(soc_value):
                soc_unknown = True
                soc_value = None
            elif soc_value < _SOC_MIN or soc_value > _SOC_MAX:
                soc_unknown = True
                # Out-of-range readings are NOT used to drive the
                # engine either. We still keep ``soc`` at the
                # conservative fallback for display, but it is
                # explicitly marked unknown.
                soc_value = None

    if soc_unknown:
        # T01 follow-up: do NOT pretend a missing SOC is 100 %. The
        # previous code path used 100 % as a display-only fallback
        # for the engine, but the predictive planner read the same
        # field and treated it as a fully-charged battery. That
        # diagnostic leak is what the audit calls out: planner
        # must see ``soc is None`` whenever the meter is unreadable.
        # The engine is told not to dispatch any command through a
        # separate ``soc_unknown`` flag on PlannerInputs; legacy
        # fields that need a numeric value fall back to 100 below.
        soc = None
        soc_fallback = True
    else:
        soc, soc_fallback = _clamp(soc_value, _SOC_MIN, _SOC_MAX, fallback=100.0)

    # ── PV power ───────────────────────────────────────────────────
    pv, pv_fallback = _clamp(raw.get("pvPower"), 0.0, _POWER_MAX_W, fallback=0.0)

    # ── Load power ─────────────────────────────────────────────────
    load, load_fallback = _clamp(raw.get("loadPower"), 0.0, _POWER_MAX_W, fallback=0.0)

    # ── Grid power ─────────────────────────────────────────────────
    grid, grid_fallback = _clamp(raw.get("gridPower"), -_POWER_MAX_W, _POWER_MAX_W, fallback=0.0)

    # ── Battery power ──────────────────────────────────────────────
    batt, batt_fallback = _clamp(raw.get("batteryPower"), -_POWER_MAX_W, _POWER_MAX_W, fallback=0.0)

    # ── Grid voltage ───────────────────────────────────────────────
    grid_v_raw = raw.get("gridVoltage")
    grid_v, grid_v_fallback = _clamp(grid_v_raw, _VOLTAGE_MIN, _VOLTAGE_MAX, fallback=230.0)

    # ── Forecasts ─────────────────────────────────────────────────
    forecast_tomorrow_kwh, forecast_origin = _clean_forecast_kwh(forecast_tomorrow_kwh)
    forecast_today_value, _ = _clean_forecast_kwh(forecast_today_kwh)

    # ── Hourly arrays ──────────────────────────────────────────────
    hourly_pv_sanitized = _sanitize_hourly(hourly_pv, 0.0, _POWER_MAX_W)
    hourly_rad_sanitized = _sanitize_hourly(hourly_radiation, 0.0, 1400.0)

    # Weather codes: keep as ints or None
    hourly_weather: list[int | None] = []
    if hourly_weather_codes:
        for c in hourly_weather_codes:
            if c is None:
                hourly_weather.append(None)
            else:
                try:
                    hourly_weather.append(int(c))
                except (TypeError, ValueError):
                    hourly_weather.append(None)

    # ── Tariff schedule ────────────────────────────────────────────
    # R05: invalid tariff values used to be silently
    # replaced with 0.0 here, which then propagated as
    # "free electricity" into the planner. A 0.0 tariff
    # at the night window tells the planner to discharge
    # during the cheapest hours — wrong. Refuse NaN,
    # Infinity, boolean, negative, and out-of-range
    # values: the schedule is set to an empty list and
    # the planner rebuilds it from the operator's
    # ``tariff_day_fallback`` / ``tariff_night_fallback``
    # parameters (or the documented defaults). The
    # caller in ``hems.engine`` passes the
    # coordinator's configured day/night rates, so the
    # operator's 8/3 UAH/kWh config reaches the
    # planner, not the hardcoded 4.32/2.16.
    tariff: list[float] = []
    if tariff_schedule and len(tariff_schedule) >= 24:
        for v in tariff_schedule[:24]:
            fv = _finite_number(v)
            if fv is None or fv < 0.0 or fv > 50.0:
                # Refuse the entire schedule. The
                # planner will rebuild from the
                # fallbacks the caller supplied.
                tariff = []
                break
            tariff.append(fv)
    if not tariff:
        # No schedule supplied or every value was
        # invalid. We do NOT substitute 0.0
        # ("free electricity"); the planner uses
        # ``tariff_day_fallback`` / ``tariff_night_fallback``
        # for an empty schedule.
        tariff = []

    # ── Consumption history ────────────────────────────────────────
    # Audit T19 follow-up: telemetry must
    # preserve the dated shape so the
    # ``ConsumptionPredictor`` can filter
    # by ``date.weekday()`` end-to-end.
    # We accept three shapes:
    #
    #   * ``list[tuple[date, list[float], bool]]``
    #     - canonical. The boolean is the
    #     ``gap_filled`` trust flag from
    #     ``history_builder.build_hourly_load_matrix``.
    #   * ``list[tuple[date, list[float]]]`` -
    #     2-tuple legacy shape (no gap flag,
    #     trusted by default).
    #   * ``list[list[float]]`` - legacy
    #     flat shape, no dates. The
    #     predictor falls back to all-history.
    #
    # All three are normalised to the
    # canonical 3-tuple shape so the
    # downstream code does not need to
    # branch on shape again.
    consumption: list = []
    if consumption_history:
        for day in consumption_history[-30:]:
            if isinstance(day, tuple):
                # Dated shape. The
                # tuple may be 2- or
                # 3-element.
                if len(day) == 3:
                    d, payload, gap_flag = day
                elif len(day) == 2:
                    d, payload = day
                    gap_flag = False
                else:
                    continue
                if not isinstance(payload, list) or len(payload) != 24:
                    continue
                if not isinstance(d, date):
                    # T19 shapes sometimes
                    # pass ``None`` as the
                    # date. We keep the
                    # row for the
                    # all-history fallback
                    # but strip the date
                    # so the predictor does
                    # not crash on
                    # ``None.weekday()``.
                    d = None
                clean_day = _sanitize_hourly(payload, 0.0, _POWER_MAX_W)
                consumption.append((d, clean_day, bool(gap_flag)))
                continue
            if not isinstance(day, list) or len(day) != 24:
                continue
            clean_day = _sanitize_hourly(day, 0.0, _POWER_MAX_W)
            # Legacy shape: no date, no
            # gap flag. The predictor
            # treats this as trusted
            # all-history.
            consumption.append((None, clean_day, False))

    return PlannerInputs(
        now=now,
        soc=soc,
        soc_unknown=soc_unknown,
        pv_w=pv,
        load_w=load,
        grid_w=grid,
        batt_w=batt,
        battery_capacity_kwh=max(0.5, float(battery_capacity_kwh)),
        grid_v=grid_v,
        grid_ok=bool(grid_available),
        smart_mode=int(smart_mode),
        forecast_today_kwh=forecast_today_value,
        forecast_tomorrow_kwh=forecast_tomorrow_kwh,
        hourly_pv=hourly_pv_sanitized,
        dated_hourly_pv=({k: float(v) for k, v in dated_hourly_pv.items()
                         if type(k) is int and type(v) in (int, float) and 0 <= v <= 20000}
                        if isinstance(dated_hourly_pv, dict) else ({} if dated_hourly_pv is not None else None)),
        hourly_radiation=hourly_rad_sanitized,
        hourly_weather_codes=hourly_weather,
        tariff_schedule=tariff,
        consumption_history=consumption,
        night_charge_window=night_charge_window,
        # T08 follow-up: the validated values reach
        # the planner here. The previous code path
        # let the dataclass default (20.0) shadow
        # the user's option, so a 35 % reserve was
        # silently dropped before any planner call.
        reserve_soc=reserve_soc_value,
        charge_efficiency=charge_eff,
        discharge_efficiency=discharge_eff,
        # R05: day/night fallbacks. The planner uses
        # them when ``tariff_schedule`` is empty (the
        # validator rejected every value in the list).
        # We pass the parameters the caller supplied;
        # ``build_planner_inputs`` does not re-validate
        # these (the coordinator's
        # ``_build_tariff_schedule`` already does that).
        tariff_day_fallback=tariff_day_fallback,
        tariff_night_fallback=tariff_night_fallback,
        soc_source=TelemetrySource(
            origin="fallback" if soc_fallback else soc_origin,
            stale=False,
            raw_value=soc_value,
        ),
        pv_source=TelemetrySource(
            origin="fallback" if pv_fallback else "api",
            stale=False,
            raw_value=raw.get("pvPower"),
        ),
        grid_v_source=TelemetrySource(
            origin="fallback" if grid_v_fallback else "api",
            stale=False,
            raw_value=grid_v_raw,
        ),
        forecast_source=TelemetrySource(
            origin=forecast_origin,
            stale=False,
            raw_value=forecast_tomorrow_kwh,
        ),
        consumption_source=TelemetrySource(
            origin="fallback" if not consumption else "api",
            stale=False,
            raw_value=None,
        ),
    )
