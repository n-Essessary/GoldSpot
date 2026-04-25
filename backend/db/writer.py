"""
db/writer.py — async PostgreSQL writer.

Responsibilities:
  write_index_snapshot   — legacy: price_index_snapshots (group-level, OHLC history)
  write_price_snapshot   — new (Task 1): price_snapshots with raw prices
  upsert_server_index    — server_price_index only (current value; history now in tiered tables)
  query_index_history    — legacy: OHLC from price_index_snapshots (group-level)
  query_server_history   — delegates to tiered storage (snapshots_1h / 5m / 1m)
  query_server_history_short — delegates to tiered storage (snapshots_1m)

Migration 012 dropped the legacy per-server history tables.
Per-server history is now served from the 4-tier rolling tables via db/tiered_snapshots.py.

All errors are non-fatal: logged, never propagated to parser loop.
Uses asyncpg pool from DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from cachetools import TTLCache

if TYPE_CHECKING:
    from service.offers_service import IndexPrice

logger = logging.getLogger(__name__)

_WRITE_THRESHOLD = 0.005     # write snapshot only if price changed > 0.5%

# TTLCache: evicts entries after 1h — prevents unbounded growth from
# rotating server_id / faction key combinations over long uptimes.
_last_written: TTLCache = TTLCache(maxsize=10_000, ttl=3600)
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
                min_size=5,
                max_size=20,
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


def _flatten_param(value):
    """Defensive param normalizer: ensure list-like values are 1D."""
    if isinstance(value, (list, tuple, set)):
        flat: list = []
        for item in value:
            if isinstance(item, (list, tuple, set)):
                flat.extend(list(item))
            else:
                flat.append(item)
        return flat
    return value


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
    best_ask: float,
    sample_size: int,
    min_price: float,
    max_price: float,
    computed_at: Optional[datetime] = None,
) -> None:
    """
    Upsert the *current* index in server_price_index (one row per server+faction).

    History writes to the legacy per-server tables have been
    removed — those tables were dropped in migration 012 and replaced by the
    4-tier rolling storage (snapshots_1m / 5m / 1h / 1d) managed by
    service/tiered_snapshot_loop.py.

    Only writes when index_price or best_ask moved > 0.5% since the last write,
    preventing spurious upserts on every parser cycle when prices are stable.

    index_price is price per unit (per 1 gold), NOT per 1k.
    """
    now = computed_at if computed_at is not None else datetime.now(timezone.utc)

    faction_db = _faction_to_db(faction)
    key     = f"si:{server_id}::{faction_db}"
    ask_key = f"si:{server_id}::{faction_db}::ask"

    # Skip DB write if neither price changed by more than the threshold
    if not _should_write(key, index_price) and not _should_write(ask_key, best_ask):
        return

    pool = await get_pool()
    if pool is None:
        return

    try:
        await pool.execute(
            """
            INSERT INTO server_price_index
                (server_id, faction, computed_at, index_price,
                 best_ask, sample_size, min_price, max_price)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (server_id, faction) DO UPDATE SET
                computed_at = EXCLUDED.computed_at,
                index_price = EXCLUDED.index_price,
                best_ask    = EXCLUDED.best_ask,
                sample_size = EXCLUDED.sample_size,
                min_price   = EXCLUDED.min_price,
                max_price   = EXCLUDED.max_price
            """,
            server_id, faction_db, now,
            index_price, best_ask, sample_size, min_price, max_price,
        )
        _last_written[key]     = index_price
        _last_written[ask_key] = best_ask
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
    params = [_flatten_param(p) for p in params]
    where = " AND ".join(conditions)

    try:
        rows = await pool.fetch(
            f"""
            WITH bucketed AS (
                SELECT
                    (
                        date_trunc('minute', ts)
                        - (EXTRACT(MINUTE FROM ts)::int % {bucket_minutes}) * INTERVAL '1 minute'
                    )                                        AS bucket,
                    index_price,
                    vwap,
                    best_ask,
                    offer_count,
                    COALESCE(sources, ARRAY[]::text[])       AS sources,
                    ts
                FROM price_index_snapshots
                WHERE {where}
            )
            SELECT
                b.bucket,
                (array_agg(b.index_price ORDER BY b.ts))[1]      AS open,
                MAX(b.index_price)                                AS high,
                MIN(b.index_price)                                AS low,
                (array_agg(b.index_price ORDER BY b.ts DESC))[1] AS close,
                AVG(b.index_price)                                AS avg_price,
                AVG(b.vwap)                                       AS vwap,
                MIN(b.best_ask)                                   AS best_ask,
                SUM(b.offer_count)                                AS offer_count,
                ARRAY(
                    SELECT DISTINCT s
                    FROM bucketed p2
                    CROSS JOIN LATERAL unnest(COALESCE(p2.sources, ARRAY[]::text[])) AS s
                    WHERE p2.bucket = b.bucket
                )                                                 AS sources
            FROM bucketed b
            GROUP BY b.bucket
            ORDER BY b.bucket ASC
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
# Legacy per-server history tables were dropped in migration 012.
# These functions now delegate to the tiered storage (db/tiered_snapshots.py) so
# existing callers (router Mode 2, tests) continue to work transparently.

