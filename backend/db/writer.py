"""
db/writer.py — async PostgreSQL writer для индексных снимков цен.

Подключается через asyncpg pool по DATABASE_URL.
Все ошибки не-фатальные: логируются и никогда не падают парсер-цикл.

Избегает кругового импорта: IndexPrice импортируется только в TYPE_CHECKING.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from service.offers_service import IndexPrice

logger = logging.getLogger(__name__)

# Порог изменения цены для записи нового снимка (0.5%).
# Органичный граф: плотность точек растёт при волатильности, падает при стабильности.
_WRITE_THRESHOLD = 0.005
_last_written: dict[str, float] = {}

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
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


def _should_write(key: str, new_price: float) -> bool:
    """Возвращает True если цена изменилась на > 0.5% или первый раз."""
    prev = _last_written.get(key)
    if prev is None or prev == 0:
        return True
    return abs(new_price - prev) / prev > _WRITE_THRESHOLD


async def write_index_snapshot(
    server: str,
    faction: str,
    idx: IndexPrice,
    ts: datetime | None = None,
) -> None:
    """
    Fire-and-forget запись индексного снимка в price_index_snapshots.
    Пишет только при изменении index_price > 0.5%.
    Никогда не пробрасывает исключения.
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
        logger.debug("DB snapshot: %s/%s idx=%.4f", server, faction, idx.index_price)
    except Exception:
        logger.warning("DB write failed %s/%s — non-fatal", server, faction)


async def query_index_history(
    server: str,
    faction: str = "all",
    last_hours: int = 168,
    max_points: int = 500,
) -> list[dict]:
    """
    Возвращает OHLC + vwap + best_ask для lightweight-charts.

    Адаптивный bucket: max(5, last_hours*60 / max_points) минут.
    Возвращает [] если БД недоступна — фронтенд покажет заглушку.
    """
    pool = await get_pool()
    if pool is None:
        return []

    bucket_minutes = max(5, (last_hours * 60) // max_points)

    conditions = ["server = $1", "ts > NOW() - $2::INTERVAL"]
    params: list = [server, f"{last_hours} hours"]

    if faction != "all":
        conditions.append(f"faction = ${len(params) + 1}")
        params.append(faction)
    else:
        # "All" — агрегированный faction
        conditions.append(f"faction = ${len(params) + 1}")
        params.append("All")

    where = " AND ".join(conditions)

    try:
        rows = await pool.fetch(
            f"""
            SELECT
                date_trunc('minute', ts)
                    - (EXTRACT(MINUTE FROM ts)::int %% {bucket_minutes})
                    * INTERVAL '1 minute'                      AS bucket,
                (array_agg(index_price ORDER BY ts))[1]        AS open,
                MAX(index_price)                               AS high,
                MIN(index_price)                               AS low,
                (array_agg(index_price ORDER BY ts DESC))[1]   AS close,
                AVG(index_price)                               AS avg_price,
                AVG(vwap)                                      AS vwap,
                MIN(best_ask)                                  AS best_ask,
                SUM(offer_count)                               AS offer_count,
                array_agg(DISTINCT src)
                    FILTER (WHERE src IS NOT NULL)             AS sources
            FROM price_index_snapshots,
                 unnest(sources) AS src
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
        logger.exception("DB query_index_history failed for %s/%s", server, faction)
        return []


async def cleanup_old_snapshots() -> None:
    """Фоновая задача: раз в сутки удаляет снимки старше 1 года."""
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
        except Exception:
            logger.exception("DB cleanup failed")
