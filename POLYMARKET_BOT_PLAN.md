# Polymarket Weather Sniping Bot - Strategic Plan

## Executive Summary
Build a Python-based trading bot that snipes underpriced weather markets on Polymarket CLOB using Tomorrow.io Weather API data, with automated Liquidity Rewards farming and risk management controls.

## 1. Architecture Overview

### 1.1 System Components
```
┌─────────────────────────────────────────────────────────────────┐
│                    AWS EC2 (t3.micro)                          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Polymarket Weather Sniping Bot             │   │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────────┐    │   │
│  │  │   Scanner   │ │   Calculator │ │    Executor     │    │   │
│  │  │   Module    │ │    Module    │ │    Module       │    │   │
│  │  └─────────────┘ └─────────────┘ └─────────────────┘    │   │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────────┐    │   │
│  │  │    Risk     │ │   Logger    │ │   Secret Mgr    │    │   │
│  │  │   Manager   │ │   Module    │ │   Module        │    │   │
│  │  └─────────────┘ └─────────────┘ └─────────────────┘    │   │
│  └─────────────────────────────────────────────────────────┘   │
│                         ↓                                      │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              External Services                          │   │
│  │  • Polymarket CLOB (py-clob-client)                     │   │
│  │  • Tomorrow.io Weather API (REST)                       │   │
│  │  • Supabase (Logging & Audit)                           │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Tech Stack
- **Language**: Python 3.11+
- **Framework**: AsyncIO with aiohttp
- **Exchange Client**: py-clob-client (Polymarket CLOB)
- **Weather API**: Tomorrow.io REST API
- **Database**: Supabase (PostgreSQL)
- **Cloud**: AWS EC2 t3.micro
- **Secrets**: AWS Secrets Manager / HashiCorp Vault
- **Deployment**: Docker + systemd

---

## 2. Infrastructure Design

### 2.1 AWS EC2 t3.micro Configuration
```yaml
Instance Type: t3.micro
Region: us-east-1 (lowest latency to Polymarket)
OS: Ubuntu 22.04 LTS
Storage: 8GB EBS gp3
Security Group:
  - Inbound: SSH (22), HTTP (80) - restricted to IP
  - Outbound: HTTPS (443) - all
```

### 2.2 Secret Management Strategy
**AWS Secrets Manager Structure:**
```
polymarket-bot/
├── polygon_private_key       # Encrypted wallet private key
├── polymarket_api_key        # Polymarket CLOB API key
├── tomorrow_io_api_key       # Tomorrow.io API key
├── supabase_url              # Supabase project URL
├── supabase_service_key      # Supabase service role key
└── webhook_secret            # Alert webhook secret
```

**Access Pattern:**
```python
import boto3
import json
from botocore.exceptions import ClientError

def get_secret(secret_name: str) -> dict:
    """Retrieve secrets from AWS Secrets Manager"""
    session = boto3.session.Session()
    client = session.client(service_name='secretsmanager')
    
    try:
        response = client.get_secret_value(SecretId=secret_name)
        return json.loads(response['SecretString'])
    except ClientError as e:
        logging.error(f"Failed to retrieve secret: {e}")
        raise
```

### 2.3 Deployment Setup
```bash
# Docker Compose for production
version: '3.8'
services:
  weather-bot:
    build: .
    container_name: polymarket-weather-bot
    environment:
      - AWS_DEFAULT_REGION=us-east-1
      - AWS_SECRET_NAME=polymarket-bot/production
      - LOG_LEVEL=INFO
    volumes:
      - ./config:/app/config:ro
      - ./logs:/app/logs
    restart: unless-stopped
    command: ["python", "main.py"]
```

---

## 3. Core Modules Design

### 3.1 Scanner Module (`scanner.py`)
**Purpose**: Discover and filter weather markets with Liquidity Rewards

**Function**: `scan_markets()` → List[Market]
```python
async def scan_markets() -> List[Market]:
    """
    Find markets with:
    1. "Blue Clock" indicator (Liquidity Rewards active)
    2. YES price < $0.15
    3. Category: Weather
    4. Active status
    """
    # Step 1: Get all active weather markets
    markets = await clob_client.get_markets(
        filter={
            "category": "weather",
            "active": True,
            "outcome": "YES"
        }
    )
    
    # Step 2: Filter for Blue Clock (Liquidity Rewards)
    reward_markets = []
    for market in markets:
        if market.has_liquidity_rewards() and market.yes_price < 0.15:
            reward_markets.append(market)
    
    return reward_markets
