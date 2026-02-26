"""
Supabase Logger Module
======================
Logs all trading decisions, calculations, and reward payouts to Supabase.
Provides full audit trail of bot activity.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from supabase import Client, create_client

from .config import BotConfig

logger = logging.getLogger(__name__)


@dataclass
class AuditLog:
    """Represents an audit log entry"""
    event_type: str  # SCAN, TRADE, DECISION, ERROR, REWARD, EXIT
    market_id: Optional[str] = None
    id: Optional[str] = None
    created_at: Optional[datetime] = None
    action: Optional[str] = None
    fair_price: Optional[float] = None
    market_price: Optional[float] = None
    alpha: Optional[float] = None
    trade_size: Optional[float] = None
    weather_data: Optional[Dict] = None
    decision_reason: Optional[str] = None
    profit_loss: Optional[float] = None
    reward_earned: Optional[float] = None
    status: str = "SUCCESS"
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for database insertion"""
        return {
            "event_type": self.event_type,
            "market_id": self.market_id,
            "action": self.action,
            "fair_price": self.fair_price,
            "market_price": self.market_price,
            "alpha": self.alpha,
            "trade_size": self.trade_size,
            "weather_data": self.weather_data,
            "decision_reason": self.decision_reason,
            "profit_loss": self.profit_loss,
            "reward_earned": self.reward_earned,
            "status": self.status,
            "error_message": self.error_message
        }


@dataclass
class PredictionLog:
    """Structured prediction log for calibration and backtesting"""
    market_id: str
    question_text: str
    city: str
    target_date: str  # e.g. "2026-02-12"
    condition_type: str  # above, below, range
    threshold_f: float
    forecast_temp_f: float
    sigma_used: float
    z_score: float
    days_to_resolution: int
    fair_price: float
    market_price: float
    alpha: float
    side: str  # BUY, SELL, SKIP
    size: float
    capital_at_time: float
    created_at: Optional[datetime] = None
    # Cross-market analysis fields
    market_type: str = "threshold"     # "threshold" or "max_temp"
    distribution_mu: float = 0.0      # Model distribution mean (°F)
    distribution_sigma: float = 0.0   # Model distribution sigma (°F)
    p_model: float = 0.0              # Model probability for this outcome
    p_implied: Optional[float] = None # Implied probability from other market type
    bucket_range: Optional[str] = None # e.g., "11-15" for bucket markets
    # Filled in later by resolution tracker
    resolved: bool = False
    actual_outcome: Optional[int] = None  # 1 = YES won, 0 = NO won
    resolved_at: Optional[datetime] = None
    pnl_realized: Optional[float] = None
    brier_score: Optional[float] = None   # (p_model - outcome)^2, lower is better

    def to_dict(self) -> Dict:
        """Convert to dictionary for Supabase insertion"""
        return {
            "market_id": self.market_id,
            "question_text": self.question_text,
            "city": self.city,
            "target_date": self.target_date,
            "condition_type": self.condition_type,
            "threshold_f": self.threshold_f,
            "forecast_temp_f": self.forecast_temp_f,
            "sigma_used": self.sigma_used,
            "z_score": self.z_score,
            "days_to_resolution": self.days_to_resolution,
            "fair_price": self.fair_price,
            "market_price": self.market_price,
            "alpha": self.alpha,
            "side": self.side,
            "size": self.size,
            "capital_at_time": self.capital_at_time,
            "market_type": self.market_type,
            "distribution_mu": self.distribution_mu,
            "distribution_sigma": self.distribution_sigma,
            "p_model": self.p_model,
            "p_implied": self.p_implied,
            "bucket_range": self.bucket_range,
            "resolved": self.resolved,
            "actual_outcome": self.actual_outcome,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "pnl_realized": self.pnl_realized,
            "brier_score": self.brier_score,
        }


