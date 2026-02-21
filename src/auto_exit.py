"""
Auto-Exit Module
================
Automatically exits positions when profit targets are reached.
- Primary target: 100% profit (position doubles in value)
- Exit type: Market order for fast execution
- Risk management: Always exit, no exceptions
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from .config import BotConfig
from .risk import RiskManager, Position
from .clients.polymarket import Market, PolymarketClient, Order

logger = logging.getLogger(__name__)


@dataclass
class ExitSignal:
    """Represents an exit signal"""
    market_id: str
    position: Position
    current_price: float
    profit_percentage: float
    profit_usdc: float
    exit_type: str  # "PROFIT_TAKE", "STOP_LOSS", "MANUAL"
    reason: str
    executed: bool = False
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "market_id": self.market_id,
            "position_size": self.position.size,
            "avg_price": self.position.avg_price,
            "current_price": self.current_price,
            "profit_percentage": self.profit_percentage,
            "profit_usdc": self.profit_usdc,
            "exit_type": self.exit_type,
            "reason": self.reason,
            "executed": self.executed
        }


class AutoExitManager:
    """
    Manages automatic position exits based on profit targets.
    
    Key Logic:
    - Primary exit: 100% profit target
    - Exit method: Market order (fastest execution)
    - No exceptions for profit taking
    """
    
    def __init__(
        self,
        config: BotConfig,
        risk_manager: RiskManager,
        polymarket_client: PolymarketClient
    ):
        """
        Initialize auto-exit manager.
        
        Args:
            config: Bot configuration
            risk_manager: Risk management instance
            polymarket_client: Polymarket CLOB client
        """
        self.config = config
        self.risk = risk_manager
        self.client = polymarket_client
        
        self.profit_target = config.risk.profit_target  # Default: 1.0 = 100%
        
        # Track exit signals
        self._exit_signals: List[ExitSignal] = []
        self._exited_positions: Dict[str, datetime] = {}
        
    def check_exit_conditions(
        self,
        positions: Dict[str, Position],
        current_prices: Dict[str, float]
    ) -> List[ExitSignal]:
        """
        Check all positions for exit conditions.

        Exit Logic:
        1. PROFIT_TARGET: Exit when position has doubled (100% gain).
        2. TRAILING_STOP: Let big winners run; exit if price drops 25% from peak.
        3. RISK_EXIT: Only for expensive markets (avg_price >= 0.20).
           Cheap prediction markets are binary bets — Kelly sizing already prices
           in total-loss risk. A -15% move on a $0.08 market is $0.012 of noise;
           stopping out here sacrifices the 12.5x resolution payout.

        Args:
            positions: Dictionary of market_id -> Position
            current_prices: Dictionary of market_id -> current price

        Returns:
            List of ExitSignal objects for positions to exit
        """
        exit_signals = []

        for market_id, position in positions.items():
            current_price = current_prices.get(market_id)

            if current_price is None:
                continue

            # Sanity check: thin markets return 0.5 as default when orderbook is empty.
            # A cheap entry ($0.03) reading $0.50 is NOT a real price — it's the
            # fallback midpoint. Skip to avoid fake profit-target triggers.
            if position.avg_price < 0.20 and current_price >= 0.45:
                logger.warning(
                    f"Skipping exit for {market_id[:8]}: price ${current_price:.3f} "
                    f"looks like stale/default read (entry ${position.avg_price:.3f})"
                )
                continue

            # Update position with current price (also updates peak_pnl)
            self.risk.update_position(market_id, current_price)

            # Check 1: PROFIT_TARGET — take profit when position has doubled.
            if position.pnl_percentage >= 100:
                signal = self._create_exit_signal(
                    position=position,
                    current_price=current_price,
                    exit_type="PROFIT_TARGET",
                    reason=(
                        f"Profit target: {position.pnl_percentage:.1f}% gain "
                        f"(entry ${position.avg_price:.3f} → ${current_price:.3f})"
                    )
                )
                exit_signals.append(signal)
                continue

            # Check 2: TRAILING_STOP — let big winners run; exit if drops 25% from peak.
            if position.pnl_percentage >= 30 and position.peak_pnl_percentage >= 50:
                drop_from_peak = position.peak_pnl_percentage - position.pnl_percentage
                if drop_from_peak >= 25:
                    signal = self._create_exit_signal(
                        position=position,
                        current_price=current_price,
                        exit_type="TRAILING_STOP",
                        reason=(
                            f"Trailing stop: {position.pnl_percentage:.1f}% "
                            f"(peak was {position.peak_pnl_percentage:.1f}%, "
                            f"drop: -{drop_from_peak:.1f}%)"
                        )
                    )
                    exit_signals.append(signal)
                    continue

            # Check 3: RISK_EXIT stop-loss.
            # Expensive markets (>= $0.20): -20% stop.
            # Cheap markets (< $0.20): -50% stop (absorbs bid-ask spread noise).
            if position.avg_price >= 0.20:
                if position.pnl_percentage < -20:
                    signal = self._create_exit_signal(
                        position=position,
                        current_price=current_price,
                        exit_type="RISK_EXIT",
                        reason=(
                            f"Risk exit: down {position.pnl_percentage:.1f}% "
                            f"(entry ${position.avg_price:.3f}, expensive market)"
                        )
                    )
                    exit_signals.append(signal)
                    continue
            else:
                if position.pnl_percentage < -50:
                    signal = self._create_exit_signal(
                        position=position,
                        current_price=current_price,
                        exit_type="RISK_EXIT",
                        reason=(
                            f"Risk exit: down {position.pnl_percentage:.1f}% "
                            f"(entry ${position.avg_price:.3f}, cheap market stop)"
                        )
                    )
                    exit_signals.append(signal)
                    continue

        return exit_signals
    
    def _create_exit_signal(
        self,
        position: Position,
        current_price: float,
        exit_type: str,
        reason: str
    ) -> ExitSignal:
        """
        Create an exit signal for a position.
        
        Args:
            position: Position to exit
            current_price: Current market price
            exit_type: Type of exit
            reason: Reason for exit
            
        Returns:
            ExitSignal object
        """
        profit_pct = position.pnl_percentage / 100  # Convert to decimal
        profit_usdc = position.unrealized_pnl
        
        return ExitSignal(
            market_id=position.market_id,
            position=position,
            current_price=current_price,
            profit_percentage=profit_pct,
            profit_usdc=profit_usdc,
            exit_type=exit_type,
            reason=reason
        )
    
    async def execute_exit(
        self,
        exit_signal: ExitSignal
    ) -> ExitSignal:
        """
        Execute an exit order.
        
        Args:
            exit_signal: Exit signal to execute
            
        Returns:
            Updated ExitSignal with execution status
        """
        position = exit_signal.position
        
        logger.info(
            f"Auto-exit triggered: {exit_signal.exit_type} "
            f"on {position.market_id[:8]}... "
            f"(Profit: ${exit_signal.profit_usdc:.2f}, "
            f"{exit_signal.profit_percentage:.1%})"
        )
        
        try:
            # Convert dollar size back to shares for the API.
            # position.size is stored in dollars; API expects share count.
            size_shares = position.shares  # = size / avg_price

            # Place market order to exit
            order = await self.client.place_market_order(
                market_id=position.market_id,
                side="SELL",
                size=size_shares,
                price=exit_signal.current_price
            )
            
            # Close position in risk manager
            self.risk.close_position(
                market_id=position.market_id,
                exit_price=exit_signal.current_price
            )
            
            # Mark signal as executed
            exit_signal.executed = True
            
            # Track exited position
            self._exited_positions[position.market_id] = datetime.now()
            self._exit_signals.append(exit_signal)
            
            logger.info(
                f"Auto-exit completed: {position.market_id[:8]}... "
                f"Order: {order.order_id}, PnL: ${exit_signal.profit_usdc:.2f}"
            )
            
            return exit_signal
            
        except Exception as e:
            logger.error(f"Auto-exit failed for {position.market_id}: {e}")
            
            # Update signal with error
            exit_signal.reason = f"ERROR: {str(e)}"
            exit_signal.executed = False
            
            return exit_signal
    
    async def execute_all_exits(
        self,
        exit_signals: List[ExitSignal]
    ) -> List[ExitSignal]:
        """
        Execute all exit signals.
        
        Args:
            exit_signals: List of exit signals to execute
            
        Returns:
            List of executed signals
        """
        results = []
        
        for signal in exit_signals:
            result = await self.execute_exit(signal)
            results.append(result)
            
            # Small delay between exits to avoid rate limits
            await asyncio.sleep(0.5)
        
        return results
    
    def should_exit(
        self,
        position: Position,
        current_price: float
    ) -> bool:
        """
        Quick check if a single position should be exited.
        
        Args:
            position: Position to check
            current_price: Current market price
            
        Returns:
            True if position should be exited
        """
        current_value = position.size * current_price
        cost_basis = position.size * position.avg_price
        
        if cost_basis == 0:
            return False
        
        profit_pct = (current_value - cost_basis) / cost_basis
        
        return profit_pct >= self.profit_target
    
    def get_profitable_positions(
        self,
        positions: Dict[str, Position],
        current_prices: Dict[str, float],
        min_profit: float = 0.5
    ) -> List[str]:
        """
        Get list of positions with profit above threshold.
        
        Args:
            positions: Dictionary of market_id -> Position
            current_prices: Dictionary of market_id -> current price
            min_profit: Minimum profit percentage (default: 50%)
            
        Returns:
            List of market IDs to consider for exit
        """
        to_exit = []
        
        for market_id, position in positions.items():
            current_price = current_prices.get(market_id)
            if current_price is None:
                continue
            
            current_value = position.size * current_price
            cost_basis = position.size * position.avg_price
            
            if cost_basis == 0:
                continue
            
            profit_pct = (current_value - cost_basis) / cost_basis
            
            if profit_pct >= min_profit:
                to_exit.append(market_id)
        
        return to_exit
    
    def get_exit_summary(self) -> Dict:
        """Get summary of all exit signals"""
        total_exits = len(self._exit_signals)
        successful_exits = len([s for s in self._exit_signals if s.executed])
        total_profit = sum(s.profit_usdc for s in self._exit_signals)
        
        profit_takes = [s for s in self._exit_signals if s.exit_type == "PROFIT_TAKE"]
        stop_losses = [s for s in self._exit_signals if s.exit_type == "STOP_LOSS"]
        
        return {
            "total_exit_signals": total_exits,
            "successful_exits": successful_exits,
            "failed_exits": total_exits - successful_exits,
            "total_profit_realized": total_profit,
            "profit_take_count": len(profit_takes),
            "stop_loss_count": len(stop_losses),
            "avg_profit_per_exit": (
                total_profit / successful_exits if successful_exits > 0 else 0
            )
        }
    
    def get_recent_exits(self, hours: int = 24) -> List[ExitSignal]:
        """
        Get recent exit signals within the specified time frame.
        
        Args:
            hours: Number of hours to look back
            
        Returns:
            List of recent exit signals
        """
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(hours=hours)
        
        return [
            s for s in self._exit_signals
            if s.position.opened_at >= cutoff
        ]
    
    def clear_old_signals(self, days: int = 7):
        """
        Clear exit signals older than specified days.
        
        Args:
            days: Number of days to keep signals
        """
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(days=days)
        
        self._exit_signals = [
            s for s in self._exit_signals
            if s.position.opened_at >= cutoff
        ]


def create_auto_exit_manager(
    config: BotConfig,
    risk_manager: RiskManager,
    polymarket_client: PolymarketClient
) -> AutoExitManager:
    """
    Factory function to create an AutoExitManager.
    
    Args:
        config: Bot configuration
        risk_manager: Risk management instance
        polymarket_client: Polymarket CLOB client
        
    Returns:
        Configured AutoExitManager instance
    """
    return AutoExitManager(
        config=config,
        risk_manager=risk_manager,
        polymarket_client=polymarket_client
    )

