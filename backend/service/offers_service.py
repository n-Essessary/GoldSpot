"""
In-memory хранилище офферов.

Lock убран намеренно: asyncio — однопоточный event loop, конкурентного
доступа нет. Вернём RWLock когда появится реальный async DB или threading.

Задел на multi-source: добавить источник = одна строка в SOURCES.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from api.schemas import Offer, PriceHistoryPoint
from parser.fanpay_parser import fetch_offers as _fanpay_fetch
from parser.funpay_parser import fetch_offers as _funpay_fetch
from parser.g2g_parser import fetch_offers as _g2g_fetch

logger = logging.getLogger(__name__)

SOURCES: dict[str, Callable[[], Awaitable[list[Offer]]]] = {
    "fanpay": _fanpay_fetch,
    "funpay": _funpay_fetch,
    "g2g": _g2g_fetch,
}

_cache: list[Offer] = []
_history_by_server = defaultdict(lambda: deque(maxlen=200))


def compute_index_price(
    offers: list[Offer],
) -> tuple[float | None, float | None, float | None]:
    if not offers:
        return None, None, None

    prices = sorted([o.price_per_1k for o in offers if o.price_per_1k])

    if not prices:
        return None, None, None

    min_price = prices[0]
    max_price = prices[-1]

    n = len(prices)

    # если мало данных — используем median
    if n < 5:
        mid = n // 2
        if n % 2 == 0:
            index_price = (prices[mid - 1] + prices[mid]) / 2
        else:
            index_price = prices[mid]
        return index_price, min_price, max_price

    # берем cheapest 20%
    k = max(3, int(n * 0.2))
    cheapest = prices[:k]

    index_price = sum(cheapest) / len(cheapest)

    return index_price, min_price, max_price


def _record_snapshot(offers: list[Offer]) -> None:
    if not offers:
        return
    now = datetime.now(timezone.utc)

    grouped: dict[str, list[Offer]] = {}
    for o in offers:
        grouped.setdefault(o.server, []).append(o)

    for server, items in grouped.items():
        index_price, min_price, max_price = compute_index_price(items)
        _history_by_server[server].append(
            {
                "timestamp": now,
                "price": index_price,
                "min": min_price,
                "max": max_price,
                "count": len(items),
            }
        )


def get_price_history(server: str, last: int) -> list[PriceHistoryPoint]:
    history = _history_by_server.get(server.lower(), [])
    recent = list(history)[-last:]
    points: list[PriceHistoryPoint] = []
    for row in recent:
        points.append(
            PriceHistoryPoint(
                timestamp=row["timestamp"],
                avg_price=round((row["price"] or 0.0), 4),
                min_price=round((row["min"] or 0.0), 4),
                offer_count=row["count"],
            )
        )
    return points


async def refresh() -> None:
    global _cache
    all_offers: list[Offer] = []

    for source_name, fetch_fn in SOURCES.items():
        try:
            offers = await fetch_fn()
            all_offers.extend(offers)
            logger.info("Источник %s: загружено %d офферов", source_name, len(offers))
        except Exception:
            logger.exception("Источник %s: ошибка загрузки", source_name)

    _cache = all_offers
    logger.info("Кэш обновлён: %d офферов", len(_cache))
    _record_snapshot(_cache)


def get_offers(
    server: str | None = None,
    faction: str | None = None,
    sort_by: str = "price",
    limit: int = 20,
) -> list[Offer]:
    result = list(_cache)

    if server:
        # o.server гарантированно lowercase (slug) — model_validator в Offer
        result = [o for o in result if o.server == server.lower()]
    if faction:
        result = [o for o in result if o.faction.lower() == faction.lower()]

    key = "price_per_1k" if sort_by == "price" else "amount_gold"
    result.sort(key=lambda o: getattr(o, key))

    return result[:limit]
