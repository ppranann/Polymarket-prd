"""
NWS (National Weather Service) Client
======================================
Free, no-API-key US weather data from weather.gov.

- Fetches hourly forecasts via /points/{lat},{lon} → forecastHourly URL
- Returns WeatherData objects with daily_stats in Celsius (matches Tomorrow.io flow)
- Permanent in-memory cache for grid points (they never change)
- 30-minute forecast cache to match Tomorrow.io behaviour
- Covers: NYC, Atlanta, Miami, Seattle, Dallas, Chicago
"""

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import aiohttp

from .tomorrow_io import WeatherData

logger = logging.getLogger(__name__)

# Official NOAA/NWS station coordinates for each US city.
# These match the primary observation stations used for Polymarket resolution.
US_CITY_COORDS: Dict[str, Dict[str, Any]] = {
    "nyc": {
        "lat": 40.7789, "lon": -73.9692,  # Central Park (standard US climate ref)
        "tz": "America/New_York",
    },
    "atlanta": {
        "lat": 33.6407, "lon": -84.4277,  # Hartsfield-Jackson Airport (ATL)
        "tz": "America/New_York",
    },
    "miami": {
        "lat": 25.7959, "lon": -80.2870,  # Miami International Airport (MIA)
        "tz": "America/New_York",
    },
    "seattle": {
        "lat": 47.4502, "lon": -122.3088,  # Seattle-Tacoma International Airport (SEA)
        "tz": "America/Los_Angeles",
    },
    "dallas": {
        "lat": 32.8998, "lon": -97.0403,  # DFW International Airport
        "tz": "America/Chicago",
    },
    "chicago": {
        "lat": 41.9742, "lon": -87.9073,  # O'Hare International Airport (ORD)
        "tz": "America/Chicago",
    },
}

FORECAST_CACHE_TTL = 1800.0  # 30 minutes — same as Tomorrow.io


def _f_to_c(fahrenheit: float) -> float:
    return (fahrenheit - 32.0) * 5.0 / 9.0


