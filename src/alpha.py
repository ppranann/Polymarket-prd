"""
Alpha Detection Module
======================
Detects and acts on market mispricings (alpha):
- Calculates alpha = (Fair Price - Market Price) / Market Price
- Triggers increased position sizes when alpha > 20%
- Provides size multipliers based on mispricing magnitude
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .config import BotConfig
from .calculator import FairPriceCalculator, PriceCalculation
from .clients.tomorrow_io import WeatherData

logger = logging.getLogger(__name__)


@dataclass
class AlphaSignal:
    """Represents an alpha signal"""
    market_id: str
    alpha: float  # Mispricing percentage
    fair_price: float
    market_price: float
    z_score: float
    confidence: float
    action: str  # "BUY", "SELL", "HOLD"
    size_multiplier: float
    expected_return: float
    recommendation: str
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "market_id": self.market_id,
            "alpha": self.alpha,
            "fair_price": self.fair_price,
            "market_price": self.market_price,
            "z_score": self.z_score,
            "confidence": self.confidence,
            "action": self.action,
            "size_multiplier": self.size_multiplier,
            "expected_return": self.expected_return,
            "recommendation": self.recommendation
        }


class AlphaDetector:
    """
    Detects market mispricings and calculates optimal position sizes.
    
    Alpha Formula:
        Alpha = (Fair Price - Market Price) / Market Price
    
    Examples:
        - Fair Price: $0.80, Market Price: $0.60
          Alpha = (0.80 - 0.60) / 0.60 = 0.333 = 33.3% underpriced
        
        - Fair Price: $0.30, Market Price: $0.50
          Alpha = (0.30 - 0.50) / 0.50 = -0.40 = 40% overpriced
    
    Size Multiplier:
        Multiplier = 1 + min(alpha, 1.0) * (max_multiplier - 1)
        
        Where max_multiplier is typically 3.0x
    """
    
    def __init__(
        self,
        config: BotConfig,
        calculator: FairPriceCalculator
    ):
        """
        Initialize alpha detector.
        
        Args:
            config: Bot configuration
            calculator: Fair price calculator
        """
        self.config = config
        self.calculator = calculator
        self.alpha_config = config.alpha
        
        # Track signals
        self._last_signal: Optional[AlphaSignal] = None
        self._total_signals = 0
        self._buy_signals = 0
        self._sell_signals = 0
    
    def calculate_alpha(
        self,
        fair_price: float,
        market_price: float
    ) -> float:
        """
        Calculate alpha (absolute mispricing in probability space).

        Alpha = Fair Price - Market Price

        Args:
            fair_price: Calculated fair price
            market_price: Current market price

        Returns:
            Alpha as absolute difference (e.g., 0.10 = 10 cents underpriced)
        """
        alpha = fair_price - market_price

        # Cap alpha at reasonable values
        return max(-0.95, min(0.95, alpha))
    
    def calculate_alpha_ratio(
        self,
        fair_price: float,
        market_price: float
    ) -> float:
        """
        Calculate alpha as a ratio.
        
        Args:
            fair_price: Calculated fair price
            market_price: Current market price
            
        Returns:
            Alpha ratio (e.g., 1.5 = 50% underpriced)
        """
        if market_price <= 0:
            return 1.0
        
        return fair_price / market_price
    
    def calculate_size_multiplier(
        self,
        alpha: float,
        max_multiplier: float = None
    ) -> float:
        """
        Calculate position size multiplier based on alpha.
        
        The more underpriced, the larger the position.
        
        Args:
            alpha: Calculated alpha
            max_multiplier: Maximum multiplier (uses config if not provided)
            
        Returns:
            Size multiplier (1.0 to max_multiplier)
        """
        max_mult = max_multiplier or self.alpha_config.size_multiplier_max
        
        if alpha <= 0:
            # No upside, use base size
            return 1.0
        
        # Scale multiplier from 1.0 to max_multiplier based on alpha
        # Alpha of 0.0 → multiplier of 1.0
        # Alpha of 1.0 → multiplier of max_multiplier
        multiplier = 1.0 + alpha * (max_mult - 1.0)
        
        return min(multiplier, max_mult)
    
    def calculate_kelly_fraction(
        self,
        fair_price: float,
        market_price: float,
        kelly_scale: float = 0.15,
        confidence: float = 1.0
    ) -> float:
        """
        Calculate fractional Kelly criterion bet size as fraction of bankroll.

        Kelly: f* = (b*p - q) / b
        Where b = net odds, p = true probability, q = 1 - p

        Confidence acts as a multiplicative brake: when the model is
        extrapolating (extreme z-scores), confidence drops and Kelly
        fraction shrinks accordingly.

        Args:
            fair_price: Our estimated probability (p)
            market_price: Market price (determines odds)
            kelly_scale: Fraction of full Kelly to use (0.15 = conservative)
            confidence: Confidence level (0-1), scales Kelly fraction

        Returns:
            Fraction of bankroll to bet (0.0 to ~0.15)
        """
        if market_price <= 0.01 or market_price >= 0.99:
            return 0.0
        if fair_price <= market_price:
            return 0.0  # No edge, don't bet

        b = (1.0 / market_price) - 1.0  # Net odds
        p = fair_price
        q = 1.0 - p

        kelly = (b * p - q) / b

        if kelly <= 0:
            return 0.0

        # Apply fractional Kelly × confidence brake
        return kelly * kelly_scale * confidence

    def calculate_expected_return(
        self,
        fair_price: float,
        market_price: float
    ) -> float:
        """
        Calculate expected return if price converges to fair value.

        Args:
            fair_price: Calculated fair price
            market_price: Current market price

        Returns:
            Expected return as absolute difference
        """
        return fair_price - market_price
    
    def is_buy_signal(
        self,
        alpha: float,
        threshold: float = None
    ) -> bool:
        """
        Check if alpha indicates a buy signal.
        
        Args:
            alpha: Calculated alpha
            threshold: Alpha threshold (uses config if not provided)
            
        Returns:
            True if alpha exceeds threshold
        """
        thresh = threshold or self.alpha_config.threshold
        return alpha > thresh
    
    def is_sell_signal(
        self,
        alpha: float,
        threshold: float = None
    ) -> bool:
        """
        Check if alpha indicates a sell signal.
        
        Args:
            alpha: Calculated alpha
            threshold: Alpha threshold (uses config if not provided)
            
        Returns:
            True if alpha is below negative threshold
        """
        thresh = threshold or self.alpha_config.threshold
        return alpha < -thresh
    
    def analyze_market(
        self,
        market_id: str,
        fair_price: float,
        market_price: float,
        z_score: float = 0.0,
        confidence: float = 0.5
    ) -> AlphaSignal:
        """
        Analyze a market and generate an alpha signal.
        
        Args:
            market_id: Market identifier
            fair_price: Calculated fair price
            market_price: Current market price
            z_score: Z-score from calculation
            confidence: Confidence level from calculation
            
        Returns:
            AlphaSignal with all analysis details
        """
        # Calculate alpha
        alpha = self.calculate_alpha(fair_price, market_price)
        
        # Calculate size multiplier
        multiplier = self.calculate_size_multiplier(alpha)
        
        # Calculate expected return
        expected_return = self.calculate_expected_return(fair_price, market_price)
        
        # Messy middle: if fair price is between 0.3 and 0.7 and absolute
        # alpha is below the wider threshold, force HOLD. These markets
        # are genuinely uncertain and our edge is too thin to trust.
        messy_threshold = self.alpha_config.messy_middle_threshold
        if 0.3 < fair_price < 0.7 and abs(alpha) < messy_threshold:
            action = "HOLD"
        elif alpha > self.alpha_config.threshold:
            action = "BUY"
            self._buy_signals += 1
        elif alpha < -self.alpha_config.threshold:
            action = "SELL"
            self._sell_signals += 1
        else:
            action = "HOLD"
        
        self._total_signals += 1
        
        # Generate recommendation
        if action == "BUY":
            recommendation = (
                f"Strong buy signal: Market is ${float(alpha):.2f} underpriced. "
                f"Fair price: ${fair_price:.4f}, Market: ${market_price:.4f}. "
                f"Size multiplier: {multiplier:.1f}x"
            )
        elif action == "SELL":
            recommendation = (
                f"Sell signal: Market is ${abs(float(alpha)):.2f} overpriced. "
                f"Fair price: ${fair_price:.4f}, Market: ${market_price:.4f}"
            )
        else:
            recommendation = (
                f"Hold: Market is fairly priced. "
                f"Spread: ${float(alpha):.2f}"
            )
        
        signal = AlphaSignal(
            market_id=market_id,
            alpha=alpha,
            fair_price=fair_price,
            market_price=market_price,
            z_score=z_score,
            confidence=confidence,
            action=action,
            size_multiplier=multiplier,
            expected_return=expected_return,
            recommendation=recommendation
        )
        
        self._last_signal = signal
        
        return signal
    
    def analyze_with_weather(
        self,
        market_id: str,
        weather_data: WeatherData,
        market_condition: str,
        market_price: float
    ) -> AlphaSignal:
        """
        Analyze a market using weather data.
        
        Args:
            market_id: Market identifier
            weather_data: Weather forecast data
            market_condition: Market condition description
            market_price: Current market price
            
        Returns:
            AlphaSignal with analysis
        """
        # Calculate fair price using weather data
        calculation = self.calculator.calculate_fair_price_from_weather(
            weather_data=weather_data,
            market_condition=market_condition,
            market_price=market_price
        )
        
        return self.analyze_market(
            market_id=market_id,
            fair_price=calculation.fair_price,
            market_price=market_price,
            z_score=calculation.z_score,
            confidence=calculation.confidence
        )
    
    def get_action_size(
        self,
        base_size: float,
        alpha: float
    ) -> float:
        """
        Get the adjusted position size for a given alpha.
        
        Args:
            base_size: Base position size
            alpha: Calculated alpha
            
        Returns:
            Adjusted position size
        """
        multiplier = self.calculate_size_multiplier(alpha)
        adjusted_size = base_size * multiplier
        
        # Clamp to max trade size
        max_size = self.config.risk.max_trade_size
        
        return min(adjusted_size, max_size)
    
    def should_increase_position(
        self,
        alpha: float,
        threshold: float = None
    ) -> bool:
        """
        Check if position size should be increased.
        
        Args:
            alpha: Calculated alpha
            threshold: Alpha threshold
            
        Returns:
            True if position should be increased
        """
        thresh = threshold or self.alpha_config.threshold
        return alpha > thresh
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get alpha detector statistics"""
        return {
            "total_signals": self._total_signals,
            "buy_signals": self._buy_signals,
            "sell_signals": self._sell_signals,
            "hold_signals": self._total_signals - self._buy_signals - self._sell_signals,
            "buy_ratio": (
                self._buy_signals / self._total_signals
                if self._total_signals > 0 else 0
            ),
            "last_signal": (
                self._last_signal.to_dict()
                if self._last_signal else None
            )
        }
    
    def reset_statistics(self):
        """Reset signal statistics"""
        self._total_signals = 0
        self._buy_signals = 0
        self._sell_signals = 0
        self._last_signal = None


def create_alpha_detector(
    config: BotConfig,
    calculator: FairPriceCalculator
) -> AlphaDetector:
    """
    Factory function to create an AlphaDetector.
    
    Args:
        config: Bot configuration
        calculator: Fair price calculator
        
    Returns:
        Configured AlphaDetector instance
    """
    return AlphaDetector(
        config=config,
        calculator=calculator
    )