async def query_server_history(
    server_name: str,
    region: str,
    version: str,
    faction: str = "All",
    last: int = 500,
    hours: int = 24,
) -> list[dict]:
    """
    Return price history for a specific real server.
    Delegates to tiered storage (query_tiered_history_by_name).
    Legacy per-server history tables were removed in migration 012.
    """
    from db.tiered_snapshots import query_tiered_history_by_name
    return await query_tiered_history_by_name(
        server_name=server_name,
        region=region,
        version=version,
        faction=faction,
        hours=hours,
        max_points=last,
    )


async def query_server_history_short(
    server_name: str,
    region: str,
    version: str,
    faction: str = "All",
    last: int = 500,
    hours: int = 6,
) -> list[dict]:
    """
    Return high-frequency price history (1H / 6H chart views).

    Delegates to tiered storage (snapshots_1m — 1-min resolution, 24h retention).
    The `hours` parameter is passed through, capped naturally by the 1m table's
    24-hour rolling window.

    Response shape is identical to the legacy version:
      [{recorded_at, index_price, index_price_per_1k, best_ask, sample_size}, ...]
    """
    from db.tiered_snapshots import query_tiered_history_by_name
    return await query_tiered_history_by_name(
        server_name=server_name,
        region=region,
        version=version,
        faction=faction,
        hours=hours,
        max_points=last,
    )


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
    """Background task: retention cleanup for legacy tables (runs daily).

    The legacy per-server history tables were dropped in
    migration 012 — their rolling retention is now managed by
    service/tiered_snapshot_loop.py (cleanup_snapshots_*).

    This task retains cleanup for the two remaining legacy tables:
      price_index_snapshots — group-level OHLC (30-day retention)
      price_snapshots       — raw offer log   (1-day retention)
    """
    while True:
        await asyncio.sleep(86_400)
        pool = await get_pool()
        if pool is None:
            continue
        try:
            # price_index_snapshots: 30 days (group OHLC, legacy /price-history/ohlc)
            result = await pool.fetchval(
                "WITH d AS (DELETE FROM price_index_snapshots "
                "WHERE ts < NOW() - INTERVAL '30 days' RETURNING 1) "
                "SELECT count(*) FROM d"
            )
            logger.info("DB cleanup: removed %s old index snapshots", result)

            # price_snapshots: 1 day (raw offer log — not a historical data product)
            result2 = await pool.fetchval(
                "WITH d AS (DELETE FROM price_snapshots "
                "WHERE fetched_at < NOW() - INTERVAL '1 day' RETURNING 1) "
                "SELECT count(*) FROM d"
            )
            logger.info("DB cleanup: removed %s old raw price snapshots", result2)
        except Exception:
            logger.exception("DB cleanup failed")
