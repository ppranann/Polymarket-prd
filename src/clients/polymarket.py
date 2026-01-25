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
    description: str = "" # Ensures 'description' keyword works

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
    def __init__(self, api_key: str, api_secret: str, passphrase: str, **kwargs):
        self.GAMMA_URL = "https://gamma-api.polymarket.com"
        self._api_key = api_key
        self._logger = logging.getLogger(__name__)
        self._session = aiohttp.ClientSession(headers={"Authorization": f"Bearer {self._api_key}"})
        self._logger.info(f"PolymarketClient initialized")

    async def get_markets(self) -> List[Market]:
        """Fetches and filters for top weather markets."""
        url = f"{self.GAMMA_URL}/markets?active=true&closed=false&limit=500"
        try:
            async with self._session.get(url) as resp:
                if resp.status != 200: return []
                data = await resp.json()
                raw_markets = data if isinstance(data, list) else data.get('data', [])
                
                weather_markets = []
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
        await self._session.close()

# THE FIX: Added **kwargs to catch 'config' and other extra params
def create_polymarket_client(api_key, api_secret, passphrase, **kwargs):
    return PolymarketClient(api_key, api_secret, passphrase, **kwargs)

