"""
Defines the Trade class — state and logic for a single trade.
Handles SL/TP calculations with side-aware exit logic (long exits on bid, short exits on ask).
"""

from datetime import datetime
import pandas as pd
import logging
from .enums import TradeStatus

logger = logging.getLogger("quant_agent_trading")


class Trade:
    """
    Represents a single live trade.
    
    Tracks entry/exit details, status, and SL/TP thresholds.
    """
    
    def __init__(self, uid, entry_price: float, entry_time: datetime, direction: str, 
                 ticker: str, quantity: float = None, sl_pct: float = 0.02, tp_pct: float = 0.04,
                 historical_data: pd.DataFrame = None, timeframe: str = None,
                 should_persist: bool = True):
        self.uid = uid 
        self.ticker = ticker 
        self.entry_price = entry_price
        self.entry_time = entry_time 
        self.direction = direction.lower()
        self.quantity = quantity
        
        self.status = TradeStatus.OPEN
        self.exit_time = None
        self.exit_price = None
        self.exit_reason = None
        
        if self.direction == "long":
            self.stop_loss_price = entry_price * (1 - sl_pct)
            self.take_profit_price = entry_price * (1 + tp_pct)
        else:
            self.stop_loss_price = entry_price * (1 + sl_pct)
            self.take_profit_price = entry_price * (1 - tp_pct)
            
        self.timeframe = timeframe
        self._historical_data = historical_data
        
        logger.info(
            f"[TRADE] New {self.direction.upper()} | Entry: {entry_price} "
            f"| SL: {self.stop_loss_price:.2f} | TP: {self.take_profit_price:.2f}"
        )

    def check_exit(self, current_price: float):
        """Check SL/TP against a single price (e.g., last trade price)."""
        if self.status != TradeStatus.OPEN:
            return None

        if self.direction == "long":
            if current_price <= self.stop_loss_price:
                return {'action': 'exit', 'reason': 'SL hit', 'price': current_price}
            if current_price >= self.take_profit_price:
                return {'action': 'exit', 'reason': 'TP hit', 'price': current_price}
        elif self.direction == "short":
            if current_price >= self.stop_loss_price:
                return {'action': 'exit', 'reason': 'SL hit', 'price': current_price}
            if current_price <= self.take_profit_price:
                return {'action': 'exit', 'reason': 'TP hit', 'price': current_price}
                
        return None

    def check_exit_with_quote(self, bid_price: float, ask_price: float):
        """
        Side-aware exit check.
        LONG exits by SELLING at BID. SHORT exits by BUYING at ASK.
        """
        if self.status != TradeStatus.OPEN:
            return None

        if self.direction == "long":
            if bid_price <= self.stop_loss_price:
                return {"action": "exit", "reason": "SL hit", "price": bid_price}
            if bid_price >= self.take_profit_price:
                return {"action": "exit", "reason": "TP hit", "price": bid_price}
        elif self.direction == "short":
            if ask_price >= self.stop_loss_price:
                return {"action": "exit", "reason": "SL hit", "price": ask_price}
            if ask_price <= self.take_profit_price:
                return {"action": "exit", "reason": "TP hit", "price": ask_price}

        return None

    def close(self, reason: str, exit_time, exit_price: float):
        """Finalize trade closure. Returns realized PnL."""
        self.status = TradeStatus.CLOSE
        if isinstance(exit_time, str):
            try:
                self.exit_time = datetime.fromisoformat(str(exit_time).replace('Z', '+00:00'))
            except ValueError:
                self.exit_time = datetime.now()
        else:
            self.exit_time = exit_time
            
        self.exit_price = exit_price
        self.exit_reason = reason
        
        if self.direction == "long":
            pnl = (self.exit_price - self.entry_price) * self.quantity
        else:
            pnl = (self.entry_price - self.exit_price) * self.quantity
            
        logger.info(
            f"[TRADE] Closed {self.ticker} ({self.direction}) at {self.exit_price}. "
            f"PnL: {pnl:.2f}. Reason: {reason}"
        )
        return pnl
