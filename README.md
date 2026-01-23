# Polymarket Weather Sniping Bot
# ===============================

## Overview
A Python-based trading bot that snipes underpriced weather markets on Polymarket CLOB using Tomorrow.io Weather API data, with automated Liquidity Rewards farming and risk management controls.

## Features
- **Market Scanning**: Finds weather markets with Liquidity Rewards (Blue Clock) and YES price < $0.15
- **Fair Price Calculation**: Uses Tomorrow.io data and Normal Distribution CDF (σ=3.5°F)
- **Alpha Detection**: Identifies mispricings > 20% for increased position sizing
- **Liquidity Farming**: Places both BUY and SELL orders when price < $0.10 to earn USDC rewards
- **Auto-Exit**: Automatically exits positions at 100% profit targets
- **Risk Management**: $50 capital, $2.50 max trade size, $5 daily loss limit
- **Full Audit Logging**: All decisions logged to Supabase

## Requirements
- Python 3.11+
- AWS Account (for Secrets Manager)
- Polymarket Account with API keys
- Tomorrow.io API Key
- Supabase Account (for logging)

## Installation

### 1. Clone and Install Dependencies
```bash
git clone <repository-url>
cd polymarket-weather-bot
pip install -r requirements.txt
```

### 2. Configure Environment
Create a `.env` file or set environment variables:
```bash
# Polymarket
export POLYMARKET_API_KEY="your_polymarket_api_key"
export POLYMARKET_API_SECRET="your_polymarket_api_secret"
export POLYGON_PRIVATE_KEY="0xyour_private_key"

# Tomorrow.io
export TOMORROW_IO_API_KEY="your_tomorrow_io_key"

# Supabase
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_SERVICE_KEY="your_service_key"
```

### 3. Configure AWS Secrets Manager
Create a secret named `polymarket-bot/production` with:
```json
{
  "polygon_private_key": "0x...",
  "polymarket_api_key": "...",
  "polymarket_api_secret": "...",
  "tomorrow_io_api_key": "...",
  "supabase_url": "...",
  "supabase_service_key": "..."
}
```

### 4. Setup Supabase Tables
Run the following SQL to create audit tables:
```sql
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

CREATE TABLE polymarket_rewards (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at DATE DEFAULT CURRENT_DATE,
    market_id VARCHAR(100),
    usdc_earned DECIMAL(10, 4),
    order_ids TEXT[],
    order_type VARCHAR(20)
);
```

## Configuration

Edit `config.yaml` to customize:
- `risk.initial_capital`: Starting capital (default: $50)
- `risk.max_trade_size`: Max per-event position (default: $2.50)
- `risk.max_daily_loss`: Daily loss limit (default: $5.00)
- `scanner.scan_interval`: Seconds between scans (default: 30)
- `farming.price_threshold`: Price threshold for farming (default: $0.10)

## Advanced Risk Management

### Kelly Criterion Position Sizing
Calculate optimal position size using Kelly Criterion for maximum long-term growth:
```python
# Automatically calculates optimal size based on win rate and avg win/loss
kelly = calculate_kelly(win_rate=0.6, avg_win=0.15, avg_loss=0.10)
# Uses half-Kelly by default for safety
```

### Trailing Stop-Loss
Lock in profits as price moves in your favor:
```python
# Entry: $0.10, Trailing: 20%
# If price rises to $0.15, stop = $0.12
# If price drops to $0.12, sell and lock in 20% profit
```

### Volatility-Adjusted Sizing
Reduce position size during high market volatility:
```python
# High volatility = smaller positions
# Low volatility = larger positions
adjusted_size = calculate_volatility_adjusted_size(base_size, current_vol)
```

## Multi-Weather API Ensemble

Combine weather data from multiple sources for more accurate forecasts:
- **Tomorrow.io** (40% weight) - Primary source
- **OpenWeatherMap** (30% weight) - Secondary source  
- **Weather.gov** (30% weight) - Official US data

**Benefits:**
- Reduces single-source errors
- Confidence scoring on all predictions
- Parallel API calls for speed

## Correlation-Based Diversification

Avoid overexposure to correlated weather events:
- Detects similar markets (e.g., "NYC Rain" + "Northeast Rain")
- Maximum 2 correlated positions
- Better risk distribution

---

## Running the Bot

### Paper Trading (Recommended First)
```bash
python -m src.main --paper --config config.yaml
```

### Live Trading
```bash
python -m src.main --config config.yaml
```

### Docker
```bash
docker-compose up -d
```

## Architecture

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

## Risk Parameters

| Parameter | Value | Purpose |
|-----------|-------|---------|
| Initial Capital | $50 USD | Starting bankroll |
| Max Trade Size | $2.50/event | Position limit |
| Profit Target | 100% | Auto-exit trigger |
| Daily Loss Limit | $5.00 | Stop trading trigger |
| Max Open Positions | 5 | Diversification |

## Key Features Explained

### Fair Price Calculation
Uses Normal Distribution CDF with σ=3.5°F:
```
Fair Price = CDF((forecasted_temp - expected_temp) / 3.5)
```

### Alpha Detection
Alpha = (Fair Price - Market Price) / Market Price
- Alpha > 20%: BUY signal, increase position size
- Alpha < -20%: SELL signal
- Otherwise: HOLD

### Liquidity Farming
When YES price < $0.10, place both BUY and SELL orders:
- BUY: slightly below market price
- SELL: at top of max spread (2%)
- Earns daily USDC Liquidity Rewards

### Auto-Exit
Exits positions when value doubles:
- Entry: $0.10
- Current: $0.20
- Action: SELL immediately

## Testing Protocol

1. **Paper Trading**: Run for 48 hours with no real capital
2. **Small Capital Test**: Start with $10, trade for 24 hours
3. **Gradual Scale**: Increase to full $50 after successful tests

## Monitoring

### View Logs
```bash
tail -f logs/bot.log
```

### Check Status
Access Supabase dashboard to view:
- `polymarket_audit_log`: All trading decisions
- `polymarket_rewards`: Liquidity rewards earned

## Troubleshooting

### Bot Won't Start
1. Check environment variables
2. Verify AWS Secrets Manager access
3. Check Polymarket API connectivity

### No Markets Found
1. Verify market scanning filters
2. Check category names
3. Ensure Liquidity Rewards are active

### Orders Not Filling
1. Check price is competitive
2. Verify Post-Only is set
3. Check order expiration

## Disclaimer

⚠️ **IMPORTANT**: This bot trades with real capital. Losses may exceed initial investment. Always test in paper mode first and never trade more than you can afford to lose.

## License

MIT License

