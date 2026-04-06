"""
db/writer.py — async PostgreSQL writer.

Responsibilities:
  write_index_snapshot   — legacy: price_index_snapshots (group-level, OHLC history)
  write_price_snapshot   — new (Task 1): price_snapshots with raw prices
  upsert_server_index    — new (Task 4): server_price_index + server_price_history
  query_index_history    — legacy: OHLC from price_index_snapshots (group-level)
  query_server_history   — new (Task 4): per-real-server price history

All errors are non-fatal: logged, never propagated to parser loop.
Uses asyncpg pool from DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from service.offers_service import IndexPrice

logger = logging.getLogger(__name__)

_WRITE_THRESHOLD = 0.005     # write snapshot only if price changed > 0.5%
_MAX_HISTORY_PER_SERVER = 1000  # keep last N points per server+faction (Task 4)

_last_written: dict[str, float] = {}
_pool = None
_pool_lock: asyncio.Lock | None = None   # created lazily inside the event loop


# ── Pool management ───────────────────────────────────────────────────────────

async def get_pool():
    """Return (or lazily create) the asyncpg connection pool.

    Double-checked locking prevents multiple concurrent callers from creating
    more than one pool when DATABASE_URL is set and the pool has not yet been
    initialised (e.g. on the first parse cycle when both FunPay and G2G tasks
    call get_pool() simultaneously).
    """
    global _pool, _pool_lock

    # Fast path: pool already exists (99% of calls after startup)
    if _pool is not None:
        return _pool

    # Lazy-init the lock inside the running loop to avoid module-level
    # event-loop binding issues on Python ≤ 3.9.
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()

    async with _pool_lock:
        # Second check after acquiring the lock: another coroutine may have
        # created the pool while we were waiting.
        if _pool is not None:
            return _pool

        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            logger.warning("DATABASE_URL not set — DB writes disabled")
            return None
        try:
            import asyncpg
            _pool = await asyncpg.create_pool(
                dsn,
                min_size=1,
                max_size=5,
                command_timeout=10,
            )
            logger.info("DB pool created")
        except Exception:
            logger.exception("DB pool creation failed")
            return None

    return _pool


# ── Helpers ───────────────────────────────────────────────────────────────────

def _should_write(key: str, new_price: float) -> bool:
    """True if price changed > 0.5% or this is the first write."""
    prev = _last_written.get(key)
    if prev is None or prev == 0:
        return True
    return abs(new_price - prev) / prev > _WRITE_THRESHOLD


def _faction_to_db(faction: str) -> str:
    """Normalise faction to DB format ('All'/'Alliance'/'Horde')."""
    if faction.lower() == "all":
        return "All"
    return faction.capitalize()


# ── Legacy: group-level index snapshot ───────────────────────────────────────

async def write_index_snapshot(
    server: str,
    faction: str,
    idx: "IndexPrice",
    ts: Optional[datetime] = None,
) -> None:
    """
    Write to price_index_snapshots (group-level, e.g. "(EU) Anniversary").
    Kept for backward compat with /price-history/ohlc endpoint.
    Only writes when index_price changed > 0.5%.
    """
    key = f"{server}::{faction}"
    if not _should_write(key, idx.index_price):
        return

    pool = await get_pool()
    if pool is None:
        return

    if ts is None:
        ts = datetime.now(timezone.utc)

    try:
        await pool.execute(
            """
            INSERT INTO price_index_snapshots
                (ts, server, faction, index_price, vwap, best_ask,
                 price_min, price_max, offer_count, total_volume,
                 sources, source_count)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """,
            ts, server, faction,
            idx.index_price, idx.vwap, idx.best_ask,
            idx.price_min, idx.price_max,
            idx.offer_count, idx.total_volume,
            idx.sources, len(idx.sources),
        )
        _last_written[key] = idx.index_price
        logger.debug("DB index snapshot: %s/%s idx=%.6f", server, faction, idx.index_price)
    except Exception:
        logger.warning("DB write_index_snapshot failed %s/%s — non-fatal", server, faction)


# ── New (Task 1): raw price snapshot ─────────────────────────────────────────

async def write_price_snapshot(
    source: str,
    offer_id: str,
    server_id: Optional[int],
    faction: str,
    raw_price: float,
    raw_price_unit: str,
    lot_size: int,
    seller: Optional[str],
    offer_url: Optional[str],
    fetched_at: Optional[datetime] = None,
    currency: str = "USD",
) -> None:
    """
    Write a raw price snapshot to price_snapshots table.
    Called by _snapshot_all_servers() in offers_service after each parse cycle.

    Stores ONLY raw price — no computed price_per_1k.
    """
    pool = await get_pool()
    if pool is None:
        return

    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc)

    try:
        await pool.execute(
            """
            INSERT INTO price_snapshots
                (source, offer_id, server_id, faction, raw_price, raw_price_unit,
                 lot_size, currency, seller, offer_url, fetched_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            """,
            source, offer_id, server_id, faction,
            raw_price, raw_price_unit, lot_size,
            currency, seller, offer_url, fetched_at,
        )
    except Exception:
        logger.debug(
            "DB write_price_snapshot failed offer_id=%s — non-fatal", offer_id
        )


# ── New (Task 4): per-server price index ─────────────────────────────────────

async def upsert_server_index(
    server_id: int,
    faction: str,
    index_price: float,    # price per unit (per 1 gold)
    sample_size: int,
    min_price: float,
    max_price: float,
    computed_at: Optional[datetime] = None,
) -> None:
    """
    Upsert current index in server_price_index table (one row per server+faction).
    Also append to server_price_history and prune to keep last 1000 points.

    index_price is price per unit (per 1 gold), NOT per 1k.
    """
    pool = await get_pool()
    if pool is None:
        return

    if computed_at is None:
        computed_at = datetime.now(timezone.utc)

    faction_db = _faction_to_db(faction)
    key = f"si:{server_id}::{faction_db}"

    # Throttle: only write if price changed > 0.5%
    if not _should_write(key, index_price):
        return

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Upsert current index
                await conn.execute(
                    """
                    INSERT INTO server_price_index
                        (server_id, faction, computed_at, index_price,
                         sample_size, min_price, max_price)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    ON CONFLICT (server_id, faction) DO UPDATE SET
                        computed_at = EXCLUDED.computed_at,
                        index_price = EXCLUDED.index_price,
                        sample_size = EXCLUDED.sample_size,
                        min_price   = EXCLUDED.min_price,
                        max_price   = EXCLUDED.max_price
                    """,
                    server_id, faction_db, computed_at,
                    index_price, sample_size, min_price, max_price,
                )
                # Append to history
                await conn.execute(
                    """
                    INSERT INTO server_price_history
                        (server_id, faction, recorded_at, index_price, sample_size)
                    VALUES ($1,$2,$3,$4,$5)
                    """,
                    server_id, faction_db, computed_at,
                    index_price, sample_size,
                )
                # Prune history: keep only last _MAX_HISTORY_PER_SERVER rows
                await conn.execute(
                    """
                    DELETE FROM server_price_history
                    WHERE server_id = $1 AND faction = $2
                      AND id NOT IN (
                          SELECT id FROM server_price_history
                          WHERE server_id = $1 AND faction = $2
                          ORDER BY id DESC
                          LIMIT $3
                      )
                    """,
                    server_id, faction_db, _MAX_HISTORY_PER_SERVER,
                )
        _last_written[key] = index_price
        logger.debug(
            "DB server index: server_id=%d faction=%s idx=%.6f",
            server_id, faction_db, index_price,
        )
    except Exception:
        logger.warning(
            "DB upsert_server_index failed server_id=%d/%s — non-fatal",
            server_id, faction,
        )


# ── Legacy: OHLC query for /price-history/ohlc ───────────────────────────────

async def query_index_history(
    server: str,
    faction: str = "all",
    last_hours: int = 168,
    max_points: int = 500,
) -> list[dict]:
    """
    OHLC + vwap + best_ask from price_index_snapshots (group-level).
    Adaptive bucket: max(5, last_hours*60 / max_points) minutes.
    Returns [] if DB unavailable.
    """
    pool = await get_pool()
    if pool is None:
        return []

    bucket_minutes = max(5, (last_hours * 60) // max_points)
    faction_db = _faction_to_db(faction)
    conditions = ["server = $1", "ts > NOW() - $2::INTERVAL", "faction = $3"]
    params: list = [server, timedelta(hours=last_hours), faction_db]
    where = " AND ".join(conditions)

    try:
        rows = await pool.fetch(
            f"""
            SELECT
                date_trunc('minute', ts)
                    - (EXTRACT(MINUTE FROM ts)::int % {bucket_minutes})
                    * INTERVAL '1 minute'                        AS bucket,
                (array_agg(index_price ORDER BY ts))[1]          AS open,
                MAX(index_price)                                 AS high,
                MIN(index_price)                                 AS low,
                (array_agg(index_price ORDER BY ts DESC))[1]     AS close,
                AVG(index_price)                                 AS avg_price,
                AVG(vwap)                                        AS vwap,
                MIN(best_ask)                                    AS best_ask,
                SUM(offer_count)                                 AS offer_count,
                ARRAY(
                    SELECT DISTINCT s
                    FROM unnest(array_agg(sources)) AS s
                    WHERE s IS NOT NULL
                )                                                AS sources
            FROM price_index_snapshots
            WHERE {where}
            GROUP BY bucket
            ORDER BY bucket ASC
            """,
            *params,
        )
        return [
            {
                "time":        r["bucket"].isoformat(),
                "open":        float(r["open"]        or 0),
                "high":        float(r["high"]        or 0),
                "low":         float(r["low"]         or 0),
                "close":       float(r["close"]       or 0),
                "avg_price":   float(r["avg_price"]   or 0),
                "vwap":        float(r["vwap"]        or 0),
                "best_ask":    float(r["best_ask"]    or 0),
                "offer_count": int(r["offer_count"]   or 0),
                "sources":     list(r["sources"] or []),
            }
            for r in rows
        ]
    except Exception:
        logger.exception(
            "DB query_index_history failed for %s/%s", server, faction
        )
        return []


# ── New (Task 4): per-server history query ────────────────────────────────────

async def query_server_history(
    server_name: str,
    region: str,
    version: str,
    faction: str = "All",
    last: int = 500,
) -> list[dict]:
    """
    Return price history for a specific real server from server_price_history.

    GET /price-history?server=Firemaw&region=EU&version=Classic+Era&faction=Horde
    → [{recorded_at, index_price, index_price_per_1k, sample_size}, ...]
    """
    pool = await get_pool()
    if pool is None:
        return []

    faction_db = _faction_to_db(faction)

    try:
        rows = await pool.fetch(
            """
            SELECT h.recorded_at, h.index_price, h.sample_size
            FROM server_price_history h
            JOIN servers s ON s.id = h.server_id
            WHERE LOWER(s.name)    = LOWER($1)
              AND s.region         = $2
              AND LOWER(s.version) = LOWER($3)
              AND h.faction        = $4
            ORDER BY h.recorded_at DESC
            LIMIT $5
            """,
            server_name, region, version, faction_db, last,
        )
        return [
            {
                "recorded_at":        r["recorded_at"].isoformat(),
                "index_price":        float(r["index_price"]),
                "index_price_per_1k": round(float(r["index_price"]) * 1000, 4),
                "sample_size":        r["sample_size"],
            }
            for r in reversed(rows)  # return chronological order
        ]
    except Exception:
        logger.exception(
            "DB query_server_history failed for %s/%s/%s/%s",
            server_name, region, version, faction,
        )
        return []


async def query_price_index_all(faction: str = "All") -> list[dict]:
    """
    Return current index for all active servers.
    GET /price-index → list of {server_name, region, version, faction, index_price, ...}
    """
    pool = await get_pool()
    if pool is None:
        return []

    faction_db = _faction_to_db(faction)

    try:
        # Build WHERE clause — "All" means all factions
        if faction.lower() == "all":
            rows = await pool.fetch(
                """
                SELECT s.name, s.region, s.version,
                       i.faction, i.computed_at,
                       i.index_price, i.sample_size, i.min_price, i.max_price
                FROM server_price_index i
                JOIN servers s ON s.id = i.server_id
                WHERE s.is_active = TRUE
                ORDER BY s.region, s.version, s.name, i.faction
                """
            )
        else:
            rows = await pool.fetch(
                """
                SELECT s.name, s.region, s.version,
                       i.faction, i.computed_at,
                       i.index_price, i.sample_size, i.min_price, i.max_price
                FROM server_price_index i
                JOIN servers s ON s.id = i.server_id
                WHERE s.is_active = TRUE AND i.faction = $1
                ORDER BY s.region, s.version, s.name
                """,
                faction_db,
            )
        return [
            {
                "server_name":      r["name"],
                "region":           r["region"],
                "version":          r["version"],
                "faction":          r["faction"],
                "index_price":      float(r["index_price"]),
                "index_price_per_1k": round(float(r["index_price"]) * 1000, 4),
                "sample_size":      r["sample_size"] or 0,
                "min_price":        float(r["min_price"] or 0),
                "max_price":        float(r["max_price"] or 0),
                "computed_at":      r["computed_at"].isoformat(),
            }
            for r in rows
        ]
    except Exception:
        logger.exception("DB query_price_index_all failed")
        return []


# ── Maintenance ───────────────────────────────────────────────────────────────

async def cleanup_old_snapshots() -> None:
    """Background task: delete snapshots older than 1 year (runs daily)."""
    while True:
        await asyncio.sleep(86_400)
        pool = await get_pool()
        if pool is None:
            continue
        try:
            result = await pool.fetchval(
                "WITH d AS (DELETE FROM price_index_snapshots "
                "WHERE ts < NOW() - INTERVAL '1 year' RETURNING 1) "
                "SELECT count(*) FROM d"
            )
            logger.info("DB cleanup: removed %s old index snapshots", result)

            # Also clean raw price snapshots older than 7 days
            result2 = await pool.fetchval(
                "WITH d AS (DELETE FROM price_snapshots "
                "WHERE fetched_at < NOW() - INTERVAL '7 days' RETURNING 1) "
                "SELECT count(*) FROM d"
            )
            logger.info("DB cleanup: removed %s old raw price snapshots", result2)
        except Exception:
            logger.exception("DB cleanup failed")
