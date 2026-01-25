import logging
import json
import aiohttp
from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class Market:
    id: str
    question: str
    condition_id: str
    yes_price: float
    no_price: float
    last_trade_price: float
    active: bool
    category: str
    volume: float
    description: str = ""

@dataclass
class Order:
    id: str
    market_id: str
    side: str
    price: float
    size: float
    status: str = "open"

@dataclass
class Position:
    market_id: str
    size: float
    avg_price: float

class PolymarketClient:
    def __init__(self, **kwargs):
        # We use .get() to pull keys from kwargs, making it bulletproof against main.py
        self._api_key = kwargs.get('api_key')
        self._api_secret = kwargs.get('api_secret')
        self._passphrase = kwargs.get('passphrase')
        
        self.GAMMA_URL = "https://gamma-api.polymarket.com"
        self._logger = logging.getLogger(__name__)
        
        # Initialize session with auth if keys exist
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
            
        self._session = aiohttp.ClientSession(headers=headers)
        self._logger.info("PolymarketClient initialized")

    async def get_markets(self) -> List[Market]:
        """Fetches and strictly filters for weather markets."""
        url = f"{self.GAMMA_URL}/markets?active=true&closed=false&limit=500"
        
        try:
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    self._logger.error(f"Gamma API error: {resp.status}")
                    return []
                
                data = await resp.json()
                raw_markets = data if isinstance(data, list) else data.get('data', [])
                
                weather_markets = []
                # Target signals for Sunday Jan 25: Seoul, NYC, London, Toronto
                weather_keywords = ['temperature', 'weather', 'rain', 'snow', '°c', '°f', 'precip']
                
                for m in raw_markets:
                    search_text = f"{m.get('question', '')} {m.get('title', '')} {m.get('description', '')}".lower()
                    
                    if any(kw in search_text for kw in weather_keywords):
                        try:
                            weather_markets.append(self.from_gamma_event(m))
                        except Exception as parse_error:
                            self._logger.debug(f"Skipping market: {parse_error}")
                
                self._logger.info(f"Filtered {len(raw_markets)} total markets down to {len(weather_markets)} weather events")
                return weather_markets
        except Exception as e:
            self._logger.error(f"Error fetching/filtering markets: {e}")
            return []

    def from_gamma_event(self, event: Dict) -> Market:
        raw_prices = event.get('outcomePrices', '["0.5", "0.5"]')
        if isinstance(raw_prices, str):
            try:
                prices = json.loads(raw_prices)
            except:
                prices = [0.5, 0.5]
        else:
            prices = raw_prices
        
        yes_p = float(prices[0]) if (prices and len(prices) > 0) else 0.5

        return Market(
            id=str(event.get('id', '')),
            question=event.get('question', '') or event.get('title', 'Unknown'),
            description=event.get('description', ''),
            condition_id=event.get('conditionId', ''),
            yes_price=yes_p,
            no_price=round(1.0 - yes_p, 4),
            last_trade_price=yes_p,
            active=not event.get('closed', False),
            category=str(event.get('category', 'weather')),
            volume=float(event.get('volume', 0) or 0)
        )

    async def close(self):
        await self._session.close()

# THE FIX: This function now catches EVERYTHING main.py throws at it
def create_polymarket_client(*args, **kwargs):
    # If main.py sends positional args (api_key, secret, etc.), we map them
    if len(args) >= 1: kwargs['api_key'] = args[0]
    if len(args) >= 2: kwargs['api_secret'] = args[1]
    if len(args) >= 3: kwargs['passphrase'] = args[2]
    
    return PolymarketClient(**kwargs)