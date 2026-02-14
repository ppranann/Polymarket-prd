"""Tests for the Fair Price Calculator"""

import pytest
from src.calculator import FairPriceCalculator, PriceCalculation


class TestFairPriceCalculator:
    """Test cases for FairPriceCalculator"""

    def setup_method(self):
        """Setup test fixtures"""
        self.calculator = FairPriceCalculator(
            sigma=3.5,  # 3.5°F standard deviation
            default_expected_temp=65.0
        )

    def test_calculate_fair_price_above_threshold(self):
        """Test fair price for 'above' temperature conditions"""
        # Temperature well above expected (z-score ~1.4)
        # Should give high probability of being above
        result = self.calculator.calculate_fair_price(
            market_condition="temp_above_70",
            forecasted_temp=70.0,
            expected_temp=65.0,
            sigma=3.5,
            market_price=0.5
        )

        assert isinstance(result, PriceCalculation)
        assert result.fair_price > 0.5  # Should be above 0.5
        assert result.z_score == pytest.approx((70.0 - 65.0) / 3.5, rel=0.01)

    def test_calculate_fair_price_below_threshold(self):
        """Test fair price for 'below' temperature conditions"""
        # Forecast (75°F) is above threshold (65°F), so P(T < 65) is low
        result = self.calculator.calculate_fair_price(
            market_condition="temp_below_60",
            forecasted_temp=75.0,
            expected_temp=65.0,
            sigma=3.5,
            market_price=0.5
        )

        assert isinstance(result, PriceCalculation)
        assert result.fair_price < 0.5  # Forecast above threshold -> low P(below)

    def test_calculate_fair_price_clamped(self):
        """Test that fair price is clamped to valid range (max 0.98)"""
        # Extreme temperature should give price close to 0 or 1, but capped at 0.98
        result = self.calculator.calculate_fair_price(
            market_condition="temp_above_100",
            forecasted_temp=100.0,
            expected_temp=65.0,
            sigma=3.5,
            market_price=0.5
        )

        assert 0.01 <= result.fair_price <= 0.98

    def test_calculate_alpha(self):
        """Test alpha calculation (absolute mispricing)"""
        # Fair price higher than market = positive alpha
        alpha = self.calculator._calculate_alpha(fair_price=0.80, market_price=0.60)
        assert alpha == pytest.approx(0.20, rel=0.01)  # 0.80 - 0.60 = 0.20

        # Fair price lower than market = negative alpha
        alpha = self.calculator._calculate_alpha(fair_price=0.30, market_price=0.50)
        assert alpha == pytest.approx(-0.20, rel=0.01)  # 0.30 - 0.50 = -0.20

    def test_calculate_alpha_clamped(self):
        """Test that alpha is clamped to [-0.95, 0.95]"""
        # Extreme mispricing should be clamped
        alpha = self.calculator._calculate_alpha(fair_price=0.01, market_price=0.99)
        assert -0.95 <= alpha <= 0.95
        assert alpha == pytest.approx(-0.95, abs=0.03)  # Clamped near -0.95

    def test_calculate_confidence(self):
        """Test confidence calculation (peaks at |z|~1.75, decreases beyond)"""
        # Low z-score: uncertain
        conf_low = self.calculator._calculate_confidence(0.0)
        assert 0.50 <= conf_low <= 0.70

        # Moderate z-score: high confidence (near peak)
        conf_peak = self.calculator._calculate_confidence(1.75)
        assert conf_peak >= 0.88

        # High z-score: declining confidence (model extrapolation risk)
        conf_extreme = self.calculator._calculate_confidence(4.0)
        assert conf_extreme < 0.65  # Should have dropped significantly

        # Verify non-monotonic: peak > extremes
        assert conf_peak > conf_extreme
        assert conf_peak > conf_low

    def test_generate_recommendation_buy(self):
        """Test BUY recommendation generation"""
        result = self.calculator._generate_recommendation(
            fair_price=0.80,
            market_price=0.50,
            alpha=0.30
        )
        assert result == "BUY"

    def test_generate_recommendation_sell(self):
        """Test SELL recommendation generation"""
        result = self.calculator._generate_recommendation(
            fair_price=0.20,
            market_price=0.50,
            alpha=-0.30
        )
        assert result == "SELL"

    def test_generate_recommendation_hold(self):
        """Test HOLD recommendation generation"""
        result = self.calculator._generate_recommendation(
            fair_price=0.52,
            market_price=0.50,
            alpha=0.02
        )
        assert result == "HOLD"

    def test_parse_condition_type_above(self):
        """Test condition type parsing for 'above'"""
        assert self.calculator._parse_condition_type("temp_above_70") == "above"
        assert self.calculator._parse_condition_type("above_80") == "above"

    def test_parse_condition_type_below(self):
        """Test condition type parsing for 'below'"""
        assert self.calculator._parse_condition_type("temp_below_50") == "below"
        assert self.calculator._parse_condition_type("below_40") == "below"

    def test_parse_condition_type_precipitation(self):
        """Test condition type parsing for precipitation"""
        assert self.calculator._parse_condition_type("rain") == "precipitation"
        assert self.calculator._parse_condition_type("snow") == "precipitation"

    def test_is_underpriced(self):
        """Test underpriced detection (absolute alpha)"""
        # Underpriced: fair > market and alpha > threshold
        assert self.calculator.is_underpriced(
            fair_price=0.80,
            market_price=0.10,
            alpha_threshold=0.10
        )

        # Not underpriced: absolute alpha below threshold (0.60 - 0.55 = 0.05)
        assert not self.calculator.is_underpriced(
            fair_price=0.60,
            market_price=0.55,
            alpha_threshold=0.10
        )

    def test_price_calculation_to_dict(self):
        """Test PriceCalculation serialization"""
        result = self.calculator.calculate_fair_price(
            market_condition="test",
            forecasted_temp=65.0,
            market_price=0.50
        )

        data = result.to_dict()
        assert "fair_price" in data
        assert "z_score" in data
        assert "alpha" in data
        assert "recommendation" in data


class TestFactoryFunction:
    """Test factory function"""

    def test_create_calculator(self):
        """Test calculator factory"""
        calc = FairPriceCalculator()

        assert calc.sigma == 3.5
        assert calc.default_expected_temp == 65.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
