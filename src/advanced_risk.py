"""
Advanced Risk Management Module
================================
Enhanced risk management features:
- Kelly Criterion Position Sizing
- Trailing Stop-Loss
- Volatility-Adjusted Sizing
- Correlation-Based Diversification
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np

from .config import BotConfig
from .risk import RiskManager, Position

logger = logging.getLogger(__name__)


@dataclass
class KellyResult:
    """Result of Kelly Criterion calculation"""
    kelly_fraction: float  # Full Kelly
    half_kelly_fraction: float  # Conservative (recommended)
    quarter_kelly_fraction: float  # Very conservative
    edge: float
    win_rate: float
    avg_win: float
    avg_loss: float
    optimal_size: float
    recommended_size: float
    
    def to_dict(self) -> Dict:
        return {
            "kelly_fraction": self.kelly_fraction,
            "half_kelly_fraction": self.half_kelly_fraction,
            "quarter_kelly_fraction": self.quarter_kelly_fraction,
            "edge": self.edge,
            "optimal_size": self.optimal_size,
            "recommended_size": self.recommended_size
        }


@dataclass
class TrailingStopState:
    """State for trailing stop implementation"""
    market_id: str
    entry_price: float
    entry_time: datetime
    trailing_pct: float  # e.g., 0.20 = 20% trailing
    highest_price: float
    stop_price: float
    triggered: bool = False
    triggered_at: Optional[datetime] = None
    profit_pct: float = 0.0
    
    def update(self, current_price: float):
        """Update trailing stop with new price"""
        if self.triggered:
            return
        
        # Update highest price
        if current_price > self.highest_price:
            self.highest_price = current_price
        
        # Calculate new stop price
        self.stop_price = self.highest_price * (1 - self.trailing_pct)
        
        # Check if stop triggered
        if current_price <= self.stop_price:
            self.triggered = True
            self.triggered_at = datetime.now()
            self.profit_pct = (current_price - self.entry_price) / self.entry_price


class AdvancedRiskManager:
    """
    Advanced risk management with Kelly Criterion, trailing stops, 
    and volatility-adjusted sizing.
    """
    
    def __init__(
        self,
        config: BotConfig,
        base_risk_manager: RiskManager
    ):
        """
        Initialize advanced risk manager.
        
        Args:
            config: Bot configuration
            base_risk_manager: Existing risk manager
        """
        self.config = config
        self.base = base_risk_manager
        
        # Risk parameters
        self.kelly_multiplier = 0.5  # Use half-Kelly for safety
        self.trailing_stop_pct = 0.20  # 20% trailing stop
        self.volatility_lookback = 24  # hours
        
        # State tracking
        self._trailing_stops: Dict[str, TrailingStopState] = {}
        self._trade_history: List[Dict] = []
        
        # Performance tracking
        self._wins = 0
        self._losses = 0
        self._total_wins = 0.0
        self._total_losses = 0.0
    
    def calculate_kelly(
        self,
        win_rate: float = None,
        avg_win: float = None,
        avg_loss: float = None,
        trade_history: List[Dict] = None
    ) -> KellyResult:
        """
        Calculate Kelly Criterion optimal position size.
        
        Kelly Formula:
            f* = (bp - q) / b
        Where:
            b = odds received (profit/loss ratio)
            p = probability of winning
            q = probability of losing (1-p)
            
        Args:
            win_rate: Historical win rate (0-1)
            avg_win: Average win amount
            avg_loss: Average loss amount
            trade_history: List of trades to calculate from
            
        Returns:
            KellyResult with optimal sizes
        """
        # Calculate from trade history if not provided
        if trade_history is not None:
            trades = trade_history
            wins = [t for t in trades if t.get('profit', 0) > 0]
            losses = [t for t in trades if t.get('profit', 0) <= 0]
            
            if len(wins) + len(losses) < 10:
                logger.warning("Not enough trade history for Kelly calculation")
                return self._default_kelly_result()
            
            win_rate = len(wins) / len(trades)
            avg_win = np.mean([w.get('profit', 0) for w in wins]) if wins else 0
            avg_loss = abs(np.mean([l.get('profit', 0) for l in losses])) if losses else 0
        
        # Use provided values
        win_rate = win_rate or (self._wins / (self._wins + self._losses)) if (self._wins + self._losses) > 0 else 0.5
        avg_win = avg_win or (self._total_wins / self._wins) if self._wins > 0 else 0.1
        avg_loss = avg_loss or (abs(self._total_losses) / self._losses) if self._losses > 0 else 0.1
        
        # Calculate odds (profit/loss ratio)
        b = avg_win / avg_loss if avg_loss > 0 else 1.0
        p = win_rate
        q = 1 - win_rate
        
        # Full Kelly
        kelly = (b * p - q) / b if b > 0 else 0
        
        # Edge calculation
        edge = (p * avg_win) - (q * avg_loss)
        
        # Ensure kelly is bounded
        kelly = max(0, min(kelly, 1.0))
        
        # Conservative variants
        half_kelly = kelly * 0.5
        quarter_kelly = kelly * 0.25
        
        # Calculate optimal sizes
        base_capital = self.config.risk.initial_capital
        optimal_size = base_capital * kelly
        recommended_size = base_capital * half_kelly * self.kelly_multiplier
        
        return KellyResult(
            kelly_fraction=kelly,
            half_kelly_fraction=half_kelly,
            quarter_kelly_fraction=quarter_kelly,
            edge=edge,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            optimal_size=optimal_size,
            recommended_size=recommended_size
        )
    
    def _default_kelly_result(self) -> KellyResult:
        """Return default Kelly result when calculation not possible"""
        return KellyResult(
            kelly_fraction=0.10,  # Conservative default
            half_kelly_fraction=0.05,
            quarter_kelly_fraction=0.025,
            edge=0.0,
            win_rate=0.5,
            avg_win=0.1,
            avg_loss=0.1,
            optimal_size=self.config.risk.max_trade_size,
            recommended_size=self.config.risk.max_trade_size * 0.5
        )
    
    def setup_trailing_stop(
        self,
        market_id: str,
        entry_price: float,
        trailing_pct: float = None
    ) -> TrailingStopState:
        """
        Set up a trailing stop for a position.
        
        Args:
            market_id: Market identifier
            entry_price: Entry price
            trailing_pct: Trailing percentage (default: 20%)
            
        Returns:
            TrailingStopState object
        """
        pct = trailing_pct or self.trailing_stop_pct
        
        state = TrailingStopState(
            market_id=market_id,
            entry_price=entry_price,
            entry_time=datetime.now(),
            trailing_pct=pct,
            highest_price=entry_price,
            stop_price=entry_price * (1 - pct)
        )
        
        self._trailing_stops[market_id] = state
        logger.info(f"Trailing stop set for {market_id[:8]}...: {pct:.0%}, stop @ ${state.stop_price:.4f}")
        
        return state
    
    def update_trailing_stops(
        self,
        prices: Dict[str, float]
    ) -> List[TrailingStopState]:
        """
        Update all trailing stops with current prices.
        
        Args:
            prices: Dictionary of market_id -> current price
            
        Returns:
            List of triggered trailing stops
        """
        triggered = []
        
        for market_id, state in self._trailing_stops.items():
            if market_id in prices:
                state.update(prices[market_id])
                
                if state.triggered:
                    triggered.append(state)
                    logger.info(
                        f"Trailing stop triggered for {market_id[:8]}...: "
                        f"Profit: {state.profit_pct:.1%}"
                    )
        
        return triggered
    
    def get_trailing_stop_status(self, market_id: str) -> Optional[TrailingStopState]:
        """Get trailing stop status for a market"""
        return self._trailing_stops.get(market_id)
    
    def calculate_volatility_adjusted_size(
        self,
        base_size: float,
        current_volatility: float,
        avg_volatility: float = 1.0
    ) -> float:
        """
        Calculate position size adjusted for volatility.
        
        Size reduces when volatility is high.
        
        Args:
            base_size: Base position size
            current_volatility: Current market volatility
            avg_volatility: Average historical volatility
            
        Returns:
            Adjusted position size
        """
        if current_volatility <= 0:
            return base_size
        
        volatility_ratio = current_volatility / avg_volatility
        
        # Size = base / volatility_ratio
        # If volatility is 2x average, size is halved
        adjusted_size = base_size / max(volatility_ratio, 0.5)
        
        # Ensure minimum size
        return max(adjusted_size, base_size * 0.25)
    
    def calculate_position_size_advanced(
        self,
        base_size: float,
        alpha: float = 0.0,
        kelly_result: KellyResult = None,
        volatility: float = 1.0,
        current_capital: float = None
    ) -> float:
        """
        Calculate final position size with all adjustments.
        
        Combines:
        - Kelly sizing
        - Alpha scaling
        - Volatility adjustment
        - Capital constraint
        
        Args:
            base_size: Base size from config
            alpha: Current alpha
            kelly_result: Kelly calculation result
            volatility: Current volatility
            current_capital: Available capital
            
        Returns:
            Final adjusted position size
        """
        # Start with Kelly or base size
        if kelly_result:
            size = min(kelly_result.recommended_size, base_size)
        else:
            size = base_size
        
        # Scale by alpha (if underpriced, increase size)
        if alpha > 0:
            alpha_multiplier = 1 + min(alpha, 1.0) * 2  # Up to 3x
            size *= alpha_multiplier
        
        # Adjust for volatility
        size = self.calculate_volatility_adjusted_size(
            base_size=size,
            current_volatility=volatility
        )
        
        # Cap at maximum
        max_size = self.config.risk.max_trade_size
        size = min(size, max_size)
        
        # Check against capital
        capital = current_capital or self.config.risk.initial_capital
        size = min(size, capital * 0.2)  # Max 20% of capital per trade
        
        return round(size, 2)
    
    def check_correlation_limit(
        self,
        market_id: str,
        existing_positions: Dict[str, Position],
        correlation_threshold: float = 0.7
    ) -> Tuple[bool, str]:
        """
        Check if adding a position would exceed correlation limits.
        
        Args:
            market_id: New market to add
            existing_positions: Current open positions
            correlation_threshold: Max correlation allowed
            
        Returns:
            Tuple of (allowed, reason)
        """
        if not existing_positions:
            return True, "No existing positions"
        
        # Simple keyword-based correlation check
        # In production, use actual correlation matrix
        new_market_words = set(market_id.lower().split())
        
        correlated_count = 0
        for pos_id in existing_positions.keys():
            pos_words = set(pos_id.lower().split())
            overlap = len(new_market_words & pos_words)
            if overlap >= 2:  # At least 2 common words
                correlated_count += 1
        
        max_correlated = 2  # Max 2 correlated positions
        if correlated_count >= max_correlated:
            return False, f"Correlation limit reached ({correlated_count} correlated positions)"
        
        return True, "Position allowed"
    
    def record_trade_result(
        self,
        market_id: str,
        entry_price: float,
        exit_price: float,
        size: float,
        profit: float
    ):
        """
        Record a trade result for performance tracking.
        
        Args:
            market_id: Market traded
            entry_price: Entry price
            exit_price: Exit price
            size: Position size
            profit: Realized profit/loss
        """
        # Update trade history
        self._trade_history.append({
            'market_id': market_id,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'size': size,
            'profit': profit,
            'timestamp': datetime.now().isoformat()
        })
        
        # Update statistics
        if profit > 0:
            self._wins += 1
            self._total_wins += profit
        else:
            self._losses += 1
            self._total_losses += abs(profit)
        
        # Clean up trailing stop if exists
        if market_id in self._trailing_stops:
            del self._trailing_stops[market_id]
        
        # Keep only last 100 trades
        if len(self._trade_history) > 100:
            self._trade_history = self._trade_history[-100:]
    
    def get_performance_summary(self) -> Dict:
        """Get performance summary"""
        total_trades = self._wins + self._losses
        
        if total_trades == 0:
            return {
                "total_trades": 0,
                "win_rate": 0,
                "avg_win": 0,
                "avg_loss": 0,
                "profit_factor": 0
            }
        
        win_rate = self._wins / total_trades
        avg_win = self._total_wins / self._wins if self._wins > 0 else 0
        avg_loss = self._total_losses / self._losses if self._losses > 0 else 0
        
        profit_factor = (
            self._total_wins / self._total_losses 
            if self._total_losses > 0 else float('inf')
        )
        
        return {
            "total_trades": total_trades,
            "wins": self._wins,
            "losses": self._losses,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "total_profit": self._total_wins - self._total_losses,
            "kelly": self.calculate_kelly().to_dict(),
            "active_trailing_stops": len(self._trailing_stops)
        }
    
    def should_exit_trailing(
        self,
        market_id: str,
        current_price: float
    ) -> bool:
        """
        Check if trailing stop should trigger exit.
        
        Args:
            market_id: Market to check
            current_price: Current price
            
        Returns:
            True if should exit
        """
        state = self._trailing_stops.get(market_id)
        if not state:
            return False
        
        state.update(current_price)
        return state.triggered


def create_advanced_risk_manager(
    config: BotConfig,
    base_risk_manager: RiskManager
) -> AdvancedRiskManager:
    """
    Factory function to create an AdvancedRiskManager.
    
    Args:
        config: Bot configuration
        base_risk_manager: Existing risk manager
        
    Returns:
        Configured AdvancedRiskManager instance
    """
    return AdvancedRiskManager(
        config=config,
        base_risk_manager=base_risk_manager
    )

