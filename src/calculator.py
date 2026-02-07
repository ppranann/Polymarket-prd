"""
Fair Price Calculator Module
=============================
Calculates "fair prices" for weather markets using Tomorrow.io data
and Normal Distribution CDF (Standard Bell Curve).

Mathematical Model:
- Fair Price = CDF(temperature_deviation)
- σ (sigma) = 3.5°F (1.9°C) standard deviation
- CDF gives probability of temperature being above/below threshold
"""

import logging
import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
from scipy import stats

from .clients.tomorrow_io import WeatherData

logger = logging.getLogger(__name__)


@dataclass
class PriceCalculation:
    """Result of a fair price calculation"""
    fair_price: float
    z_score: float
    confidence: float
    alpha: float
    market_price: float
    forecast_temp: float
    expected_temp: float
    sigma: float
    condition_type: str  # "above", "below", "precipitation"
    recommendation: str  # "BUY", "SELL", "HOLD"
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for logging"""
        return {
            "fair_price": self.fair_price,
            "z_score": self.z_score,
            "confidence": self.confidence,
            "alpha": self.alpha,
            "market_price": self.market_price,
            "forecast_temp": self.forecast_temp,
            "expected_temp": self.expected_temp,
            "sigma": self.sigma,
            "condition_type": self.condition_type,
            "recommendation": self.recommendation
        }


class FairPriceCalculator:
    """
    Calculator for determining fair prices of weather prediction markets.
    
    Uses Normal Distribution CDF to convert temperature forecasts into
    probability-based prices.
    
    Key Parameters:
    - sigma: Standard deviation for temperature volatility (default: 3.5°F)
    - default_expected_temp: Historical average temperature (default: 65°F)
    """
    
    # Default sigma in Fahrenheit (3.5°F = 1.9°C)
    DEFAULT_SIGMA_FAHRENHEIT = 3.5

    # Default expected temperature (historical average)
    DEFAULT_EXPECTED_TEMP = 65.0

    # Price validation constants (Priority 0: Critical Safety)
    MIN_PRICE = 0.001  # Minimum valid price
    MAX_PRICE = 0.999  # Maximum valid price
    
    # Temperature thresholds for common market conditions
    TEMP_THRESHOLDS = {
        "above_50": 50.0,
        "above_55": 55.0,
        "above_60": 60.0,
        "above_65": 65.0,
        "above_70": 70.0,
        "above_75": 75.0,
        "above_80": 80.0,
        "above_85": 85.0,
        "above_90": 90.0,
        "below_50": 50.0,
        "below_55": 55.0,
        "below_60": 60.0,
        "below_65": 65.0,
        "below_70": 70.0,
        "below_75": 75.0,
        "below_80": 80.0,
        "below_85": 85.0,
        "below_90": 90.0,
    }
    
    def __init__(
        self,
        sigma: float = None,
        default_expected_temp: float = None
    ):
        """
        Initialize the fair price calculator.
        
        Args:
            sigma: Standard deviation for temperature (default: 3.5°F)
            default_expected_temp: Default expected temperature (default: 65°F)
        """
        self.sigma = sigma or self.DEFAULT_SIGMA_FAHRENHEIT
        self.default_expected_temp = default_expected_temp or self.DEFAULT_EXPECTED_TEMP

        # Pre-compute for performance
        self._normal_dist = stats.norm(loc=0, scale=1)

    def _validate_price(self, price: float, context: str = "") -> Optional[float]:
        """
        Validate a price value for sanity (Priority 0: Critical Safety).

        Checks for:
        - NaN values
        - Negative values
        - Values greater than 1.0

        Args:
            price: Price value to validate
            context: Context string for error logging

        Returns:
            Validated price or None if invalid
        """
        # Check for NaN
        if math.isnan(price):
            logger.error(f"CRITICAL: NaN price detected{' in ' + context if context else ''}")
            return None

        # Check for negative
        if price < 0:
            logger.error(f"CRITICAL: Negative price detected: {price:.4f}{' in ' + context if context else ''}")
            return None

        # Check for > 1.0
        if price > 1.0:
            logger.error(f"CRITICAL: Price > 1.0 detected: {price:.4f}{' in ' + context if context else ''}")
            return None

        return price
    
    def calculate_fair_price(
        self,
        market_condition: str,
        forecasted_temp: float,
        expected_temp: float = None,
        sigma: float = None,
        market_price: float = 0.5
    ) -> PriceCalculation:
        """
        Calculate the fair price for a weather market.
        
        Args:
            market_condition: Condition description (e.g., "temp_above_70", "rain")
            forecasted_temp: Temperature from Tomorrow.io forecast
            expected_temp: Expected/historical temperature (optional)
            sigma: Volatility parameter (optional)
            market_price: Current market price (optional)
            
        Returns:
            PriceCalculation with fair price and alpha
        """
        exp_temp = expected_temp or self.default_expected_temp
        sig = sigma or self.sigma
        
        # Parse market condition to determine calculation method
        condition_type = self._parse_condition_type(market_condition)
        
        # Calculate z-score
        z_score = (forecasted_temp - exp_temp) / sig
        
        # Calculate fair price using CDF
        if condition_type == "above":
            # For "above" conditions, higher temp = higher probability
            # Fair price = P(T > threshold) = 1 - CDF(z)
            fair_price = 1 - self._normal_dist.cdf(z_score)
        elif condition_type == "below":
            # For "below" conditions, lower temp = higher probability
            # Fair price = P(T < threshold) = CDF(z)
            fair_price = self._normal_dist.cdf(z_score)
        elif condition_type == "precipitation":
            # For precipitation, use probability directly
            fair_price = min(forecasted_temp, 1.0) if forecasted_temp <= 1.0 else 0.5
        elif condition_type == "range":
            # For range markets, use probability of being in range
            fair_price = self._calculate_range_probability(
                market_condition, forecasted_temp, exp_temp, sig
            )
        else:
            # Default: use midpoint with small adjustment
            fair_price = 0.5
            z_score = 0

        # Validate price before clamping (Priority 0: Critical Safety)
        validated_price = self._validate_price(fair_price, f"calculate_fair_price for {market_condition[:30]}")
        if validated_price is None:
            # Return safe fallback on validation failure
            logger.warning(f"Price validation failed, using fallback price 0.50")
            fair_price = 0.5
        else:
            fair_price = validated_price

        # Clamp fair price to valid range
        fair_price = np.clip(fair_price, self.MIN_PRICE, self.MAX_PRICE)
        
        # Calculate alpha: how under/overpriced is the market?
        alpha = self._calculate_alpha(fair_price, market_price)
        
        # Calculate confidence based on distance from mean
        confidence = self._calculate_confidence(z_score)
        
        # Generate recommendation
        recommendation = self._generate_recommendation(
            fair_price, market_price, alpha
        )
        
        return PriceCalculation(
            fair_price=fair_price,
            z_score=z_score,
            confidence=confidence,
            alpha=alpha,
            market_price=market_price,
            forecast_temp=forecasted_temp,
            expected_temp=exp_temp,
            sigma=sig,
            condition_type=condition_type,
            recommendation=recommendation
        )
    
    def calculate_fair_price_from_weather(
        self,
        weather_data: WeatherData,
        market_condition: str,
        market_price: float = 0.5,
        expected_temp: float = None
    ) -> PriceCalculation:
        """
        Calculate fair price using WeatherData object.

        Args:
            weather_data: WeatherData from Tomorrow.io client
            market_condition: Market condition description
            market_price: Current market price
            expected_temp: Override expected temperature

        Returns:
            PriceCalculation result
        """
        # Get relevant temperature for market type
        relevant_temp = self._get_relevant_temperature(
            weather_data, market_condition
        )

        # Validate temperature input (Priority 0: Critical Safety)
        if relevant_temp is None or math.isnan(relevant_temp) or relevant_temp < -100 or relevant_temp > 200:
            logger.error(f"CRITICAL: Invalid temperature from weather data: {relevant_temp}")
            # Return safe fallback calculation
            return PriceCalculation(
                fair_price=0.5,
                z_score=0.0,
                confidence=0.0,
                alpha=0.0,
                market_price=market_price,
                forecast_temp=65.0,
                expected_temp=65.0,
                sigma=self.sigma,
                condition_type="error",
                recommendation="HOLD - Invalid weather data"
            )

        # Extract threshold from market question if not provided
        if expected_temp is None:
            extracted_threshold = self.get_temperature_threshold_from_condition(market_condition)
            if extracted_threshold is not None:
                expected_temp = extracted_threshold
                logger.debug(f"✅ Extracted threshold: {extracted_threshold}°F from '{market_condition[:50]}...'")
            else:
                logger.warning(f"❌ Failed to extract threshold from: '{market_condition[:80]}' - using default {self.default_expected_temp}°F")
                expected_temp = self.default_expected_temp

        return self.calculate_fair_price(
            market_condition=market_condition,
            forecasted_temp=relevant_temp,
            expected_temp=expected_temp,
            market_price=market_price
        )
    
    def _parse_condition_type(self, condition: str) -> str:
        """
        Parse market condition string to determine calculation type.
        
        Args:
            condition: Market condition description
            
        Returns:
            Condition type: "above", "below", "precipitation", "range", or "other"
        """
        condition_lower = condition.lower()
        
        if "above" in condition_lower or "gt" in condition_lower or ">" in condition:
            return "above"
        elif "below" in condition_lower or "lt" in condition_lower or "<" in condition:
            return "below"
        elif any(word in condition_lower for word in ["rain", "precip", "snow", "storm"]):
            return "precipitation"
        elif "between" in condition_lower or "range" in condition_lower:
            return "range"
        else:
            return "other"
    
    def _get_relevant_temperature(
        self,
        weather_data: WeatherData,
        condition: str
    ) -> float:
        """
        Get the relevant temperature from WeatherData for the condition.
        
        Args:
            weather_data: Weather data
            condition: Market condition
            
        Returns:
            Temperature value to use for calculation
        """
        condition_type = self._parse_condition_type(condition)
        
        if condition_type == "above":
            # Use max temp for "above" conditions
            return weather_data.temp_max
        elif condition_type == "below":
            # Use min temp for "below" conditions
            return weather_data.temp_min
        else:
            # Use mean for other conditions
            return weather_data.temp_mean
    
    def _calculate_range_probability(
        self,
        condition: str,
        forecasted_temp: float,
        expected_temp: float,
        sigma: float
    ) -> float:
        """
        Calculate probability of temperature being in a range.
        
        Args:
            condition: Range condition string
            forecasted_temp: Forecasted temperature
            expected_temp: Expected temperature
            sigma: Standard deviation
            
        Returns:
            Probability of temperature in range
        """
        # Parse range from condition (simplified - would need more robust parsing)
        import re
        
        # Look for patterns like "between_60_and_70" or "60_to_70"
        patterns = [
            r'between\s*(\d+\.?\d*)\s*and\s*(\d+\.?\d*)',
            r'(\d+\.?\d*)\s*to\s*(\d+\.?\d*)',
            r'range\s*(\d+\.?\d*)\s*-\s*(\d+\.?\d*)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, condition.lower())
            if match:
                low = float(match.group(1))
                high = float(match.group(2))
                
                # Calculate probability using normal distribution
                z_low = (low - expected_temp) / sigma
                z_high = (high - expected_temp) / sigma
                
                return self._normal_dist.cdf(z_high) - self._normal_dist.cdf(z_low)
        
        # Default: return 0.5 if range parsing fails
        return 0.5
    
    def _calculate_alpha(
        self,
        fair_price: float,
        market_price: float
    ) -> float:
        """
        Calculate alpha (mispricing percentage).
        
        Alpha = (Fair Price - Market Price) / Market Price
        
        Positive alpha means market is underpriced (buy signal)
        Negative alpha means market is overpriced (sell signal)
        
        Args:
            fair_price: Calculated fair price
            market_price: Current market price
            
        Returns:
            Alpha as a decimal (e.g., 0.20 = 20% underpriced)
        """
        if market_price <= 0.01:
            # Avoid division by very small numbers
            return 0.0
        
        alpha = (fair_price - market_price) / market_price
        
        # Cap alpha at reasonable values
        return np.clip(alpha, -2.0, 2.0)
    
    def _calculate_confidence(self, z_score: float) -> float:
        """
        Calculate confidence based on z-score distance from mean.
        
        Args:
            z_score: Standard deviations from expected value
            
        Returns:
            Confidence level (0.0 to 1.0)
        """
        # Confidence increases as we move away from mean (extreme predictions)
        # But caps at high values
        abs_z = abs(z_score)
        
        if abs_z <= 1:
            return 0.5 + (abs_z * 0.1)  # 0.5 to 0.6
        elif abs_z <= 2:
            return 0.6 + ((abs_z - 1) * 0.2)  # 0.6 to 0.8
        elif abs_z <= 3:
            return 0.8 + ((abs_z - 2) * 0.1)  # 0.8 to 0.9
        else:
            return 0.95  # Very confident for extreme z-scores
    
    def _generate_recommendation(
        self,
        fair_price: float,
        market_price: float,
        alpha: float,
        alpha_threshold: float = 0.20
    ) -> str:
        """
        Generate trading recommendation based on alpha.
        
        Args:
            fair_price: Calculated fair price
            market_price: Current market price
            alpha: Calculated alpha
            alpha_threshold: Minimum alpha for action
            
        Returns:
            Recommendation: "BUY", "SELL", or "HOLD"
        """
        if alpha > alpha_threshold:
            return "BUY"
        elif alpha < -alpha_threshold:
            return "SELL"
        else:
            return "HOLD"
    
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
    
    def is_underpriced(
        self,
        fair_price: float,
        market_price: float,
        alpha_threshold: float = 0.15,
        snipe_price_threshold: float = 0.15
    ) -> bool:
        """
        Check if a market is underpriced.
        
        Args:
            fair_price: Calculated fair price
            market_price: Current market price
            alpha_threshold: Minimum alpha threshold
            snipe_price_threshold: Maximum price threshold for sniping
            
        Returns:
            True if market is underpriced
        """
        alpha = self._calculate_alpha(fair_price, market_price)
        return alpha > alpha_threshold and market_price < snipe_price_threshold
    
    def is_overpriced(
        self,
        fair_price: float,
        market_price: float,
        threshold: float = 0.15
    ) -> bool:
        """
        Check if a market is overpriced.
        
        Args:
            fair_price: Calculated fair price
            market_price: Current market price
            threshold: Minimum alpha threshold
            
        Returns:
            True if market is overpriced
        """
        alpha = self._calculate_alpha(fair_price, market_price)
        return alpha < -threshold
    
    def get_temperature_threshold_from_condition(
        self,
        condition: str
    ) -> Optional[float]:
        """
        Extract temperature threshold from market condition.

        CRITICAL FIX: Handles Celsius/Fahrenheit conversion!
        - London/Seoul markets use °C
        - NYC markets use °F
        - Converts everything to °F for comparison

        Args:
            condition: Market condition string

        Returns:
            Temperature threshold in Fahrenheit, or None
        """
        # Check predefined thresholds
        for key, threshold in self.TEMP_THRESHOLDS.items():
            if key.replace("_", " ") in condition.lower():
                return threshold

        # Try to extract from pattern
        import re

        # CRITICAL: Detect if temperature is in Celsius or Fahrenheit
        is_celsius = '°C' in condition or '°c' in condition or ' C' in condition

        # First try to match range patterns like "30-34°F", "30 to 34°C"
        # Enhanced regex to support NEGATIVE temperatures (e.g., "-4°C")
        range_patterns = [
            r'(-?\d+\.?\d*)\s*(?:to|-|–|~)\s*(-?\d+\.?\d*)\s*[°]?[CF]?',  # Supports negative
            r'(-?\d+\.?\d*)\s*[-–]\s*(-?\d+\.?\d*)',  # Simple dash
        ]

        for range_pattern in range_patterns:
            range_match = re.search(range_pattern, condition, re.IGNORECASE)
            if range_match:
                low = float(range_match.group(1))
                high = float(range_match.group(2))
                midpoint = (low + high) / 2.0

                # Convert Celsius to Fahrenheit if needed
                if is_celsius:
                    midpoint_f = (midpoint * 9/5) + 32
                    logger.debug(f"Extracted {low}-{high}°C, midpoint: {midpoint}°C = {midpoint_f:.1f}°F")
                    return midpoint_f
                else:
                    logger.debug(f"Extracted {low}-{high}°F, midpoint: {midpoint}°F")
                    return midpoint

        # Look for single temperature patterns: "13°C or higher", "-4°C", "15°F or below"
        # CRITICAL: Support negative temps (common in winter)
        temp_patterns = [
            r'(-?\d+\.?\d*)\s*[°]?[CF]?\s*(?:or\s+)?(?:higher|above|over)',  # "13°C or higher"
            r'(-?\d+\.?\d*)\s*[°]?[CF]?\s*(?:or\s+)?(?:lower|below|under)',  # "15°F or below"
            r'\?\s*-\s*(-?\d+\.?\d*)\s*[°]?[CF]?(?:\s|$)',  # "February 7? - -4°C"
        ]

        for pattern in temp_patterns:
            match = re.search(pattern, condition, re.IGNORECASE)
            if match:
                temp = float(match.group(1))

                # Convert Celsius to Fahrenheit if needed
                if is_celsius:
                    temp_f = (temp * 9/5) + 32
                    logger.debug(f"✅ Extracted {temp}°C = {temp_f:.1f}°F")
                    return temp_f
                else:
                    logger.debug(f"✅ Extracted {temp}°F")
                    return temp

        return None


def create_calculator(
    sigma: float = 3.5,
    default_expected_temp: float = 65.0
) -> FairPriceCalculator:
    """
    Factory function to create a FairPriceCalculator.
    
    Args:
        sigma: Standard deviation for temperature
        default_expected_temp: Default expected temperature
        
    Returns:
        Configured FairPriceCalculator instance
    """
    return FairPriceCalculator(
        sigma=sigma,
        default_expected_temp=default_expected_temp
    )

