# System Architecture & Data Flow Guide

## 1. Architecture Overview

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

## 2. Data Flow Pipelines

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

## 3. Module Reference

| Module | Responsibility |
| :--- | :--- |
| **`run_live_trading.py`** | **Entry Point**. Initializes `MultiSymbolTrader`, sets up the DB connection, and spawns independent asyncio tasks for each symbol's loop. |
| **`config.py`** | **Configuration**. Static definitions for Symbols, API Keys, Database credentials, and Risk parameters. Loads from `env.env`. |
| **`api/alpaca_socket.py`** | **WebSocket Client**. Handles connection lifecycle, authentication, and subscription management. Reconnects automatically on disconnect. |
| **`candle_poller.py`** | **Synchronization**. Ensures data fetching occurs strictly at candle close times to prevent "repainting" or incomplete data analysis. |
| **`data_fetcher.py`** | **REST Client**. Async wrapper for Alpaca's Bar Data API. Handles pagination and DataFrame formatting. |
| **`trade_manager.py`** | **State Machine**. Manages the lifecycle of a trade (Open -> Monitor -> Close). Orchestrates DB updates and Order execution. |
| **`order_executor.py`** | **Broker Interface**. Submits Market Orders and closes positions via Alpaca Orders API v2. Handles order polling to confirm fills. |
| **`api/trading_monitor.py`** | **Risk Engine**. The consumer of the WebSocket queue. Performs high-frequency checks of current price vs. trade limits. |
| **`db_handler.py`** | **Database**. Manages `asyncpg` connection pool. Provides CRUD operations for the `trades` table. Ensures schema exists on startup. |

---

## 4. Database Schema (`trades` table)

| Column | Type | Description |
| :--- | :--- | :--- |
| `uid` | TEXT (PK) | Unique UUID for the trade. |
| `ticker` | TEXT | Symbol (e.g., BTC/USD). |
| `direction` | TEXT | LONG or SHORT. |
| `entry_price` | FLOAT | Filled average price from broker. |
| `entry_time` | TIMESTAMPTZ | UTC timestamp of entry fill. |
| `status` | TEXT | OPEN or CLOSED. |
| `sl_price` | FLOAT | Calculated Stop Loss level. |
| `tp_price` | FLOAT | Calculated Take Profit level. |
| `exit_price` | FLOAT | Filled average price at exit. |
| `pnl` | FLOAT | Realized Profit/Loss. |

---

## 5. Execution Logic Details

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
