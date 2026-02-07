# Supabase Dashboard Guide

**Yes, you can view everything from your Supabase dashboard!** Here's how:

---

## 🖥️ Quick Start

1. **Go to your Supabase project** → https://app.supabase.com
2. **Click "SQL Editor"** in the left sidebar
3. **Copy/paste queries from [supabase_queries.sql](supabase_queries.sql)**
4. **Click "Run"** to see results

---

## 📊 Most Important Queries

### 1. Quick Summary (One Query for Everything)

Use **Query #7** from [supabase_queries.sql](supabase_queries.sql):

```sql
-- Copy/paste into Supabase SQL Editor
WITH exits AS (
    SELECT * FROM polymarket_audit_log
    WHERE event_type = 'EXIT'
    AND created_at >= NOW() - INTERVAL '7 days'
),
rewards AS (
    SELECT SUM(reward_earned) as total_rewards
    FROM polymarket_audit_log
    WHERE event_type = 'REWARD'
    AND created_at >= NOW() - INTERVAL '7 days'
)
SELECT
    -- Win Rate
    COUNT(*) FILTER (WHERE profit_loss > 0) as wins,
    COUNT(*) FILTER (WHERE profit_loss < 0) as losses,
    ROUND(
        COUNT(*) FILTER (WHERE profit_loss > 0)::numeric /
        NULLIF(COUNT(*), 0)::numeric * 100,
        1
    ) as win_rate_pct,

    -- P&L
    ROUND(SUM(profit_loss)::numeric, 2) as realized_pnl,

    -- Total
    ROUND(
        SUM(profit_loss)::numeric +
        COALESCE((SELECT total_rewards FROM rewards), 0),
        2
    ) as total_profit,

    -- Sample Size
    COUNT(*) as total_exits
FROM exits;
```

**What you'll see:**
| wins | losses | win_rate_pct | realized_pnl | total_profit | total_exits |
|------|--------|--------------|--------------|--------------|-------------|
| 19   | 9      | 67.9         | 12.45        | 13.20        | 28          |

**Read it as:**
- 19 winning trades, 9 losing trades
- **67.9% win rate** ⭐
- Made $12.45 in P&L + $0.75 in rewards = **$13.20 total**
- 28 completed exits (good sample size)

---

### 2. Go-Live Readiness Check

Use **Query #9** from [supabase_queries.sql](supabase_queries.sql):

```sql
WITH metrics AS (
    SELECT
        COUNT(*) FILTER (WHERE event_type = 'EXIT') as total_exits,
        ROUND(
            COUNT(*) FILTER (WHERE event_type = 'EXIT' AND profit_loss > 0)::numeric /
            NULLIF(COUNT(*) FILTER (WHERE event_type = 'EXIT'), 0)::numeric * 100,
            1
        ) as win_rate,
        ROUND(SUM(profit_loss) FILTER (WHERE event_type = 'EXIT')::numeric, 2) as total_pnl
    FROM polymarket_audit_log
    WHERE created_at >= NOW() - INTERVAL '7 days'
)
SELECT
    *,
    CASE
        WHEN total_exits < 10 THEN '⏳ TOO EARLY - Need more exits'
        WHEN win_rate < 50 THEN '❌ NOT READY - Win rate too low'
        WHEN win_rate >= 60 AND total_pnl > 0 THEN '✅ READY - Start with small capital'
        ELSE '⚠️ PROCEED WITH CAUTION'
    END as recommendation
FROM metrics;
```

**Tells you immediately if you're ready to go live!**

---

### 3. Daily Breakdown

Use **Query #3** to see performance by day:

```sql
SELECT
    DATE(created_at) as trade_date,
    COUNT(*) FILTER (WHERE event_type = 'EXIT') as exits,
    ROUND(
        COUNT(*) FILTER (WHERE event_type = 'EXIT' AND profit_loss > 0)::numeric /
        NULLIF(COUNT(*) FILTER (WHERE event_type = 'EXIT'), 0)::numeric * 100,
        1
    ) as win_rate,
    ROUND(SUM(profit_loss) FILTER (WHERE event_type = 'EXIT')::numeric, 2) as daily_pnl
FROM polymarket_audit_log
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY DATE(created_at)
ORDER BY trade_date DESC;
```

---

## 🆚 Dashboard vs Script Comparison

| Feature | Supabase Dashboard | `./analyze.sh` Script |
|---------|-------------------|----------------------|
| **Setup** | Just open browser | Need AWS CLI, Python |
| **Speed** | Instant | 5-10 seconds |
| **Customization** | Edit SQL freely | Fixed format |
| **Visuals** | Can create charts | Text only |
| **Automation** | Manual refresh | Can schedule |
| **Offline** | ❌ Need internet | ❌ Need internet |
| **Best for** | Quick checks, custom queries | Automated reports, CLI workflow |

