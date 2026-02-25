"""
QuantAgent — Headless Live Trading System

Entry point. Launches the multi-symbol trading engine which:
1. Polls candle boundaries per symbol.
2. Runs QuantAgent (LLM multi-agent) or mock signals for trade decisions.
3. Executes orders via Alpaca.
4. Monitors positions in real-time via WebSocket for SL/TP exits.
5. Persists trade records to PostgreSQL.

Usage:
    python main.py
"""

import asyncio
import logging

import config
from core.engine import MultiSymbolTrader, build_symbol_configs

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("live_trading.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("main")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def main():
    symbol_configs = build_symbol_configs()
    logger.info("Starting QuantAgent with %d symbol(s)", len(symbol_configs))
    for cfg in symbol_configs:
        logger.info("  %s (%s) — $%.0f capital, SL=%.1f%%, TP=%.1f%%",
                     cfg["symbol"], cfg["timeframe"],
                     cfg["capital"], cfg["stop_loss_pct"]*100, cfg["take_profit_pct"]*100)
    
    orchestrator = MultiSymbolTrader(symbol_configs)
    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
