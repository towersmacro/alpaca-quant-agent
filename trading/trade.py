"""
Module: trade.py

Defines the `Trade` class which encapsulates the state and logic for a single trade.
It handles Stop Loss (SL) and Take Profit (TP) calculations and checks, specifically
implementing side-aware exit logic for realistic execution against bid/ask quotes.
"""

from datetime import datetime
import pandas as pd
import logging
from .enums import TradeStatus

# Get logger
logger = logging.getLogger('quant_agent_trading')

class Trade:
    """
    Represents a single live trade.
    
    Tracks:
    - Entry details (price, time, quantity)
    - Exit details (price, time, reason)
    - Status (OPEN/CLOSED)
    - SL/TP thresholds
    """
    
    def __init__(self, uid, entry_price: float, entry_time: datetime, direction: str, 
                 ticker: str, quantity: float = None, sl_pct: float = 0.02, tp_pct: float = 0.04,
                 historical_data: pd.DataFrame = None, timeframe: str = None,
                 should_persist: bool = True):
        """
        Initialize a new trade and calculate SL/TP levels.
        
        Args:
            uid: Unique identifier for the trade.
            entry_price: The average filled price of the entry order.
            entry_time: The timestamp of the entry fill.
            direction: 'long' or 'short'.
            ticker: The symbol traded (e.g., 'BTC/USD').
            quantity: The quantity of the asset traded.
            sl_pct: Stop Loss percentage (0.02 = 2%).
            tp_pct: Take Profit percentage (0.04 = 4%).
            historical_data: Optional DataFrame of data leading up to the trade.
            timeframe: The candle timeframe used for the signal.
            should_persist: Flag for persistence (handled by manager).
        """
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
        
        # Calculate SL/TP levels immediately upon initialization
        # Long: SL below entry, TP above entry
        # Short: SL above entry, TP below entry
        if self.direction == "long":
            self.stop_loss_price = entry_price * (1 - sl_pct)
            self.take_profit_price = entry_price * (1 + tp_pct)
        else:
            self.stop_loss_price = entry_price * (1 + sl_pct)
            self.take_profit_price = entry_price * (1 - tp_pct)
            
        self.timeframe = timeframe
        self._historical_data = historical_data
        
        # Persistence is managed by async trade manager in async context.
        
        logger.info(f"[TRADE] New {self.direction.upper()} | Entry: {entry_price} | SL: {self.stop_loss_price:.2f} | TP: {self.take_profit_price:.2f}")

    def check_exit(self, current_price: float):
        """
        Check if SL or TP is hit based on a single price (e.g., last trade price).
        
        Note: For more accurate simulation, use check_exit_with_quote() with bid/ask.
        
        Returns:
            dict: {'action': 'exit', 'reason': str, 'price': float} if triggered, else None.
        """
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
        Check exit conditions using side-aware quote logic.
        
        Logic:
        - LONG positions exit by SELLING at the BID price.
        - SHORT positions exit by BUYING at the ASK price.
        
        Args:
            bid_price: Current highest buy offer.
            ask_price: Current lowest sell offer.
            
        Returns:
            dict: {'action': 'exit', 'reason': str, 'price': float} if triggered, else None.
        """
        if self.status != TradeStatus.OPEN:
            return None

        if self.direction == "long":
            # Long exits on Bid
            if bid_price <= self.stop_loss_price:
                return {"action": "exit", "reason": "SL hit", "price": bid_price}
            if bid_price >= self.take_profit_price:
                return {"action": "exit", "reason": "TP hit", "price": bid_price}
        elif self.direction == "short":
            # Short exits on Ask
            if ask_price >= self.stop_loss_price:
                return {"action": "exit", "reason": "SL hit", "price": ask_price}
            if ask_price <= self.take_profit_price:
                return {"action": "exit", "reason": "TP hit", "price": ask_price}

        return None

    def close(self, reason: str, exit_time, exit_price: float):
        """
        Finalize the trade closure.
        
        Updates status, exit details, and calculates PnL.
        
        Args:
            reason: Reason for closure (e.g., 'SL hit', 'TP hit', 'Manual').
            exit_time: Timestamp of the exit.
            exit_price: The price at which the trade was closed.
            
        Returns:
            float: Realized PnL.
        """
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
        
        # Calculate PnL
        if self.direction == "long":
            pnl = (self.exit_price - self.entry_price) * self.quantity
        else:
            pnl = (self.entry_price - self.exit_price) * self.quantity
            
        logger.info(f"[TRADE] Closed {self.ticker} ({self.direction}) at {self.exit_price}. PnL: {pnl:.2f}. Reason: {reason}")
        return pnl
