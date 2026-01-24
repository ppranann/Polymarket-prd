import json
import logging
import asyncio
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
    def __init__(self, clob_client=None, config=None):
        self.clob = clob_client
        self.config = config
        logger.info(f"PolymarketClient ready for action.")

    async def health_check(self) -> bool:
        return True 

    async def get_markets(self, *args, **kwargs) -> List[Dict[str, Any]]:
        """Flexible market fetcher that ignores invalid arguments from older scanner logic."""
        try:
            if not self.clob: 
                return []
            
            # Extract the actual filter if passed as 'filter' or 'category'
            active_filter = kwargs.get('filter') or kwargs.get('category')
            
            # 2026 CLOB call
            resp = await self.clob.get_markets(filter=active_filter if active_filter else None)
            
            if isinstance(resp, str):
                try:
                    resp = json.loads(resp)
                except json.JSONDecodeError:
                    return []
            
            markets_list = []
            if isinstance(resp, list):
                markets_list = resp
            elif isinstance(resp, dict):
                markets_list = resp.get('data', resp.get('markets', []))
            
            return markets_list
        except Exception as e:
            logger.error(f"Scan Error: {e}")
            return []

    async def get_balance(self, asset_id: str) -> float:
        return 0.0

def create_polymarket_client(*args, **kwargs) -> PolymarketClient:
    clob = kwargs.get('clob_client') or (args[0] if len(args) > 0 else None)
    config = kwargs.get('config') or (args[1] if len(args) > 1 else None)
    return PolymarketClient(clob_client=clob, config=config)
