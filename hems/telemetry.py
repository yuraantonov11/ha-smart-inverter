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

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .engine import SmartMode


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
    """

    now: datetime
    soc: float
    pv_w: float
    load_w: float
    grid_w: float
    batt_w: float
    battery_capacity_kwh: float
    grid_v: float
    grid_ok: bool
    smart_mode: int

    # Forecasts (None = missing — never substitute zero)
    forecast_today_kwh: float | None = None
    forecast_tomorrow_kwh: float | None = None
    hourly_pv: list[float] = field(default_factory=list)
    hourly_radiation: list[float] = field(default_factory=list)
    hourly_weather_codes: list[int | None] = field(default_factory=list)

    # Tariff schedule (24 values UAH/kWh). Empty = unknown.
    tariff_schedule: list[float] = field(default_factory=list)

    # Historical consumption (most-recent day last; length <= 7).
    consumption_history: list[list[float]] = field(default_factory=list)

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
    hourly_radiation: list[float] | None = None,
    hourly_weather_codes: list[int | None] | None = None,
    tariff_schedule: list[float] | None = None,
    consumption_history: list[list[float]] | None = None,
    battery_capacity_kwh: float = 4.8,
    grid_available: bool = True,
    max_age_sec: float = 60.0,
) -> PlannerInputs:
    """Build a ``PlannerInputs`` from raw API + already-corrected values.

    Args:
        raw: dict from ``InverterApiClient.fetch_realtime_data()``.
        now: clock to stamp on the inputs (defaults to naive UTC now).
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

    # ── SOC ────────────────────────────────────────────────────────
    soc_value: float | None
    if corrected_soc is not None:
        soc_value = float(corrected_soc)
        soc_origin = "api"
    else:
        soc_value = raw.get("batterySoc")
        soc_origin = "api"
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
    tariff: list[float] = []
    if tariff_schedule and len(tariff_schedule) >= 24:
        for v in tariff_schedule[:24]:
            try:
                fv = float(v)
                if fv < 0 or fv > 50:
                    fv = 0.0
            except (TypeError, ValueError):
                fv = 0.0
            tariff.append(fv)

    # ── Consumption history ────────────────────────────────────────
    consumption: list[list[float]] = []
    if consumption_history:
        for day in consumption_history[-7:]:
            if not isinstance(day, list) or len(day) != 24:
                continue
            clean_day = _sanitize_hourly(day, 0.0, _POWER_MAX_W)
            consumption.append(clean_day)

    return PlannerInputs(
        now=now,
        soc=soc,
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
        hourly_radiation=hourly_rad_sanitized,
        hourly_weather_codes=hourly_weather,
        tariff_schedule=tariff,
        consumption_history=consumption,
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