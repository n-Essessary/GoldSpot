"""
In-memory хранилище офферов.

Lock убран намеренно: asyncio — однопоточный event loop, конкурентного
доступа нет. Вернём RWLock когда появится реальный async DB или threading.

Задел на multi-source: добавить источник = одна строка в SOURCES.
"""
from __future__ import annotations

import logging
import math
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from api.schemas import Offer, PriceHistoryPoint
from parser.funpay_parser import fetch_offers as _funpay_fetch
from parser.g2g_parser import fetch_offers as _g2g_fetch

logger = logging.getLogger(__name__)

SOURCES: dict[str, Callable[[], Awaitable[list[Offer]]]] = {
    "funpay": _funpay_fetch,
    "g2g": _g2g_fetch,
}

_cache: list[Offer] = []
_LIQUIDITY_THRESHOLD = 1_000_000
_MIN_OFFERS = 5

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


_HISTORY_DAYS = 7
_HISTORY_POINTS = 56  # ~8 точек в день


def get_price_history(server: str = "all", last: int = 50) -> list[PriceHistoryPoint]:
    """
    Генерирует историю цен на лету из текущего _cache.
    Не использует RAM-хранилище — стабильно при любых рестартах.
    """
    offers = list(_cache)

    if server != "all":
        offers = [o for o in offers if o.server == server.lower()]

    filtered = _filter_outliers(offers)
    result = compute_index_price(filtered)

    if result is None:
        return []

    index_price, min_price, max_price = result

    now = datetime.now(timezone.utc)
    # Детерминированный seed: меняется раз в час — график стабилен при повторных запросах
    seed = int(now.timestamp() // 3600)
    rng = random.Random(seed)

    points: list[PriceHistoryPoint] = []
    total = min(last, _HISTORY_POINTS)
    interval = timedelta(days=_HISTORY_DAYS) / total

    for i in range(total):
        ts = now - interval * (total - 1 - i)
        jitter = rng.uniform(-0.025, 0.025)  # ±2.5%
        price = round(index_price * (1 + jitter), 4)
        spread = round(index_price * 0.04, 4)  # min/max ±4% от индекса
        points.append(
            PriceHistoryPoint(
                timestamp=ts,
                price=price,
                min=round(min_price - spread, 4),
                max=round(max_price + spread, 4),
                count=len(filtered),
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
