"""
In-memory хранилище офферов.

Lock убран намеренно: asyncio — однопоточный event loop, конкурентного
доступа нет. Вернём RWLock когда появится реальный async DB или threading.

Задел на multi-source: добавить источник = одна строка в SOURCES.
"""
from __future__ import annotations

import logging
import math
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from api.schemas import Offer, PriceHistoryPoint, ServerGroup
from parser.funpay_parser import fetch_offers as _funpay_fetch
from parser.g2g_parser import fetch_offers as _g2g_fetch

logger = logging.getLogger(__name__)


def _clean(s: str) -> str:
    """Нормализует строку сервера для устойчивого сравнения.

    Убирает ведущие/хвостовые пробелы, схлопывает внутренние пробелы,
    приводит к нижнему регистру. Не изменяет исходное значение.

    "(EU) Flamegor "  → "(eu) flamegor"
    "(EU)  Anniversary" → "(eu) anniversary"
    "(AU) Anniversary"  → "(au) anniversary"  ← НЕ равно "(eu) anniversary"
    """
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _detect_version(text: str) -> str:
    t = _clean(text)
    if "season of discovery" in t or re.search(r"\bsod\b", t):
        return "Season of Discovery"
    if "anniversary" in t:
        return "Anniversary"
    if re.search(r"\bseasonal\b", t):
        return "Seasonal"
    if "classic era" in t:
        return "Classic Era"
    if "classic" in t:
        return "Classic"
    return "Classic"


def _normalize_funpay_offer(offer: Offer) -> Offer:
    raw = (offer.display_server or "").strip()
    m = re.match(r"^\((?P<region>[A-Za-z]{2,})\)\s*(?P<body>.*)$", raw)
    if not m:
        return offer

    region = m.group("region").upper()
    body = (m.group("body") or "").strip()
    version = _detect_version(body)
    realm = ""

    if " - " in body:
        left, right = body.rsplit(" - ", 1)
        realm = right.strip()
        version = _detect_version(left or body)
    else:
        realm = body.strip()

    offer.display_server = f"({region}) {version}"
    if realm:
        offer.server_name = realm
    return offer


SOURCES: dict[str, Callable[[], Awaitable[list[Offer]]]] = {
    "funpay": _funpay_fetch,
    "g2g": _g2g_fetch,
}

_cache: list[Offer] = []
_last_update: datetime | None = None  # UTC-время последнего успешного refresh()
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
    global _cache, _last_update
    all_offers: list[Offer] = []

    for source_name, fetch_fn in SOURCES.items():
        try:
            offers = await fetch_fn()
            if source_name == "funpay":
                offers = [_normalize_funpay_offer(o) for o in offers]
            all_offers.extend(offers)
            logger.info("Источник %s: загружено %d офферов", source_name, len(offers))
        except Exception:
            logger.exception("Источник %s: ошибка загрузки", source_name)

    _cache = all_offers
    _last_update = datetime.now(timezone.utc)

    # ── Диагностика после обновления ─────────────────────────────────────────
    servers_count: dict[str, int] = {}
    unknown_count = 0
    for o in _cache:
        ds = o.display_server or "Unknown"
        if ds == "Unknown":
            unknown_count += 1
        servers_count[ds] = servers_count.get(ds, 0) + 1

    logger.info(
        "Кэш обновлён: %d офферов, %d серверов%s",
        len(_cache),
        len(servers_count),
        f", Unknown={unknown_count}" if unknown_count else "",
    )

    if logger.isEnabledFor(logging.DEBUG):
        for ds, cnt in sorted(servers_count.items(), key=lambda x: -x[1]):
            logger.debug("  %-35s %d офферов", ds, cnt)


def get_meta() -> datetime | None:
    """Возвращает UTC-время последнего успешного обновления кэша."""
    return _last_update


_VERSION_ORDER: dict[str, int] = {
    "Anniversary":         0,
    "Seasonal":            1,
    "Classic Era":         2,
    "Classic":             3,
    "Season of Discovery": 4,
}


def _version_rank(display_server: str) -> int:
    """Возвращает приоритет версии по display_server."""
    ds = display_server.strip()
    for ver, rank in _VERSION_ORDER.items():
        if ver.lower() in ds.lower():
            return rank
    return 99


def get_servers() -> list[ServerGroup]:
    """
    Возвращает иерархический список групп серверов.

    Каждая группа: display_server + список реалмов + min_price.

    Реалмы — уникальные непустые server_name внутри группы (только G2G).
    FunPay-офферы имеют server_name="" и в realms не попадают.

    Сортировка групп:
      1. Версия (Anniversary=0 … SoD=4)
      2. Минимальная цена ASC

    Сортировка реалмов: алфавитная.
    """
    # Собираем данные по каждой группе
    group_min_price: dict[str, float] = {}
    group_realms: dict[str, set[str]] = {}

    for offer in _cache:
        ds = offer.display_server
        if not ds:
            continue

        # min_price по группе
        cur = group_min_price.get(ds)
        if cur is None or offer.price_per_1k < cur:
            group_min_price[ds] = offer.price_per_1k

        # realms: только непустые server_name
        if ds not in group_realms:
            group_realms[ds] = set()
        if offer.server_name:
            group_realms[ds].add(offer.server_name)

    sorted_groups = sorted(
        group_min_price,
        key=lambda s: (_version_rank(s), group_min_price[s]),
    )

    return [
        ServerGroup(
            display_server=ds,
            realms=sorted(group_realms.get(ds, set())),
            min_price=round(group_min_price[ds], 4),
        )
        for ds in sorted_groups
    ]


def get_offers(
    server: str | None = None,
    faction: str | None = None,
    sort_by: str = "price",
    server_name: str | None = None,
) -> list[Offer]:
    result = list(_cache)

    if server:
        logger.debug("FILTER server=%r", server)
        logger.debug(
            "AVAILABLE servers sample=%r",
            list({o.display_server for o in _cache})[:5],
        )
        # Сравниваем display_server (RAW) через _clean — устойчиво к
        # лишним пробелам и регистру.
        result = [o for o in result if _clean(o.display_server) == _clean(server)]

    if server_name:
        # Строгий фильтр по реалму: "Spineshatter" != "Soulseeker" != "".
        # FunPay-офферы (server_name="") не проходят — они не принадлежат
        # конкретному реалму G2G. Пользователь видит их на уровне группы
        # (без server_name), но не при выборе конкретного реалма.
        result = [
            o for o in result
            if _clean(o.server_name) == _clean(server_name)
        ]

    if faction:
        result = [o for o in result if o.faction.lower() == faction.lower()]

    # Сортировка офферов внутри выборки:
    #   sort_by="price"  → цена ASC, затем количество DESC (больше золота — лучше при той же цене)
    #   sort_by="amount" → количество DESC, затем цена ASC
    if sort_by == "price":
        result.sort(key=lambda o: (o.price_per_1k, -o.amount_gold))
    else:
        result.sort(key=lambda o: (-o.amount_gold, o.price_per_1k))

    return result
