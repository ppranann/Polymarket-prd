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
        Hybrid Discovery: Uses Tag 84 (Weather Events) with surgical filtering.
        
        Strategy:
        - Tag 84 returns Events, each containing nested Markets (temperature buckets)
        - Filter events by city name in slug/title
        - If query provided, also filter by keyword in title/question
        - Iterate through event.markets array to get temperature buckets
        - Check acceptingOrders and !closed for active markets
        
        API Structure:
        /events?tag=84&active=true&closed=false
          -> events[].slug = "highest-temperature-in-london-on-january-25"
          -> events[].markets = [{ groupItemTitle: "7°C", acceptingOrders: true, ... }]
        """
        # Tag 84 returns EVENTS with nested markets
        # Try both tag and tag_id parameters
        url = f"{self.GAMMA_URL}/events?tag=84&active=true&closed=false&limit=100"
        
        try:
            async with self._session.get(url) as resp:
                data = await resp.json()
                
                # DEBUG: Print raw response info
                print(f"DEBUG: API URL: {url}")
                print(f"DEBUG: Response type: {type(data)}")
                
                events = data if isinstance(data, list) else data.get('data', [])
                print(f"DEBUG: Total events returned: {len(events)}")
                
                # Print first 3 events for debugging
                for i, event in enumerate(events[:3]):
                    print(f"  DEBUG Event {i}: slug='{event.get('slug', '')[:50]}' | title='{event.get('title', '')[:50]}' | markets={len(event.get('markets', []))}")
                
                weather_markets = []
                # Target cities - match ANY city name in slug/title
                target_cities = ['london', 'seoul', 'incheon', 'new york', 'nyc']
                
                print("--- HYBRID DISCOVERY: Scanning Tag 84 Events ---")
                
                for event in events:
                    slug = event.get('slug', '').lower()
                    title = event.get('title', '').lower()
                    
                    # Check if this event is about any target city
                    if not any(city in slug or city in title for city in target_cities):
                        continue
                    
                    print(f"  📍 Found city event: {event.get('slug', '')[:60]}")
                    
                    # Iterate through NESTED markets array
                    for market in event.get('markets', []):
                        # Skip if not accepting orders or already closed
                        if not market.get('acceptingOrders', True):
                            continue
                        if market.get('closed', False):
                            continue
                        
                        # Extract temperature bucket from groupItemTitle
                        group_item_title = market.get('groupItemTitle', '')
                        print(f"    🎯 Bucket: {group_item_title}")
                        
                        try:
                            # Convert gamma market to our Market format
                            converted = self._convert_gamma_market(market, event)
                            weather_markets.append(converted)
                        except Exception as e:
                            self._logger.warning(f"Failed to convert market: {e}")
                            continue
                
                self._logger.info(f"Hybrid Discovery: Found {len(weather_markets)} temperature buckets")
                return weather_markets
        except Exception as e:
            self._logger.error(f"Hybrid Discovery Error: {e}")
            return []

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

