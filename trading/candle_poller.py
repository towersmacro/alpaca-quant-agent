"""
Module: candle_poller.py

Handles the timing and fetching of historical candle data.
Ensures that data fetches are aligned with candle boundaries (e.g., 5-minute marks)
to provide clean, complete candles for analysis.
"""

import logging
from datetime import datetime, timezone
from typing import List
import pandas as pd
from trading.data_fetcher import fetch_historical_data_async

logger = logging.getLogger("quant_agent_trading")

class AsyncCandlePoller:
    """
    Asynchronous poller for Crypto candles.
    
    Features:
    - Calculates time until next candle close.
    - Fetches latest N candles aligned to the requested timeframe.
    - Handles timeframe format mapping for Alpaca API.
    """

    def __init__(self, symbol: str, timeframe: str):
        """
        Args:
            symbol: Trading pair (e.g., 'BTC/USD').
            timeframe: Candle size (e.g., '5m', '1h').
        """
        self.symbol = symbol
        self.timeframe = timeframe
        self.interval_seconds = self._get_interval_seconds(timeframe)

    def _get_interval_seconds(self, timeframe: str) -> int:
        """Convert timeframe string to seconds for boundary calculations."""
        tf = timeframe.lower()
        if tf == "1m": return 60
        if tf == "5m": return 300
        if tf == "15m": return 900
        if tf == "30m": return 1800
        if tf == "1h": return 3600
        if tf == "4h": return 14400
        if tf == "1d": return 86400
        return 60 # Default to 1 minute

    def compute_seconds_until_next_candle(self) -> float:
        """
        Calculate seconds remaining until the next candle closes.
        Uses UTC timestamp to align with standard market intervals.
        """
        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())
        
        # Align to interval boundaries (e.g., next 5-minute mark)
        next_boundary_ts = ((now_ts // self.interval_seconds) + 1) * self.interval_seconds
        
        seconds_remaining = next_boundary_ts - now_ts
        return max(0.0, float(seconds_remaining))

    def get_fetch_schedule_seconds(self) -> List[float]:
        """
        Return a list of wait times (in seconds) for the next fetch.
        Currently returns just the time until the next boundary.
        """
        seconds_to_close = self.compute_seconds_until_next_candle()
        return [seconds_to_close]

    async def fetch_latest_candles(self, limit: int = 45) -> pd.DataFrame:
        """
        Fetch the latest N candles from the API.
        
        Args:
            limit: Number of candles to retrieve.
            
        Returns:
            pd.DataFrame: OHLCV data with 'Datetime' index, or empty DF on error.
        """
        # Explicit mapping for Alpaca timeframe format
        tf_map = {
            "1m": "1Min",
            "5m": "5Min",
            "15m": "15Min",
            "30m": "30Min",
            "1h": "1Hour",
            "4h": "4Hour",
            "1d": "1Day"
        }
        # Use lower() to match keys, default to original if not found
        alpaca_tf = tf_map.get(self.timeframe.lower(), self.timeframe)

        try:
            # Fetch data using the centralized data fetcher
            df = await fetch_historical_data_async(
                symbol=self.symbol,
                bars=limit,
                timeframe_=alpaca_tf,
            )
            
            if df.empty:
                return pd.DataFrame()
            
            # Rename columns to match what run_live_trading.py and strategies expect (Title Case)
            df = df.rename(
                columns={
                    "timestamp": "Datetime",
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                    "volume": "Volume",
                }
            )
            
            # Ensure we only return the requested number of bars
            return df.tail(limit).reset_index(drop=True)
        except Exception as e:
            logger.error(f"[{self.symbol}] Error fetching candles: {e}")
            return pd.DataFrame()
