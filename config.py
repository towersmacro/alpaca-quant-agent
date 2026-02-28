"""
Configuration for QuantAgent Live Trading.
Centralizes all settings: multi-symbol trading, API keys, database, and system flags.
"""

import os
from pathlib import Path
import dotenv

# Load environment variables from .env (or env.env) at project root
root = Path(__file__).parent
dotenv.load_dotenv(root / ".env")
dotenv.load_dotenv(root / "env.env")  # fallback if you use env.env

# --- Trading Strategy Settings ---
# Multi-symbol configuration. Each entry is a fully independent flow:
# polling, websocket stream, trade manager, and QuantAgent analyzer instance.
SYMBOL_CONFIGS = [
    {
        "symbol": "BTC/USD",       # Symbol for data fetch + websocket subscription
        "exec_symbol": "BTC/USD",  # Symbol for actual order placement (Alpaca)
        "timeframe": "1m",         # Candle timeframe: 1m, 5m, 15m, 1h, 4h, 1d
        "capital_pct": 0.10,       # 10% of startup Alpaca balance (fixed for entire run)
        "stop_loss_pct": 0.02,     # 2% Stop Loss distance
        "take_profit_pct": 0.04,   # 4% Take Profit distance
    },
        {
        "symbol": "ETH/USD",       # Symbol for data fetch + websocket subscription
        "exec_symbol": "ETH/USD",  # Symbol for actual order placement (Alpaca)
        "timeframe": "1m",         # Candle timeframe: 1m, 5m, 15m, 1h, 4h, 1d
        "capital_pct": 0.10,       # 10% of startup Alpaca balance (fixed for entire run)
        "stop_loss_pct": 0.02,     # 2% Stop Loss distance
        "take_profit_pct": 0.04,   # 4% Take Profit distance
    },
]

# Backward-compatible single-symbol defaults (used as fallback if SYMBOL_CONFIGS is empty)
SYMBOL = "BTC/USD"
EXEC_SYMBOL = "BTC/USD"
TIMEFRAME = "5m"
CAPITAL = 100.0
CAPITAL_PCT = float(os.getenv("CAPITAL_PCT", "0.10"))  # Legacy single-symbol percentage sizing

# --- Default Risk Parameters ---
STOP_LOSS_PCT = 0.02     # Default 2% Stop Loss
TAKE_PROFIT_PCT = 0.04   # Default 4% Take Profit

# --- API Keys (Loaded from env.env) ---
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TRADING_MODE = os.getenv("TRADING_MODE", "paper") # 'paper' or 'live'

# --- Database Settings (PostgreSQL) ---
# Connection details for asyncpg. Defaults provided for local development.
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "54322"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "postgres")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")

# --- System Settings ---
LOG_LEVEL = "INFO"

# --- Analysis Settings ---
USE_MOCK_SIGNALS = True  # Set to True to bypass QuantAgent and use random signals for testing

# --- Alpaca API Constants ---
# Endpoints for data and streaming
ALPACA_CRYPTO_BARS_URL = "https://data.alpaca.markets/v1beta3/crypto/us/bars"
ALPACA_CRYPTO_WS_URL = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"

# Order polling settings
ORDER_POLL_TIMEOUT_SECONDS = 30   # Max time to wait for order fill
ORDER_POLL_INTERVAL_SECONDS = 1   # Polling frequency for order status
