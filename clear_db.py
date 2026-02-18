import asyncio
from trading.db_handler import get_pool

async def clear_trades():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM trades")
        print("All trades cleared from database.")

if __name__ == "__main__":
    asyncio.run(clear_trades())
