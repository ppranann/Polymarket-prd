#!/usr/bin/env python3
"""
Debug Alpha Calculation
========================
Helps identify why alpha is showing 100% on all markets.
"""

import asyncio
import logging
from src.config import load_config
from src.calculator import create_calculator
from src.clients.polymarket import create_polymarket_client
from src.clients.tomorrow_client import create_tomorrow_client
from src.secrets_manager import load_bot_secrets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def debug_alpha():
    """Debug alpha calculation for real markets"""

    # Load config and secrets
    config = load_config("config.yaml")
    secrets = load_bot_secrets()

    # Create clients
    poly_client = create_polymarket_client(
        api_key=secrets.polymarket_api_key,
        api_secret=secrets.polymarket_api_secret,
        api_passphrase=secrets.polymarket_api_passphrase,
        private_key=secrets.polygon_private_key,
        chain_id=137,
        paper_mode=True
    )

    weather_client = create_tomorrow_client(
        api_key=secrets.tomorrow_io_api_key,
        cache_ttl_seconds=300
    )

    calculator = create_calculator(sigma=3.5, default_expected_temp=65.0)

    # Fetch real markets
    logger.info("Fetching markets...")
    markets = await poly_client.get_markets()

    logger.info(f"Found {len(markets)} markets\n")
    logger.info("=" * 80)

    # Analyze first 5 markets
    for i, market in enumerate(markets[:5]):
        logger.info(f"\n📊 Market #{i+1}: {market.id[:8]}")
        logger.info(f"   Question: {market.question}")
        logger.info(f"   Price: ${market.yes_price:.4f}")

        # Try to extract threshold
        threshold = calculator.get_temperature_threshold_from_condition(market.question)
        logger.info(f"   Extracted Threshold: {threshold}°F (None = failed extraction)")

        # Get weather data
        try:
            # Try to extract city from question
            city = "london"  # Default for testing
            if "seoul" in market.question.lower():
                city = "seoul"
            elif "nyc" in market.question.lower() or "new york" in market.question.lower():
                city = "nyc"

            logger.info(f"   City: {city}")

            weather = await weather_client.get_weather(city_slug=city)
            forecast_temp = weather.get("temperature", 65.0)

            logger.info(f"   Forecast Temp: {forecast_temp}°F")

            # Calculate fair price
            result = calculator.calculate_fair_price(
                market_condition=market.question,
                forecasted_temp=forecast_temp,
                market_price=market.yes_price,
                expected_temp=threshold if threshold else 65.0
            )

            logger.info(f"   Fair Price: ${result.fair_price:.4f}")
            logger.info(f"   Alpha: {result.alpha:.1%}")
            logger.info(f"   Expected Temp Used: {result.expected_temp}°F")

            # Diagnose issue
            if result.alpha >= 0.99:
                logger.warning("   ⚠️  PROBLEM: Alpha is 100%!")
                if threshold is None:
                    logger.warning("   🐛 ROOT CAUSE: Threshold extraction FAILED")
                    logger.warning(f"   🐛 Using default {result.expected_temp}°F instead of parsing question")
                else:
                    logger.warning(f"   🐛 Threshold extracted ({threshold}°F) but alpha still 100%")

        except Exception as e:
            logger.error(f"   ❌ Error: {e}")

        logger.info("-" * 80)

    await poly_client.close()


if __name__ == "__main__":
    asyncio.run(debug_alpha())
