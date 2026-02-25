"""
Async functions to fetch historical crypto bar data from Alpaca.
Handles pagination, timeframe normalization, and data formatting.
"""

import aiohttp
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Optional, Union

import config


def _build_headers() -> dict:
    return {
        "APCA-API-KEY-ID": config.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY,
    }


def _bars_to_df(symbol: str, bars: list) -> pd.DataFrame:
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
    Async fetch of crypto bars from Alpaca v1beta3 REST API.
    Handles pagination automatically.
    """
    tf_map = {
        "1Min": "1Min", "5Min": "5Min", "15Min": "15Min",
        "30Min": "30Min", "1H": "1Hour", "1D": "1Day",
    }
    timeframe_api = tf_map.get(timeframe_, timeframe_)

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

            next_token = data.get("next_page_token")
            if not next_token:
                break
            params["page_token"] = next_token

    return _bars_to_df(symbol, all_bars)


async def get_latest_crypto_price(symbol: str) -> Optional[float]:
    url = "https://data.alpaca.markets/v1beta3/crypto/us/latest/trades"
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
    tf = str(timeframe_).strip().lower()
    mapping = {
        "1min": 1, "5min": 5, "15min": 15, "30min": 30,
        "1hour": 60, "4hour": 240, "1day": 1440,
        "1m": 1, "5m": 5, "15m": 15, "30m": 30,
        "1h": 60, "4h": 240, "1d": 1440,
    }
    return mapping.get(tf, 1)


async def fetch_historical_data_async(symbol: str, bars: int = 1, timeframe_: str = "1Min") -> pd.DataFrame:
    """High-level wrapper to fetch the last N bars ending now."""
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