class NWSClient:
    """
    Async NWS/weather.gov client for US city max-temp forecasts.

    API flow:
    1. GET /points/{lat},{lon}  →  properties.forecastHourly URL
    2. GET forecastHourly URL   →  hourly periods with temperatures in °F
    3. Group periods by LOCAL date (start-time includes tz offset)
    4. Compute daily min/max/mean, store in Celsius (matches _get_weather_for_date)
    5. Return WeatherData with daily_stats embedded in raw_data
    """

    BASE_URL = "https://api.weather.gov"
    # NWS requires a descriptive User-Agent or requests get blocked
    USER_AGENT = "PolymarketWeatherBot/1.0 (contact: bot@example.com)"

    def __init__(self, cache_ttl_seconds: float = FORECAST_CACHE_TTL):
        self.cache_ttl = cache_ttl_seconds
        self._session: Optional[aiohttp.ClientSession] = None

        # city_slug → {"forecastHourly": url, ...}  — permanent until container restart
        self._grid_cache: Dict[str, str] = {}

        # city_slug → (WeatherData, timestamp)
        self._forecast_cache: Dict[str, Tuple[WeatherData, float]] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": self.USER_AGENT},
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def _is_cache_valid(self, city_slug: str) -> bool:
        if city_slug not in self._forecast_cache:
            return False
        _, ts = self._forecast_cache[city_slug]
        return (time.time() - ts) < self.cache_ttl

    async def _resolve_forecast_url(self, city_slug: str, lat: float, lon: float) -> Optional[str]:
        """
        Resolve the forecastHourly URL for a city.
        Caches permanently — grid points don't change.
        """
        if city_slug in self._grid_cache:
            return self._grid_cache[city_slug]

        session = await self._get_session()
        url = f"{self.BASE_URL}/points/{lat},{lon}"
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"NWS /points failed for {city_slug} ({resp.status}): {body[:200]}")
                    return None
                data = await resp.json(content_type=None)
                hourly_url = data.get("properties", {}).get("forecastHourly")
                if not hourly_url:
                    logger.error(f"NWS /points response missing forecastHourly for {city_slug}")
                    return None
                self._grid_cache[city_slug] = hourly_url
                logger.info(f"NWS grid resolved for {city_slug}: {hourly_url}")
                return hourly_url
        except Exception as e:
            logger.error(f"NWS /points request failed for {city_slug}: {e}")
            return None

    async def get_city_weather(self, city_slug: str) -> Optional[WeatherData]:
        """
        Fetch and return a WeatherData for the given US city.

        Returns WeatherData directly (already parsed), with:
        - temp_max/min/mean in °F (overall range across forecast window)
        - raw_data["daily_stats"] keyed by "YYYY-MM-DD" with values in CELSIUS
          (so _get_weather_for_date() can apply its c_to_f conversion as usual)

        Returns None on network failure or unsupported city.
        """
        city_slug = city_slug.lower()
        city_conf = US_CITY_COORDS.get(city_slug)
        if not city_conf:
            logger.warning(f"NWS: unsupported city '{city_slug}'")
            return None

        # Return cached data if still fresh
        if self._is_cache_valid(city_slug):
            wd, ts = self._forecast_cache[city_slug]
            age_min = (time.time() - ts) / 60
            logger.info(f"NWS cache hit for {city_slug} (age: {age_min:.1f}min)")
            return wd

        lat, lon = city_conf["lat"], city_conf["lon"]

        # Step 1: resolve the forecast URL (cached permanently)
        forecast_url = await self._resolve_forecast_url(city_slug, lat, lon)
        if not forecast_url:
            return None

        # Step 2: fetch hourly forecast
        session = await self._get_session()
        try:
            async with session.get(forecast_url) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"NWS forecast failed for {city_slug} ({resp.status}): {body[:200]}")
                    return None
                data = await resp.json(content_type=None)
        except Exception as e:
            logger.error(f"NWS forecast request failed for {city_slug}: {e}")
            return None

        # Step 3: parse periods
        periods = data.get("properties", {}).get("periods", [])
        if not periods:
            logger.warning(f"NWS: no periods in forecast for {city_slug}")
            return None

        # Group hourly temps (°F) by local date.
        # NWS startTime includes timezone offset, e.g. "2026-02-22T14:00:00-05:00"
        # The date portion before 'T' is already in local time.
        temps_by_date: Dict[str, list] = defaultdict(list)
        for period in periods:
            start = period.get("startTime", "")
            temp_f = period.get("temperature")
            temp_unit = period.get("temperatureUnit", "F")
            if not start or temp_f is None:
                continue
            if temp_unit == "C":
                temp_f = temp_f * 9.0 / 5.0 + 32.0  # normalise to °F
            local_date = start[:10]  # "YYYY-MM-DD"
            temps_by_date[local_date].append(float(temp_f))

        if not temps_by_date:
            logger.warning(f"NWS: no temperature data parsed for {city_slug}")
            return None

        # Step 4: compute daily stats; store in CELSIUS (matches _get_weather_for_date)
        daily_stats_c: Dict[str, Dict[str, float]] = {}
        for date, temps_f in temps_by_date.items():
            daily_stats_c[date] = {
                "min":  _f_to_c(min(temps_f)),
                "max":  _f_to_c(max(temps_f)),
                "mean": _f_to_c(sum(temps_f) / len(temps_f)),
            }

        logger.info(
            f"NWS daily (°C) for {city_slug}: " +
            " | ".join(
                f"{d}: hi={s['max']:.1f} lo={s['min']:.1f}"
                for d, s in sorted(daily_stats_c.items())
            )
        )

        # Step 5: build overall WeatherData in °F
        all_temps_f = [t for ts in temps_by_date.values() for t in ts]
        first_temp_f = float(periods[0].get("temperature", all_temps_f[0]))

        wd = WeatherData(
            location=(lat, lon),
            fetched_at=datetime.now(),
            current_temp=first_temp_f,
            feels_like=first_temp_f,
            temp_min=min(all_temps_f),
            temp_max=max(all_temps_f),
            temp_mean=sum(all_temps_f) / len(all_temps_f),
            precipitation_probability=0.0,
            precipitation_amount=None,
            weather_code=None,
            weather_description=None,
            raw_data={"daily_stats": daily_stats_c, "source": "nws"},
        )

        self._forecast_cache[city_slug] = (wd, time.time())
        return wd


def create_nws_client(cache_ttl_seconds: float = FORECAST_CACHE_TTL) -> NWSClient:
    """Factory function for NWSClient."""
    return NWSClient(cache_ttl_seconds=cache_ttl_seconds)
