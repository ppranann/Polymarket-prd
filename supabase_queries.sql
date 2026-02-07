-- Supabase Dashboard Queries for Performance Analysis
-- =====================================================
-- Copy/paste these into your Supabase SQL Editor

-- ========================================
-- 1. WIN RATE (Last 7 Days)
-- ========================================
SELECT
    COUNT(*) FILTER (WHERE profit_loss > 0) as winning_trades,
    COUNT(*) FILTER (WHERE profit_loss < 0) as losing_trades,
    COUNT(*) FILTER (WHERE profit_loss = 0) as breakeven_trades,
    COUNT(*) as total_exits,
    ROUND(
        COUNT(*) FILTER (WHERE profit_loss > 0)::numeric /
        NULLIF(COUNT(*), 0)::numeric * 100,
        2
    ) as win_rate_pct
FROM polymarket_audit_log
WHERE event_type = 'EXIT'
AND created_at >= NOW() - INTERVAL '7 days';


-- ========================================
-- 2. TOTAL P&L (Last 7 Days)
-- ========================================
SELECT
    ROUND(SUM(profit_loss)::numeric, 2) as total_pnl,
    ROUND(AVG(profit_loss) FILTER (WHERE profit_loss > 0)::numeric, 2) as avg_win,
    ROUND(AVG(profit_loss) FILTER (WHERE profit_loss < 0)::numeric, 2) as avg_loss,
    ROUND(MAX(profit_loss)::numeric, 2) as best_trade,
    ROUND(MIN(profit_loss)::numeric, 2) as worst_trade,
    COUNT(*) as total_trades
FROM polymarket_audit_log
WHERE event_type = 'EXIT'
AND created_at >= NOW() - INTERVAL '7 days';


-- ========================================
-- 3. DAILY BREAKDOWN (Last 7 Days)
-- ========================================
SELECT
    DATE(created_at) as trade_date,
    COUNT(*) FILTER (WHERE event_type = 'TRADE') as trades_executed,
    COUNT(*) FILTER (WHERE event_type = 'EXIT') as positions_closed,
    COUNT(*) FILTER (WHERE event_type = 'EXIT' AND profit_loss > 0) as winning_exits,
    COUNT(*) FILTER (WHERE event_type = 'EXIT' AND profit_loss < 0) as losing_exits,
    ROUND(
        COUNT(*) FILTER (WHERE event_type = 'EXIT' AND profit_loss > 0)::numeric /
        NULLIF(COUNT(*) FILTER (WHERE event_type = 'EXIT'), 0)::numeric * 100,
        1
    ) as daily_win_rate,
    ROUND(SUM(profit_loss) FILTER (WHERE event_type = 'EXIT')::numeric, 2) as daily_pnl,
    ROUND(SUM(reward_earned)::numeric, 2) as daily_rewards
FROM polymarket_audit_log
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY DATE(created_at)
ORDER BY trade_date DESC;


-- ========================================
-- 4. ALL EXITS (Last 7 Days, Most Recent First)
-- ========================================
SELECT
    created_at,
    market_id,
    ROUND(profit_loss::numeric, 2) as pnl,
    decision_reason,
    CASE
        WHEN profit_loss > 0 THEN '✅ WIN'
        WHEN profit_loss < 0 THEN '❌ LOSS'
        ELSE '⚪ BREAKEVEN'
    END as result
FROM polymarket_audit_log
WHERE event_type = 'EXIT'
AND created_at >= NOW() - INTERVAL '7 days'
ORDER BY created_at DESC
LIMIT 50;


-- ========================================
-- 5. ALPHA DISTRIBUTION (Are signals realistic?)
-- ========================================
SELECT
    ROUND(alpha::numeric, 2) as alpha_value,
    action,
    COUNT(*) as count
FROM polymarket_audit_log
WHERE event_type = 'TRADE'
AND created_at >= NOW() - INTERVAL '7 days'
AND alpha IS NOT NULL
GROUP BY ROUND(alpha::numeric, 2), action
ORDER BY alpha_value DESC
LIMIT 20;


-- ========================================
-- 6. REWARDS EARNED (Last 7 Days)
-- ========================================
SELECT
    DATE(created_at) as date,
    COUNT(*) as reward_events,
    ROUND(SUM(reward_earned)::numeric, 2) as total_rewards
FROM polymarket_audit_log
WHERE event_type = 'REWARD'
AND created_at >= NOW() - INTERVAL '7 days'
GROUP BY DATE(created_at)
ORDER BY date DESC;


-- ========================================
-- 7. QUICK SUMMARY (All Metrics in One Query)
-- ========================================
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
    ROUND(AVG(profit_loss) FILTER (WHERE profit_loss > 0)::numeric, 2) as avg_win,
    ROUND(AVG(profit_loss) FILTER (WHERE profit_loss < 0)::numeric, 2) as avg_loss,

    -- Rewards
    (SELECT ROUND(total_rewards::numeric, 2) FROM rewards) as total_rewards,

    -- Total
    ROUND(
        SUM(profit_loss)::numeric +
        COALESCE((SELECT total_rewards FROM rewards), 0),
        2
    ) as total_profit,

    -- Sample Size
    COUNT(*) as total_exits
FROM exits;


-- ========================================
-- 8. ERRORS (Check for Issues)
-- ========================================
SELECT
    DATE(created_at) as date,
    action as error_type,
    COUNT(*) as count,
    STRING_AGG(DISTINCT SUBSTRING(error_message, 1, 100), ' | ') as sample_errors
FROM polymarket_audit_log
WHERE event_type = 'ERROR'
AND created_at >= NOW() - INTERVAL '7 days'
GROUP BY DATE(created_at), action
ORDER BY date DESC;


-- ========================================
-- 9. GO-LIVE READINESS CHECK
-- ========================================
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
        WHEN total_exits < 20 THEN '⚠️ MARGINAL - Need 20+ exits'
        WHEN win_rate < 50 THEN '❌ NOT READY - Win rate too low'
        WHEN win_rate < 60 AND total_pnl < 0 THEN '❌ NOT READY - Losing strategy'
        WHEN win_rate >= 60 AND total_pnl > 0 THEN '✅ READY - Start with small capital'
        ELSE '⚠️ PROCEED WITH CAUTION'
    END as recommendation
FROM metrics;


-- ========================================
-- CHANGE TIME RANGE
-- ========================================
-- To see different time periods, replace:
--   NOW() - INTERVAL '7 days'
-- With:
--   NOW() - INTERVAL '1 day'   -- Today only
--   NOW() - INTERVAL '3 days'  -- Last 3 days
--   NOW() - INTERVAL '30 days' -- Last 30 days
