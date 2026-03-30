"""
In-memory хранилище офферов.

Lock убран намеренно: asyncio — однопоточный event loop, конкурентного
доступа нет. Вернём RWLock когда появится реальный async DB или threading.

Задел на multi-source: добавить источник = одна строка в SOURCES.
"""
from __future__ import annotations

import logging
import math
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
_history_by_server: dict[str, deque] = {}
_LIQUIDITY_THRESHOLD = 1_000_000
_MIN_OFFERS = 5
_SANE_PRICE_MAX = 5.0  # защита от мусора

# ── Фильтрация выбросов ───────────────────────────────────────────────────────
OUTLIER_TRIM_PCT = 0.05
MIN_PRICE_PER_1K = 0.10
_TRIM_MIN_SAMPLE = 10


def _filter_outliers(offers: list[Offer]) -> list[Offer]:
    """
    Возвращает очищенный от выбросов список офферов.
    """
    result = [o for o in offers if o.price_per_1k > MIN_PRICE_PER_1K]
    removed_abs = len(offers) - len(result)
    if removed_abs:
        logger.info(
            "Фильтр выбросов: удалено %d офферов с ценой ≤ %.2f$/1k",
            removed_abs,
            MIN_PRICE_PER_1K,
        )

    if len(result) < _TRIM_MIN_SAMPLE:
        if result:
            logger.debug(
                "Фильтр выбросов: выборка мала (%d), percentile-отсечение пропущено",
                len(result),
            )
        return result

    prices = sorted(o.price_per_1k for o in result)
    n = len(prices)
    lo_idx = max(0, math.floor(n * OUTLIER_TRIM_PCT))
    hi_idx = min(n - 1, math.ceil(n * (1 - OUTLIER_TRIM_PCT)) - 1)
    lo_price = prices[lo_idx]
    hi_price = prices[hi_idx]

    trimmed = [o for o in result if lo_price <= o.price_per_1k <= hi_price]
    removed_pct = len(result) - len(trimmed)
    if removed_pct:
        logger.info(
            "Фильтр выбросов: percentile [%.0f%%–%.0f%%] → "
            "порог [%.4f, %.4f] $/1k, удалено %d офферов",
            OUTLIER_TRIM_PCT * 100,
            (1 - OUTLIER_TRIM_PCT) * 100,
            lo_price,
            hi_price,
            removed_pct,
        )

    return trimmed


def compute_index_price(
    offers: list[Offer],
) -> tuple[float, float, float] | None:
    if not offers:
        return None

    sorted_offers = sorted(offers, key=lambda o: o.price_per_1k)

    if len(sorted_offers) < _MIN_OFFERS:
        selected = sorted_offers
    else:
        selected = sorted_offers[:_MIN_OFFERS]
        accumulated = sum(o.amount_gold for o in selected)

        for offer in sorted_offers[_MIN_OFFERS:]:
            if accumulated >= _LIQUIDITY_THRESHOLD:
                break
            selected.append(offer)
            accumulated += offer.amount_gold
        else:
            if accumulated < _LIQUIDITY_THRESHOLD:
                selected = sorted_offers

    prices = [o.price_per_1k for o in selected]

    index_price = round(sum(prices) / len(prices), 4)
    min_price = round(min(prices), 4)
    max_price = round(max(prices), 4)

    return index_price, min_price, max_price


def _record_snapshot(offers: list[Offer]) -> None:
    if not offers:
        return

    grouped: dict[str, list[Offer]] = {}
    for o in offers:
        grouped.setdefault(o.server, []).append(o)

    now = datetime.now(timezone.utc)

    for server, items in grouped.items():
        result = compute_index_price(items)
        if result is None:
            continue
        index_price, min_price, max_price = result
        if index_price > _SANE_PRICE_MAX:
            logger.warning(
                "SKIP SNAPSHOT server=%s: index_price=%s > %s",
                server,
                index_price,
                _SANE_PRICE_MAX,
            )
            continue

        _history_by_server.setdefault(server, deque(maxlen=200)).append(
            PriceHistoryPoint(
                timestamp=now,
                price=index_price,
                min=min_price,
                max=max_price,
                count=len(items),
            )
        )


def get_price_history(server: str = "all", last: int = 50) -> list[PriceHistoryPoint]:
    if server == "all":
        combined: list[PriceHistoryPoint] = []
        for dq in _history_by_server.values():
            combined.extend(dq)
        combined.sort(key=lambda x: x.timestamp)
        return combined[-last:]

    dq = _history_by_server.get(server)
    if not dq:
        return []
    return list(dq)[-last:]


def clear_history() -> None:
    global _history_by_server
    _history_by_server = {}


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
    _record_snapshot(_filter_outliers(_cache))


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
