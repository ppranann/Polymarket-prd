"""
Calibration Report Module
=========================
Analyzes resolved predictions to measure model accuracy.
Run: python -m src.calibration --days 30
"""

import argparse
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from supabase import create_client

logger = logging.getLogger(__name__)


def compute_brier_score(predictions: List[dict]) -> float:
    """Brier score: mean squared error of probability forecasts. Lower = better."""
    if not predictions:
        return 1.0
    total = sum(
        (p["fair_price"] - p["actual_outcome"]) ** 2
        for p in predictions
    )
    return total / len(predictions)


def compute_calibration(predictions: List[dict], n_buckets: int = 5) -> Dict:
    """
    Group predictions by fair_price bucket and compute hit rate vs predicted rate.
    Perfect calibration: predicted 15% bucket → actual 15% hit rate.
    """
    bucket_size = 1.0 / n_buckets
    buckets = defaultdict(list)

    for p in predictions:
        bucket_idx = min(int(p["fair_price"] / bucket_size), n_buckets - 1)
        low = bucket_idx * bucket_size
        high = low + bucket_size
        label = f"{low:.0%}-{high:.0%}"
        buckets[label].append(p)

    result = {}
    for label, preds in sorted(buckets.items()):
        avg_predicted = sum(p["fair_price"] for p in preds) / len(preds)
        actual_hit_rate = sum(p["actual_outcome"] for p in preds) / len(preds)
        avg_market = sum(p["market_price"] for p in preds) / len(preds)
        total_pnl = sum(p.get("pnl_realized", 0) or 0 for p in preds)

        result[label] = {
            "count": len(preds),
            "avg_predicted": avg_predicted,
            "actual_hit_rate": actual_hit_rate,
            "calibration_error": abs(avg_predicted - actual_hit_rate),
            "avg_market_price": avg_market,
            "total_pnl": total_pnl
        }

    return result


def compute_by_dimension(predictions: List[dict], key: str) -> Dict:
    """Group by a dimension (city, condition_type, days_to_resolution) and compute stats."""
    groups = defaultdict(list)
    for p in predictions:
        groups[str(p.get(key, "unknown"))].append(p)

    result = {}
    for label, preds in sorted(groups.items()):
        resolved = [p for p in preds if p.get("actual_outcome") is not None]
        if not resolved:
            continue

        wins = sum(1 for p in resolved if p["actual_outcome"] == 1 and p.get("side") == "BUY")
        losses = len(resolved) - wins
        brier = compute_brier_score(resolved)
        total_pnl = sum(p.get("pnl_realized", 0) or 0 for p in resolved)

        result[label] = {
            "total": len(preds),
            "resolved": len(resolved),
            "wins": wins,
            "losses": losses,
            "brier_score": brier,
            "total_pnl": total_pnl
        }

    return result


