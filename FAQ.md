# Frequently Asked Questions

## 1. Will the bot stop if I close VSCode?

**NO** - The bot runs on EC2, not your computer.

- **VSCode** = Just viewing/editing code on your laptop
- **Bot** = Running in Docker on AWS EC2 (cloud server)
- **Closing VSCode** = No effect on the bot

### How to check if bot is running:
```bash
ssh -i keys/Polymarket-ec2-test.pem ubuntu@<YOUR-IP>
docker-compose ps
```

### How to stop the bot:
```bash
docker-compose down
```

### How to restart the bot:
```bash
docker-compose up -d
```

**Bottom line**: You can close your laptop, the bot keeps running 24/7 on EC2.

---

## 2. When does the bot sell positions?

The bot has **2 automatic exit conditions**:

### Exit #1: Trailing Stop (Capture Profits) 💰
- **Condition**: Position is up 20%+ AND peaked at 30%+
- **Trigger**: Drops 15% from peak
- **Purpose**: Let winners run, but lock in gains

**Example:**
```
Entry price: $0.10
Peak price: $0.13 (+30%)
Current: $0.115 (+15%)
Drop from peak: 30% - 15% = 15% → SELL!
```

### Exit #2: Risk Exit (Cut Losses) 🛑
- **Condition**: Entered with high alpha (>30%) but position down 15%+
- **Trigger**: P&L < -15%
- **Purpose**: Admit when wrong, cut losses

**Example:**
```
Entry alpha: 50% (high confidence)
Current P&L: -23.8%
→ SELL! Something went wrong
```

### What ISN'T automatic:
- **Expiry**: You hold until market resolves (real temperature measured)
- **Manual exits**: You can manually close positions

### Why "Unrealized P&L"?
**Unrealized** = You haven't sold yet (paper profit)
**Realized** = You sold and locked in the profit

The bot holds positions until:
1. Trailing stop triggers (up 20%+, drops 15% from peak)
2. Risk exit triggers (high confidence entry, now down 15%+)
3. Market expires and settles

---

## 3. How do I track profit without reading SQL?

**Forget Supabase** - Use Docker logs instead!

### Quick P&L Check (1 second):
```bash
docker-compose logs -f | grep "P&L:"

# You'll see:
💰 P&L: +$5.23 (Unrealized: +$3.45, Daily: +$1.78) | Positions: 12 | Capital: $28.50
```

**What this means:**
- **Total P&L**: +$5.23 (your overall profit)
- **Unrealized**: +$3.45 (profit on open positions, not sold yet)
- **Daily**: +$1.78 (profit from positions closed today)
- **Positions**: 12 (trades currently open)
- **Capital**: $28.50 (money available for new trades)

### Full Performance Report (every hour):
```bash
docker-compose logs | grep -A 25 "PERFORMANCE SUMMARY"
```

You'll see:
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
... and 10 more positions

Today's Stats:
----------------------------------------------------------------------
✅ Winning Trades: 8
❌ Losing Trades: 2
🎯 Win Rate: 80.0%

🚀 TOTAL P&L: +$5.23
======================================================================
```

**Supabase is optional** - only for debugging. The real profit tracking is in Docker logs.

---

## 4. How does position sizing scale with capital growth?

**FIXED** ✅ - Position sizes now scale automatically!

### Before (BROKEN):
```
Capital: $30  → Position: $1.50 (fixed)
Capital: $100 → Position: $1.50 (still fixed! 😢)
Capital: $1000 → Position: $1.50 (not growing!)
```

### After (WORKING):
```
Capital: $30   → Position: $3.00 (10% of $30)
Capital: $100  → Position: $10.00 (10% of $100)
Capital: $1000 → Position: $100.00 (10% of $1000)
```

### How it works:
1. **Base size** = 10% of current capital
2. **Alpha multiplier** = Up to 3x for high-conviction trades
3. **Hard cap** = max_trade_size ($3.00) prevents over-leveraging

**Example progression:**
```
Day 1: Capital $30 → Position $3.00
Day 7: Capital $60 → Position $6.00 (doubled!)
Day 30: Capital $300 → Position $30.00 (but capped at $30 max)
```

### Configuration:
```yaml
risk:
  max_trade_size: 3.00              # Hard cap (safety)
  max_trade_size_pct: 0.10          # 10% of capital (scales)
```

**Benefits:**
- 📈 Automatically scales with profits
- 🔒 Still respects hard cap for safety
- 💰 More efficient use of capital
- 🚀 Better compound growth

**Example with $100 starting capital:**
```
Week 1: $100 capital → $10 positions
Week 2: $120 capital → $12 positions (+20%)
Week 4: $150 capital → $15 positions (+50%)
```

Your positions grow as you profit! 🎉

---

## Quick Reference

| Question | Answer |
|----------|--------|
| Does bot stop if I close VSCode? | **NO** - runs on EC2 |
| How do I see profit? | `docker-compose logs -f \| grep "P&L:"` |
| When does bot sell? | Trailing stop (drops 15% from peak) or Risk exit (-15% on high confidence) |
| Do positions scale with capital? | **YES** - 10% of capital per trade |

---

## Common Scenarios

### Scenario 1: "I made $50 profit, why are positions still $3?"
You're probably in paper mode. Real profit doesn't accumulate in paper mode.

Check: `config.yaml` → `paper_trading: false` to go live.

### Scenario 2: "Why didn't my +50% position sell?"
Trailing stop requires:
1. Position up 20%+ ✓
2. Peaked at 30%+ ✓
3. Dropped 15% from peak ✗ (still rising!)

**It's letting your winner run!** It will sell when it drops 15% from peak.

### Scenario 3: "Capital is $100 but positions are only $10?"
Perfect! That's 10% position sizing working correctly.

With 10 positions, you'd have $100 total exposure = all capital deployed.

---

## Advanced Tips

### Adjust position sizing:
```yaml
# More aggressive (20% per trade)
max_trade_size_pct: 0.20

# More conservative (5% per trade)
max_trade_size_pct: 0.05
```

### Adjust exit conditions:
```yaml
# In auto_exit.py, change:
if position.pnl_percentage >= 20 and position.peak_pnl_percentage >= 30:
    drop_from_peak = position.peak_pnl_percentage - position.pnl_percentage
    if drop_from_peak >= 15:  # ← Change this number
```

### Monitor real-time:
```bash
# Live profit tracking
watch -n 1 'docker-compose logs --tail 5 | grep "P&L:"'
```

---

**Still have questions?** See:
- [PAPER_TRADING.md](PAPER_TRADING.md) - How to test before going live
- [MAINTENANCE.md](MAINTENANCE.md) - Operational tips
- [CRITICAL_BUGS_FIXED.md](CRITICAL_BUGS_FIXED.md) - Recent fixes
