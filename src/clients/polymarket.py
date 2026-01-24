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
    condition_id: str
    tokens: List[Dict[str, str]]
    last_trade_price: float
    # Additional fields for scanner compatibility
    active: bool = True
    yes_price: float = 0.0
    no_price: float = 0.0
    category: str = ""
    liquidity_rewards_enabled: bool = False
    volume: float = 0.0

    @classmethod
    def from_gamma_event(cls, event: Dict) -> 'Market':
        """Create Market from Gamma API event response"""
        return cls(
            id=event.get('id', ''),
            question=event.get('question', ''),
            condition_id=event.get('id', ''),  # Gamma event ID maps to condition_id
            tokens=[],
            last_trade_price=float(event.get('volume', 0) or 0) / 1000 if event.get('volume') else 0.5,
            active=not event.get('closed', False) and not event.get('archived', False),
            yes_price=0.5,  # Default price
            no_price=0.5,
            category=','.join(event.get('tags', [])) or event.get('category', ''),
            liquidity_rewards_enabled=event.get('active', True),  # Approximate
            volume=float(event.get('volume', 0) or 0)
        )

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

    async def get_markets(
        self,
        category: str = None,
        filter: str = None,
        active: bool = True,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Fetch markets from Gamma API.
        
        Uses tag_id for 2026 weather markets (100381 = Weather/Climate).
        
        Args:
            category: Category filter (maps to tags in Gamma API)
            filter: Alternative filter name
            active: Only return active markets
            limit: Maximum number of markets to return
            
        Returns:
            List of market dictionaries from Gamma API
        """
        try:
            session = await self._get_session()
            
            # 2026: Use tag_id for specific weather markets
            # tag_id 100381 = Weather/Climate category
            params = {
                "limit": min(limit, 100),
                "active": str(active).lower(),
                "closed": "false",
                "archived": "false",
                "tag_id": "100381"  # Weather/Climate tag for 2026
            }
            
            async with session.get(
                f"{self.GAMMA_URL}/events",
                params=params
            ) as response:
                
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Gamma API error: {response.status} - {error_text[:200]}")
                    return []
                
                result = await response.json()
                markets = result.get('data', [])
                
                logger.info(f"Gamma API returned {len(markets)} weather markets (tag_id=100381)")
                return markets
                
        except aiohttp.ClientError as e:
            logger.error(f"Gamma API request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
            return []

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
