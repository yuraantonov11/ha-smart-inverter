"""Solar forecast service using Open-Meteo API with self-learning PV ratio.

Ported from Flutter WeatherService — provides hourly/daily PV generation
forecasts by combining solar radiation data with a dynamically learned
conversion ratio.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────
OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"
FORECAST_PARAMS = (
    "shortwave_radiation,temperature_2m,weather_code,cloud_cover,"
    "wind_speed_10m,direct_radiation,diffuse_radiation"
)
LOCAL_CACHE_TTL_SEC = 60 * 12       # 12 min for hourly data
DAILY_CACHE_TTL_SEC = 60 * 20       # 20 min for daily aggregates
MIN_REQUEST_INTERVAL_SEC = 1.0       # Rate limit
DEFAULT_LEARNED_RATIO = 0.12         # Default W per W/m²
ANOMALY_MIN_SAMPLES = 4
ANOMALY_SIGMA = 2.0


class SolarForecast:
    """Daily solar forecast result."""

    def __init__(
        self,
        date: str,
        energy_kwh: float,
        peak_power_w: float,
        hourly_power: list[float] | None = None,
    ) -> None:
        self.date = date
        self.energy_kwh = energy_kwh
        self.peak_power_w = peak_power_w
        self.hourly_power = hourly_power or []


class ForecastService:
    """Async service that fetches solar radiation from Open-Meteo and
    converts it to PV power using a self-learning ratio."""

    def __init__(
        self,
        latitude: float = 50.45,
        longitude: float = 30.52,
        pv_capacity_w: float = 3000.0,
    ) -> None:
        self._latitude = latitude
        self._longitude = longitude
        self._pv_capacity_w = pv_capacity_w
        self._session: aiohttp.ClientSession | None = None

        # Learned conversion ratio (W of PV per W/m² of radiation)
        self.learned_ratio: float = DEFAULT_LEARNED_RATIO
        self._ratio_samples: list[float] = []

        # Caches
        self._hourly_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._daily_cache: tuple[float, dict[str, SolarForecast]] | None = None
        self._last_request_time: float = 0.0
        self._in_flight_local: asyncio.Task | None = None
        self._in_flight_daily: asyncio.Task | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15, connect=10),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ── Public API ──────────────────────────────────────────────────────

    async def get_hourly_forecast(self) -> list[dict[str, Any]]:
        """Return list of {time, radiation_wm2, power_w} for next 48 hours."""
        now = time.monotonic()
        if self._hourly_cache and (now - self._hourly_cache[0]) < LOCAL_CACHE_TTL_SEC:
            return self._hourly_cache[1]

        if self._in_flight_local and not self._in_flight_local.done():
            return await self._in_flight_local

        self._in_flight_local = asyncio.create_task(self._fetch_hourly())
        try:
            result = await self._in_flight_local
            self._hourly_cache = (time.monotonic(), result)
            return result
        finally:
            self._in_flight_local = None

    async def get_daily_forecasts(self, days: int = 2) -> dict[str, SolarForecast]:
        """Return {date_str: SolarForecast} for the next `days` days."""
        now = time.monotonic()
        if self._daily_cache and (now - self._daily_cache[0]) < DAILY_CACHE_TTL_SEC:
            return self._daily_cache[1]

        if self._in_flight_daily and not self._in_flight_daily.done():
            return await self._in_flight_daily

        self._in_flight_daily = asyncio.create_task(self._fetch_daily(days))
        try:
            result = await self._in_flight_daily
            self._daily_cache = (time.monotonic(), result)
            return result
        finally:
            self._in_flight_daily = None

    def update_ratio(self, actual_pv_kwh: float, radiation_kwh_m2: float) -> None:
        """Feed actual daily PV energy + radiation total to learn the ratio."""
        if radiation_kwh_m2 <= 0:
            return
        raw_ratio = actual_pv_kwh / radiation_kwh_m2
        if self._is_anomaly(raw_ratio):
            _LOGGER.info("Ignoring anomalous PV ratio: %.4f", raw_ratio)
            return
        self._ratio_samples.append(raw_ratio)
        # Exponential moving average (α=0.3)
        self.learned_ratio = 0.7 * self.learned_ratio + 0.3 * raw_ratio
        _LOGGER.info(
            "Updated learned PV ratio: %.4f (from %.2f kWh / %.2f kWh/m²)",
            self.learned_ratio, actual_pv_kwh, radiation_kwh_m2,
        )

    # ── Internal ────────────────────────────────────────────────────────

    async def _fetch_hourly(self) -> list[dict[str, Any]]:
        """Fetch hourly shortwave radiation and convert to PV power."""
        await self._rate_limit()
        session = await self._ensure_session()
        params = {
            "latitude": self._latitude,
            "longitude": self._longitude,
            "hourly": "shortwave_radiation",
            "timezone": "auto",
            "forecast_days": 2,
        }
        try:
            async with session.get(OPEN_METEO_BASE, params=params) as resp:
                data = await resp.json()
        except Exception as exc:
            _LOGGER.error("Open-Meteo hourly request failed: %s", exc)
            return []

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        radiations = hourly.get("shortwave_radiation", [])

        result: list[dict[str, Any]] = []
        for t, rad in zip(times, radiations):
            power_w = round((rad or 0) * self.learned_ratio)
            result.append({"time": t, "radiation_wm2": rad or 0, "power_w": power_w})
        return result

    async def _fetch_daily(self, days: int) -> dict[str, SolarForecast]:
        """Aggregate hourly data into daily forecasts."""
        hourly = await self.get_hourly_forecast()
        if not hourly:
            return {}

        daily: dict[str, list[dict[str, Any]]] = {}
        for h in hourly:
            date_str = h["time"][:10]  # "2026-06-15"
            daily.setdefault(date_str, []).append(h)

        result: dict[str, SolarForecast] = {}
        for date_str, hours in daily.items():
            if len(result) >= days:
                break
            energies = [h["power_w"] for h in hours]  # Wh per hour (W * 1h)
            total_kwh = sum(energies) / 1000.0
            peak_w = max(energies) if energies else 0.0
            result[date_str] = SolarForecast(
                date=date_str,
                energy_kwh=round(total_kwh, 2),
                peak_power_w=round(peak_w),
                hourly_power=energies,
            )
        return result

    def _is_anomaly(self, value: float) -> bool:
        if len(self._ratio_samples) < ANOMALY_MIN_SAMPLES:
            return False
        avg = sum(self._ratio_samples) / len(self._ratio_samples)
        variance = sum((v - avg) ** 2 for v in self._ratio_samples) / len(self._ratio_samples)
        std = variance ** 0.5
        if std < 0.001:
            return False
        return abs(value - avg) > ANOMALY_SIGMA * std

    async def _rate_limit(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < MIN_REQUEST_INTERVAL_SEC:
            await asyncio.sleep(MIN_REQUEST_INTERVAL_SEC - elapsed)
        self._last_request_time = time.monotonic()
