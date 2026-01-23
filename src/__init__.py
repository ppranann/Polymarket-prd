"""
Polymarket Weather Sniping Bot
==============================
A Python-based trading bot for sniping underpriced weather markets
on Polymarket CLOB and farming Liquidity Rewards.

Modules:
- clients: API clients for Polymarket and Tomorrow.io
- Core modules: scanner, calculator, executor, alpha, risk, auto_exit, logger
"""

__version__ = "1.0.0"
__author__ = "Quant Developer"

from .main import run_bot, TradingBot

__all__ = ["run_bot", "TradingBot"]

