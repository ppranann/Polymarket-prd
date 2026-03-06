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
from .clients.nws_client import create_nws_client, NWSClient, US_CITY_COORDS
from .clients.open_meteo import create_open_meteo_client, OpenMeteoClient
from .calculator import create_calculator, FairPriceCalculator
from .risk import create_risk_manager, RiskManager
from .scanner import create_market_scanner, MarketScanner, MarketGroup
from .executor import create_order_executor, OrderExecutor
from .alpha import create_alpha_detector, AlphaDetector
from .auto_exit import create_auto_exit_manager, AutoExitManager
from .logger import create_audit_logger, AuditLogger, PredictionLog
from .performance import create_performance_reporter, PerformanceReporter
from .resolution import ResolutionTracker
from .utils import extract_city, extract_target_date, make_event_key, classify_market_type, hours_remaining_in_local_day

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
        live_trading: bool = False
    ):
        """
        Initialize the trading bot.

        Args:
            config: Bot configuration
            live_trading: Explicitly opt into live trading (real money).
                          Default is False — paper mode is always the safe default.
        """
        self.config = config
        # Paper mode is the DEFAULT. Live trading requires an explicit --live flag.
        # This means a missing flag, stale config, or deployment mistake can NEVER
        # accidentally trigger real trades.
        self.paper_trading = not live_trading
        
        # Component placeholders
        self._secrets = None
        self._polymarket_client = None
        self._weather_client = None
        self._nws_client: Optional[NWSClient] = None
        self._open_meteo_client: Optional[OpenMeteoClient] = None
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
            per_city_cooldown_seconds=1800.0,  # 30 min cooldown after 429
            global_min_interval_seconds=2.0,  # 2s between API calls
        )
        # Weather cache: 30min TTL.
        # 3 cities × 2 fetches/hour = 6 calls/hour → well under Tomorrow.io's 25/hour limit.
        # 10min TTL (18 calls/hour) was hitting limits on restarts when all 3 city caches
        # are cold simultaneously. Forecast data changes slowly enough that 30min is fine.
        self._weather_client = create_tomorrow_client(
            api_key=self._secrets.tomorrow_io_api_key,
            rate_limit_config=rate_limit_config,
            cache_ttl_seconds=1800.0,  # 30 minutes
        )

        logger.info("Weather client initialized with rate limiting")

        # Initialize NWS client for US cities (free, no API key, no rate limits)
        logger.info("Initializing NWS client for US cities...")
        self._nws_client = create_nws_client(cache_ttl_seconds=1800.0)
        logger.info("NWS client initialized (NYC, Atlanta, Miami, Seattle, Dallas, Chicago)")

        # Initialize Open-Meteo client (free, no API key) for ensemble validation
        logger.info("Initializing Open-Meteo ensemble client...")
        self._open_meteo_client = create_open_meteo_client()
        logger.info("Open-Meteo client initialized")

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

        # Paper mode: override capital and position cap for data collection.
        # Live values in risk: section are preserved for real-money trading.
        if self.paper_trading:
            self._risk_manager._capital = self.config.development.paper_capital
            self._risk_manager.risk.max_open_positions = self.config.development.paper_max_positions
            self._risk_manager.risk.max_exposure_per_event = self.config.development.paper_max_exposure_per_event
            self._risk_manager.risk.max_daily_loss = self.config.development.paper_max_daily_loss
            logger.info(
                f"Paper mode overrides: capital=${self.config.development.paper_capital:.0f}, "
                f"max_positions={self.config.development.paper_max_positions}, "
                f"max_exposure_per_event=${self.config.development.paper_max_exposure_per_event:.0f}, "
                f"max_daily_loss=${self.config.development.paper_max_daily_loss:.0f}"
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

        # Pre-populate permanent block list from Supabase history on startup.
        # Without this, container restarts clear the in-memory block set and
        # the bot re-buys markets it already holds or has already exited.
        #
        # Block recently bought markets (covers open positions lost on restart)
        # AND recently exited markets (covers the exit+re-entry bug).
        bought_ids = await self._logger.get_recently_bought_market_ids(days=7)
        exited_ids = await self._logger.get_recently_exited_market_ids(days=7)
        all_blocked = set(bought_ids) | set(exited_ids)
        for market_id in all_blocked:
            self._executor.block_market(market_id)
        if all_blocked:
            logger.info(
                f"Startup block list: {len(all_blocked)} market(s) blocked "
                f"({len(bought_ids)} bought, {len(exited_ids)} exited)"
            )

        # Recover open positions from Supabase so the bot can manage them
        # (apply stop-loss, track P&L) even after a container restart.
        await self._recover_open_positions()

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
                # Step 0: Check circuit breaker + daily loss auto-reset (Priority 4: Risk Management)
                # Both the circuit breaker and daily-loss pause are UTC-day-scoped.
                # Auto-reset at the start of each new UTC day so the bot resumes automatically.
                now_utc = datetime.utcnow()
                if not hasattr(self, '_last_reset_date'):
                    self._last_reset_date = now_utc.date()
                if now_utc.date() > self._last_reset_date:
                    self._last_reset_date = now_utc.date()
                    if self._risk_manager._trading_paused:
                        logger.info(
                            f"AUTO-RESET: new UTC day ({now_utc.date()}), "
                            f"clearing daily loss pause (was: {self._risk_manager._pause_reason})"
                        )
                        self._risk_manager.resume_trading()
                        self._risk_manager.reset_daily_stats()
                    if self._risk_manager._circuit_breaker_triggered:
                        logger.info(
                            f"AUTO-RESET: new UTC day ({now_utc.date()}), "
                            "clearing circuit breaker"
                        )
                        self._risk_manager._circuit_breaker_triggered = False
                        self._risk_manager._recent_pnl.clear()

                if self._risk_manager._circuit_breaker_triggered:
                    logger.critical("Circuit breaker triggered - trading halted until next UTC day")
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
                        # Close resolved positions in the risk manager so slots free up.
                        # In paper mode nothing is sold, but the position needs to be
                        # removed from the in-memory count or max_open_positions blocks
                        # all new trades indefinitely.
                        for market_id, result in resolutions.items():
                            if result.get("resolved") and self._risk_manager.get_position(market_id):
                                exit_price = 1.0 if result.get("winning_side") == "YES" else 0.0
                                self._risk_manager.close_position(market_id, exit_price)
                                logger.info(
                                    f"Resolution close: {market_id[:8]}... → "
                                    f"{result['winning_side']} @ ${exit_price:.2f}"
                                )

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

            # Step 4: Group markets by city+date event
            market_groups = self._scanner.group_markets_by_event(
                result.qualifying_markets
            )

            # Step 5: Process each market group with shared weather + distribution
            skipped_cities = []
            for group in market_groups:
                city = group.city

                # Check scan-level cache first
                if city in weather_cache:
                    weather_data = weather_cache[city]
                else:
                    # Route US cities to NWS (free, no limits), international to Tomorrow.io
                    if city in US_CITY_COORDS:
                        weather_data = await self._nws_client.get_city_weather(city)
                        if not weather_data:
                            logger.warning(f"Skipping {city} - NWS data unavailable.")
                            skipped_cities.append(city)
                            continue
                    else:
                        # Check Tomorrow.io cooldown first
                        if self._weather_client._is_city_rate_limited(city):
                            logger.warning(f"Skipping {city} - still in cooldown from previous rate limit.")
                            skipped_cities.append(city)
                            continue

                        location = self._weather_client.COORD_MAP.get(
                            city, self._weather_client.COORD_MAP["nyc"]
                        )
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
                            ],
                            timesteps="1h",
                        )
                        if not weather_raw:
                            logger.warning(f"Skipping {city} - weather data unavailable.")
                            skipped_cities.append(city)
                            continue

                        weather_data = await self._process_weather_response(weather_raw)
                        if not weather_data:
                            logger.warning(f"Skipping {city} - could not parse weather data.")
                            skipped_cities.append(city)
                            continue

                    weather_cache[city] = weather_data

                # Analyze the entire market group using cross-market alpha
                try:
                    await self._analyze_market_group(
                        group=group,
                        weather_data=weather_data,
                        prices=prices,
                    )
                except Exception as e:
                    logger.error(f"Error analyzing group {group.event_key}: {e}")
                    await self._logger.log_error(
                        error_type="GROUP_ANALYSIS_ERROR",
                        error_message=str(e),
                        context={"event_key": group.event_key}
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
    
    async def _analyze_market_group(
        self,
        group: MarketGroup,
        weather_data,
        prices: Dict[str, float],
    ) -> None:
        """
        Analyze all markets in a city+date group using shared distribution
        and cross-market alpha signals.

        Args:
            group: MarketGroup with threshold and bucket markets
            weather_data: Pre-fetched WeatherData for the city
            prices: Dict of market_id → current price
        """
        # Get date-specific weather
        date_weather = self._get_weather_for_date(weather_data, group.date)

        # Compute lead time in hours
        _, days_out = extract_target_date(
            group.all_markets[0].question if group.all_markets else ""
        )
        # Negative days_out means the target date is in the past — market already expired.
        if days_out < 0:
            logger.info(
                f"Skipping {group.event_key} — target date is in the past (days_out={days_out})"
            )
            return
        lead_hours = days_out * 24

        # Same-day markets (lead=0h): only trade if enough of the day remains.
        # Early morning still has model edge (day's max temp not yet known).
        # Afternoon/evening: market prices in live observations we can't see.
        # Cutoff: skip if < 6h left in local day (outcome basically determined).
        if lead_hours == 0:
            hours_left = hours_remaining_in_local_day(group.city)
            if hours_left < 6:
                logger.info(
                    f"Skipping {group.event_key} — {hours_left:.1f}h left in local day "
                    f"(< 6h cutoff), outcome nearly determined"
                )
                return
            logger.info(
                f"Same-day {group.event_key}: {hours_left:.1f}h remaining locally — trading"
            )

        # Get shared distribution (mu, sigma) in Fahrenheit
        forecast_spread_f = date_weather.temp_max - date_weather.temp_min
        mu, sigma = self._calculator.get_distribution(
            forecast_max_temp_f=date_weather.temp_max,
            lead_hours=lead_hours,
            forecast_spread_f=forecast_spread_f,
        )

        logger.info(
            f"Distribution for {group.event_key}: "
            f"mu={mu:.1f}°F, sigma={sigma:.1f}°F "
            f"(forecast_max={date_weather.temp_max:.1f}°F, lead={lead_hours}h, "
            f"{len(group.threshold_markets)} threshold, {len(group.bucket_markets)} bucket)"
        )

        # --- Ensemble gate: validate against Open-Meteo (free second source) ---
        # If the two independent sources disagree by more than the threshold,
        # the forecast is genuinely uncertain — skip rather than trade noise.
        ensemble_max_spread = getattr(self.config.alpha, 'ensemble_max_spread_f', 4.0)
        try:
            om_temp = await self._open_meteo_client.get_temp_for_date(group.city, group.date)
            if om_temp is not None:
                spread = abs(mu - om_temp)
                if spread > ensemble_max_spread:
                    logger.info(
                        f"ENSEMBLE SKIP {group.event_key}: "
                        f"primary={mu:.1f}°F vs Open-Meteo={om_temp:.1f}°F "
                        f"(spread={spread:.1f}°F > {ensemble_max_spread:.1f}°F threshold) "
                        f"— forecasts disagree, skipping to avoid noise"
                    )
                    return
                logger.info(
                    f"ENSEMBLE OK {group.event_key}: "
                    f"primary={mu:.1f}°F Open-Meteo={om_temp:.1f}°F spread={spread:.1f}°F"
                )
            else:
                logger.info(
                    f"ENSEMBLE N/A {group.event_key}: Open-Meteo has no data for {group.date}, proceeding"
                )
        except Exception as e:
            logger.warning(f"Open-Meteo check failed for {group.event_key}: {e} — proceeding without ensemble gate")

        # Get cross-market alpha signals
        signals = self._alpha_detector.analyze_market_group(
            group=group,
            mu=mu,
            sigma=sigma,
            prices=prices,
        )

        # Execute trades for actionable signals
        for signal in signals:
            if signal.action not in ("BUY", "SELL"):
                self._record_skip_reason("no_alpha")
                continue

            market = None
            for m in group.all_markets:
                if m.id == signal.market_id:
                    market = m
                    break

            if not market:
                continue

            # Skip SELL signals without position
            if signal.action == "SELL":
                position = self._risk_manager.get_position(market.id)
                if not position or position.size <= 0:
                    self._record_skip_reason("no_position_sell")
                    continue

            # Liquidity gate: skip markets with very low volume (thin/stale)
            if market.volume < 50:
                logger.info(
                    f"Liquidity skip: {market.id[:8]} volume=${market.volume:.0f} < $50"
                )
                self._record_skip_reason("thin_market")
                continue

            # Per-scan trade budget
            if getattr(self, '_scan_trades_this_cycle', 0) >= getattr(self, '_scan_trade_budget', 5):
                self._record_skip_reason("scan_budget")
                continue

            current_price = prices.get(market.id, market.yes_price)

            logger.info(
                f"Alpha signal: {signal.action} {signal.market_type} | "
                f"Alpha: ${float(signal.alpha):.2f} | "
                f"P_model: {signal.p_model:.3f} | "
                f"P_implied: {f'{signal.p_implied:.3f}' if signal.p_implied is not None else 'N/A'} | "
                f"Market: ${current_price:.4f} | "
                f"{group.event_key}"
            )

            result = await self._executor.execute_trade(
                market=market,
                current_price=current_price,
                fair_price=signal.fair_price,
                alpha=signal.alpha,
                action=signal.action,
                confidence=signal.confidence,
                market_type=signal.market_type,
                event_key=group.event_key,
            )

            self._trade_count += 1
            if result.success:
                self._scan_trades_this_cycle = getattr(self, '_scan_trades_this_cycle', 0) + 1

            # Only log to Supabase on actual executions — not on SKIP/blocked
            if result.action == "SKIP":
                logger.warning(f"Trade failed: {result.reason}")
                continue

            # Trace log: exact inputs that produced this trade (key for debugging calibration)
            threshold_logged = self._calculator.get_temperature_threshold_from_condition(market.question)
            logger.info(
                f"TRADE_TRACE {market.id[:8]}: "
                f"city={group.city} date={group.date} "
                f"mu={mu:.2f}°F sigma={sigma:.2f}°F "
                f"threshold_f={threshold_logged:.2f}°F "
                f"z={(( threshold_logged - mu) / sigma if sigma > 0 and threshold_logged else 0):.3f} "
                f"p_model={signal.p_model:.4f} market_price={current_price:.4f} "
                f"alpha={signal.alpha:.4f} question='{market.question[:80]}'"
            )

            # Log prediction with cross-market fields
            prediction = PredictionLog(
                market_id=market.id,
                question_text=market.question[:200],
                city=group.city,
                target_date=group.date,
                condition_type=signal.market_type,
                threshold_f=self._calculator.get_temperature_threshold_from_condition(market.question) or signal.distribution_mu,
                forecast_temp_f=date_weather.temp_max,
                sigma_used=signal.distribution_sigma,
                z_score=signal.z_score,
                days_to_resolution=days_out,
                fair_price=signal.fair_price,
                market_price=current_price,
                alpha=signal.alpha,
                side=signal.action,
                size=result.order.size if result.order else 0.0,
                capital_at_time=self._risk_manager._capital,
                market_type=signal.market_type,
                distribution_mu=mu,
                distribution_sigma=sigma,
                p_model=signal.p_model,
                p_implied=signal.p_implied,
                bucket_range=signal.bucket_range,
            )
            await self._logger.log_prediction(prediction)

            # Log trade
            await self._logger.log_trade(
                market_id=market.id,
                action=result.action,
                fair_price=signal.fair_price,
                market_price=current_price,
                trade_size=result.order.size if result.order else 0,
                alpha=signal.alpha,
                reward_earned=result.reward_earned,
            )

            if result.success:
                logger.info(f"Trade executed: {result.action} on {market.id[:8]}...")
                self._traded_market_ids.add(market.id)
            else:
                logger.warning(f"Trade failed: {result.reason}")

    def _extract_city_from_market(self, market) -> str:
        """Extract city name from market question using shared utility."""
        return extract_city(market.question)

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
        """Extract target date and days-to-resolution using shared utility."""
        return extract_target_date(question)

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

    async def _recover_open_positions(self):
        """Reload open positions from Supabase after container restart."""
        try:
            open_positions = await self._logger.get_open_positions(days=3)
            if not open_positions:
                logger.info("No open positions to recover from Supabase")
                return

            recovered = 0
            for pos_data in open_positions:
                success = self._risk_manager.recover_position(
                    market_id=pos_data["market_id"],
                    entry_price=pos_data["entry_price"],
                    size=pos_data["size"],
                    opened_at=pos_data["opened_at"],
                    entry_alpha=pos_data["entry_alpha"],
                )
                if success:
                    recovered += 1
                    self._traded_market_ids.add(pos_data["market_id"])

            logger.info(f"Position recovery complete: {recovered}/{len(open_positions)} position(s) restored")
        except Exception as e:
            logger.error(f"Position recovery failed: {e}")

    async def _check_auto_exits(self):
        """Check positions for stop-loss conditions and cash out losers via market sell."""
        # In paper mode: hold all positions to resolution.
        # Early exits produce meaningless P&L and hide whether the model's
        # directional call was right. We validate by checking resolutions only.
        if self.paper_trading:
            return

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
                        # Block this market from re-entry for the rest of this run
                        self._executor.block_market(signal.market_id)
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

        if self._nws_client:
            await self._nws_client.close()

        if hasattr(self, '_open_meteo_client') and self._open_meteo_client:
            await self._open_meteo_client.close()

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
    live_trading: bool = False
):
    """
    Run the trading bot.

    Args:
        config_path: Path to config.yaml
        live_trading: Opt into live trading (default False = paper mode)
    """
    # Load configuration
    config = load_config(config_path)

    # Create and initialize bot
    bot = TradingBot(config=config, live_trading=live_trading)
    
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
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Polymarket Weather Sniping Bot")
    parser.add_argument(
        "--config",
        type=str,
        help="Path to config.yaml",
        default=None
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in LIVE trading mode with real money. Without this flag the bot always runs in paper mode.",
        default=False
    )
    # Keep --paper and --dry-run as no-ops for backwards compatibility
    parser.add_argument("--paper", action="store_true", default=False, help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", default=False, help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.live:
        logger.warning("=" * 60)
        logger.warning("LIVE MODE — REAL MONEY AT RISK")
        logger.warning("=" * 60)
    else:
        logger.info("PAPER MODE (default) — no real trades will be placed")


    # Run the bot
    asyncio.run(run_bot(config_path=args.config, live_trading=args.live))


if __name__ == "__main__":
    main()

