"""
Real-time monitoring engine for active trades.

Consumes quote ticks (bid/ask) and checks all open trades against SL/TP levels.
Uses side-aware logic: Long exits on Bid, Short exits on Ask.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("quant_agent_trading")


class TradingMonitor:
    """Monitors open trades against real-time price quotes."""
    
    def __init__(self, trade_manager: Any, symbol: str) -> None:
        self.trade_manager = trade_manager
        self.symbol = symbol
        self.last_price: Optional[float] = None
        self._closing_trades: set[str] = set()

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            if value in (None, "N/A"):
                return None
            return float(value)
        except Exception:
            return None

    async def on_quote_update(self, quote_tick: dict) -> None:
        bid_price = self._safe_float(quote_tick.get("bid_price"))
        ask_price = self._safe_float(quote_tick.get("ask_price"))
        if bid_price is None or ask_price is None:
            return
        self.last_price = (bid_price + ask_price) / 2.0
        self.trade_manager.last_candle_close = self.last_price
        await self.check_levels(bid_price, ask_price)

    async def check_levels(self, bid_price: float, ask_price: float) -> None:
        if not self.trade_manager:
            return

        for trade_id, trade in list(self.trade_manager.open_trades.items()):
            if trade_id in self._closing_trades:
                continue
            if trade_id not in self.trade_manager.open_trades:
                continue

            try:
                exit_signal = trade.check_exit_with_quote(bid_price, ask_price)
                if not exit_signal:
                    continue
                
                self._closing_trades.add(trade_id)
                
                reason = exit_signal.get("reason", "Exit triggered")
                executable_price = exit_signal.get("price")
                
                logger.warning(
                    "[MONITOR] %s %s at %.6f (bid=%.6f ask=%.6f)",
                    self.symbol, reason,
                    executable_price if executable_price is not None else self.last_price,
                    bid_price, ask_price,
                )
                try:
                    await self.trade_manager.close_trade(
                        trade_id, reason, fallback_exit_price=executable_price
                    )
                finally:
                    self._closing_trades.discard(trade_id)
            except Exception as exc:
                self._closing_trades.discard(trade_id)
                logger.error("[MONITOR] Error checking trade %s: %s", trade_id, exc)
