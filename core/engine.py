"""
Multi-Symbol Live Trading Engine.

Architecture:
- MultiSymbolTrader: Orchestrator managing shared resources (WebSocket, DB pool)
  and spawning independent SymbolTrader instances.
- SymbolTrader: Independent trading loop for a single symbol (candle polling,
  signal generation, order execution, real-time monitoring).
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pandas as pd

import config
from strategy.utils.static_util import generate_kline_image, generate_trend_image
from strategy.graph import TradingGraph
from core.api.alpaca_socket import AlpacaWebSocket
from core.api.trading_monitor import TradingMonitor
from core.data_fetcher import fetch_historical_crypto_async
from core.db_handler import close_pool, ensure_trades_table
from core.trade_manager import TradeManager
from core.order_executor import get_account_info
from core.mock_signal import generate_mock_signal
from core.candle_poller import AsyncCandlePoller

logger = logging.getLogger("live_trader")


class SymbolTrader:
    """
    Manages the trading lifecycle for a single symbol.
    Runs an infinite loop: wait for candle -> fetch data -> analyse -> trade -> monitor.
    """
    
    def __init__(self, cfg: Dict[str, Any]):
        self.symbol = cfg["symbol"]
        self.exec_symbol = cfg.get("exec_symbol", self.symbol)
        self.timeframe = cfg.get("timeframe", config.TIMEFRAME)
        self.capital = float(cfg.get("capital", config.CAPITAL))
        self.capital_pct = float(cfg.get("capital_pct", 0.0))
        self.stop_loss_pct = float(cfg.get("stop_loss_pct", config.STOP_LOSS_PCT))
        self.take_profit_pct = float(cfg.get("take_profit_pct", config.TAKE_PROFIT_PCT))

        if not config.USE_MOCK_SIGNALS:
            self.strategy = TradingGraph()
        else:
            self.strategy = None

        self.manager = TradeManager(self.exec_symbol)
        self.monitor = TradingMonitor(trade_manager=self.manager, symbol=self.exec_symbol)
        
        self.quote_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self.is_running = False
        self.poller = AsyncCandlePoller(self.symbol, self.timeframe)

    async def _quote_queue_consumer(self):
        """Background task: feeds quote ticks to the TradingMonitor."""
        while self.is_running:
            quote_update = await self.quote_queue.get()
            try:
                await self.monitor.on_quote_update(quote_update)
            finally:
                self.quote_queue.task_done()

    def run_agent_analysis(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Run the QuantAgent analysis pipeline on the provided DataFrame."""
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
        return self.strategy.graph.invoke(initial_state)

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
        """Main execution loop for this symbol."""
        self.is_running = True
        logger.info("[%s] Starting flow (%s)", self.symbol, self.timeframe)

        consumer_task = asyncio.create_task(self._quote_queue_consumer())
        
        try:
            while self.is_running:
                seconds_to_wait = self.poller.compute_seconds_until_next_candle()
                await asyncio.sleep(seconds_to_wait)
                
                if not self.is_running:
                    break

                try:
                    df = await self.poller.fetch_latest_candles(limit=45)
                    if df.empty:
                        logger.warning("[%s] No data fetched, skipping analysis", self.symbol)
                        continue
                    
                    last_bar = df.iloc[-1]
                    logger.info("[%s] Last Bar: %s | Close: %s", self.symbol, last_bar['Datetime'], last_bar['Close'])

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
                        rr_val = 2.0

                    logger.info("[%s] Decision=%s RR=%s (Parsed: %.2f)", self.symbol, decision, rr_ratio, rr_val)

                    if decision in ["LONG", "SHORT"]:
                        reverse_direction = "short" if decision == "LONG" else "long"
                        await self.manager.close_position_by_signal(
                            exit_signal=reverse_direction,
                            fallback_exit_price=float(last_bar["Close"]),
                        )
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
                    await asyncio.sleep(10)
                    
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
        self.trader_runtimes = [SymbolTrader(cfg) for cfg in symbol_configs]
        self.trader_by_symbol = {runtime.symbol: runtime for runtime in self.trader_runtimes}
        
        self.socket = AlpacaWebSocket(
            symbols=list(self.trader_by_symbol.keys()),
            on_quote=self.on_quote_update,
            subscribe_quotes=True,
        )

    async def on_quote_update(self, quote_update: Dict[str, Any]):
        symbol = quote_update.get("symbol")
        runtime = self.trader_by_symbol.get(symbol)
        if runtime is None:
            return
        await runtime.quote_queue.put(quote_update)

    @staticmethod
    def _normalize_pct(value: float) -> float:
        """Accept 0.10 (10%) or 10 (10%)."""
        pct = float(value)
        if pct > 1.0:
            pct = pct / 100.0
        return max(0.0, pct)

    async def _initialize_symbol_capitals(self):
        """
        Compute per-symbol notional once at startup from Alpaca account balance.
        These computed notionals remain fixed for the process lifetime.
        """
        account = await get_account_info()
        if not account:
            raise RuntimeError(
                "Could not fetch Alpaca account info at startup. "
                "Cannot initialize percentage-based capital allocation."
            )

        cash_value = account.get("cash")
        try:
            base_balance = float(cash_value)
        except (TypeError, ValueError):
            raise RuntimeError(
                f"Invalid Alpaca cash value at startup: {cash_value!r}. "
                "Cannot initialize percentage-based capital allocation."
            )

        if base_balance <= 0:
            raise RuntimeError(
                f"Non-positive Alpaca cash balance at startup: {base_balance}. "
                "Cannot initialize percentage-based capital allocation."
            )

        total_pct = 0.0
        for runtime in self.trader_runtimes:
            if runtime.capital_pct > 0:
                total_pct += self._normalize_pct(runtime.capital_pct)

        if total_pct > 1.0:
            logger.warning(
                "Total capital_pct across symbols is %.2f%% (>100%%). Notionals may exceed account balance.",
                total_pct * 100,
            )

        logger.info("Initializing position notionals from Alpaca balance: %.2f USD", base_balance)

        for runtime in self.trader_runtimes:
            pct = self._normalize_pct(runtime.capital_pct)
            if pct > 0:
                runtime.capital = round(base_balance * pct, 2)
                logger.info(
                    "[%s] capital_pct=%.2f%% -> fixed startup notional=%.2f USD",
                    runtime.symbol, pct * 100, runtime.capital
                )
            else:
                logger.info(
                    "[%s] capital_pct not set. Using fixed configured notional=%.2f USD",
                    runtime.symbol, runtime.capital
                )

    async def run(self):
        if not self.trader_runtimes:
            logger.error("No symbol configs provided. Exiting.")
            return

        await ensure_trades_table()
        await self._initialize_symbol_capitals()

        socket_task = asyncio.create_task(self.socket.start_async())
        trader_tasks = [asyncio.create_task(runtime.loop()) for runtime in self.trader_runtimes]
        
        try:
            await asyncio.gather(*trader_tasks)
        finally:
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
    return [
        {
            "symbol": config.SYMBOL,
            "exec_symbol": config.EXEC_SYMBOL,
            "timeframe": config.TIMEFRAME,
            "capital": config.CAPITAL,
            "capital_pct": getattr(config, "CAPITAL_PCT", 0.0),
            "stop_loss_pct": config.STOP_LOSS_PCT,
            "take_profit_pct": config.TAKE_PROFIT_PCT,
        }
    ]
