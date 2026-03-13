"""
Open-Meteo Ensemble Weather Client
====================================
Free, no-API-key forecast source used as a second opinion alongside
NWS/Tomorrow.io for ensemble validation.

- Uses Open-Meteo free API (https://api.open-meteo.com)
- Returns daily max temperature in Fahrenheit per city per date
- 30-minute in-memory cache (same TTL as NWS/Tomorrow.io)
- If unavailable, returns None — the caller decides whether to proceed
"""

import asyncio
import logging
import time
from typing import Dict, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

# Coordinates for each supported city (lat, lon)
CITY_COORDS: Dict[str, Tuple[float, float]] = {
    "nyc":     (40.7789, -73.9692),
    "london":  (51.5074, -0.1278),
    "seoul":   (37.4691, 126.4505),  # Incheon Airport — matches Tomorrow.io + Polymarket resolution station
    "chicago": (41.9742, -87.9073),
    "miami":   (25.7959, -80.2870),
    "dallas":  (32.8998, -97.0403),
    "atlanta": (33.6407, -84.4277),
    "seattle": (47.4502, -122.3088),
}

BASE_URL = "https://api.open-meteo.com/v1/forecast"
CACHE_TTL = 1800.0  # 30 minutes


class OpenMeteoClient:
    """
    Async client for Open-Meteo free daily forecast API.

    Returns daily max temperature in Fahrenheit for each date in the
    7-day forecast window. Used as a second weather source to validate
    primary NWS/Tomorrow.io forecasts before placing trades.
    """

    def __init__(self, cache_ttl: float = CACHE_TTL):
        self._cache: Dict[str, Tuple[Dict[str, float], float]] = {}
        self._cache_ttl = cache_ttl
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_daily_forecast(self, city: str) -> Optional[Dict[str, float]]:
        """
        Fetch daily max temperature forecast for a city.

        Args:
            city: City slug (e.g. "nyc", "london", "seoul")

        Returns:
            Dict mapping ISO date strings to max temp in Fahrenheit,
            or None if the request fails or city is unknown.
        """
        # Check cache
        cached = self._cache.get(city)
        if cached:
            data, ts = cached
            if time.time() - ts < self._cache_ttl:
                return data

        coords = CITY_COORDS.get(city)
        if not coords:
            logger.debug(f"Open-Meteo: no coordinates for city '{city}'")
            return None

        lat, lon = coords
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max",
            "temperature_unit": "fahrenheit",
            "forecast_days": 7,
            "timezone": "auto",
        }

        try:
            session = await self._get_session()
            timeout = aiohttp.ClientTimeout(total=8)
            async with session.get(BASE_URL, params=params, timeout=timeout) as resp:
                if resp.status != 200:
                    logger.warning(f"Open-Meteo HTTP {resp.status} for {city}")
                    return None
                payload = await resp.json()
        except asyncio.TimeoutError:
            logger.warning(f"Open-Meteo timeout for {city}")
            return None
        except Exception as e:
            logger.warning(f"Open-Meteo error for {city}: {e}")
            return None

        daily = payload.get("daily", {})
        dates = daily.get("time", [])
        temps = daily.get("temperature_2m_max", [])

        if not dates or not temps:
            logger.warning(f"Open-Meteo: empty daily data for {city}")
            return None

        result: Dict[str, float] = {}
        for date, temp in zip(dates, temps):
            if temp is not None:
                result[date] = float(temp)

        if not result:
            return None

        self._cache[city] = (result, time.time())
        logger.info(
            f"Open-Meteo {city}: "
            + ", ".join(f"{d}: {t:.1f}°F" for d, t in list(result.items())[:4])
        )
        return result

    async def get_temp_for_date(self, city: str, date: str) -> Optional[float]:
        """
        Get max temperature forecast for a specific city and date.

        Args:
            city: City slug
            date: ISO date string e.g. "2026-03-06"

        Returns:
            Max temperature in Fahrenheit, or None if unavailable.
        """
        forecast = await self.get_daily_forecast(city)
        if forecast is None:
            return None
        return forecast.get(date)

    async def close(self) -> None:
        """Close the underlying aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()


def create_open_meteo_client() -> OpenMeteoClient:
    """Factory function."""
    return OpenMeteoClient()
