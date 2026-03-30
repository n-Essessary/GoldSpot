"""
In-memory хранилище офферов.

Lock убран намеренно: asyncio — однопоточный event loop, конкурентного
доступа нет. Вернём RWLock когда появится реальный async DB или threading.

Задел на multi-source: добавить источник = одна строка в SOURCES.
"""
from __future__ import annotations

import logging
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from api.schemas import Offer, PriceHistoryPoint
from parser.funpay_parser import fetch_offers as _funpay_fetch
from parser.g2g_parser import fetch_offers as _g2g_fetch

logger = logging.getLogger(__name__)

SOURCES: dict[str, Callable[[], Awaitable[list[Offer]]]] = {
    "funpay": _funpay_fetch,
    "g2g": _g2g_fetch,
}

_cache: list[Offer] = []
_history: deque[PriceHistoryPoint] = deque(maxlen=200)


def _record_snapshot(offers: list[Offer]) -> None:
    if not offers:
        return
    prices = [o.price_per_1k for o in offers]
    _history.append(
        PriceHistoryPoint(
            timestamp=datetime.now(timezone.utc),
            avg_price=round(sum(prices) / len(prices), 4),
            min_price=round(min(prices), 4),
            offer_count=len(offers),
        )
    )


def get_price_history() -> list[PriceHistoryPoint]:
    return list(_history)


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