```

**Key Filters:**
- Liquidity Rewards: Check `liquidity_rewards_enabled` flag
- Price threshold: `yes_price < 0.15`
- Category: Weather-related keywords

### 3.2 Calculator Module (`calculator.py`)
**Purpose**: Calculate "Fair Price" using Tomorrow.io weather data

**Function**: `calculate_fair_price(market, weather_data) → float`

**Mathematical Model:**
```
Fair Price = CDF(temperature_deviation)
Where:
  - μ = Expected temperature (historical average)
  - σ = 3.5°F (1.9°C) standard deviation
  - CDF = Standard Normal Cumulative Distribution Function
```

**Implementation:**
```python
import numpy as np
from scipy import stats

def calculate_fair_price(
    market_condition: str,
    forecasted_temp: float,
    expected_temp: float = 65.0,  # Adjust per market
    sigma: float = 3.5  #°F
) -> float:
    """
    Calculate fair price using Normal Distribution CDF
    
    Args:
        market_condition: e.g., "temp_above_70", "rain"
        forecasted_temp: Tomorrow.io forecast
        expected_temp: Historical average
        sigma: Temperature volatility
    
    Returns:
        Fair price between 0 and 1
    """
    # Calculate z-score
    z_score = (forecasted_temp - expected_temp) / sigma
    
    # Get CDF value (probability)
    # For "above" conditions: use 1 - CDF
    # For "below" conditions: use CDF directly
    if "above" in market_condition.lower():
        fair_price = 1 - stats.norm.cdf(z_score)
    elif "below" in market_condition.lower():
        fair_price = stats.norm.cdf(z_score)
    else:
        # For categorical events (rain, snow, etc.)
        fair_price = 0.5  # Default midpoint
    
    # Clamp to valid range
    return np.clip(fair_price, 0.01, 0.99)
```

**Tomorrow.io Integration:**
```python
import aiohttp

class TomorrowIOClient:
    BASE_URL = "https://api.tomorrow.io/v4"
    
    async def get_weather(self, location: tuple[float, float]) -> dict:
        """Get weather forecast for specific coordinates"""
        async with aiohttp.ClientSession() as session:
            params = {
                "apikey": self.api_key,
                "location": f"{location[0]},{location[1]}",
                "fields": "temperature,temperatureApparent,temperatureMin,temperatureMax,precipitationProbability,weatherCode",
                "timesteps": "1h",
                "units": "imperial"
            }
            
            async with session.get(f"{self.BASE_URL}/weather", params=params) as response:
                return await response.json()
    
    def extract_temperature(self, weather_data: dict) -> float:
        """Extract mean temperature from forecast"""
        timelines = weather_data.get("data", {}).get("timelines", [])
        if not timelines:
            return 65.0  # Default
        
        hourly = timelines[0].get("intervals", [])
        temps = [h["values"]["temperature"] for h in hourly[:24]]  # 24h avg
        return sum(temps) / len(temps)
```

### 3.3 Executor Module (`executor.py`)
**Purpose**: Execute EIP-712 signed orders with Post-Only protection

**Key Functions:**
1. `place_buy_order()` - Post-only limit buy
2. `place_sell_order()` - Post-only limit sell
3. `place_maker_order()` - Combined for Liquidity Farming

**Order Structure:**
```python
from py_clob_client.client import ClobClient
from py_clob_client.signer import Signer

class OrderExecutor:
    def __init__(self, clob_client: ClobClient, signer: Signer):
        self.client = clob_client
        self.signer = signer
    
    async def place_post_only_order(
        self,
        market_id: str,
        side: str,  # "BUY" or "SELL"
        price: float,
        size: float
    ) -> dict:
        """
        Place EIP-712 signed Post-Only limit order
        
        Benefits:
        - Avoids maker fees (get rebates instead)
        - Qualifies for Liquidity Rewards
        - Ensures order doesn't execute immediately (maker only)
        """
        order = {
            "market_id": market_id,
            "side": side,
            "price": price,  # Price in dollars (0.10 = 10 cents)
            "size": size,   # Size in USDC
            "order_type": "LIMIT",
            "time_in_force": "GTC",
            "post_only": True,  # CRITICAL: Ensures maker status
            "nonce": self.client.get_nonce(),
            "expiration": int(time.time() + 86400)  # 24h expiry
        }
        
        # Sign with EIP-712
        signed_order = self.signer.sign_order(order)
        
        # Submit to CLOB
        response = await self.client.post_order(signed_order)
        
        return response
