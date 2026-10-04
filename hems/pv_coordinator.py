"""Async recorder/archive orchestration, owned by the existing coordinator.

No separate evaluator or control writes. Synchronous disk/recorder work stays
in executor jobs. Kept as a mixin so real wiring can be tested without HA.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from functools import partial
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .history_builder import build_hourly_load_matrix
from .predictive import PredictiveHemsController, normalize_night_window
from .options_helpers import compute_site_coordinates
from .pv_learning import (PvLearningState, RealForecastPairs, finite, complete_hourly_days, daily_energy_deltas,
                          day_bounds, timestamp, train_station)

_LOGGER = logging.getLogger("custom_components.powmr_inverter.coordinator")


class PvLearningCoordinatorMixin:
    _load_matrix_days: int = 30
    _pv_matrix_days: int = 30

    def _init_pv_learning(self):
        self._site_timezone = ZoneInfo(self.hass.config.time_zone)
        # T16 audit: the runtime default
        # comes from ``options_helpers.compute_site_coordinates``,
        # which falls back to the canonical
        # ``DEFAULT_SITE_LATITUDE`` /
        # ``DEFAULT_SITE_LONGITUDE`` in
        # ``hems.defaults``. ``entry.options``
        # always wins when the user has set a
        # value.
        latitude, longitude = compute_site_coordinates(
            self._entry.options
        )
        self._pv_learning = PvLearningState(
            self.hass.config.time_zone,
            latitude,
            longitude,
        )
        self._pv_calibrator = self._pv_learning.calibrator
        self._pv_matrix: list[list[float]] = []
        self._pv_actual: dict[str, float] = {}
        self._cloud_pv_actual = {}
        self._cloud_history_attempt_at = None
        self._pv_matrix_at = None
        self._pv_calibrator_log_at = None
        self._archive_attempt_at = None
        self._pv_state_loaded = False
        self._pv_state_dirty = False
        self._night_window_last_persist_at = None
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
        await self._maybe_refresh_cloud_pv_history(now)
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
            self._hourly_pv_rows = rows
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
                    partial(rec_stats.get_metadata, statistic_ids={energy_ent}), self.hass)
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
        # Prefer independently covered recorder days; cloud fills gaps only.
        for day, value in getattr(self, "_cloud_pv_actual", {}).items():
            self._pv_actual.setdefault(day, value)
        await self._maybe_train_pv_station(now)
        await self._maybe_train_hourly_pv(now)
        self._schedule_cloud_hourly_history(now)
        await self._maybe_record_pv_pairs(now)

    def _schedule_cloud_hourly_history(self, now):
        """Backfill outside the control update; respect API's shared rate limit."""
        if not hasattr(getattr(self, "api", None), "fetch_hourly_pv_history_day"):
            return
        task = getattr(self, "_cloud_hourly_task", None)
        if task is not None and not task.done():
            return
        last = getattr(self, "_cloud_hourly_attempt_at", None)
        if last is not None and now - last < timedelta(hours=24):
            return
        self._cloud_hourly_attempt_at = now
        self._cloud_hourly_task = self.hass.async_create_task(self._refresh_cloud_hourly_history(now))
        self._entry.async_on_unload(self._cloud_hourly_task.cancel)

    async def _refresh_cloud_hourly_history(self, now):
        from .cloud_history import CloudHourlyHistory
        try:
            local_now = self._pv_local_now()
            cache = getattr(self, "_cloud_hourly_cache", None)
            path = self._pv_state_path.parent / f"cloud_hourly_{self._entry.entry_id}.json"
            if cache is None:
                cache = CloudHourlyHistory(self._pv_learning.identity)
                await self.hass.async_add_executor_job(cache.load, path)
                self._cloud_hourly_cache = cache
            start = local_now.date() - timedelta(days=14)
            cache.days = {d: rows for d, rows in cache.days.items()
                          if start.isoformat() <= d < local_now.date().isoformat()}
            # Newest days first; historical requests use the verified daily API.
            for offset in range(1, 15):
                day = local_now.date() - timedelta(days=offset)
                if (day.isoformat() in cache.days
                        and all("samples" in row for row in cache.days[day.isoformat()])):
                    continue
                rows = await self.api.fetch_hourly_pv_history_day(day, self.hass.config.time_zone)
                if day.isoformat() in complete_hourly_days(rows, self._site_timezone, local_now.date()):
                    cache.days[day.isoformat()] = rows
            await self.hass.async_add_executor_job(cache.save, path)
            self._hourly_pv_attempt_at = None
            await self._maybe_train_hourly_pv(now)
            _LOGGER.info("Cloud hourly PV history imported: days=%d hours=%d source=real_half_hour_samples",
                         len(cache.days), sum(len(rows) for rows in cache.days.values()))
        except Exception as exc:
            _LOGGER.warning("Cloud hourly PV history unavailable; retaining measured recorder history: %s", exc)

    async def _maybe_train_hourly_pv(self, now):
        from .pv_hourly import train_hourly_response, validate_hourly_response
        rows = getattr(self, "_hourly_pv_rows", [])
        cache = getattr(self, "_cloud_hourly_cache", None)
        if cache and cache.days:
            cloud_days = set(cache.days)
            recorder_rows = []
            for row in rows:
                try:
                    if timestamp(row.get("start")).astimezone(self._site_timezone).date().isoformat() not in cloud_days:
                        recorder_rows.append(row)
                except (ValueError, TypeError, OverflowError, OSError):
                    continue
            rows = recorder_rows
            rows += [r for day_rows in cache.days.values() for r in day_rows]
        if not rows or self._forecast is None:
            return
        last = getattr(self, "_hourly_pv_attempt_at", None)
        if last is not None and now - last < timedelta(hours=24):
            return
        self._hourly_pv_attempt_at = now
        local_now = self._pv_local_now()
        try:
            archive = await self._forecast.get_archive_hourly_radiation(
                local_now.date() - timedelta(days=14), local_now.date() - timedelta(days=1))
            model = train_hourly_response(rows, archive, self._site_timezone, local_now.date())
            if model:
                model["validation"] = validate_hourly_response(rows, archive, self._site_timezone,
                                                                 local_now.date())
            if self._forecast.set_hourly_response(model):
                self._forecast_last_fetch = None
                await self._maybe_refresh_forecast(now)
            if model:
                _LOGGER.info("PV hourly response trained: days=%d provisional=%s rejected=%s gains=%s",
                             model["sample_days"], model["provisional"], model["rejected_days"],
                             [round(g, 3) if g is not None else None for g in model["gains"]])
                if model["validation"]:
                    _LOGGER.info("PV hourly validation: %s", model["validation"])
        except Exception as exc:
            _LOGGER.warning("PV hourly response unavailable; retaining daily gain: %s", exc)

    async def _maybe_refresh_cloud_pv_history(self, now):
        fetch = getattr(getattr(self, "api", None), "fetch_daily_pv_history", None)
        if fetch is None:
            return
        last = getattr(self, "_cloud_history_attempt_at", None)
        if last is not None and now - last < timedelta(days=1):
            return
        self._cloud_history_attempt_at = now  # also throttle empty/error replies
        local_now = self._pv_local_now()
        start = local_now.date() - timedelta(days=120)
        try:
            facts = await fetch(start, local_now.date() - timedelta(days=1))
            if facts:
                self._cloud_pv_actual = facts
                _LOGGER.info("Cloud PV history imported: %d measured days; forecast samples unchanged", len(facts))
        except Exception as exc:
            _LOGGER.warning("Cloud PV history unavailable; retaining recorder/model: %s", exc)

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
        await self._save_real_forecast_pair(now)
        await self._save_pv_state()

    async def _save_real_forecast_pair(self, now):
        """Persist issued forecasts and completed facts, then publish evidence.

        Missing midnight statistics are retried at the next history refresh.
        A failed disk write cannot acknowledge a pair or consume its retry.
        """
        await self._ensure_pv_state_loaded()
        local_now = self._pv_local_now()
        state = self._pv_learning
        if not hasattr(self, "_real_pairs_store"):
            store = RealForecastPairs(state.identity)
            entry = getattr(self, "_entry", None)
            entry_id = entry.entry_id if entry else self._pv_state_path.stem
            self._real_pairs_path = self._pv_state_path.parent / entry_id / "real_forecast_pairs.json"
            try:
                loaded = await self.hass.async_add_executor_job(store.load, self._real_pairs_path)
                if not loaded:
                    store.migrate(state)
                store.prune(local_now.date())
            except Exception as exc:
                _LOGGER.warning("Real forecast journal restore failed: %s", exc)
                return  # do not overwrite a corrupt/unrelated journal
            self._real_pairs_store = store
            self._real_pairs_dirty = True
            self._real_pair_signature = None
        store = self._real_pairs_store
        # Snapshot values come from the raw station forecast, before bias adjustment.
        raw = getattr(self, "_raw_forecast_kwh", {})
        forecasts = tuple(raw.get((local_now.date()+timedelta(days=d)).isoformat(),
                              getattr(self, attr, None) if not raw else None)
                          for d, attr in ((1, "forecast_tomorrow_kwh"), (2, "forecast_day_after_kwh")))
        signature = (local_now.date(), self._pv_matrix_at, forecasts, tuple(state.snapshots), state.calibration_model)
        if self._real_pair_signature == signature and not self._real_pairs_dirty:
            return
        previous = deepcopy(store.pairs)
        store.migrate(state)
        for offset, value in enumerate(forecasts, 1):
            day = (local_now.date()+timedelta(days=offset)).isoformat()
            store.snapshot(day, value, local_now, forecast_model=state.calibration_model)
        actual = dict(self._pv_actual)
        pending = [d for d, p in store.pairs.items() if not p["used"] and d < local_now.date().isoformat()]
        if pending:
            # HA daily sum is cumulative and its buckets use UTC days. The
            # complete hourly facts are the coverage check and the local/DST
            # fallback; never turn a bare cumulative sum into a day's energy.
            try:
                from homeassistant.components.recorder import statistics as rec_stats
                ent = self._history_entity("daily_energy_api", "sensor.garazh_smart_solar_inverter_daily_pv_energy")
                start, _ = day_bounds(min(pending), self._site_timezone)
                _, end = day_bounds(max(pending), self._site_timezone)
                stats = await self.hass.async_add_executor_job(
                    rec_stats.statistics_during_period, self.hass, start-timedelta(days=1), end,
                    {ent}, "day", None, {"sum"})
                metadata = await self.hass.async_add_executor_job(
                    partial(rec_stats.get_metadata, statistic_ids={ent}), self.hass)
                raw_meta = metadata.get(ent)
                meta = raw_meta[1] if isinstance(raw_meta, tuple) else raw_meta
                scale = {"Wh": .001, "kWh": 1., "MWh": 1000.}.get(
                    meta.get("unit_of_measurement") if isinstance(meta, dict) and meta.get("has_sum") else None)
                endpoints = {}
                if scale is not None:
                    for row in stats.get(ent, []):
                        value = finite(row.get("sum"))
                        if value is not None:
                            endpoints[timestamp(row["start"])+timedelta(days=1)] = value*scale
                    for day in pending:
                        first, last = day_bounds(day, self._site_timezone)
                        if first in endpoints and last in endpoints and day in actual:
                            delta = finite(endpoints[last]-endpoints[first], high=500)
                            if delta is not None and abs(delta-actual[day]) < .001:
                                actual[day] = delta
            except Exception as exc:
                _LOGGER.debug("Daily PV sums unavailable; complete local-day facts retained: %s", exc)
        captured = store.match(actual, local_now)
        store.prune(local_now.date())
        changed = store.pairs != previous or self._real_pairs_dirty
        if changed:
            try:
                await self.hass.async_add_executor_job(store.save, self._real_pairs_path)
            except Exception as exc:
                store.pairs = previous
                self._real_pairs_dirty = True
                _LOGGER.warning("Real forecast journal persist failed: %s", exc)
                return
            store.publish(state)
            self._pv_state_dirty = True
            self._real_pairs_dirty = False
            for pair in captured:
                _LOGGER.info("Real pair captured: date=%s fc=%.3fkWh ac=%.3fkWh delta=%+.3fkWh",
                             pair["date"], pair["forecast_kwh"], pair["actual_kwh"],
                             pair["actual_kwh"]-pair["forecast_kwh"])
        self._last_real_pair_date = local_now.date()
        self._real_pair_signature = signature
        self._adjust_daily_forecasts()

    def _adjust_daily_forecasts(self):
        """Correct fresh raw daily forecasts in the calibrator's kWh unit."""
        raw = getattr(self, "_raw_forecast_kwh", None)
        if raw is None:
            return
        m = self._pv_calibrator.metrics()
        for offset, attr in ((0, "_forecast_today_kwh"), (1, "forecast_tomorrow_kwh"), (2, "forecast_day_after_kwh")):
            day = (self._pv_local_now().date()+timedelta(days=offset)).isoformat()
            if offset == 0 and day not in raw:
                continue
            before = finite(raw.get(day), high=500)
            if before is None:
                setattr(self, attr, None)
                continue
            state = getattr(self, "_pv_learning", None)
            compatible = (state is None or state.calibration_model is None
                          or self._forecast_model_for_day(datetime.fromisoformat(day).date()) == state.calibration_model)
            after = (self._pv_calibrator.adjust(before)
                     if compatible and abs(m.bias_w) > .1 * before else before)
            powers = [h["power_w"] for h in getattr(self, "_raw_hourly_forecast", []) if h["time"][:10] == day]
            if powers and max(powers) > 0:
                after = min(after, sum(powers)/1000 * 20000/max(powers))
            if after != before and getattr(self, attr, None) != after:
                _LOGGER.info("Forecast adjusted: bias=%+.3f kWh fc_before=%.3f fc_after=%.3f mae=%.3f",
                             m.bias_w, before, after, m.mae_w)
            setattr(self, attr, after)
        self._publish_calibrated_hours()

    def _publish_calibrated_hours(self):
        """Distribute daily bias over its issued shape; never add kWh to W."""
        rows = getattr(self, "_raw_hourly_forecast", None)
        if rows is None:
            return
        today = self._pv_local_now().date()
        totals = {(today+timedelta(days=d)).isoformat(): getattr(self, attr, None)
                  for d, attr in ((0, "_forecast_today_kwh"), (1, "forecast_tomorrow_kwh"), (2, "forecast_day_after_kwh"))}
        dated = {}
        buckets = [[] for _ in range(24)]
        raw_energy = {}
        for row in rows:
            day = row["time"][:10]
            raw_energy[day] = raw_energy.get(day, 0.) + row["power_w"]/1000
        for row in rows:
            day = row["time"][:10]
            before = finite(self._raw_forecast_kwh.get(day), high=500)
            after = finite(totals.get(day), high=500)
            if before is None or after is None:
                continue
            energy = raw_energy.get(day, 0.)
            factor = after/energy if energy > 0 else 1.
            power = round(row["power_w"]*factor, 6)
            dated[row["timestamp"]] = power
            if day == today.isoformat():
                buckets[int(row["time"][11:13])].append(power)
        self._dated_hourly_pv_forecast = dated
        # A complete spring-DST day has 23 actual hours. The legacy 24-slot
        # chart may contain an unused slot; the planner uses only dated instants.
        self.hourly_forecast_today = [sum(b)/len(b) if b else 0. for b in buckets] if today.isoformat() in raw_energy else []

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
        options = {**getattr(self._entry, "data", {}), **self._entry.options}
        window = normalize_night_window((options.get("predictive_night_window_start_hour", options.get("night_charge_start_hour", 23)),
                                         options.get("predictive_night_window_end_hour", options.get("night_charge_end_hour", 7))))
        self.night_charge_start_hour, self.night_charge_end_hour = window
        self._hems._predictive_controller.night_charge_window = window

    def _persist_night_recommendation(self, now):
        hint = getattr(self._hems, "_last_predictive_hint", None)
        plan = getattr(self._hems, "_last_predictive_plan", None)
        expected = getattr(self._hems, "_planner_forecast_now", now)
        if hint is None or plan is None or getattr(plan, "generated_at", None) != expected:
            return
        value = {"start_hour": hint.night_charge_start_hour, "end_hour": hint.night_charge_end_hour}
        if any(type(h) is not int or not 0 <= h <= 23 for h in value.values()) or value["start_hour"] == value["end_hour"]:
            return
        if self._entry.options.get("night_charge_window_recommended") == value:
            return
        last = getattr(self, "_night_window_last_persist_at", None)
        if last and now - last < timedelta(hours=1):
            return
        options = dict(self._entry.options)
        options["night_charge_window_recommended"] = value
        try:
            self.hass.config_entries.async_update_entry(self._entry, options=options)
        except Exception as exc:
            _LOGGER.warning("Night recommendation persist failed: %s", exc)
            return
        self._night_window_last_persist_at = now
        _LOGGER.info("night_charge_window_recommended: start=%d end=%d", value["start_hour"], value["end_hour"])

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
            model = self._forecast_model_for_day(local_now.date()+timedelta(days=1))
            if self._pv_learning.calibration_model != model:
                self._pv_learning.set_calibration_model(model)
                self._pv_state_dirty = True
                _LOGGER.info("Forecast calibration model selected: model=%s samples=%d", model, len(self._pv_calibrator))
            complete = {}
            for offset in range(3):
                day = (local_now.date() + timedelta(days=offset)).isoformat()
                start, end = day_bounds(day, self._site_timezone)
                hours = [h for h in hourly if h["time"].startswith(day)]
                expected = {int((start+timedelta(hours=h)).timestamp())
                            for h in range(int((end-start).total_seconds()/3600))}
                if {h.get("timestamp") for h in hours} == expected and day in daily:
                    complete[day] = daily[day]
                    if self._pv_learning.snapshot(day, daily[day].energy_kwh, local_now,
                            forecast_model=self._forecast_model_for_day(datetime.fromisoformat(day).date())):
                        self._pv_state_dirty = True
            tomorrow, after = [(local_now.date()+timedelta(days=d)).isoformat() for d in (1, 2)]
            fc, fc2 = complete.get(tomorrow), complete.get(after)
            self.forecast_tomorrow_kwh = fc.energy_kwh if fc else None
            self.forecast_day_after_kwh = fc2.energy_kwh if fc2 else None
            self._raw_forecast_kwh = {d: fc.energy_kwh for d, fc in complete.items()}
            self._raw_hourly_forecast = [dict(h) for h in hourly if h["time"][:10] in complete]
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
            self._adjust_daily_forecasts()
            await self._save_pv_state()
        except Exception as exc:
            # A previous day's chart must not masquerade as today's forecast.
            self.forecast_tomorrow_kwh = None
            self.forecast_day_after_kwh = None
            self._forecast_today_kwh = None
            self._raw_forecast_kwh = {}
            self._raw_hourly_forecast = []
            self._dated_hourly_pv_forecast = {}
            self.hourly_forecast_today = []
            self.hourly_weather_today = []
            self.hourly_radiation_today = []
            _LOGGER.warning("Forecast fetch failed: %s", exc)

    def _forecast_model_for_day(self, day):
        """Pipeline family, not daily coefficients that change during training."""
        response = getattr(getattr(self, "_forecast", None), "hourly_response", None)
        if response and 0 <= (day-datetime.fromisoformat(response["last_day"]).date()).days <= 14:
            return "hourly_response_v1"
        return "station_gain_v1"
