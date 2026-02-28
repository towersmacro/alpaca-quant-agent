<p align="center">
<b>Alpaca Quant Agent: Multi-Agent Live Trading on Alpaca</b>
<br><br>
🚀 Alpaca Quant Agent — An enhanced version built on the <a href="https://github.com/Y-Research-SBU/QuantAgent">QuantAgent</a> framework for Alpaca users who want to use AI to trade. It adds real-time Alpaca (REST + WebSocket), crypto support, automated trading, and saves trades to PostgreSQL.
<br><br>
<em>Disclaimer:</em> This is for educational and research only. It is not financial or trading advice. Trading has risk; do your own research before you trade.
<br><br>
📑 <a href="#quick-start">Quick Start</a> · 📐 <a href="#architecture">Architecture</a> · 📡 <a href="#data-flow">Data Flow</a> · ⚙️ <a href="#execution">Execution</a> · 🤝 <a href="#contributing">Contributing</a> · 📚 <a href="#reference">Reference</a>
</p>

---

<a id="quick-start"></a>
# QuantAgent Headless Trader

A lightweight, multi-symbol live trading engine. It uses the QuantAgent multi-agent LLM strategy by default but you can plug in your own strategy.

## Quick Start

1. **Create and activate a virtual environment**
   ```bash
   python -m venv venv
   ```
   Then activate it:
   - **macOS/Linux:** `source venv/bin/activate`
   - **Windows:** `venv\Scripts\activate`

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up environment variables**  
   Create a `.env` file in the project root with your Alpaca and OpenAI keys:
   ```bash
   ALPACA_API_KEY=your_alpaca_key
   ALPACA_SECRET_KEY=your_alpaca_secret
   TRADING_MODE=paper              # or 'live'
   OPENAI_API_KEY=sk-your-openai-key-here
   ```
   The bot uses `OPENAI_API_KEY` from .env, so you don’t need to edit any config.  
   **No OpenAI key yet?** See [Getting an OpenAI API key](#getting-an-openai-api-key) below.

4. **PostgreSQL (trade storage)**  
   The bot saves trades to PostgreSQL. Keep PostgreSQL running (e.g. locally or in Docker), then add the DB details to your `.env` (or leave them unset to use the defaults in `config.py`):
   ```bash
   POSTGRES_HOST=localhost
   POSTGRES_PORT=54322
   POSTGRES_DB=postgres
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=postgres
   ```
   If these are not set, `config.py` uses the values above. Change them to match your own database.

5. **Configure symbols**  
   Edit `config.py` to set trading pairs, allocation percentage, and risk:
   ```python
   SYMBOL_CONFIGS = [
       {
           "symbol": "BTC/USD",
           "timeframe": "1m",
           "capital_pct": 0.10,   # 10% of startup Alpaca cash
           "stop_loss_pct": 0.02,
           "take_profit_pct": 0.04,
       }
   ]
   ```

6. **Run**
   ```bash
   python main.py
   ```

<a id="getting-an-openai-api-key"></a>
### Getting an OpenAI API key

The bot uses OpenAI for the analysis. To get a key:

1. Sign up or log in at [platform.openai.com](https://platform.openai.com).
2. Go to **API keys** (in your profile or [here](https://platform.openai.com/api-keys)).
3. Click **Create new secret key**, give it a name (e.g. “Alpaca Quant Agent”), and copy the key.
4. Put it in your `.env` as `OPENAI_API_KEY=sk-...`.  
   Keep the key private and don’t put it in git.

## How It Works

The system runs a continuous loop for each symbol:

1. **Waits** for the next candle to close (e.g., exact 5-minute mark).
2. **Fetches** the latest candle data from Alpaca.
3. **Analyzes** the data with the strategy (QuantAgent or your own) to get a `LONG`, `SHORT`, or `HOLD` signal.
4. **Executes** the trade via Alpaca if a signal is generated (market order, then records entry and SL/TP).
5. **Monitors** the trade in real time using WebSockets: every quote tick is checked against your stop-loss and take-profit; when a level is hit, the position is closed and the trade is written to PostgreSQL.

So: time-aligned candles → strategy decision → Alpaca execution → live quote stream for exits.

## Swapping the Strategy

The trading engine (`core/`) is separate from the strategy (`strategy/`). To use a different bot or algorithm instead of QuantAgent:

1. Open `core/engine.py`.
2. Locate the `SymbolTrader` class.
3. Replace the `TradingGraph` initialization and the `run_agent_analysis` call with your own bot’s logic.

**Example:**

```python
# In core/engine.py

# 1. Initialize your bot
self.my_bot = MyCustomStrategy()

# 2. In the loop(), get the signal
# final_state = await asyncio.to_thread(self.run_agent_analysis, df)  <-- REMOVE
decision = self.my_bot.get_signal(df)  # <-- ADD: your bot returns LONG/SHORT (+ optional risk ratio)
```

Your bot only needs to accept a DataFrame of candles and return a decision (`LONG` / `SHORT`) and optionally a risk-reward ratio.

---

<a id="guide"></a>
# System Architecture & Data Flow Guide

<a id="architecture"></a>
## 1. Architecture Overview 📐

The system is a multi-symbol, asynchronous trading engine built on Python's `asyncio`. It operates as a single process managing concurrent event loops for independent trading pairs.

```text
[Main Process (run_live_trading.py)]
      |
      +---> [MultiSymbolTrader]
               |
               +---> {Shared Resource: Alpaca WebSocket}
               +---> {Shared Resource: PostgreSQL Pool}
               |
               +---> [SymbolTrader: BTC/USD]
               |        |
               |        +---> [AsyncCandlePoller]
               |        +---> [TradeManager]
               |        +---> [TradingMonitor]
               |
               +---> [SymbolTrader: ETH/USD]
                        |
                        +---> [AsyncCandlePoller]
                        +---> [TradeManager]
                        +---> [TradingMonitor]
```

**Key Characteristics:**
*   **Concurrency**: Uses `asyncio` tasks to handle WebSocket streaming and candle polling simultaneously without blocking.
*   **Isolation**: Each symbol (e.g., `BTC/USD`) runs in its own `SymbolTrader` instance with dedicated state, queues, and logic.
*   **Persistence**: PostgreSQL (via `asyncpg`) is used for atomic trade storage.

---

<a id="data-flow"></a>
## 2. Data Flow Pipelines 📡

The system manages two distinct data pipelines: **Real-Time (Ticks)** and **Historical (Candles)**.

### A. Real-Time Quote Stream (WebSocket)
**Purpose**: Risk Management (SL/TP Checks)

```text
[Alpaca Stream]
      |
      v
[AlpacaWebSocket]
      |  1. Ingestion
      |  - Connect to wss://stream.data.alpaca.markets/v1beta3/crypto/us
      |  - Subscribe to Quotes (Bid/Ask) for active symbols (BTC/USD, ETH/USD)
      |  - Deserialize JSON message
      v
[MultiSymbolTrader]
      |  2. Routing
      |  - Read Symbol "S": "BTC/USD"
      |  - Push to specific Symbol Queue
      v
[SymbolTrader.Queue]
      |
      v
[TradingMonitor]
      |  3. Processing
      |  - Pop Quote Tick
      |  - Extract Bid (for Long exit) / Ask (for Short exit)
      |  - Compare vs Stop Loss / Take Profit
      |  - Trigger Close if condition met
```

**Detailed Steps:**

1.  **Ingestion (`AlpacaWebSocket`)**:
    *   Establishes a single persistent connection to `wss://stream.data.alpaca.markets/v1beta3/crypto/us`.
    *   Subscribes to `quotes` (Bid/Ask) for all symbols defined in `config.SYMBOL_CONFIGS` (e.g., `['BTC/USD', 'ETH/USD']`).
    *   **Deserialization**: Parses incoming JSON arrays into Python dictionaries.
        *   *Example Payload*:
            ```json
            {
              "T": "q",
              "S": "BTC/USD",
              "bp": 64200.50,  // Bid Price
              "bs": 0.1,       // Bid Size
              "ap": 64201.00,  // Ask Price
              "as": 0.05,      // Ask Size
              "t": "2023-10-27T10:00:00.123Z"
            }
            ```

2.  **Routing (`MultiSymbolTrader`)**:
    *   Acts as the central traffic controller.
    *   Inspects the `"S"` (Symbol) field in the tick.
    *   Routes the payload to the specific `asyncio.Queue` belonging to that symbol's `SymbolTrader`.

3.  **Processing (`TradingMonitor`)**:
    *   Consumes ticks from the queue immediately.
    *   Extracts `bp` (Bid) for Long exit checks and `ap` (Ask) for Short exit checks.
    *   Compares against active trade limits (e.g., `if Bid <= Stop_Loss`).

### B. Historical Data Loop (REST Polling)
**Purpose**: Signal Generation (Strategy Analysis)

```text
[AsyncCandlePoller]
      |
      |  1. Timing (Wait T+5m)
      v
[DataFetcher]
      |
      |  2. Fetch (GET /bars)
      v
[Alpaca REST API]
      |
      |  3. Return OHLCV Data
      v
[DataFetcher]
      |
      |  4. Format DataFrame
      v
[SymbolTrader]
      |
      |  5. Run Analysis (QuantAgent)
      v
[TradeManager]
      |  6. Receive Signal (LONG/SHORT)
```

**Detailed Steps:**

1.  **Timing (`AsyncCandlePoller`)**:
    *   Calculates the precise sleep interval to align with UTC candle boundaries.
    *   *Logic*: `Sleep_Seconds = Interval - (Current_Epoch % Interval)`.
    *   Ensures analysis runs exactly when a candle closes (e.g., 12:00, 12:05, 12:10).

2.  **Fetching (`data_fetcher.py`)**:
    *   Executes an async GET request to `/v1beta3/crypto/us/bars`.
    *   Parameters: `symbol="BTC/USD"`, `timeframe="5Min"`, `limit=45`.
    *   **Normalization**: Converts raw API response into a Pandas DataFrame with standard columns: `[Datetime, Open, High, Low, Close, Volume]`.

3.  **Analysis (`SymbolTrader`)**:
    *   Passes the DataFrame to the Strategy Agent.
    *   Receives a structured decision object:
        ```json
        {
          "decision": "LONG",
          "risk_reward_ratio": 2.5,
          "confidence": 0.85
        }
        ```

---

<a id="execution"></a>
## 3. Execution Logic Details ⚙️

### Entry Logic

```text
[Strategy]
    |
    |  1. Signal: LONG
    v
[TradeManager]
    |
    |  2. Call submit_market_order(side="buy")
    v
[OrderExecutor]
    |
    |  3. POST /v2/orders (Alpaca API)
    |  <-- Order ID: 12345
    |
    |  4. Poll /v2/orders/12345
    |  <-- Status: FILLED, AvgPrice: 50000
    v
[TradeManager]
    |
    |  5. Create Trade Object
    |  6. INSERT INTO trades (DB)
    v
[TradingMonitor]
       (Trade added to watch list)
```

1.  **Signal**: Strategy returns `LONG`.
2.  **Order**: `TradeManager` calls `submit_market_order("buy")`.
3.  **Fill**: System polls for `filled` status to get the exact execution price.
4.  **Record**: Trade object created with `filled_avg_price` (e.g., $50,000).
5.  **Persist**: `INSERT` into DB with status `OPEN`.
6.  **Monitor**: Trade added to `TradingMonitor` watch list.

### Exit Logic (SL/TP)

```text
[TradingMonitor]
    |
    |  1. Check: Bid (49000) <= SL (49500)
    |  (Condition Met)
    v
[TradeManager]
    |
    |  2. Call close_trade(reason="SL Hit")
    v
[OrderExecutor]
    |
    |  3. DELETE /v2/positions/BTCUSD (Alpaca API)
    |  <-- Order Filled
    v
[TradeManager]
    |
    |  4. Get Exit Price: 48990
    |  5. UPDATE trades SET status='CLOSED', pnl=-1010 (DB)
    v
[Memory Cleanup]
       (Trade removed from active list)
```

1.  **Tick**: WebSocket receives `Quote(bid=49000, ask=49005)`.
2.  **Check**:
    *   **Long Logic**: `Bid (49000) <= SL_Price (49500)` -> **TRUE**.
3.  **Trigger**: Condition met.
4.  **Close**: `TradeManager` calls `close_position()`.
5.  **Update**: `UPDATE` DB with `exit_price` and `pnl`.
6.  **Cleanup**: Removed from memory and monitor list.

---

<a id="contributing"></a>
### Contributing

We welcome contributions. This repo is a standalone fork of [QuantAgent](https://github.com/Y-Research-SBU/QuantAgent) that adds Alpaca execution, cash-based sizing, and runs headless (no UI). If you add a feature, fix a bug, or improve the docs, open an issue or pull request. We’re happy to improve Alpaca support and multi-symbol trading with the community.

---

<a id="reference"></a>
### 📚 Reference

This project is built on:

- **QuantAgent** — [Y-Research-SBU/QuantAgent](https://github.com/Y-Research-SBU/QuantAgent) · Multi-agent LLM framework for high-frequency trading (indicators, pattern, trend, decision agents).

If you use QuantAgent in your work, you can cite the original paper:

```text
@article{xiong2025quantagent,
  title   = {QuantAgent: Price-Driven Multi-Agent LLMs for High-Frequency Trading},
  author  = {Fei Xiong and Xiang Zhang and Aosong Feng and Siqi Sun and Chenyu You},
  journal = {arXiv preprint arXiv:2509.09995},
  year    = {2025}
}
```
