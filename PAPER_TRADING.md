# Paper Trading Mode Guide

## What is Paper Trading?

Paper trading = **simulated trading** with fake money. No real money is spent, but the bot:
- ✅ Finds real markets
- ✅ Fetches real weather data
- ✅ Calculates real alpha signals
- ✅ Places **MOCK orders** (not real trades)
- ✅ Tracks **simulated P&L**

This lets you test if the strategy is profitable before risking real money.

---

## How to Know if You're Profiting

The bot now shows your **simulated P&L** after every scan:

### Compact Summary (After Each Scan)
```
💰 P&L: +$5.23 (Unrealized: +$3.45, Daily: +$1.78) | Positions: 12 | Capital: $28.50
```

**What this means:**
- **Total P&L**: Your overall profit/loss across all positions
- **Unrealized**: Profit from positions still open (not sold yet)
- **Daily**: Profit from positions closed today
- **Positions**: Number of open trades
- **Capital**: Money available for new trades

### Full Summary (Every Hour)
```
======================================================================
PERFORMANCE SUMMARY (PAPER TRADING)
======================================================================
💰 Capital: $28.50
📊 Total Exposure: $18.00
📈 Open Positions: 12

🟢 Unrealized P&L: +$3.45
🟢 Daily Realized P&L: +$1.78

Open Positions:
----------------------------------------------------------------------
🟢 1338922... | Entry: $0.0160 | Current: $0.0210 | P&L: +$0.75 (+46.9%)
🟢 1338926... | Entry: $0.1100 | Current: $0.1250 | P&L: +$0.23 (+13.6%)
🔴 1344855... | Entry: $0.1250 | Current: $0.1150 | P&L: -$0.15 (-8.0%)
... and 9 more positions

Today's Stats:
----------------------------------------------------------------------
✅ Winning Trades: 8
❌ Losing Trades: 2
🎯 Win Rate: 80.0%
💵 Total Volume: $45.00
🎁 Rewards Earned: $0.45

🚀 TOTAL P&L: +$5.23
======================================================================
```

---

## Key Metrics Explained

### 1. **Unrealized P&L**
Profit/loss on **open positions** (not yet sold).

- 🟢 Positive = Your open positions are worth more than you paid
- 🔴 Negative = Your open positions are worth less than you paid

**Example:**
- You bought at $0.10
- Current price: $0.12
- Unrealized P&L = +$0.02 per share

### 2. **Realized P&L**
Profit/loss on **closed positions** (already sold).

- This is your "locked in" profit/loss
- Only counts trades that completed today

**Example:**
- You bought at $0.10
- You sold at $0.15
- Realized P&L = +$0.05 per share

### 3. **Total P&L**
Unrealized + Realized = Overall performance

This is your **total profit/loss** for the day.

### 4. **Win Rate**
Percentage of trades that made money.

- 80% win rate = 8 out of 10 trades were profitable
- Higher is better, but even 50%+ can be good if winners are bigger than losers

---

## When to Switch to Live Trading

**Only switch to live trading when:**

1. ✅ **Positive P&L** for at least 3-7 days
2. ✅ **Win rate > 60%** consistently
3. ✅ **No major bugs** or crashes
4. ✅ **You understand the risks**

### How to Switch to Live

1. **Change config.yaml:**
   ```yaml
   development:
     paper_trading: false  # Change true → false
   ```

2. **Deploy:**
   ```bash
   ./build.sh
   # Wait for GitHub Actions
   ./deploy.sh latest
   ```

3. **Monitor closely:**
   - Check logs every few hours
   - Watch for errors
   - Verify real trades are executing correctly

⚠️ **WARNING**: Live trading uses real money! Start small.

---

## Understanding Your Strategy's Performance

### Good Signs 🟢
- Total P&L is positive
- Win rate > 50%
- Unrealized P&L growing over time
- No major losses (largest loss < 20%)

### Warning Signs 🔴
- Total P&L negative for multiple days
- Win rate < 40%
- Large single losses (> 50% of capital)
- Frequent errors in logs

### Neutral Signs 🟡
- P&L near $0 (break-even)
- Low number of trades (not finding opportunities)
- High win rate but tiny profits (need better position sizing)

---

## Example Scenarios

### Scenario 1: Profitable Strategy
```
Day 1: +$2.34
Day 2: +$1.89
Day 3: +$3.12
Day 4: +$2.56
Win Rate: 75%
```
**Action**: Continue paper trading for a few more days, then consider going live.

### Scenario 2: Losing Strategy
```
Day 1: -$1.23
Day 2: -$2.45
Day 3: -$1.67
Day 4: -$3.01
Win Rate: 35%
```
**Action**: DON'T go live. Review config, check alpha calculations, adjust parameters.

### Scenario 3: Break-Even Strategy
```
Day 1: +$0.34
Day 2: -$0.12
Day 3: +$0.45
Day 4: -$0.23
Win Rate: 52%
```
**Action**: Need to improve. Either position sizing is too small, or alpha signals need tuning.

---

## Quick Commands

### Check Current P&L
```bash
# View live logs
docker-compose logs -f

# Look for lines like:
# 💰 P&L: +$5.23 (Unrealized: +$3.45, Daily: +$1.78)
```

### View Full Performance Summary
```bash
# Wait for hourly summary, or restart to see immediate summary:
docker-compose restart
docker-compose logs -f
```

### Check All-Time Performance
```bash
# Look for "PERFORMANCE SUMMARY" in logs
docker-compose logs | grep "TOTAL P&L"
```

---

## FAQ

**Q: I see "Mode: PAPER" but my P&L is $0. Why?**
A: The bot just started. P&L will show after trades execute and positions open.

**Q: My P&L is negative. Should I worry?**
A: Not in paper mode! This is why we test. Adjust config and try again.

**Q: How long should I paper trade before going live?**
A: At least 3-7 days of positive P&L. Longer is safer.

**Q: Can paper trading performance guarantee live trading success?**
A: No. Live trading has slippage, fees, and real market impact. But positive paper trading is a good sign.

**Q: What's a good daily profit target?**
A: For $30 starting capital, +$1-3/day (3-10%) is excellent. Don't expect 100% daily gains.

---

## Monitoring Tips

### Daily Check (30 seconds)
```bash
# SSH to EC2
ssh -i keys/Polymarket-ec2-test.pem ubuntu@<IP>

# Check recent P&L
docker-compose logs --tail 20 | grep "P&L:"
```

### Weekly Review (5 minutes)
```bash
# Get full performance summary
docker-compose logs | grep -A 20 "PERFORMANCE SUMMARY"

# Calculate average daily P&L
# Sum up daily totals and divide by number of days
```

---

## Next Steps

1. **Let it run for 1-2 days** - Watch the P&L
2. **Review performance** - Is it profitable?
3. **Adjust if needed** - Tweak config parameters
4. **Test more** - Wait for consistent positive P&L
5. **Go live** - When confident (or stay in paper mode forever!)

---

**Remember**: Paper trading is **free practice**. Use it to learn and optimize before risking real money!

See [MAINTENANCE.md](MAINTENANCE.md) for operational tips or [README.md](README.md) for strategy details.