**My recommendation:** Use the dashboard for quick daily checks, use the script for detailed analysis or automation.

---

## 💡 Pro Tips for Dashboard

### Save Your Queries
1. In SQL Editor, write your query
2. Click **"Save"** at the top
3. Name it (e.g., "Daily Win Rate")
4. Now you can re-run it with one click!

### Create a Chart
1. Run a query (e.g., daily breakdown)
2. Click **"Chart"** tab
3. Choose chart type (line, bar, etc.)
4. Supabase will visualize your data!

### Set Up Alerts (Advanced)
You can create a webhook that alerts you when win rate drops:
1. Create an Edge Function
2. Query the audit log
3. Send notification if win_rate < 50%

---

## 📋 Common Scenarios

### "I just want to see if I'm profitable"
Run **Query #7** (Quick Summary) → Look at `total_profit` column

### "What's my win rate?"
Run **Query #1** (Win Rate) → Look at `win_rate_pct`

### "Should I go live?"
Run **Query #9** (Readiness Check) → Read the `recommendation`

### "Show me my best/worst trades"
Run **Query #4** (All Exits) → Sorted by P&L

### "Are my alpha calculations realistic?"
Run **Query #5** (Alpha Distribution) → Should see variety, not all 100%

---

## 🔧 Change Time Period

All queries use `NOW() - INTERVAL '7 days'`. Change it to:

```sql
-- Today only
NOW() - INTERVAL '1 day'

-- Last 3 days
NOW() - INTERVAL '3 days'

-- Last month
NOW() - INTERVAL '30 days'

-- All time (90 days max recommended)
NOW() - INTERVAL '90 days'
```

---

## 🚨 What to Look For

### Good Signs ✅
| Metric | Value |
|--------|-------|
| Win Rate | > 60% |
| Total P&L | Positive |
| Exits | 20+ |
| Alpha Range | 0-80% (varied) |
| Errors | 0 or very few |

### Warning Signs ⚠️
| Metric | Value |
|--------|-------|
| Win Rate | 50-60% |
| Total P&L | Near $0 |
| Exits | 10-20 |
| Alpha | Always 100% (bug!) |

### Bad Signs ❌
| Metric | Value |
|--------|-------|
| Win Rate | < 50% |
| Total P&L | Negative |
| Exits | < 10 |
| Errors | Many |

---

## 📱 Mobile Access

Yes! Supabase dashboard works on mobile:
1. Open https://app.supabase.com on your phone
2. Navigate to SQL Editor
3. Tap saved queries
4. View results

Perfect for quick checks on the go!

---

## 🎯 Daily Monitoring Routine (2 minutes)

**Morning check:**
1. Open Supabase dashboard
2. Run saved "Quick Summary" query
3. Check: Win rate > 60%? P&L positive?
4. If yes: you're on track ✅
5. If no: review errors (Query #8)

**Weekly review:**
1. Run "Daily Breakdown" (Query #3)
2. Look for consistent profitability
3. Check for any error patterns
4. Decide: continue paper trading or go live?

---

## 🆘 Troubleshooting

### "No data returned"
- Bot hasn't run yet, or
- No trades completed, or
- Wrong time range (try 30 days)

### "Error: relation does not exist"
- Table `polymarket_audit_log` not created yet
- Run setup SQL from [README.md](README.md)

### "Permission denied"
- Using wrong API key
- Need Service Key, not anon key
- Check Settings → API

---

## 📚 All Available Queries

All queries are in [supabase_queries.sql](supabase_queries.sql):

1. **Win Rate** - Last 7 days win percentage
2. **Total P&L** - Profit/loss summary
3. **Daily Breakdown** - Performance by day
4. **All Exits** - Every closed position
5. **Alpha Distribution** - Signal quality check
6. **Rewards Earned** - Farming income
7. **Quick Summary** - Everything in one query ⭐ **START HERE**
8. **Errors** - Problem detection
9. **Go-Live Readiness** - Am I ready? ⭐ **IMPORTANT**

---

## 🎬 Next Steps

1. **Open Supabase dashboard** → https://app.supabase.com
2. **Go to SQL Editor**
3. **Copy/paste Query #7** (Quick Summary)
4. **Click Run**
5. **Check your results!**

If you have data, you'll immediately see your win rate and P&L!

---

**TL;DR:** Yes, you can absolutely use the Supabase dashboard instead of the scripts. It's faster for quick checks and more visual. The scripts are just automation for the same data.
