"""
Rate-Limit Aware Tomorrow.io Client
====================================
Enhanced client with:
- Automatic retry with exponential backoff for 429 errors
- Per-city cooldown after rate limit
- Global rate limiting between API calls
- Per-city weather cache with TTL (prevents redundant API calls)
- Comprehensive logging

Usage:
    from src.clients.tomorrow_client import TomorrowClient, RateLimitConfig
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)


class RateLimitConfig:
    """
    Base config for Tomorrow.io API rate limiting.

    - max_retries: how many times to retry a request when hitting 429 or transient failures
    - base_backoff_seconds: starting backoff delay (will be multiplied exponentially)
    - max_backoff_seconds: cap for backoff delay
    - per_city_cooldown_seconds: if a city hits 429, don't ask again within this window
    - global_min_interval_seconds: minimum time between any two API calls
    """
    def __init__(
        self,
        max_retries: int = 5,
        base_backoff_seconds: float = 2.0,
        max_backoff_seconds: float = 60.0,
        per_city_cooldown_seconds: float = 180.0,
        global_min_interval_seconds: float = 1.0,
    ):
        self.max_retries = max_retries
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.per_city_cooldown_seconds = per_city_cooldown_seconds
        self.global_min_interval_seconds = global_min_interval_seconds


class TomorrowClient:
    """
    Async client around Tomorrow.io API that:
    - Handles HTTP 429 with exponential backoff
    - Applies per-city cooldown after rate-limit
    - Throttles overall request rate with global_min_interval_seconds
    - Caches weather data per city with TTL to avoid redundant API calls
    """
    BASE_URL = "https://api.tomorrow.io/v4"

    # Bulletproof mapping to the EXACT weather stations Polymarket uses
    COORD_MAP = {
        # London City Airport (EGLC) - precise for London markets
        "london": "51.5028,-0.0551",
        "heathrow": "51.5028,-0.0551",

        # Incheon Intl Airport (RKSI) - used for Seoul/Incheon markets
        "incheon": "37.4602,126.4407",
        "seoul": "37.4602,126.4407",

        # Central Park Station - precise for NYC markets
        "nyc": "40.7128,-74.0060",
        "new-york": "40.7128,-74.0060",
        "new york": "40.7128,-74.0060",
    }

    def __init__(
        self,
        api_key: str,
        rate_limit_config: Optional[RateLimitConfig] = None,
        cache_ttl_seconds: float = 21600.0,  # 6 hours default TTL
    ):
        self.api_key = api_key
        self.rate_limit_config = rate_limit_config or RateLimitConfig()
        self.cache_ttl_seconds = cache_ttl_seconds
        self._session: Optional[aiohttp.ClientSession] = None

        # city_slug -> timestamp of when we last saw a 429
        self._city_rate_limited_until: Dict[str, float] = {}

        # city_slug -> (cached_data, timestamp) for weather cache
        self._weather_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}

        # timestamp of last *any* request
        self._last_request_ts: float = 0.0

        logger.info("☁️ Tomorrow.io client initialized with rate limiting and %ds cache TTL", cache_ttl_seconds)

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        """Close the HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def _is_city_rate_limited(self, city_slug: str) -> bool:
        """Check if city is in rate-limit cooldown"""
        until_ts = self._city_rate_limited_until.get(city_slug)
        if not until_ts:
            return False
        now = time.time()
        if now >= until_ts:
            # cooldown expired
            del self._city_rate_limited_until[city_slug]
            return False
        return True

    def _mark_city_rate_limited(self, city_slug: str):
        """Mark a city as rate-limited with cooldown"""
        cooldown = self.rate_limit_config.per_city_cooldown_seconds
        until_ts = time.time() + cooldown
        self._city_rate_limited_until[city_slug] = until_ts
        logger.warning(
            "⛔ City %s hit rate limit. Cooldown until %s (%.0fs).",
            city_slug,
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(until_ts)),
            cooldown,
        )

    def _is_cache_valid(self, city_slug: str) -> bool:
        """Check if cached weather data is still valid"""
        if city_slug not in self._weather_cache:
            return False
        cached_data, timestamp = self._weather_cache[city_slug]
        now = time.time()
        age_seconds = now - timestamp
        if age_seconds >= self.cache_ttl_seconds:
            logger.debug(
                "Cache expired for %s (age: %.0fs, TTL: %.0fs)",
                city_slug,
                age_seconds,
                self.cache_ttl_seconds
            )
            del self._weather_cache[city_slug]
            return False
        return True

    def _get_cached_weather(self, city_slug: str) -> Optional[Dict[str, Any]]:
        """Get cached weather data for a city"""
        if self._is_cache_valid(city_slug):
            cached_data, timestamp = self._weather_cache[city_slug]
            age_minutes = (time.time() - timestamp) / 60
            logger.info(
                "📦 Using cached weather for %s (age: %.1f min, TTL: %.0f min)",
                city_slug,
                age_minutes,
                self.cache_ttl_seconds / 60
            )
            return cached_data
        return None

    def _cache_weather(self, city_slug: str, data: Dict[str, Any]):
        """Cache weather data for a city"""
        self._weather_cache[city_slug] = (data, time.time())
        logger.debug(
            "💾 Cached weather for %s (TTL: %.0f min)",
            city_slug,
            self.cache_ttl_seconds / 60
        )

    def clear_cache(self, city_slug: Optional[str] = None):
        """Clear weather cache"""
        if city_slug:
            if city_slug in self._weather_cache:
                del self._weather_cache[city_slug]
                logger.info(f"Cleared cache for {city_slug}")
        else:
            count = len(self._weather_cache)
            self._weather_cache.clear()
            logger.info(f"Cleared weather cache for {count} cities")

    async def _respect_global_rate(self):
        """Ensure we don't exceed global_min_interval_seconds between API calls"""
        now = time.time()
        elapsed = now - self._last_request_ts
        min_interval = self.rate_limit_config.global_min_interval_seconds
        if elapsed < min_interval:
            sleep_for = min_interval - elapsed
            logger.debug(
                "Global rate limiter: sleeping for %.2fs before next API call.",
                sleep_for,
            )
            await asyncio.sleep(sleep_for)

    async def get_city_weather(
        self,
        city_slug: str,
        location: str,
        fields: Optional[list] = None,
        timesteps: str = "1h",
        units: str = "metric",
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch weather data for a city with rate-limit handling and caching.

        Args:
            city_slug: internal name used in logs ("london", "seoul", etc)
            location: lat,long or address supported by Tomorrow.io
            fields: fields to request from Tomorrow.io API
            timesteps: "1h", "1d", etc
            units: "metric" or "imperial"

        Returns:
            Weather data dictionary or None if rate-limited or failed
        """
        # Check cache first - skip API call if we have fresh data
        cached = self._get_cached_weather(city_slug)
        if cached is not None:
            return cached

        # Check city cooldown
        if self._is_city_rate_limited(city_slug):
            logger.warning(
                "⏱️ Skipping %s - still in cooldown window after previous rate limit.",
                city_slug,
            )
            return None

        endpoint = f"{self.BASE_URL}/timelines"
        params = {
            "location": location,
            "timesteps": timesteps,
            "apikey": self.api_key,
            "units": units,
        }
        if fields:
            params["fields"] = ",".join(fields)

        logger.info(f"☁️ Fetching weather for {city_slug} via Tomorrow.io")

        response_json, status_code = await self._request_with_retries(
            url=endpoint,
            params=params,
            city_slug=city_slug,
        )

        if response_json is None:
            return None

        if status_code == 200:
            # Cache successful responses
            self._cache_weather(city_slug, response_json)
            return response_json

        logger.error(
            "Weather API request for %s failed with status %s and no more retries.",
            city_slug,
            status_code,
        )
        return None

    async def _request_with_retries(
        self,
        url: str,
        params: Dict[str, Any],
        city_slug: str,
    ) -> Tuple[Optional[Dict[str, Any]], int]:
        """
        Perform HTTP request with retries.

        IMPORTANT: 429 errors are NOT retried - they trigger a hard cooldown.
        Only 5xx errors and network issues are retried with exponential backoff.

        Returns (response_json_or_none, status_code_or_0)
        """
        attempt = 0
        backoff = self.rate_limit_config.base_backoff_seconds
        max_backoff = self.rate_limit_config.max_backoff_seconds
        max_retries = self.rate_limit_config.max_retries
        last_status = 0

        session = await self._get_session()

        while attempt <= max_retries:
            attempt += 1

            # Global throttling
            await self._respect_global_rate()

            # Re-check cooldown before each attempt (critical!)
            if self._is_city_rate_limited(city_slug):
                logger.warning(
                    "⏱️ City %s is in cooldown - skipping all retry attempts.",
                    city_slug,
                )
                return None, 429

            try:
                self._last_request_ts = time.time()

                async with session.get(url, params=params) as response:
                    last_status = response.status

                    # Success
                    if response.status == 200:
                        try:
                            return await response.json(), response.status
                        except ValueError:
                            logger.error(
                                f"Failed to parse JSON from Tomorrow.io response for {city_slug}",
                            )
                            return None, response.status

                    # Rate limit (429) - HARD FAIL, NO RETRY
                    if response.status == 429:
                        body_text = await response.text()
                        logger.error(
                            f"Weather API error 429 for {city_slug}: {body_text}",
                        )
                        # Mark as rate-limited - this city is blocked for cooldown period
                        self._mark_city_rate_limited(city_slug)
                        # DO NOT RETRY - cooldown handles it
                        logger.warning(
                            f"🚫 Rate limit hit for {city_slug}. Skipping this city for {self.rate_limit_config.per_city_cooldown_seconds}s.",
                        )
                        return None, response.status

                    # Server errors (5xx) - RETRY with backoff
                    if 500 <= response.status < 600:
                        if attempt > max_retries:
                            logger.error(
                                f"Weather API server error {response.status} for {city_slug} after {attempt - 1} attempts.",
                            )
                            return None, response.status

                        logger.warning(
                            f"Weather API server error {response.status} for {city_slug}. Attempt {attempt}/{max_retries}. Sleeping {backoff:.1f}s.",
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, max_backoff)
                        continue

                    # Non-retriable errors (4xx except 429)
                    logger.error(
                        f"Non-retriable weather API error {response.status} for {city_slug}: {await response.text()}",
                    )
                    return None, response.status

            except aiohttp.ClientError as e:
                # Network issues - RETRY with backoff
                if attempt > max_retries:
                    logger.exception(
                        f"Network error calling Tomorrow.io for {city_slug}; giving up after {attempt - 1} attempts.",
                    )
                    return None, last_status

                logger.warning(
                    f"Network error calling Tomorrow.io for {city_slug} (attempt {attempt}/{max_retries}): {str(e)}. Sleeping {backoff:.1f}s.",
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

        return None, last_status


def create_tomorrow_client(
    api_key: str,
    rate_limit_config: Optional[RateLimitConfig] = None,
    cache_ttl_seconds: float = 21600.0,  # 6 hours default
) -> TomorrowClient:
    """
    Factory function to create a TomorrowClient.

    Args:
        api_key: Tomorrow.io API key
        rate_limit_config: Optional rate limiting configuration
        cache_ttl_seconds: Cache TTL in seconds (default: 6 hours)

    Returns:
        Configured TomorrowClient instance
    """
    return TomorrowClient(
        api_key=api_key,
        rate_limit_config=rate_limit_config,
        cache_ttl_seconds=cache_ttl_seconds,
    )

