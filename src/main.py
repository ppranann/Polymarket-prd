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
from .clients.tomorrow_io import create_tomorrow_io_client, TomorrowIOClient
from .calculator import create_calculator, FairPriceCalculator
from .risk import create_risk_manager, RiskManager
from .scanner import create_market_scanner, MarketScanner
from .executor import create_order_executor, OrderExecutor
from .alpha import create_alpha_detector, AlphaDetector
from .auto_exit import create_auto_exit_manager, AutoExitManager
from .logger import create_audit_logger, AuditLogger

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
        
        # Bot state
        self._running = False
        self._start_time: Optional[datetime] = None
        self._scan_count = 0
        self._trade_count = 0
        self._last_scan_time: Optional[datetime] = None
        
        # Setup signal handlers
        self._setup_signal_handlers()
    
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
        
        # Initialize Weather client
        logger.info("Initializing Tomorrow.io client...")
        self._weather_client = create_tomorrow_io_client(
            config=self.config,
            api_key=self._secrets.tomorrow_io_api_key
        )
        
        logger.info("Weather client initialized")
        
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
        
        # Track failed cities for batch logging
        failed_cities = []
        
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
            
            # Step 3: Analyze each market
            for market in result.qualifying_markets:
                try:
                    weather_failed = await self._analyze_and_trade(
                        market, 
                        prices.get(market.id, market.yes_price)
                    )
                    # Track failed cities for batch logging
                    if weather_failed:
                        failed_cities.append(weather_failed)
                except Exception as e:
                    logger.error(f"Error analyzing {market.id}: {e}")
                    await self._logger.log_error(
                        error_type="MARKET_ANALYSIS_ERROR",
                        error_message=str(e),
                        market_id=market.id
                    )
            
            # Step 4: Log weather failures ONCE at end of scan (stop log spam)
            if failed_cities:
                unique_fails = set(failed_cities)
                logger.warning(
                    f"Scan complete. Weather data missing for: {', '.join(unique_fails)}"
                )
            
        except Exception as e:
            logger.error(f"Scan and trade error: {e}")
            await self._logger.log_error(
                error_type="SCAN_TRADE_ERROR",
                error_message=str(e)
            )
    
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
        logger.info(f"Analyzing {market.id[:8]}... (Price: ${current_price:.4f})")
        
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
                f"Alpha: {float(signal.alpha):.1%} | "
                f"Multiplier: {signal.size_multiplier:.1f}x"
            )
            
            result = await self._executor.execute_trade(
                market=market,
                current_price=current_price,
                fair_price=calculation.fair_price,
                alpha=signal.alpha,
                action=signal.action
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
    
    async def _process_weather_response(self, weather_data: Dict) -> Optional[Any]:
        """
        Process raw Tomorrow.io API response into WeatherData object.
        
        Args:
            weather_data: Raw API response dictionary
            
        Returns:
            WeatherData object or None if parsing fails
        """
        try:
            from .clients.tomorrow_io import WeatherData
            timelines = weather_data.get("data", {}).get("timelines", [])
            
            # Get hourly timeline
            hourly_timeline = next(
                (t for t in timelines if t.get("timestep") == "1h"),
                {}
            )
            intervals = hourly_timeline.get("intervals", [])
            
            if not intervals:
                logger.warning("No hourly intervals in weather response")
                return None
            
            # Use first hour's values for current conditions
            current = intervals[0]["values"]
            
            # Calculate mean from available intervals
            temps = [i["values"]["temperature"] for i in intervals if "values" in i and "temperature" in i["values"]]
            temp_mins = [i["values"]["temperatureMin"] for i in intervals if "values" in i and "temperatureMin" in i["values"]]
            temp_maxs = [i["values"]["temperatureMax"] for i in intervals if "values" in i and "temperatureMax" in i["values"]]
            
            from datetime import datetime
            
            return WeatherData(
                location=(0, 0),  # Not needed for calculation
                fetched_at=datetime.now(),
                current_temp=current.get("temperature", 65.0),
                feels_like=current.get("temperatureApparent", 65.0),
                temp_min=min(temp_mins) if temp_mins else 60.0,
                temp_max=max(temp_maxs) if temp_maxs else 70.0,
                temp_mean=sum(temps) / len(temps) if temps else 65.0,
                precipitation_probability=current.get("precipitationProbability", 0.0),
                precipitation_amount=current.get("precipitation"),
                weather_code=current.get("weatherCode"),
                weather_description=None,
                raw_data=weather_data
            )
        except Exception as e:
            logger.error(f"Error processing weather response: {e}")
            return None
    
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
                    await self._auto_exit.execute_exit(signal)
                    
                    await self._logger.log_exit(
                        market_id=signal.market_id,
                        profit_loss=signal.profit_usdc,
                        exit_reason=signal.reason,
                        duration_hours=(
                            datetime.now() - signal.position.opened_at
                        ).total_seconds() / 3600
                    )
                    
                except Exception as e:
                    logger.error(f"Auto-exit failed for {signal.market_id}: {e}")
    
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
        """Get current bot status"""
        risk_summary = self._risk_manager.get_risk_summary() if self._risk_manager else {}
        
        return {
            "running": self._running,
            "mode": "PAPER" if self.paper_trading else "LIVE",
            "scan_count": self._scan_count,
            "trade_count": self._trade_count,
            "start_time": self._start_time.isoformat() if self._start_time else None,
            "last_scan": self._last_scan_time.isoformat() if self._last_scan_time else None,
            "risk": risk_summary,
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

