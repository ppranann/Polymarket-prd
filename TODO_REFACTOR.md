# TODO: Refactor Hardcoded 0.15 Values

## Goal
Replace hardcoded `0.15` values with configurable `snipe_price_threshold` from config.yaml

## Changes Completed

### 1. src/scanner.py ✅
- ✅ Change `max_price: float = 0.15` in `find_blue_clock_markets()` to use config value
- ✅ Note: `self._snipe_price_threshold` was already using `config.get('scanner', {}).get('snipe_price_threshold', 0.15)`

### 2. src/calculator.py ✅
- ✅ Added `snipe_price_threshold` parameter to `is_underpriced()` method
- ✅ Updated `market_price < 0.15` to use the parameter `market_price < snipe_price_threshold`

## Files NOT to Change
- config.py - Already correctly reads from config.yaml
- Comments mentioning 0.15 - Just documentation (kept for clarity)
- Default parameter values - Sensible fallbacks for backward compatibility

## Summary
The `snipe_price_threshold` in config.yaml (default: 0.80) now controls:
1. Which markets get weather data fetched (scanner.py)
2. The max_price default in `find_blue_clock_markets()` (scanner.py)
3. The price threshold in `is_underpriced()` (calculator.py)