```

**Farming Logic (Price < $0.10):**
```python
async def farm_liquidity(
    self,
    market_id: str,
    current_price: float,
    max_spread: float = 0.02
) -> List[dict]:
    """
    When price < $0.10, place BOTH buy and sell orders
    to earn Liquidity Rewards
    
    Example:
    - Market Price: $0.08
    - Buy Order: $0.07 (2 cents below)
    - Sell Order: $0.09 (1 cent above)
    - Spread: $0.02
    """
    orders_placed = []
    
    if current_price < 0.10:
        # Calculate spread positions
        buy_price = max(0.01, current_price - 0.01)
        sell_price = min(0.99, current_price + max_spread)
        
        # Place buy order
        buy_result = await self.place_post_only_order(
            market_id=market_id,
            side="BUY",
            price=buy_price,
            size=self.max_trade_size
        )
        orders_placed.append(buy_result)
        
        # Place sell order (for liquidity provision)
        sell_result = await self.place_post_only_order(
            market_id=market_id,
            side="SELL",
            price=sell_price,
            size=self.max_trade_size
        )
        orders_placed.append(sell_result)
        
        logging.info(f"Farming liquidity on {market_id}: BUY @ {buy_price}, SELL @ {sell_price}")
    
    return orders_placed
```

### 3.4 Alpha Snipe Module (`alpha.py`)
**Purpose**: Identify and aggressively trade undervalued markets

**Condition Check:**
```python
def calculate_alpha(
    fair_price: float,
    market_price: float
) -> float:
    """
    Calculate alpha (mispricing percentage)
    
    Alpha = (Fair Price - Market Price) / Market Price
    
    Returns positive value if undervalued
    """
    if market_price <= 0:
        return 0
    
    alpha = (fair_price - market_price) / market_price
    return alpha

def should_increase_position(alpha: float, threshold: float = 0.20) -> bool:
    """
    Decision: Increase buy size if alpha > 20%
    """
    return alpha > threshold
```

**Size Adjustment Logic:**
```python
def calculate_trade_size(
    base_size: float,
    alpha: float,
    max_alpha_multiplier: float = 3.0
) -> float:
    """
    Scale position size based on alpha
    
    Base Size: $2.50
    Alpha 20%:  1.4x multiplier → $3.50 (capped at max)
    Alpha 50%:  2.5x multiplier → $6.25 (capped)
    Alpha 100%: 3.0x multiplier → $7.50 (capped at max)
    """
    multiplier = 1 + min(alpha, 1.0) * (max_alpha_multiplier - 1)
    adjusted_size = base_size * multiplier
    
    # Cap at maximum trade size
    return min(adjusted_size, base_size * max_alpha_multiplier)
```

### 3.5 Auto-Exit Module (`auto_exit.py`)
**Purpose**: Automatically close positions at profit targets

**Exit Logic:**
```python
class AutoExitManager:
    PROFIT_THRESHOLD = 1.0  # 100% profit target
    
    def __init__(self, positions: dict):
        self.positions = positions  # {market_id: Position}
    
    def should_exit(self, market_id: str, current_price: float) -> bool:
        """
        Exit if position value doubled
        
        Example:
        - Bought at: $0.10
        - Current:   $0.20
        - Profit:    100%
        - Action:    SELL immediately
        """
        position = self.positions.get(market_id)
        if not position:
            return False
        
        current_value = position.size * current_price
        cost_basis = position.size * position.avg_price
        
        profit_pct = (current_value - cost_basis) / cost_basis
        
        return profit_pct >= self.PROFIT_THRESHOLD
    
    async def execute_exit(self, market_id: str) -> dict:
        """
        Execute market sell order to close position
        """
        position = self.positions.get(market_id)
        
        # Use market order for fast exit (accept taker fee)
        exit_order = {
            "market_id": market_id,
            "side": "SELL",
            "size": position.size,
            "order_type": "MARKET",
            "time_in_force": "IOC"  # Immediate or cancel
        }
        
        signed = self.signer.sign_order(exit_order)
        return await self.client.post_order(signed)
