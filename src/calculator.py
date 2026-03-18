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
import re
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
    
    # Default sigma in Fahrenheit (3.5°F = 1.9°C) - used as fallback
    DEFAULT_SIGMA_FAHRENHEIT = 3.5

    # Dynamic sigma by forecast horizon (days out), in °F.
    # Calibrated against Seoul/Incheon (RKSI) vs Tomorrow.io actuals (Mar 2026, n=4):
    # observed std dev of (forecast - actual) ≈ 0.5–2°F. Previous wide values
    # (4–10°F) caused the model to assign 10–18% probability to thresholds
    # that were 3–4°C above forecast — inflating EV on consistently losing positions.
    SIGMA_BY_HORIZON = {
        0: 2.0,   # Same-day: Tomorrow.io is very accurate once day is underway
        1: 3.0,   # 1 day out: calibrated vs Wunderground RKSI actuals
        2: 4.5,   # 2 days out: NWP error grows
        3: 6.0,   # 3 days out: wider but still much tighter than previous 10°F
    }

    # Calibrated forecast error by lead time (hours), in °F.
    # bias = mean(forecast - actual); positive → forecast runs hot.
    # sigma = std(forecast - actual).
    # Sigma calibrated from Seoul/RKSI actuals (Mar 2026, n=4): observed ≈0.5°F,
    # using conservative 2–6°F to account for small sample and city-to-city variation.
    # Bias set to 0.0 globally — Seoul showed -1.1°F cold bias but other cities
    # are uncalibrated. Per-city corrections live in CITY_BIAS_F below.
    FORECAST_ERROR = {
        0:  {"bias": 0.0, "sigma": 2.0},   # Same-day
        24: {"bias": 0.0, "sigma": 3.0},   # 1 day out
        48: {"bias": 0.0, "sigma": 4.5},   # 2 days out (primary trading horizon)
        72: {"bias": 0.0, "sigma": 6.0},   # 3 days out
    }

    # Per-city bias in °F: mean(Tomorrow.io forecast − Wunderground actual).
    # Positive → Tomorrow.io runs hot. Applied on top of FORECAST_ERROR["bias"].
    # Calibrate by collecting (forecast_temp_f, actual_resolution_temp) pairs from Supabase.
    CITY_BIAS_F = {
        # bias = mean(Tomorrow.io forecast_f − Wunderground actual_f)
        # negative → Tomorrow.io runs cold; positive → runs hot
        "seoul":    -5.7,  # n=6, mid-Mar 2026: large cold bias during spring warming
        "incheon":  -5.7,  # same station (RKSI)
        "atlanta":  -1.5,  # n=1, Mar 2026
        "dallas":   +4.5,  # n=2, Mar 2026: Tomorrow.io runs hot
        "miami":    -3.5,  # n=2, Mar 2026: Tomorrow.io runs cold
        # "london": 0.0,   # uncalibrated
        # "nyc":    0.0,   # uncalibrated
    }

    # Extra sigma for range/bucket markets
    RANGE_SIGMA_ADDON = 1.0

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

        # Rolling biases from Supabase (refreshed each scan cycle by main.py).
        # Overrides CITY_BIAS_F when populated with enough data points.
        self._rolling_biases: Dict[str, float] = {}

    def update_rolling_biases(self, biases: Dict[str, float]) -> None:
        """Update per-city bias from rolling Supabase data. Called by main.py."""
        self._rolling_biases = biases
        if biases:
            logger.info(
                "Rolling bias update: "
                + ", ".join(f"{c}={v:+.2f}°F" for c, v in sorted(biases.items()))
            )

    def _interpolate_forecast_error(self, lead_hours: int) -> Dict[str, float]:
        """
        Interpolate forecast error stats for a given lead time.

        Args:
            lead_hours: Hours until resolution

        Returns:
            Dict with "bias" and "sigma" keys
        """
        breakpoints = sorted(self.FORECAST_ERROR.keys())

        # Clamp to range
        if lead_hours <= breakpoints[0]:
            return self.FORECAST_ERROR[breakpoints[0]]
        if lead_hours >= breakpoints[-1]:
            return self.FORECAST_ERROR[breakpoints[-1]]

        # Linear interpolation between bracketing breakpoints
        for i in range(len(breakpoints) - 1):
            lo, hi = breakpoints[i], breakpoints[i + 1]
            if lo <= lead_hours <= hi:
                frac = (lead_hours - lo) / (hi - lo)
                lo_err = self.FORECAST_ERROR[lo]
                hi_err = self.FORECAST_ERROR[hi]
                return {
                    "bias": lo_err["bias"] + frac * (hi_err["bias"] - lo_err["bias"]),
                    "sigma": lo_err["sigma"] + frac * (hi_err["sigma"] - lo_err["sigma"]),
                }

        return self.FORECAST_ERROR[breakpoints[-1]]

    def get_distribution(
        self,
        forecast_max_temp_f: float,
        lead_hours: int,
        forecast_spread_f: float = 0.0,
        city: Optional[str] = None,
    ) -> Tuple[float, float]:
        """
        Get the adjusted Normal distribution parameters for max temperature.

        Uses calibrated forecast error to debias and set uncertainty.
        All values in Fahrenheit.

        Args:
            forecast_max_temp_f: Tomorrow.io forecast max temp in °F
            lead_hours: Hours until market resolution
            forecast_spread_f: temp_max - temp_min from forecast (°F), used
                               to widen sigma if forecast itself is uncertain
            city: Optional city slug for per-city bias correction (e.g. "seoul")

        Returns:
            Tuple of (mu, sigma) in °F for T ~ Normal(mu, sigma²)
        """
        error = self._interpolate_forecast_error(lead_hours)
        city_key = city.lower() if city else None
        # Prefer live rolling bias (self-calibrating); fall back to hardcoded table.
        if city_key and city_key in self._rolling_biases:
            city_bias = self._rolling_biases[city_key]
        else:
            city_bias = self.CITY_BIAS_F.get(city_key, 0.0) if city_key else 0.0
        mu = forecast_max_temp_f - error["bias"] - city_bias
        sigma = error["sigma"]

        # Widen sigma if Tomorrow.io's own spread is wider than our model
        if forecast_spread_f > 0:
            spread_sigma = forecast_spread_f / 2.0
            if spread_sigma > sigma:
                logger.info(
                    f"Distribution: widening sigma {sigma:.1f}°F → {spread_sigma:.1f}°F "
                    f"(forecast spread: {forecast_spread_f:.1f}°F)"
                )
                sigma = spread_sigma

        return mu, sigma

    def calculate_bucket_probabilities(
        self,
        mu: float,
        sigma: float,
        buckets: list,
    ) -> list:
        """
        Calculate P(T in bucket) for each bucket using Normal CDF.

        Args:
            mu: Distribution mean (°F)
            sigma: Distribution std dev (°F)
            buckets: List of (low, high) tuples in °F.
                     Use float('-inf') / float('inf') for unbounded.

        Returns:
            List of probabilities (same order as buckets), summing to ~1.0
        """
        probs = []
        for low, high in buckets:
            if low == float('-inf'):
                p = self._normal_dist.cdf((high - mu) / sigma)
            elif high == float('inf'):
                p = 1.0 - self._normal_dist.cdf((low - mu) / sigma)
            else:
                p = (
                    self._normal_dist.cdf((high - mu) / sigma)
                    - self._normal_dist.cdf((low - mu) / sigma)
                )
            probs.append(min(max(p, 0.001), 0.98))  # Floor 0.1%, ceiling 98%

        # Normalize only for multi-bucket queries (mutually exclusive outcomes).
        # For single-bucket calls (alpha.py queries each market individually),
        # normalization would compute p/p = 1.0, completely masking the actual
        # CDF probability and causing P_model=1.000 on wide-range buckets.
        if len(probs) > 1:
            total = sum(probs)
            if total > 0:
                probs = [p / total for p in probs]

        return probs

    def calculate_threshold_probability(
        self,
        mu: float,
        sigma: float,
        threshold_f: float,
        direction: str,
    ) -> float:
        """
        Calculate P(T >= threshold) or P(T <= threshold) from the distribution.

        Args:
            mu: Distribution mean (°F)
            sigma: Distribution std dev (°F)
            threshold_f: Temperature threshold in °F
            direction: "above" or "below"

        Returns:
            Probability in [0.001, 0.98]
        """
        z = (threshold_f - mu) / sigma
        if direction == "above":
            p = 1.0 - self._normal_dist.cdf(z)
        else:
            p = self._normal_dist.cdf(z)

        p_clipped = float(np.clip(p, self.MIN_PRICE, 0.98))
        logger.info(
            f"THRESHOLD_PROB: dir={direction} mu={mu:.2f}°F threshold={threshold_f:.2f}°F "
            f"sigma={sigma:.2f}°F z={z:.3f} p_raw={p:.4f} p_final={p_clipped:.4f}"
        )
        return p_clipped

    def get_dynamic_sigma(self, market_condition: str) -> float:
        """
        Calculate sigma based on forecast horizon extracted from the question.

        Args:
            market_condition: Market question (e.g. "Highest temperature in London on February 12?")

        Returns:
            Sigma in °F adjusted for forecast horizon
        """
        from datetime import datetime

        month_map = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12
        }
        match = re.search(r'on\s+(\w+)\s+(\d{1,2})', market_condition, re.IGNORECASE)
        if match:
            month_name = match.group(1).lower()
            day = int(match.group(2))
            month = month_map.get(month_name)
            if month:
                try:
                    year = datetime.now().year
                    target = datetime(year, month, day).date()
                    days_out = max(0, (target - datetime.now().date()).days)
                    sigma = self.SIGMA_BY_HORIZON.get(days_out, 2.0 + days_out * 1.5)
                    return sigma
                except ValueError:
                    pass

        return self.sigma  # Fallback to default

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
        # Model: T ~ N(forecast, sigma²), z = (forecast - threshold) / sigma
        # P(T > threshold) = Φ(z) = CDF(z)
        # P(T < threshold) = 1 - Φ(z) = 1 - CDF(z)
        if condition_type == "above":
            # P(T > threshold): higher forecast → higher z → higher probability ✓
            fair_price = self._normal_dist.cdf(z_score)
        elif condition_type == "below":
            # P(T < threshold): lower forecast → more negative z → higher probability ✓
            fair_price = 1 - self._normal_dist.cdf(z_score)
        elif condition_type == "precipitation":
            # For precipitation, use probability directly
            fair_price = min(forecasted_temp, 1.0) if forecasted_temp <= 1.0 else 0.5
        elif condition_type == "range":
            # For range markets, use probability of being in range
            fair_price = self._calculate_range_probability(
                market_condition, forecasted_temp, exp_temp, sig
            )
        else:
            # Unparseable condition: assume no edge, set fair = market price
            # (was 0.5, which created false $0.47 BUY signals on $0.03 markets)
            logger.warning(f"Unknown condition type '{condition_type}' for: {market_condition[:60]}")
            fair_price = market_price
            z_score = 0

        # Validate price before clamping (Priority 0: Critical Safety)
        validated_price = self._validate_price(fair_price, f"calculate_fair_price for {market_condition[:30]}")
        if validated_price is None:
            # Return safe fallback on validation failure
            logger.warning(f"Price validation failed, using fallback price 0.50")
            fair_price = 0.5
        else:
            fair_price = validated_price

        # Clamp fair price: never believe 98%+ (fat tails exist)
        fair_price = np.clip(fair_price, self.MIN_PRICE, 0.98)
        
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
            else:
                logger.warning(f"❌ Failed to extract threshold from: '{market_condition[:80]}' - using default {self.default_expected_temp}°F")
                expected_temp = self.default_expected_temp

        # Dynamic sigma based on forecast horizon
        dynamic_sigma = self.get_dynamic_sigma(market_condition)

        # Widen sigma if Tomorrow.io's forecast spread is wider than our default.
        # When temp_max - temp_min > 2*sigma, our normal model is overconfident.
        if hasattr(weather_data, 'temp_max') and hasattr(weather_data, 'temp_min'):
            forecast_spread_f = weather_data.temp_max - weather_data.temp_min
            if forecast_spread_f > 0:
                spread_sigma = forecast_spread_f / 2.0
                if spread_sigma > dynamic_sigma:
                    logger.info(
                        f"Widening sigma: {dynamic_sigma:.1f}°F → {spread_sigma:.1f}°F "
                        f"(forecast spread: {forecast_spread_f:.1f}°F)"
                    )
                    dynamic_sigma = spread_sigma

        # Parse condition type for logging
        cond_type = self._parse_condition_type(market_condition)
        logger.info(
            f"CALC: type={cond_type} | threshold={expected_temp:.1f}°F | "
            f"forecast={relevant_temp:.1f}°F | sigma={dynamic_sigma:.1f}°F | "
            f"question='{market_condition[:60]}'"
        )

        return self.calculate_fair_price(
            market_condition=market_condition,
            forecasted_temp=relevant_temp,
            expected_temp=expected_temp,
            sigma=dynamic_sigma,
            market_price=market_price
        )
    
    def _parse_condition_type(self, condition: str) -> str:
        """
        Parse market condition string to determine calculation type.

        Polymarket question formats:
        - "13°C or higher" → above
        - "15°F or below" → below
        - "-4°C" (standalone threshold, no direction) → range (single-degree bucket)
        - "26-27°F" (explicit range) → range

        Args:
            condition: Market condition description

        Returns:
            Condition type: "above", "below", "precipitation", "range", or "other"
        """
        condition_lower = condition.lower()

        # CRITICAL FIX: Polymarket uses "higher"/"lower", not just "above"/"below"
        above_keywords = ["above", "higher", "or more", "at least", "over", "gt"]
        below_keywords = ["below", "lower", "or less", "at most", "under", "lt"]

        # Check explicit direction keywords FIRST (unambiguous)
        if any(kw in condition_lower for kw in above_keywords) or ">" in condition:
            return "above"
        elif any(kw in condition_lower for kw in below_keywords) or "<" in condition:
            return "below"
        elif any(word in condition_lower for word in ["rain", "precip", "snow", "storm"]):
            return "precipitation"

        # CRITICAL FIX: Detect range/bucket markets BEFORE "highest temperature" fallback
        # "26-27°F", "30-34°F", etc. → explicit range
        if re.search(r'-?\d+\s*[-–]\s*-?\d+', condition):
            return "range"

        # Bare number without direction keyword → single-degree bucket
        # e.g. "Highest temperature in Seoul? - 4°C", "...? - -2°C", "...? - 10°C"
        if re.search(r'\?\s*-\s*-?\d+\s*[°]?[CFcf]?\s*$', condition):
            return "range"

        if "between" in condition_lower or "range" in condition_lower:
            return "range"

        return "other"
    
    def _get_relevant_temperature(
        self,
        weather_data: WeatherData,
        condition: str
    ) -> float:
        """
        Get the relevant temperature from WeatherData for the condition.

        CRITICAL: All "Highest temperature" questions use temp_max regardless
        of whether the condition is above, below, or range. The question asks
        about the daily HIGH, so the forecast HIGH is the right center.

        Args:
            weather_data: Weather data
            condition: Market condition

        Returns:
            Temperature value to use for calculation
        """
        # "Highest temperature" questions always use the forecast high
        if "highest temperature" in condition.lower():
            return weather_data.temp_max

        condition_type = self._parse_condition_type(condition)

        if condition_type == "above":
            return weather_data.temp_max
        elif condition_type == "below":
            return weather_data.temp_min
        else:
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

        Model: T ~ N(forecasted_temp, sigma²)
        P(low ≤ T ≤ high) = Φ((high - forecast)/σ) - Φ((low - forecast)/σ)

        Handles Polymarket formats:
        - "26-27°F" → P(26 ≤ T ≤ 27)
        - "10°C" (bare number) → P(10°C ≤ T < 11°C) = P(50°F ≤ T < 51.8°F)

        Args:
            condition: Range condition string
            forecasted_temp: Forecasted temperature in °F (distribution center)
            expected_temp: Extracted threshold (not used for range calc)
            sigma: Standard deviation in °F

        Returns:
            Probability of temperature in range
        """
        # Fat tails for range/bucket markets: widen sigma to avoid overconfidence
        sigma = sigma + self.RANGE_SIGMA_ADDON

        is_celsius = '°C' in condition or '°c' in condition

        # Pattern 1: Explicit range "26-27°F", "30-34°F", "-2-0°C"
        range_match = re.search(r'(-?\d+\.?\d*)\s*[-–]\s*(-?\d+\.?\d*)\s*[°]?[CFcf]?', condition)
        if range_match:
            low = float(range_match.group(1))
            high = float(range_match.group(2))
            if is_celsius:
                low = low * 9.0 / 5.0 + 32.0
                high = high * 9.0 / 5.0 + 32.0

            z_low = (low - forecasted_temp) / sigma
            z_high = (high - forecasted_temp) / sigma
            prob = self._normal_dist.cdf(z_high) - self._normal_dist.cdf(z_low)
            logger.debug(
                f"Range {low:.0f}-{high:.0f}°F: z=[{z_low:.2f},{z_high:.2f}] "
                f"P={prob:.4f} (forecast={forecasted_temp:.1f}°F)"
            )
            return min(max(prob, 0.001), 0.98)

        # Pattern 2: Bare number "? - 4°C" or "? - -2°C" → 1-degree bucket
        bare_match = re.search(r'\?\s*-\s*(-?\d+\.?\d*)\s*[°]?[CFcf]?', condition)
        if bare_match:
            temp = float(bare_match.group(1))
            if is_celsius:
                low = temp * 9.0 / 5.0 + 32.0
                high = (temp + 1) * 9.0 / 5.0 + 32.0  # 1°C bucket
            else:
                low = temp
                high = temp + 1.0  # 1°F bucket

            z_low = (low - forecasted_temp) / sigma
            z_high = (high - forecasted_temp) / sigma
            prob = self._normal_dist.cdf(z_high) - self._normal_dist.cdf(z_low)
            logger.debug(
                f"Bucket {low:.0f}-{high:.0f}°F: z=[{z_low:.2f},{z_high:.2f}] "
                f"P={prob:.4f} (forecast={forecasted_temp:.1f}°F)"
            )
            return min(max(prob, 0.001), 0.98)

        # Fallback: old patterns
        patterns = [
            r'between\s*(-?\d+\.?\d*)\s*and\s*(-?\d+\.?\d*)',
            r'(-?\d+\.?\d*)\s*to\s*(-?\d+\.?\d*)',
        ]
        for pattern in patterns:
            match = re.search(pattern, condition.lower())
            if match:
                low = float(match.group(1))
                high = float(match.group(2))
                z_low = (low - forecasted_temp) / sigma
                z_high = (high - forecasted_temp) / sigma
                return min(max(self._normal_dist.cdf(z_high) - self._normal_dist.cdf(z_low), 0.001), 0.98)

        logger.warning(f"Could not parse range from: '{condition[:60]}' — using fallback 0.5")
        return 0.5
    
    def _calculate_alpha(
        self,
        fair_price: float,
        market_price: float
    ) -> float:
        """
        Calculate alpha (absolute mispricing in probability space).

        Alpha = Fair Price - Market Price

        Positive alpha means market is underpriced (buy signal)
        Negative alpha means market is overpriced (sell signal)

        Args:
            fair_price: Calculated fair price
            market_price: Current market price

        Returns:
            Alpha as absolute difference (e.g., 0.10 = 10 cents underpriced)
        """
        alpha = fair_price - market_price

        # Cap alpha at reasonable values
        return np.clip(alpha, -0.95, 0.95)
    
    def _calculate_confidence(self, z_score: float) -> float:
        """
        Calculate confidence based on z-score distance from mean.

        Peaks around |z|~1.75 then DECREASES for extreme z-scores
        (model extrapolation risk). Acts as a brake on Kelly sizing
        when the normal-distribution assumption is least trustworthy.

        Curve shape:
            |z| = 0.0 -> ~0.65  (near threshold, uncertain)
            |z| = 1.0 -> ~0.80
            |z| = 1.75 -> ~0.92 (peak)
            |z| = 3.0 -> ~0.77
            |z| = 4.0 -> ~0.57  (extrapolation, low trust)

        Args:
            z_score: Standard deviations from expected value

        Returns:
            Confidence level (0.0 to 1.0)
        """
        abs_z = abs(z_score)

        # Gaussian-like confidence: peaks at |z|~1.75, decays beyond
        peak_z = 1.75
        width = 1.2
        max_boost = 0.42

        confidence = 0.50 + max_boost * math.exp(-0.5 * ((abs_z - peak_z) / width) ** 2)

        return min(confidence, 0.95)
    
    def _generate_recommendation(
        self,
        fair_price: float,
        market_price: float,
        alpha: float,
        alpha_threshold: float = 0.10
    ) -> str:
        """
        Generate trading recommendation based on alpha.

        Args:
            fair_price: Calculated fair price
            market_price: Current market price
            alpha: Calculated alpha (absolute)
            alpha_threshold: Minimum absolute alpha for action

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
        alpha_threshold: float = 0.10,
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
        threshold: float = 0.10
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

        # CRITICAL: Detect if temperature is in Celsius or Fahrenheit
        # NOTE: bare ' C' is too broad — "New York City" and "Chicago" both contain " C".
        # Only treat as Celsius when °C symbol present OR digits immediately precede C (e.g. "15 C").
        is_celsius = (
            '°C' in condition
            or '°c' in condition
            or bool(re.search(r'\d\s*C\b', condition))
        )

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

