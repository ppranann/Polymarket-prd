# TODO: Bot Fixes

## Issue 1: Missing Methods in PolymarketClient
- [x] Add `place_limit_order()` method for paper trading
- [x] Add `get_order_book()` method for farming logic
- [x] Add `**kwargs` to catch 'post_only' parameter and prevent crash
- [x] Add `cancel_order()` method to fix fatal mismatch

## Issue 2: Tomorrow.io API 404
- [x] Add `get_realtime_weather()` method with correct V4 endpoint
- [x] Add 404 error handling

## Issue 3: Logging Noise in Scanner
- [x] Change noisy logger.info() calls to logger.debug()
- [x] Add summary logging at end of scan instead of per-bucket logging
- [x] Remove "DEBUG:" prefix from all log messages

## Issue 4: HTTP Library Noise
- [x] Muzzle httpx library in main.py
- [x] Muzzle aiohttp library in main.py

## Issue 5: Order Response Type (dict vs object)
- [x] Fix executor to use dict access for order responses
- [x] Fix `_recent_orders` tracking to use `order["orderID"]`
- [x] Fix farming position tracking to use `buy_order["orderID"]`
- [x] Fix order cancellation to use safe dict access

## Issue 6: Alpha Detector Safety
- [x] Skip markets when weather data is unavailable instead of returning fake alpha

## Completion
All fixes implemented and verified.




