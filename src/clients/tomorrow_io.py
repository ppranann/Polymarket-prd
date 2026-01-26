"""
Tomorrow.io Weather API Client
==============================
REST client for fetching weather data from Tomorrow.io.
Used for calculating fair prices of weather prediction markets.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from ..config import BotConfig

logger = logging.getLogger(__name__)


@dataclass
class WeatherData:
    """Container for weather forecast data"""
    location: Tuple[float, float]
    fetched_at: datetime
    
    # Temperature data (in Fahrenheit)
    current_temp: float
    feels_like: float
    temp_min: float
    temp_max: float
    temp_mean: float  # Average over forecast period
    
    # Precipitation data
    precipitation_probability: float  # 0.0 to 1.0
    precipitation_amount: Optional[float] = None
    
    # Weather code for categorical conditions
    weather_code: Optional[int] = None
    weather_description: Optional[str] = None
    
    # Raw API response
    raw_data: Optional[Dict] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging"""
        return {
            "location": {"lat": self.location[0], "lon": self.location[1]},
            "fetched_at": self.fetched_at.isoformat(),
            "temperature": {
                "current": self.current_temp,
                "feels_like": self.feels_like,
                "min": self.temp_min,
                "max": self.temp_max,
                "mean": self.temp_mean
            },
            "precipitation": {
                "probability": self.precipitation_probability,
                "amount": self.precipitation_amount
            },
            "weather_code": self.weather_code,
            "weather_description": self.weather_description
        }


# Tomorrow.io weather code mappings
WEATHER_CODES = {
    0: "Unknown",
    1000: "Clear",
    1001: "Cloudy",
    1100: "Mostly Clear",
    1101: "Partly Cloudy",
    1102: "Mostly Cloudy",
    2000: "Fog",
    2100: "Light Fog",
    3000: "Light Wind",
    3001: "Wind",
    3002: "Strong Wind",
    4000: "Drizzle",
    4001: "Rain",
    4200: "Light Rain",
    4201: "Heavy Rain",
    5000: "Snow",
    5001: "Flurries",
    5100: "Light Snow",
    5101: "Heavy Snow",
    6000: "Freezing Drizzle",
    6001: "Freezing Rain",
    6200: "Light Freezing Rain",
    6201: "Heavy Freezing Rain",
    7000: "Hail",
    7100: "Small Hail",
    7101: "Heavy Hail",
    8000: "Thunderstorm",
    8001: "Heavy Thunderstorm",
    9000: "Tornado",
    9001: "Tropical Storm",
    9002: "Hurricane",
}


