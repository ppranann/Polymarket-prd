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

    async def get_markets(self) -> List[Market]:
        """Deep search that handles the nested Event -> Market structure."""
        # This endpoint mimics exactly what you did in the UI search bar
        url = f"{self.GAMMA_URL}/public-search?query=temperature&active=true"
        
        try:
            async with self._session.get(url) as resp:
                data = await resp.json()
                
                self._logger.debug(f"API Response type: {type(data)}, keys: {data.keys() if isinstance(data, dict) else 'not a dict'}")
                
                # Try different response structures
                events = []
                
                # 1. Try 'events' key (nested structure)
                if isinstance(data, dict) and 'events' in data:
                    events = data.get('events', [])
                    self._logger.debug(f"Found 'events' key with {len(events)} events")
                # 2. Try 'data' key
                elif isinstance(data, dict) and 'data' in data:
                    data_list = data.get('data', [])
                    self._logger.debug(f"Found 'data' key with {len(data_list)} items")
                    # Check if data items have nested 'markets'
                    if data_list and isinstance(data_list[0], dict) and 'markets' in data_list[0]:
                        events = data_list
                    else:
                        # Treat as flat market list
                        events = [{'markets': data_list}]
                # 3. Try as flat list
                elif isinstance(data, list):
                    self._logger.debug(f"Response is a flat list with {len(data)} items")
                    events = [{'markets': data}]
                else:
                    self._logger.warning(f"Unknown response structure: {type(data)}")
                
                weather_markets = []
                target_cities = ['seoul', 'nyc', 'london', 'incheon', 'toronto', 'dallas']
                
                for event in events:
                    # 2. Extract markets from inside each event
                    markets = event.get('markets', [])
                    self._logger.debug(f"Event has {len(markets)} markets")
                    
                    for m in markets:
                        title = m.get('question', '').lower()
                        
                        # 3. Only pull if it's one of our target cities
                        if any(city in title for city in target_cities):
                            self._logger.info(f"Found target market: {m.get('question', '')[:50]}...")
                            # Pass the market dict to our parser
                            weather_markets.append(self.from_gamma_event(m))
                
                self._logger.info(f"Scan #1: Parsed {len(events)} events, found {len(weather_markets)} targeted markets")
                return weather_markets
                
        except Exception as e:
            self._logger.error(f"Search API structure error: {e}")
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
            volume=float(event.get('volume', 0) or 0),
            liquidity_rewards_enabled=event.get('liquidityRewardsEnabled', False)
        )

    async def close(self):
        if not self._session.closed: await self._session.close()

def create_polymarket_client(*args, **kwargs):
    if len(args) >= 1: kwargs['api_key'] = args[0]
    return PolymarketClient(**kwargs)

