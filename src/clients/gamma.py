"""
Gamma API Client for Polymarket Market Discovery
=================================================
Uses the Gamma API (https://gamma-api.polymarket.com) for market discovery.
Designed for filtering by tags and categories.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class GammaEvent:
    """Represents an event from Gamma API"""
    id: str
    slug: str
    question: str
    description: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    tags: List[str] = None
    categories: List[str] = None
    active: bool = True
    volume: float = 0.0
    liquidity: float = 0.0
    closed: bool = False
    archived: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "question": self.question,
            "description": self.description,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "tags": self.tags or [],
            "categories": self.categories or [],
            "active": self.active,
            "volume": self.volume,
            "liquidity": self.liquidity,
            "closed": self.closed,
            "archived": self.archived
        }


class GammaAPIClient:
    """
    Client for Polymarket Gamma API.
    
    API Documentation: https://docs.polymarket.com/#/markets/getMarkets
    Base URL: https://gamma-api.polymarket.com
    """
    
    BASE_URL = "https://gamma-api.polymarket.com"
    
    def __init__(self, timeout: int = 30):
        """
        Initialize Gamma API client.
        
        Args:
            timeout: Request timeout in seconds
        """
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session
    
    async def close(self):
        """Close the HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def get_events(
        self,
        active: bool = True,
        closed: bool = False,
        archived: bool = False,
        limit: int = 100,
        cursor: Optional[str] = None,
        tags: Optional[List[str]] = None,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fetch events from Gamma API.
        
        Args:
            active: Filter for active events
            closed: Include closed events
            archived: Include archived events
            limit: Maximum number of events to return
            cursor: Pagination cursor
            tags: Filter by tags (e.g., ['weather', 'temperature'])
            search: Search query for event titles
            
        Returns:
            Dict with 'data' (list of events) and 'cursor' (next page)
        """
        session = await self._get_session()
        
        params = {
            "limit": limit,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "archived": str(archived).lower()
        }
        
        if cursor:
            params["cursor"] = cursor
        
        if tags:
            params["tags"] = ",".join(tags)
        
        if search:
            params["search"] = search
        
        try:
            async with session.get(
                f"{self.BASE_URL}/events",
                params=params
            ) as response:
                
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Gamma API error: {response.status} - {error_text}")
                    return {"data": [], "cursor": None}
                
                return await response.json()
                
        except aiohttp.ClientError as e:
            logger.error(f"Gamma API request failed: {e}")
            return {"data": [], "cursor": None}
    
    async def get_all_events(
        self,
        tags: Optional[List[str]] = None,
        search: Optional[str] = None,
        max_events: int = 500
    ) -> List[GammaEvent]:
        """
        Fetch all events using pagination.
        
        Args:
            tags: Filter by tags
            search: Search query
            max_events: Maximum events to fetch
            
        Returns:
            List of GammaEvent objects
        """
        all_events = []
        cursor = None
        
        while len(all_events) < max_events:
            result = await self.get_events(
                tags=tags,
                search=search,
                cursor=cursor,
                limit=100
            )
            
            events_data = result.get("data", [])
            if not events_data:
                break
            
            for e in events_data:
                event = self._parse_event(e)
                all_events.append(event)
            
            cursor = result.get("cursor")
            if not cursor:
                break
        
        logger.info(f"Fetched {len(all_events)} events from Gamma API")
        return all_events[:max_events]
    
    async def get_weather_events(
        self,
        keywords: Optional[List[str]] = None
    ) -> List[GammaEvent]:
        """
        Fetch weather-related events.
        
        Args:
            keywords: Keywords to search for (e.g., ['London', 'Seoul', 'NYC', 'Rain'])
            
        Returns:
            List of weather-related GammaEvents
        """
        if keywords:
            search_query = " ".join(keywords)
        else:
            search_query = "weather temperature rain precipitation"
        
        return await self.get_all_events(
            tags=["weather", "temperature", "precipitation"],
            search=search_query,
            max_events=200
        )
    
    def _parse_event(self, data: Dict) -> GammaEvent:
        """Parse a single event from API response"""
        return GammaEvent(
            id=data.get("id", ""),
            slug=data.get("slug", ""),
            question=data.get("question", ""),
            description=data.get("description", ""),
            start_date=data.get("startDate"),
            end_date=data.get("endDate"),
            tags=data.get("tags", []),
            categories=data.get("categories", []),
            active=data.get("active", True),
            volume=float(data.get("volume", 0) or 0),
            liquidity=float(data.get("liquidity", 0) or 0),
            closed=data.get("closed", False),
            archived=data.get("archived", False)
        )
    
    def event_to_market_id(self, event: GammaEvent) -> Optional[str]:
        """
        Convert Gamma event to CLOB market ID (condition ID).
        
        The Gamma event ID can be used as the condition ID in CLOB.
        
        Args:
            event: GammaEvent object
            
        Returns:
            Market ID (condition ID) for CLOB
        """
        # Gamma event ID maps to CLOB condition_id
        if event.id:
            return event.id
        return None


def create_gamma_client(timeout: int = 30) -> GammaAPIClient:
    """
    Factory function to create a Gamma API client.
    
    Args:
        timeout: Request timeout in seconds
        
    Returns:
        Configured GammaAPIClient instance
    """
    return GammaAPIClient(timeout=timeout)

