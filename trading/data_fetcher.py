"""
Module: data_fetcher.py

Provides asynchronous functions to fetch historical crypto bar data from Alpaca.
Handles pagination, timeframe normalization, and data formatting.
"""

import aiohttp
import asyncio
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Optional, Union
import os 
import dotenv
from pathlib import Path
import config

# Load env vars from env.env in the same directory
env_path = Path(__file__).parent / "env.env"
dotenv.load_dotenv(env_path)

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

def _build_headers() -> dict:
    """Construct headers for Alpaca API requests."""
    return {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }

def _bars_to_df(symbol: str, bars: list) -> pd.DataFrame:
    """
    Convert raw bar dicts from the API into a clean DataFrame.
    
    Args:
        symbol: The ticker symbol.
        bars: List of dictionaries from Alpaca API response.
        
    Returns:
        pd.DataFrame: Cleaned data with standard columns (timestamp, open, high, low, close, volume).
    """
    if not bars:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "symbol"])
    df = pd.DataFrame(bars)
    df.rename(columns={"t": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
    df["symbol"] = symbol
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    keep = ["timestamp", "open", "high", "low", "close", "volume", "symbol"]
    return df[[c for c in keep if c in df.columns]].sort_values("timestamp").reset_index(drop=True)

async def fetch_historical_crypto_async(
    symbol: str,
    start_date: Union[datetime, str],
    end_date: Union[datetime, str],
    timeframe_: str,
) -> pd.DataFrame:
    """
    True async fetch of crypto bars from Alpaca v1beta3 REST API using aiohttp.
    Handles pagination automatically.
    
    Args:
        symbol: e.g., "BTC/USD"
        start_date: Start datetime or ISO string.
        end_date: End datetime or ISO string.
        timeframe_: e.g., "1Min", "5Min", "1H", "1D".
        
    Returns:
        pd.DataFrame: Historical bar data.
    """
    # Normalize timeframe string to Alpaca API format
    tf_map = {
        "1Min": "1Min", "5Min": "5Min", "15Min": "15Min",
        "30Min": "30Min", "1H": "1Hour", "1D": "1Day",
    }
    timeframe_api = tf_map.get(timeframe_, timeframe_)

    # Normalize dates to ISO 8601 UTC strings
    def to_iso(dt):
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "symbols": symbol,
        "timeframe": timeframe_api,
        "start": to_iso(start_date),
        "end": to_iso(end_date),
        "limit": 1000,
        "sort": "asc",
    }

    all_bars = []
    headers = _build_headers()

    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            async with session.get(config.ALPACA_CRYPTO_BARS_URL, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

            bars_for_symbol = data.get("bars", {}).get(symbol, [])
            all_bars.extend(bars_for_symbol)

            # Pagination
            next_token = data.get("next_page_token")
            if not next_token:
                break
            params["page_token"] = next_token

    return _bars_to_df(symbol, all_bars)

async def get_latest_crypto_price(symbol: str) -> Optional[float]:
    """
    Fetch the latest trade price for a crypto symbol from Alpaca.
    Used for initial price checks or fallbacks.
    """
    url = f"https://data.alpaca.markets/v1beta3/crypto/us/latest/trades"
    headers = _build_headers()
    params = {"symbols": symbol}

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                trades = data.get("trades", {})
                if symbol in trades:
                    return float(trades[symbol]["p"])
    except Exception:
        pass
    return None

def _timeframe_minutes(timeframe_: str) -> int:
    """Helper to convert various timeframe strings to integer minutes."""
    tf = str(timeframe_).strip().lower()
    mapping = {
        "1min": 1,
        "5min": 5,
        "15min": 15,
        "30min": 30,
        "1hour": 60,
        "4hour": 240,
        "1day": 1440,
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
    }
    return mapping.get(tf, 1)

async def fetch_historical_data_async(symbol: str, bars: int = 1, timeframe_: str = "1Min") -> pd.DataFrame:
    """
    High-level wrapper to fetch the last N bars ending now.
    
    Args:
        symbol: e.g., "BTC/USD"
        bars: Number of bars to fetch.
        timeframe_: e.g., "1Min", "5m".
        
    Returns:
        pd.DataFrame: Historical data.
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=max(1, bars) * _timeframe_minutes(timeframe_))
    df = await fetch_historical_crypto_async(
        symbol=symbol,
        start_date=start,
        end_date=now,
        timeframe_=timeframe_,
    )
    df.drop(columns=["vwap", "trade_count"], inplace=True, errors="ignore")
    return df