```

### 3.6 Risk Manager Module (`risk.py`)
**Purpose**: Enforce all risk parameters

**Risk Parameters:**
```python
class RiskManager:
    INITIAL_CAPITAL = 50.0      # USD on Polygon
    MAX_TRADE_SIZE = 2.50       # Per event
    MAX_DAILY_LOSS = 5.00       # Stop trading if exceeded
    MAX_OPEN_POSITIONS = 5      # Diversification
    POSITION_CONCENTRATION = 0.20  # Max 20% in single market
    
    def __init__(self, current_capital: float, open_positions: List):
        self.capital = current_capital
        self.open_positions = open_positions
    
    def can_open_position(self, size: float) -> bool:
        """Check if new position is allowed"""
        # Check capital
        if size > self.capital:
            return False
        
        # Check max trade size
        if size > self.MAX_TRADE_SIZE:
            return False
        
        # Check daily loss limit
        daily_pnl = self.calculate_daily_pnl()
        if daily_pnl < -self.MAX_DAILY_LOSS:
            return False
        
        # Check position concentration
        if self.capital > 0:
            concentration = (self.capital - size) / self.capital
            if concentration < self.POSITION_CONCENTRATION:
                return False
        
        # Check max open positions
        if len(self.open_positions) >= self.MAX_OPEN_POSITIONS:
            return False
        
        return True
    
    def calculate_daily_pnl(self) -> float:
        """Calculate PnL for current trading day"""
        # Implementation to track daily gains/losses
        pass
```

### 3.7 Logger Module (`logger.py`)
**Purpose**: Log all decisions to Supabase

**Supabase Schema:**
```sql
-- Audit log table
CREATE TABLE polymarket_audit_log (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    event_type VARCHAR(50) NOT NULL,
    market_id VARCHAR(100),
    action VARCHAR(50),
    fair_price DECIMAL(10, 4),
    market_price DECIMAL(10, 4),
    alpha DECIMAL(10, 4),
    trade_size DECIMAL(10, 4),
    weather_data JSONB,
    decision_reason TEXT,
    profit_loss DECIMAL(10, 4),
    reward_earned DECIMAL(10, 4),
    status VARCHAR(20),
    error_message TEXT
);

-- Daily rewards table
CREATE TABLE liquidity_rewards (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at DATE DEFAULT CURRENT_DATE,
    market_id VARCHAR(100),
    usdc_earned DECIMAL(10, 4),
    order_ids TEXT[]
);
```

**Logging Implementation:**
```python
from supabase import create_client, Client

class AuditLogger:
    def __init__(self, url: str, key: str):
        self.client: Client = create_client(url, key)
    
    async def log_decision(
        self,
        event_type: str,
        market_id: str,
        action: str,
        fair_price: float,
        market_price: float,
        alpha: float,
        trade_size: float,
        weather_data: dict,
        decision_reason: str,
        profit_loss: float = None,
        reward_earned: float = None,
        status: str = "SUCCESS",
        error_message: str = None
    ):
        """Log trading decision to Supabase"""
        record = {
            "event_type": event_type,
            "market_id": market_id,
            "action": action,
            "fair_price": fair_price,
            "market_price": market_price,
            "alpha": alpha,
            "trade_size": trade_size,
            "weather_data": weather_data,
            "decision_reason": decision_reason,
            "profit_loss": profit_loss,
            "reward_earned": reward_earned,
            "status": status,
            "error_message": error_message
        }
        
        return self.client.table("polymarket_audit_log").insert(record).execute()
    
    async def log_reward(self, market_id: str, usdc_earned: float, order_ids: List[str]):
        """Log liquidity reward earned"""
        record = {
            "market_id": market_id,
            "usdc_earned": usdc_earned,
            "order_ids": order_ids
        }
        
        return self.client.table("liquidity_rewards").insert(record).execute()
