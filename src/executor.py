"""
Order Executor Module
=====================
Handles all order execution on Polymarket CLOB:
- EIP-712 signed orders
- Post-Only limit orders for maker rewards
- Liquidity Farming with Adjusted Midpoint (2026 reward formula)
- Automatic order management and rebalancing

Key Features:
- Post-Only orders to qualify for maker rebates
- Adjusted Midpoint Farming (keeps orders in "Blue Zone")
- 60-second rebalance for moving midpoints
- Lazy API calls to save Tomorrow.io credits
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .config import BotConfig
from .clients.polymarket import Market, PolymarketClient, Order
from .risk import RiskManager

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of an order execution"""
    success: bool
    order: Optional[Order]
    action: str  # "BUY", "SELL", "FARM", "CANCEL", "SKIP"
    reason: str
    reward_earned: float = 0.0
    error: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "success": self.success,
            "order_id": self.order.order_id if self.order else None,
            "action": self.action,
            "reason": self.reason,
            "reward_earned": self.reward_earned,
            "error": self.error
        }


@dataclass
class FarmingPosition:
    """Track active farming positions"""
    market_id: str
    buy_order_id: Optional[str]
    sell_order_id: Optional[str]
    midpoint: float
    created_at: datetime
    last_rebalance: datetime
    order_value: float
    
    def needs_rebalance(self, new_midpoint: float, threshold: float = 0.01) -> bool:
        """
        Check if position needs rebalancing.

        Rebalance if midpoint moved by more than threshold (1.0% - increased from 0.5%).
        Also rebalance if older than 120 seconds (increased from 60s).

        Priority 1: Cost Control - reduced rebalance frequency

        Args:
            new_midpoint: New market midpoint
            threshold: Minimum movement to trigger rebalance (default: 1.0%)

        Returns:
            True if rebalance is needed
        """
        # Check time-based rebalance (120 seconds - increased from 60s)
        age = datetime.now() - self.last_rebalance
        if age.total_seconds() > 120:
            return True

        # Check midpoint movement (1.0% - increased from 0.5%)
        midpoint_change = abs(new_midpoint - self.midpoint)
        if midpoint_change > threshold:
            return True

        return False


