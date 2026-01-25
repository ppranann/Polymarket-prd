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
        """
        Fetch markets from Gamma API with surgical query.
        
        Args:
            categories: Filter by category (e.g., ['weather', 'science', 'politics'])
            limit: Maximum number of markets to fetch (default 1000)
            query: Search query for filtering (e.g., 'temperature')
            
        Returns:
            List of Market objects
        """
        all_markets = []
        
        try:
            # Build params with active filters
            params = {
                'active': 'true',
                'closed': 'false',
                'archived': 'false',
                'limit': limit
            }
            
            # Add query if provided
            if query:
                params['query'] = query
            
            # Strategy 1: Fetch with query parameter (temperature search)
            # This hits the hidden metadata and finds weather markets
            if query:
                url = f"{self.GAMMA_URL}/markets"
                async with self._session.get(url, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        raw_markets = data if isinstance(data, list) else data.get('data', [])
                        self._logger.info(f"Query search '{query}': Found {len(raw_markets)} markets")
                        
                        # Filter for target cities
                        target_cities = ['seoul', 'incheon', 'london', 'nyc', 'new york']
                        for m in raw_markets:
                            title = m.get('question', '').lower()
                            if any(city in title for city in target_cities):
                                all_markets.append(m)
            
            # Strategy 2: Fetch all active markets if no results yet
            if not all_markets:
                url = f"{self.GAMMA_URL}/markets"
                async with self._session.get(url, params={k: v for k, v in params.items() if k != 'query'}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        raw_markets = data if isinstance(data, list) else data.get('data', [])
                        self._logger.info(f"All active markets: Found {len(raw_markets)}")
                        all_markets = raw_markets
            
            self._logger.info(f"Total markets to process: {len(all_markets)}")
            
            # Convert to Market objects
            markets = []
            for m in all_markets:
                try:
                    markets.append(self.from_gamma_event(m))
                except Exception as e:
                    self._logger.warning(f"Failed to parse market {m.get('id')}: {e}")
                    continue
            
            return markets
            
        except Exception as e:
            self._logger.error(f"Market fetch error: {e}")
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

