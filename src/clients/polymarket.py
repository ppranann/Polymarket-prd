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

# Mock order class for paper trading
class MockOrder:
    """Mock order object for paper trading mode."""
    def __init__(self, order_id: str, market_id: str = "", side: str = "", price: float = 0, size: float = 0):
        self.order_id = order_id
        self.market_id = market_id
        self.side = side
        self.price = price
        self.size = size
        self.status = "SUCCESS"

    def __getitem__(self, key):
        """Support dict-style access for compatibility with executor."""
        # Map camelCase API keys to snake_case attributes
        key_mapping = {
            "orderID": "order_id",
            "order_id": "order_id",
            "marketID": "market_id",
            "market_id": "market_id",
            "side": "side",
            "price": "price",
            "size": "size",
            "status": "status"
        }
        attr_name = key_mapping.get(key, key)
        if hasattr(self, attr_name):
            return getattr(self, attr_name)
        raise KeyError(f"MockOrder has no key '{key}'")

    def get(self, key, default=None):
        """Support dict.get() method for compatibility."""
        try:
            return self[key]
        except KeyError:
            return default

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
        Slug-based Discovery: Fetches weather markets by predictable slug pattern.
        
        Strategy:
        - Polymarket creates weather markets with predictable naming pattern
        - Pattern: highest-temperature-in-{city}-on-{month}-{day}
        - Fetch each slug directly via /events/slug/{slug}
        - Extract nested markets and filter for active buckets
        
        This approach is MORE RELIABLE than Tag 84 which returns wrong results.
        """
        from datetime import datetime, timedelta
        
        # Target cities for weather markets
        target_cities = ['london', 'seoul', 'incheon', 'new-york', 'nyc']
        
        # Generate slugs for today + next 2 days
        slugs_to_fetch = []
        for i in range(3):  # Today, tomorrow, day after
            date = datetime.utcnow() + timedelta(days=i)
            date_str = date.strftime('%B-%-d-%Y').lower()  # "february-7-2026"
            for city in target_cities:
                slugs_to_fetch.append(f"highest-temperature-in-{city}-on-{date_str}")
        
        weather_markets = []
        fetched_count = 0
        
        # Use logger instead of print to save storage
        self._logger.info("Fetching weather markets via slug-based discovery...")
        
        for slug in slugs_to_fetch:
            try:
                url = f"{self.GAMMA_URL}/events/slug/{slug}"
                async with self._session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    
                    event = await resp.json()
                    fetched_count += 1
                    
                    city = slug.split('-in-')[1].split('-on-')[0] if '-in-' in slug else 'unknown'
                    
                    # Iterate through NESTED markets array
                    for market in event.get('markets', []):
                        # Skip if not accepting orders or already closed
                        if not market.get('acceptingOrders', True):
                            continue
                        if market.get('closed', False):
                            continue
                        
                        try:
                            # Convert gamma market to our Market format
                            converted = self._convert_gamma_market(market, event)
                            weather_markets.append(converted)
                        except Exception as e:
                            self._logger.warning(f"Failed to convert market: {e}")
                            continue
                            
            except Exception as e:
                self._logger.debug(f"Slug {slug} not found or error: {e}")
                continue
        
        self._logger.info(f"Discovery complete: {len(weather_markets)} temperature buckets found")
        return weather_markets

    def _convert_gamma_market(self, market: Dict, event: Dict) -> Market:
        """
        Convert a Gamma market object to our Market format.
        
        Args:
            market: Individual market from event.markets array
            event: Parent event object for context
            
        Returns:
            Market object
        """
        # Parse outcome prices
        raw_prices = market.get('outcomePrices', '["0.5", "0.5"]')
        if isinstance(raw_prices, str):
            try:
                prices = json.loads(raw_prices)
            except:
                prices = [0.5, 0.5]
        else:
            prices = raw_prices
        
        yes_p = float(prices[0]) if (prices and len(prices) > 0) else 0.5
        
        # Build descriptive question from groupItemTitle
        group_title = market.get('groupItemTitle', '')
        event_title = event.get('title', 'Unknown')
        question = f"{event_title} - {group_title}" if group_title else event_title
        
        return Market(
            id=str(market.get('id', '')),
            question=question,
            condition_id=market.get('conditionId', event.get('id', '')),
            yes_price=yes_p,
            no_price=round(1.0 - yes_p, 4),
            last_trade_price=market.get('lastTradePrice', yes_p),
            active=market.get('acceptingOrders', True) and not market.get('closed', False),
            category='weather',
            volume=float(market.get('volume', 0) or 0),
            description=market.get('description', event.get('description', '')),
            liquidity_rewards_enabled=market.get('liquidityRewardsEnabled', False)
        )

    async def cancel_all_orders(self, *args, **kwargs):
        """Fixes the 'object has no attribute' error during shutdown."""
        self._logger.info("Cleanup: All pending paper orders cancelled.")
        return True

    async def place_limit_order(self, market_id: str, side: str, price: float, size: float, **kwargs):
        """
        Mock hand for PAPER trading. **kwargs catches 'post_only' and stops the crash.
        Returns MockOrder object instead of dict for proper attribute access.
        """
        self._logger.debug(f"PAPER ORDER: {side.upper()} {size} @ ${price} on {market_id}")
        return MockOrder(
            order_id=f"mock-paper-{market_id[:8]}",
            market_id=market_id,
            side=side,
            price=price,
            size=size
        )

    async def get_order_book(self, token_id: str):
        """Mock order book to allow farming logic to calculate midpoints."""
        # Returns a dummy spread so the farming logic doesn't crash
        return {"bids": [{"price": "0.50", "size": "100"}], "asks": [{"price": "0.52", "size": "100"}]}

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order. For paper trading, always returns success."""
        self._logger.debug(f"PAPER CANCEL: Order {order_id}")
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

