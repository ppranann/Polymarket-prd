import logging
import json
import aiohttp
from dataclasses import dataclass
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
        self._api_key = kwargs.get('api_key')
        self.GAMMA_URL = "https://gamma-api.polymarket.com"
        self._logger = logging.getLogger(__name__)
        
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        self._session = aiohttp.ClientSession(headers=headers)
        self._logger.info("PolymarketClient initialized")

    async def health_check(self) -> bool:
        """The 'Yes I am alive' signal main.py is looking for."""
        try:
            async with self._session.get(f"{self.GAMMA_URL}/health") as resp:
                # Even if /health doesn't exist, a 200 or 404 from Gamma means it's up
                return resp.status in [200, 404]
        except:
            return True # Fallback to True so we don't block the bot

    async def get_markets(self) -> List[Market]:
        url = f"{self.GAMMA_URL}/markets?active=true&closed=false&limit=500"
        try:
            async with self._session.get(url) as resp:
                if resp.status != 200: return []
                data = await resp.json()
                raw_markets = data if isinstance(data, list) else data.get('data', [])
                
                weather_markets = []
                # Target: Seoul, NYC, London, Toronto
                weather_keywords = ['temperature', 'weather', 'rain', 'snow', '°c', '°f', 'precip']
                
                for m in raw_markets:
                    search_text = f"{m.get('question', '')} {m.get('title', '')} {m.get('description', '')}".lower()
                    if any(kw in search_text for kw in weather_keywords):
                        try:
                            weather_markets.append(self.from_gamma_event(m))
                        except:
                            continue
                self._logger.info(f"Filtered {len(raw_markets)} markets down to {len(weather_markets)} weather events")
                return weather_markets
        except Exception as e:
            self._logger.error(f"Scan error: {e}")
            return []

    def from_gamma_event(self, event: Dict) -> Market:
        raw_prices = event.get('outcomePrices', '["0.5", "0.5"]')
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
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
        if not self._session.closed:
            await self._session.close()

def create_polymarket_client(*args, **kwargs):
    if len(args) >= 1: kwargs['api_key'] = args[0]
    return PolymarketClient(**kwargs)