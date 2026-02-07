#!/usr/bin/env python3
"""
Performance Analytics Script
=============================
Queries Supabase to calculate detailed win rate and P&L metrics.

Usage:
    python analyze_performance.py [days]

Examples:
    python analyze_performance.py        # Last 7 days
    python analyze_performance.py 1      # Today only
    python analyze_performance.py 30     # Last 30 days
"""

import os
import sys
from datetime import datetime, timedelta
from supabase import create_client, Client
from collections import defaultdict


def get_supabase_client() -> Client:
    """Create Supabase client from environment variables"""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")

    if not url or not key:
        print("❌ Error: SUPABASE_URL and SUPABASE_KEY environment variables required")
        print("\nSet them with:")
        print("  export SUPABASE_URL='https://your-project.supabase.co'")
        print("  export SUPABASE_KEY='your-service-key'")
        sys.exit(1)

    return create_client(url, key)


def analyze_performance(days: int = 7):
    """
    Analyze trading performance from Supabase logs.

    Args:
        days: Number of days to analyze
    """
    client = get_supabase_client()

    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    print("=" * 80)
    print(f"POLYMARKET BOT PERFORMANCE ANALYSIS")
    print("=" * 80)
    print(f"Period: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')} ({days} days)")
    print("")

    # Query all audit logs
    try:
        response = (
            client
            .table("polymarket_audit_log")
            .select("*")
            .gte("created_at", start_date.isoformat())
            .lte("created_at", end_date.isoformat())
            .order("created_at", desc=False)
            .execute()
        )

        logs = response.data

        if not logs:
            print("⚠️  No data found for this period")
            print("\nPossible reasons:")
            print("  - Bot hasn't run yet")
            print("  - Date range is too narrow")
            print("  - Supabase connection issue")
            return

        print(f"✅ Found {len(logs)} audit log entries")
        print("")

        # Analyze by event type
        trades = [l for l in logs if l.get("event_type") == "TRADE"]
        exits = [l for l in logs if l.get("event_type") == "EXIT"]
        scans = [l for l in logs if l.get("event_type") == "SCAN"]
        rewards = [l for l in logs if l.get("event_type") == "REWARD"]
        errors = [l for l in logs if l.get("event_type") == "ERROR"]

        # === SCANNING ACTIVITY ===
        print("📡 SCANNING ACTIVITY")
        print("-" * 80)
        print(f"Total Scans: {len(scans)}")
        if scans:
            avg_markets = sum(float(s.get("profit_loss", 0)) for s in scans) / len(scans)
            print(f"Avg Markets Found per Scan: {avg_markets:.1f}")
        print("")

        # === TRADING ACTIVITY ===
        print("💼 TRADING ACTIVITY")
        print("-" * 80)
        print(f"Total Trades Executed: {len(trades)}")

        if trades:
            buy_trades = [t for t in trades if t.get("action") == "BUY"]
            sell_trades = [t for t in trades if t.get("action") == "SELL"]
            farm_trades = [t for t in trades if t.get("action") == "FARM"]

            print(f"  BUY orders: {len(buy_trades)}")
            print(f"  SELL orders: {len(sell_trades)}")
            print(f"  FARM orders: {len(farm_trades)}")

            # Total volume
            total_volume = sum(
                float(t.get("trade_size", 0)) for t in trades
            )
            print(f"Total Volume: ${total_volume:.2f}")

            # Alpha distribution
            alphas = [float(t.get("alpha", 0)) for t in trades if t.get("alpha")]
            if alphas:
                avg_alpha = sum(alphas) / len(alphas)
                max_alpha = max(alphas)
                min_alpha = min(alphas)
                print(f"Alpha Range: {min_alpha:.1%} to {max_alpha:.1%} (avg: {avg_alpha:.1%})")
        print("")

        # === EXIT ANALYSIS (WIN RATE) ===
        print("🎯 EXIT ANALYSIS (WIN RATE)")
        print("-" * 80)

        if exits:
            winning_exits = [e for e in exits if float(e.get("profit_loss", 0)) > 0]
            losing_exits = [e for e in exits if float(e.get("profit_loss", 0)) < 0]
            breakeven_exits = [e for e in exits if float(e.get("profit_loss", 0)) == 0]

            total_exits = len(exits)
            win_rate = (len(winning_exits) / total_exits * 100) if total_exits > 0 else 0

            print(f"Total Exits: {total_exits}")
            print(f"  ✅ Winning: {len(winning_exits)} ({len(winning_exits)/total_exits*100:.1f}%)")
            print(f"  ❌ Losing: {len(losing_exits)} ({len(losing_exits)/total_exits*100:.1f}%)")
            print(f"  ⚪ Breakeven: {len(breakeven_exits)}")
            print("")
            print(f"🎯 WIN RATE: {win_rate:.1f}%")

            # P&L analysis
            total_pnl = sum(float(e.get("profit_loss", 0)) for e in exits)
            avg_win = sum(float(e.get("profit_loss", 0)) for e in winning_exits) / len(winning_exits) if winning_exits else 0
            avg_loss = sum(float(e.get("profit_loss", 0)) for e in losing_exits) / len(losing_exits) if losing_exits else 0

            print("")
            print(f"💰 REALIZED P&L: ${total_pnl:+.2f}")
            print(f"  Avg Win: ${avg_win:+.2f}")
            print(f"  Avg Loss: ${avg_loss:+.2f}")

            if avg_loss != 0:
                profit_factor = abs(avg_win / avg_loss)
                print(f"  Profit Factor: {profit_factor:.2f}x")

            # Best and worst trades
            if winning_exits:
                best_trade = max(winning_exits, key=lambda e: float(e.get("profit_loss", 0)))
                print(f"  Best Trade: ${float(best_trade.get('profit_loss', 0)):+.2f}")

            if losing_exits:
                worst_trade = min(losing_exits, key=lambda e: float(e.get("profit_loss", 0)))
                print(f"  Worst Trade: ${float(worst_trade.get('profit_loss', 0)):+.2f}")
        else:
            print("No exits yet - positions still open or no trades executed")

        print("")

        # === REWARDS ===
        print("🎁 LIQUIDITY REWARDS")
        print("-" * 80)

        if rewards:
            total_rewards = sum(float(r.get("reward_earned", 0)) for r in rewards)
            print(f"Total Rewards Earned: ${total_rewards:.2f}")
            print(f"Reward Events: {len(rewards)}")
        else:
            print("No rewards earned yet")

        print("")

        # === ERRORS ===
        if errors:
            print("⚠️  ERRORS")
            print("-" * 80)
            print(f"Total Errors: {len(errors)}")

            error_types = defaultdict(int)
            for e in errors:
                error_types[e.get("action", "Unknown")] += 1

            for error_type, count in error_types.items():
                print(f"  {error_type}: {count}")

            print("")

        # === DAILY BREAKDOWN ===
        print("📅 DAILY BREAKDOWN")
        print("-" * 80)

        daily_stats = defaultdict(lambda: {
            "trades": 0,
            "exits": 0,
            "pnl": 0.0,
            "rewards": 0.0,
            "wins": 0,
            "losses": 0
        })

        for trade in trades:
            date = datetime.fromisoformat(trade["created_at"]).strftime("%Y-%m-%d")
            daily_stats[date]["trades"] += 1

        for exit_log in exits:
            date = datetime.fromisoformat(exit_log["created_at"]).strftime("%Y-%m-%d")
            daily_stats[date]["exits"] += 1
            pnl = float(exit_log.get("profit_loss", 0))
            daily_stats[date]["pnl"] += pnl

            if pnl > 0:
                daily_stats[date]["wins"] += 1
            elif pnl < 0:
                daily_stats[date]["losses"] += 1

        for reward in rewards:
            date = datetime.fromisoformat(reward["created_at"]).strftime("%Y-%m-%d")
            daily_stats[date]["rewards"] += float(reward.get("reward_earned", 0))

        for date in sorted(daily_stats.keys()):
            stats = daily_stats[date]
            total = stats["pnl"] + stats["rewards"]
            emoji = "🟢" if total >= 0 else "🔴"

            win_rate_daily = (stats["wins"] / stats["exits"] * 100) if stats["exits"] > 0 else 0

            print(
                f"{emoji} {date} | "
                f"Trades: {stats['trades']:2d} | "
                f"Exits: {stats['exits']:2d} | "
                f"Win%: {win_rate_daily:4.1f}% | "
                f"P&L: ${total:+6.2f}"
            )

        # === SUMMARY ===
        print("")
        print("=" * 80)
        print("📊 OVERALL SUMMARY")
        print("=" * 80)

        if exits:
            total_realized = sum(float(e.get("profit_loss", 0)) for e in exits)
            total_rewards = sum(float(r.get("reward_earned", 0)) for r in rewards)
            grand_total = total_realized + total_rewards

            print(f"Realized P&L: ${total_realized:+.2f}")
            print(f"Rewards: ${total_rewards:+.2f}")
            print(f"TOTAL: ${grand_total:+.2f}")
            print("")

            if total_exits > 0:
                print(f"Win Rate: {win_rate:.1f}%")
                print(f"Total Exits: {total_exits}")
                print(f"Avg P&L per Exit: ${total_realized/total_exits:+.2f}")

        # === RECOMMENDATION ===
        print("")
        print("=" * 80)
        print("🚦 GO-LIVE RECOMMENDATION")
        print("=" * 80)

        if not exits:
            print("❌ NOT READY - No completed trades yet")
            print("   Wait for at least 10-20 exits to evaluate performance")
        elif len(exits) < 10:
            print(f"⚠️  TOO EARLY - Only {len(exits)} exits (recommend 20+)")
            print("   Continue paper trading to build a larger sample size")
        elif win_rate < 50:
            print(f"❌ NOT READY - Win rate too low ({win_rate:.1f}%)")
            print("   Need at least 60% win rate before going live")
        elif total_pnl < 0:
            print(f"❌ NOT READY - Negative P&L (${total_pnl:+.2f})")
            print("   Strategy is losing money in paper trading")
        elif win_rate >= 60 and total_pnl > 0 and len(exits) >= 20:
            print(f"✅ READY FOR LIVE TRADING")
            print(f"   Win rate: {win_rate:.1f}% (target: >60%)")
            print(f"   P&L: ${total_pnl:+.2f} (positive)")
            print(f"   Sample size: {len(exits)} exits (good)")
            print("")
            print("   ⚠️  Start with SMALL capital ($10-20) and monitor closely!")
        else:
            print(f"⚠️  MARGINAL - Proceeding with caution")
            print(f"   Win rate: {win_rate:.1f}%")
            print(f"   P&L: ${total_pnl:+.2f}")
            print(f"   Consider running longer for confidence")

        print("=" * 80)

    except Exception as e:
        print(f"❌ Error querying Supabase: {e}")
        print("\nTroubleshooting:")
        print("  1. Check SUPABASE_URL and SUPABASE_KEY are correct")
        print("  2. Verify table 'polymarket_audit_log' exists")
        print("  3. Check network connection")
        sys.exit(1)


if __name__ == "__main__":
    days = 7  # Default: last 7 days

    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except ValueError:
            print("Usage: python analyze_performance.py [days]")
            sys.exit(1)

    analyze_performance(days)
