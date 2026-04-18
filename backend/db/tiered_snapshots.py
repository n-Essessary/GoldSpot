"""
db/tiered_snapshots.py — 4-tier rolling price history storage.

Tier schema (identical columns, different resolution/retention):
  snapshots_1m — 1-min resolution, 24h rolling   (live writes, one per parser cycle)
  snapshots_5m — 5-min resolution, 30d rolling   (downsampled from 1m every 5 min)
  snapshots_1h — 1-hour resolution, 2y rolling   (downsampled from 5m every 5 min)
  snapshots_1d — 1-day resolution, forever        (downsampled from 1h every 5 min)

Design principles:
  - All functions non-fatal: errors logged at WARNING, never propagated.
  - All DB access via get_pool() from db.writer — shared pool, no second connection.
  - All writes use INSERT ON CONFLICT DO NOTHING (idempotent / safe to re-run).
  - Downsampling windows overlap intentionally (last 10 min / 2h / 2d) to fill
    any bucket that was missed due to a restart or DB hiccup.
  - query_tiered_history selects the coarsest tier that covers the requested
    window, keeping query cost proportional to what the frontend actually needs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _faction_to_db(faction: str) -> str:
    """Normalise faction string to DB canonical form: 'All' | 'Alliance' | 'Horde'."""
    if faction.lower() == "all":
        return "All"
    return faction.capitalize()


# ── Write ─────────────────────────────────────────────────────────────────────

async def write_snapshot_1m(
    server_id:   int,
    faction:     str,
    index_price: float,
    best_ask:    Optional[float],
    sample_size: Optional[int],
    recorded_at: Optional[datetime] = None,
) -> None:
    """INSERT a 1-minute price snapshot.  ON CONFLICT DO NOTHING (idempotent).

    index_price and best_ask are stored as price-per-unit (per 1 gold),
    consistent with the existing server_price_index / compute_server_index
    conventions.
    """
    from db.writer import get_pool

    pool = await get_pool()
    if pool is None:
        return

    if recorded_at is None:
        recorded_at = datetime.now(timezone.utc)

    faction_db = _faction_to_db(faction)

    try:
        await pool.execute(
            """
            INSERT INTO snapshots_1m
                (server_id, faction, recorded_at, index_price, best_ask, sample_size)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (server_id, faction, recorded_at) DO NOTHING
            """,
            server_id, faction_db, recorded_at, index_price, best_ask, sample_size,
        )
        logger.debug(
            "tiered 1m write: server_id=%d faction=%s idx=%.6f",
            server_id, faction_db, index_price,
        )
    except Exception:
        logger.warning(
            "write_snapshot_1m failed server_id=%d/%s — non-fatal",
            server_id, faction,
        )


# ── Downsampling ──────────────────────────────────────────────────────────────

async def downsample_1m_to_5m() -> None:
    """Aggregate the last 10 minutes of snapshots_1m into 5-minute buckets
    in snapshots_5m.

    The 10-minute lookback window (vs. the 5-min schedule) is intentional:
    it ensures that any bucket whose 5-min window straddles the job boundary
    still gets filled correctly on the next run.
    """
    from db.writer import get_pool

    pool = await get_pool()
    if pool is None:
        return

    try:
        await pool.execute(
            """
            WITH agg AS (
                SELECT
                    server_id,
                    faction,
                    date_trunc('hour', recorded_at)
                        + (EXTRACT(MINUTE FROM recorded_at)::int / 5)
                          * INTERVAL '5 minutes'        AS bucket,
                    AVG(index_price)                    AS index_price,
                    MIN(best_ask)                       AS best_ask,
                    MAX(sample_size)                    AS sample_size
                FROM snapshots_1m
                WHERE recorded_at > NOW() - INTERVAL '10 minutes'
                GROUP BY
                    server_id, faction,
                    date_trunc('hour', recorded_at)
                        + (EXTRACT(MINUTE FROM recorded_at)::int / 5)
                          * INTERVAL '5 minutes'
            )
            INSERT INTO snapshots_5m
                (server_id, faction, recorded_at, index_price, best_ask, sample_size)
            SELECT server_id, faction, bucket, index_price, best_ask, sample_size
            FROM agg
            ON CONFLICT (server_id, faction, recorded_at) DO NOTHING
            """
        )
        logger.debug("downsample_1m_to_5m: completed")
    except Exception:
        logger.warning("downsample_1m_to_5m failed — non-fatal")


async def downsample_5m_to_1h() -> None:
    """Aggregate the last 2 hours of snapshots_5m into 1-hour buckets
    in snapshots_1h.

    2-hour lookback (vs. the 5-min schedule) covers at least one full
    prior hour even when the job runs right at the hour boundary.
    """
    from db.writer import get_pool

    pool = await get_pool()
    if pool is None:
        return

    try:
        await pool.execute(
            """
            WITH agg AS (
                SELECT
                    server_id,
                    faction,
                    date_trunc('hour', recorded_at)     AS bucket,
                    AVG(index_price)                    AS index_price,
                    MIN(best_ask)                       AS best_ask,
                    MAX(sample_size)                    AS sample_size
                FROM snapshots_5m
                WHERE recorded_at > NOW() - INTERVAL '2 hours'
                GROUP BY server_id, faction, date_trunc('hour', recorded_at)
            )
            INSERT INTO snapshots_1h
                (server_id, faction, recorded_at, index_price, best_ask, sample_size)
            SELECT server_id, faction, bucket, index_price, best_ask, sample_size
            FROM agg
            ON CONFLICT (server_id, faction, recorded_at) DO NOTHING
            """
        )
        logger.debug("downsample_5m_to_1h: completed")
    except Exception:
        logger.warning("downsample_5m_to_1h failed — non-fatal")


async def downsample_1h_to_1d() -> None:
    """Aggregate the last 2 days of snapshots_1h into 1-day buckets
    in snapshots_1d.

    2-day lookback covers the previous full day even when the job runs
    at the very start of a new day.
    """
    from db.writer import get_pool

    pool = await get_pool()
    if pool is None:
        return

    try:
        await pool.execute(
            """
            WITH agg AS (
                SELECT
                    server_id,
                    faction,
                    date_trunc('day', recorded_at)      AS bucket,
                    AVG(index_price)                    AS index_price,
                    MIN(best_ask)                       AS best_ask,
                    MAX(sample_size)                    AS sample_size
                FROM snapshots_1h
                WHERE recorded_at > NOW() - INTERVAL '2 days'
                GROUP BY server_id, faction, date_trunc('day', recorded_at)
            )
            INSERT INTO snapshots_1d
                (server_id, faction, recorded_at, index_price, best_ask, sample_size)
            SELECT server_id, faction, bucket, index_price, best_ask, sample_size
            FROM agg
            ON CONFLICT (server_id, faction, recorded_at) DO NOTHING
            """
        )
        logger.debug("downsample_1h_to_1d: completed")
    except Exception:
        logger.warning("downsample_1h_to_1d failed — non-fatal")


# ── Cleanup ───────────────────────────────────────────────────────────────────

async def cleanup_snapshots_1m() -> None:
    """Delete snapshots_1m rows older than 24 hours (rolling window)."""
    from db.writer import get_pool

    pool = await get_pool()
    if pool is None:
        return

    try:
        deleted = await pool.fetchval(
            "WITH d AS ("
            "  DELETE FROM snapshots_1m"
            "  WHERE recorded_at < NOW() - INTERVAL '24 hours'"
            "  RETURNING 1"
            ") SELECT COUNT(*) FROM d"
        )
        logger.debug("cleanup_snapshots_1m: deleted %s rows", deleted)
    except Exception:
        logger.warning("cleanup_snapshots_1m failed — non-fatal")


async def cleanup_snapshots_5m() -> None:
    """Delete snapshots_5m rows older than 30 days (rolling window)."""
    from db.writer import get_pool

    pool = await get_pool()
    if pool is None:
        return

    try:
        deleted = await pool.fetchval(
            "WITH d AS ("
            "  DELETE FROM snapshots_5m"
            "  WHERE recorded_at < NOW() - INTERVAL '30 days'"
            "  RETURNING 1"
            ") SELECT COUNT(*) FROM d"
        )
        logger.debug("cleanup_snapshots_5m: deleted %s rows", deleted)
    except Exception:
        logger.warning("cleanup_snapshots_5m failed — non-fatal")


async def cleanup_snapshots_1h() -> None:
    """Delete snapshots_1h rows older than 2 years (rolling window)."""
    from db.writer import get_pool

    pool = await get_pool()
    if pool is None:
        return

    try:
        deleted = await pool.fetchval(
            "WITH d AS ("
            "  DELETE FROM snapshots_1h"
            "  WHERE recorded_at < NOW() - INTERVAL '2 years'"
            "  RETURNING 1"
            ") SELECT COUNT(*) FROM d"
        )
        logger.debug("cleanup_snapshots_1h: deleted %s rows", deleted)
    except Exception:
        logger.warning("cleanup_snapshots_1h failed — non-fatal")


# ── Query ─────────────────────────────────────────────────────────────────────

async def query_tiered_history(
    server_id:  int,
    faction:    str,
    hours:      int,
    max_points: int = 500,
) -> list[dict]:
    """Smart router: select the finest tier that still covers `hours`.

    Tier selection:
      hours ≤ 24    → snapshots_1m  (1-min resolution, 24h retention)
      hours ≤ 720   → snapshots_5m  (5-min resolution, 30d retention)
      hours ≤ 17520 → snapshots_1h  (1-hour resolution, 2y retention)
      else          → snapshots_1d  (1-day resolution, forever)

    Returns list of dicts matching the /price-history response shape:
      {recorded_at, index_price, index_price_per_1k, best_ask, sample_size}

    index_price stored as price-per-unit (per 1 gold).
    best_ask returned as price-per-1k (×1000) for frontend compatibility.
    """
    from db.writer import get_pool

    pool = await get_pool()
    if pool is None:
        return []

    faction_db = _faction_to_db(faction)

    if hours <= 24:
        table = "snapshots_1m"
    elif hours <= 720:        # 30 days
        table = "snapshots_5m"
    elif hours <= 17_520:     # 2 years
        table = "snapshots_1h"
    else:
        table = "snapshots_1d"

    try:
        rows = await pool.fetch(
            f"""
            SELECT recorded_at, index_price, best_ask, sample_size
            FROM {table}
            WHERE server_id   = $1
              AND faction      = $2
              AND recorded_at  > NOW() - ($3::integer * INTERVAL '1 hour')
            ORDER BY recorded_at DESC
            LIMIT $4
            """,
            server_id, faction_db, hours, max_points,
        )
    except Exception:
        logger.exception(
            "query_tiered_history failed server_id=%d faction=%s hours=%d table=%s",
            server_id, faction, hours, table,
        )
        return []

    def _best_ask_per_1k(r) -> float:
        v = r["best_ask"]
        base = float(r["index_price"])
        if v is None:
            return round(base * 1000, 4)
        return round(float(v) * 1000, 4)

    # rows are DESC from DB (newest first) — reverse to ASC for chart
    result = [
        {
            "recorded_at":        r["recorded_at"].isoformat(),
            "index_price":        float(r["index_price"]),
            "index_price_per_1k": round(float(r["index_price"]) * 1000, 4),
            "best_ask":           _best_ask_per_1k(r),
            "sample_size":        r["sample_size"],
        }
        for r in rows
    ]
    result.reverse()
    return result


async def query_tiered_history_by_name(
    server_name: str,
    region:      str,
    version:     str,
    faction:     str,
    hours:       int,
    max_points:  int = 500,
) -> list[dict]:
    """Convenience wrapper: resolve (name, region, version) → server_id,
    then delegate to query_tiered_history.

    Returns [] if the server is not found in the servers table or DB is
    unavailable.
    """
    from db.writer import get_pool

    pool = await get_pool()
    if pool is None:
        return []

    try:
        server_id = await pool.fetchval(
            """
            SELECT id FROM servers
            WHERE LOWER(name)    = LOWER($1)
              AND region         = $2
              AND LOWER(version) = LOWER($3)
            LIMIT 1
            """,
            server_name, region.upper(), version,
        )
    except Exception:
        logger.warning(
            "query_tiered_history_by_name: server lookup failed %s/%s/%s — non-fatal",
            server_name, region, version,
        )
        return []

    if server_id is None:
        logger.debug(
            "query_tiered_history_by_name: server not found %s/%s/%s",
            server_name, region, version,
        )
        return []

    return await query_tiered_history(server_id, faction, hours, max_points)
