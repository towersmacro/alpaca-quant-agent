"""
Handles timing and fetching of historical candle data.
Ensures fetches align with candle boundaries for clean, complete candles.
"""

import logging
from datetime import datetime, timezone
from typing import List
import pandas as pd

from .data_fetcher import fetch_historical_data_async

logger = logging.getLogger("quant_agent_trading")


class AsyncCandlePoller:
    """
    Asynchronous poller for crypto candles.
    Calculates time until next candle close and fetches aligned data.
    """

    def __init__(self, symbol: str, timeframe: str):
        self.symbol = symbol
        self.timeframe = timeframe
        self.interval_seconds = self._get_interval_seconds(timeframe)

    def _get_interval_seconds(self, timeframe: str) -> int:
        tf = timeframe.lower()
        if tf == "1m": return 60
        if tf == "5m": return 300
        if tf == "15m": return 900
        if tf == "30m": return 1800
        if tf == "1h": return 3600
        if tf == "4h": return 14400
        if tf == "1d": return 86400
        return 60

    def compute_seconds_until_next_candle(self) -> float:
        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())
        next_boundary_ts = ((now_ts // self.interval_seconds) + 1) * self.interval_seconds
        seconds_remaining = next_boundary_ts - now_ts
        return max(0.0, float(seconds_remaining))

    def get_fetch_schedule_seconds(self) -> List[float]:
        seconds_to_close = self.compute_seconds_until_next_candle()
        return [seconds_to_close]

    async def fetch_latest_candles(self, limit: int = 45) -> pd.DataFrame:
        tf_map = {
            "1m": "1Min", "5m": "5Min", "15m": "15Min",
            "30m": "30Min", "1h": "1Hour", "4h": "4Hour", "1d": "1Day"
        }
        alpaca_tf = tf_map.get(self.timeframe.lower(), self.timeframe)

        try:
            df = await fetch_historical_data_async(
                symbol=self.symbol,
                bars=limit,
                timeframe_=alpaca_tf,
            )
            
            if df.empty:
                return pd.DataFrame()
            
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
            
            return df.tail(limit).reset_index(drop=True)
        except Exception as e:
            logger.error(f"[{self.symbol}] Error fetching candles: {e}")
            return pd.DataFrame()
