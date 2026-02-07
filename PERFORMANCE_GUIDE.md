# Performance Analysis Guide

## Quick Start (AWS Secrets Manager)

The easiest way to check your bot's performance using credentials from AWS:

```bash
# Analyze last 7 days (default)
./analyze.sh

# Analyze today only
./analyze.sh 1

# Analyze last 30 days
./analyze.sh 30

# Skip connection test (faster, for repeated checks)
./analyze.sh --skip-test 7
```

That's it! The script automatically:
1. 🔐 Fetches Supabase credentials from AWS Secrets Manager
2. 🧪 Tests the connection
3. 📊 Generates comprehensive performance report

---

## What You'll See

The analysis shows:

### 1. **Trading Activity**
- Total trades executed (BUY, SELL, FARM)
- Total volume traded
- Alpha distribution (how good your edges were)

### 2. **Win Rate Analysis** ⭐ MOST IMPORTANT
```
🎯 WIN RATE: 67.5%

Total Exits: 28
  ✅ Winning: 19 (67.9%)
  ❌ Losing: 9 (32.1%)
```

### 3. **Profit & Loss**
```
💰 REALIZED P&L: +$12.45
  Avg Win: +$0.85
  Avg Loss: -$0.35
  Profit Factor: 2.43x
  Best Trade: +$2.10
  Worst Trade: -$0.65
```

### 4. **Daily Breakdown**
```
🟢 2026-02-07 | Trades: 12 | Exits:  8 | Win%: 75.0% | P&L: +$3.45
🟢 2026-02-06 | Trades: 15 | Exits: 10 | Win%: 60.0% | P&L: +$2.80
🔴 2026-02-05 | Trades:  8 | Exits:  5 | Win%: 40.0% | P&L: -$1.20
```

### 5. **Go-Live Recommendation** 🚦

The script automatically tells you if you're ready:

```
✅ READY FOR LIVE TRADING
   Win rate: 67.5% (target: >60%)
   P&L: +$12.45 (positive)
   Sample size: 28 exits (good)

   ⚠️  Start with SMALL capital ($10-20) and monitor closely!
```

---

## Decision Criteria

| Metric | Requirement | Why It Matters |
|--------|-------------|----------------|
| **Win Rate** | > 60% | Need to win more than you lose |
| **Total P&L** | Positive | Strategy must be profitable |
| **Sample Size** | 20+ exits | Need enough data to be confident |
| **Profit Factor** | > 1.5x | Avg win should exceed avg loss |

---

## Troubleshooting

### "❌ Failed to fetch secret from AWS Secrets Manager"

**Fix:**
```bash
# Check AWS CLI is configured
aws sts get-caller-identity

# Verify secret exists
aws secretsmanager get-secret-value \
    --secret-id polymarket-bot/production \
    --region us-east-1
```

If the secret is in a different region, edit [analyze.sh](analyze.sh) line 8:
```bash
REGION="us-west-2"  # Change to your region
```

### "❌ Table 'polymarket_audit_log' doesn't exist"

The bot hasn't created the Supabase tables yet. Run the setup SQL from [README.md](README.md) lines 62-89.

### "⚠️ No data found in the last 7 days"

The bot either:
- Hasn't run yet
- Is not logging to Supabase
- Data is older than 7 days

**Check:**
```bash
# Is the bot running?
docker-compose ps

# Check recent logs
docker-compose logs --tail 50 | grep "AUDIT:"

# Try analyzing a longer period
./analyze.sh 30  # Last 30 days
```

### "No exits yet - positions still open"

This is normal for a new bot. Exits happen when:
- Position doubles (100% profit target)
- Daily loss limit hit
- Manual exit triggered

**What to do:** Wait for positions to mature. Check again in 12-24 hours.

---

## Manual Analysis (Without AWS)

If you prefer to set credentials manually:

```bash
# Export credentials
export SUPABASE_URL='https://your-project.supabase.co'
export SUPABASE_KEY='your-service-key'

# Run analysis directly
python3 analyze_performance.py 7
```

---

## Testing Connection Only

To verify Supabase setup without running full analysis:

```bash
# Method 1: Via AWS Secrets Manager
./analyze.sh --test-only

# Method 2: Manual
export SUPABASE_URL='...'
export SUPABASE_KEY='...'
python3 test_supabase.py
```

---

## Configuration

### Change AWS Secret Name/Region

Edit [analyze.sh](analyze.sh):
```bash
SECRET_NAME="your-secret-name"
REGION="your-region"
```

### Change Analysis Period

```bash
# Last 24 hours
./analyze.sh 1

# Last 3 days
./analyze.sh 3

# Last week (default)
./analyze.sh 7

# Last month
./analyze.sh 30

# All time (90 days)
./analyze.sh 90
```

---

## When to Go Live

**Go live when ALL of these are true:**

1. ✅ Win rate > 60% for at least 3-7 days
2. ✅ Total P&L is positive
3. ✅ At least 20 completed exits
4. ✅ Profit factor > 1.5x
5. ✅ No major errors in logs
6. ✅ You understand the risks

**Start conservatively:**
- Begin with $10-20 (not your full $30-50 capital)
- Monitor every few hours for the first 24h
- Scale up gradually if profitable

---

## Monitoring Schedule

### Daily (30 seconds)
```bash
./analyze.sh 1  # Today's performance
```

### Weekly (2 minutes)
```bash
./analyze.sh 7  # Weekly review
```

### Before Going Live
```bash
./analyze.sh 30  # Comprehensive analysis
```

---

## Next Steps

1. **Run the analysis now:**
   ```bash
   ./analyze.sh
   ```

2. **Review the results** - Is win rate > 60% and P&L positive?

3. **If not ready:**
   - Continue paper trading
   - Check for errors in logs
   - Adjust config if needed
   - Re-analyze in 24-48 hours

4. **If ready:**
   - Review [PAPER_TRADING.md](PAPER_TRADING.md)
   - Set `paper_trading: false` in [config.yaml](config.yaml)
   - Deploy with `./deploy.sh`
   - Monitor closely

---

## Questions?

- **How to interpret alpha values?** See [CRITICAL_BUGS_FIXED.md](CRITICAL_BUGS_FIXED.md)
- **How does P&L work in paper mode?** See [PAPER_TRADING.md](PAPER_TRADING.md)
- **Strategy details?** See [README.md](README.md)
- **Operational tips?** See [FAQ.md](FAQ.md)

---

**Remember:** Paper trading is free practice. Use it to build confidence before risking real money! 📊
