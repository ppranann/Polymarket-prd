"""
Multi-Weather API Client
=========================
Combines weather data from multiple sources:
- Tomorrow.io (primary)
- OpenWeatherMap (secondary)
- Weather.gov (free, official data)

Uses weighted ensemble for more accurate forecasts.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from .config import BotConfig
from .clients.tomorrow_io import WeatherData, TomorrowIOClient

logger = logging.getLogger(__name__)


@dataclass
class EnsembleWeatherData:
    """Combined weather data from multiple sources"""
    location: Tuple[float, float]
    fetched_at: datetime
    sources_used: List[str]
    
    # Temperature data (weighted average)
    temperature: float
    temperature_confidence: float  # How much the sources agree
    
    # Precipitation (weighted average)
    precipitation_probability: float
    precipitation_confidence: float
    
    # Per-source data
    source_data: Dict[str, Dict[str, Any]]
    
    def to_dict(self) -> Dict:
        return {
            "location": {"lat": self.location[0], "lon": self.location[1]},
            "fetched_at": self.fetched_at.isoformat(),
            "sources_used": self.sources_used,
            "temperature": self.temperature,
            "temperature_confidence": self.temperature_confidence,
            "precipitation_probability": self.precipitation_probability,
            "precipitation_confidence": self.precipitation_confidence,
            "source_data": self.source_data
        }


# Weight for each source (should sum to 1.0)
SOURCE_WEIGHTS = {
    "tomorrow_io": 0.40,  # Primary source
    "openweathermap": 0.30,  # Secondary
    "weather_gov": 0.30,  # Official source
}


class OpenWeatherMapClient:
    """Client for OpenWeatherMap API (free tier available)"""
    
    BASE_URL = "https://api.openweathermap.org/data/2.5"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def get_forecast(
        self,
        lat: float,
        lon: float,
        hours: int = 24
    ) -> Dict[str, Any]:
        """
        Get weather forecast from OpenWeatherMap.
        
        Args:
            lat: Latitude
            lon: Longitude
            hours: Hours to forecast
            
        Returns:
            Raw API response
        """
        session = await self._get_session()
        
        params = {
            "lat": lat,
            "lon": lon,
            "appid": self.api_key,
            "units": "imperial"
        }
        
        try:
            async with session.get(
                f"{self.BASE_URL}/forecast",
                params=params
            ) as response:
                if response.status == 401:
                    logger.error("OpenWeatherMap API key invalid")
                    return {}
                elif response.status != 200:
                    logger.warning(f"OpenWeatherMap returned {response.status}")
                    return {}
                
                return await response.json()
        except Exception as e:
            logger.error(f"OpenWeatherMap request failed: {e}")
            return {}
    
    def extract_temperature(self, data: Dict) -> float:
        """Extract mean temperature from forecast"""
        try:
            forecasts = data.get("list", [])[:8]  # First 8 = 24 hours
            if not forecasts:
                return 65.0
            
            temps = [
                f["main"]["temp"] 
                for f in forecasts 
                if "main" in f and "temp" in f["main"]
            ]
            
            return sum(temps) / len(temps) if temps else 65.0
        except Exception:
            return 65.0
    
    def extract_precipitation(self, data: Dict) -> float:
        """Extract max precipitation probability"""
        try:
            forecasts = data.get("list", [])[:8]
            if not forecasts:
                return 0.0
            
            probs = []
            for f in forecasts:
                if "pop" in f:  # Probability of precipitation
                    probs.append(f["pop"] * 100)  # Convert to percentage
            
            return max(probs) if probs else 0.0
        except Exception:
            return 0.0


class WeatherGovClient:
    """Client for Weather.gov API (free, official US data)"""
    
    BASE_URL = "https://api.weather.gov"
    
    def __init__(self, user_agent: str = "PolymarketBot/1.0"):
        self.user_agent = user_agent
        self._session: Optional[aiohttp.ClientSession] = None
        self._grid_cache: Dict[Tuple[float, float], str] = {}
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            headers = {"User-Agent": self.user_agent}
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers=headers
            )
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def get_forecast(
        self,
        lat: float,
        lon: float,
        hours: int = 24
    ) -> Dict[str, Any]:
        """
        Get weather forecast from Weather.gov.
        
        Args:
            lat: Latitude
            lon: Longitude
            hours: Hours to forecast
            
        Returns:
            Raw API response
        """
        session = await self._get_session()
        
        try:
            # First get the grid point
            grid_key = (round(lat, 2), round(lon, 2))
            if grid_key not in self._grid_cache:
                async with session.get(
                    f"{self.BASE_URL}/points/{lat},{lon}"
                ) as response:
                    if response.status != 200:
                        return {}
                    
                    points_data = await response.json()
                    properties = points_data.get("properties", {})
                    self._grid_cache[grid_key] = {
                        "office": properties.get("gridId"),
                        "gridX": properties.get("gridX"),
                        "gridY": properties.get("gridY")
                    }
            
            grid = self._grid_cache[grid_key]
            
            # Get forecast
            url = (
                f"{self.BASE_URL}/gridpoints/"
                f"{grid['office']}/{grid['gridX']},{grid['gridY']}/forecast"
            )
            
            async with session.get(url) as response:
                if response.status != 200:
                    return {}
                
                return await response.json()
                
        except Exception as e:
            logger.error(f"Weather.gov request failed: {e}")
            return {}
    
    def extract_temperature(self, data: Dict) -> float:
        """Extract mean temperature from forecast"""
        try:
            periods = data.get("properties", {}).get("periods", [])[:8]
            if not periods:
                return 65.0
            
            temps = [p.get("temperature", 65.0) for p in periods]
            return sum(temps) / len(temps)
        except Exception:
            return 65.0
    
    def extract_precipitation(self, data: Dict) -> float:
        """Extract max precipitation probability"""
        try:
            periods = data.get("properties", {}).get("periods", [])[:8]
            if not periods:
                return 0.0
            
            probs = []
            for p in periods:
                # Weather.gov expresses precip as text, try to extract
                forecast = p.get("shortForecast", "").lower()
                if "rain" in forecast or "shower" in forecast:
                    probs.append(50.0)  # Moderate probability
                elif "thunderstorm" in forecast:
                    probs.append(70.0)
                elif "snow" in forecast or "flurries" in forecast:
                    probs.append(40.0)
                else:
                    probs.append(10.0)
            
            return max(probs) if probs else 0.0
        except Exception:
            return 0.0


class MultiWeatherClient:
    """
    Combines weather data from multiple sources.
    
    Features:
    - Parallel API calls for speed
    - Weighted ensemble averaging
    - Confidence scoring
    - Fallback handling
    """
    
    def __init__(
        self,
        config: BotConfig,
        tomorrow_io_key: str = None,
        openweathermap_key: str = None
    ):
        """
        Initialize multi-weather client.
        
        Args:
            config: Bot configuration
            tomorrow_io_key: Tomorrow.io API key
            openweathermap_key: OpenWeatherMap API key
        """
        self.config = config
        self.location = (
            config.default_location.lat,
            config.default_location.lon
        )
        
        # Initialize clients
        self.tomorrow_client = TomorrowIOClient(
            api_key=tomorrow_io_key or config.tomorrow_io.get('api_key', ''),
            default_location=self.location
        )
        
        self.owm_client = OpenWeatherMapClient(
            api_key=openweathermap_key or ''
        )
        
        self.gov_client = WeatherGovClient()
    
    async def close(self):
        """Close all client connections"""
        await self.tomorrow_client.close()
        await self.owm_client.close()
        await self.gov_client.close()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def get_ensemble_forecast(
        self,
        hours: int = 24,
        location: Tuple[float, float] = None
    ) -> EnsembleWeatherData:
        """
        Get weather forecast from all sources and combine.
        
        Args:
            hours: Hours to forecast
            location: (lat, lon) override
            
        Returns:
            EnsembleWeatherData with combined forecasts
        """
        loc = location or self.location
        
        # Fetch from all sources in parallel
        tasks = [
            self._fetch_tomorrow(loc, hours),
            self._fetch_owm(loc, hours),
            self._fetch_gov(loc, hours)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        source_data = {}
        temperatures = []
        precipitations = []
        
        for i, (name, weight) in enumerate(SOURCE_WEIGHTS.items()):
            result = results[i]
            
            if isinstance(result, Exception):
                logger.warning(f"Weather source {name} failed: {result}")
                continue
            
            if not result:
                logger.warning(f"Weather source {name} returned empty")
                continue
            
            source_data[name] = result
            
            # Extract temperature
            if "temperature" in result:
                temperatures.append({
                    "value": result["temperature"],
                    "weight": weight
                })
            
            # Extract precipitation
            if "precipitation" in result:
                precipitations.append({
                    "value": result["precipitation"],
                    "weight": weight
                })
        
        # Calculate weighted averages
        temperature = self._weighted_average(temperatures) if temperatures else 65.0
        precipitation = self._weighted_average(precipitations) if precipitations else 0.0
        
        # Calculate confidence (1 - variance)
        temp_confidence = self._calculate_confidence(
            [t["value"] for t in temperatures]
        ) if temperatures else 0.5
        
        precip_confidence = self._calculate_confidence(
            [p["value"] for p in precipitations]
        ) if precipitations else 0.5
        
        return EnsembleWeatherData(
            location=loc,
            fetched_at=datetime.now(),
            sources_used=list(source_data.keys()),
            temperature=temperature,
            temperature_confidence=temp_confidence,
            precipitation_probability=precipitation,
            precipitation_confidence=precip_confidence,
            source_data=source_data
        )
    
    async def _fetch_tomorrow(
        self,
        location: Tuple[float, float],
        hours: int
    ) -> Dict[str, Any]:
        """Fetch from Tomorrow.io"""
        try:
            data = await self.tomorrow_client.get_hourly_forecast(hours=hours, location=location)
            return {
                "temperature": data.temp_mean,
                "precipitation": data.precipitation_probability,
                "source": "tomorrow_io"
            }
        except Exception as e:
            logger.error(f"Tomorrow.io error: {e}")
            return {}
    
    async def _fetch_owm(
        self,
        location: Tuple[float, float],
        hours: int
    ) -> Dict[str, Any]:
        """Fetch from OpenWeatherMap"""
        try:
            data = await self.owm_client.get_forecast(location[0], location[1], hours)
            if not data:
                return {}
            
            return {
                "temperature": self.owm_client.extract_temperature(data),
                "precipitation": self.owm_client.extract_precipitation(data),
                "source": "openweathermap"
            }
        except Exception as e:
            logger.error(f"OpenWeatherMap error: {e}")
            return {}
    
    async def _fetch_gov(
        self,
        location: Tuple[float, float],
        hours: int
    ) -> Dict[str, Any]:
        """Fetch from Weather.gov"""
        try:
            data = await self.gov_client.get_forecast(location[0], location[1], hours)
            if not data:
                return {}
            
            return {
                "temperature": self.gov_client.extract_temperature(data),
                "precipitation": self.gov_client.extract_precipitation(data),
                "source": "weather_gov"
            }
        except Exception as e:
            logger.error(f"Weather.gov error: {e}")
            return {}
    
    def _weighted_average(self, values: List[Dict]) -> float:
        """Calculate weighted average from list of {value, weight}"""
        if not values:
            return 0.0
        
        total_weight = sum(v["weight"] for v in values)
        if total_weight == 0:
            return values[0]["value"] if values else 0.0
        
        weighted_sum = sum(v["value"] * v["weight"] for v in values)
        return weighted_sum / total_weight
    
    def _calculate_confidence(self, values: List[float]) -> float:
        """
        Calculate confidence based on agreement between sources.
        
        Returns 1.0 if all sources agree, lower if they diverge.
        """
        if not values or len(values) < 2:
            return 0.8
        
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std_dev = variance ** 0.5
        
        # Convert to confidence (higher agreement = higher confidence)
        # Use relative standard deviation
        if mean == 0:
            return 0.5
        
        cv = std_dev / abs(mean)  # Coefficient of variation
        
        # Confidence = 1 / (1 + CV)
        # If CV = 0, confidence = 1
        # If CV = 1, confidence = 0.5
        confidence = 1 / (1 + cv)
        
        return max(0.3, min(1.0, confidence))
    
    def get_temperature_for_market(
        self,
        ensemble: EnsembleWeatherData,
        market_type: str
    ) -> float:
        """
        Get the relevant temperature for a market type.
        
        Args:
            ensemble: Combined weather data
            market_type: "above", "below", etc.
            
        Returns:
            Temperature value adjusted by confidence
        """
        base_temp = ensemble.temperature
        
        # Adjust for confidence
        # Lower confidence = more conservative estimate (closer to 65°F)
        adjustment = (1 - ensemble.temperature_confidence) * (65.0 - base_temp) * 0.5
        
        return base_temp + adjustment
    
    def get_precipitation_for_market(
        self,
        ensemble: EnsembleWeatherData
    ) -> float:
        """
        Get precipitation probability for market.
        
        Args:
            ensemble: Combined weather data
            
        Returns:
            Precipitation probability (0-100)
        """
        base_prob = ensemble.precipitation_probability
        
        # Adjust for confidence
        adjustment = (1 - ensemble.precipitation_confidence) * (50 - base_prob) * 0.3
        
        return min(100, max(0, base_prob + adjustment))


def create_multi_weather_client(
    config: BotConfig,
    tomorrow_io_key: str = None,
    openweathermap_key: str = None
) -> MultiWeatherClient:
    """
    Factory function to create a MultiWeatherClient.
    
    Args:
        config: Bot configuration
        tomorrow_io_key: Tomorrow.io API key
        openweathermap_key: OpenWeatherMap API key
        
    Returns:
        Configured MultiWeatherClient instance
    """
    return MultiWeatherClient(
        config=config,
        tomorrow_io_key=tomorrow_io_key,
        openweathermap_key=openweathermap_key
    )

