"""
Market Scanner Module
=====================
Scans Polymarket for weather markets with:
- Liquidity Rewards ("Blue Clock" indicator)
- YES price < $0.15
- Active status

Uses get_price endpoint to avoid stale data issues.

Key Features:
- Lazy API calls to Tomorrow.io (only when needed)
- 15-minute cache for weather data
- Real-time price polling (free, no rate limits)
- Smart filtering for snipe-worthy markets
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .config import BotConfig
from .clients.polymarket import Market, PolymarketClient

logger = logging.getLogger(__name__)


@dataclass
class ScannedMarket:
    """Market with cached weather data tracking"""
    market: Market
    last_weather_scan: Optional[datetime] = None
    weather_data: Optional[Dict] = None
    fair_price: Optional[float] = None
    alpha: Optional[float] = None


@dataclass
class ScanResult:
    """Result of a market scan"""
    scanned_at: datetime
    markets_found: int
    qualifying_markets: List[Market]
    weather_scanned_count: int
    errors: List[str]
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "scanned_at": self.scanned_at.isoformat(),
            "markets_found": self.markets_found,
            "qualifying_markets": [
                {
                    "id": m.id,
                    "question": m.question,
                    "yes_price": m.yes_price,
                    "liquidity_rewards": m.liquidity_rewards_enabled,
                    "category": m.category
                }
                for m in self.qualifying_markets
            ],
            "weather_scanned_count": self.weather_scanned_count,
            "errors": self.errors
        }


class MarketScanner:
    """
    Scanner for finding trading opportunities on Polymarket.
    
    Optimizations:
    - Poll prices every 30 seconds (free, no rate limits)
    - Only call Tomorrow.io for markets in snipe range (<$0.15)
    - Cache weather data for 15 minutes per market
    - Batch weather API calls to reduce calls
    
    Filters for:
    - Weather-related markets
    - YES price below threshold (default: $0.15)
    - Active Liquidity Rewards (Blue Clock)
    - Active trading status
    """
    
    def __init__(
        self,
        config: BotConfig,
        polymarket_client: PolymarketClient
    ):
        """
        Initialize market scanner.
        
        Args:
            config: Bot configuration
            polymarket_client: Polymarket CLOB client
        """
        self.config = config
        self.client = polymarket_client
        self.scanner_config = config.scanner
        
        # Cache for weather data (market_id -> ScannedMarket)
        self._market_cache: Dict[str, ScannedMarket] = {}
        
        # Track scan history
        self._last_scan: Optional[ScanResult] = None
        self._scan_count = 0
        
        # Configurable intervals
        self._price_poll_interval = 30  # Poll prices every 30 seconds (free)
        self._weather_cache_ttl = 15 * 60  # 15 minutes cache for weather
        self._snipe_price_threshold = config.get('scanner', {}).get('snipe_price_threshold', 0.15)
    
    async def scan(
        self,
        categories: Optional[List[str]] = None,
        price_threshold: Optional[float] = None,
        require_liquidity_rewards: bool = True,
        fetch_weather: bool = True
    ) -> ScanResult:
        """
        Scan for qualifying weather markets.
        
        Strategy:
        1. Fetch prices every 30 seconds (free, no limit)
        2. Only call Tomorrow.io if market < $0.15 AND cached > 15 min old
        
        Args:
            categories: Market categories to scan (uses config if not provided)
            price_threshold: Price threshold (uses config if not provided)
            require_liquidity_rewards: Only return Blue Clock markets
            fetch_weather: Whether to fetch weather data for qualifying markets
            
        Returns:
            ScanResult with qualifying markets
        """
        cats = categories or self.scanner_config.categories
        threshold = price_threshold or self.scanner_config.price_threshold
        
        errors = []
        all_markets = []
        qualifying_markets = []
        weather_scanned = 0
        
        self._scan_count += 1
        scan_time = datetime.now()
        
        logger.info(f"Starting market scan #{self._scan_count}")
        logger.info(f"Categories: {cats}, Price threshold: ${threshold:.4f}")
        
        try:
            # Scan each category
            for category in cats:
                try:
                    markets = await self.client.get_markets(
                        category=category,
                        active=True,
                        limit=100
                    )
                    all_markets.extend(markets)
                    logger.debug(f"Found {len(markets)} markets in {category}")
                    
                except Exception as e:
                    error = f"Error scanning category '{category}': {str(e)}"
                    logger.error(error)
                    errors.append(error)
            
            # Get current prices for all markets (free polling)
            prices = await self._get_current_prices(all_markets)
            
            # Filter for qualifying markets
            for market in all_markets:
                current_price = prices.get(market.id, market.yes_price)
                market.yes_price = current_price  # Update with latest price
                
                if await self._qualifies(
                    market,
                    threshold=threshold,
                    require_rewards=require_liquidity_rewards
                ):
                    qualifying_markets.append(market)
                    
                    # LAZY WEATHER FETCHING: Only fetch if needed
                    if fetch_weather and current_price < self._snipe_price_threshold:
                        needs_fetch = self._needs_weather_fetch(market.id)
                        if needs_fetch:
                            logger.debug(f"Weather fetch needed for {market.id[:8]}...")
            
            logger.info(
                f"Scan complete: {len(all_markets)} total, "
                f"{len(qualifying_markets)} qualifying"
            )
            
        except Exception as e:
            error = f"Critical scan error: {str(e)}"
            logger.error(error)
            errors.append(error)
        
        result = ScanResult(
            scanned_at=scan_time,
            markets_found=len(all_markets),
            qualifying_markets=qualifying_markets,
            weather_scanned_count=weather_scanned,
            errors=errors
        )
        
        self._last_scan = result
        
        return result
    
    async def _get_current_prices(self, markets: List[Market]) -> Dict[str, float]:
        """
        Get current prices for all markets.
        
        This is free to call frequently (no rate limits on get_price).
        
        Args:
            markets: List of markets to get prices for
            
        Returns:
            Dictionary of market_id -> current price
        """
        prices = {}
        
        # Batch fetch prices (can parallelize)
        tasks = [self._get_single_price(m) for m in markets]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for market, result in zip(markets, results):
            if isinstance(result, Exception):
                logger.warning(f"Price fetch error for {market.id}: {result}")
                prices[market.id] = market.yes_price  # Use cached
            else:
                prices[market.id] = result
        
        return prices
    
    async def _get_single_price(self, market: Market) -> float:
        """Get price for a single market"""
        try:
            return await self.client.get_price(market_id=market.id)
        except Exception as e:
            logger.warning(f"Could not get price for {market.id}: {e}")
            return market.yes_price  # Fallback to cached
    
    def _needs_weather_fetch(self, market_id: str) -> bool:
        """
        Check if weather data needs to be fetched for a market.
        
        Rules:
        1. Never fetched before -> YES
        2. Fetched > 15 minutes ago -> YES
        3. Fetched < 15 minutes ago -> NO (use cache)
        
        Args:
            market_id: Market to check
            
        Returns:
            True if weather fetch is needed
        """
        if market_id not in self._market_cache:
            return True
        
        cached = self._market_cache[market_id]
        
        if cached.last_weather_scan is None:
            return True
        
        age = datetime.now() - cached.last_weather_scan
        return age.total_seconds() > self._weather_cache_ttl
    
    async def get_weather_for_market(
        self,
        market: Market,
        weather_data_provider: callable
    ) -> Dict[str, Any]:
        """
        Get weather data for a market with caching.
        
        This implements the "Lazy API" pattern:
        - Only calls Tomorrow.io when necessary
        - Caches results for 15 minutes
        
        Args:
            market: Market to get weather for
            weather_data_provider: Async function to fetch weather
            
        Returns:
            Weather data dictionary
        """
        # Check if we need to fetch
        if not self._needs_weather_fetch(market.id):
            logger.debug(f"Using cached weather for {market.id[:8]}...")
            cached = self._market_cache.get(market.id)
            return cached.weather_data if cached else None
        
        # Fetch fresh weather data
        try:
            logger.info(f"Fetching fresh weather for {market.id[:8]}...")
            weather = await weather_data_provider(market)
            
            # Update cache
            if market.id not in self._market_cache:
                self._market_cache[market.id] = ScannedMarket(market=market)
            
            cached = self._market_cache[market.id]
            cached.last_weather_scan = datetime.now()
            cached.weather_data = weather
            
            return weather
            
        except Exception as e:
            logger.error(f"Weather fetch failed for {market.id}: {e}")
            # Return cached if available
            cached = self._market_cache.get(market.id)
            return cached.weather_data if cached else None
    
    async def batch_get_weather_data(
        self,
        markets: List[Market],
        weather_data_provider: callable
    ) -> Dict[str, Dict[str, Any]]:
        """
        Batch fetch weather data for multiple markets.
        
        Only fetches for markets that need updates.
        Useful for end-of-day analysis or when starting the bot.
        
        Args:
            markets: List of markets to get weather for
            weather_data_provider: Async function to fetch weather
            
        Returns:
            Dictionary of market_id -> weather data
        """
        # Filter to only markets that need fetching
        markets_needing_fetch = [
            m for m in markets 
            if self._needs_weather_fetch(m.id)
        ]
        
        if not markets_needing_fetch:
            logger.debug("No markets need weather updates (using cache)")
            return {
                mid: data.weather_data 
                for mid, data in self._market_cache.items()
                if data.weather_data
            }
        
        logger.info(f"Batch fetching weather for {len(markets_needing_fetch)} markets")
        
        # Fetch in parallel (but be respectful of rate limits)
        tasks = [
            self.get_weather_for_market(m, weather_data_provider)
            for m in markets_needing_fetch
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        weather_data = {}
        for market, result in zip(markets_needing_fetch, results):
            if not isinstance(result, Exception):
                weather_data[market.id] = result
        
        return weather_data
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get statistics about the weather cache"""
        total_cached = len(self._market_cache)
        stale_entries = sum(
            1 for m in self._market_cache.values()
            if self._needs_weather_fetch(m.market.id)
        )
        
        return {
            "total_cached": total_cached,
            "stale_entries": stale_entries,
            "fresh_entries": total_cached - stale_entries,
            "cache_ttl_seconds": self._weather_cache_ttl,
            "price_poll_interval": self._price_poll_interval
        }
    
    def clear_cache(self, market_id: Optional[str] = None):
        """
        Clear the weather cache.
        
        Args:
            market_id: Specific market to clear, or None for all
        """
        if market_id:
            if market_id in self._market_cache:
                del self._market_cache[market_id]
                logger.info(f"Cleared cache for {market_id[:8]}...")
        else:
            count = len(self._market_cache)
            self._market_cache.clear()
            logger.info(f"Cleared cache for {count} markets")
    
    async def _qualifies(
        self,
        market: Market,
        threshold: float,
        require_rewards: bool
    ) -> bool:
        """
        Check if a market qualifies for trading.
        
        Args:
            market: Market to check
            threshold: Price threshold
            require_rewards: Require Liquidity Rewards
            
        Returns:
            True if market qualifies
        """
        # Must be active
        if not market.active:
            return False
        
        # Must be below price threshold
        if market.yes_price >= threshold:
            return False
        
        # Must have Liquidity Rewards if required
        if require_rewards and not market.liquidity_rewards_enabled:
            return False
        
        # Check for weather-related keywords in category
        category_lower = market.category.lower()
        weather_keywords = ['weather', 'temperature', 'precip', 'rain', 'snow', 'storm']
        
        if not any(kw in category_lower for kw in weather_keywords):
            # Also check the question
            question_lower = market.question.lower()
            if not any(kw in question_lower for kw in weather_keywords):
                return False
        
        return True
    
    async def get_market_prices(
        self,
        markets: List[Market]
    ) -> Dict[str, float]:
        """
        Get current prices for a list of markets.
        
        Uses get_price endpoint to avoid stale data.
        
        Args:
            markets: List of markets to get prices for
            
        Returns:
            Dictionary of market_id -> current price
        """
        return await self._get_current_prices(markets)
    
    async def find_blue_clock_markets(
        self,
        min_price: float = 0.01,
        max_price: float = None
    ) -> List[Market]:
        """
        Find markets with active Liquidity Rewards (Blue Clock).
        
        Args:
            min_price: Minimum price
            max_price: Maximum price (defaults to snipe_price_threshold from config)
            
        Returns:
            List of qualifying markets
        """
        # Use config default if not specified
        if max_price is None:
            max_price = self.scanner_config.snipe_price_threshold
        result = await self.scan(
            price_threshold=max_price,
            require_liquidity_rewards=True
        )
        
        # Filter by minimum price
        return [
            m for m in result.qualifying_markets
            if m.yes_price >= min_price
        ]
    
    async def find_underpriced_markets(
        self,
        weather_data_provider: callable = None,
        fair_price_calculator: callable = None,
        alpha_threshold: float = 0.20
    ) -> List[Dict[str, Any]]:
        """
        Find markets that are underpriced based on weather data.
        
        Uses lazy loading - only fetches weather for snipe-worthy markets.
        
        Args:
            weather_data_provider: Async function to get weather data
            fair_price_calculator: Calculator for fair prices
            alpha_threshold: Minimum alpha to qualify
            
        Returns:
            List of underpriced market info
        """
        result = await self.scan()
        underpriced = []
        
        if not weather_data_provider or not fair_price_calculator:
            logger.warning("No weather data provider or calculator provided")
            return underpriced
        
        # Get current prices
        prices = await self.get_market_prices(result.qualifying_markets)
        
        for market in result.qualifying_markets:
            market_id = market.id
            current_price = prices.get(market_id, market.yes_price)
            
            try:
                # LAZY: Only get weather data if needed
                # (Check cache first)
                weather = await self.get_weather_for_market(
                    market, 
                    weather_data_provider
                )
                
                if not weather:
                    continue
                
                # Calculate fair price
                calculation = fair_price_calculator.calculate_fair_price_from_weather(
                    weather_data=weather,
                    market_condition=market.question,
                    market_price=current_price
                )
                
                # Check if underpriced
                if calculation.alpha > alpha_threshold:
                    underpriced.append({
                        "market": market,
                        "current_price": current_price,
                        "fair_price": calculation.fair_price,
                        "alpha": calculation.alpha,
                        "z_score": calculation.z_score,
                        "weather": weather
                    })
                    
            except Exception as e:
                logger.warning(f"Error analyzing {market_id}: {e}")
        
        return underpriced
    
    def get_last_scan(self) -> Optional[ScanResult]:
        """Get the last scan result"""
        return self._last_scan
    
    def get_scan_count(self) -> int:
        """Get total number of scans performed"""
        return self._scan_count
    
    def get_stats(self) -> Dict[str, Any]:
        """Get scanner statistics"""
        return {
            "total_scans": self._scan_count,
            "last_scan_time": (
                self._last_scan.scanned_at.isoformat()
                if self._last_scan else None
            ),
            "last_markets_found": (
                self._last_scan.markets_found
                if self._last_scan else 0
            ),
            "last_qualifying": (
                len(self._last_scan.qualifying_markets)
                if self._last_scan else 0
            ),
            "cache_stats": self.get_cache_stats()
        }


def create_market_scanner(
    config: BotConfig,
    polymarket_client: PolymarketClient
) -> MarketScanner:
    """
    Factory function to create a MarketScanner.
    
    Args:
        config: Bot configuration
        polymarket_client: Polymarket CLOB client
        
    Returns:
        Configured MarketScanner instance
    """
    return MarketScanner(
        config=config,
        polymarket_client=polymarket_client
    )

