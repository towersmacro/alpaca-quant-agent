"""
Module: trading_monitor.py

Real-time monitoring engine for active trades.

This module listens to incoming quote ticks (bid/ask updates) and checks
all open trades against their Stop Loss and Take Profit levels.

Key Logic:
- Uses side-aware checks: Long exits on Bid, Short exits on Ask.
- Triggers `close_trade` on the TradeManager when levels are breached.
- Maintains a 'closing' set to prevent duplicate exit attempts.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("quant_agent_trading")


class TradingMonitor:
    """
    Monitors open trades against real-time price quotes.
    """
    
    def __init__(
        self,
        trade_manager: Any,
        symbol: str,
    ) -> None:
        """
        Args:
            trade_manager: Instance of TradeManager to call close_trade on.
            symbol: The symbol this monitor is responsible for.
        """
        self.trade_manager = trade_manager
        self.symbol = symbol
        self.last_price: Optional[float] = None
        self._closing_trades: set[str] = set()

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        """Safely convert value to float, handling None or 'N/A'."""
        try:
            if value in (None, "N/A"):
                return None
            return float(value)
        except Exception:
            return None

    async def on_quote_update(self, quote_tick: dict) -> None:
        """
        Callback for new quote data.
        
        1. Updates internal last_price (midpoint).
        2. Calls check_levels with new bid/ask.
        """
        bid_price = self._safe_float(quote_tick.get("bid_price"))
        ask_price = self._safe_float(quote_tick.get("ask_price"))
        if bid_price is None or ask_price is None:
            return
        self.last_price = (bid_price + ask_price) / 2.0
        # Keep a best-effort latest midpoint for close fallback logging.
        self.trade_manager.last_candle_close = self.last_price
        await self.check_levels(bid_price, ask_price)

    async def check_levels(self, bid_price: float, ask_price: float) -> None:
        """
        Compare current bid/ask against SL/TP of all open trades.
        
        Args:
            bid_price: Current best bid (sell price for longs).
            ask_price: Current best ask (buy price for shorts).
        """
        if not self.trade_manager:
            return

        for trade_id, trade in list(self.trade_manager.open_trades.items()):
            if trade_id in self._closing_trades:
                continue
            if trade_id not in self.trade_manager.open_trades:
                continue

            try:
                # Debug monitor checks (commented out intentionally for cleaner logs).
                # Re-enable these blocks when you want per-tick SL/TP comparison traces.
                # if trade.direction == "long":
                #     logger.info(
                #         "[MONITOR][CHECK] %s %s LONG | bid=%.6f | SL=%.6f | TP=%.6f | sl_hit=%s | tp_hit=%s",
                #         self.symbol,
                #         trade_id,
                #         bid_price,
                #         trade.stop_loss_price,
                #         trade.take_profit_price,
                #         bid_price <= trade.stop_loss_price,
                #         bid_price >= trade.take_profit_price,
                #     )
                # elif trade.direction == "short":
                #     logger.info(
                #         "[MONITOR][CHECK] %s %s SHORT | ask=%.6f | SL=%.6f | TP=%.6f | sl_hit=%s | tp_hit=%s",
                #         self.symbol,
                #         trade_id,
                #         ask_price,
                #         trade.stop_loss_price,
                #         trade.take_profit_price,
                #         ask_price >= trade.stop_loss_price,
                #         ask_price <= trade.take_profit_price,
                #     )

                exit_signal = trade.check_exit_with_quote(bid_price, ask_price)
                if not exit_signal:
                    continue
                
                # Mark as closing to prevent re-entry in this loop
                self._closing_trades.add(trade_id)
                
                reason = exit_signal.get("reason", "Exit triggered")
                executable_price = exit_signal.get("price")
                
                logger.warning(
                    "[MONITOR] %s %s at %.6f (bid=%.6f ask=%.6f)",
                    self.symbol,
                    reason,
                    executable_price if executable_price is not None else self.last_price,
                    bid_price,
                    ask_price,
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
