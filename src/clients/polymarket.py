import logging
import json
import asyncio
import os
import aiohttp
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Try to import py-clob-client for live order placement
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
    CLOB_AVAILABLE = True
except ImportError:
    CLOB_AVAILABLE = False


@dataclass
class Market:
    id: str
    question: str
    condition_id: str
    yes_price: float
    no_price: float
    last_trade_price: float
    active: bool
    category: str
    volume: float
    description: str = ""
    liquidity_rewards_enabled: bool = False
    clob_token_id: str = ""  # YES token ID for CLOB order placement

@dataclass
class Order:
    id: str
    market_id: str
    side: str
    price: float
    size: float
    status: str = "open"

@dataclass
class Position:
    market_id: str
    size: float
    avg_price: float

# Mock order class for paper trading
class MockOrder:
    """Mock order object for paper trading mode."""
    def __init__(self, order_id: str, market_id: str = "", side: str = "", price: float = 0, size: float = 0):
        self.order_id = order_id
        self.market_id = market_id
        self.side = side
        self.price = price
        self.size = size
        self.status = "SUCCESS"

    def __getitem__(self, key):
        """Support dict-style access for compatibility with executor."""
        key_mapping = {
            "orderID": "order_id",
            "order_id": "order_id",
            "marketID": "market_id",
            "market_id": "market_id",
            "side": "side",
            "price": "price",
            "size": "size",
            "status": "status"
        }
        attr_name = key_mapping.get(key, key)
        if hasattr(self, attr_name):
            return getattr(self, attr_name)
        raise KeyError(f"MockOrder has no key '{key}'")

    def get(self, key, default=None):
        """Support dict.get() method for compatibility."""
        try:
            return self[key]
        except KeyError:
            return default


