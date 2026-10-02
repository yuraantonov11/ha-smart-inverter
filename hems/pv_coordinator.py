"""Async recorder/archive orchestration, owned by the existing coordinator.

No separate evaluator or control writes. Synchronous disk/recorder work stays
in executor jobs. Kept as a mixin so real wiring can be tested without HA.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .history_builder import build_hourly_load_matrix
from .predictive import PredictiveHemsController, normalize_night_window
from .pv_learning import (PvLearningState, complete_hourly_days, daily_energy_deltas,
                          day_bounds, timestamp, train_station)

_LOGGER = logging.getLogger("custom_components.powmr_inverter.coordinator")


class PvLearningCoordinatorMixin:
    _load_matrix_days: int = 30
    _pv_matrix_days: int = 30

    def _init_pv_learning(self):
        self._site_timezone = ZoneInfo(self.hass.config.time_zone)
        self._pv_learning = PvLearningState(
            self.hass.config.time_zone,
            float(self._entry.options.get("site_latitude", 50.45)),
            float(self._entry.options.get("site_longitude", 30.52)),
        )
        self._pv_calibrator = self._pv_learning.calibrator
        self._pv_matrix: list[list[float]] = []
        self._pv_actual: dict[str, float] = {}
        self._pv_matrix_at = None
        self._pv_calibrator_log_at = None
        self._archive_attempt_at = None
        self._pv_state_loaded = False
        self._pv_state_dirty = False
        self._night_recommendation_at = None
        directory = Path(__file__).resolve().parent
        self._pv_state_path = directory / f"pv_fact_pairs_{self._entry.entry_id}.json"
        self._pv_legacy_path = directory / "pv_fact_pairs.json"
        self.forecast_learned_ratio = .12
        controller = PredictiveHemsController()
        controller.calibrator = self._pv_calibrator
        self._hems._predictive_controller = controller
        self._configure_night_window()

    def _pv_local_now(self):
        return datetime.now(timezone.utc).astimezone(self._site_timezone)

    async def _ensure_pv_state_loaded(self):
        if self._pv_state_loaded:
            return
        self._pv_state_loaded = True
        try:
            await self.hass.async_add_executor_job(
                self._pv_learning.load, self._pv_state_path, self._pv_legacy_path)
            if self._pv_learning.model:
                self.forecast_learned_ratio = self._pv_learning.model["gain"]
        except Exception as exc:
            _LOGGER.warning("PV learning restore skipped: %s", exc)

    async def _save_pv_state(self):
        if not self._pv_state_dirty:
            return
        try:
            await self.hass.async_add_executor_job(self._pv_learning.save, self._pv_state_path)
            self._pv_state_dirty = False
        except Exception as exc:
            # Retry next refresh; never silently discard a completed day's pair.
            _LOGGER.warning("PV learning persist failed: %s", exc)

    def _history_entity(self, key, fallback):
        """Find this device's renamed sensor without changing its public API."""
        from homeassistant.helpers import entity_registry as er
        registry = er.async_get(self.hass)
        entity = registry.async_get_entity_id("sensor", "powmr_inverter", f"{self.api.device_sn}_{key}")
        if entity:
            return entity
        legacy = registry.async_get(fallback)
        if legacy is not None and legacy.config_entry_id == self._entry.entry_id:
            return fallback
        raise LookupError(f"No {key} history sensor for this inverter")

    async def _maybe_refresh_pv_history(self, now):
        if self._pv_matrix_at and now - self._pv_matrix_at < timedelta(hours=1):
            return
        self._pv_matrix_at = now  # also throttle errors, not five-minute retries
        await self._ensure_pv_state_loaded()
        try:
            from homeassistant.components.recorder import statistics as rec_stats
            local_now = self._pv_local_now()
            start, _ = day_bounds(local_now.date() - timedelta(days=120), self._site_timezone)
            start -= timedelta(hours=1)  # cumulative energy at initial midnight
            ent = self._history_entity("pv_power", "sensor.garazh_smart_solar_inverter_pv_power")
            stats = await self.hass.async_add_executor_job(
                rec_stats.statistics_during_period,
                self.hass, start, None, {ent}, "hour", None, {"mean"})
            rows = stats.get(ent, [])
            samples = []
            for row in rows:
                try:
                    samples.append((timestamp(row.get("start")).astimezone(self._site_timezone)
                                    .replace(tzinfo=None), row.get("mean")))
                except (ValueError, TypeError, OverflowError, OSError):
                    continue
            self._pv_matrix = build_hourly_load_matrix(samples, local_now, days=self._pv_matrix_days)
            # Model training/calibration never consumes gap-filled matrix values.
            actual = complete_hourly_days(rows, self._site_timezone, local_now.date())
            energy_ent = self._history_entity("daily_energy_api", "sensor.garazh_smart_solar_inverter_daily_pv_energy")
            try:
                energy = await self.hass.async_add_executor_job(
                    rec_stats.statistics_during_period,
                    self.hass, start, None, {energy_ent}, "hour", None, {"sum"})
                metadata = await self.hass.async_add_executor_job(
                    rec_stats.get_metadata, self.hass, {energy_ent})
                raw_meta = metadata.get(energy_ent)
                meta = raw_meta[1] if isinstance(raw_meta, tuple) else raw_meta
                unit = meta.get("unit_of_measurement") if isinstance(meta, dict) and meta.get("has_sum") else None
                actual.update(daily_energy_deltas(energy.get(energy_ent, []), unit,
                                                 self._site_timezone, local_now.date()))
            except Exception as exc:
                _LOGGER.debug("PV energy statistics unavailable; complete power-day fallback: %s", exc)
            cutoff = (local_now.date() - timedelta(days=120)).isoformat()
            self._pv_actual = {day: value for day, value in actual.items() if day >= cutoff}
            _LOGGER.debug("PV history refreshed: %d matrix days, %d complete energy days",
                          len(self._pv_matrix), len(self._pv_actual))
        except Exception as exc:
            _LOGGER.warning("PV history refresh failed: %s", exc)
            return
        await self._maybe_train_pv_station(now)
        await self._maybe_record_pv_pairs(now)

    async def _maybe_train_pv_station(self, now):
        if self._forecast is None or not self._pv_actual:
            return
        local_now = self._pv_local_now()
        state = self._pv_learning
        # Archive may lag realtime by two days. Preserve cached/newer values.
        end = local_now.date() - timedelta(days=2)
        start = local_now.date() - timedelta(days=120)
        if state.archive_checked_day != local_now.date().isoformat():
            if self._archive_attempt_at and now - self._archive_attempt_at < timedelta(hours=1):
                return
            self._archive_attempt_at = now
            try:
                # Fetch missing tail, retaining initial cached boundary hour.
                fetch_start = start
                if state.radiation:
                    cached_end = datetime.fromisoformat(max(state.radiation)).date()
                    fetch_start = max(start, cached_end - timedelta(days=1))
                radiation = await self._forecast.get_archive_radiation(fetch_start, end)
                if not radiation:
                    raise ValueError("No complete archive radiation days returned")
                state.radiation.update(radiation)
                state.radiation = {d: r for d, r in state.radiation.items() if d >= start.isoformat()}
                state.archive_checked_day = local_now.date().isoformat()
                self._pv_state_dirty = True
            except Exception as exc:
                _LOGGER.warning("PV archive unavailable; previous station model retained: %s", exc)
                return
        model = train_station(self._pv_actual, state.radiation)
        if model is None:
            _LOGGER.debug("PV station training needs seven complete radiation/energy pairs")
            return
        if model != state.model:
            state.model = model
            self._pv_state_dirty = True
            self.forecast_learned_ratio = model["gain"]
            if self._forecast.set_station_gain(model["gain"]):
                self._forecast_last_fetch = None
                # Immediately give sensors and planner the same trained powers.
                await self._maybe_refresh_forecast(now)
            _LOGGER.info("PV station trained: days=%d gain=%.4f archive_holdout=%s",
                         model["sample_count"], model["gain"], model["validation"])

    async def _maybe_record_pv_pairs(self, now):
        await self._ensure_pv_state_loaded()
        if self._pv_learning.match(self._pv_actual, self._pv_local_now()):
            self._pv_state_dirty = True
        await self._save_pv_state()

    def _log_pv_calibrator_state(self, now):
        if self._pv_calibrator_log_at and now - self._pv_calibrator_log_at < timedelta(hours=1):
            return
        self._pv_calibrator_log_at = now
        m = self._pv_calibrator.metrics()
        _LOGGER.info("PV calibrator updated: n=%d bias=%.3f kWh mae=%.3f conf=%.2f",
                     m.sample_count, m.bias_w, m.mae_w, m.confidence_factor)
        self.check_assist_ready()

    def check_assist_ready(self):
        m = self._pv_calibrator.metrics()
        ready = m.sample_count >= 3 and m.confidence_factor > .2
        _LOGGER.info("%s: samples=%d confidence=%.2f",
                     "Assist-ready" if ready else "Auto-assist blocked", m.sample_count, m.confidence_factor)
        return ready

    def _configure_night_window(self):
        window = normalize_night_window((self._entry.options.get("night_charge_start_hour", 23),
                                         self._entry.options.get("night_charge_end_hour", 7)))
        self.night_charge_start_hour, self.night_charge_end_hour = window
        self._hems._predictive_controller.night_charge_window = window

    def _persist_night_recommendation(self, now):
        hint = getattr(self._hems, "_last_predictive_hint", None)
        plan = getattr(self._hems, "_last_predictive_plan", None)
        if hint is None or plan is None or getattr(plan, "generated_at", None) != now:
            return
        value = {"start_hour": hint.night_charge_start_hour, "end_hour": hint.night_charge_end_hour}
        if self._entry.options.get("night_charge_window_recommended") == value:
            return
        if self._night_recommendation_at and now - self._night_recommendation_at < timedelta(hours=1):
            return
        options = dict(self._entry.options)
        options["night_charge_window_recommended"] = value
        self.hass.config_entries.async_update_entry(self._entry, options=options)
        self._night_recommendation_at = now

    async def _maybe_refresh_forecast(self, now):
        from .forecast import ForecastService
        await self._ensure_pv_state_loaded()
        if self._forecast is None:
            state = self._pv_learning
            self._forecast = ForecastService(latitude=state.identity["latitude"],
                longitude=state.identity["longitude"], timezone_name=state.identity["timezone"])
            self._forecast.set_station_gain(self.forecast_learned_ratio)
        if self._forecast_last_fetch and now - self._forecast_last_fetch < timedelta(minutes=15):
            return
        # Throttle failures too; missing forecasts are marked unknown below.
        self._forecast_last_fetch = now
        try:
            local_now = self._pv_local_now()
            daily = await self._forecast.get_daily_forecasts(days=3)
            hourly = await self._forecast.get_hourly_forecast()
            complete = {}
            for offset in range(3):
                day = (local_now.date() + timedelta(days=offset)).isoformat()
                start, end = day_bounds(day, self._site_timezone)
                hours = [h for h in hourly if h["time"].startswith(day)]
                expected = {int((start+timedelta(hours=h)).timestamp())
                            for h in range(int((end-start).total_seconds()/3600))}
                if {h.get("timestamp") for h in hours} == expected and day in daily:
                    complete[day] = daily[day]
                    if self._pv_learning.snapshot(day, daily[day].energy_kwh, local_now):
                        self._pv_state_dirty = True
            tomorrow, after = [(local_now.date()+timedelta(days=d)).isoformat() for d in (1, 2)]
            fc, fc2 = complete.get(tomorrow), complete.get(after)
            self.forecast_tomorrow_kwh = fc.energy_kwh if fc else None
            self.forecast_day_after_kwh = fc2.energy_kwh if fc2 else None
            self.weather_tomorrow_code = fc.dominant_weather_code if fc else None
            self.weather_day_after_code = fc2.dominant_weather_code if fc2 else None
            today = local_now.date().isoformat()
            self._forecast_today_kwh = complete[today].energy_kwh if today in complete else None
            self.hourly_forecast_today, self.hourly_weather_today, self.hourly_radiation_today = [], [], []
            if today in complete:
                hours = [h for h in hourly if h["time"].startswith(today)]
                for hour in range(24):
                    bucket = [h for h in hours if int(h["time"][11:13]) == hour]
                    self.hourly_forecast_today.append(sum(h["power_w"] for h in bucket)/max(1,len(bucket)))
                    self.hourly_radiation_today.append(sum(h["radiation_wm2"] for h in bucket)/max(1,len(bucket)))
                    self.hourly_weather_today.append(bucket[0].get("weather_code") if bucket else None)
            await self._save_pv_state()
        except Exception as exc:
            # A previous day's chart must not masquerade as today's forecast.
            self.forecast_tomorrow_kwh = None
            self.forecast_day_after_kwh = None
            self._forecast_today_kwh = None
            self.hourly_forecast_today = []
            self.hourly_weather_today = []
            self.hourly_radiation_today = []
            _LOGGER.warning("Forecast fetch failed: %s", exc)