def print_report(predictions: List[dict]):
    """Print a formatted calibration report."""
    resolved = [p for p in predictions if p.get("actual_outcome") is not None]
    traded = [p for p in predictions if p.get("side") in ("BUY", "SELL")]

    print("\n" + "=" * 70)
    print("  CALIBRATION REPORT")
    print("=" * 70)

    print(f"\n  Total predictions logged: {len(predictions)}")
    print(f"  Resolved markets:        {len(resolved)}")
    print(f"  Traded (BUY/SELL):       {len(traded)}")

    if not resolved:
        print("\n  No resolved predictions yet. Wait for markets to settle.")
        print("=" * 70)
        return

    # Overall Brier Score
    brier = compute_brier_score(resolved)
    print(f"\n  Brier Score: {brier:.4f}  (0 = perfect, 0.25 = random)")
    print(f"  {'GOOD' if brier < 0.20 else 'OK' if brier < 0.25 else 'NEEDS WORK'}")

    # Calibration by probability bucket
    print(f"\n  {'PROBABILITY CALIBRATION':^50}")
    print(f"  {'Bucket':<12} {'Count':>6} {'Predicted':>10} {'Actual':>10} {'Error':>8}")
    print("  " + "-" * 50)
    cal = compute_calibration(resolved)
    for label, stats in cal.items():
        print(
            f"  {label:<12} {stats['count']:>6} "
            f"{stats['avg_predicted']:>9.1%} {stats['actual_hit_rate']:>9.1%} "
            f"{stats['calibration_error']:>7.1%}"
        )

    # By City
    print(f"\n  {'BY CITY':^50}")
    print(f"  {'City':<12} {'Resolved':>8} {'Wins':>6} {'Brier':>8} {'PnL':>10}")
    print("  " + "-" * 50)
    by_city = compute_by_dimension(resolved, "city")
    for label, stats in by_city.items():
        print(
            f"  {label:<12} {stats['resolved']:>8} {stats['wins']:>6} "
            f"{stats['brier_score']:>7.4f} ${stats['total_pnl']:>9.2f}"
        )

    # By Horizon
    print(f"\n  {'BY HORIZON (days out)':^50}")
    print(f"  {'Days':<12} {'Resolved':>8} {'Wins':>6} {'Brier':>8} {'PnL':>10}")
    print("  " + "-" * 50)
    by_horizon = compute_by_dimension(resolved, "days_to_resolution")
    for label, stats in by_horizon.items():
        print(
            f"  {label + 'd':<12} {stats['resolved']:>8} {stats['wins']:>6} "
            f"{stats['brier_score']:>7.4f} ${stats['total_pnl']:>9.2f}"
        )

    # By Condition Type
    print(f"\n  {'BY CONDITION TYPE':^50}")
    print(f"  {'Type':<12} {'Resolved':>8} {'Wins':>6} {'Brier':>8} {'PnL':>10}")
    print("  " + "-" * 50)
    by_type = compute_by_dimension(resolved, "condition_type")
    for label, stats in by_type.items():
        print(
            f"  {label:<12} {stats['resolved']:>8} {stats['wins']:>6} "
            f"{stats['brier_score']:>7.4f} ${stats['total_pnl']:>9.2f}"
        )

    # Sigma validation
    print(f"\n  {'SIGMA VALIDATION':^50}")
    print(f"  {'Sigma':>8} {'Count':>6} {'Brier':>8} {'Avg Error':>10}")
    print("  " + "-" * 40)
    sigma_groups = defaultdict(list)
    for p in resolved:
        sigma_groups[f"{p.get('sigma_used', 3.5):.1f}"].append(p)
    for sigma_label, preds in sorted(sigma_groups.items()):
        b = compute_brier_score(preds)
        avg_err = sum(abs(p["fair_price"] - p["actual_outcome"]) for p in preds) / len(preds)
        print(f"  {sigma_label + '°F':>8} {len(preds):>6} {b:>7.4f} {avg_err:>9.1%}")

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Weather Bot Calibration Report")
    parser.add_argument("--days", type=int, default=30, help="Days of history to analyze")
    parser.add_argument("--supabase-url", type=str, help="Supabase URL")
    parser.add_argument("--supabase-key", type=str, help="Supabase key")
    args = parser.parse_args()

    import os
    url = args.supabase_url or os.environ.get("SUPABASE_URL")
    key = args.supabase_key or os.environ.get("SUPABASE_KEY")

    if not url or not key:
        print("Error: SUPABASE_URL and SUPABASE_KEY required (env vars or args)")
        sys.exit(1)

    client = create_client(url, key)

    since = (datetime.now() - timedelta(days=args.days)).isoformat()

    try:
        response = (
            client
            .table("polymarket_predictions")
            .select("*")
            .gte("created_at", since)
            .execute()
        )
        predictions = response.data or []
    except Exception as e:
        print(f"Error fetching predictions: {e}")
        sys.exit(1)

    print_report(predictions)


if __name__ == "__main__":
    main()
