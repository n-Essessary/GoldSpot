"""
In-memory хранилище офферов.

Раздельные фоновые циклы на каждый парсер:
  - FunPay: запускается сразу, обновление каждые 60 с после завершения
  - G2G:    запускается сразу, обновление каждые 30 с после завершения

HTTP-handlers читают из _cache без ожидания (< 5 мс).
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, timezone
from typing import Optional

from api.schemas import Offer, PriceHistoryPoint, ServerGroup

logger = logging.getLogger(__name__)

# ── Per-source state ──────────────────────────────────────────────────────────
_cache:       dict[str, list[Offer]]        = {"funpay": [], "g2g": []}
_last_update: dict[str, Optional[datetime]] = {"funpay": None, "g2g": None}
_running:     dict[str, bool]               = {"funpay": False, "g2g": False}

FUNPAY_INTERVAL = 60   # пауза между циклами FunPay (секунд)
G2G_INTERVAL    = 30   # пауза после завершения G2G-цикла

# ── Аналитика ─────────────────────────────────────────────────────────────────
_LIQUIDITY_THRESHOLD = 1_000_000
_MIN_OFFERS          = 5
OUTLIER_TRIM_PCT     = 0.05
MIN_PRICE_PER_1K     = 0.10
_TRIM_MIN_SAMPLE     = 10

_VERSION_ORDER: dict[str, int] = {
    "Anniversary":         0,
    "Seasonal":            1,
    "Classic Era":         2,
    "Classic":             3,
    "Season of Discovery": 4,
}


# ── Утилиты ───────────────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    """Нормализует строку сервера: trim + collapse spaces + lowercase."""
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
    return "Classic"


def _normalize_funpay_offer(offer: Offer) -> Offer:
    """Приводит FunPay-оффер к формату display_server=(EU) Version."""
    raw = (offer.display_server or "").strip()
    m = re.match(r"^\((?P<region>[A-Za-z]{2,})\)\s*(?P<body>.*)$", raw)
    if not m:
        return offer

    region = m.group("region").upper()
    body   = (m.group("body") or "").strip()
    version = _detect_version(body)
    realm   = ""

    if " - " in body:
        left, right = body.rsplit(" - ", 1)
        realm   = right.strip()
        version = _detect_version(left or body)
    else:
        realm = body.strip()

    offer.display_server = f"({region}) {version}"
    if realm:
        offer.server_name = realm
    return offer


def _version_rank(display_server: str) -> int:
    ds = display_server.strip()
    for ver, rank in _VERSION_ORDER.items():
        if ver.lower() in ds.lower():
            return rank
    return 99


# ── Публичный агрегат ─────────────────────────────────────────────────────────

def get_all_offers() -> list[Offer]:
    """Объединяет кэши всех источников."""
    return _cache["funpay"] + _cache["g2g"]


def get_parser_status() -> dict:
    """Возвращает состояние каждого парсера для /parser-status."""
    return {
        src: {
            "offers":      len(_cache[src]),
            "last_update": _last_update[src].isoformat() if _last_update[src] else None,
            "running":     _running[src],
        }
        for src in ("funpay", "g2g")
    }


# ── Фоновые циклы ─────────────────────────────────────────────────────────────

async def _run_funpay_loop() -> None:
    from parser.funpay_parser import fetch_offers as fp_fetch

    while True:
        _running["funpay"] = True
        try:
            offers = await fp_fetch()
            offers = [_normalize_funpay_offer(o) for o in offers]
            _cache["funpay"] = offers
            _last_update["funpay"] = datetime.now(timezone.utc)
            logger.info("FunPay updated: %d offers", len(offers))
        except Exception:
            logger.exception("FunPay parser failed")
        finally:
            _running["funpay"] = False
        await asyncio.sleep(FUNPAY_INTERVAL)


async def _run_g2g_loop() -> None:
    from parser.g2g_parser import fetch_offers as g2g_fetch

    while True:
        _running["g2g"] = True
        t0 = asyncio.get_event_loop().time()
        try:
            offers = await g2g_fetch()
            _cache["g2g"] = offers
            _last_update["g2g"] = datetime.now(timezone.utc)
            elapsed = asyncio.get_event_loop().time() - t0
            logger.info("G2G updated: %d offers in %.1fs", len(offers), elapsed)
        except Exception:
            logger.exception("G2G parser failed")
        finally:
            _running["g2g"] = False
        await asyncio.sleep(G2G_INTERVAL)


async def start_background_parsers() -> None:
    """Запускает фоновые циклы FunPay и G2G. Вызвать один раз в lifespan."""
    asyncio.create_task(_run_funpay_loop())
    asyncio.create_task(_run_g2g_loop())
    logger.info("Background parsers started (funpay + g2g)")


# ── Публичный API (читают из кэша) ────────────────────────────────────────────

def get_meta() -> Optional[datetime]:
    """UTC-время последнего успешного обновления любого источника."""
    updates = [t for t in _last_update.values() if t is not None]
    return max(updates) if updates else None


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
    min_price   = round(min(prices), 4)
    max_price   = round(max(prices), 4)
    return index_price, min_price, max_price


def get_price_history(
    server: str = "all",
    faction: str = "all",
    last: int = 50,
) -> list[PriceHistoryPoint]:
    offers = get_all_offers()

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


def get_servers() -> list[ServerGroup]:
    """
    Иерархический список групп серверов.

    Сортировка: версия (Anniversary=0 … SoD=4), затем min_price ASC.
    Реалмы: только непустые server_name (G2G), алфавитная сортировка.
    """
    group_min_price: dict[str, float]     = {}
    group_realms:    dict[str, set[str]]  = {}

    for offer in get_all_offers():
        ds = offer.display_server
        if not ds:
            continue

        cur = group_min_price.get(ds)
        if cur is None or offer.price_per_1k < cur:
            group_min_price[ds] = offer.price_per_1k

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
    result = get_all_offers()

    if server:
        result = [o for o in result if _clean(o.display_server) == _clean(server)]

    if server_name:
        result = [
            o for o in result
            if _clean(o.server_name) == _clean(server_name)
        ]

    if faction:
        result = [o for o in result if o.faction.lower() == faction.lower()]

    if sort_by == "price":
        result.sort(key=lambda o: (o.price_per_1k, -o.amount_gold))
    else:
        result.sort(key=lambda o: (-o.amount_gold, o.price_per_1k))

    return result
