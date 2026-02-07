# Polymarket Bot - Complete Deployment Guide
## From Account Setup to Making Money (On $30 Budget!)

---

## 📋 Step 1: Account Setup

### 1.1 Polymarket Account (Required - Trading)
```
1. Go to https://polymarket.com
2. Click "Sign Up" or connect wallet (MetaMask recommended)
3. Complete KYC verification (required for US users on Polygon)
4. Deposit USDC to Polygon network wallet
   - Current Budget: $30 ✅
```

**Important**: Enable API access in settings to get API keys.

### 1.2 Tomorrow.io Account (Required - Weather Data)
```
1. Go to https://www.tomorrow.io
2. Sign up for free account
3. Go to API Keys section
4. Generate new API key
5. Free tier: 500 calls/day (sufficient for this bot)
```

### 1.3 AWS Account (Required - Deployment)
```
1. Go to https://aws.amazon.com
2. Sign up for free tier (12 months free t3.micro)
3. Set up billing alerts ($10-15/month expected)
4. Create IAM user with SecretsManager access
```

### 1.4 Supabase Account (Required - Logging)
```
1. Go to https://supabase.com
2. Create free account
3. Create new project
4. Go to Settings > API to get:
   - Project URL
   - service_role_key (for writing data)
```

### 1.5 OpenWeatherMap (Optional - Enhanced Weather)
```
1. Go to https://openweathermap.org
2. Sign up for free API key
3. Free tier: 60 calls/minute (plenty)
```

---

## 💰 Step 2: Fund Your Accounts

### Polymarket (Main Trading Account)
```
1. You already deposited $30 ✅
2. Max trade size: $1.50 per event (5% of capital)
3. Daily loss limit: $2.00 max
```

### AWS (For EC2 + Secrets)
```
1. Add payment method to AWS account
2. Expected cost: $10-15/month for t3.micro
3. Set up billing alert at $20
```

---

## 🔐 Step 3: Configure Secrets

### Option A: AWS Secrets Manager (Recommended)

1. **Go to AWS Console > Secrets Manager**
2. **Create Secret**
3. **Select "Other type of secret"**
4. **Add these exact key-value pairs**:

| Key | Value |
|-----|-------|
| `polygon_private_key` | `0x` + your 64-character hex private key |
| `polymarket_api_key` | Your Polymarket API key |
| `polymarket_api_secret` | Your Polymarket API secret |
| `tomorrow_io_api_key` | Your Tomorrow.io API key |
| `supabase_url` | `https://your-project.supabase.co` |
| `supabase_service_key` | Your service_role key |

**Example** (replace with YOUR actual values):
```json
{
  "polygon_private_key": "0xabc123...",
  "polymarket_api_key": "pk_live_abc123...",
  "polymarket_api_secret": "sk_live_abc123...",
  "tomorrow_io_api_key": "YOUR_TOMORROW_IO_KEY",
  "supabase_url": "https://xyz123.supabase.co",
  "supabase_service_key": "eyJhbGciOiJIUzI1NiIsInR..."
}
```

5. **Name**: `polymarket-bot/production`
6. **Store secret**

### Option B: Environment Variables (Easier for beginners)

Create `.env` file in the project root:
```bash
# Copy this template and fill in your values
POLYGON_PRIVATE_KEY="0xYOUR_PRIVATE_KEY_HERE"
POLYMARKET_API_KEY="pk_live_..."
POLYMARKET_API_SECRET="sk_live_..."
TOMORROW_IO_API_KEY="YOUR_TOMORROW_IO_KEY"
SUPABASE_URL="https://your-project.supabase.co"
SUPABASE_SERVICE_KEY="eyJhbGci..."
```

**To load environment variables:**
```bash
# On Linux/Mac
source .env

# Or run bot with env vars inline
POLYMARKET_API_KEY="..." python -m src.main --paper
```

### ⚠️ How to Get Your Keys

**Polygon Private Key:**
1. Open MetaMask
2. Click 3 dots > Account Details > Export Private Key
3. Copy the 64-character hex string
4. Add `0x` prefix

**Polymarket API Key:**
1. Go to polymarket.com/settings/api
2. Create new API key
3. Copy both API key and Secret

**Tomorrow.io API Key:**
1. Go to tomorrow.io/dashboard/developers/apikeys
2. Copy your API key

---

## 🗄️ Step 4: Setup Supabase Tables

1. **Go to Supabase > SQL Editor**
2. **Run this SQL**:

```sql
-- Create audit log table
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

-- Create rewards table
CREATE TABLE polymarket_rewards (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at DATE DEFAULT CURRENT_DATE,
    market_id VARCHAR(100),
    usdc_earned DECIMAL(10, 4),
    order_ids TEXT[],
    order_type VARCHAR(20)
);

-- Enable RLS (optional but recommended)
ALTER TABLE polymarket_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE polymarket_rewards ENABLE ROW LEVEL SECURITY;
```

---

## ☁️ Step 5: Deploy to AWS EC2