class PolymarketClient:
    def __init__(self, **kwargs):
        self._api_key = kwargs.get('api_key', '')
        # Respect PAPER_TRADING env var as a hard override — prevents real CLOB init
        # even if paper_trading=False somehow slips through config or CLI args.
        env_paper = os.environ.get('PAPER_TRADING', '').lower() in ('true', '1', 'yes')
        self._paper_trading = kwargs.get('paper_trading', True) or env_paper
        self.GAMMA_URL = "https://gamma-api.polymarket.com"
        self.CLOB_URL = "https://clob.polymarket.com"
        self._logger = logging.getLogger(__name__)

        # Extract credentials from secrets/config
        self._private_key = ''
        self._api_secret = ''
        self._api_passphrase = ''
        self._proxy_address = ''
        self._chain_id = 137
        secrets = kwargs.get('secrets')
        if secrets:
            self._private_key = getattr(secrets, 'polygon_private_key', '')
            self._api_secret = getattr(secrets, 'polymarket_api_secret', '')
            self._api_passphrase = getattr(secrets, 'polymarket_api_passphrase', '')
            self._proxy_address = getattr(secrets, 'polymarket_proxy_address', '')
        config = kwargs.get('config')
        if config:
            self._chain_id = getattr(config, 'chain_id', 137)

        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        self._session = aiohttp.ClientSession(headers=headers)

        # Cache: market_id -> clob_token_id (populated during market discovery)
        self._token_id_map: Dict[str, str] = {}

        # Initialize real CLOB client for live trading
        self._clob = None
        if not self._paper_trading and self._private_key and CLOB_AVAILABLE:
            try:
                # Use signature_type=2 (proxy wallet) when proxy address is provided
                # — this is the correct mode when funds live in Polymarket's contract wallet
                use_proxy = bool(self._proxy_address)
                self._clob = ClobClient(
                    host=self.CLOB_URL,
                    key=self._private_key,
                    chain_id=self._chain_id,
                    signature_type=2 if use_proxy else 0,
                    funder=self._proxy_address if use_proxy else None,
                )

                # Use pre-provided API creds if passphrase is in secrets,
                # otherwise derive them from the private key (slower, needs network)
                if self._api_key and self._api_secret and self._api_passphrase:
                    from py_clob_client.clob_types import ApiCreds
                    api_creds = ApiCreds(
                        api_key=self._api_key,
                        api_secret=self._api_secret,
                        api_passphrase=self._api_passphrase,
                    )
                    self._logger.info("Using pre-provided Polymarket API credentials")
                else:
                    api_creds = self._clob.create_or_derive_api_creds()
                    self._logger.info("Derived API credentials from private key")

                self._clob.set_api_creds(api_creds)

                # Set ERC-1155 conditional token approvals so the CLOB exchange
                # contract can transfer YES tokens on SELL orders.  Without this
                # every SELL returns 400 "not enough balance / allowance".
                # Must be called once per key/proxy pair; the approval is on-chain.
                try:
                    self._clob.update_allowances()
                    self._logger.info("Token allowances set — SELL orders enabled")
                except Exception as allow_err:
                    self._logger.warning(
                        f"update_allowances() failed: {allow_err} — "
                        f"SELL orders may fail with allowance error"
                    )

                mode = "proxy wallet" if use_proxy else "EOA"
                self._logger.info(f"CLOB client initialized for LIVE order placement ({mode} mode)")
            except Exception as e:
                self._logger.error(f"Failed to initialize CLOB client: {e}")
                self._clob = None

        self._logger.info("PolymarketClient initialized")

    async def get_price(self, market_id: str) -> float:
        """Get current YES price via Gamma API."""
        url = f"{self.GAMMA_URL}/markets/{market_id}"
        try:
            async with self._session.get(url) as resp:
                data = await resp.json()
                prices = data.get('outcomePrices', ['0.5', '0.5'])
                if isinstance(prices, str): prices = json.loads(prices)
                return float(prices[0])
        except:
            return 0.5

    async def get_market_resolution(self, market_id: str) -> Optional[int]:
        """
        Check if a market has resolved and return the outcome.

        Returns:
            1  if YES won
            0  if NO won
            None if still active / unknown
        """
        url = f"{self.GAMMA_URL}/markets/{market_id}"
        try:
            async with self._session.get(url) as resp:
                data = await resp.json()
                if data.get("active", True):
                    return None  # Still trading
                prices = data.get("outcomePrices", ["0.5", "0.5"])
                if isinstance(prices, str):
                    prices = json.loads(prices)
                yes_price = float(prices[0])
                if yes_price >= 0.99:
                    return 1  # YES won
                if yes_price <= 0.01:
                    return 0  # NO won
                return None  # Inconclusive
        except Exception:
            return None

    async def health_check(self) -> bool:
        return True

    async def get_markets(
        self,
        categories=None,
        limit: int = 1000,
        query=None
    ) -> List[Market]:
        """
        Slug-based Discovery: Fetches weather markets by predictable slug pattern.
        """
        from datetime import datetime, timedelta

        target_cities = [
            # International (Tomorrow.io)
            'london', 'seoul', 'incheon', 'new-york', 'nyc',
            # US cities (NWS)
            'atlanta', 'miami', 'seattle', 'dallas', 'chicago',
        ]

        slugs_to_fetch = []
        for i in range(3):  # Today, tomorrow, day after
            date = datetime.utcnow() + timedelta(days=i)
            date_str = date.strftime('%B-%-d-%Y').lower()
            for city in target_cities:
                slugs_to_fetch.append(f"highest-temperature-in-{city}-on-{date_str}")

        weather_markets = []

        self._logger.info("Fetching weather markets via slug-based discovery...")

        for slug in slugs_to_fetch:
            try:
                url = f"{self.GAMMA_URL}/events/slug/{slug}"
                async with self._session.get(url) as resp:
                    if resp.status != 200:
                        continue

                    event = await resp.json()

                    for market in event.get('markets', []):
                        if not market.get('acceptingOrders', True):
                            continue
                        if market.get('closed', False):
                            continue

                        try:
                            converted = self._convert_gamma_market(market, event)
                            weather_markets.append(converted)
                        except Exception as e:
                            self._logger.warning(f"Failed to convert market: {e}")
                            continue

            except Exception as e:
                self._logger.debug(f"Slug {slug} not found or error: {e}")
                continue

        self._logger.info(f"Discovery complete: {len(weather_markets)} temperature buckets found")
        return weather_markets

    def _convert_gamma_market(self, market: Dict, event: Dict) -> Market:
        raw_prices = market.get('outcomePrices', '["0.5", "0.5"]')
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
        yes_p = float(prices[0]) if (prices and len(prices) > 0) else 0.5

        # Extract YES token ID for CLOB order placement
        raw_token_ids = market.get('clobTokenIds', '[]')
        token_ids = json.loads(raw_token_ids) if isinstance(raw_token_ids, str) else (raw_token_ids or [])
        clob_token_id = str(token_ids[0]) if token_ids else ''

        market_id = market.get('id', '')

        # Cache the mapping so place_limit_order can look it up by market_id
        if clob_token_id:
            self._token_id_map[market_id] = clob_token_id

        return Market(
            id=market_id,
            question=market.get('question', event.get('title', '')),
            condition_id=market.get('conditionId', event.get('id', '')),
            yes_price=yes_p,
            no_price=round(1.0 - yes_p, 4),
            last_trade_price=market.get('lastTradePrice', yes_p),
            active=market.get('acceptingOrders', True) and not market.get('closed', False),
            category='weather',
            volume=float(market.get('volume', 0) or 0),
            description=market.get('description', event.get('description', '')),
            liquidity_rewards_enabled=market.get('liquidityRewardsEnabled', False),
            clob_token_id=clob_token_id,
        )

    async def cancel_all_orders(self, *args, **kwargs):
        """Cancel all open orders."""
        if self._paper_trading or self._clob is None:
            self._logger.info("Cleanup: All pending paper orders cancelled.")
            return True
        try:
            await asyncio.to_thread(self._clob.cancel_all)
            self._logger.info("LIVE: All open orders cancelled.")
        except Exception as e:
            self._logger.warning(f"cancel_all_orders error: {e}")
        return True

    async def place_limit_order(self, market_id: str, side: str, price: float, size: float, **kwargs):
        """
        Place a limit order.
        - Paper mode: returns MockOrder (no real submission)
        - Live mode: signs and submits via Polymarket CLOB API
        """
        if self._paper_trading or self._clob is None:
            self._logger.debug(f"PAPER ORDER: {side.upper()} {size} @ ${price} on {market_id}")
            return MockOrder(
                order_id=f"mock-paper-{market_id[:8]}",
                market_id=market_id,
                side=side,
                price=price,
                size=size
            )

        # Look up the YES token ID (needed by CLOB API)
        token_id = self._token_id_map.get(market_id, '')
        if not token_id:
            self._logger.error(f"No clobTokenId found for market {market_id[:8]} — cannot place order")
            raise ValueError(f"Missing CLOB token ID for market {market_id}")

        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=side.upper(),  # "BUY" or "SELL"
            )
            signed_order = await asyncio.to_thread(self._clob.create_order, order_args)
            resp = await asyncio.to_thread(self._clob.post_order, signed_order, OrderType.GTC)

            order_id = resp.get('orderID', resp.get('id', 'unknown')) if isinstance(resp, dict) else str(resp)
            self._logger.info(
                f"LIVE ORDER submitted: {side} ${size:.2f} @ ${price:.4f} "
                f"on {market_id[:8]}... (orderID: {order_id})"
            )

            return MockOrder(
                order_id=order_id,
                market_id=market_id,
                side=side,
                price=price,
                size=size
            )
        except Exception as e:
            self._logger.error(f"LIVE order placement failed for {market_id[:8]}: {e}")
            raise

    async def place_market_order(self, market_id: str, side: str, size: float, price: float = None, **kwargs):
        """Place a market order (used for exits). GTC in live mode."""
        if self._paper_trading or self._clob is None:
            self._logger.debug(f"PAPER MARKET ORDER: {side.upper()} {size} @ market on {market_id}")
            return MockOrder(
                order_id=f"mock-market-{market_id[:8]}",
                market_id=market_id,
                side=side,
                price=price or 0.5,
                size=size
            )

        token_id = self._token_id_map.get(market_id, '')
        if not token_id:
            raise ValueError(f"Missing CLOB token ID for market {market_id}")

        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price or 0.5,
                size=size,
                side=side.upper(),  # "BUY" or "SELL"
            )
            signed_order = await asyncio.to_thread(self._clob.create_order, order_args)
            resp = await asyncio.to_thread(self._clob.post_order, signed_order, OrderType.FOK)

            order_id = resp.get('orderID', resp.get('id', 'unknown')) if isinstance(resp, dict) else str(resp)
            status = resp.get('status', '') if isinstance(resp, dict) else ''

            # FOK orders that can't fill are returned with status 'unmatched' —
            # they are immediately cancelled and nothing is sold on Polymarket.
            # Raise so execute_exit() marks executed=False and skips the Supabase EXIT log.
            if status not in ('matched', 'delayed', ''):
                raise Exception(f"FOK order not filled on Polymarket (status: '{status}', orderID: {order_id})")

            self._logger.info(f"LIVE MARKET ORDER (FOK): {side} ${size:.2f} on {market_id[:8]}... (status: {status}, orderID: {order_id})")

            return MockOrder(
                order_id=order_id,
                market_id=market_id,
                side=side,
                price=price or 0.5,
                size=size
            )
        except Exception as e:
            self._logger.error(f"LIVE market order failed for {market_id[:8]}: {e}")
            raise

    async def get_order_book(self, token_id: str):
        """Mock order book — used by farming logic."""
        return {"bids": [{"price": "0.50", "size": "100"}], "asks": [{"price": "0.52", "size": "100"}]}

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order."""
        if self._paper_trading or self._clob is None:
            self._logger.debug(f"PAPER CANCEL: Order {order_id}")
            return True
        try:
            await asyncio.to_thread(self._clob.cancel, {"orderID": order_id})
            self._logger.info(f"LIVE: Cancelled order {order_id}")
            return True
        except Exception as e:
            self._logger.warning(f"cancel_order {order_id} failed: {e}")
            return False

    def from_gamma_event(self, event: Dict) -> Market:
        raw_prices = event.get('outcomePrices', '["0.5", "0.5"]')
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
        yes_p = float(prices[0]) if (prices and len(prices) > 0) else 0.5

        return Market(
            id=event.get('id', ''),
            question=event.get('question', ''),
            condition_id=event.get('conditionId', event.get('id', '')),
            yes_price=yes_p,
            no_price=round(1.0 - yes_p, 4),
            last_trade_price=yes_p,
            active=True,
            category='weather',
            volume=float(event.get('volume', 0) or 0),
        )

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


def create_polymarket_client(*args, **kwargs) -> 'PolymarketClient':
    """Factory function — passes all kwargs straight to PolymarketClient."""
    return PolymarketClient(**kwargs)
