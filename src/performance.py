"""
Performance Reporting Module
============================
Shows P&L and performance metrics for paper trading mode.
"""

import logging
from datetime import datetime
from typing import Dict, List
from .risk import RiskManager, Position

logger = logging.getLogger(__name__)


class PerformanceReporter:
    """
    Reports trading performance metrics.

    Shows:
    - Unrealized P&L (open positions)
    - Realized P&L (closed positions)
    - Win rate
    - Best/worst positions
    """

    def __init__(self, risk_manager: RiskManager):
        """
        Initialize performance reporter.

        Args:
            risk_manager: RiskManager instance to get metrics from
        """
        self.risk = risk_manager
        self._last_report_time = datetime.now()

    def print_performance_summary(self):
        """
        Print a formatted performance summary.

        Shows:
        - Capital and exposure
        - Open positions with P&L
        - Daily stats
        - All-time stats
        """
        logger.info("=" * 70)
        logger.info("PERFORMANCE SUMMARY (PAPER TRADING)")
        logger.info("=" * 70)

        # Get risk summary
        summary = self.risk.get_risk_summary()

        # Capital
        logger.info(f"💰 Capital: ${summary['available_capital']:.2f}")
        logger.info(f"📊 Total Exposure: ${summary['total_exposure']:.2f}")
        logger.info(f"📈 Open Positions: {summary['open_positions_count']}")

        # P&L
        unrealized = summary['unrealized_pnl']
        daily = summary['daily_pnl']

        unrealized_emoji = "🟢" if unrealized >= 0 else "🔴"
        daily_emoji = "🟢" if daily >= 0 else "🔴"

        logger.info(f"{unrealized_emoji} Unrealized P&L: ${unrealized:+.2f}")
        logger.info(f"{daily_emoji} Daily Realized P&L: ${daily:+.2f}")

        # Open positions detail
        positions = self.risk.get_open_positions()
        if positions:
            logger.info("")
            logger.info("Open Positions:")
            logger.info("-" * 70)

            # Sort by P&L
            sorted_positions = sorted(positions, key=lambda p: p.unrealized_pnl, reverse=True)

            for pos in sorted_positions[:5]:  # Show top 5
                pnl_emoji = "🟢" if pos.unrealized_pnl >= 0 else "🔴"
                logger.info(
                    f"{pnl_emoji} {pos.market_id[:8]}... | "
                    f"Entry: ${pos.avg_price:.4f} | "
                    f"Current: ${pos.current_price:.4f} | "
                    f"P&L: ${pos.unrealized_pnl:+.2f} ({pos.pnl_percentage:+.1f}%)"
                )

            if len(positions) > 5:
                logger.info(f"... and {len(positions) - 5} more positions")

        # Daily stats
        today_stats = self.risk._get_today_stats()
        total_trades = today_stats.winning_trades + today_stats.losing_trades
        win_rate = (today_stats.winning_trades / total_trades * 100) if total_trades > 0 else 0

        logger.info("")
        logger.info("Today's Stats:")
        logger.info("-" * 70)
        logger.info(f"✅ Winning Trades: {today_stats.winning_trades}")
        logger.info(f"❌ Losing Trades: {today_stats.losing_trades}")
        logger.info(f"🎯 Win Rate: {win_rate:.1f}%")
        logger.info(f"💵 Total Volume: ${today_stats.total_volume:.2f}")
        logger.info(f"🎁 Rewards Earned: ${today_stats.rewards_earned:.2f}")

        # Total P&L
        total_pnl = unrealized + daily
        total_emoji = "🚀" if total_pnl >= 0 else "📉"

        logger.info("")
        logger.info(f"{total_emoji} TOTAL P&L: ${total_pnl:+.2f}")
        logger.info("=" * 70)

    def print_compact_summary(self):
        """
        Print a compact one-line performance summary.

        Useful for frequent updates without log spam.
        """
        summary = self.risk.get_risk_summary()

        unrealized = summary['unrealized_pnl']
        daily = summary['daily_pnl']
        total_pnl = unrealized + daily

        logger.info(
            f"💰 P&L: ${total_pnl:+.2f} "
            f"(Unrealized: ${unrealized:+.2f}, Daily: ${daily:+.2f}) | "
            f"Positions: {summary['open_positions_count']} | "
            f"Capital: ${summary['available_capital']:.2f}"
        )

    def should_print_full_summary(self, interval_minutes: int = 60) -> bool:
        """
        Check if it's time to print a full summary.

        Args:
            interval_minutes: Minutes between full summaries

        Returns:
            True if should print
        """
        now = datetime.now()
        elapsed = (now - self._last_report_time).total_seconds() / 60

        if elapsed >= interval_minutes:
            self._last_report_time = now
            return True

        return False

    def get_best_trade(self) -> Position:
        """Get the best performing open position"""
        positions = self.risk.get_open_positions()
        if not positions:
            return None

        return max(positions, key=lambda p: p.pnl_percentage)

    def get_worst_trade(self) -> Position:
        """Get the worst performing open position"""
        positions = self.risk.get_open_positions()
        if not positions:
            return None

        return min(positions, key=lambda p: p.pnl_percentage)


def create_performance_reporter(risk_manager: RiskManager) -> PerformanceReporter:
    """
    Factory function to create a PerformanceReporter.

    Args:
        risk_manager: RiskManager instance

    Returns:
        Configured PerformanceReporter instance
    """
    return PerformanceReporter(risk_manager)