```

---

## 4. Trading Logic Flow

### 4.1 Main Loop
```python
async def main():
    """
    Main trading loop
    """
    # Initialize components
    secrets = get_all_secrets()
    clob = initialize_clob_client(secrets)
    signer = initialize_signer(secrets)
    tomorrow_io = TomorrowIOClient(secrets['tomorrow_io_api_key'])
    logger = AuditLogger(secrets['supabase_url'], secrets['supabase_service_key'])
    
    # Risk state
    capital = INITIAL_CAPITAL
    positions = {}
    
    while True:
        try:
            # 1. Scan for opportunities
            markets = await scanner.scan_markets()
            
            for market in markets:
                # 2. Get current price (use get_price, NOT get_book)
                current_price = await clob.get_price(market_id=market.id)
                
                # 3. Get weather data
                weather_data = await tomorrow_io.get_weather(market.coordinates)
                
                # 4. Calculate fair price
                fair_price = calculator.calculate_fair_price(
                    market.condition,
                    weather_data.temperature,
                    market.expected_temp
                )
                
                # 5. Calculate alpha
                alpha = calculator.calculate_alpha(fair_price, current_price)
                
                # 6. Risk check
                trade_size = calculate_trade_size(MAX_TRADE_SIZE, alpha)
                if not risk_manager.can_open_position(trade_size):
                    await logger.log_decision(
                        event_type="RISK_CHECK_FAILED",
                        market_id=market.id,
                        action="SKIP",
                        fair_price=fair_price,
                        market_price=current_price,
                        alpha=alpha,
                        trade_size=trade_size,
                        weather_data=weather_data.to_dict(),
                        decision_reason="Risk check failed",
                        status="BLOCKED"
                    )
                    continue
                
                # 7. Execute trade
                if current_price < 0.10:
                    # Farming mode: place both buy and sell
                    await executor.farm_liquidity(
                        market.id, current_price, max_spread=0.02
                    )
                    decision = "FARM_LIQUIDITY"
                elif alpha > 0.20:
                    # Alpha mode: increase size
                    increased_size = calculate_trade_size(trade_size, alpha)
                    await executor.place_post_only_order(
                        market.id, "BUY", current_price, increased_size
                    )
                    decision = "ALPHA_SNIPE"
                else:
                    decision = "NO_ACTION"
                
                # 8. Log decision
                await logger.log_decision(
                    event_type="TRADE_DECISION",
                    market_id=market.id,
                    action=decision,
                    fair_price=fair_price,
                    market_price=current_price,
                    alpha=alpha,
                    trade_size=trade_size,
                    weather_data=weather_data.to_dict(),
                    decision_reason=f"Alpha={alpha:.2%}, Price=${current_price:.4f}"
                )
                
                # 9. Check auto-exit for existing positions
                for pos_id, position in positions.items():
                    if auto_exit.should_exit(pos_id, current_price):
                        await auto_exit.execute_exit(pos_id)
                        await logger.log_decision(
                            event_type="AUTO_EXIT",
                            market_id=pos_id,
                            action="SELL",
                            market_price=current_price,
                            decision_reason="100% profit target reached",
                            profit_loss=position.size * (current_price - position.avg_price)
                        )
            
            # Wait before next scan
            await asyncio.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            logging.error(f"Main loop error: {e}")
            await logger.log_decision(
                event_type="ERROR",
                action="LOG_ERROR",
                decision_reason=str(e),
                status="ERROR",
                error_message=str(e)
            )
            await asyncio.sleep(60)  # Back off on error
```

---

## 5. Configuration

### 5.1 Config File (`config.yaml`)
```yaml
# Polymarket Configuration
polymarket:
  chain_id: 137  # Polygon
  api_url: https://clob.polymarket.com
  api_key: ${POLYMARKET_API_KEY}

# Tomorrow.io Configuration
tomorrow_io:
  api_key: ${TOMORROW_IO_API_KEY}
  default_location:  # Default coordinates
    lat: 40.7128
    lon: -74.0060

# Risk Parameters
risk:
  initial_capital: 50.0
  max_trade_size: 2.50
  max_daily_loss: 5.0
  max_open_positions: 5
  profit_target: 1.0  # 100%

# Farming Parameters
farming:
  enabled: true
  price_threshold: 0.10
  max_spread: 0.02
  farming_size: 2.50

# Alpha Parameters
alpha:
  enabled: true
  threshold: 0.20  # 20% undervaluation
  size_multiplier_max: 3.0

# Scanner Parameters
scanner:
  scan_interval: 30  # seconds
  price_threshold: 0.15
  categories:
    - weather
    - temperature
    - precipitation

# Logging
logging:
  level: INFO
  supabase_url: ${SUPABASE_URL}
  supabase_key: ${SUPABASE_KEY}
  log_table: polymarket_audit_log

# AWS Secrets
aws:
  region: us-east-1
  secret_name: polymarket-bot/production
