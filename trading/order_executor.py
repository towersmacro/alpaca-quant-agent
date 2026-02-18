"""
Module: order_executor.py

Handles all interactions with the Alpaca Trading API.
Responsible for:
- Submitting market orders.
- Closing positions.
- Fetching account info, positions, and order details.
- Polling orders until they are filled or reach a terminal state.
"""

import asyncio
import httpx
import logging
import time
import os
from datetime import datetime, timezone
from pathlib import Path
import dotenv
from typing import Optional, Dict, Any, List

# Local imports
from .retry_handler import retry_http_request, RetryStrategy
import config

# Load env vars
env_path = "env.env"
dotenv.load_dotenv(env_path)

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
TRADING_MODE = os.getenv("TRADING_MODE", "paper")

logger = logging.getLogger('quant_agent_trading')

# Shared async HTTP client for Alpaca API to reuse connections
_http_client: Optional[httpx.AsyncClient] = None


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """
    Parse Alpaca ISO timestamp (can include nanoseconds + trailing Z).
    Converts to a standard timezone-aware datetime object (UTC).
    """
    if not value:
        return None
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        # Normalize nanoseconds to microseconds for datetime.fromisoformat.
        if "." in s:
            main, frac_tz = s.split(".", 1)
            tz_part = ""
            frac_part = frac_tz
            if "+" in frac_tz:
                frac_part, tz_part = frac_tz.split("+", 1)
                tz_part = "+" + tz_part
            elif "-" in frac_tz:
                frac_part, tz_part = frac_tz.split("-", 1)
                tz_part = "-" + tz_part
            frac_part = (frac_part[:6]).ljust(6, "0")
            s = f"{main}.{frac_part}{tz_part}"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def _get_alpaca_base_url() -> str:
    """Get Alpaca API base URL based on trading mode (paper vs live)."""
    if str(TRADING_MODE).lower() == "paper":
        return "https://paper-api.alpaca.markets"
    return "https://api.alpaca.markets"

def _get_alpaca_headers() -> Dict[str, str]:
    """Get Alpaca API authentication headers."""
    return {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }

async def _get_http_client() -> httpx.AsyncClient:
    """Get or create shared httpx.AsyncClient for Alpaca API."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            base_url=_get_alpaca_base_url(),
            headers=_get_alpaca_headers(),
            timeout=30.0,
        )
    return _http_client

async def close_http_client():
    """Close the shared HTTP client. Call on system shutdown."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None

async def get_positions() -> Dict[str, Dict[str, Any]]:
    """
    Return all open positions indexed by symbol.
    
    Returns:
        Dict where key is symbol and value is position details (qty, side, pnl, etc.)
    """
    try:
        client = await _get_http_client()
        response = await retry_http_request(client, "get", "/v2/positions", RetryStrategy.READ_OPERATION)
        positions_data = response.json()
        
        position_dict = {}
        for pos in positions_data:
            symbol = pos["symbol"]
            qty = float(pos["qty"])
            position_dict[symbol] = {
                'symbol': symbol,
                'qty': qty,
                'side': 'long' if qty > 0 else 'short',
                'market_value': float(pos.get("market_value", 0)),
                'avg_entry_price': float(pos.get("avg_entry_price", 0)),
                'current_price': float(pos.get("current_price", 0)),
                'unrealized_pl': float(pos.get("unrealized_pl", 0))
            }
        return position_dict
    except Exception as e:
        logger.error(f"Error getting positions: {e}")
        return {}

async def get_account_info() -> Optional[Dict[str, Any]]:
    """Return a snapshot of account-level metrics (equity, buying power, etc.)."""
    try:
        client = await _get_http_client()
        response = await retry_http_request(client, "get", "/v2/account", RetryStrategy.READ_OPERATION)
        account = response.json()
        
        return {
            'cash': float(account.get("cash", 0)),
            'equity': float(account.get("equity", 0)),
            'portfolio_value': float(account.get("portfolio_value", 0)),
            'buying_power': float(account.get("buying_power", 0)),
            'daytrade_count': account.get("daytrade_count", 0),
            'absolute_investment': float(account.get("long_market_value", 0)) - float(account.get("short_market_value", 0))
        }
    except Exception as e:
        logger.error(f"Error getting account info: {e}")
        return None

