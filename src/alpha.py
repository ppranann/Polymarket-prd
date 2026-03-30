"""
Alpha Detection Module
======================
Detects and acts on market mispricings (alpha):
- Calculates alpha = (Fair Price - Market Price) / Market Price
- Triggers increased position sizes when alpha > 20%
- Provides size multipliers based on mispricing magnitude
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .config import BotConfig
from .calculator import FairPriceCalculator, PriceCalculation
from .clients.tomorrow_io import WeatherData
from .utils import (
    classify_market_type, parse_bucket_range, is_celsius_market, c_to_f,
)

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
    # New fields for cross-market analysis
    market_type: str = "threshold"  # "threshold" or "max_temp"
    p_model: float = 0.0           # Model probability for this outcome
    p_implied: Optional[float] = None  # Implied probability from other market type
    distribution_mu: float = 0.0   # Distribution mean (°F)
    distribution_sigma: float = 0.0  # Distribution sigma (°F)
    bucket_range: Optional[str] = None  # e.g. "11-15" for bucket markets
    event_key: str = ""  # city+date key

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
            "recommendation": self.recommendation,
            "market_type": self.market_type,
            "p_model": self.p_model,
            "p_implied": self.p_implied,
            "distribution_mu": self.distribution_mu,
            "distribution_sigma": self.distribution_sigma,
            "bucket_range": self.bucket_range,
            "event_key": self.event_key,
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
        
        # Dynamic threshold: scale with market price so cheap markets
        # need less absolute edge (they have higher relative EV).
        # Floor at config threshold (0.05), scales up for expensive markets.
        effective_threshold = max(self.alpha_config.threshold, market_price * 0.25)

        # Messy middle: if fair price is between 0.3 and 0.7 and absolute
        # alpha is below the wider threshold, force HOLD. These markets
        # are genuinely uncertain and our edge is too thin to trust.
        messy_threshold = self.alpha_config.messy_middle_threshold
        if 0.3 < fair_price < 0.7 and abs(alpha) < messy_threshold:
            action = "HOLD"
        elif alpha > effective_threshold:
            action = "BUY"
            self._buy_signals += 1
        elif alpha < -effective_threshold:
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
    
    def analyze_market_group(
        self,
        group,  # MarketGroup from scanner
        mu: float,
        sigma: float,
        prices: Dict[str, float],
    ) -> List[AlphaSignal]:
        """
        Analyze all markets in a city+date group using a shared distribution.

        Produces signals for both threshold and bucket markets, using
        cross-market implied probabilities for sanity checking.

        Args:
            group: MarketGroup with threshold_markets and bucket_markets
            mu: Distribution mean in °F
            sigma: Distribution sigma in °F
            prices: Dict of market_id → current YES price

        Returns:
            List of AlphaSignal for markets worth trading
        """
        signals: List[AlphaSignal] = []
        bucket_threshold = getattr(self.alpha_config, 'bucket_threshold', 0.10)

        # --- Step 1: Compute model probabilities for bucket markets ---
        bucket_info: List[Dict] = []  # [{market, low_f, high_f, p_model, price}]
        for market in group.bucket_markets:
            low, high = parse_bucket_range(market.question)
            # Convert to Fahrenheit if needed
            if is_celsius_market(market.question):
                low_f = c_to_f(low) if low != float('-inf') else float('-inf')
                high_f = c_to_f(high) if high != float('inf') else float('inf')
            else:
                low_f = low
                high_f = high

            bucket_probs = self.calculator.calculate_bucket_probabilities(
                mu, sigma, [(low_f, high_f)]
            )
            p_model = bucket_probs[0]
            price = prices.get(market.id, market.yes_price)

            bucket_info.append({
                "market": market,
                "low_f": low_f,
                "high_f": high_f,
                "p_model": p_model,
                "price": price,
                "range_str": f"{low}-{high}",
            })

        # Minimum |z| required to generate a signal for threshold markets.
        # When forecast ≈ threshold (|z| < min_z), the model outputs p ≈ 0.5
        # regardless of market price, creating fake alpha. Markets correctly
        # price these near-uncertain events much lower than 50%.
        min_z = getattr(self.alpha_config, 'min_z_score', 1.0)

        # --- Step 2: For threshold markets, compute model prob + implied from buckets ---
        for market in group.threshold_markets:
            price = prices.get(market.id, market.yes_price)
            cond_type = self.calculator._parse_condition_type(market.question)
            threshold_f = self.calculator.get_temperature_threshold_from_condition(
                market.question
            )

            if threshold_f is None:
                logger.warning(f"Cannot extract threshold from: {market.question[:60]}")
                continue

            z = (threshold_f - mu) / sigma if sigma > 0 else 0

            # Skip markets where forecast is too close to threshold (unreliable zone).
            # The Normal model outputs ~50% here but markets consistently price lower,
            # meaning NWS/Tomorrow.io has systematic warm bias vs Polymarket resolution station.
            if abs(z) < min_z:
                logger.info(
                    f"SKIP {market.id[:8]}: |z|={abs(z):.2f} < {min_z:.1f} "
                    f"(mu={mu:.1f}°F threshold={threshold_f:.1f}°F sigma={sigma:.1f}°F — "
                    f"forecast too close to threshold, model has no edge)"
                )
                continue

            # Model probability from shared distribution
            p_model = self.calculator.calculate_threshold_probability(
                mu, sigma, threshold_f, cond_type
            )

            # Implied probability from bucket markets
            p_implied = self._implied_threshold_from_buckets(
                threshold_f, cond_type, bucket_info
            )

            # Cross-check: skip if model and bucket-implied strongly disagree
            cross_check_ok = True
            if p_implied is not None and len(bucket_info) >= 3:
                model_says_buy = (p_model > price)
                implied_says_buy = (p_implied > price)
                if model_says_buy != implied_says_buy:
                    logger.info(
                        f"Cross-check CONFLICT for {market.id[:8]}: "
                        f"P_model={p_model:.3f} vs P_implied={p_implied:.3f} vs P_market={price:.3f}"
                    )
                    cross_check_ok = False

            alpha = p_model - price
            confidence = self.calculator._calculate_confidence(z)

            # Generate signal using existing logic
            signal = self.analyze_market(
                market_id=market.id,
                fair_price=p_model,
                market_price=price,
                z_score=z,
                confidence=confidence,
            )

            # Override with cross-market metadata
            signal.market_type = "threshold"
            signal.p_model = p_model
            signal.p_implied = p_implied
            signal.distribution_mu = mu
            signal.distribution_sigma = sigma
            signal.event_key = group.event_key

            # Downgrade to HOLD if cross-check failed
            if not cross_check_ok and signal.action == "BUY":
                signal.action = "HOLD"
                signal.recommendation = (
                    f"HOLD: cross-check conflict (model={p_model:.3f}, "
                    f"implied={p_implied:.3f}, market={price:.3f})"
                )

            # Cheap YES trap filter: cheap markets are cheap because the model
            # is miscalibrated or the market knows something we don't.
            # Only buy YES below min_yes_buy_price if p_model is genuinely strong.
            min_yes = getattr(self.alpha_config, 'min_yes_buy_price', 0.10)
            if signal.action == "BUY" and price < min_yes and p_model < 0.25:
                signal.action = "HOLD"
                signal.recommendation = (
                    f"HOLD: cheap YES trap (price=${price:.3f} < min=${min_yes:.2f}, "
                    f"p_model={p_model:.3f} < 0.25)"
                )
                logger.info(
                    f"CHEAP_YES_FILTER: {market.id[:8]} price={price:.3f} p_model={p_model:.3f} — blocked"
                )

            signals.append(signal)

        # --- Step 3: For bucket markets, ladder top N underpriced buckets ---
        # Instead of only the single best bucket, we add up to max_ladder_buckets
        # adjacent underpriced buckets. One winner covers the rest.
        max_ladder = getattr(self.alpha_config, 'max_ladder_buckets', 2)
        ladder_candidates = [
            info for info in bucket_info
            if (info["p_model"] - info["price"]) > bucket_threshold
        ]
        # Sort by EV descending, take top N
        ladder_candidates.sort(key=lambda x: x["p_model"] - x["price"], reverse=True)
        ladder_candidates = ladder_candidates[:max_ladder]

        for info in ladder_candidates:
            market = info["market"]
            p_model = info["p_model"]
            price = info["price"]
            ev = p_model - price
            confidence = min(0.8, 0.5 + ev)

            signal = self.analyze_market(
                market_id=market.id,
                fair_price=p_model,
                market_price=price,
                z_score=0.0,
                confidence=confidence,
            )
            signal.market_type = "max_temp"
            signal.p_model = p_model
            signal.distribution_mu = mu
            signal.distribution_sigma = sigma
            signal.bucket_range = info["range_str"]
            signal.event_key = group.event_key

            if signal.action == "BUY":
                min_yes = getattr(self.alpha_config, 'min_yes_buy_price', 0.10)
                if price < min_yes and p_model < 0.25:
                    logger.info(
                        f"CHEAP_YES_FILTER bucket: {market.id[:8]} price={price:.3f} "
                        f"p_model={p_model:.3f} — blocked"
                    )
                else:
                    signals.append(signal)

        # --- Step 4: Bucket integrity check (model-free structural arb) ---
        # Σ P_market(bucket_i) should ≈ 1.0. Significant deviation = arb opportunity.
        if len(bucket_info) >= 3:
            already_trading = {s.market_id for s in signals}
            bucket_sum = sum(info["price"] for info in bucket_info)

            if bucket_sum < 0.92:
                # Aggregate underpriced → buy cheapest convex bucket not already in signals
                arb_candidates = [
                    info for info in bucket_info
                    if info["market"].id not in already_trading
                ]
                if arb_candidates:
                    best = max(arb_candidates, key=lambda x: x["p_model"] - x["price"])
                    ev = best["p_model"] - best["price"]
                    logger.info(
                        f"BUCKET INTEGRITY: sum={bucket_sum:.3f} < 0.92 (underpriced). "
                        f"Best arb: {best['range_str']} EV={ev:.3f}"
                    )
                    if ev > 0.05:
                        sig = self.analyze_market(
                            market_id=best["market"].id,
                            fair_price=best["p_model"],
                            market_price=best["price"],
                            z_score=0.0,
                            confidence=0.65,
                        )
                        sig.market_type = "bucket_arb"
                        sig.p_model = best["p_model"]
                        sig.distribution_mu = mu
                        sig.distribution_sigma = sigma
                        sig.bucket_range = best["range_str"]
                        sig.event_key = group.event_key
                        if sig.action == "BUY":
                            signals.append(sig)

            elif bucket_sum > 1.08:
                logger.info(
                    f"BUCKET INTEGRITY: sum={bucket_sum:.3f} > 1.08 (overpriced aggregate). "
                    f"No actionable long — monitor for YES deflation."
                )

        return signals

    def _implied_threshold_from_buckets(
        self,
        threshold_f: float,
        direction: str,
        bucket_info: List[Dict],
    ) -> Optional[float]:
        """
        Derive P(T >= threshold) or P(T <= threshold) from bucket market prices.

        For "above" (T >= threshold): sum prices of all buckets whose range
        overlaps with T >= threshold.

        Args:
            threshold_f: Temperature threshold in °F
            direction: "above" or "below"
            bucket_info: List of bucket dicts with low_f, high_f, price

        Returns:
            Implied probability, or None if insufficient bucket data
        """
        if len(bucket_info) < 2:
            return None

        p_implied = 0.0
        any_overlap = False
        for info in bucket_info:
            low_f = info["low_f"]
            high_f = info["high_f"]
            price = info["price"]

            if direction == "above":
                # Bucket fully above threshold → add full price
                if low_f >= threshold_f:
                    p_implied += price
                    any_overlap = True
                # Bucket partially overlaps → add proportional share
                elif high_f > threshold_f and low_f < threshold_f:
                    if high_f != float('inf') and low_f != float('-inf'):
                        overlap = (high_f - threshold_f) / (high_f - low_f)
                        p_implied += price * overlap
                    else:
                        p_implied += price * 0.5  # rough estimate
                    any_overlap = True
            else:  # below
                if high_f <= threshold_f:
                    p_implied += price
                    any_overlap = True
                elif low_f < threshold_f and high_f > threshold_f:
                    if high_f != float('inf') and low_f != float('-inf'):
                        overlap = (threshold_f - low_f) / (high_f - low_f)
                        p_implied += price * overlap
                    else:
                        p_implied += price * 0.5
                    any_overlap = True

        # If no bucket overlaps the relevant side, we have no evidence → skip cross-check
        if not any_overlap:
            return None

        return min(p_implied, 1.0)

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

