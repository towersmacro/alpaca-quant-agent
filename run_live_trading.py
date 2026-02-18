"""
Script: run_live_trading.py

Entry point for the Multi-Symbol Live Trading System.

Architecture:
- MultiSymbolTrader: Orchestrator that manages shared resources (WebSocket, DB pool)
  and spawns independent SymbolTrader instances.
- SymbolTrader: Independent trading loop for a single symbol. Handles:
    - Candle polling (via AsyncCandlePoller)
    - Signal generation (QuantAgent or Mock)
    - Order execution (TradeManager)
    - Real-time monitoring (TradingMonitor consuming WebSocket quotes)

Usage:
    python run_live_trading.py
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pandas as pd

import config
from static_util import generate_kline_image, generate_trend_image
from trading.api.alpaca_socket import AlpacaWebSocket
from trading.api.trading_monitor import TradingMonitor
from trading.data_fetcher import fetch_historical_crypto_async
from trading.db_handler import close_pool, ensure_trades_table
from trading.trade_manager import TradeManager
from trading.mock_signal import generate_mock_signal
from trading.candle_poller import AsyncCandlePoller
from web_interface import WebTradingAnalyzer

# Configure Logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("live_trading.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("live_trader")
# Suppress noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class SymbolTrader:
    """
    Manages the trading lifecycle for a single symbol.
    Runs an infinite loop that:
    1. Waits for the next candle boundary.
    2. Fetches historical data.
    3. Generates a signal (Agent or Mock).
    4. Executes trades.
    5. Monitors active trades via WebSocket updates.
    """
    
    def __init__(self, cfg: Dict[str, Any]):
        self.symbol = cfg["symbol"]
        self.exec_symbol = cfg.get("exec_symbol", self.symbol)
        self.timeframe = cfg.get("timeframe", config.TIMEFRAME)
        self.capital = float(cfg.get("capital", config.CAPITAL))
        self.stop_loss_pct = float(cfg.get("stop_loss_pct", config.STOP_LOSS_PCT))
        self.take_profit_pct = float(cfg.get("take_profit_pct", config.TAKE_PROFIT_PCT))

        self.analyzer = WebTradingAnalyzer()
        self.manager = TradeManager(self.exec_symbol)
        self.monitor = TradingMonitor(trade_manager=self.manager, symbol=self.exec_symbol)
        
        # Queue for receiving real-time quotes from the main WebSocket
        self.quote_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self.is_running = False
        
        # Initialize Poller for candle synchronization
        self.poller = AsyncCandlePoller(self.symbol, self.timeframe)

    async def _quote_queue_consumer(self):
        """
        Background task: Consumes symbol-specific quote ticks from the queue
        and feeds them to the TradingMonitor for SL/TP checks.
        """
        while self.is_running:
            quote_update = await self.quote_queue.get()
            try:
                await self.monitor.on_quote_update(quote_update)
            finally:
                self.quote_queue.task_done()

    def run_agent_analysis(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Run the QuantAgent analysis pipeline on the provided DataFrame.
        Generates charts and invokes the LangChain graph.
        """
        df_slice = df.tail(45).reset_index(drop=True)
        df_slice_dict = {
            "Datetime": df_slice["Datetime"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist(),
            "Open": df_slice["Open"].tolist(),
            "High": df_slice["High"].tolist(),
            "Low": df_slice["Low"].tolist(),
            "Close": df_slice["Close"].tolist(),
            "Volume": df_slice["Volume"].tolist() if "Volume" in df_slice else [],
        }

        p_image = generate_kline_image(df_slice_dict)
        t_image = generate_trend_image(df_slice_dict)

        initial_state = {
            "kline_data": df_slice_dict,
            "analysis_results": None,
            "messages": [],
            "time_frame": self.timeframe,
            "stock_name": self.symbol,
            "pattern_image": p_image["pattern_image"],
            "trend_image": t_image["trend_image"],
        }
        return self.analyzer.trading_graph.graph.invoke(initial_state)

    @staticmethod
    def parse_decision(decision_raw: str) -> Dict[str, Any]:
        """Extract JSON decision from Agent's output string."""
        try:
            start = decision_raw.find("{")
            end = decision_raw.rfind("}") + 1
            if start != -1 and end != 0:
                return json.loads(decision_raw[start:end])
        except Exception:
            return {}
        return {}

    async def loop(self):
        """
        Main execution loop for this symbol.
        """
        self.is_running = True
        logger.info("[%s] Starting flow (%s)", self.symbol, self.timeframe)

        # Start the quote consumer background task
        consumer_task = asyncio.create_task(self._quote_queue_consumer())
        
        try:
            while self.is_running:
                # 1. Wait for next candle boundary
                seconds_to_wait = self.poller.compute_seconds_until_next_candle()
                await asyncio.sleep(seconds_to_wait)
                
                if not self.is_running:
                    break

                try:
                    # 2. Fetch latest candles
                    df = await self.poller.fetch_latest_candles(limit=45)
                    if df.empty:
                        logger.warning("[%s] No data fetched, skipping analysis", self.symbol)
                        continue
                    
                    # Log last bar for confirmation
                    last_bar = df.iloc[-1]
                    logger.info("[%s] Last Bar: %s | Close: %s", self.symbol, last_bar['Datetime'], last_bar['Close'])

                    # 3. Run analysis to get decision
                    if config.USE_MOCK_SIGNALS:
                        final_state = generate_mock_signal()
                    else:
                        final_state = await asyncio.to_thread(self.run_agent_analysis, df)
                    
                    decision_data = self.parse_decision(final_state.get("final_trade_decision", ""))
                    decision = decision_data.get("decision", "").upper()
                    rr_ratio = decision_data.get("risk_reward_ratio")
                    
                    try:
                        if isinstance(rr_ratio, str) and ":" in rr_ratio:
                            rr_val = float(rr_ratio.split(":")[1])
                        else:
                            rr_val = float(rr_ratio)
                    except (ValueError, TypeError, IndexError):
                        rr_val = 2.0 # Default fallback if parsing fails

                    logger.info("[%s] Decision=%s RR=%s (Parsed: %.2f)", self.symbol, decision, rr_ratio, rr_val)

                    if decision in ["LONG", "SHORT"]:
                        dynamic_tp_pct = self.stop_loss_pct * rr_val
                        
                        await self.manager.open_trade(
                            direction=decision,
                            notional=self.capital,
                            sl_pct=self.stop_loss_pct,
                            tp_pct=dynamic_tp_pct,
                            timeframe=self.timeframe,
                            historical_data=df,
                        )

                except Exception as exc:
                    logger.error("[%s] Loop error: %s", self.symbol, exc, exc_info=True)
                    await asyncio.sleep(10) # Short retry sleep on error
                    
        finally:
            self.is_running = False
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass


class MultiSymbolTrader:
    """
    Orchestrator for multiple SymbolTrader instances.
    Manages the single shared WebSocket connection and routes data.
    """
    
    def __init__(self, symbol_configs: List[Dict[str, Any]]):
        # Create a SymbolTrader for each config entry
        self.trader_runtimes = [SymbolTrader(cfg) for cfg in symbol_configs]
        
        # Map symbol -> runtime for fast routing
        self.trader_by_symbol = {runtime.symbol: runtime for runtime in self.trader_runtimes}
        
        # Single WebSocket connection for all symbols
        self.socket = AlpacaWebSocket(
            symbols=list(self.trader_by_symbol.keys()),
            on_quote=self.on_quote_update,
            subscribe_quotes=True,
        )

    async def on_quote_update(self, quote_update: Dict[str, Any]):
        """
        Callback from AlpacaWebSocket.
        Routes the quote tick to the appropriate SymbolTrader's queue.
        """
        symbol = quote_update.get("symbol")
        runtime = self.trader_by_symbol.get(symbol)
        if runtime is None:
            return
        await runtime.quote_queue.put(quote_update)

    async def run(self):
        """
        Main system entry point.
        1. Initializes DB.
        2. Starts WebSocket.
        3. Launches all SymbolTrader loops.
        4. Handles graceful shutdown.
        """
        if not self.trader_runtimes:
            logger.error("No symbol configs provided. Exiting.")
            return

        # Ensure DB schema exists once for the whole trading system startup.
        await ensure_trades_table()

        socket_task = asyncio.create_task(self.socket.start_async())
        trader_tasks = [asyncio.create_task(runtime.loop()) for runtime in self.trader_runtimes]
        
        try:
            await asyncio.gather(*trader_tasks)
        finally:
            # Shutdown sequence
            self.socket.stop()
            socket_task.cancel()
            for runtime in self.trader_runtimes:
                runtime.is_running = False
            for task in trader_tasks:
                task.cancel()
            try:
                await socket_task
            except asyncio.CancelledError:
                pass
            await close_pool()


def build_symbol_configs() -> List[Dict[str, Any]]:
    """Load symbol configurations from config.py."""
    cfgs = getattr(config, "SYMBOL_CONFIGS", None)
    if cfgs:
        return cfgs
    # Fallback to single symbol constants
    return [
        {
            "symbol": config.SYMBOL,
            "exec_symbol": config.EXEC_SYMBOL,
            "timeframe": config.TIMEFRAME,
            "capital": config.CAPITAL,
            "stop_loss_pct": config.STOP_LOSS_PCT,
            "take_profit_pct": config.TAKE_PROFIT_PCT,
        }
    ]


if __name__ == "__main__":
    orchestrator = MultiSymbolTrader(build_symbol_configs())
    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        logger.info("Stopping multi-symbol trader...")
