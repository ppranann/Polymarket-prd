"""
Risk Management Module
======================
Manages all risk parameters for the trading bot including:
- Position sizing
- Daily loss limits
- Capital allocation
- Diversification rules
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .config import BotConfig

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents an open position"""
    market_id: str
    outcome: str  # "YES" or "NO"
    size: float  # Size in USDC
    avg_price: float  # Average entry price
    current_price: float  # Current market price
    opened_at: datetime
    last_updated: datetime
    peak_pnl_percentage: float = 0.0  # Track peak profit for trailing stop
    entry_alpha: float = 0.0  # Alpha signal strength when entering
    
    @property
    def current_value(self) -> float:
        """Current value of the position"""
        return self.size * self.current_price
    
    @property
    def cost_basis(self) -> float:
        """Total cost of the position"""
        return self.size * self.avg_price
    
    @property
    def unrealized_pnl(self) -> float:
        """Unrealized profit/loss"""
        return self.current_value - self.cost_basis
    
    @property
    def pnl_percentage(self) -> float:
        """Profit/loss as percentage"""
        if self.cost_basis == 0:
            return 0.0
        return (self.unrealized_pnl / self.cost_basis) * 100


@dataclass
class DailyStats:
    """Tracks daily trading statistics"""
    date: datetime
    trades_count: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_volume: float = 0.0
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    rewards_earned: float = 0.0
    max_drawdown: float = 0.0
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "date": self.date.strftime("%Y-%m-%d"),
            "trades_count": self.trades_count,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "total_volume": self.total_volume,
            "realized_pnl": self.realized_pnl,
            "fees_paid": self.fees_paid,
            "rewards_earned": self.rewards_earned,
            "max_drawdown": self.max_drawdown
        }


