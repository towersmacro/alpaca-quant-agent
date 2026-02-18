"""
Module: alpaca_socket.py

Provides an asynchronous WebSocket client for streaming real-time data from Alpaca.
It supports subscribing to both trade and quote updates for multiple crypto symbols.
Uses the v1beta3/crypto stream.
"""

import json
import logging
import asyncio
import os
from pathlib import Path
from typing import Any, Callable, Optional, List, Dict
import websockets
import dotenv
import config

# Load env
env_path = Path(__file__).parent.parent / "env.env"
dotenv.load_dotenv(env_path)

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

logger = logging.getLogger('quant_agent_trading')

class AlpacaWebSocket:
    """
    Async WebSocket client for Alpaca Crypto streams.
    
    Handles:
    - Connection management (auto-reconnect).
    - Authentication.
    - Subscription to Trades and/or Quotes.
    - Dispatching updates to callback functions.
    """

    def __init__(
        self,
        symbols: List[str],
        on_trade: Optional[Callable[[Dict[str, Any]], Any]] = None,
        on_quote: Optional[Callable[[Dict[str, Any]], Any]] = None,
        subscribe_quotes: bool = False,
    ) -> None:
        """
        Initialize the WebSocket client.
        
        Args:
            symbols: List of symbols to subscribe to (e.g., ['BTC/USD']).
            on_trade: Async callback for trade updates.
            on_quote: Async callback for quote updates.
            subscribe_quotes: Whether to subscribe to quote channel (bid/ask).
        """
        self.api_key = ALPACA_API_KEY
        self.secret_key = ALPACA_SECRET_KEY
        self.symbols = symbols
        self.on_trade = on_trade
        self.on_quote = on_quote
        self.subscribe_quotes = subscribe_quotes
        self.ws_url = config.ALPACA_CRYPTO_WS_URL
        
        self.should_reconnect = True
        self.ws = None

    async def _authenticate(self, ws):
        """
        Send authentication payload to Alpaca.
        Required immediately after connection.
        """
        auth_payload = {
            "action": "auth",
            "key": self.api_key,
            "secret": self.secret_key
        }
        await ws.send(json.dumps(auth_payload))
        logger.info("Sent Alpaca auth request")

    async def _subscribe(self, ws):
        """
        Send subscription payload for trades and optionally quotes.
        Called after successful authentication.
        """
        # Alpaca expects symbols like "BTC/USD"
        sub_payload = {
            "action": "subscribe",
            "trades": self.symbols,
        }
        if self.subscribe_quotes:
            sub_payload["quotes"] = self.symbols
        await ws.send(json.dumps(sub_payload))
        logger.info(f"Sent subscription for {self.symbols}")

    async def _handle_message(self, message: str, ws):
        """
        Process incoming WebSocket messages.
        
        Messages are JSON arrays containing one or more update objects.
        Dispatches 't' (trade) and 'q' (quote) messages to registered callbacks.
        """
        try:
            data = json.loads(message)
        except Exception:
            return

        if not isinstance(data, list):
            data = [data]

        for item in data:
            msg_type = item.get("T")
            
            if msg_type == "success":
                if item.get("msg") == "connected":
                    logger.info("Connected to Alpaca Stream")
                    # Authentication is handled in start_async flow
                elif item.get("msg") == "authenticated":
                    logger.info("Authenticated successfully")
                    await self._subscribe(ws)
            
            elif msg_type == "subscription":
                logger.info("Subscription confirmed")

            elif msg_type == "error":
                logger.error(f"Alpaca Stream Error: {item}")

            elif msg_type == "t":  # Trade tick
                trade_tick = {
                    "symbol": item.get("S"),
                    "price": float(item.get("p", 0)),
                    "size": float(item.get("s", 0)),
                    "timestamp": item.get("t"),
                    "type": "trade",
                }

                if self.on_trade:
                    try:
                        res = self.on_trade(trade_tick)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as e:
                        logger.error(f"Trade callback error: {e}")
            elif msg_type == "q":  # Quote tick
                quote_tick = {
                    "symbol": item.get("S"),
                    "bid_price": float(item.get("bp", 0)),
                    "bid_size": float(item.get("bs", 0)),
                    "ask_price": float(item.get("ap", 0)),
                    "ask_size": float(item.get("as", 0)),
                    "timestamp": item.get("t"),
                    "type": "quote",
                }

                if self.on_quote:
                    try:
                        res = self.on_quote(quote_tick)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as e:
                        logger.error(f"Quote callback error: {e}")

    async def start_async(self):
        """
        Main run loop:
        1. Connects to WebSocket.
        2. Authenticates.
        3. Listens for messages.
        4. Handles automatic reconnection on failure.
        """
        while self.should_reconnect:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self.ws = ws
                    logger.info(f"Connecting to {self.ws_url}...")
                    
                    # Authenticate immediately
                    await self._authenticate(ws)
                    
                    async for message in ws:
                        await self._handle_message(message, ws)
                        
            except Exception as e:
                logger.error(f"WebSocket connection failed: {e}")
                await asyncio.sleep(5) # Backoff
            
            if self.should_reconnect:
                logger.info("Reconnecting to Alpaca...")

    def stop(self):
        """Signal the loop to stop reconnecting."""
        self.should_reconnect = False
        # Connection will close when async with block exits or next loop check
