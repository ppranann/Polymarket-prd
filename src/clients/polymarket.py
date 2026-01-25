import json
import logging
import asyncio
import aiohttp
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class Market:
    id: str
    question: str
    description: str = ""
    condition_id: str = ""
    tokens: List[Dict[str, str]] = None
    last_trade_price: float = 0.0
    # Additional fields for scanner compatibility
    active: bool = True
    yes_price: float = 0.0
    no_price: float = 0.0
    category: str = ""
    liquidity_rewards_enabled: bool = False
    volume: float = 0.0
    
    def __post_init__(self):
        if self.tokens is None:
            self.tokens = []

@dataclass
class Order:
    order_id: str
    market_id: str
    price: float
    side: str

@dataclass
class Position:
    asset_id: str
    size: float
    avg_price: float

class PolymarketClient:
    """
    Polymarket CLOB Client for market data and trading.
    
    Uses Gamma API for market discovery and CLOB for trading.
    """
    
    CLOB_URL = "https://clob.polymarket.com"
    GAMMA_URL = "https://gamma-api.polymarket.com"
    
    def __init__(self, config=None, api_key: str = None):
        self.config = config
        self.api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None
        logger.info(f"PolymarketClient initialized with API key: {'set' if api_key else 'not set'}")
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    async def close(self):
        """Close the HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def health_check(self) -> bool:
        """Check if Polymarket APIs are reachable"""
        try:
            session = await self._get_session()
            # Quick check against Gamma API (no auth needed)
            async with session.get(f"{self.GAMMA_URL}/events?limit=1") as resp:
                return resp.status == 200
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    async def get_markets(self):
        """Fetches a broad list of active markets for the scanner to filter."""
        # We switch to /markets to get flat, easy-to-read data
        url = f"{self.GAMMA_URL}/markets?active=true&closed=false&limit=100&order=volume&ascending=false"
        try:
            async with self._session.get(url) as resp:
                data = await resp.json()
                # Handle both list and dict responses
                raw_markets = data if isinstance(data, list) else data.get('data', [])
                logger.info(f"Gamma API /markets returned {len(raw_markets)} markets")
                return [self.from_gamma_event(m) for m in raw_markets]
        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
            return []

    @classmethod
    def from_gamma_event(cls, event: dict) -> 'Market':
        # 1. Parse prices safely
        raw_prices = event.get('outcomePrices', '["0.5", "0.5"]')
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
        yes_p = float(prices[0]) if prices else 0.5

        # 2. Extract title
        title = event.get('question') or event.get('title') or "Unknown"

        # 3. FIX: Return a Market object, not a Client
        return Market(
            id=event.get('id', ''),
            question=title,
            description=event.get('description', ''),
            condition_id=event.get('conditionId', ''),
            yes_price=yes_p,
            no_price=1.0 - yes_p,
            last_trade_price=yes_p,
            active=not event.get('closed', False),
            category=event.get('category', 'general'),
            volume=float(event.get('volume', 0) or 0)
        )

    async def get_market(self, market_id: str) -> Optional[Dict[str, Any]]:
        """Get a single market by ID"""
        try:
            session = await self._get_session()
            
            async with session.get(
                f"{self.GAMMA_URL}/events/{market_id}"
            ) as response:
                if response.status == 200:
                    return await response.json()
                return None
                
        except Exception as e:
            logger.error(f"Error fetching market {market_id}: {e}")
            return None

    async def get_price(self, market_id: str) -> float:
        """
        Get current price for a market from CLOB.
        
        For now, returns a default price since we need proper CLOB auth.
        """
        try:
            if not self.api_key:
                logger.debug(f"No API key for CLOB, using default price for {market_id[:8]}")
                return 0.5  # Default price
            
            session = await self._get_session()
            
            # Try CLOB get_price endpoint
            async with session.get(
                f"{self.CLOB_URL}/prices/{market_id}",
                headers={"Authorization": f"Bearer {self.api_key}"}
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return float(data.get('price', 0.5))
                
                # Fallback to default price
                return 0.5
                
        except Exception as e:
            logger.warning(f"Could not fetch price for {market_id}: {e}")
            return 0.5

    async def get_balance(self, asset_id: str) -> float:
        """Get token balance (placeholder - needs proper auth)"""
        return 0.0

    async def get_order_book(self, market_id: str) -> Dict[str, Any]:
        """Get order book for a market (placeholder - needs proper auth)"""
        return {"bids": [], "asks": []}

def create_polymarket_client(
    config=None,
    secrets=None,
    paper_trading: bool = False
) -> PolymarketClient:
    """
    Create a PolymarketClient.
    
    Args:
        config: Bot configuration
        secrets: Secrets dict with API keys
        paper_trading: Whether in paper trading mode
        
    Returns:
        Configured PolymarketClient
    """
    api_key = secrets.polymarket_api_key if secrets else None
    
    return PolymarketClient(
        config=config,
        api_key=api_key
    )