class RiskManager:
    """
    Comprehensive risk management for the trading bot.
    
    Enforces:
    - Position sizing limits
    - Daily loss limits
    - Diversification rules
    - Concentration limits
    """
    
    def __init__(
        self,
        config: BotConfig,
        initial_capital: float = None
    ):
        """
        Initialize risk manager.
        
        Args:
            config: Bot configuration
            initial_capital: Starting capital (uses config if not provided)
        """
        self.config = config
        self.risk = config.risk
        
        self._capital = initial_capital or self.risk.initial_capital
        self._positions: Dict[str, Position] = {}
        self._daily_stats: Dict[datetime, DailyStats] = {}
        self._trading_paused = False
        self._pause_reason = None

        # Daily trade limit tracking (Priority 4: Risk Management)
        self._daily_trade_count = 0
        self._max_daily_trades = self.risk.max_daily_trades
        self._trade_count_reset_date = datetime.now().date()

        # Circuit breaker tracking (Priority 4: Risk Management)
        self._circuit_breaker_triggered = False
        self._circuit_breaker_threshold = self.risk.circuit_breaker_pnl_pct
        self._circuit_breaker_window_minutes = self.risk.circuit_breaker_window
        self._recent_pnl: List[tuple[datetime, float]] = []
    
    @property
    def capital(self) -> float:
        """Get current available capital"""
        # Subtract value of open positions
        positions_value = sum(p.current_value for p in self._positions.values())
        return self._capital - positions_value
    
    @property
    def total_exposure(self) -> float:
        """Get total value of open positions"""
        return sum(p.current_value for p in self._positions.values())
    
    @property
    def positions(self) -> Dict[str, Position]:
        """Get copy of open positions"""
        return self._positions.copy()
    
    @property
    def is_paused(self) -> bool:
        """Check if trading is paused"""
        return self._trading_paused
    
    @property
    def pause_reason(self) -> Optional[str]:
        """Get reason for pause"""
        return self._pause_reason
    
    def check_circuit_breaker(self) -> tuple[bool, str]:
        """
        Check if circuit breaker should be triggered (Priority 4: Risk Management).

        Circuit breaker triggers if total P&L drops by threshold % within the window.

        Returns:
            Tuple of (triggered: bool, reason: str)
        """
        if self._circuit_breaker_triggered:
            return True, "Circuit breaker already triggered"

        # Clean old P&L entries outside the window
        from datetime import timedelta
        cutoff_time = datetime.now() - timedelta(minutes=self._circuit_breaker_window_minutes)
        self._recent_pnl = [(ts, pnl) for ts, pnl in self._recent_pnl if ts >= cutoff_time]

        # Calculate total P&L change in window
        if not self._recent_pnl:
            return False, "No P&L changes in window"

        total_pnl_change = sum(pnl for _, pnl in self._recent_pnl)

        # Check if loss exceeds threshold
        loss_pct = abs(total_pnl_change) / self._capital if self._capital > 0 else 0

        if total_pnl_change < 0 and loss_pct >= self._circuit_breaker_threshold:
            self._circuit_breaker_triggered = True
            reason = (
                f"Circuit breaker triggered: ${total_pnl_change:.2f} loss "
                f"({loss_pct:.1%}) in {self._circuit_breaker_window_minutes} minutes"
            )
            logger.critical(reason)
            return True, reason

        return False, "Circuit breaker OK"

    def _check_daily_trade_limit(self) -> tuple[bool, str]:
        """
        Check if daily trade limit has been exceeded (Priority 4: Risk Management).

        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        # Reset counter if new day
        today = datetime.now().date()
        if today != self._trade_count_reset_date:
            self._daily_trade_count = 0
            self._trade_count_reset_date = today
            logger.info(f"Daily trade counter reset for {today}")

        # Check limit
        if self._daily_trade_count >= self._max_daily_trades:
            return False, f"Daily trade limit exceeded: {self._daily_trade_count}/{self._max_daily_trades}"

        return True, "Trade allowed"

    def can_open_position(
        self,
        size: float,
        market_id: str = None,
        check_daily_limit: bool = True
    ) -> tuple[bool, str]:
        """
        Check if a new position can be opened.
        
        Args:
            size: Size of the position in USDC
            market_id: Market ID for concentration check
            check_daily_limit: Whether to check daily loss limit
            
        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        # Check if trading is paused
        if self._trading_paused:
            return False, f"Trading paused: {self._pause_reason}"
        
        # Check minimum size
        if size < 0.10:
            return False, f"Position too small: ${size:.2f} (minimum: $0.10)"
        
        # Check maximum trade size
        if size > self.risk.max_trade_size:
            return False, f"Position exceeds max size: ${size:.2f} (max: ${self.risk.max_trade_size:.2f})"
        
        # Check available capital
        if size > self.capital:
            return False, f"Insufficient capital: ${size:.2f} > ${self.capital:.2f}"
        
        # Check max open positions
        if len(self._positions) >= self.risk.max_open_positions:
            return False, f"Max positions reached: {len(self._positions)}"
        
        # Check position concentration
        if self.capital > 0:
            concentration = (self.capital - size) / self.capital
            if concentration < self.risk.position_concentration:
                return False, f"Position concentration too high: {concentration:.1%}"

        # Check per-market exposure cap (Priority 4: Risk Management)
        if market_id:
            existing = self._positions.get(market_id)
            if existing:
                total_exposure = existing.size + size
                if total_exposure > self.risk.max_exposure_per_market:
                    return False, f"Per-market exposure cap exceeded: ${total_exposure:.2f} > ${self.risk.max_exposure_per_market:.2f}"

        # Check daily trade limit (Priority 4: Risk Management)
        if check_daily_limit:
            can_trade_limit, limit_reason = self._check_daily_trade_limit()
            if not can_trade_limit:
                return False, limit_reason

        # Check daily loss limit
        if check_daily_limit:
            today_stats = self._get_today_stats()
            if today_stats.realized_pnl < -self.risk.max_daily_loss:
                self._pause_trading(f"Daily loss limit exceeded: ${abs(today_stats.realized_pnl):.2f}")
                return False, f"Daily loss limit exceeded: ${abs(today_stats.realized_pnl):.2f}"

        return True, "Position allowed"
    
    def calculate_position_size(
        self,
        base_size: float,
        alpha: float = 0.0,
        max_multiplier: float = 3.0
    ) -> float:
        """
        Calculate position size based on alpha and risk parameters.

        Uses percentage-based sizing that scales with capital growth.

        Args:
            base_size: Base position size (can be ignored if using percentage)
            alpha: Current alpha (underpricing percentage)
            max_multiplier: Maximum size multiplier

        Returns:
            Adjusted position size
        """
        # Calculate base size as percentage of current capital
        max_trade_size_pct = getattr(self.risk, 'max_trade_size_pct', None)
        if max_trade_size_pct and self.capital > 0:
            # Use percentage-based sizing (scales with capital)
            base_size_pct = self.capital * max_trade_size_pct
        else:
            # Fallback to fixed base_size if percentage not configured
            base_size_pct = base_size

        # Scale size based on alpha
        if alpha > 0:
            # Positive alpha: increase size
            multiplier = 1 + min(alpha, 1.0) * (max_multiplier - 1)
            size = base_size_pct * multiplier
        else:
            # Negative or zero alpha: use base size
            size = base_size_pct

        # Clamp to fixed max trade size (hard limit)
        return min(size, self.risk.max_trade_size)
    
    def open_position(
        self,
        market_id: str,
        outcome: str,
        size: float,
        price: float,
        entry_alpha: float = 0.0
    ) -> Position:
        """
        Open a new position and track it.
        
        Args:
            market_id: Market ID
            outcome: "YES" or "NO"
            size: Position size in USDC
            price: Entry price
            entry_alpha: Alpha signal strength when entering
            
        Returns:
            New Position object
        """
        position = Position(
            market_id=market_id,
            outcome=outcome,
            size=size,
            avg_price=price,
            current_price=price,
            opened_at=datetime.now(),
            last_updated=datetime.now(),
            peak_pnl_percentage=0.0,
            entry_alpha=entry_alpha
        )
        
        self._positions[market_id] = position

        # Increment daily trade counter (Priority 4: Risk Management)
        self._daily_trade_count += 1

        # Update daily stats
        stats = self._get_today_stats()
        stats.trades_count += 1
        stats.total_volume += size

        logger.info(f"Opened position: {outcome} {size} @ ${price} on {market_id} (alpha={entry_alpha:.0%})")

        return position
    
    def update_position(
        self,
        market_id: str,
        current_price: float
    ) -> Optional[Position]:
        """
        Update position with current market price.
        
        Args:
            market_id: Market ID
            current_price: Current market price
            
        Returns:
            Updated Position or None
        """
        if market_id not in self._positions:
            return None
        
        position = self._positions[market_id]
        position.current_price = current_price
        position.last_updated = datetime.now()
        
        # Track peak PnL for trailing stop
        if position.pnl_percentage > position.peak_pnl_percentage:
            position.peak_pnl_percentage = position.pnl_percentage
        
        # Check for profit target (100% = auto-exit)
        if position.pnl_percentage >= (self.risk.profit_target * 100):
            logger.info(
                f"Position {market_id} hit profit target: {position.pnl_percentage:.1f}% "
                f"(peak: {position.peak_pnl_percentage:.1f}%)"
            )
        
        return position
    
    def close_position(
        self,
        market_id: str,
        exit_price: float
    ) -> Optional[Position]:
        """
        Close a position and record PnL.
        
        Args:
            market_id: Market ID
            exit_price: Exit price
            
        Returns:
            Closed Position or None
        """
        if market_id not in self._positions:
            return None
        
        position = self._positions[market_id]
        position.current_price = exit_price
        
        # Calculate realized PnL
        realized_pnl = position.unrealized_pnl

        # Record P&L for circuit breaker (Priority 4: Risk Management)
        self._recent_pnl.append((datetime.now(), realized_pnl))

        # Check circuit breaker
        breaker_triggered, breaker_reason = self.check_circuit_breaker()
        if breaker_triggered:
            self._pause_trading(breaker_reason)

        # Update daily stats
        stats = self._get_today_stats()
        stats.realized_pnl += realized_pnl

        if realized_pnl > 0:
            stats.winning_trades += 1
        else:
            stats.losing_trades += 1

        # Check if this triggered daily loss limit
        if stats.realized_pnl < -self.risk.max_daily_loss:
            self._pause_trading(f"Daily loss limit exceeded: ${abs(stats.realized_pnl):.2f}")

        # Remove position
        del self._positions[market_id]
        
        logger.info(
            f"Closed position {market_id}: PnL=${realized_pnl:.2f} "
            f"({position.pnl_percentage:.1f}%)"
        )
        
        return position
    
    def get_position(self, market_id: str) -> Optional[Position]:
        """Get a specific position"""
        return self._positions.get(market_id)
    
    def get_all_positions(self) -> List[Position]:
        """Get all open positions"""
        return list(self._positions.values())
    
    def get_profitable_positions(self) -> List[Position]:
        """Get positions with positive PnL"""
        return [p for p in self._positions.values() if p.unrealized_pnl > 0]
    
    def get_losing_positions(self) -> List[Position]:
        """Get positions with negative PnL"""
        return [p for p in self._positions.values() if p.unrealized_pnl < 0]
    
    def get_positions_for_auto_exit(self, profit_target: float = None) -> List[Position]:
        """
        Get positions that should be exited for profit.
        
        Args:
            profit_target: PnL percentage threshold (default: from config)
            
        Returns:
            List of positions to exit
        """
        target = profit_target or (self.risk.profit_target * 100)
        return [p for p in self._positions.values() if p.pnl_percentage >= target]
    
    def _get_today_stats(self) -> DailyStats:
        """Get or create daily stats for today"""
        today = datetime.now().date()
        
        if today not in self._daily_stats:
            self._daily_stats[today] = DailyStats(date=datetime.now())
        
        return self._daily_stats[today]
    
    def _pause_trading(self, reason: str):
        """Pause trading due to risk breach"""
        self._trading_paused = True
        self._pause_reason = reason
        logger.warning(f"Trading paused: {reason}")
    
    def resume_trading(self):
        """Resume trading after pause"""
        self._trading_paused = False
        self._pause_reason = None
        logger.info("Trading resumed")
    
    def reset_daily_stats(self):
        """Reset daily stats (called at start of new day)"""
        self._daily_stats.clear()
        logger.info("Daily stats reset")
    
    def get_daily_summary(self) -> Dict:
        """Get summary of today's trading activity"""
        stats = self._get_today_stats()
        return stats.to_dict()
    
    def get_all_state(self) -> Dict:
        """
        Get all risk manager state (Priority 5: Logging & Debugging).

        Returns:
            Complete state dictionary
        """
        return {
            "positions": {
                mid: {
                    "outcome": pos.outcome,
                    "size": pos.size,
                    "avg_price": pos.avg_price,
                    "current_price": pos.current_price,
                    "unrealized_pnl": pos.unrealized_pnl,
                    "pnl_percentage": pos.pnl_percentage,
                    "opened_at": pos.opened_at.isoformat(),
                    "entry_alpha": pos.entry_alpha
                }
                for mid, pos in self._positions.items()
            },
            "capital": self.capital,
            "exposure": self.total_exposure,
            "daily_stats": self._get_today_stats().to_dict(),
            "paused": self._trading_paused,
            "pause_reason": self._pause_reason,
            "daily_trade_count": self._daily_trade_count,
            "max_daily_trades": self._max_daily_trades,
            "circuit_breaker_triggered": self._circuit_breaker_triggered
        }

    def get_risk_summary(self) -> Dict:
        """Get current risk summary"""
        positions_value = sum(p.current_value for p in self._positions.values())
        positions_pnl = sum(p.unrealized_pnl for p in self._positions.values())
        
        return {
            "available_capital": self.capital,
            "total_exposure": positions_value,
            "open_positions_count": len(self._positions),
            "unrealized_pnl": positions_pnl,
            "daily_pnl": self._get_today_stats().realized_pnl,
            "trading_paused": self._trading_paused,
            "pause_reason": self._pause_reason
        }
    
    def validate_market_filter(
        self,
        market_price: float,
        market_id: str
    ) -> tuple[bool, str]:
        """
        Validate if a market meets our trading criteria.
        
        Args:
            market_price: Current YES price
            market_id: Market ID
            
        Returns:
            Tuple of (valid: bool, reason: str)
        """
        # Check price threshold (only trade cheap markets)
        if market_price >= self.config.scanner.price_threshold:
            return False, f"Price too high: ${market_price:.4f} (threshold: ${self.config.scanner.price_threshold:.4f})"
        
        # Check if we already have too much exposure to this market
        if market_id in self._positions:
            existing = self._positions[market_id]
            if existing.size >= self.risk.max_trade_size:
                return False, f"Max position already held in {market_id}"
        
        return True, "Market valid for trading"


def create_risk_manager(
    config: BotConfig,
    initial_capital: float = None
) -> RiskManager:
    """
    Factory function to create a RiskManager.
    
    Args:
        config: Bot configuration
        initial_capital: Starting capital
        
    Returns:
        Configured RiskManager instance
    """
    return RiskManager(
        config=config,
        initial_capital=initial_capital
    )

