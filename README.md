# QuantAgent Headless Trader

A lightweight, multi-symbol live trading engine. It currently uses a multi-agent LLM strategy (QuantAgent) but is designed to be modular so you can plug in any strategy.

## Quick Start

1.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

2.  **Setup Trading Keys (Alpaca)**
    Create a `.env` file in the root directory:
    ```
    ALPACA_API_KEY=your_key
    ALPACA_SECRET_KEY=your_secret
    TRADING_MODE=paper              # or 'live'
    ```

3.  **Setup Strategy Keys (LLM)**
    Open `strategy/default_config.py` and enter your API key directly:
    ```python
    DEFAULT_CONFIG = {
        "agent_llm_provider": "openai",
        "api_key": "sk-...",  # Put your OpenAI Key here
        # ...
    }
    ```

4.  **Configure Symbols**
    Edit `config.py` to set your trading pairs, capital, and risk parameters:
    ```python
    SYMBOL_CONFIGS = [
        {
            "symbol": "BTC/USD",
            "timeframe": "1m",
            "capital": 100.0,
            "stop_loss_pct": 0.02,
            "take_profit_pct": 0.04,
        }
    ]
    ```

5.  **Run**
    ```bash
    python main.py
    ```

## How It Works

The system runs a continuous loop for each symbol:
1.  **Waits** for the next candle to close (e.g., exact 5-minute mark).
2.  **Fetches** the latest candle data from Alpaca.
3.  **Analyzes** the data to get a `LONG`, `SHORT`, or `HOLD` signal.
4.  **Executes** the trade via Alpaca if a signal is generated.
5.  **Monitors** the trade in real-time using WebSockets to trigger Stop Loss or Take Profit immediately.

## Swapping the Strategy

The trading engine (`core/`) is separate from the strategy (`strategy/`).

To connect a different bot or algorithm instead of QuantAgent:

1.  Open `core/engine.py`.
2.  Locate the `SymbolTrader` class.
3.  Replace the `TradingGraph` initialization and the `run_agent_analysis` call with your own bot's logic.

**Example:**

```python
# In core/engine.py

# 1. Initialize your bot
self.my_bot = MyCustomStrategy() 

# 2. In the loop(), get the signal
# final_state = await asyncio.to_thread(self.run_agent_analysis, df)  <-- DELETE THIS
decision = self.my_bot.get_signal(df)                                 # <-- ADD THIS
```

Your bot just needs to accept a DataFrame of candles and return a decision (`LONG`/`SHORT`) and optionally a risk ratio.
