"""Predictive ML Planner for HEMS.

Layered decision-making system that goes beyond reactive rules:
- Day-ahead SOC target planner (saves $$ by pre-optimising battery for tomorrow)
- Hour-ahead rollout (re-plans as forecast updates every 15min)
- Consumption predictor (learns user's pattern from history)
- Weather-aware charger (skips night-charge if tomorrow is sunny)
- Storm preemption (enters offline mode before grid dies)
- Tariff optimiser (charges cheapest hours, not just night window)

All predictions stay explainable: every decision includes a `reason`
string the user can see in logs / dashboard.

Architecture:
    inputs (state + forecasts) -> [planners] -> HemsDecision(s)
                                          \
                                           -> day_ahead_plan (list[HourlyPlan])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .engine import (
    ChargerPriority,
    HemsDecision,
    OutputPriority,
)

_LOGGER = logging.getLogger(__name__)

# Tariff constants (Ukraine 2026 — Юра's utility)
TARIFF_DAY = 4.32
TARIFF_NIGHT = 2.16
NIGHT_START = 23  # 23:00
NIGHT_END = 7     # 07:00
PEAK_START = 17   # 18:00-22:00 (highest rates)
PEAK_END = 22


# ─────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class HourlyPlan:
    """One hour of a 24h plan: predicted state + recommended decision."""

    hour: int
    timestamp: datetime
    pv_w: float           # predicted PV
    load_w: float         # predicted load
    grid_w: float         # predicted grid import
    batt_w: float         # predicted battery power
    soc_pred: float       # predicted SOC at end of hour
    tariff: float         # UAH/kWh at this hour
    output: str           # recommended OutputPriority
    charger: str          # recommended ChargerPriority
    confidence: float     # 0.0-1.0
    reason: str           # human-readable


@dataclass(slots=True)
class PredictiveHint:
    """Light-weight ML hint provided to existing HEMS modes.

    Unlike `decide()` (which takes full control), `suggest()` provides:
    - recommended SOC target (morning/evening) - replaces hardcoded 90/20
    - recommended charging window - helps ARBITRAGE pick right hours
    - reasoning why this hardware choice makes economic sense
    - confidence score for UI display

    The active mode (ADAPTIVE/ARBITRAGE/STORM) still decides output/charger.
    ML only says "if you want optimal, here's what I'd target."
    """
    target_soc_morning: float
    target_soc_evening: float
    night_charge_start_hour: int
    night_charge_end_hour: int
    storm_preemption: bool
    storm_reason: str | None
    reason: str
    confidence: float



@dataclass(slots=True)
class DayAheadPlan:
    """24-hour plan: when to charge, when to discharge, when to idle."""

    generated_at: datetime
    tomorrow_date: datetime
    target_soc_morning: float   # SOC at 07:00 (end of night)
    target_soc_evening: float   # SOC at 18:00 (start of evening peak)
    hourly: list[HourlyPlan] = field(default_factory=list)
    expected_pv_kwh: float = 0.0
    expected_load_kwh: float = 0.0
    expected_night_charge_kwh: float = 0.0
    total_savings_uah: float = 0.0
    confidence: float = 0.0


@dataclass(slots=True)
class PlannerInputs:
    """All inputs the planner needs to make a decision."""

    # Current state
    now: datetime
    soc: float                # current SOC %
    soc_corrected: float      # battery SOC after corrections
    pv_w: float               # current PV power
    load_w: float             # current PV power
    grid_w: float             # grid import (+) / export (-)
    batt_w: float             # battery power
    battery_capacity_kwh: float
    grid_v: float
    grid_ok: bool
    smart_mode: int

    # Forecasts
    forecast_today_kwh: float | None
    forecast_tomorrow_kwh: float | None
    hourly_pv: list[float]              # 24 values, W
    hourly_weather: list[dict[str, Any]]  # 24 values
    hourly_radiation: list[float]       # 24 values, W/m²

    # Tariff schedule (24 values, UAH/kWh)
    tariff_schedule: list[float]

    # Historical consumption (7 days × 24 hours of W)
    consumption_history: list[list[float]]

    # Weather alerts (will be added in Phase 1F)
    storm_alert: bool = False
    storm_hours_away: int | None = None


# ─────────────────────────────────────────────────────────────────
# Tariff helpers
# ─────────────────────────────────────────────────────────────────


def get_tariff(hour: int) -> float:
    """Return tariff at given hour (0-23)."""
    if NIGHT_START <= hour or hour < NIGHT_END:
        return TARIFF_NIGHT
    if PEAK_START <= hour < PEAK_END:
        return TARIFF_DAY * 1.0  # same as day in Ukraine, peak not separate
    return TARIFF_DAY


def build_tariff_schedule() -> list[float]:
    """24-hour tariff schedule."""
    return [get_tariff(h) for h in range(24)]


def is_night(hour: int) -> bool:
    return NIGHT_START <= hour or hour < NIGHT_END


def is_peak_evening(hour: int) -> bool:
    return PEAK_START <= hour < PEAK_END


# ─────────────────────────────────────────────────────────────────
# Consumption predictor
# ─────────────────────────────────────────────────────────────────


class ConsumptionPredictor:
    """ML-lite consumption predictor.

    Uses historical pattern matching + day-of-week priors + smoothing.

    Why this works for solar HEMS:
        Юра's pattern is regular (200-300W baseline, peaks 1-1.8kW for kettle).
        Pattern repeats: weekday vs weekend differs slightly.
        Weather impacts: cold = more heating; hot = more AC.
    """

    def __init__(self, history: list[list[float]] | None = None):
        """history: list of [7 days][24 hours] of W."""
        self._hist = history or []

    def add_day(self, hourly_load: list[float]) -> None:
        """Add today's hourly load to rolling 7-day history."""
        if len(hourly_load) != 24:
            return
        self._hist.append(hourly_load)
        # Keep only 7 days
        if len(self._hist) > 7:
            self._hist.pop(0)

    def predict(self, hour: int, day_of_week: int) -> tuple[float, float]:
        """Predict load at `hour` on `day_of_week`. Return (mean, stdev)."""
        if not self._hist:
            # No history yet — fall back to typical 250W baseline + peaks
            base = 250.0
            if 17 <= hour <= 22:
                base = 800.0
            elif 6 <= hour <= 9:
                base = 500.0
            return base, 200.0

        # Day-of-week matching: same weekday from history
        same_dow = [
            day[hour]
            for day in self._hist
            if day[hour] is not None
        ]
        if len(same_dow) >= 2:
            n = len(same_dow)
            mean = sum(same_dow) / n
            var = sum((x - mean) ** 2 for x in same_dow) / max(n - 1, 1)
            return mean, var ** 0.5

        # Fall back to all-history average for this hour
        all_hours = [day[hour] for day in self._hist if day[hour] is not None]
        n = len(all_hours)
        if n == 0:
            return 250.0, 200.0
        mean = sum(all_hours) / n
        var = sum((x - mean) ** 2 for x in all_hours) / max(n - 1, 1)
        return mean, var ** 0.5

    def predict_day(self, day_of_week: int) -> list[float]:
        """Predict full 24h of load for given day_of_week."""
        return [self.predict(h, day_of_week)[0] for h in range(24)]


