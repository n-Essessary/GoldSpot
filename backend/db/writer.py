"""
db/writer.py — async PostgreSQL writer for price snapshots.

Connects via asyncpg pool using DATABASE_URL environment variable.
Non-fatal: all DB errors are logged but never crash the parser loop.
"""
from __future__ import annotations

import asyncpg
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL not set")
        _pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=5,
            command_timeout=10,
        )
    return _pool


async def write_snapshots(offers: list) -> None:
    """
    Writes a price snapshot batch to price_snapshots.
    Call after each cache update. Non-blocking via asyncio.create_task().

    offers — list[Offer] from api/schemas.py
    """
    if not offers:
        return
    try:
        pool = await get_pool()
        rows = [
            (
                o.source,
                o.display_server,
                o.server_name or "",
                o.faction,
                float(o.price_per_1k),
                int(o.amount_gold),
                o.seller,
                o.offer_url,
            )
            for o in offers
        ]
        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO price_snapshots
                    (source, server, server_name, faction,
                     price_per_1k, amount_gold, seller, offer_url)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                rows,
            )
        logger.debug("DB: wrote %d price snapshots", len(rows))
    except Exception:
        logger.exception("DB write_snapshots failed — non-fatal, continuing")


async def query_price_history(
    server: str,
    faction: str = "all",
    last_hours: int = 168,   # 1 week default
    bucket_minutes: int = 60,
) -> list[dict]:
    """
    Returns aggregated OHLC-like price history for a given server/faction.

    Bucketed by bucket_minutes intervals (e.g. 60 → hourly buckets).
    Returns list of dicts with keys: bucket, min_price, max_price,
    avg_price, median_price, offer_count.
    """
    pool = await get_pool()

    faction_filter = "" if faction == "all" else "AND faction = $3"
    params: list = [server, f"{last_hours} hours"]
    if faction != "all":
        params.append(faction)

    rows = await pool.fetch(
        f"""
        SELECT
            date_trunc('hour', ts) +
                (EXTRACT(MINUTE FROM ts)::int / {bucket_minutes}) *
                INTERVAL '{bucket_minutes} minutes' AS bucket,
            MIN(price_per_1k)  AS min_price,
            MAX(price_per_1k)  AS max_price,
            AVG(price_per_1k)  AS avg_price,
            PERCENTILE_CONT(0.5) WITHIN GROUP
                (ORDER BY price_per_1k) AS median_price,
            COUNT(*)           AS offer_count
        FROM price_snapshots
        WHERE server = $1
          AND ts > NOW() - $2::INTERVAL
          {faction_filter}
        GROUP BY bucket
        ORDER BY bucket ASC
        """,
        *params,
    )
    return [dict(r) for r in rows]
