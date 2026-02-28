"""
Manages the lifecycle of trades for a single symbol.
Coordinates with OrderExecutor for broker interaction and db_handler for persistence.
"""

import uuid
import logging
from datetime import datetime, timezone
from typing import Dict
import pandas as pd

from .trade import Trade
from .db_handler import insert_trade, update_trade_close
from .order_executor import submit_market_order, close_position

logger = logging.getLogger("quant_agent_trading")


def _coerce_datetime(value):
    """Ensure a value is a timezone-aware datetime object."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            s = value.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None
    return None


class TradeManager:
    """
    Manages active and closed trades for a specific symbol.
    
    Opens trades (market orders), persists to DB, closes trades, and
    maintains in-memory list of open trades.
    """
    
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.open_trades: Dict[str, Trade] = {}
        self.closed_trades: Dict[str, Trade] = {}
        self.last_candle_close = None

    async def open_trade(self, direction: str, quantity: float = None, notional: float = None, 
                         sl_pct: float = 0.02, tp_pct: float = 0.04, timeframe: str = None, 
                         historical_data: pd.DataFrame = None):
        logger.info(f"Attempting to open {direction} trade for {self.symbol}")
        
        side = "buy" if direction.lower() == "long" else "sell"
        order_result = await submit_market_order(self.symbol, side, notional_value=notional, qty=quantity)
        
        if not order_result or order_result['status'] not in ('filled', 'partially_filled'):
            logger.error(f"Failed to open trade for {self.symbol}")
            return None

        uid = str(uuid.uuid4())
        entry_price = order_result.get('filled_avg_price') or order_result.get('current_price')
        entry_time = _coerce_datetime(order_result.get('filled_at')) or datetime.now(timezone.utc)
        filled_qty = order_result.get('filled_qty') or order_result.get('qty')

        if entry_price is None:
            logger.error(
                "Order filled but no fill price returned for %s. order_result=%s",
                self.symbol, order_result,
            )
            return None
        
        trade = Trade(
            uid=uid,
            entry_price=entry_price,
            entry_time=entry_time,
            direction=direction.lower(),
            ticker=self.symbol,
            quantity=filled_qty,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            historical_data=historical_data,
            timeframe=timeframe
        )

        await insert_trade({
            'uid': trade.uid,
            'ticker': trade.ticker,
            'direction': trade.direction,
            'entry_price': trade.entry_price,
            'entry_time': trade.entry_time,
            'quantity': trade.quantity,
            'status': 'OPEN',
            'sl_price': trade.stop_loss_price,
            'tp_price': trade.take_profit_price,
            'timeframe': trade.timeframe
        })
        
        self.open_trades[uid] = trade
        logger.info(f"Trade opened: {uid} | {filled_qty} {self.symbol} @ {entry_price}")
        return trade

    async def close_trade(self, uid: str, reason: str, fallback_exit_price: float = None):
        trade = self.open_trades.get(uid)
        if not trade:
            logger.warning(f"Trade {uid} not found")
            return False
            
        logger.info(f"Closing trade {uid} due to {reason}")
        
        close_result = await close_position(self.symbol)
        
        if not close_result or close_result['status'] not in ('filled', 'partially_filled'):
            if close_result and close_result.get('status') == 'no_position':
                logger.info(f"Position for {self.symbol} already closed at broker.")
                exit_price = fallback_exit_price or self.last_candle_close or trade.entry_price
                exit_time = datetime.now()
            else:
                logger.error(f"Failed to close position for {self.symbol}")
                return False
        else:
            exit_price = (
                close_result.get('filled_avg_price')
                or close_result.get('current_price')
                or fallback_exit_price
                or self.last_candle_close
                or trade.entry_price
            )
            exit_time = _coerce_datetime(close_result.get('filled_at')) or datetime.now(timezone.utc)

        pnl = trade.close(reason, exit_time, exit_price)
        await update_trade_close(trade.uid, trade.exit_price, trade.exit_time, trade.exit_reason, pnl)
        
        self.closed_trades[uid] = trade
        del self.open_trades[uid]
        return True

    async def close_position_by_signal(
        self,
        exit_signal: str,
        fallback_exit_price: float = None,
    ) -> bool:
        """
        Close open trades for this symbol that match the provided direction.

        Used for signal reversal handling, e.g. close SHORT trades when a LONG
        signal appears (and vice versa).
        """
        direction = str(exit_signal or "").strip().lower()
        if direction not in ("long", "short"):
            logger.warning("Invalid exit signal '%s' for %s", exit_signal, self.symbol)
            return False

        target_uids = [
            uid for uid, trade in self.open_trades.items()
            if trade.ticker == self.symbol and trade.direction == direction
        ]
        if not target_uids:
            return False

        closed_any = False
        for uid in target_uids:
            try:
                if await self.close_trade(
                    uid=uid,
                    reason=f"Reverse signal ({direction.upper()})",
                    fallback_exit_price=fallback_exit_price,
                ):
                    closed_any = True
            except Exception as exc:
                logger.error(
                    "[%s] Error closing %s trade %s on reverse signal: %s",
                    self.symbol, direction, uid, exc
                )
        return closed_any

    async def close_all(self, reason="Manual Close"):
        for uid in list(self.open_trades.keys()):
            await self.close_trade(uid, reason)
