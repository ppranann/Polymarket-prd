"""
Resolution Tracker Module
=========================
Checks if markets the bot has bet on have resolved,
records actual outcomes for calibration tracking.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

import re

import aiohttp

from .logger import AuditLogger


def _bucket_midpoint_f(bucket_range: str, question_text: str) -> Optional[float]:
    """
    Convert a stored bucket_range string (e.g. "8-9", "-2-0", "11-15") to a
    midpoint temperature in °F.  Returns None if parsing fails.

    bucket_range is stored in the original market unit (°C or °F), so we detect
    the unit from question_text and convert if needed.
    """
    try:
        # Match "low-high" where low can be negative: e.g. "-2-0", "8-9", "11-15"
        m = re.match(r'^(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)$', bucket_range.strip())
        if not m:
            return None
        low, high = float(m.group(1)), float(m.group(2))
        mid = (low + high) / 2.0
        # Convert C→F if the market question uses Celsius
        if '°C' in question_text or '°c' in question_text:
            mid = mid * 9.0 / 5.0 + 32.0
        return mid
    except Exception:
        return None

logger = logging.getLogger(__name__)


class ResolutionTracker:
    """
    Periodically checks Polymarket for resolved markets
    and updates prediction logs with actual outcomes.
    """

    GAMMA_API = "https://gamma-api.polymarket.com"

    def __init__(self, audit_logger: AuditLogger):
        self._logger = audit_logger
        self._checked_markets: set = set()
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def check_resolutions(self, market_ids: List[str]) -> Dict[str, dict]:
        """
        Check which markets have resolved and what the outcome was.

        Args:
            market_ids: List of market IDs to check

        Returns:
            Dict of market_id -> {"resolved": bool, "outcome": 0 or 1, "winning_side": "YES"/"NO"}
        """
        results = {}
        unchecked = [m for m in market_ids if m not in self._checked_markets]

        if not unchecked:
            return results

        session = await self._get_session()

        for market_id in unchecked:
            try:
                # Query Gamma API for market status
                url = f"{self.GAMMA_API}/markets/{market_id}"
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()

                # Check if market is resolved
                is_closed = data.get("closed", False)
                resolution = data.get("resolutionSource", "")
                outcome_prices = data.get("outcomePrices", "")

                if is_closed and outcome_prices:
                    # Parse outcome: "[1, 0]" means YES won, "[0, 1]" means NO won
                    try:
                        import json
                        prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
                        if len(prices) >= 2:
                            yes_won = float(prices[0]) > 0.5
                            outcome = 1 if yes_won else 0
                            results[market_id] = {
                                "resolved": True,
                                "outcome": outcome,
                                "winning_side": "YES" if yes_won else "NO"
                            }
                            self._checked_markets.add(market_id)

                            logger.info(
                                f"RESOLVED: {market_id[:8]} → "
                                f"{'YES' if yes_won else 'NO'} won"
                            )
                    except (ValueError, TypeError) as e:
                        logger.debug(f"Failed to parse outcome for {market_id}: {e}")

            except Exception as e:
                logger.debug(f"Failed to check resolution for {market_id[:8]}: {e}")

        if results:
            logger.info(f"Resolution check: {len(results)}/{len(unchecked)} markets resolved")

        return results

    async def update_predictions(self, resolutions: Dict[str, dict]) -> int:
        """
        Update prediction logs in Supabase with resolution outcomes and Brier scores.

        Brier score = (p_model - actual_outcome)^2
        Lower is better: 0.0 = perfect, 0.25 = random, 1.0 = maximally wrong.

        Args:
            resolutions: Dict from check_resolutions()

        Returns:
            Number of predictions updated
        """
        if not resolutions or not self._logger._connected:
            return 0

        updated = 0
        for market_id, result in resolutions.items():
            actual_outcome = result["outcome"]
            try:
                # Fetch existing prediction fields needed for Brier score and bias tracking
                existing = (
                    self._logger._client
                    .table(f"{self._logger.table_prefix}predictions")
                    .select("p_model, bucket_range, question_text, forecast_temp_f")
                    .eq("market_id", market_id)
                    .eq("resolved", False)
                    .limit(1)
                    .execute()
                )
                row = existing.data[0] if existing.data else {}
                p_model = row.get("p_model", 0.5)
                brier = (p_model - actual_outcome) ** 2

                update_payload = {
                    "resolved": True,
                    "actual_outcome": actual_outcome,
                    "resolved_at": datetime.now().isoformat(),
                    "brier_score": brier,
                }

                # If YES won on a bucket market, record actual temp for rolling bias.
                if actual_outcome == 1 and row.get("bucket_range"):
                    actual_temp_f = _bucket_midpoint_f(
                        row["bucket_range"],
                        row.get("question_text", ""),
                    )
                    if actual_temp_f is not None:
                        update_payload["actual_temp_f"] = actual_temp_f
                        logger.info(
                            f"ACTUAL TEMP: {market_id[:8]} | "
                            f"bucket={row['bucket_range']} → {actual_temp_f:.1f}°F "
                            f"| forecast={row.get('forecast_temp_f', '?')}°F "
                            f"| bias={row.get('forecast_temp_f', 0) - actual_temp_f:.1f}°F"
                        )

                response = (
                    self._logger._client
                    .table(f"{self._logger.table_prefix}predictions")
                    .update(update_payload)
                    .eq("market_id", market_id)
                    .eq("resolved", False)
                    .execute()
                )
                if response.data:
                    updated += len(response.data)
                    logger.info(
                        f"BRIER: {market_id[:8]} | outcome={'YES' if actual_outcome else 'NO'} "
                        f"| p_model={p_model:.3f} | brier={brier:.4f}"
                    )
            except Exception as e:
                logger.debug(f"Failed to update prediction for {market_id}: {e}")

        if updated:
            logger.info(f"Updated {updated} predictions with resolution outcomes + Brier scores")

        return updated

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