async def get_order_details(order_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch specific order details by ID.
    
    Returns:
        Dict with status, filled_qty, filled_avg_price, timestamps, etc.
    """
    try:
        client = await _get_http_client()
        response = await retry_http_request(client, "get", f"/v2/orders/{order_id}", RetryStrategy.READ_OPERATION)
        order = response.json()
        
        return {
            'id': order.get("id"),
            'symbol': order.get("symbol"),
            'qty': float(order.get("qty", 0)),
            'filled_qty': float(order.get("filled_qty", 0)),
            'side': order.get("side"),
            'type': order.get("type"),
            'status': order.get("status"),
            'submitted_at': _parse_iso_datetime(order.get("submitted_at")),
            'filled_at': _parse_iso_datetime(order.get("filled_at")),
            'filled_avg_price': float(order.get("filled_avg_price")) if order.get("filled_avg_price") is not None else None,
        }
    except Exception as e:
        logger.error(f"Error getting order details for {order_id}: {e}")
        return None

async def _poll_order_until_terminal(
    order_id: str,
    timeout_seconds: float = config.ORDER_POLL_TIMEOUT_SECONDS,
    poll_interval_seconds: float = config.ORDER_POLL_INTERVAL_SECONDS
):
    """
    Poll an order until it reaches a terminal state (filled, canceled, etc.) or times out.
    
    Returns:
        The final order object state, or None if timed out/failed.
    """
    deadline = time.time() + timeout_seconds
    last_status = None
    TERMINAL_STATES = {"filled", "canceled", "rejected", "expired", "done_for_day"}
    
    while time.time() < deadline:
        try:
            order = await get_order_details(order_id)
            if not order:
                await asyncio.sleep(poll_interval_seconds)
                continue
            
            status = order.get("status")
            if status != last_status:
                logger.debug(f"Order {order_id} status: {status}")
                last_status = status

            if status in TERMINAL_STATES:
                return order

            await asyncio.sleep(poll_interval_seconds)
        except Exception as e:
            logger.error(f"Polling error for order {order_id}: {e}")
            await asyncio.sleep(poll_interval_seconds)
            continue

    try:
        return await get_order_details(order_id)
    except Exception:
        return None

async def get_current_price(symbol: str) -> Optional[float]:
    """
    Return the latest traded price for the symbol using Alpaca Data API.
    Useful for calculating order quantities based on notional value.
    """
    # Ensure symbol format is correct for Alpaca (e.g. BTC/USD)
    
    url = "https://data.alpaca.markets/v1beta3/crypto/us/latest/trades"
    params = {"symbols": symbol}
    
    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url, headers=headers, params=params)
            if response.status_code != 200:
                logger.error(f"Alpaca Data API error: {response.status_code} {response.text}")
                return None
            
            data = response.json()
            trades = data.get("trades", {})
            if symbol in trades:
                return float(trades[symbol]["p"])
            
            logger.warning(f"No trade data found for {symbol} in Alpaca response")
            return None
            
    except Exception as e:
        logger.error(f"Error getting price from Alpaca for {symbol}: {e}")
        return None

async def submit_market_order(
    symbol: str,
    side: str,
    notional_value: Optional[float] = None,
    qty: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    """
    Submit a market order.
    
    Args:
        symbol: e.g. "BTC/USD"
        side: "buy" or "sell"
        notional_value: Dollar amount to trade (will be converted to qty).
        qty: Exact quantity to trade.
        
    Returns:
        The filled order object, or None if failed.
    """
    alpaca_symbol = symbol.replace("/", "")
    current_price = await get_current_price(symbol)
    
    try:
        if notional_value is not None:  
            if not current_price or current_price <= 0:
                logger.warning(f"Could not get valid current price for {symbol}")
                return None
            
            qty = round(notional_value / current_price, 6)
        
        price_str = f"${current_price:.4f}" if current_price is not None else "n/a"
        logger.info(f"Submitting {side.upper()} order: ({qty} {symbol} @ ~{price_str})")
        
        time_in_force = "gtc" if "/" in symbol else "day"
        
        order_payload = {
            "symbol": alpaca_symbol,
            "qty": str(qty),
            "side": side.lower(),
            "type": "market",
            "time_in_force": time_in_force
        }
        
        client = await _get_http_client()
        response = await retry_http_request(client, "post", "/v2/orders", RetryStrategy.ORDER_SUBMIT, json=order_payload)
        actual_order = response.json()
        order_id = actual_order["id"]
        
        # Wait for fill
        order = await _poll_order_until_terminal(order_id)
        if not order:
            logger.error(f"Failed to retrieve final state for order id {order_id}")
            return None

        logger.info(f"Order executed successfully: {order_id}")
        logger.info(f"Status: {order.get('status')}")

        return order
    except Exception as e:
        logger.error(f"[{symbol}] Unexpected error submitting order: {e}", exc_info=True)
        return None

async def close_position(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Close an existing position for a symbol.
    
    1. Checks if position exists.
    2. Sends DELETE request to Alpaca (market sell/buy to close).
    3. Polls until closed.
    """
    alpaca_symbol = symbol.replace("/", "")
    
    try:
        client = await _get_http_client()
        try:
            response = await retry_http_request(client, "delete", f"/v2/positions/{alpaca_symbol}", RetryStrategy.ORDER_SUBMIT)
            close_order = response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {'status': 'no_position', 'symbol': alpaca_symbol}
            raise
        order_id = close_order["id"]
        
        logger.info(f"Closing position: {alpaca_symbol}")
        
        order = await _poll_order_until_terminal(order_id)
        if not order:
            logger.error(f"Failed to retrieve final state for close order id {order_id}")
            return None
        
        logger.info(f"Position close order completed: {order_id} - Status: {order.get('status')}")
        return order
    except Exception as e:
        logger.error(f"Error closing position for {alpaca_symbol}: {e}")
        return None
