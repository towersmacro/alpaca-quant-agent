"""
Module: db_handler.py

Async PostgreSQL database handler for trade persistence using asyncpg.
Manages connection pooling and CRUD operations for trade records.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncpg

import config

logger = logging.getLogger("quant_agent_trading")

_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()


async def get_pool() -> asyncpg.Pool:
    """
    Get or create the global asyncpg connection pool.
    Thread-safe initialization using asyncio.Lock.
    """
    global _pool
    if _pool is not None:
        return _pool

    async with _pool_lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(
                host=config.POSTGRES_HOST,
                port=config.POSTGRES_PORT,
                database=config.POSTGRES_DB,
                user=config.POSTGRES_USER,
                password=config.POSTGRES_PASSWORD,
                min_size=1,
                max_size=10,
            )
            logger.info(
                "Connected to PostgreSQL at %s:%s/%s",
                config.POSTGRES_HOST,
                config.POSTGRES_PORT,
                config.POSTGRES_DB,
            )
    return _pool


async def close_pool() -> None:
    """Gracefully close the database connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def init_db() -> None:
    """Initialize PostgreSQL schema for trades if not exists."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                uid TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price DOUBLE PRECISION,
                entry_time TIMESTAMPTZ,
                quantity DOUBLE PRECISION,
                status TEXT,
                exit_price DOUBLE PRECISION,
                exit_time TIMESTAMPTZ,
                exit_reason TEXT,
                pnl DOUBLE PRECISION,
                sl_price DOUBLE PRECISION,
                tp_price DOUBLE PRECISION,
                timeframe TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )


async def ensure_trades_table() -> None:
    """Idempotent schema guard for fresh startup."""
    await init_db()


async def insert_trade(trade_data: Dict[str, Any]) -> None:
    """
    Insert a new trade record into the database.
    
    Args:
        trade_data: Dictionary containing trade details (uid, ticker, etc.)
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO trades (
                    uid, ticker, direction, entry_price, entry_time, quantity,
                    status, sl_price, tp_price, timeframe
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (uid) DO NOTHING
                """,
                trade_data["uid"],
                trade_data["ticker"],
                trade_data["direction"],
                trade_data["entry_price"],
                trade_data["entry_time"],
                trade_data["quantity"],
                trade_data["status"],
                trade_data["sl_price"],
                trade_data["tp_price"],
                trade_data["timeframe"],
            )
        logger.info("Trade %s inserted into DB", trade_data["uid"])
    except Exception as exc:
        logger.error("Failed to insert trade: %s", exc)


async def update_trade_close(
    uid: str,
    exit_price: float,
    exit_time: datetime,
    exit_reason: str,
    pnl: float,
) -> None:
    """
    Update an existing trade record upon closure.
    
    Args:
        uid: The trade's unique identifier.
        exit_price: Price at exit.
        exit_time: Timestamp of exit.
        exit_reason: Reason string (e.g. "SL hit").
        pnl: Realized Profit/Loss.
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE trades
                SET status = 'CLOSED',
                exit_price = $1,
                exit_time = $2,
                exit_reason = $3,
                pnl = $4
                WHERE uid = $5
                """,
                exit_price,
                exit_time,
                exit_reason,
                pnl,
                uid,
            )
        logger.info("Trade %s updated as CLOSED in DB", uid)
    except Exception as exc:
        logger.error("Failed to update trade close: %s", exc)


async def get_active_trades() -> List[Dict[str, Any]]:
    """Retrieve all active (OPEN) trades from PostgreSQL."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM trades WHERE status = 'OPEN'")
            return [dict(row) for row in rows]
    except Exception as exc:
        logger.error("Failed to fetch active trades: %s", exc)
        return []