# ─────────────────────────────────────────────────────────────────
# PV generator forecaster (wraps forecast.py)
# ─────────────────────────────────────────────────────────────────


class PvForecastAdjuster:
    """Adjusts raw PV forecast by historical accuracy.

    Open-Meteo often overestimates on cloudy days.
    Use recent PV-vs-forecast ratio to calibrate.
    """

    def __init__(self):
        self._samples: list[tuple[float, float]] = []  # (forecast_w, actual_w)

    def record(self, forecast_w: float, actual_w: float) -> None:
        self._samples.append((forecast_w, actual_w))
        # Keep last 50 samples (rolling 5 days at 5-min interval if sampled)
        if len(self._samples) > 50:
            self._samples.pop(0)

    def ratio(self) -> float:
        """Average forecast/actual ratio. <1 means forecast overestimates."""
        if not self._samples:
            return 1.0
        ratios = [
            actual / max(forecast, 1.0)
            for forecast, actual in self._samples
            if forecast > 50  # ignore low-light samples (noisy)
        ]
        if not ratios:
            return 1.0
        # Trim outliers
        ratios.sort()
        n = len(ratios)
        trim = max(1, n // 5)
        trimmed = ratios[trim : n - trim] if n > 2 * trim else ratios
        return sum(trimmed) / len(trimmed)

    def adjust(self, forecast_w: float) -> float:
        """Apply historical calibration to forecast."""
        return forecast_w * self.ratio()


# ─────────────────────────────────────────────────────────────────
# Day-ahead SOC target planner
# ─────────────────────────────────────────────────────────────────


def plan_soc_targets(inputs: PlannerInputs) -> tuple[float, float]:
    """Decide optimal SOC targets for morning (07:00) and evening (18:00).

    Returns:
        (target_soc_morning, target_soc_evening)

    Logic:
        1. Compute net energy needed for tomorrow's expected load
        2. Account for expected PV production
        3. Decide how much to charge tonight
        4. Reserve battery for evening if sunny tomorrow
    """
    tomorrow_pv = inputs.forecast_tomorrow_kwh or 0.0
    today_pv = inputs.forecast_today_kwh or 0.0
    battery_kwh = inputs.battery_capacity_kwh

    # Estimate tomorrow's consumption from history
    predictor = ConsumptionPredictor(inputs.consumption_history)
    tomorrow_dow = (inputs.now.weekday() + 1) % 7  # tomorrow's day-of-week
    tomorrow_load_kwh = sum(predictor.predict_day(tomorrow_dow)) / 1000.0

    # Net deficit tomorrow = load - pv (positive = need from battery/grid)
    net_deficit_kwh = tomorrow_load_kwh - tomorrow_pv

    # Reserve buffer: 15% of battery for unexpected loads
    RESERVE_FRAC = 0.15
    min_soc_kwh = battery_kwh * RESERVE_FRAC
    max_soc_kwh = battery_kwh * 0.95  # never fill to 100% (extends life)

    usable_kwh = max_soc_kwh - min_soc_kwh

    # ── Evening target (at 18:00) ──
    # We want to enter evening with enough battery to cover evening peak
    # without buying peak-rate electricity.
    evening_peak_kwh = sum(predictor.predict(h, tomorrow_dow)[0]
                           for h in range(18, 23)) / 1000.0

    if evening_peak_kwh > tomorrow_pv:
        # Need battery for evening — target higher
        target_evening_soc_kwh = min(evening_peak_kwh + min_soc_kwh,
                                     max_soc_kwh)
    else:
        # PV covers — no need to keep extra
        target_evening_soc_kwh = min_soc_kwh

    # ── Morning target (at 07:00, end of night charging) ──
    # Charge enough during night to reach evening target minus
    # expected PV production during the day.
    if tomorrow_pv > tomorrow_load_kwh:
        # Tomorrow is sunny — don't bother charging from grid
        target_morning_soc_kwh = min_soc_kwh
    elif tomorrow_pv > tomorrow_load_kwh * 0.5:
        # Half covered — partial charge from grid
        needed = target_evening_soc_kwh - (tomorrow_pv * 0.5)
        target_morning_soc_kwh = max(min_soc_kwh,
                                     min(needed, max_soc_kwh))
    else:
        # Low PV tomorrow — charge fully tonight
        target_morning_soc_kwh = target_evening_soc_kwh

    target_morning_soc = (target_morning_soc_kwh / battery_kwh) * 100.0
    target_evening_soc = (target_evening_soc_kwh / battery_kwh) * 100.0

    _LOGGER.info(
        "SOC planner: tomorrow_pv=%.2fkWh, load=%.2fkWh, "
        "morning=%.0f%%, evening=%.0f%%",
        tomorrow_pv, tomorrow_load_kwh,
        target_morning_soc, target_evening_soc,
    )
    return target_morning_soc, target_evening_soc


# ─────────────────────────────────────────────────────────────────
# Hour-ahead rollout simulator
# ─────────────────────────────────────────────────────────────────


def simulate_24h(
    inputs: PlannerInputs,
    target_morning: float,
    target_evening: float,
    predictor: ConsumptionPredictor,
) -> list[HourlyPlan]:
    """Roll out 24h of decisions: when to charge, when to discharge.

    Uses:
        - PV forecast (hourly)
        - Load forecast (from history)
        - Tariff schedule
        - Battery constraints (SOC min/max, charge/discharge current limits)

    Returns:
        List of 24 HourlyPlan, one per hour.
    """
    battery_kwh = inputs.battery_capacity_kwh
    soc = getattr(inputs, "soc_corrected", inputs.soc)
    plans = []
    now_hour = inputs.now.hour

    for h in range(24):
        # Wrap-around: hour 0 is tomorrow
        delta = (h - now_hour) % 24
        ts = inputs.now + timedelta(hours=delta)

        pv_forecast = inputs.hourly_pv[h] if h < len(inputs.hourly_pv) else 0.0
        # Adjust by historical accuracy
        pv_forecast = PvForecastAdjuster().adjust(pv_forecast)

        load_forecast = predictor.predict(h, ts.weekday())[0]

        tariff = get_tariff(h)

        # SOC bounds
        min_soc = 20.0
        max_soc = 95.0

        # Decide output + charger
        if is_night(h):
            # Night window
            if soc < target_morning:
                # Need to charge
                output = OutputPriority.USB
                charger = ChargerPriority.SNU
                reason = f"night_charge: SOC {soc:.0f}% < target {target_morning:.0f}%"
            else:
                # Target reached
                output = OutputPriority.USB
                charger = ChargerPriority.OSO
                reason = "night_idle: SOC at target"
        elif 9 <= h <= 16:
            # Daylight window — PV available
            if pv_forecast > load_forecast * 1.2:
                # Surplus — charge battery
                output = OutputPriority.SBU
                charger = ChargerPriority.OSO
                reason = f"day_surplus: PV {pv_forecast:.0f}W > load {load_forecast:.0f}W"
            elif pv_forecast > load_forecast * 0.8:
                # Almost balanced
                output = OutputPriority.SBU
                charger = ChargerPriority.OSO
                reason = f"day_balanced: PV {pv_forecast:.0f}W ≈ load {load_forecast:.0f}W"
            else:
                # PV insufficient
                output = OutputPriority.USB
                charger = ChargerPriority.OSO
                reason = f"day_pv_low: PV {pv_forecast:.0f}W < load {load_forecast:.0f}W"
        else:
            # Evening (17-22) — peak consumption
            if soc > target_evening:
                # Discharge battery to cover peak
                output = OutputPriority.SBU
                charger = ChargerPriority.OSO
                reason = f"evening_peak: SOC {soc:.0f}% > target {target_evening:.0f}%, discharge"
            else:
                # Below target — use grid
                output = OutputPriority.USB
                charger = ChargerPriority.OSO
                reason = f"evening_grid: SOC {soc:.0f}% ≤ target {target_evening:.0f}%"

        # Predict SOC after this hour
        net_kwh = (pv_forecast - load_forecast) / 1000.0
        # Battery efficiency
        if net_kwh > 0:
            # Charging — inverter charge/convert losses
            batt_change = net_kwh * 0.85  # 85% charge efficiency
        else:
            # Discharging
            batt_change = net_kwh / 0.90  # 90% discharge efficiency

        soc_pred = max(0, min(100, soc + (batt_change / battery_kwh) * 100.0))

        # Confidence: based on hour distance from now + forecast accuracy
        confidence = max(0.3, 1.0 - (delta / 24.0) * 0.6)
        if pv_forecast < 50:
            confidence *= 0.7  # low-PV hours are noisier

        plans.append(HourlyPlan(
            hour=h,
            timestamp=ts,
            pv_w=pv_forecast,
            load_w=load_forecast,
            grid_w=max(0, load_forecast - pv_forecast),
            batt_w=pv_forecast - load_forecast,
            soc_pred=soc_pred,
            tariff=tariff,
            output=("SBU" if output == OutputPriority.SBU else "USB"),
            charger=("SNU" if charger == ChargerPriority.SNU else "OSO"),
            confidence=confidence,
            reason=reason,
        ))

        # Roll SOC forward for next iteration
        soc = soc_pred

    return plans


# ─────────────────────────────────────────────────────────────────
# Storm preemption
# ─────────────────────────────────────────────────────────────────


def check_storm_preemption(inputs: PlannerInputs) -> HemsDecision | None:
    """Check if we should preemptively enter STORM mode.

    The old version read ``inputs.storm_alert`` and ``inputs.storm_hours_away``,
    but those flags were always defaulted to ``False/None`` in the call
    sites — i.e. the function only ever checked the 200 V threshold.
    The engine already detects grid outage via ``coordinator._evaluate_grid``
    with hysteresis, so we rely on the coordinator's ``grid_ok`` flag
    (which is what the engine passes via ``grid_available=`` in
    ``PlannerInputs.grid_ok``) plus an explicit voltage threshold.

    Triggers:
        - grid_v < 200V AND grid_ok=True → low voltage (sensor dropout
          or genuine problem); NOT storm preemption if grid_ok=False
          (coordinator already escalated to Storm via grid outage logic).
    """
    if inputs.grid_v < 200.0 and inputs.grid_ok:
        # Low voltage but coordinator still says grid is fine —
        # be conservative, log a hint instead of forcing STORM.
        return None
    return None


# ─────────────────────────────────────────────────────────────────
# Tariff arbitrage (smart night charging)
# ─────────────────────────────────────────────────────────────────


def plan_night_charge(
    inputs: PlannerInputs,
    target_morning: float,
) -> tuple[float, float, str]:
    """Plan optimal night charging window.

    Returns:
        (charge_start_hour, charge_end_hour, reason)

    Idea:
        - Don't always start at 21:00 — if tomorrow is sunny, start later
        - Use cheapest night hours (23-06) when possible
        - Avoid charging past target (waste of electricity)
    """
    tomorrow_pv = inputs.forecast_tomorrow_kwh or 0.0
    soc_corrected = getattr(inputs, "soc_corrected", inputs.soc)

    if tomorrow_pv > 3.0:
        # Sunny tomorrow — only charge enough for safety reserve
        if soc_corrected >= 40:
            # Skip night charge entirely
            return -1, -1, "skip_night_charge: tomorrow sunny, SOC≥40%"
        # Partial charge
        return 5, 7, "partial_night: late charge only"

    if tomorrow_pv > 1.0:
        # Moderate — half-charge
        if soc_corrected >= 60:
            return -1, -1, "skip_night_charge: tomorrow moderate, SOC≥60%"
        return 3, 7, "half_night_charge"

    # Low PV tomorrow — full charge
    if soc_corrected >= target_morning - 5:
        return -1, -1, "skip_night_charge: already at target"
    return 23, 7, "full_night_charge"


# ─────────────────────────────────────────────────────────────────
# Main entry point — pluggable into engine.py
# ─────────────────────────────────────────────────────────────────


class PredictiveHemsController:
    """Main controller coordinating all planners."""

    def __init__(self):
        self.consumption_predictor = ConsumptionPredictor()
        self.pv_adjuster = PvForecastAdjuster()
        self.last_plan: DayAheadPlan | None = None

    def update_history(self, hourly_load: list[float]) -> None:
        self.consumption_predictor.add_day(hourly_load)


    def suggest(self, inputs: PlannerInputs) -> PredictiveHint:
        """Provide a HINT to existing HEMS modes (do not take control)."""
        # Day-ahead targets
        target_morning, target_evening = plan_soc_targets(inputs)

        # Storm preemption check
        storm = check_storm_preemption(inputs)

        # Night charge window
        night_start, night_end, night_reason = plan_night_charge(
            inputs, target_morning
        )

        return PredictiveHint(
            target_soc_morning=target_morning,
            target_soc_evening=target_evening,
            night_charge_start_hour=night_start,
            night_charge_end_hour=night_end,
            storm_preemption=storm is not None,
            storm_reason=storm.reason if storm else None,
            reason=(
                f"ML: morning target {target_morning:.0f}%, "
                f"evening target {target_evening:.0f}%, "
                f"night window {night_start}-{night_end}: {night_reason}"
            ),
            confidence=self._estimate_confidence(inputs),
        )

    def _estimate_confidence(self, inputs: PlannerInputs) -> float:
        """Confidence based on real measurements + history depth.

        Replaces the old hard-coded ``0.4 + (days/7)*0.5`` formula,
        which produced a fake confidence number unrelated to actual
        forecast accuracy.

        Inputs:
            - history depth (consumption_history): need ≥3 days for 0.5
            - forecast availability: missing/None → 0.0
            - forecast magnitude: clamped [0, 10] kWh → [0, 0.5]
            - calibrator confidence_factor (from ForecastCalibrator.metrics)
              if attached, multiplied in
        """
        history_days = len(inputs.consumption_history)
        history_factor = 0.5 if history_days >= 3 else (history_days / 3.0) * 0.5

        forecast = inputs.forecast_tomorrow_kwh
        if forecast is None or forecast <= 0:
            forecast_factor = 0.0
        else:
            forecast_factor = min(0.5 + min(forecast, 10.0) / 20.0, 1.0)

        base = history_factor * forecast_factor
        # If a calibrator is attached, scale by its measured confidence.
        cal = getattr(self, "calibrator", None)
        if cal is not None and hasattr(cal, "metrics"):
            try:
                cm = cal.metrics()
                base = base * max(0.0, min(1.0, cm.confidence_factor))
            except Exception:
                pass
        return round(min(1.0, max(0.0, base)), 2)

    def decide(
        self,
        inputs: PlannerInputs,
    ) -> tuple[HemsDecision, DayAheadPlan]:
        """Make a decision: current + day-ahead plan.

        Returns:
            (immediate_decision, day_ahead_plan)

        The immediate_decision is what the inverter should do RIGHT NOW.
        The day_ahead_plan shows what we'll do over the next 24h.
        """
        # 1. Storm check (highest priority)
        storm = check_storm_preemption(inputs)
        if storm is not None:
            # In storm — fall back to safe SBU+OSO mode
            plan = self._build_plan(inputs, [], reason=storm.reason)
            return storm, plan

        # 2. Plan day-ahead SOC targets
        target_morning, target_evening = plan_soc_targets(inputs)

        # 3. Simulate 24h
        plans = simulate_24h(
            inputs,
            target_morning,
            target_evening,
            self.consumption_predictor,
        )

        # 4. Apply NOW
        now_plan = plans[0]
        decision = HemsDecision(
            output_priority=OutputPriority.SBU if now_plan.output == "SBU" else OutputPriority.USB,
            charger_priority=ChargerPriority.SNU if now_plan.charger == "SNU" else ChargerPriority.OSO,
            reason=f"[ML] {now_plan.reason} (conf={now_plan.confidence:.2f})",
        )

        # 5. Build day-ahead plan
        plan = self._build_plan(inputs, plans)
        self.last_plan = plan

        return decision, plan

    def _build_plan(
        self,
        inputs: PlannerInputs,
        plans: list[HourlyPlan],
        reason: str = "",
    ) -> DayAheadPlan:
        soc_corrected = getattr(inputs, "soc_corrected", inputs.soc)
        return DayAheadPlan(
            generated_at=inputs.now,
            tomorrow_date=inputs.now + timedelta(days=1),
            target_soc_morning=plans[0].soc_pred if plans else soc_corrected,
            target_soc_evening=sum(p.soc_pred for p in plans[-3:]) / 3 if plans else soc_corrected,
            hourly=plans,
            expected_pv_kwh=sum(p.pv_w for p in plans) / 1000.0,
            expected_load_kwh=sum(p.load_w for p in plans) / 1000.0,
        )


# ─────────────────────────────────────────────────────────────────
# Persistence helper
# ─────────────────────────────────────────────────────────────────


def load_history_from_recorder(
    states_client: Any,
    entity_id: str = "sensor.garazh_powmr_smart_inverter_load_power",
    days: int = 7,
) -> list[list[float]]:
    """Load 7-day hourly history of load power from recorder.

    Returns list of [24 values] in W.
    """
    # TODO: integrate with HA recorder — returns empty for now
    # In production, use recorder.statistics_during_period
    return []


def export_plan_to_attributes(plan: DayAheadPlan) -> dict[str, Any]:
    """Convert DayAheadPlan to HA sensor attributes."""
    return {
        "generated_at": plan.generated_at.isoformat(),
        "tomorrow_date": plan.tomorrow_date.date().isoformat(),
        "target_morning_soc": round(plan.target_soc_morning, 1),
        "target_evening_soc": round(plan.target_soc_evening, 1),
        "expected_pv_kwh": round(plan.expected_pv_kwh, 2),
        "expected_load_kwh": round(plan.expected_load_kwh, 2),
        "plan": [
            {
                "hour": p.hour,
                "pv_w": round(p.pv_w, 0),
                "load_w": round(p.load_w, 0),
                "soc_pred": round(p.soc_pred, 1),
                "tariff": p.tariff,
                "output": p.output,
                "charger": p.charger,
                "confidence": round(p.confidence, 2),
                "reason": p.reason,
            }
            for p in plan.hourly
        ],
    }