### 5.1 Launch Instance
```
1. Go to AWS Console > EC2
2. Click "Launch Instance"
3. Name: polymarket-bot
4. AMI: Ubuntu 22.04 LTS (free tier eligible)
5. Instance type: t3.micro (free tier eligible)
6. Key pair: Create new (save .pem file)
7. Security Group:
   - Inbound: SSH (22) from your IP
   - Inbound: HTTP (80) - restrict to your IP
   - Outbound: All traffic (443)
8. Launch
```

### 5.2 Connect to Instance
```bash
# Downloaded .pem file location
chmod 400 your-key.pem
ssh -i your-key.pem ubuntu@YOUR_EC2_IP
```

### 5.3 Install Docker on EC2
```bash
# Update
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

### 5.4 Deploy Bot
```bash
# Clone or upload your project
git clone <your-repo-url>
cd polymarket-weather-bot

# Create logs directory
mkdir logs

# Configure environment
# Edit config.yaml with your settings

# Build and run with Docker
docker-compose up -d --build

# Check logs
docker-compose logs -f weather-bot
```

### 5.5 Setup systemd (Auto-restart)
```bash
sudo nano /etc/systemd/system/polymarket-bot.service
```

```ini
[Unit]
Description=Polymarket Weather Sniping Bot
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/ubuntu/polymarket-weather-bot
ExecStart=/usr/local/bin/docker-compose up -d
ExecStop=/usr/local/bin/docker-compose down
TimeoutStartSec=0
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable polymarket-bot
sudo systemctl start polymarket-bot
sudo systemctl status polymarket-bot
```

---

## 🏃 Step 6: Run in Paper Mode First

**⚠️ CRITICAL: Run paper mode for 48-72 hours before real money**

```bash
# Edit config.yaml
development:
  paper_trading: true
  dry_run: false

# Or run with flag
python -m src.main --paper --config config.yaml
```

### Monitor During Paper Mode:
```
1. Check logs: tail -f logs/bot.log
2. Check Supabase: View audit_log table
3. Verify:
   - Markets being scanned
   - Prices being fetched correctly
   - No API errors
   - Calculations look reasonable
```

---

## 💵 Step 7: Go Live

Once paper mode is stable for 48+ hours:

```bash
# Edit config.yaml
development:
  paper_trading: false
  dry_run: false

# Restart bot
docker-compose restart weather-bot

# Monitor closely for first 24 hours
docker-compose logs -f weather-bot
```

---

## 📊 Step 8: Monitoring & Making Money

### 8.1 Check Profits
```bash
# View recent trades
docker-compose exec weather-bot python -c "
from src.logger import create_audit_logger
from src.config import load_config
config = load_config('config.yaml')
logger = create_audit_logger(config)
print('Daily Summary:', asyncio.run(logger.get_daily_summary()))
"
```

### 8.2 Withdraw Profits
```
1. Go to Polymarket
2. Check your positions
3. For USDC on Polygon:
   - Withdraw to MetaMask wallet
   - Bridge to mainnet if needed
   - Sell on exchange
   
4. For Liquidity Rewards:
   - Check rewards tab on Polymarket
   - Withdraw when >$5 (min withdrawal)
```

### 8.3 Key Metrics to Track
| Metric | Target | Action |
|--------|--------|--------|
| Win Rate | >55% | Review losing trades |
| Avg Profit | >15% | Adjust alpha threshold |
| Daily Rewards | >$0.50 | Check farming efficiency |
| Max Drawdown | <20% | Reduce position size |

---

## 🛠️ Troubleshooting

### Bot Won't Start
```bash
# Check logs
docker-compose logs weather-bot

# Common issues:
# - Invalid API keys
# - Insufficient capital
# - Network connectivity
```

### No Markets Found
```bash
# Check Polymarket API status
# Verify market categories
# Ensure Liquidity Rewards are active
```

### Orders Not Filling
```bash
# Check price competitiveness
# Verify Post-Only is enabled
# Check order expiration
```

---

## 📈 Expected Income Streams

### 1. Alpha Trading (Main)
- Target: 15-30% monthly ROI
- Based on weather mispricings

### 2. Liquidity Farming (Secondary)
- Target: $0.50-2.00 daily per market
- Earn USDC rewards for providing liquidity

### 3. Maker Rebates
- ~0.1% rebate on volume
- Small but consistent

### Combined Monthly Target: $5-20 (on $30 capital)

---

## ⚠️ Risk Management Checklist

- [ ] Start with $30 (you already have this!)
- [ ] Run paper mode 24+ hours
- [ ] Set daily loss limit ($2 max)
- [ ] Max position size: $1.50 per trade
- [ ] Diversify across markets (max 3)
- [ ] Monitor bot daily
- [ ] Withdraw profits weekly
- [ ] Never deposit more than you can lose

---

## 📞 Support

- Check logs: `docker-compose logs weather-bot`
- Check Supabase audit logs
- Review ENHANCEMENTS.md for improvements
- Common issues documented in README.md

---

*Good luck! Start small, grow gradually.* 🚀

