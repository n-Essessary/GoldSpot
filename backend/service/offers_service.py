"""
In-memory хранилище офферов.

Lock убран намеренно: asyncio — однопоточный event loop, конкурентного
доступа нет. Вернём RWLock когда появится реальный async DB или threading.

Задел на multi-source: добавить источник = одна строка в SOURCES.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from api.schemas import Offer, PriceHistoryPoint
from parser.funpay_parser import fetch_offers as _funpay_fetch
from parser.g2g_parser import fetch_offers as _g2g_fetch

logger = logging.getLogger(__name__)


def _clean(s: str) -> str:
    """Нормализует строку сервера для устойчивого сравнения.

    Убирает лишние пробелы и приводит к нижнему регистру.
    Не изменяет исходное значение — используется только при сравнении.

    "(EU) Flamegor " → "(eu) flamegor"
    "(EU)  Flamegor" → "(eu)  flamegor"  (двойной пробел тоже схлопнется через strip)
    """
    return s.strip().lower()


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
        selected = []
        accumulated = 0
        for offer in sorted_offers:
            selected.append(offer)
            accumulated += offer.amount_gold
            if accumulated >= _LIQUIDITY_THRESHOLD:
                break

    prices = [o.price_per_1k for o in selected]

    index_price = round(sum(prices) / len(prices), 4)
    min_price = round(min(prices), 4)
    max_price = round(max(prices), 4)

    return index_price, min_price, max_price


def get_price_history(
    server: str = "all",
    faction: str = "all",
    last: int = 50,
) -> list[PriceHistoryPoint]:
    """
    Возвращает 1 реальную точку — текущий index_price из _cache.
    Синтетическое заполнение истории делает фронтенд.
    """
    offers = list(_cache)

    if server != "all":
        offers = [o for o in offers if _clean(o.display_server) == _clean(server)]

    if faction != "all":
        offers = [o for o in offers if o.faction.lower() == faction.lower()]

    result = compute_index_price(offers)

    if result is None:
        return []

    index_price, min_price, max_price = result

    return [
        PriceHistoryPoint(
            timestamp=datetime.now(timezone.utc),
            price=index_price,
            min=min_price,
            max=max_price,
            count=len(offers),
        )
    ]


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


def get_servers() -> list[str]:
    """
    Возвращает уникальные имена серверов из кэша,
    отсортированные по количеству офферов (DESC).

    Использует display_server (читаемое имя) как ключ —
    именно это значение показывается пользователю в UI.

    Пример: ["(EU) Flamegor", "(EU) Firemaw", "(EU) Gehennas"]
    """
    counts: dict[str, int] = {}
    for offer in _cache:
        counts[offer.display_server] = counts.get(offer.display_server, 0) + 1

    return sorted(counts, key=lambda s: counts[s], reverse=True)


def get_offers(
    server: str | None = None,
    faction: str | None = None,
    sort_by: str = "price",
) -> list[Offer]:
    result = list(_cache)

    if server:
        logger.info("FILTER server=%r", server)
        logger.info(
            "AVAILABLE servers sample=%r",
            list({o.display_server for o in _cache})[:5],
        )
        # Сравниваем display_server (RAW) через _clean — устойчиво к
        # лишним пробелам и регистру. Slug (o.server) не используем,
        # потому что фронтенд передаёт RAW строку: "(EU) Flamegor".
        result = [o for o in result if _clean(o.display_server) == _clean(server)]
    if faction:
        result = [o for o in result if o.faction.lower() == faction.lower()]

    # Первичная сортировка: server (display_server для стабильной группировки)
    # Вторичная сортировка: зависит от sort_by (price ASC / amount ASC)
    secondary_key = "price_per_1k" if sort_by == "price" else "amount_gold"
    result.sort(key=lambda o: (o.display_server, getattr(o, secondary_key)))

    return result
