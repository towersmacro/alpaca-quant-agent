<p align="center">
<b>Alpaca Quant Agent: Multi-Agent Live Trading on Alpaca</b>
<br><br>
🚀 Alpaca Quant Agent — An independent enhanced version built on the <a href="https://github.com/Y-Research-SBU/QuantAgent">QuantAgent</a> framework for Alpaca users who want to run AI agents to trade on their accounts, extending it with real-time Alpaca (REST + WebSocket), crypto support, automated trading, and PostgreSQL persistence.
<br><br>
<em>Disclaimer:</em> This project is provided solely for educational and research purposes. It is not financial, investment, or trading advice. Trading involves risk, and users should conduct their own due diligence before making any trading decisions.
<br><br>
📑 <a href="#quick-start">Quick Start</a> · 📐 <a href="#architecture">Architecture</a> · 📡 <a href="#data-flow">Data Flow</a> · ⚙️ <a href="#execution">Execution</a> · 📚 <a href="#reference">Reference</a>
</p>

---

<a id="quick-start"></a>
# QuantAgent Headless Trader

A lightweight, multi-symbol live trading engine. It currently uses a multi-agent LLM strategy (QuantAgent) but is designed to be modular so you can plug in any strategy.

## Quick Start

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Setup Trading Keys (Alpaca)**  
   Create a `.env` file in the root directory:
   ```bash
   ALPACA_API_KEY=your_key
   ALPACA_SECRET_KEY=your_secret
   TRADING_MODE=paper              # or 'live'
   ```

3. **Setup Strategy Keys (LLM)**  
   Open `strategy/default_config.py` and enter your API key directly:
   ```python
   DEFAULT_CONFIG = {
       "agent_llm_provider": "openai",
       "api_key": "sk-...",  # Put your OpenAI Key here
       # ...
   }
   ```

4. **Configure Symbols**  
   Edit `config.py` to set trading pairs, allocation percentage, and risk parameters:
   ```python
   SYMBOL_CONFIGS = [
       {
           "symbol": "BTC/USD",
           "timeframe": "1m",
           "capital_pct": 0.10,   # 10% of startup Alpaca balance
           "stop_loss_pct": 0.02,
           "take_profit_pct": 0.04,
       }
   ]
   ```

5. **Run**
   ```bash
   python main.py
   ```

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

<a id="reference"></a>
### 📚 Reference

This project is inspired by and builds upon:

- **QuantAgent** — [Y-Research-SBU/QuantAgent](https://github.com/Y-Research-SBU/QuantAgent) · Multi-agent LLM framework for high-frequency trading (indicators, pattern, trend, decision agents).

If you use QuantAgent in your work, you may cite the original paper:

```text
@article{xiong2025quantagent,
  title   = {QuantAgent: Price-Driven Multi-Agent LLMs for High-Frequency Trading},
  author  = {Fei Xiong and Xiang Zhang and Aosong Feng and Siqi Sun and Chenyu You},
  journal = {arXiv preprint arXiv:2509.09995},
  year    = {2025}
}
```
