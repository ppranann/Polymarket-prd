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
        result = self.calculator.calculate_fair_price(
            market_condition="temp_below_60",
            forecasted_temp=55.0,
            expected_temp=65.0,
            sigma=3.5,
            market_price=0.5
        )
        
        assert isinstance(result, PriceCalculation)
        assert result.fair_price < 0.5  # Should be below 0.5
    
    def test_calculate_fair_price_clamped(self):
        """Test that fair price is clamped to valid range"""
        # Extreme temperature should give price close to 0 or 1
        result = self.calculator.calculate_fair_price(
            market_condition="temp_above_100",
            forecasted_temp=100.0,
            expected_temp=65.0,
            sigma=3.5,
            market_price=0.5
        )
        
        assert 0.01 <= result.fair_price <= 0.99
    
    def test_calculate_alpha(self):
        """Test alpha calculation"""
        # Fair price higher than market = positive alpha
        alpha = self.calculator._calculate_alpha(fair_price=0.80, market_price=0.60)
        assert alpha == pytest.approx((0.80 - 0.60) / 0.60, rel=0.01)
        
        # Fair price lower than market = negative alpha
        alpha = self.calculator._calculate_alpha(fair_price=0.30, market_price=0.50)
        assert alpha == pytest.approx((0.30 - 0.50) / 0.50, rel=0.01)
    
    def test_calculate_alpha_clamped(self):
        """Test that alpha is clamped to reasonable values"""
        # Extreme mispricing should be clamped
        alpha = self.calculator._calculate_alpha(fair_price=0.01, market_price=1.0)
        assert -2.0 <= alpha <= 2.0
    
    def test_calculate_confidence(self):
        """Test confidence calculation based on z-score"""
        # Low z-score = lower confidence
        conf_low = self.calculator._calculate_confidence(0.5)
        assert conf_low < 0.6
        
        # Medium z-score = medium confidence
        conf_med = self.calculator._calculate_confidence(1.5)
        assert 0.6 < conf_med < 0.8
        
        # High z-score = high confidence
        conf_high = self.calculator._calculate_confidence(3.0)
        assert conf_high >= 0.9
    
    def test_generate_recommendation_buy(self):
        """Test BUY recommendation generation"""
        result = self.calculator._generate_recommendation(
            fair_price=0.80,
            market_price=0.50,
            alpha=0.60
        )
        assert result == "BUY"
    
    def test_generate_recommendation_sell(self):
        """Test SELL recommendation generation"""
        result = self.calculator._generate_recommendation(
            fair_price=0.20,
            market_price=0.50,
            alpha=-0.60
        )
        assert result == "SELL"
    
    def test_generate_recommendation_hold(self):
        """Test HOLD recommendation generation"""
        result = self.calculator._generate_recommendation(
            fair_price=0.52,
            market_price=0.50,
            alpha=0.04
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
        """Test underpriced detection"""
        # Underpriced: fair > market and alpha > threshold
        assert self.calculator.is_underpriced(
            fair_price=0.80,
            market_price=0.10,
            threshold=0.15
        )
        
        # Not underpriced: alpha below threshold
        assert not self.calculator.is_underpriced(
            fair_price=0.60,
            market_price=0.50,
            threshold=0.15
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

