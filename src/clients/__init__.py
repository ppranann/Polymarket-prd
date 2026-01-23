"""API Clients for Polymarket Weather Sniping Bot"""

from .polymarket import PolymarketClient, create_polymarket_client, Market, Order, Position
from .tomorrow_io import TomorrowIOClient, create_tomorrow_io_client, WeatherData
from .multi_weather import (
    MultiWeatherClient, 
    create_multi_weather_client,
    EnsembleWeatherData,
    OpenWeatherMapClient,
    WeatherGovClient
)

__all__ = [
    "PolymarketClient",
    "create_polymarket_client",
    "Market",
    "Order",
    "Position",
    "TomorrowIOClient",
    "create_tomorrow_io_client",
    "WeatherData",
    "MultiWeatherClient",
    "create_multi_weather_client",
    "EnsembleWeatherData",
    "OpenWeatherMapClient",
    "WeatherGovClient",
]