@dataclass
class RewardLog:
    """Represents a liquidity reward log entry"""
    market_id: str
    usdc_earned: float
    order_ids: List[str]
    order_type: str  # FARMING, REGULAR
    id: Optional[str] = None
    created_at: Optional[datetime] = None
    duration_hours: Optional[float] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for database insertion"""
        return {
            "market_id": self.market_id,
            "usdc_earned": self.usdc_earned,
            "order_ids": self.order_ids if isinstance(self.order_ids, list) else [self.order_ids],
            "order_type": self.order_type,
            "duration_hours": self.duration_hours
        }


class AuditLogger:
    """
    Logs all bot activities to Supabase for auditing and analysis.
    
    Tables:
    - polymarket_audit_log: All trading decisions and events
    - polymarket_rewards: Liquidity rewards earned
    - polymarket_positions: Current open positions
    """
    
    def __init__(
        self,
        config: BotConfig,
        url: str = None,
        key: str = None
    ):
        """
        Initialize audit logger.
        
        Args:
            config: Bot configuration
            url: Supabase URL (uses config if not provided)
            key: Supabase API key (uses config if not provided)
        """
        self.config = config
        self.table_prefix = config.logging.supabase.get("table_prefix", "polymarket_")
        
        # Get Supabase credentials
        supabase_url = url or config.logging.supabase.get("url")
        supabase_key = key or config.logging.supabase.get("key")
        
        self._client: Optional[Client] = None
        self._connected = False
        
        if supabase_url and supabase_key:
            try:
                self._client = create_client(supabase_url, supabase_key)
                self._connected = True
                logger.info("Supabase logger initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize Supabase logger: {e}")
        else:
            logger.warning("Supabase credentials not provided, logging to console only")
        
        # In-memory buffer for offline logging
        self._log_buffer: List[Dict] = []
        self._reward_buffer: List[Dict] = []
    
    async def log_decision(
        self,
        event_type: str,
        market_id: Optional[str] = None,
        action: Optional[str] = None,
        fair_price: Optional[float] = None,
        market_price: Optional[float] = None,
        alpha: Optional[float] = None,
        trade_size: Optional[float] = None,
        weather_data: Optional[Dict] = None,
        decision_reason: Optional[str] = None,
        profit_loss: Optional[float] = None,
        reward_earned: Optional[float] = None,
        status: str = "SUCCESS",
        error_message: Optional[str] = None
    ) -> Optional[str]:
        """
        Log a trading decision to Supabase.
        
        Args:
            event_type: Type of event (SCAN, TRADE, DECISION, ERROR, EXIT)
            market_id: Market ID (optional)
            action: Action taken (optional)
            fair_price: Calculated fair price (optional)
            market_price: Current market price (optional)
            alpha: Calculated alpha (optional)
            trade_size: Trade size (optional)
            weather_data: Weather data used (optional)
            decision_reason: Reason for decision (optional)
            profit_loss: PnL realized (optional)
            reward_earned: Rewards earned (optional)
            status: Status of the event
            error_message: Error message if failed
            
        Returns:
            Log entry ID if successful, None otherwise
        """
        log_entry = AuditLog(
            event_type=event_type,
            market_id=market_id,
            action=action,
            fair_price=fair_price,
            market_price=market_price,
            alpha=alpha,
            trade_size=trade_size,
            weather_data=weather_data,
            decision_reason=decision_reason,
            profit_loss=profit_loss,
            reward_earned=reward_earned,
            status=status,
            error_message=error_message
        )
        
        # Log to console for debugging - safely handle alpha formatting
        alpha_str = f"{float(alpha or 0):.2%}"
        logger.info(
            f"AUDIT: {event_type} | {action or 'N/A'} | "
            f"Market: {market_id or 'N/A'} | "
            f"Alpha: {alpha_str} | "
            f"Status: {status}"
        )
        
        # Try to send to Supabase
        if self._connected and self._client:
            try:
                response = (
                    self._client
                    .table(f"{self.table_prefix}audit_log")
                    .insert(log_entry.to_dict())
                    .execute()
                )
                
                if response.data:
                    return response.data[0].get("id")

            except Exception as e:
                logger.debug(f"Failed to log to Supabase: {e}")
        
        # Buffer for later sync
        self._log_buffer.append(log_entry.to_dict())
        
        return None
    
    async def log_prediction(self, prediction: PredictionLog) -> Optional[str]:
        """
        Log a structured prediction for calibration tracking.

        Writes to polymarket_predictions table in Supabase.
        """
        if self._connected and self._client:
            try:
                response = (
                    self._client
                    .table(f"{self.table_prefix}predictions")
                    .insert(prediction.to_dict())
                    .execute()
                )
                if response.data:
                    return response.data[0].get("id")
            except Exception as e:
                logger.debug(f"Failed to log prediction to Supabase: {e}")

        # Always log to console for visibility
        logger.info(
            f"PREDICTION: {prediction.side} | {prediction.city} {prediction.target_date} | "
            f"type={prediction.condition_type} | threshold={prediction.threshold_f:.1f}°F | "
            f"forecast={prediction.forecast_temp_f:.1f}°F | sigma={prediction.sigma_used:.1f} | "
            f"fair=${prediction.fair_price:.3f} vs market=${prediction.market_price:.3f} | "
            f"alpha={prediction.alpha:.1%} | days_out={prediction.days_to_resolution}"
        )
        return None

    async def update_prediction_resolution(
        self,
        market_id: str,
        actual_outcome: int,  # 1=YES won, 0=NO won
        p_model: float,
    ) -> None:
        """
        Mark a prediction as resolved and compute its Brier score.

        Brier score = (p_model - actual_outcome)^2
        Lower is better. 0.0 = perfect, 0.25 = random, 1.0 = maximally wrong.
        """
        brier = (p_model - actual_outcome) ** 2
        resolved_at = datetime.utcnow().isoformat()

        logger.info(
            f"RESOLUTION: market={market_id[:8]} outcome={'YES' if actual_outcome else 'NO'} "
            f"p_model={p_model:.3f} brier={brier:.4f}"
        )

        if not (self._connected and self._client):
            return

        try:
            self._client.table(f"{self.table_prefix}predictions").update({
                "resolved": True,
                "actual_outcome": actual_outcome,
                "resolved_at": resolved_at,
                "brier_score": brier,
            }).eq("market_id", market_id).eq("resolved", False).execute()
        except Exception as e:
            logger.debug(f"Failed to update resolution for {market_id}: {e}")

    async def get_unresolved_predictions(self, cutoff_date: str) -> List[Dict]:
        """
        Fetch predictions whose target_date <= cutoff_date and not yet resolved.
        Used by the resolution checker to find markets that should have settled.
        """
        if not (self._connected and self._client):
            return []
        try:
            response = (
                self._client
                .table(f"{self.table_prefix}predictions")
                .select("market_id, p_model, target_date")
                .eq("resolved", False)
                .lte("target_date", cutoff_date)
                .execute()
            )
            return response.data or []
        except Exception as e:
            logger.debug(f"Failed to fetch unresolved predictions: {e}")
            return []

    async def log_scan(
        self,
        markets_found: int,
        qualifying_markets: int,
        scan_duration_ms: float,
        errors: Optional[List[str]] = None
    ) -> Optional[str]:
        """
        Log a market scan event.
        
        Args:
            markets_found: Total markets found
            qualifying_markets: Markets meeting criteria
            scan_duration_ms: Duration of scan in milliseconds
            errors: List of errors encountered
            
        Returns:
            Log entry ID if successful
        """
        return await self.log_decision(
            event_type="SCAN",
            action=f"Found {markets_found}, Qualifying: {qualifying_markets}",
            decision_reason=f"Scan completed in {scan_duration_ms:.0f}ms",
            profit_loss=markets_found,
            status="SUCCESS" if not errors else "PARTIAL",
            error_message=", ".join(errors) if errors else None
        )
    
    async def log_trade(
        self,
        market_id: str,
        action: str,
        fair_price: float,
        market_price: float,
        trade_size: float,
        alpha: float,
        order_id: Optional[str] = None,
        reward_earned: Optional[float] = None
    ) -> Optional[str]:
        """
        Log a trade execution.
        
        Args:
            market_id: Market ID
            action: Action taken (BUY, SELL, FARM)
            fair_price: Calculated fair price
            market_price: Execution price
            trade_size: Size of trade
            alpha: Calculated alpha
            order_id: Order ID if available
            reward_earned: Rewards earned from trade
            
        Returns:
            Log entry ID if successful
        """
        return await self.log_decision(
            event_type="TRADE",
            market_id=market_id,
            action=action,
            fair_price=fair_price,
            market_price=market_price,
            trade_size=trade_size,
            alpha=alpha,
            reward_earned=reward_earned,
            decision_reason=f"Order ID: {order_id or 'N/A'}"
        )
    
    async def log_exit(
        self,
        market_id: str,
        profit_loss: float,
        exit_reason: str,
        duration_hours: float
    ) -> Optional[str]:
        """
        Log a position exit.
        
        Args:
            market_id: Market ID
            profit_loss: Realized PnL
            exit_reason: Reason for exit
            duration_hours: How long position was held
            
        Returns:
            Log entry ID if successful
        """
        return await self.log_decision(
            event_type="EXIT",
            market_id=market_id,
            action="SELL",
            profit_loss=profit_loss,
            decision_reason=f"{exit_reason} (Held: {duration_hours:.1f}h)"
        )
    
    async def log_reward(
        self,
        market_id: str,
        usdc_earned: float,
        order_ids: List[str],
        order_type: str = "FARMING"
    ) -> Optional[str]:
        """
        Log liquidity rewards earned.
        
        Args:
            market_id: Market ID
            usdc_earned: Amount earned
            order_ids: Orders that earned rewards
            order_type: Type of orders (FARMING, REGULAR)
            
        Returns:
            Log entry ID if successful
        """
        # Log to audit log
        await self.log_decision(
            event_type="REWARD",
            market_id=market_id,
            action=order_type,
            reward_earned=usdc_earned,
            decision_reason=f"Orders: {', '.join(order_ids[:3])}..."
        )
        
        # Create reward log entry
        reward_entry = {
            "market_id": market_id,
            "usdc_earned": usdc_earned,
            "order_ids": order_ids,
            "order_type": order_type,
            "created_at": datetime.now().isoformat()
        }
        
        # Try to insert into rewards table
        if self._connected and self._client:
            try:
                response = (
                    self._client
                    .table(f"{self.table_prefix}rewards")
                    .insert(reward_entry)
                    .execute()
                )
                
                if response.data:
                    return response.data[0].get("id")
                    
            except Exception as e:
                logger.warning(f"Failed to log reward to Supabase: {e}")
        
        # Buffer for later sync
        self._reward_buffer.append(reward_entry)
        
        return None
    
    async def log_error(
        self,
        error_type: str,
        error_message: str,
        market_id: Optional[str] = None,
        context: Optional[Dict] = None
    ) -> Optional[str]:
        """
        Log an error event.
        
        Args:
            error_type: Type of error
            error_message: Error message
            market_id: Related market ID (optional)
            context: Additional context (optional)
            
        Returns:
            Log entry ID if successful
        """
        return await self.log_decision(
            event_type="ERROR",
            market_id=market_id,
            action=error_type,
            decision_reason=error_message,
            status="ERROR",
            error_message=error_message
        )
    
    async def sync_buffer(self) -> int:
        """
        Sync buffered logs to Supabase.
        
        Returns:
            Number of entries synced
        """
        synced = 0
        
        # Sync audit logs
        for entry in self._log_buffer:
            try:
                self._client.table(f"{self.table_prefix}audit_log").insert(entry).execute()
                synced += 1
            except Exception as e:
                logger.warning(f"Failed to sync audit log: {e}")
        
        self._log_buffer.clear()
        
        # Sync rewards
        for entry in self._reward_buffer:
            try:
                self._client.table(f"{self.table_prefix}rewards").insert(entry).execute()
                synced += 1
            except Exception as e:
                logger.warning(f"Failed to sync reward: {e}")
        
        self._reward_buffer.clear()
        
        if synced > 0:
            logger.info(f"Synced {synced} buffered log entries to Supabase")
        
        return synced
    
    async def get_daily_summary(self, date: datetime = None) -> Dict:
        """
        Get summary of activity for a specific day.
        
        Args:
            date: Date to summarize (defaults to today)
            
        Returns:
            Summary dictionary
        """
        if not self._connected:
            return {"error": "Not connected to Supabase"}
        
        target_date = date or datetime.now()
        date_str = target_date.strftime("%Y-%m-%d")
        
        try:
            # Get trade count
            trades_response = (
                self._client
                .table(f"{self.table_prefix}audit_log")
                .select("id", count="exact")
                .eq("event_type", "TRADE")
                .gte("created_at", f"{date_str}T00:00:00")
                .lt("created_at", f"{date_str}T23:59:59")
                .execute()
            )
            
            # Get total PnL
            pnl_response = (
                self._client
                .table(f"{self.table_prefix}audit_log")
                .select("profit_loss")
                .eq("event_type", "EXIT")
                .gte("created_at", f"{date_str}T00:00:00")
                .lt("created_at", f"{date_str}T23:59:59")
                .execute()
            )
            
            # Get total rewards
            rewards_response = (
                self._client
                .table(f"{self.table_prefix}audit_log")
                .select("reward_earned", count="exact")
                .eq("event_type", "REWARD")
                .gte("created_at", f"{date_str}T00:00:00")
                .lt("created_at", f"{date_str}T23:59:59")
                .execute()
            )
            
            total_pnl = sum(
                float(r.get("profit_loss", 0))
                for r in pnl_response.data
                if r.get("profit_loss")
            )
            
            total_rewards = sum(
                float(r.get("reward_earned", 0))
                for r in rewards_response.data
                if r.get("reward_earned")
            )
            
            return {
                "date": date_str,
                "trades_count": trades_response.count or 0,
                "total_pnl": total_pnl,
                "total_rewards": total_rewards,
                "total_profit": total_pnl + total_rewards
            }
            
        except Exception as e:
            logger.error(f"Failed to get daily summary: {e}")
            return {"error": str(e)}
    
    async def get_open_positions(self, days: int = 3) -> list:
        """
        Recover open positions on startup by finding BUY trades with no matching EXIT.

        Returns:
            List of dicts: {market_id, entry_price, size, entry_alpha, opened_at}
        """
        if not self._connected or not self._client:
            return []

        try:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            table = f"{self.table_prefix}audit_log"

            buys = (
                self._client.table(table)
                .select("market_id, market_price, trade_size, alpha, created_at")
                .eq("event_type", "TRADE")
                .eq("action", "BUY")
                .eq("status", "SUCCESS")
                .gte("created_at", cutoff)
                .execute()
            )

            exits = (
                self._client.table(table)
                .select("market_id")
                .eq("event_type", "EXIT")
                .gte("created_at", cutoff)
                .execute()
            )

            exited_ids = {row["market_id"] for row in exits.data if row.get("market_id")}

            open_positions = []
            seen = set()
            for row in sorted(buys.data, key=lambda x: x.get("created_at", ""), reverse=True):
                mid = row.get("market_id")
                if not mid or mid in exited_ids or mid in seen:
                    continue
                seen.add(mid)
                entry_price = float(row.get("market_price") or 0)
                shares = float(row.get("trade_size") or 0)
                if entry_price <= 0 or shares <= 0:
                    continue
                # trade_size stores SHARES (order.size), not USDC.
                # Convert: usdc_size = shares × entry_price
                usdc_size = round(shares * entry_price, 4)
                if usdc_size <= 0 or usdc_size > 50:
                    logger.warning(f"Skipping recovery of {mid[:8]}: computed usdc=${usdc_size:.2f} out of range (shares={shares:.2f}, price={entry_price:.4f})")
                    continue
                open_positions.append({
                    "market_id": mid,
                    "entry_price": entry_price,
                    "size": usdc_size,
                    "entry_alpha": float(row.get("alpha") or 0),
                    "opened_at": row.get("created_at"),
                })

            if open_positions:
                logger.info(f"Found {len(open_positions)} open position(s) in Supabase to recover")
            return open_positions

        except Exception as e:
            logger.warning(f"Could not recover open positions from Supabase: {e}")
            return []

    async def get_recently_bought_market_ids(self, days: int = 7) -> list:
        """
        Fetch market IDs that had BUY trades in the last N days.
        Used on startup to block markets we currently hold positions in —
        the in-memory _positions dict is empty after a restart, so without
        this the bot re-buys markets it already owns.

        Args:
            days: How far back to look (default 7 — covers all active weather markets)

        Returns:
            List of market_id strings that had recent BUY events
        """
        if not self._connected or not self._client:
            return []

        try:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            table = f"{self.table_prefix}audit_log"
            result = (
                self._client.table(table)
                .select("market_id")
                .eq("event_type", "TRADE")
                .eq("action", "BUY")
                .gte("created_at", cutoff)
                .execute()
            )
            ids = list({row["market_id"] for row in result.data if row.get("market_id")})
            if ids:
                logger.info(f"Loaded {len(ids)} recently bought market(s) from Supabase to block list")
            return ids
        except Exception as e:
            logger.warning(f"Could not load recently bought markets from Supabase: {e}")
            return []

    async def get_recently_exited_market_ids(self, days: int = 7) -> list:
        """
        Fetch market IDs that were exited in the last N days from Supabase.
        Used on startup to pre-populate the permanent block list so container
        restarts don't re-enter markets we already traded and exited.

        Args:
            days: How far back to look (default 7 — covers all active weather markets)

        Returns:
            List of market_id strings that had EXIT events
        """
        if not self._connected or not self._client:
            return []

        try:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            table = f"{self.table_prefix}audit_log"
            result = (
                self._client.table(table)
                .select("market_id")
                .eq("event_type", "EXIT")
                .gte("created_at", cutoff)
                .execute()
            )
            ids = [row["market_id"] for row in result.data if row.get("market_id")]
            if ids:
                logger.info(f"Loaded {len(ids)} previously exited market(s) from Supabase to block list")
            return ids
        except Exception as e:
            logger.warning(f"Could not load exited markets from Supabase: {e}")
            return []

    async def close(self):
        """Close the logger and sync any remaining buffer"""
        await self.sync_buffer()
        logger.info("Audit logger closed")


def create_audit_logger(
    config: BotConfig,
    url: str = None,
    key: str = None
) -> AuditLogger:
    """
    Factory function to create an AuditLogger.
    
    Args:
        config: Bot configuration
        url: Supabase URL (optional)
        key: Supabase API key (optional)
        
    Returns:
        Configured AuditLogger instance
    """
    return AuditLogger(
        config=config,
        url=url,
        key=key
    )