class OrderExecutor:
    """
    Executes orders on Polymarket with advanced features.
    
    2026 Updates:
    - Adjusted Midpoint Farming: Orders hover around midpoint
    - 60-second rebalance: Cancel and re-post if midpoint moves
    - Blue Zone optimization: Stay in reward-eligible zone
    
    Features:
    - EIP-712 signed Post-Only limit orders
    - Liquidity Rewards farming (dual orders)
    - Order lifecycle management
    - Retry logic with exponential backoff
    """
    
    def __init__(
        self,
        config: BotConfig,
        polymarket_client: PolymarketClient,
        risk_manager: RiskManager
    ):
        """
        Initialize order executor.
        
        Args:
            config: Bot configuration
            polymarket_client: Polymarket CLOB client
            risk_manager: Risk management instance
        """
        self.config = config
        self.client = polymarket_client
        self.risk = risk_manager
        
        self.farming_config = config.farming
        self.execution_config = config.execution
        
        # Track active farming positions
        self._farming_positions: Dict[str, FarmingPosition] = {}

        # Track recent orders
        self._recent_orders: Dict[str, datetime] = {}

        # Midpoint tracking for farming
        self._midpoint_cache: Dict[str, float] = {}

        # Price validation constants (Priority 0: Critical Safety)
        self.MIN_PRICE = 0.001
        self.MAX_PRICE = 0.999

        # Action cooldown tracking (Priority 1: Cost Control)
        self._market_last_action: Dict[str, datetime] = {}
        self._cooldown_seconds = self.execution_config.cooldown_seconds

    async def _get_existing_orders(self, market_id: str) -> Dict[str, Any]:
        """
        Get existing orders for a market (Priority 1: Cost Control).

        Args:
            market_id: Market to get orders for

        Returns:
            Dictionary of side -> order (e.g., {"BUY": order, "SELL": order})
        """
        try:
            all_orders = await self.client.get_open_orders()
            market_orders = {}

            for order in all_orders:
                # Handle both dict and Order object responses
                if isinstance(order, dict):
                    order_market_id = order.get('market', order.get('market_id'))
                    order_side = order.get('side')
                    order_price = float(order.get('price', 0))
                else:
                    order_market_id = getattr(order, 'market_id', getattr(order, 'market', None))
                    order_side = order.side
                    order_price = float(order.price)

                if order_market_id == market_id:
                    market_orders[order_side] = {"price": order_price, "order": order}

            return market_orders
        except Exception as e:
            logger.debug(f"Error getting existing orders for {market_id[:8]}: {e}")
            return {}

    def _orders_match(
        self,
        desired_buy: float,
        desired_sell: float,
        existing: Dict[str, Any]
    ) -> bool:
        """
        Check if desired orders match existing orders (Priority 1: Cost Control).

        Args:
            desired_buy: Desired buy price
            desired_sell: Desired sell price
            existing: Existing orders dict from _get_existing_orders()

        Returns:
            True if orders are essentially identical (within threshold)
        """
        threshold = self.execution_config.no_op_threshold

        # Check if we have both buy and sell orders
        if "BUY" not in existing or "SELL" not in existing:
            return False

        existing_buy_price = existing["BUY"]["price"]
        existing_sell_price = existing["SELL"]["price"]

        # Compare prices within threshold (0.1% by default)
        buy_match = abs(desired_buy - existing_buy_price) < threshold
        sell_match = abs(desired_sell - existing_sell_price) < threshold

        return buy_match and sell_match

    def _is_in_cooldown(self, market_id: str) -> tuple[bool, Optional[str]]:
        """
        Check if a market is in cooldown period (Priority 1: Cost Control).

        Args:
            market_id: Market to check

        Returns:
            Tuple of (is_in_cooldown: bool, reason: Optional[str])
        """
        if market_id not in self._market_last_action:
            return False, None

        last_action = self._market_last_action[market_id]
        elapsed = (datetime.now() - last_action).total_seconds()

        if elapsed < self._cooldown_seconds:
            remaining = int(self._cooldown_seconds - elapsed)
            reason = f"Market in cooldown: {remaining}s remaining (last action: {elapsed:.0f}s ago)"
            return True, reason

        return False, None

    def _validate_price_bands(
        self,
        buy_price: float,
        sell_price: float,
        midpoint: float
    ) -> bool:
        """
        Validate price bands to prevent invalid orders (Priority 0: Critical Safety).

        Checks:
        - buy_price must be < sell_price
        - buy_price must be >= MIN_PRICE (0.001)
        - sell_price must be <= MAX_PRICE (0.999)
        - Spread should not be excessively wide (warn if > 50%)

        Args:
            buy_price: Desired buy order price
            sell_price: Desired sell order price
            midpoint: Market midpoint

        Returns:
            True if price bands are valid, False otherwise
        """
        # Critical check: buy must be less than sell
        if buy_price >= sell_price:
            logger.error(
                f"CRITICAL: Invalid price bands - buy_price ({buy_price:.4f}) >= "
                f"sell_price ({sell_price:.4f})"
            )
            return False

        # Check minimum buy price
        if buy_price < self.MIN_PRICE:
            logger.error(f"CRITICAL: buy_price ({buy_price:.4f}) < MIN_PRICE ({self.MIN_PRICE})")
            return False

        # Check maximum sell price
        if sell_price > self.MAX_PRICE:
            logger.error(f"CRITICAL: sell_price ({sell_price:.4f}) > MAX_PRICE ({self.MAX_PRICE})")
            return False

        # Warn if spread is very wide (> 50%)
        spread = sell_price - buy_price
        if spread > 0.50:
            logger.warning(
                f"Wide spread detected: {spread:.4f} (buy={buy_price:.4f}, "
                f"sell={sell_price:.4f}, mid={midpoint:.4f})"
            )
            # Still allow wide spreads, just warn

        return True

    async def execute_trade(
        self,
        market: Market,
        current_price: float,
        fair_price: float,
        alpha: float,
        action: str = "BUY",
        confidence: float = 1.0
    ) -> ExecutionResult:
        """
        Execute a trade based on market analysis.

        Args:
            market: Market to trade
            current_price: Current market price
            fair_price: Calculated fair price
            alpha: Calculated alpha (absolute mispricing)
            action: "BUY", "SELL", or "HOLD"
            confidence: Confidence level (0-1), scales Kelly sizing

        Returns:
            ExecutionResult with order details
        """
        if action == "HOLD":
            return ExecutionResult(
                success=True,
                order=None,
                action="HOLD",
                reason="No trading signal (alpha below threshold)"
            )

        # Check cooldown (Priority 1: Cost Control)
        in_cooldown, cooldown_reason = self._is_in_cooldown(market.id)
        if in_cooldown:
            logger.debug(f"Skipping {market.id[:8]}: {cooldown_reason}")
            return ExecutionResult(
                success=False,
                order=None,
                action="SKIP",
                reason=cooldown_reason
            )

        # Restrict naked sells (Priority 2: Strategy & Position Logic)
        if action == "SELL":
            position = self.risk.get_position(market.id)
            if not position or position.size <= 0:
                logger.debug(f"Skipping sell {market.id[:8]}: no position held")
                return ExecutionResult(
                    success=False,
                    order=None,
                    action="SKIP",
                    reason="Cannot sell: no position held"
                )

        # Calculate position size (Kelly + capital scaling)
        base_size = self.config.risk.max_trade_size
        size = self.risk.calculate_position_size(
            base_size=base_size,
            alpha=alpha,
            max_multiplier=self.config.alpha.size_multiplier_max,
            fair_price=fair_price,
            market_price=current_price,
            confidence=confidence
        )

        # Cap at max_trade_size (defensive: calculate_position_size should
        # already do this, but ensure no over-sized orders reach risk checks)
        max_size = self.config.risk.max_trade_size
        if size > max_size:
            logger.info(f"Capping position size from ${size:.2f} to ${max_size:.2f}")
            size = max_size

        # Check risk limits
        can_trade, reason = self.risk.can_open_position(size, market.id)
        if not can_trade:
            return ExecutionResult(
                success=False,
                order=None,
                action="SKIP",
                reason=reason
            )
        
        # Determine execution strategy
        # CRITICAL FIX: When alpha signals a directional trade (BUY/SELL),
        # use _place_order() which registers positions in the risk manager.
        # Farming places symmetric BUY+SELL but never calls risk.open_position(),
        # so positions were invisible (showed 0 positions for 2+ hours).
        if action in ("BUY", "SELL") and abs(alpha) >= self.config.alpha.threshold:
            # Directional trade: strong alpha signal → take a position
            logger.info(
                f"Directional {action} on {market.id[:8]} "
                f"(alpha={alpha:.1%}, price=${current_price:.4f})"
            )
            return await self._place_order(
                market=market,
                side=action,
                price=current_price,
                size=size,
                post_only=True,
                entry_alpha=alpha
            )
        elif current_price < self.farming_config.price_threshold:
            # Farming mode: weak/no signal, farm for liquidity rewards
            return await self._farm_liquidity_adjusted(
                market=market,
                current_price=current_price,
                size=size,
                entry_alpha=alpha
            )
        else:
            # Regular mode: place single order
            return await self._place_order(
                market=market,
                side=action,
                price=current_price,
                size=size,
                post_only=True,
                entry_alpha=alpha
            )
    
    async def _farm_liquidity_adjusted(
        self,
        market: Market,
        current_price: float,
        size: float,
        entry_alpha: float = 0.0
    ) -> ExecutionResult:
        """
        Farm Liquidity Rewards using Adjusted Midpoint strategy.
        
        2026 Reward Formula:
        - Rewards based on being near the MIDPOINT (not just near price)
        - Must stay within "Blue Zone" (reward zone)
        - Cancel and re-post within 60 seconds if midpoint moves
        
        Strategy:
        1. Calculate current midpoint
        2. Place buy order just below midpoint
        3. Place sell order just above midpoint
        4. If midpoint moves > 0.5%, cancel and re-post
        5. Always re-post every 60 seconds maximum
        
        Args:
            market: Market to farm
            current_price: Current market price
            size: Size per order
            entry_alpha: Alpha signal strength when entering
            
        Returns:
            ExecutionResult with farming details
        """
        market_id = market.id
        logger.info(
            f"Entering Adjusted Midpoint Farming for {market_id[:8]}... "
            f"(Price: ${current_price:.4f})"
        )

        # Check cooldown (Priority 1: Cost Control)
        in_cooldown, cooldown_reason = self._is_in_cooldown(market_id)
        if in_cooldown:
            logger.debug(f"Skipping farming for {market_id[:8]}: {cooldown_reason}")
            return ExecutionResult(
                success=False,
                order=None,
                action="SKIP",
                reason=cooldown_reason
            )

        # Get current midpoint (use price as proxy if no order book)
        midpoint = await self._get_adjusted_midpoint(market, current_price)
        
        # Check if we have an existing position
        existing = self._farming_positions.get(market_id)
        
        if existing and not existing.needs_rebalance(midpoint):
            logger.debug(f"Existing position still valid for {market_id[:8]}...")
            return ExecutionResult(
                success=True,
                order=None,
                action="FARM_HOLD",
                reason="Existing position still in Blue Zone",
                reward_earned=0.0
            )
        
        # Cancel existing orders if any
        if existing:
            await self._cancel_farming_position(market_id)
        
        # Calculate new order prices around midpoint
        # FIX #1: Dynamic exits at current midpoint + small spread
        # Take tiny wins instead of waiting for impossible prices
        spread_buffer = 0.01  # 1% buffer for Blue Zone

        buy_price = max(0.01, midpoint - spread_buffer)
        sell_price = midpoint + 0.01  # FIXED: Always midpoint + $0.01 for quick exits

        # Ensure buy price doesn't exceed current price
        buy_price = min(buy_price, current_price - 0.005)

        # Validate price bands (Priority 0: Critical Safety)
        if not self._validate_price_bands(buy_price, sell_price, midpoint):
            logger.error(
                f"Invalid price bands for {market_id[:8]}: "
                f"buy={buy_price:.4f}, sell={sell_price:.4f}, mid={midpoint:.4f}"
            )
            return ExecutionResult(
                success=False,
                order=None,
                action="SKIP",
                reason="Invalid price bands (buy >= sell or out of range)"
            )

        # No-op guard: Check if desired orders match existing orders (Priority 1: Cost Control)
        existing_orders = await self._get_existing_orders(market_id)
        if self._orders_match(buy_price, sell_price, existing_orders):
            logger.debug(
                f"Orders unchanged for {market_id[:8]}: "
                f"buy={buy_price:.4f}, sell={sell_price:.4f} (skipping re-post)"
            )
            return ExecutionResult(
                success=True,
                order=None,
                action="NO_OP",
                reason="Existing orders match desired state"
            )

        orders_placed = []
        
        try:
            # Place buy order (below midpoint)
            buy_order = await self.client.place_limit_order(
                market_id=market_id,
                side="BUY",
                price=buy_price,
                size=size,
                post_only=True
            )
            orders_placed.append(buy_order)

            # Place sell order (above midpoint)
            sell_order = await self.client.place_limit_order(
                market_id=market_id,
                side="SELL",
                price=sell_price,
                size=size,
                post_only=True
            )
            orders_placed.append(sell_order)

            # Log both orders on one line
            logger.info(
                f"Farming: BUY ${buy_price:.4f} / SELL ${sell_price:.4f} "
                f"(mid: ${midpoint:.4f}, size: {size})"
            )
            
            # Track the farming position (use dict access for mock responses)
            farming_pos = FarmingPosition(
                market_id=market_id,
                buy_order_id=buy_order["orderID"],
                sell_order_id=sell_order["orderID"],
                midpoint=midpoint,
                created_at=datetime.now(),
                last_rebalance=datetime.now(),
                order_value=size * 2
            )
            self._farming_positions[market_id] = farming_pos

            # Record action timestamp for cooldown (Priority 1: Cost Control)
            self._market_last_action[market_id] = datetime.now()

            # Estimate rewards
            estimated_reward = self._estimate_farming_reward(size, midpoint)

            return ExecutionResult(
                success=True,
                order=buy_order,
                action="FARM_ADJUSTED",
                reason=f"Midpoint farming: BUY @ ${buy_price:.4f}, SELL @ ${sell_price:.4f}",
                reward_earned=estimated_reward
            )
            
        except Exception as e:
            logger.error(f"Adjusted midpoint farming failed: {e}")
            
            # Cancel any placed orders (use dict access)
            for order in orders_placed:
                order_id = order.get("orderID") if isinstance(order, dict) else order.order_id
                await self.client.cancel_order(order_id)
            
            return ExecutionResult(
                success=False,
                order=None,
                action="FARM_FAILED",
                reason="Farming order placement failed",
                error=str(e)
            )
    
    async def _get_adjusted_midpoint(
        self,
        market: Market,
        current_price: float
    ) -> float:
        """
        Calculate the Adjusted Midpoint for farming.

        CRITICAL FIX: For wide-spread markets (common in weather),
        the orderbook midpoint can be $0.50+ even when the market
        trades at $0.04. This causes sell orders at $0.52 that never fill.

        Now: Use orderbook midpoint ONLY if it's close to current_price.
        Otherwise use current_price as the midpoint.

        Args:
            market: Market to calculate for
            current_price: Current YES price

        Returns:
            Adjusted midpoint price
        """
        market_id = market.id

        # Try to get midpoint from order book
        try:
            book = await self.client.get_order_book(market_id)

            if book and 'bids' in book and 'asks' in book:
                bids = book['bids']
                asks = book['asks']

                if bids and asks:
                    best_bid = float(bids[0]['price'])
                    best_ask = float(asks[0]['price'])
                    book_midpoint = (best_bid + best_ask) / 2

                    # SANITY CHECK: Only use orderbook midpoint if it's
                    # within 1.5x of current price (prevents $0.51 midpoint
                    # on a $0.04 market — tightened from 3x)
                    if current_price > 0.01 and book_midpoint < current_price * 1.5:
                        self._midpoint_cache[market_id] = book_midpoint
                        return book_midpoint
                    else:
                        logger.debug(
                            f"Ignoring wide orderbook midpoint {book_midpoint:.4f} "
                            f"for {market_id[:8]} (price: {current_price:.4f})"
                        )

        except Exception as e:
            logger.debug(f"Could not get order book for {market_id[:8]}: {e}")

        # Use current price as midpoint (safe for cheap markets)
        midpoint = current_price
        self._midpoint_cache[market_id] = midpoint
        return midpoint
    
    async def _cancel_farming_position(self, market_id: str) -> bool:
        """
        Cancel all orders for a farming position.
        
        Args:
            market_id: Market to cancel
            
        Returns:
            True if successful
        """
        position = self._farming_positions.get(market_id)
        if not position:
            return True
        
        cancelled = 0
        
        # Cancel buy order
        if position.buy_order_id:
            success = await self.client.cancel_order(position.buy_order_id)
            if success:
                cancelled += 1
        
        # Cancel sell order
        if position.sell_order_id:
            success = await self.client.cancel_order(position.sell_order_id)
            if success:
                cancelled += 1
        
        # Remove from tracking
        del self._farming_positions[market_id]
        
        logger.info(f"Cancelled {cancelled} farming orders for {market_id[:8]}...")
        
        return cancelled > 0
    
    async def rebalance_all_farming_positions(self) -> int:
        """
        Rebalance all active farming positions.
        
        Called periodically to:
        1. Check if midpoints have moved
        2. Cancel and re-post orders if needed
        3. Ensure positions stay in Blue Zone
        
        Returns:
            Number of positions rebalanced
        """
        rebalanced = 0
        now = datetime.now()
        
        for market_id, position in list(self._farming_positions.items()):
            # Check if we need to rebalance
            needs_rebalance = False
            
            try:
                # Get current price
                current_price = await self.client.get_price(market_id)
                
                # Get current midpoint
                midpoint = await self._get_adjusted_midpoint(
                    self._get_market_by_id(market_id),
                    current_price
                )
                
                # Check rebalance conditions
                if position.needs_rebalance(midpoint):
                    needs_rebalance = True
                    
            except Exception as e:
                logger.warning(f"Error checking rebalance for {market_id[:8]}: {e}")
                # Force rebalance on error
                needs_rebalance = True
            
            if needs_rebalance:
                logger.info(f"Rebalancing farming position for {market_id[:8]}...")
                
                # Cancel old orders
                await self._cancel_farming_position(market_id)
                
                # Get current price and re-farm
                try:
                    current_price = await self.client.get_price(market_id)
                    size = position.order_value / 2
                    
                    result = await self._farm_liquidity_adjusted(
                        self._get_market_by_id(market_id),
                        current_price,
                        size
                    )
                    
                    if result.success:
                        rebalanced += 1
                        
                except Exception as e:
                    logger.error(f"Rebalance failed for {market_id[:8]}: {e}")
        
        if rebalanced > 0:
            logger.info(f"Rebalanced {rebalanced} farming positions")
        
        return rebalanced
    
    def _get_market_by_id(self, market_id: str) -> Market:
        """Helper to create a minimal Market object"""
        return Market(
            id=market_id,
            question="",
            condition_id=market_id,
            yes_price=0.5,
            no_price=0.5,
            last_trade_price=0.5,
            active=True,
            category="weather",
            volume=0,
            liquidity_rewards_enabled=True
        )
    
    async def _place_order(
        self,
        market: Market,
        side: str,
        price: float,
        size: float,
        post_only: bool = True,
        entry_alpha: float = 0.0
    ) -> ExecutionResult:
        """
        Place a single order.

        Args:
            market: Market to trade
            side: "BUY" or "SELL"
            price: Order price
            size: Position size
            post_only: Use Post-Only order (maker)
            entry_alpha: Alpha signal strength when entering

        Returns:
            ExecutionResult with order details
        """
        # Validate price (Priority 0: Critical Safety)
        if price < self.MIN_PRICE or price > self.MAX_PRICE:
            logger.error(
                f"CRITICAL: Invalid order price {price:.4f} for {side} order "
                f"(valid range: {self.MIN_PRICE} - {self.MAX_PRICE})"
            )
            return ExecutionResult(
                success=False,
                order=None,
                action="ERROR",
                reason=f"Invalid price: {price:.4f} out of valid range",
                error=f"Price {price:.4f} violates MIN_PRICE/MAX_PRICE constraints"
            )

        try:
            # Place the order
            order = await self.client.place_limit_order(
                market_id=market.id,
                side=side,
                price=price,
                size=size,
                post_only=post_only
            )
            
            logger.info(
                f"Order placed: {side} {size} @ ${price:.4f} "
                f"on {market.id[:8]}... (Post-Only: {post_only})"
            )
            
            # Track order (use dict access for mock responses)
            self._recent_orders[order["orderID"]] = datetime.now()

            # Record action timestamp for cooldown (Priority 1: Cost Control)
            self._market_last_action[market.id] = datetime.now()

            # Track position in risk manager (for BUY orders)
            if side == "BUY":
                self.risk.open_position(
                    market_id=market.id,
                    outcome="YES",
                    size=size,
                    price=price,
                    entry_alpha=entry_alpha
                )

            return ExecutionResult(
                success=True,
                order=order,
                action=side,
                reason=f"Order placed successfully",
                reward_earned=0.0 if not post_only else self._estimate_maker_rebate(size)
            )
            
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            return ExecutionResult(
                success=False,
                order=None,
                action="ERROR",
                reason="Order placement failed",
                error=str(e)
            )
    
    async def place_buy_order(
        self,
        market: Market,
        price: float,
        size: float
    ) -> ExecutionResult:
        """
        Place a single BUY order.
        
        Args:
            market: Market to buy
            price: Order price
            size: Position size
            
        Returns:
            ExecutionResult with order details
        """
        return await self._place_order(
            market=market,
            side="BUY",
            price=price,
            size=size,
            post_only=True  # Maker order for rebates
        )
    
    async def place_sell_order(
        self,
        market: Market,
        price: float,
        size: float
    ) -> ExecutionResult:
        """
        Place a single SELL order.
        
        Args:
            market: Market to sell
            price: Order price
            size: Position size
            
        Returns:
            ExecutionResult with order details
        """
        return await self._place_order(
            market=market,
            side="SELL",
            price=price,
            size=size,
            post_only=True
        )
    
    async def place_market_exit(
        self,
        market: Market,
        size: float,
        current_price: float
    ) -> ExecutionResult:
        """
        Exit a position with market order (fast execution).
        
        Used for auto-exit when profit target is reached.
        
        Args:
            market: Market to exit
            size: Position size to close
            current_price: Current market price
            
        Returns:
            ExecutionResult with order details
        """
        # Cancel any farming position first
        await self._cancel_farming_position(market.id)
        
        try:
            order = await self.client.place_market_order(
                market_id=market.id,
                side="SELL",
                size=size,
                price=current_price
            )
            
            logger.info(
                f"Market exit: SELL {size} @ market price "
                f"(~${current_price:.4f}) on {market.id[:8]}..."
            )
            
            return ExecutionResult(
                success=True,
                order=order,
                action="EXIT",
                reason="Market exit executed",
                reward_earned=0.0  # No rewards on taker orders
            )
            
        except Exception as e:
            logger.error(f"Market exit failed: {e}")
            return ExecutionResult(
                success=False,
                order=None,
                action="EXIT_FAILED",
                reason="Market exit failed",
                error=str(e)
            )
    
    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order.
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            True if cancellation successful
        """
        return await self.client.cancel_order(order_id)
    
    async def cancel_all_orders(
        self,
        market_id: Optional[str] = None
    ) -> int:
        """
        Cancel all open orders.
        
        Args:
            market_id: Optional market ID to filter
            
        Returns:
            Number of orders cancelled
        """
        # Cancel farming positions first
        if market_id:
            await self._cancel_farming_position(market_id)
        else:
            for mid in list(self._farming_positions.keys()):
                await self._cancel_farming_position(mid)
        
        return await self.client.cancel_all_orders(market_id)
    
    async def get_open_orders(self) -> List[Order]:
        """Get all open orders"""
        return await self.client.get_open_orders()
    
    def get_farming_state(self) -> Dict:
        """
        Get complete farming state (Priority 5: Logging & Debugging).

        Returns:
            Farming state dictionary
        """
        return {
            "farming_positions": {
                mid: {
                    "midpoint": pos.midpoint,
                    "buy_order_id": pos.buy_order_id,
                    "sell_order_id": pos.sell_order_id,
                    "last_rebalance": pos.last_rebalance.isoformat(),
                    "age_seconds": (datetime.now() - pos.created_at).total_seconds(),
                    "order_value": pos.order_value
                }
                for mid, pos in self._farming_positions.items()
            },
            "cooldown_tracked_markets": len(self._market_last_action),
            "midpoint_cache_size": len(self._midpoint_cache)
        }

    def get_farming_positions(self) -> List[Dict[str, Any]]:
        """Get all active farming positions"""
        return [
            {
                "market_id": pos.market_id,
                "midpoint": pos.midpoint,
                "created_at": pos.created_at.isoformat(),
                "last_rebalance": pos.last_rebalance.isoformat(),
                "order_value": pos.order_value,
                "age_seconds": (datetime.now() - pos.created_at).total_seconds()
            }
            for pos in self._farming_positions.values()
        ]
    
    def _estimate_maker_rebate(self, size: float) -> float:
        """
        Estimate maker rebate for a Post-Only order.
        
        Note: Actual rebates vary by market and conditions.
        
        Args:
            size: Order size in USDC
            
        Returns:
            Estimated rebate in USDC
        """
        # Approximate maker rebate rate (varies by market)
        rebate_rate = 0.001  # 0.1% rebate
        
        # Maker rebate = size * rebate_rate
        return size * rebate_rate
    
    def _estimate_farming_reward(
        self,
        size: float,
        midpoint: float
    ) -> float:
        """
        Estimate Liquidity Rewards for Adjusted Midpoint farming.
        
        2026 Reward Formula considers:
        - Proximity to midpoint
        - Order book depth
        - Duration of liquidity provision
        
        Args:
            size: Size per order
            midpoint: Current midpoint price
            
        Returns:
            Estimated daily reward in USDC
        """
        # Base reward for providing liquidity near midpoint
        base_reward = 0.05  # Base daily reward
        
        # Volume bonus (1% of volume)
        volume_bonus = size * 0.01
        
        # Midpoint proximity bonus (closer = higher)
        # If midpoint is 0.10, being at 0.10 is optimal
        proximity_bonus = 0.02
        
        # Maker rebate on both orders
        maker_rebate = self._estimate_maker_rebate(size * 2)
        
        total = base_reward + volume_bonus + proximity_bonus + maker_rebate
        
        return round(total, 4)
    
    def calculate_optimal_farm_size(
        self,
        market_price: float,
        max_size: float = None
    ) -> float:
        """
        Calculate optimal size for liquidity farming.
        
        Args:
            market_price: Current market price
            max_size: Maximum size (uses config if not provided)
            
        Returns:
            Optimal farm size
        """
        max_s = max_size or self.farming_config.farming_size
        
        # For very cheap markets, use smaller sizes
        if market_price < 0.05:
            return max_s * 0.5
        elif market_price < 0.08:
            return max_s * 0.75
        
        return max_s


def create_order_executor(
    config: BotConfig,
    polymarket_client: PolymarketClient,
    risk_manager: RiskManager
) -> OrderExecutor:
    """
    Factory function to create an OrderExecutor.
    
    Args:
        config: Bot configuration
        polymarket_client: Polymarket CLOB client
        risk_manager: Risk management instance
        
    Returns:
        Configured OrderExecutor instance
    """
    return OrderExecutor(
        config=config,
        polymarket_client=polymarket_client,
        risk_manager=risk_manager
    )