class TomorrowIOClient:
    """
    Client for Tomorrow.io Weather API.
    
    Provides async methods for fetching weather forecasts
    and processing them for market price calculations.
    """
    
    BASE_URL = "https://api.tomorrow.io/v4"
    
    # Bulletproof mapping to the EXACT weather stations Polymarket uses
    # Using Lat/Long is 100% more reliable than city names
    COORD_MAP = {
        # London City Airport (EGLC) - precise for London markets
        "london": "51.5028,-0.0551",
        
        # Incheon Intl Airport (RKSI) - used for Seoul/Incheon markets
        "incheon": "37.4602,126.4407",
        "seoul": "37.4602,126.4407",  # Force Seoul queries to use Incheon station
        
        # Central Park Station - precise for NYC markets
        "nyc": "40.7128,-74.0060",
        "new-york": "40.7128,-74.0060",
        "new york": "40.7128,-74.0060",
    }
    
    # Keep COORDS tuple format for internal use (backward compatibility)
    COORDS = {
        "london": (51.5028, -0.0551),
        "seoul": (37.4602, 126.4407),
        "incheon": (37.4602, 126.4407),
        "nyc": (40.7128, -74.0060),
        "new-york": (40.7128, -74.0060),
        "new york": (40.7128, -74.0060),
    }
    
    def __init__(
        self,
        api_key: str,
        default_location: Optional[Tuple[float, float]] = None,
        timeout: int = 30
    ):
        """
        Initialize Tomorrow.io client.
        
        Args:
            api_key: Tomorrow.io API key
            default_location: Default (lat, lon) for forecasts
            timeout: Request timeout in seconds
        """
        self.api_key = api_key
        self.default_location = default_location or (40.7128, -74.0060)  # NYC default
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
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
    
    async def get_forecast(
        self,
        location: Optional[Tuple[float, float]] = None,
        timesteps: Optional[List[str]] = None,
        units: str = "imperial",
        fields: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Fetch weather forecast from Tomorrow.io.
        
        Args:
            location: (lat, lon) tuple. Uses default if not provided.
            timesteps: List of timesteps to fetch (e.g., ["1h", "1d"])
            units: "imperial" (Fahrenheit) or "metric" (Celsius)
            fields: List of weather fields to include
            
        Returns:
            Raw API response dictionary
        """
        loc = location or self.default_location
        
        # Default fields for our use case
        if fields is None:
            fields = [
                "temperature",
                "temperatureApparent",
                "temperatureMin",
                "temperatureMax",
                "precipitationProbability",
                "precipitation",
                "weatherCode"
            ]
        
        if timesteps is None:
            timesteps = ["1h", "1d"]
        
        params = {
            "apikey": self.api_key,
            "location": f"{loc[0]},{loc[1]}",
            "fields": ",".join(fields),
            "timesteps": ",".join(timesteps),
            "units": units,
            "timezone": "America/New_York"  # Default to ET
        }
        
        session = await self._get_session()
        
        try:
            async with session.get(
                f"{self.BASE_URL}/weather",
                params=params
            ) as response:
                
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Tomorrow.io API error: {response.status} - {error_text}")
                    raise ValueError(f"Weather API returned {response.status}")
                
                return await response.json()
                
        except aiohttp.ClientError as e:
            logger.error(f"Tomorrow.io request failed: {e}")
            raise
    
    def _extract_hourly_temps(self, data: Dict) -> List[float]:
        """Extract hourly temperatures from API response"""
        try:
            timelines = data.get("data", {}).get("timelines", [])
            hourly_timeline = next(
                (t for t in timelines if t.get("timestep") == "1h"),
                {}
            )
            intervals = hourly_timeline.get("intervals", [])
            temps = [
                i["values"]["temperature"]
                for i in intervals
                if "values" in i and "temperature" in i["values"]
            ]
            return temps
        except Exception as e:
            logger.warning(f"Could not extract hourly temps: {e}")
            return []
    
    def _extract_daily_temps(self, data: Dict) -> List[Dict]:
        """Extract daily temperature ranges from API response"""
        try:
            timelines = data.get("data", {}).get("timelines", [])
            daily_timeline = next(
                (t for t in timelines if t.get("timestep") == "1d"),
                {}
            )
            intervals = daily_timeline.get("intervals", [])
            
            daily_data = []
            for i in intervals:
                if "values" in i:
                    daily_data.append({
                        "min": i["values"].get("temperatureMin"),
                        "max": i["values"].get("temperatureMax"),
                        "mean": (i["values"].get("temperatureMin", 0) + 
                                i["values"].get("temperatureMax", 0)) / 2
                    })
            
            return daily_data
        except Exception as e:
            logger.warning(f"Could not extract daily temps: {e}")
            return []
    
    def _get_weather_description(self, code: int) -> str:
        """Get human-readable weather description from code"""
        return WEATHER_CODES.get(code, f"Unknown ({code})")
    
    async def get_realtime_weather(
        self,
        location_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch realtime weather using Tomorrow.io V4 API.
        
        Args:
            location_name: Location name (city name)
            
        Returns:
            Weather data dictionary or None if 404
        """
        # Tomorrow.io V4 Realtime Endpoint
        url = "https://api.tomorrow.io/v4/weather/realtime"
        params = {
            "location": location_name,
            "apikey": self.api_key,
            "units": "metric"
        }
        
        session = await self._get_session()
        
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 404:
                    logger.error(f"Weather 404: Check if location '{location_name}' is valid.")
                    return None
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientError as e:
            logger.error(f"Tomorrow.io realtime request failed: {e}")
            return None
    
    async def get_forecast_by_city(
        self,
        city_name: str,
        timesteps: Optional[List[str]] = None,
        units: str = "metric"
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch weather forecast for a city using Lat/Long coordinates.

        This method is more reliable than city names as it uses exact coordinates.
        Uses the V4 timelines endpoint with coordinate-based location.

        Args:
            city_name: City name (e.g., "london", "seoul", "nyc")
            timesteps: List of timesteps to fetch (e.g., ["1h", "1d"])
            units: "metric" (Celsius) or "imperial" (Fahrenheit)

        Returns:
            Weather forecast dictionary or None if failed
        """
        # Get coordinates for the city using COORD_MAP (string format "lat,lon")
        # This is 100% more reliable than city names
        city_key = city_name.lower().strip()
        location = self.COORD_MAP.get(city_key, self.COORD_MAP["nyc"])  # Default to NYC

        # Use the V4 /timelines endpoint (not /weather/forecast)
        url = f"{self.BASE_URL}/timelines"

        # Default fields for our use case
        fields = [
            "temperature",
            "temperatureApparent",
            "temperatureMin",
            "temperatureMax",
            "precipitationProbability",
            "precipitation",
            "weatherCode"
        ]

        if timesteps is None:
            timesteps = ["1h"]

        params = {
            "location": location,  # Already in "lat,lon" format from COORD_MAP
            "apikey": self.api_key,
            "fields": ",".join(fields),
            "timesteps": ",".join(timesteps),
            "units": units,
            "timezone": "America/New_York"
        }

        session = await self._get_session()

        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.debug(f"Weather fetch success for {city_name}: {location}")
                    return data
                elif resp.status == 404:
                    logger.warning(f"Weather 404 for city '{city_name}' with coords {location}")
                    return None
                else:
                    error_text = await resp.text()
                    logger.error(f"Weather API error {resp.status} for {city_name}: {error_text}")
                    return None

        except aiohttp.ClientError as e:
            logger.error(f"Weather request failed for {city_name}: {e}")
            return None
    
    async def get_current_weather(
        self,
        location: Optional[Tuple[float, float]] = None
    ) -> WeatherData:
        """
        Get current weather conditions.
        
        Args:
            location: (lat, lon) tuple
            
        Returns:
            WeatherData object with current conditions
        """
        data = await self.get_forecast(
            location=location,
            timesteps=["current"]
        )
        
        try:
            current = data["data"]["timelines"][0]["intervals"][0]["values"]
            
            return WeatherData(
                location=location or self.default_location,
                fetched_at=datetime.now(),
                current_temp=current.get("temperature", 65.0),
                feels_like=current.get("temperatureApparent", 65.0),
                temp_min=current.get("temperatureMin", 65.0),
                temp_max=current.get("temperatureMax", 65.0),
                temp_mean=current.get("temperature", 65.0),
                precipitation_probability=current.get("precipitationProbability", 0.0),
                precipitation_amount=current.get("precipitation"),
                weather_code=current.get("weatherCode"),
                weather_description=self._get_weather_description(
                    current.get("weatherCode", 0)
                ),
                raw_data=data
            )
            
        except (KeyError, IndexError) as e:
            logger.error(f"Error parsing current weather: {e}")
            # Return default values
            return WeatherData(
                location=location or self.default_location,
                fetched_at=datetime.now(),
                current_temp=65.0,
                feels_like=65.0,
                temp_min=60.0,
                temp_max=70.0,
                temp_mean=65.0,
                precipitation_probability=0.0,
                raw_data=data
            )
    
    async def get_hourly_forecast(
        self,
        hours: int = 24,
        location: Optional[Tuple[float, float]] = None
    ) -> WeatherData:
        """
        Get hourly forecast for the next N hours.
        
        Args:
            hours: Number of hours to forecast
            location: (lat, lon) tuple
            
        Returns:
            WeatherData with hourly averages
        """
        data = await self.get_forecast(
            location=location,
            timesteps=["1h"]
        )
        
        try:
            hourly = data["data"]["timelines"][0]["intervals"][:hours]
            values = [i["values"] for i in hourly]
            
            temps = [v.get("temperature", 65.0) for v in values]
            feels_like = [v.get("temperatureApparent", 65.0) for v in values]
            temp_mins = [v.get("temperatureMin", 65.0) for v in values]
            temp_maxs = [v.get("temperatureMax", 65.0) for v in values]
            precip_probs = [v.get("precipitationProbability", 0.0) for v in values]
            
            return WeatherData(
                location=location or self.default_location,
                fetched_at=datetime.now(),
                current_temp=temps[0] if temps else 65.0,
                feels_like=feels_like[0] if feels_like else 65.0,
                temp_min=min(temp_mins) if temp_mins else 60.0,
                temp_max=max(temp_maxs) if temp_maxs else 70.0,
                temp_mean=sum(temps) / len(temps) if temps else 65.0,
                precipitation_probability=max(precip_probs) if precip_probs else 0.0,
                weather_code=values[0].get("weatherCode") if values else None,
                weather_description=self._get_weather_description(
                    values[0].get("weatherCode", 0) if values else 0
                ),
                raw_data=data
            )
            
        except (KeyError, IndexError, ZeroDivisionError) as e:
            logger.error(f"Error parsing hourly forecast: {e}")
            return WeatherData(
                location=location or self.default_location,
                fetched_at=datetime.now(),
                current_temp=65.0,
                feels_like=65.0,
                temp_min=60.0,
                temp_max=70.0,
                temp_mean=65.0,
                precipitation_probability=0.0,
                raw_data=data
            )
    
    async def get_daily_forecast(
        self,
        days: int = 7,
        location: Optional[Tuple[float, float]] = None
    ) -> List[WeatherData]:
        """
        Get daily forecast for the next N days.
        
        Args:
            days: Number of days to forecast
            location: (lat, lon) tuple
            
        Returns:
            List of WeatherData objects, one per day
        """
        data = await self.get_forecast(
            location=location,
            timesteps=["1d"]
        )
        
        try:
            daily = data["data"]["timelines"][0]["intervals"][:days]
            
            weather_list = []
            for i, interval in enumerate(daily):
                values = interval.get("values", {})
                
                weather = WeatherData(
                    location=location or self.default_location,
                    fetched_at=datetime.now() + timedelta(days=i),
                    current_temp=values.get("temperatureMax", 65.0),
                    feels_like=values.get("temperatureApparent", 65.0),
                    temp_min=values.get("temperatureMin", 60.0),
                    temp_max=values.get("temperatureMax", 70.0),
                    temp_mean=(values.get("temperatureMin", 60.0) + 
                              values.get("temperatureMax", 70.0)) / 2,
                    precipitation_probability=values.get("precipitationProbability", 0.0),
                    precipitation_amount=values.get("precipitation"),
                    weather_code=values.get("weatherCode"),
                    weather_description=self._get_weather_description(
                        values.get("weatherCode", 0)
                    ),
                    raw_data=data
                )
                weather_list.append(weather)
            
            return weather_list
            
        except (KeyError, IndexError) as e:
            logger.error(f"Error parsing daily forecast: {e}")
            return []
    
    def get_temperature_for_market(
        self,
        weather_data: WeatherData,
        market_type: str
    ) -> float:
        """
        Extract the relevant temperature for a market type.
        
        Args:
            weather_data: WeatherData object
            market_type: Type of weather market ("above", "below", "range", "exact")
            
        Returns:
            Temperature value for price calculation
        """
        if market_type in ["above", "below"]:
            return weather_data.temp_mean
        elif market_type == "range":
            return weather_data.temp_mean
        elif market_type == "exact":
            return weather_data.current_temp
        else:
            return weather_data.temp_mean
    
    def get_precipitation_probability(self, weather_data: WeatherData) -> float:
        """
        Get precipitation probability (0.0 to 1.0).
        
        Args:
            weather_data: WeatherData object
            
        Returns:
            Probability of precipitation
        """
        return weather_data.precipitation_probability / 100.0 if weather_data.precipitation_probability > 1 else weather_data.precipitation_probability


def create_tomorrow_io_client(
    config: BotConfig,
    api_key: Optional[str] = None
) -> TomorrowIOClient:
    """
    Factory function to create a Tomorrow.io client.
    
    Args:
        config: Bot configuration
        api_key: Optional API key override
        
    Returns:
        Configured TomorrowIOClient instance
    """
    return TomorrowIOClient(
        api_key=api_key or config.tomorrow_io.get('api_key', ''),
        default_location=(
            config.default_location.lat,
            config.default_location.lon
        )
    )

