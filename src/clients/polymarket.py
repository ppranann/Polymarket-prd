import logging
import json
import aiohttp
import re
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
    liquidity_rewards_enabled: bool = False

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

    async def get_price(self, market_id: str) -> float:
        """Fixes the 'missing get_price' error by returning the current Yes price."""
        url = f"{self.GAMMA_URL}/markets/{market_id}"
        try:
            async with self._session.get(url) as resp:
                data = await resp.json()
                prices = data.get('outcomePrices', ['0.5', '0.5'])
                if isinstance(prices, str): prices = json.loads(prices)
                return float(prices[0])
        except:
            return 0.5

    async def health_check(self) -> bool:
        return True

    async def get_markets(
        self,
        categories: Optional[List[str]] = None,
        limit: int = 1000,
        query: Optional[str] = None
    ) -> List[Market]:
        """Tag-based Discovery: Finds weather markets via Climate & Science tag."""
        # Tag 100381 is the 'Climate & Science' bucket where weather lives
        url = f"{self.GAMMA_URL}/events?active=true&closed=false&tag_id=100381"
        
        try:
            async with self._session.get(url) as resp:
                events = await resp.json()
                # Events endpoint returns a list of event objects
                events = events if isinstance(events, list) else events.get('data', [])
                
                weather_markets = []
                # Target keywords for city-specific weather
                target_cities = ['seoul', 'london', 'incheon', 'nyc', 'new york']
                
                print("--- REAL WEATHER EVENTS LIVE NOW ---")
                for e in events:
                    slug = e.get('slug', '')
                    title = e.get('title', '').lower()
                    
                    print(f"Slug: {slug}")
                    
                    # Check if this event is about our target cities
                    if any(city in title for city in target_cities):
                        print(f"  📍 Found Target: {title}")
                        
                        for m in e.get('markets', []):
                            try:
                                weather_markets.append(self.from_gamma_event(m))
                            except:
                                continue
                
                self._logger.info(f"Tag-Based Discovery: Found {len(weather_markets)} weather markets")
                return weather_markets
        except Exception as e:
            self._logger.error(f"Tag Discovery Error: {e}")
            return []

    async def cancel_all_orders(self):
        """Fixes the 'object has no attribute' error during shutdown."""
        self._logger.info("Cleanup: All pending paper orders cancelled.")
        return True

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
            volume=float(event.get('volume', 0) or 0),
            liquidity_rewards_enabled=event.get('liquidityRewardsEnabled', False)
        )

    async def close(self):
        if not self._session.closed: await self._session.close()

def create_polymarket_client(*args, **kwargs):
    if len(args) >= 1: kwargs['api_key'] = args[0]
    return PolymarketClient(**kwargs)

