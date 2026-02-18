"""
Module: trade_manager.py

Manages the lifecycle of trades for a single symbol in a live trading environment.
It handles opening trades (via order executor), tracking open positions,
and closing trades (either manually or via signal).

It integrates with:
- OrderExecutor: For submitting orders to the broker (Alpaca).
- DBHandler: For persisting trade state to PostgreSQL.
- Trade: For in-memory state and SL/TP logic.
"""

import uuid
import logging
from datetime import datetime, timezone
from typing import Dict
import pandas as pd

from .trade import Trade
from .db_handler import insert_trade, update_trade_close
from .order_executor import (
    submit_market_order,
    close_position,
)

logger = logging.getLogger('quant_agent_trading')


def _coerce_datetime(value):
    """
    Helper to ensure a value is a timezone-aware datetime object.
    Handles ISO strings and naive datetimes (assumes UTC).
    """
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
    
    Responsibilities:
    - Opening new trades (market orders).
    - Persisting trade data to DB.
    - Closing trades and updating DB.
    - Maintaining in-memory list of open trades.
    """
    
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.open_trades: Dict[str, Trade] = {}
        self.closed_trades: Dict[str, Trade] = {}
        self.last_candle_close = None

    async def open_trade(self, direction: str, quantity: float = None, notional: float = None, 
                         sl_pct: float = 0.02, tp_pct: float = 0.04, timeframe: str = None, 
                         historical_data: pd.DataFrame = None):
        """
        Execute a market order to open a trade.

        1. Submits market order to Alpaca.
        2. Waits for fill details (price, qty, time).
        3. Creates a Trade object.
        4. Persists to DB.
        
        Args:
            direction: 'long' or 'short'.
            quantity: Exact quantity to trade (optional).
            notional: Dollar amount to trade (optional).
            sl_pct: Stop Loss percentage.
            tp_pct: Take Profit percentage.
            timeframe: Candle timeframe of the signal.
            historical_data: Data context for the signal.
            
        Returns:
            Trade: The created trade object, or None if failed.
        """
        logger.info(f"Attempting to open {direction} trade for {self.symbol}")
        
        # Submit order
        side = "buy" if direction.lower() == "long" else "sell"
        order_result = await submit_market_order(self.symbol, side, notional_value=notional, qty=quantity)
        
        if not order_result or order_result['status'] not in ('filled', 'partially_filled'):
            logger.error(f"Failed to open trade for {self.symbol}")
            return None

        # Create trade object
        uid = str(uuid.uuid4())
        entry_price = order_result.get('filled_avg_price') or order_result.get('current_price')
        entry_time = _coerce_datetime(order_result.get('filled_at')) or datetime.now(timezone.utc)
        filled_qty = order_result.get('filled_qty') or order_result.get('qty')

        if entry_price is None:
            logger.error(
                "Order filled but no fill price returned for %s. order_result=%s",
                self.symbol,
                order_result,
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

        # Persist to DB
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
        """
        Close an existing trade.

        1. Closes position at Alpaca.
        2. Updates Trade object with exit details.
        3. Updates DB record.
        4. Moves trade from open to closed list.
        
        Args:
            uid: Trade ID to close.
            reason: Reason for closure.
            fallback_exit_price: Price to use if API doesn't return fill price (e.g. simulation).
        """
        trade = self.open_trades.get(uid)
        if not trade:
            logger.warning(f"Trade {uid} not found")
            return False
            
        logger.info(f"Closing trade {uid} due to {reason}")
        
        # Close position at broker
        close_result = await close_position(self.symbol)
        
        if not close_result or close_result['status'] not in ('filled', 'partially_filled'):
             # Check if it was already closed (no_position)
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

        # Finalize trade object
        pnl = trade.close(reason, exit_time, exit_price)
        await update_trade_close(trade.uid, trade.exit_price, trade.exit_time, trade.exit_reason, pnl)
        
        # Move to closed
        self.closed_trades[uid] = trade
        del self.open_trades[uid]
        return True

    async def close_all(self, reason="Manual Close"):
        """
        Emergency or shutdown method to close all open trades tracked by this manager.
        """
        for uid in list(self.open_trades.keys()):
            await self.close_trade(uid, reason)

