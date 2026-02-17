"""
Main Trading Loop Module
========================
The main entry point for the Polymarket Weather Sniping Bot.
Orchestrates all components:
- Market scanning
- Fair price calculation
- Order execution
- Auto-exit management
- Logging and auditing

Key Features:
- Async trading loop with configurable intervals
- Graceful error handling and recovery
- Full audit logging to Supabase
- Risk management enforcement
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime
from typing import Dict, List, Optional, Any

from .config import load_config, BotConfig
from .secrets_manager import load_bot_secrets, SecretsManager
from .clients.polymarket import create_polymarket_client, PolymarketClient
from .clients.tomorrow_client import create_tomorrow_client, RateLimitConfig
from .calculator import create_calculator, FairPriceCalculator
from .risk import create_risk_manager, RiskManager
from .scanner import create_market_scanner, MarketScanner
from .executor import create_order_executor, OrderExecutor
from .alpha import create_alpha_detector, AlphaDetector
from .auto_exit import create_auto_exit_manager, AutoExitManager
from .logger import create_audit_logger, AuditLogger, PredictionLog
from .performance import create_performance_reporter, PerformanceReporter
from .resolution import ResolutionTracker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Muzzle the external yappers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Silence internal debugs - only show important info
logging.getLogger("src.scanner").setLevel(logging.INFO)
logging.getLogger("src.executor").setLevel(logging.INFO)

logger = logging.getLogger(__name__)


class TradingBot:
    """
    Main trading bot orchestrator.
    
    Coordinates all components in the trading loop:
    1. Scan for weather markets with Liquidity Rewards
    2. Fetch weather data from Tomorrow.io
    3. Calculate fair prices using CDF
    4. Execute trades based on alpha signals
    5. Auto-exit at 100% profit targets
    6. Log all decisions to Supabase
    """
    
    def __init__(
        self,
        config: BotConfig,
        paper_trading: bool = False
    ):
        """
        Initialize the trading bot.
        
        Args:
            config: Bot configuration
            paper_trading: Use paper trading mode
        """
        self.config = config
        self.paper_trading = paper_trading or config.development.paper_trading
        
        # Component placeholders
        self._secrets = None
        self._polymarket_client = None
        self._weather_client = None
        self._calculator = None
        self._risk_manager = None
        self._scanner = None
        self._executor = None
        self._alpha_detector = None
        self._auto_exit = None
        self._logger = None
        self._performance = None
        self._resolution_tracker = None
        self._traded_market_ids: set = set()
        
        # Bot state
        self._running = False
        self._start_time: Optional[datetime] = None
        self._scan_count = 0
        self._trade_count = 0
        self._last_scan_time: Optional[datetime] = None

        # Skip reason tracking (Priority 0: Critical Safety & Priority 5: Logging)
        self._skip_reasons: Dict[str, int] = {}
        
        # Setup signal handlers
        self._setup_signal_handlers()

    def _record_skip_reason(self, reason: str):
        """
        Record why a market was skipped (Priority 0 & 5).

        Args:
            reason: Skip reason category
        """
        self._skip_reasons[reason] = self._skip_reasons.get(reason, 0) + 1

    def _setup_signal_handlers(self):
        """Setup graceful shutdown handlers"""
        loop = asyncio.get_event_loop()
        
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self._shutdown(s))
            )
    
    async def initialize(self):
        """Initialize all bot components"""
        logger.info("Initializing Polymarket Weather Sniping Bot...")
        
        # Load secrets
        logger.info("Loading secrets...")
        secrets_manager = SecretsManager(
            secret_name=self.config.aws.get("secret_name", "polymarket-bot/production"),
            region_name=self.config.aws.get("region", "us-east-1")
        )
        self._secrets = secrets_manager.load_secrets()
        
        # Validate required secrets (Supabase is optional for logging)
        if not secrets_manager.validate_secrets(self._secrets, require_supabase=False):
            raise ValueError("Secret validation failed")
        
        logger.info("Secrets loaded successfully")
        
        # Initialize Polymarket client
        logger.info("Initializing Polymarket client...")
        self._polymarket_client = create_polymarket_client(
            config=self.config,
            secrets=self._secrets,
            paper_trading=self.paper_trading
        )
        
        # Health check
        if not await self._polymarket_client.health_check():
            raise ConnectionError("Polymarket CLOB health check failed")
        
        logger.info("Polymarket client initialized")
        
        # Initialize Weather client with rate limiting and caching
        logger.info("Initializing Tomorrow.io client with rate limiting and caching...")
        rate_limit_config = RateLimitConfig(
            max_retries=5,
            base_backoff_seconds=2.0,
            max_backoff_seconds=60.0,
            per_city_cooldown_seconds=600.0,  # 10 min cooldown after 429 (was 180s = retry too fast)
            global_min_interval_seconds=2.0,  # 2s between API calls (was 1s)
        )
        # Weather cache: 10min TTL balances freshness vs rate limits.
        # 3 cities × 1 call per 10min = 18 calls/hour (well within Tomorrow.io limits)
        # Old: 300s (5 min) caused 36 calls/hour → constant 429 rate limits
        self._weather_client = create_tomorrow_client(
            api_key=self._secrets.tomorrow_io_api_key,
            rate_limit_config=rate_limit_config,
            cache_ttl_seconds=600.0,  # 10 minutes - prevents rate limit loops
        )

        logger.info("Weather client initialized with rate limiting")
        
        # Initialize Calculator
        logger.info("Initializing Fair Price Calculator...")
        self._calculator = create_calculator(
            sigma=3.5,  # 3.5°F standard deviation
            default_expected_temp=65.0
        )
        
        logger.info("Calculator initialized")
        
        # Initialize Risk Manager
        logger.info("Initializing Risk Manager...")
        self._risk_manager = create_risk_manager(
            config=self.config,
            initial_capital=self.config.risk.initial_capital
        )
        
        logger.info("Risk Manager initialized")

        # Initialize Performance Reporter
        logger.info("Initializing Performance Reporter...")
        self._performance = create_performance_reporter(
            risk_manager=self._risk_manager
        )

        logger.info("Performance Reporter initialized")

        # Initialize Scanner
        logger.info("Initializing Market Scanner...")
        self._scanner = create_market_scanner(
            config=self.config,
            polymarket_client=self._polymarket_client
        )
        
        logger.info("Scanner initialized")
        
        # Initialize Executor
        logger.info("Initializing Order Executor...")
        self._executor = create_order_executor(
            config=self.config,
            polymarket_client=self._polymarket_client,
            risk_manager=self._risk_manager
        )
        
        logger.info("Executor initialized")
        
        # Initialize Alpha Detector
        logger.info("Initializing Alpha Detector...")
        self._alpha_detector = create_alpha_detector(
            config=self.config,
            calculator=self._calculator
        )
        
        logger.info("Alpha Detector initialized")
        
        # Initialize Auto-Exit Manager
        logger.info("Initializing Auto-Exit Manager...")
        self._auto_exit = create_auto_exit_manager(
            config=self.config,
            risk_manager=self._risk_manager,
            polymarket_client=self._polymarket_client
        )
        
        logger.info("Auto-Exit Manager initialized")
        
        # Initialize Audit Logger
        logger.info("Initializing Audit Logger...")
        self._logger = create_audit_logger(
            config=self.config,
            url=self._secrets.supabase_url,
            key=self._secrets.supabase_service_key
        )
        
        logger.info("Audit Logger initialized")

        # Initialize Resolution Tracker
        self._resolution_tracker = ResolutionTracker(self._logger)
        
        logger.info(
            f"Bot initialized in {'PAPER' if self.paper_trading else 'LIVE'} trading mode"
        )
    
    async def start(self):
        """Start the main trading loop"""
        if self._running:
            logger.warning("Bot is already running")
            return
        
        self._running = True
        self._start_time = datetime.now()
        
        logger.info("=" * 60)
        logger.info("STARTING TRADING BOT")
        logger.info(f"Mode: {'PAPER' if self.paper_trading else 'LIVE'}")
        logger.info(f"Initial Capital: ${self.config.risk.initial_capital:.2f}")
        logger.info(f"Max Trade Size: ${self.config.risk.max_trade_size:.2f}")
        logger.info(f"Profit Target: {self.config.risk.profit_target}")
        logger.info("=" * 60)
        
        await self._main_loop()
    
    async def _main_loop(self):
        """Main trading loop with rebalancing"""
        scan_interval = self.config.scanner.scan_interval
        
        # Track when we last rebalanced farming positions
        last_rebalance = datetime.now()
        rebalance_interval = self.config.farming.rebalance_interval
        
        while self._running:
            try:
                # Step 0: Check circuit breaker (Priority 4: Risk Management)
                if self._risk_manager._circuit_breaker_triggered:
                    logger.critical("Circuit breaker triggered - trading halted")
                    await self._handle_circuit_breaker()
                    break

                # Step 1: Check for auto-exit conditions first
                await self._check_auto_exits()
                
                # Step 2: Rebalance farming positions every 60 seconds
                # (2026 Adjusted Midpoint Farming requirement)
                now = datetime.now()
                if (now - last_rebalance).total_seconds() >= rebalance_interval:
                    rebalanced = await self._executor.rebalance_all_farming_positions()
                    if rebalanced > 0:
                        logger.info(f"Rebalanced {rebalanced} farming positions")
                    last_rebalance = now
                
                # Step 3: Scan for new opportunities
                await self._scan_and_trade()

                # Update stats
                self._scan_count += 1

                # Step 3.5: Check market resolutions every 10 scans
                if self._scan_count % 10 == 0 and self._traded_market_ids:
                    resolutions = await self._resolution_tracker.check_resolutions(
                        list(self._traded_market_ids)
                    )
                    if resolutions:
                        await self._resolution_tracker.update_predictions(resolutions)

                # Step 4: Show P&L (compact after each scan, full every hour)
                if self._performance.should_print_full_summary(interval_minutes=60):
                    self._performance.print_performance_summary()
                else:
                    self._performance.print_compact_summary()

                # Wait before next scan
                await asyncio.sleep(scan_interval)
                
            except Exception as e:
                logger.error(f"Main loop error: {e}")
                await self._logger.log_error(
                    error_type="MAIN_LOOP_ERROR",
                    error_message=str(e),
                    context={"scan_count": self._scan_count}
                )
                
                # Brief pause on error
                await asyncio.sleep(60)
    
    async def _scan_and_trade(self):
        """Scan markets and execute trades"""
        scan_start = datetime.now()

        logger.info(f"Scan #{self._scan_count} starting...")

        # Reset skip reasons for this scan (Priority 5: Logging)
        self._skip_reasons = {}

        # Per-scan trade budget: prevent burning through daily limit in one cycle
        self._scan_trades_this_cycle = 0
        self._scan_trade_budget = 5

        # Weather cache for this scan (temperature doesn't change every second!)
        weather_cache: Dict[str, Any] = {}

        try:
            # Step 1: Scan for qualifying markets
            result = await self._scanner.scan()
            self._last_scan_time = scan_start

            if result.errors:
                for error in result.errors:
                    logger.warning(f"Scan warning: {error}")

            if not result.qualifying_markets:
                logger.info("No qualifying markets found")
                return

            logger.info(
                f"Found {len(result.qualifying_markets)} qualifying markets"
            )

            # Log scan result
            await self._logger.log_scan(
                markets_found=result.markets_found,
                qualifying_markets=len(result.qualifying_markets),
                scan_duration_ms=(datetime.now() - scan_start).total_seconds() * 1000,
                errors=result.errors
            )

            # Step 2: Get current prices using get_price (not get_book)
            prices = await self._scanner.get_market_prices(result.qualifying_markets)

            # Step 3: Group markets by city to avoid duplicate API calls
            markets_by_city: Dict[str, List] = {}
            for m in result.qualifying_markets:
                city = self._extract_city_from_market(m).lower()
                markets_by_city.setdefault(city, []).append(m)

            # Step 4: Process city by city with rate-limit handling
            skipped_cities = []
            for city, city_markets in markets_by_city.items():
                logger.info(f"☁️ Fetching weather: {city} ({len(city_markets)} markets)")

                # Check if city is in cooldown from previous attempts
                if self._weather_client._is_city_rate_limited(city):
                    logger.warning(f"⏱️ Skipping {city} - still in cooldown from previous rate limit.")
                    skipped_cities.append(city)
                    continue

                # Check scan-level cache first
                if city in weather_cache:
                    logger.debug(f"Using cached weather for {city}")
                    weather_data = weather_cache[city]
                else:
                    # Get location coordinates for this city from the client's COORD_MAP
                    location = self._weather_client.COORD_MAP.get(city, self._weather_client.COORD_MAP["nyc"])

                    # Fetch weather using rate-limit aware client
                    # NOTE: "precipitation" field is NOT valid for /timelines endpoint
                    # Using only safe fields to avoid 400 errors
                    weather_raw = await self._weather_client.get_city_weather(
                        city_slug=city,
                        location=location,
                        fields=[
                            "temperature",
                            "temperatureApparent",
                            "temperatureMin",
                            "temperatureMax",
                            "precipitationProbability",
                            "weatherCode"
                        ],  # REMOVED: "precipitation" (invalid field)
                        timesteps="1h",
                    )

                    if not weather_raw:
                        logger.warning(f"❌ Skipping {city} - weather data unavailable (rate-limited or failed).")
                        skipped_cities.append(city)
                        continue

                    # Process weather response
                    weather_data = await self._process_weather_response(weather_raw)

                    if not weather_data:
                        logger.warning(f"❌ Skipping {city} - could not parse weather data.")
                        skipped_cities.append(city)
                        continue

                    # Cache for this scan
                    weather_cache[city] = weather_data

                # Step 5: Analyze ALL markets for THIS city using the single fetch
                for market in city_markets:
                    try:
                        current_price = prices.get(market.id, market.yes_price)

                        # Skip individual analyzing log - we already logged city group
                        await self._analyze_single_market(
                            market=market,
                            current_price=current_price,
                            weather_data=weather_data
                        )

                    except Exception as e:
                        logger.error(f"Error on {market.id}: {e}")
                        await self._logger.log_error(
                            error_type="MARKET_ANALYSIS_ERROR",
                            error_message=str(e),
                            market_id=market.id
                        )

            # Log summary
            if skipped_cities:
                logger.warning(f"Scan #{self._scan_count} complete. Skipped rate-limited cities: {', '.join(skipped_cities)}")

            # Log skip reasons summary (Priority 5: Logging)
            if self._skip_reasons:
                logger.info(f"Skip reasons: {dict(self._skip_reasons)}")

        except Exception as e:
            logger.error(f"Scan and trade error: {e}")
            await self._logger.log_error(
                error_type="SCAN_TRADE_ERROR",
                error_message=str(e)
            )
    
    async def _analyze_single_market(
        self,
        market,
        current_price: float,
        weather_data
    ) -> None:
        """
        Analyze a single market using pre-fetched weather data.

        Args:
            market: Market to analyze
            current_price: Current market price
            weather_data: Pre-fetched WeatherData object

        Returns:
            None
        """
        # Step 1: Risk check
        can_trade, reason = self._risk_manager.validate_market_filter(
            market_price=current_price,
            market_id=market.id
        )

        if not can_trade:
            logger.debug(f"Skipping {market.id[:8]}...: {reason}")
            self._record_skip_reason("risk_filter")
            return

        # Early exit: if we hold no positions, expensive markets are SELL-only
        # territory — skip them entirely to save weather API calls
        if not self._risk_manager.positions and current_price > self.config.scanner.price_threshold:
            self._record_skip_reason("overpriced_no_positions")
            return

        # Step 2: Get city name
        city_name = self._extract_city_from_market(market)

        # Step 3: Check if we have weather data
        if not weather_data:
            logger.debug(f"Weather unavailable for {city_name}, skipping...")
            self._record_skip_reason("no_weather")
            return

        # CRITICAL: Use date-specific forecast, not overall max across all days
        target_date, days_out = self._extract_target_date(market.question)
        date_weather = self._get_weather_for_date(weather_data, target_date)

        calculation = self._calculator.calculate_fair_price_from_weather(
            weather_data=date_weather,
            market_condition=market.question,
            market_price=current_price
        )

        # Validate calculation results (Priority 0: Critical Safety)
        import math
        if calculation.fair_price is None or math.isnan(calculation.fair_price):
            logger.warning(f"Invalid fair_price (NaN/None) for {market.id[:8]}, skipping")
            self._record_skip_reason("invalid_price")
            return

        if calculation.fair_price < 0.001 or calculation.fair_price > 0.999:
            logger.warning(
                f"Fair price out of range ({calculation.fair_price:.4f}) for {market.id[:8]}, skipping"
            )
            self._record_skip_reason("invalid_price")
            return

        if math.isnan(calculation.alpha):
            logger.warning(f"Invalid alpha (NaN) for {market.id[:8]}, skipping")
            self._record_skip_reason("invalid_alpha")
            return

        # Step 4: Analyze alpha
        signal = self._alpha_detector.analyze_market(
            market_id=market.id,
            fair_price=calculation.fair_price,
            market_price=current_price,
            z_score=calculation.z_score,
            confidence=calculation.confidence
        )

        # Log prediction for calibration (Phase 2)
        prediction = PredictionLog(
            market_id=market.id,
            question_text=market.question[:200],
            city=city_name,
            target_date=target_date,
            condition_type=calculation.condition_type,
            threshold_f=calculation.expected_temp,
            forecast_temp_f=calculation.forecast_temp,
            sigma_used=calculation.sigma,
            z_score=calculation.z_score,
            days_to_resolution=days_out,
            fair_price=calculation.fair_price,
            market_price=current_price,
            alpha=signal.alpha,
            side=signal.action,
            size=0.0,
            capital_at_time=self._risk_manager._capital
        )
        await self._logger.log_prediction(prediction)

        # Step 5: Execute trade based on signal
        if signal.action in ["BUY", "SELL"]:
            # Skip SELL signals early if we have no position — don't waste
            # scan budget or API calls on trades that will always fail
            if signal.action == "SELL":
                position = self._risk_manager.get_position(market.id)
                if not position or position.size <= 0:
                    logger.debug(f"Skipping sell {market.id[:8]}: no position held")
                    self._record_skip_reason("no_position_sell")
                    return

            # Per-scan trade budget: don't burn through daily limit in one cycle
            if getattr(self, '_scan_trades_this_cycle', 0) >= getattr(self, '_scan_trade_budget', 5):
                logger.debug(f"Scan trade budget reached, deferring {market.id[:8]}")
                self._record_skip_reason("scan_budget")
                return

            logger.info(
                f"Alpha signal: {signal.action} | "
                f"Alpha: ${float(signal.alpha):.2f} | "
                f"Confidence: {calculation.confidence:.2f}"
            )

            result = await self._executor.execute_trade(
                market=market,
                current_price=current_price,
                fair_price=calculation.fair_price,
                alpha=signal.alpha,
                action=signal.action,
                confidence=calculation.confidence
            )

            # Only count successful trades toward scan budget
            self._trade_count += 1
            if result.success:
                self._scan_trades_this_cycle = getattr(self, '_scan_trades_this_cycle', 0) + 1

            # Log trade
            await self._logger.log_trade(
                market_id=market.id,
                action=result.action,
                fair_price=calculation.fair_price,
                market_price=current_price,
                trade_size=result.order.size if result.order else 0,
                alpha=signal.alpha,
                reward_earned=result.reward_earned
            )

            if result.success:
                logger.info(f"Trade executed: {result.action} on {market.id[:8]}...")
                self._traded_market_ids.add(market.id)
            else:
                logger.warning(f"Trade failed: {result.reason}")

        elif signal.action == "HOLD":
            logger.debug(
                f"HOLD: Alpha={signal.alpha:.1%}, "
                f"Fair=${calculation.fair_price:.4f}, "
                f"Market=${current_price:.4f}"
            )
            self._record_skip_reason("no_alpha")

        # Return None on successful analysis (no weather failure)
        return None

    async def _analyze_and_trade(
        self,
        market,
        current_price: float
    ) -> Optional[str]:
        """
        Analyze a single market and execute trades if appropriate.
        
        Args:
            market: Market to analyze
            current_price: Current market price
            
        Returns:
            City name if weather fetch failed, None otherwise
        """
        logger.debug(f"Analyzing {market.id[:8]}... (Price: ${current_price:.4f})")

        # Step 1: Risk check
        can_trade, reason = self._risk_manager.validate_market_filter(
            market_price=current_price,
            market_id=market.id
        )
        
        if not can_trade:
            logger.debug(f"Skipping {market.id[:8]}...: {reason}")
            return None
        
        # Step 2: Extract city name and get weather data using coordinates
        city_name = self._extract_city_from_market(market)
        
        try:
            # Use get_forecast_by_city which converts city name to coordinates
            weather_raw = await self._weather_client.get_forecast_by_city(city_name)
        except Exception as e:
            logger.warning(f"Weather fetch failed for {city_name}: {e}")
            weather_raw = None
        
        # Step 3: Calculate fair price
        if not weather_raw:
            logger.debug(f"Weather unavailable for {city_name}, skipping...")
            return city_name  # Return city name for batch logging
        
        # Convert raw API response to WeatherData
        weather = await self._process_weather_response(weather_raw)
        
        # ... rest of the method unchanged
        
        calculation = self._calculator.calculate_fair_price_from_weather(
            weather_data=weather,
            market_condition=market.question,
            market_price=current_price
        )
        
        # Step 4: Analyze alpha
        signal = self._alpha_detector.analyze_market(
            market_id=market.id,
            fair_price=calculation.fair_price,
            market_price=current_price,
            z_score=calculation.z_score,
            confidence=calculation.confidence
        )
        
        # Step 5: Execute trade based on signal
        if signal.action in ["BUY", "SELL"]:
            logger.info(
                f"Alpha signal: {signal.action} | "
                f"Alpha: ${float(signal.alpha):.2f} | "
                f"Confidence: {calculation.confidence:.2f}"
            )

            result = await self._executor.execute_trade(
                market=market,
                current_price=current_price,
                fair_price=calculation.fair_price,
                alpha=signal.alpha,
                action=signal.action,
                confidence=calculation.confidence
            )

            self._trade_count += 1

            # Log trade
            await self._logger.log_trade(
                market_id=market.id,
                action=result.action,
                fair_price=calculation.fair_price,
                market_price=current_price,
                trade_size=result.order.size if result.order else 0,
                alpha=signal.alpha,
                reward_earned=result.reward_earned
            )
            
            if result.success:
                logger.info(f"Trade executed: {result.action} on {market.id[:8]}...")
            else:
                logger.warning(f"Trade failed: {result.reason}")
        
        elif signal.action == "HOLD":
            logger.debug(
                f"HOLD: Alpha={signal.alpha:.1%}, "
                f"Fair=${calculation.fair_price:.4f}, "
                f"Market=${current_price:.4f}"
            )
        
        # Return None on successful analysis (no weather failure)
        return None
    
    def _extract_city_from_market(self, market) -> str:
        """
        Extract city name from market question for tracking.
        
        Args:
            market: Market to extract city from
            
        Returns:
            City name in lowercase
        """
        question = market.question.lower()
        
        # Common city patterns in market names
        city_patterns = {
            "seoul": ["seoul", "incheon"],
            "london": ["london", "heathrow"],
            "nyc": ["new york", "nyc", "new-york"],
        }
        
        for city, patterns in city_patterns.items():
            for pattern in patterns:
                if pattern in question:
                    return city
        
        return "unknown"

    def _get_weather_for_date(self, weather_data, target_date: str):
        """
        Get weather forecast filtered to a specific target date.

        Without this, all markets in a city use the same temp_max across ALL
        forecast days, producing wrong fair prices for future dates.

        Args:
            weather_data: WeatherData with daily_stats in raw_data
            target_date: ISO date string e.g. "2026-02-12"

        Returns:
            WeatherData with date-specific min/max/mean
        """
        from .clients.tomorrow_io import WeatherData as WD

        if not weather_data.raw_data or "daily_stats" not in weather_data.raw_data:
            return weather_data

        daily_stats = weather_data.raw_data.get("daily_stats", {})
        day_stats = daily_stats.get(target_date)

        if not day_stats:
            return weather_data  # No data for this specific date

        def c_to_f(c: float) -> float:
            return (c * 9.0 / 5.0) + 32.0

        return WD(
            location=weather_data.location,
            fetched_at=weather_data.fetched_at,
            current_temp=weather_data.current_temp,
            feels_like=weather_data.feels_like,
            temp_min=c_to_f(day_stats["min"]),
            temp_max=c_to_f(day_stats["max"]),
            temp_mean=c_to_f(day_stats["mean"]),
            precipitation_probability=weather_data.precipitation_probability,
            precipitation_amount=weather_data.precipitation_amount,
            weather_code=weather_data.weather_code,
            weather_description=weather_data.weather_description,
            raw_data=weather_data.raw_data
        )

    def _extract_target_date(self, question: str) -> tuple[str, int]:
        """
        Extract target date and days-to-resolution from market question.

        E.g. "Highest temperature in London on February 12?" → ("2026-02-12", 2)

        Returns:
            Tuple of (date_string, days_to_resolution)
        """
        import re
        month_map = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12
        }
        match = re.search(r'on\s+(\w+)\s+(\d{1,2})', question, re.IGNORECASE)
        if match:
            month_name = match.group(1).lower()
            day = int(match.group(2))
            month = month_map.get(month_name)
            if month:
                year = datetime.now().year
                try:
                    target = datetime(year, month, day).date()
                    days_out = (target - datetime.now().date()).days
                    return target.isoformat(), max(0, days_out)
                except ValueError:
                    pass
        return datetime.now().date().isoformat(), 0

    async def _process_weather_response(self, weather_data: Dict) -> Optional[Any]:
        """
        Process raw Tomorrow.io V4 API response into WeatherData object.

        The V4 API returns:
        {
          "timelines": {
            "hourly": [
              {
                "time": "2026-01-26T15:00:00Z",
                "values": {
                  "temperature": 7.2,
                  "humidity": 85
                }
              }
            ]
          }
        }

        Args:
            weather_data: Raw API response dictionary

        Returns:
            WeatherData object or None if parsing fails
        """
        try:
            from .clients.tomorrow_io import WeatherData

            # Tomorrow.io V4 API format: data.timelines[0].intervals
            data = weather_data.get("data", {})
            timelines = data.get("timelines", [])

            if not timelines or len(timelines) == 0:
                logger.warning("No timelines in weather response")
                return None

            # Get intervals from first timeline
            intervals = timelines[0].get("intervals", [])

            if not intervals:
                logger.warning("No intervals in weather timeline")
                return None

            # Use first interval's values for current conditions
            first_interval = intervals[0]
            if "values" not in first_interval:
                logger.warning("No values in first interval")
                return None

            current = first_interval["values"]

            from collections import defaultdict
            from datetime import datetime

            # CRITICAL FIX: Tomorrow.io returns Celsius by default!
            def c_to_f(celsius: float) -> float:
                return (celsius * 9.0 / 5.0) + 32.0

            # Group hourly intervals by date (UTC) for per-day forecasts
            # Without this, we use the OVERALL max across all days which is wrong
            # e.g. Feb 10 max=12°C, Feb 12 max=8°C → old code used 12°C for both
            temps_by_date = defaultdict(list)
            for interval in intervals:
                # V4 /timelines uses "startTime", /weather/forecast uses "time"
                time_str = interval.get("startTime") or interval.get("time", "")
                date_str = time_str[:10]  # "2026-02-10"
                temp = interval.get("values", {}).get("temperature")
                if temp is not None and date_str:
                    temps_by_date[date_str].append(temp)

            # Compute per-day stats (in Celsius)
            daily_stats = {}
            for date, temps in temps_by_date.items():
                daily_stats[date] = {
                    "min": min(temps),
                    "max": max(temps),
                    "mean": sum(temps) / len(temps),
                }

            if daily_stats:
                logger.info(
                    f"WEATHER DAILY (°C): " +
                    " | ".join(f"{d}: hi={s['max']:.1f} lo={s['min']:.1f}" for d, s in sorted(daily_stats.items()))
                )

            # Overall stats (fallback)
            all_temps = [t for temps in temps_by_date.values() for t in temps]
            raw_temp = current.get("temperature", 18.3)
            raw_feels = current.get("temperatureApparent", 18.3)
            raw_min = min(all_temps) if all_temps else 15.0
            raw_max = max(all_temps) if all_temps else 21.0
            raw_mean = sum(all_temps) / len(all_temps) if all_temps else 18.3

            logger.info(
                f"WEATHER RAW (°C): temp={raw_temp:.1f} min={raw_min:.1f} "
                f"max={raw_max:.1f} mean={raw_mean:.1f}"
            )

            return WeatherData(
                location=(0, 0),
                fetched_at=datetime.now(),
                current_temp=c_to_f(raw_temp),
                feels_like=c_to_f(raw_feels),
                temp_min=c_to_f(raw_min),
                temp_max=c_to_f(raw_max),
                temp_mean=c_to_f(raw_mean),
                precipitation_probability=current.get("precipitationProbability", 0.0),
                precipitation_amount=current.get("precipitation"),
                weather_code=current.get("weatherCode"),
                weather_description=None,
                raw_data={"original": weather_data, "daily_stats": daily_stats}
            )
        except Exception as e:
            logger.error(f"Error processing weather response: {e}")
            logger.debug(f"Weather response keys: {list(weather_data.keys())}")
            if "data" in weather_data:
                logger.debug(f"Data keys: {list(weather_data.get('data', {}).keys())}")
            return None
    
    async def _handle_circuit_breaker(self):
        """
        Handle circuit breaker trigger (Priority 4: Risk Management).

        Actions:
        - Cancel all open orders
        - Optionally close all positions (if configured)
        - Log critical alert
        - Pause trading
        """
        logger.critical("=" * 60)
        logger.critical("CIRCUIT BREAKER TRIGGERED - EMERGENCY STOP")
        logger.critical("=" * 60)

        # Cancel all open orders
        try:
            cancelled = await self._executor.cancel_all_orders()
            logger.info(f"Cancelled {cancelled} open orders")
        except Exception as e:
            logger.error(f"Error cancelling orders: {e}")

        # Optionally close all positions
        if self.config.risk.circuit_breaker_close_all:
            logger.warning("Closing all positions due to circuit breaker...")
            # This would require implementing position liquidation logic
            # For now, we just log and let the user handle it manually

        # Log final state
        await self._log_final_stats()

        logger.critical("Trading halted - manual intervention required")

    async def _check_auto_exits(self):
        """Check and execute auto-exits for profitable positions"""
        positions = self._risk_manager.positions
        
        if not positions:
            return
        
        # Get current prices for open positions
        prices = {}
        for market_id in positions.keys():
            try:
                prices[market_id] = await self._polymarket_client.get_price(market_id)
            except Exception as e:
                logger.warning(f"Could not get price for {market_id}: {e}")
        
        # Check exit conditions
        exit_signals = self._auto_exit.check_exit_conditions(positions, prices)
        
        if exit_signals:
            logger.info(f"Auto-exit triggered for {len(exit_signals)} positions")
            
            # Execute exits
            for signal in exit_signals:
                try:
                    result_signal = await self._auto_exit.execute_exit(signal)

                    # Only log success if execution actually succeeded
                    if result_signal.executed:
                        await self._logger.log_exit(
                            market_id=signal.market_id,
                            profit_loss=signal.profit_usdc,
                            exit_reason=signal.reason,
                            duration_hours=(
                                datetime.now() - signal.position.opened_at
                            ).total_seconds() / 3600
                        )
                    else:
                        # Log failure
                        await self._logger.log_error(
                            error_type="AUTO_EXIT_FAILED",
                            error_message=result_signal.reason,
                            market_id=signal.market_id
                        )

                except Exception as e:
                    logger.error(f"Auto-exit failed for {signal.market_id}: {e}")
                    await self._logger.log_error(
                        error_type="AUTO_EXIT_EXCEPTION",
                        error_message=str(e),
                        market_id=signal.market_id
                    )
    
    async def _shutdown(self, signum=None):
        """Graceful shutdown"""
        logger.info("Shutting down trading bot...")
        self._running = False
        
        # Cancel all open orders
        try:
            cancelled = await self._executor.cancel_all_orders()
            logger.info(f"Cancelled {cancelled} open orders")
        except Exception as e:
            logger.error(f"Error cancelling orders: {e}")
        
        # Log final stats
        await self._log_final_stats()
        
        # Close connections
        if self._weather_client:
            await self._weather_client.close()
        
        if self._logger:
            await self._logger.close()
        
        logger.info("Trading bot shutdown complete")
        sys.exit(0)
    
    async def _log_final_stats(self):
        """Log final trading statistics"""
        runtime = (
            datetime.now() - self._start_time
        ).total_seconds() if self._start_time else 0
        
        risk_summary = self._risk_manager.get_risk_summary()
        
        logger.info("=" * 60)
        logger.info("FINAL STATISTICS")
        logger.info(f"Runtime: {runtime/3600:.1f} hours")
        logger.info(f"Scans: {self._scan_count}")
        logger.info(f"Trades: {self._trade_count}")
        logger.info(f"Available Capital: ${risk_summary['available_capital']:.2f}")
        logger.info(f"Total Exposure: ${risk_summary['total_exposure']:.2f}")
        logger.info(f"Unrealized PnL: ${risk_summary['unrealized_pnl']:.2f}")
        logger.info("=" * 60)
    
    def get_status(self) -> Dict:
        """Get current bot status (Priority 5: Centralized State)"""
        # Get centralized state from components
        risk_state = self._risk_manager.get_all_state() if self._risk_manager else {}
        farming_state = self._executor.get_farming_state() if self._executor else {}

        return {
            "running": self._running,
            "mode": "PAPER" if self.paper_trading else "LIVE",
            "scan_count": self._scan_count,
            "trade_count": self._trade_count,
            "start_time": self._start_time.isoformat() if self._start_time else None,
            "last_scan": self._last_scan_time.isoformat() if self._last_scan_time else None,
            "skip_reasons": self._skip_reasons,
            "risk": risk_state,
            "farming": farming_state,
            "scanner_stats": self._scanner.get_stats() if self._scanner else {},
            "alpha_stats": self._alpha_detector.get_statistics() if self._alpha_detector else {}
        }


async def run_bot(
    config_path: str = None,
    paper_trading: bool = False
):
    """
    Run the trading bot.
    
    Args:
        config_path: Path to config.yaml
        paper_trading: Use paper trading mode
    """
    # Load configuration
    config = load_config(config_path)
    
    # Create and initialize bot
    bot = TradingBot(config=config, paper_trading=paper_trading)
    
    try:
        await bot.initialize()
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Bot error: {e}")
        raise
    finally:
        if bot._running:
            await bot._shutdown()


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Polymarket Weather Sniping Bot")
    parser.add_argument(
        "--config",
        type=str,
        help="Path to config.yaml",
        default=None
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Run in paper trading mode",
        default=False
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without actual trading",
        default=False
    )
    
    args = parser.parse_args()
    
    # Run the bot
    asyncio.run(run_bot(config_path=args.config, paper_trading=args.paper or args.dry_run))


if __name__ == "__main__":
    main()

