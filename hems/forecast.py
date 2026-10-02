"""Solar forecast service using Open-Meteo API with self-learning PV ratio.

Ported from Flutter WeatherService — provides hourly/daily PV generation
forecasts by combining solar radiation data with a dynamically learned
conversion ratio.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from .pv_learning import complete_hourly_days, finite

_LOGGER = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────
OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"
FORECAST_PARAMS = (
    "shortwave_radiation,temperature_2m,weather_code,cloud_cover,"
    "wind_speed_10m,direct_radiation,diffuse_radiation"
)

# WMO Weather code → HA weather entity mapping (subset of HA's weather conditions)
# https://open-meteo.com/en/docs (WMO Weather interpretation codes)
WMO_TO_HA_WEATHER = {
    0: ("clear-night" if False else "sunny", "☀️", "Ясно"),
    1: ("partlycloudy", "🌤️", "Переважно ясно"),
    2: ("partlycloudy", "⛅", "Хмарно з проясненнями"),
    3: ("cloudy", "☁️", "Хмарно"),
    45: ("fog", "🌫️", "Туман"),
    48: ("fog", "🌫️", "Паморозний туман"),
    51: ("rainy", "🌦️", "Легка мряка"),
    53: ("rainy", "🌦️", "Мряка"),
    55: ("rainy", "🌧️", "Сильна мряка"),
    61: ("rainy", "🌧️", "Слабкий дощ"),
    63: ("rainy", "🌧️", "Дощ"),
    65: ("rainy", "🌧️", "Сильний дощ"),
    71: ("snowy", "🌨️", "Слабкий сніг"),
    73: ("snowy", "🌨️", "Сніг"),
    75: ("snowy", "❄️", "Сильний сніг"),
    80: ("rainy", "🌦️", "Зливи"),
    81: ("rainy", "🌧️", "Сильні зливи"),
    82: ("pouring", "⛈️", "Дуже сильні зливи"),
    95: ("lightning", "⛈️", "Гроза"),
    96: ("lightning-rainy", "⛈️", "Гроза з градом"),
    99: ("lightning-rainy", "⛈️", "Сильна гроза з градом"),
}
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
        hourly_weather: list[int] | None = None,
        hourly_radiation_wm2: list[float] | None = None,
        hourly_cloud_cover: list[float | None] | None = None,
        hourly_temperature: list[float | None] | None = None,
        dominant_weather_code: int | None = None,
    ) -> None:
        self.date = date
        self.energy_kwh = energy_kwh
        self.peak_power_w = peak_power_w
        self.hourly_power = hourly_power or []
        self.hourly_weather = hourly_weather or []
        self.hourly_radiation_wm2 = hourly_radiation_wm2 or []
        self.hourly_cloud_cover = hourly_cloud_cover or []
        self.hourly_temperature = hourly_temperature or []
        # Dominant weather = the code with most hours (rough summary)
        if hourly_weather:
            from collections import Counter
            self.dominant_weather_code = Counter(hourly_weather).most_common(1)[0][0]
        elif dominant_weather_code is not None:
            self.dominant_weather_code = dominant_weather_code
        else:
            self.dominant_weather_code = None


class ForecastService:
    """Async service that fetches solar radiation from Open-Meteo and
    converts it to PV power using a self-learning ratio."""

    def __init__(
        self,
        latitude: float = 50.45,
        longitude: float = 30.52,
        pv_capacity_w: float = 3000.0,
        timezone_name: str = "UTC",
    ) -> None:
        self._latitude = latitude
        self._longitude = longitude
        self._pv_capacity_w = pv_capacity_w
        self.timezone_name = timezone_name
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
        """Return hourly weather and station power for three local calendar days."""
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
        self._hourly_cache = None
        self._daily_cache = None
        _LOGGER.info(
            "Updated learned PV ratio: %.4f (from %.2f kWh / %.2f kWh/m²)",
            self.learned_ratio, actual_pv_kwh, radiation_kwh_m2,
        )

    def set_station_gain(self, gain: float) -> bool:
        """Use independently trained station gain for every forecast consumer."""
        if not math.isfinite(gain) or gain <= 0:
            return False
        if self.learned_ratio == gain:
            return False
        self.learned_ratio = gain
        self._hourly_cache = None
        self._daily_cache = None
        return True

    async def get_archive_radiation(self, start_day, end_day) -> dict[str, float]:
        """Complete local daily radiation in kWh/m², from independent archive.

        Fetch UTC hours (unambiguous at DST). Include both edge UTC dates,
        then group by HA timezone. One bounded request avoids blocking the
        initial coordinator refresh on four sequential monthly requests.
        """
        from .pv_learning import day_bounds
        tz = ZoneInfo(self.timezone_name)
        first, _ = day_bounds(start_day, tz)
        _, last = day_bounds(end_day, tz)
        stop = (last - timedelta(seconds=1)).date()
        session = await self._ensure_session()
        await self._rate_limit()
        params = {"latitude": self._latitude, "longitude": self._longitude,
                  "start_date": first.date().isoformat(), "end_date": stop.isoformat(),
                  "hourly": "shortwave_radiation", "timezone": "UTC", "timeformat": "unixtime"}
        async with session.get("https://archive-api.open-meteo.com/v1/archive", params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
        hourly = data.get("hourly", {})
        times, values = hourly.get("time", []), hourly.get("shortwave_radiation", [])
        if not times or len(times) != len(values):
            raise ValueError("Archive radiation response is incomplete")
        rows = [{"start": ts, "mean": value} for ts, value in zip(times, values)]
        daily = complete_hourly_days(rows, tz, end_day + timedelta(days=1), ceiling=2000)
        return {day: value for day, value in daily.items()
                if start_day.isoformat() <= day <= end_day.isoformat()}

    # ── Internal ────────────────────────────────────────────────────────

    async def _fetch_hourly(self) -> list[dict[str, Any]]:
        """Fetch hourly shortwave radiation and convert to PV power."""
        await self._rate_limit()
        session = await self._ensure_session()
        params = {
            "latitude": self._latitude,
            "longitude": self._longitude,
            # Request both shortwave_radiation AND weather_code so we
            # can show cloud/rain conditions alongside the power curve.
            "hourly": "shortwave_radiation,weather_code,cloud_cover,temperature_2m",
            "timezone": self.timezone_name,
            "timeformat": "unixtime",
            "forecast_days": 3,
        }
        try:
            async with session.get(OPEN_METEO_BASE, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as exc:
            _LOGGER.error("Open-Meteo hourly request failed: %s", exc)
            return []

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        radiations = hourly.get("shortwave_radiation", [])
        weather_codes = hourly.get("weather_code", [])
        cloud_covers = hourly.get("cloud_cover", [])
        temperatures = hourly.get("temperature_2m", [])

        result: list[dict[str, Any]] = []
        for i, t in enumerate(times):
            rad = radiations[i] if i < len(radiations) else 0
            wcode = weather_codes[i] if i < len(weather_codes) else None
            cc = cloud_covers[i] if i < len(cloud_covers) else None
            temp = temperatures[i] if i < len(temperatures) else None
            radiation = finite(rad, high=2000)
            if radiation is None:
                continue  # unknown radiation is not a measured zero
            local_time = datetime.fromtimestamp(t, timezone.utc).astimezone(ZoneInfo(self.timezone_name))
            power_w = round(min(20000.0, max(0.0, radiation * self.learned_ratio)))
            result.append({
                "time": local_time.strftime("%Y-%m-%dT%H:00"),
                "timestamp": t,
                "radiation_wm2": radiation,
                "power_w": power_w,
                "weather_code": wcode,
                "cloud_cover": cc,
                "temperature": temp,
            })
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
            energies = [h["power_w"] for h in hours]
            weathers = [h.get("weather_code") for h in hours]
            radiations = [h.get("radiation_wm2", 0) for h in hours]
            clouds = [h.get("cloud_cover") for h in hours]
            temps = [h.get("temperature") for h in hours]
            total_kwh = sum(energies) / 1000.0
            peak_w = max(energies) if energies else 0.0
            # Compute the dominant (most frequent) WMO weather code
            # so the dashboard can show a single weather icon for the day.
            dominant_w = None
            filtered_w = [w for w in weathers if w is not None]
            if filtered_w:
                from collections import Counter
                dominant_w = Counter(filtered_w).most_common(1)[0][0]
            result[date_str] = SolarForecast(
                date=date_str,
                energy_kwh=round(total_kwh, 2),
                peak_power_w=round(peak_w),
                hourly_power=energies,
                hourly_weather=weathers,
                hourly_radiation_wm2=radiations,
                hourly_cloud_cover=clouds,
                hourly_temperature=temps,
                dominant_weather_code=dominant_w,
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