```

---

## 6. File Structure

```
polymarket-weather-bot/
├── config/
│   └── config.yaml
├── src/
│   ├── __init__.py
│   ├── main.py                 # Entry point
│   ├── scanner.py              # Market scanning
│   ├── calculator.py           # Fair price calculation
│   ├── executor.py             # Order execution
│   ├── alpha.py                # Alpha detection
│   ├── auto_exit.py            # Profit taking
│   ├── risk.py                 # Risk management
│   ├── logger.py               # Supabase logging
│   └── clients/
│       ├── __init__.py
│       ├── polymarket.py       # CLOB client wrapper
│       └── tomorrow_io.py      # Weather API client
├── tests/
│   ├── __init__.py
│   ├── test_calculator.py
│   ├── test_executor.py
│   └── test_risk.py
├── logs/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── PLAN.md
└── README.md
```

---

## 7. Implementation Steps

### Phase 1: Foundation (Day 1)
1. [ ] Set up project structure
2. [ ] Create config.yaml
3. [ ] Implement Secrets Manager integration
4. [ ] Implement basic CLOB client connection
5. [ ] Implement get_price endpoint (NOT get_book)

### Phase 2: Core Logic (Day 2)
6. [ ] Implement Scanner module
7. [ ] Implement Tomorrow.io client
8. [ ] Implement Calculator module with CDF math
9. [ ] Implement Risk Manager module

### Phase 3: Execution (Day 3)
10. [ ] Implement Executor module with EIP-712 signing
11. [ ] Implement Post-Only order placement
12. [ ] Implement Liquidity Farming logic
13. [ ] Implement Alpha Snipe logic

### Phase 4: Automation (Day 4)
14. [ ] Implement Auto-Exit module
15. [ ] Implement main trading loop
16. [ ] Implement Supabase logging
17. [ ] Add error handling and retries

### Phase 5: Deployment (Day 5)
18. [ ] Create Dockerfile
19. [ ] Set up AWS EC2 instance
20. [ ] Configure Secrets Manager
21. [ ] Deploy and test in staging
22. [ ] Go live with paper trading
23. [ ] Switch to live trading

---

## 8. Risk Disclosure & Compliance

### 8.1 Financial Risk Warning
⚠️ **IMPORTANT**: This bot trades with real capital. Losses may exceed initial investment.

### 8.2 Parameters Summary
| Parameter | Value | Purpose |
|-----------|-------|---------|
| Initial Capital | $50 USD | Starting bankroll |
| Max Trade Size | $2.50/event | Position limit |
| Profit Target | 100% | Auto-exit trigger |
| Daily Loss Limit | $5.00 | Stop trading trigger |
| Max Open Positions | 5 | Diversification |

### 8.3 Testing Protocol
1. **Paper Trading**: Run for 48 hours with no real capital
2. **Small Capital Test**: Start with $10, trade for 24 hours
3. **Gradual Scale**: Increase to full $50 after successful tests

---

## 9. Key API Endpoints

### 9.1 Polymarket CLOB (py-clob-client)
```python
# Get price (use this, NOT get_book)
price = await clob.get_price(market_id="0x1234...")

# Get market details
market = await clob.get_market(market_id="0x1234...")

# Place order
order = await clob.post_order(signed_order)

# Get positions
positions = await clob.get_positions()

# Get order book (if needed)
book = await clob.get_order_book(market_id="0x1234...")
```

### 9.2 Tomorrow.io REST API
```bash
# Weather forecast
GET https://api.tomorrow.io/v4/weather?apikey=XXX&location=40.7128,-74.0060&fields=temperature,precipitationProbability&timesteps=1h&units=imperial
```

---

## 10. Success Metrics

### 10.1 Performance Targets
- **Win Rate**: > 60%
- **Average Profit per Trade**: > 15%
- **Daily Liquidity Rewards**: > $0.50
- **Monthly Return**: > 10% on capital

### 10.2 Operational Targets
- **Uptime**: > 99%
- **Error Rate**: < 1%
- **Execution Latency**: < 5 seconds
- **Audit Log Coverage**: 100% of trades

---

## 11. Contingency Plans

### 11.1 API Failure
- Retry with exponential backoff (max 3 attempts)
- Fallback to cached weather data
- Log error and skip trade

### 11.2 Market Freezes
- Cancel all open orders
- Close positions at market price
- Pause trading for 1 hour

### 11.3 Large Losses
- Trigger daily loss limit ($5.00)
- Pause all trading
- Alert via webhook
- Manual review required

---

*Plan generated for Polymarket Weather Sniping Bot*
*Version: 1.0*
*Date: 2024*

