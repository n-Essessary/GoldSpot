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
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from api.schemas import Offer, PriceHistoryPoint, ServerGroup

logger = logging.getLogger(__name__)

# ── Per-source state ──────────────────────────────────────────────────────────
_cache:         dict[str, list[Offer]]        = {"funpay": [], "g2g": []}
_last_update:   dict[str, Optional[datetime]] = {"funpay": None, "g2g": None}
_running:       dict[str, bool]               = {"funpay": False, "g2g": False}
# Monotonically-increasing counter — frontend detects updates even if timestamp unchanged
_cache_version: dict[str, int]               = {"funpay": 0, "g2g": 0}

FUNPAY_INTERVAL = 60   # пауза между циклами FunPay (секунд)
G2G_INTERVAL    = 30   # пауза после завершения G2G-цикла

# ── Аналитика — константы ─────────────────────────────────────────────────────
_OUTLIER_MULTIPLIER  = 3.0       # цены > median * 3 — выброс, отбрасываем
_MIN_LIQUID_GOLD     = 50_000    # порог ликвидности для best_ask
_VWAP_GOLD_CAP       = 1_000_000 # лимит накопления объёма для VWAP
_MIN_OFFERS          = 2         # минимум офферов для расчёта индекса

# Канонические имена версий (применять ко всем источникам перед записью в кэш).
_VERSION_ALIASES: dict[str, str] = {
    "seasonal":            "Season of Discovery",
    "season of discovery": "Season of Discovery",
    "sod":                 "Season of Discovery",
    "anniversary":         "Anniversary",
    "classic era":         "Classic Era",
    "classic":             "Classic",
}

# Порядок отображения групп в сайдбаре (меньше = выше).
_VERSION_ORDER: dict[str, int] = {
    "Anniversary":         0,
    "Season of Discovery": 1,
    "Classic Era":         2,
    "Classic":             3,
}


# ── IndexPrice ────────────────────────────────────────────────────────────────

@dataclass
class IndexPrice:
    index_price:  float        # VW-Median — основная линия графика
    vwap:         float        # Volume-Weighted Avg Price — вторая линия
    best_ask:     float        # минимальная цена с ликвидностью >= 50k gold
    price_min:    float        # абсолютный минимум (после фильтра выбросов)
    price_max:    float        # абсолютный максимум (после фильтра выбросов)
    offer_count:  int
    total_volume: int
    sources:      list[str]    # ['funpay', 'g2g', ...]


# In-memory кэш индексных цен: key = "display_server::faction"
_index_cache: dict[str, IndexPrice] = {}


# ── Утилиты ───────────────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    """Нормализует строку сервера: trim + collapse spaces + lowercase."""
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def _canonicalize_version(version: str) -> str:
    """Приводит любое название версии к каноническому имени."""
    return _VERSION_ALIASES.get(version.lower().strip(), version)


def _detect_version(text: str) -> str:
    """Определяет версию из произвольного текста и возвращает каноническое имя."""
    t = _clean(text)
    if "season of discovery" in t or re.search(r"\bsod\b|\bseasonal\b", t):
        return "Season of Discovery"
    if "anniversary" in t:
        return "Anniversary"
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


def _normalize_g2g_offer(offer: Offer) -> Offer:
    """Канонизирует display_server G2G-оффера через _VERSION_ALIASES."""
    ds = offer.display_server or ""
    m = re.match(r"^\((?P<region>[A-Za-z]{2,})\)\s*(?P<version>.+)$", ds)
    if m:
        region  = m.group("region").upper()
        version = _canonicalize_version(m.group("version").strip())
        offer.display_server = f"({region}) {version}"
        offer.server         = offer.display_server
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
            "version":     _cache_version[src],
        }
        for src in ("funpay", "g2g")
    }


# ── Индексная цена ─────────────────────────────────────────────────────────────

def compute_index_price(offers: list[Offer]) -> IndexPrice | None:
    """
    Трёхкомпонентный индекс цены (биржевой подход).

    index_price = Volume-Weighted Median:
        Цена при которой 50% суммарного объёма — ниже неё.
        Устойчива к выбросам: один продавец с огромным объёмом
        не перекашивает результат.

    vwap = Volume-Weighted Average Price по топ-офферам до 1M gold:
        Аналог TradingView VWAP. Учитывает ликвидность.

    best_ask = минимальная цена при которой накоплено >= 50k gold:
        "Реально достижимая цена для покупателя прямо сейчас."
    """
    if not offers or len(offers) < _MIN_OFFERS:
        return None

    # Шаг 1: фильтр выбросов (цены > median * 3)
    prices_sorted = sorted(o.price_per_1k for o in offers)
    raw_median = prices_sorted[len(prices_sorted) // 2]
    clean = [
        o for o in offers
        if o.price_per_1k <= raw_median * _OUTLIER_MULTIPLIER and o.price_per_1k > 0
    ]
    if len(clean) < _MIN_OFFERS:
        clean = [o for o in offers if o.price_per_1k > 0]
    if not clean:
        return None

    clean.sort(key=lambda o: o.price_per_1k)

    # Шаг 2: VW-Median
    total_vol = sum(o.amount_gold for o in clean)
    cumulative, vw_median = 0, clean[0].price_per_1k
    for o in clean:
        cumulative += o.amount_gold
        if cumulative >= total_vol * 0.5:
            vw_median = o.price_per_1k
            break

    # Шаг 3: VWAP по топ-офферам до 1M gold
    selected, acc = [], 0
    for o in clean:
        selected.append(o)
        acc += o.amount_gold
        if acc >= _VWAP_GOLD_CAP:
            break
    total_w = sum(o.amount_gold for o in selected)
    vwap = (
        sum(o.price_per_1k * o.amount_gold for o in selected) / total_w
        if total_w else clean[0].price_per_1k
    )

    # Шаг 4: best_ask — первая цена с накопленным объёмом >= 50k
    acc_ask = 0
    best_ask = clean[0].price_per_1k
    for o in clean:
        acc_ask += o.amount_gold
        best_ask = o.price_per_1k
        if acc_ask >= _MIN_LIQUID_GOLD:
            break

    return IndexPrice(
        index_price  = round(vw_median, 4),
        vwap         = round(vwap, 4),
        best_ask     = round(best_ask, 4),
        price_min    = round(clean[0].price_per_1k, 4),
        price_max    = round(clean[-1].price_per_1k, 4),
        offer_count  = len(clean),
        total_volume = total_vol,
        sources      = sorted({o.source for o in clean}),
    )


# ── Фоновые снимки ────────────────────────────────────────────────────────────

async def _snapshot_all_servers() -> None:
    """
    Вычисляет IndexPrice для всех server+faction комбинаций
    из текущего объединённого кэша, записывает в БД и обновляет _index_cache.
    Вызывается после каждого обновления парсера (non-blocking create_task).
    """
    from db.writer import write_index_snapshot
    ts = datetime.now(timezone.utc)
    all_offers = get_all_offers()

    # Группируем: (display_server, faction) + (display_server, "All")
    groups: dict[tuple[str, str], list[Offer]] = {}
    for o in all_offers:
        ds = o.display_server
        if not ds:
            continue
        # По фракции
        key = (ds, o.faction)
        groups.setdefault(key, []).append(o)
        # Агрегат All
        key_all = (ds, "All")
        groups.setdefault(key_all, []).append(o)

    tasks = []
    for (server, faction), offers in groups.items():
        idx = compute_index_price(offers)
        if idx is not None:
            _index_cache[f"{server}::{faction}"] = idx
            tasks.append(write_index_snapshot(server, faction, idx, ts))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# ── Фоновые циклы ─────────────────────────────────────────────────────────────

async def _run_funpay_loop() -> None:
    from parser.funpay_parser import fetch_offers as fp_fetch

    while True:
        _running["funpay"] = True
        try:
            offers = await fp_fetch()
            offers = [_normalize_funpay_offer(o) for o in offers]
            # Атомарное обновление кэша + версии
            _cache["funpay"] = offers
            _cache_version["funpay"] += 1
            _last_update["funpay"] = datetime.now(timezone.utc)
            logger.info("FunPay updated: %d offers", len(offers))
            # Non-blocking: снимки индексов + запись в БД
            asyncio.create_task(_snapshot_all_servers())
        except Exception:
            logger.exception("FunPay parser failed")
        finally:
            _running["funpay"] = False

        delay = random.uniform(50, 70)
        logger.debug("FunPay next update in %.1fs", delay)
        await asyncio.sleep(delay)


async def _run_g2g_loop() -> None:
    from parser.g2g_parser import fetch_offers as g2g_fetch

    while True:
        _running["g2g"] = True
        t0 = asyncio.get_event_loop().time()
        try:
            offers = await g2g_fetch()
            offers = [_normalize_g2g_offer(o) for o in offers]
            # Атомарное обновление кэша + версии
            _cache["g2g"] = offers
            _cache_version["g2g"] += 1
            _last_update["g2g"] = datetime.now(timezone.utc)
            elapsed = asyncio.get_event_loop().time() - t0
            logger.info("G2G updated: %d offers in %.1fs", len(offers), elapsed)
            # Non-blocking: снимки индексов + запись в БД
            asyncio.create_task(_snapshot_all_servers())
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


def get_price_history(
    server: str = "all",
    faction: str = "all",
    last: int = 50,
) -> list[PriceHistoryPoint]:
    """Текущий снимок из in-memory кэша — для обратной совместимости /price-history."""
    offers = get_all_offers()

    if server != "all":
        offers = [o for o in offers if _clean(o.display_server) == _clean(server)]
    if faction != "all":
        offers = [o for o in offers if o.faction.lower() == faction.lower()]

    result = compute_index_price(offers)
    if result is None:
        return []

    return [
        PriceHistoryPoint(
            timestamp=datetime.now(timezone.utc),
            price=result.index_price,
            min=result.price_min,
            max=result.price_max,
            count=result.offer_count,
        )
    ]


def get_servers() -> list[ServerGroup]:
    """
    Иерархический список групп серверов.

    min_price = best_ask из _index_cache (честная цена покупки).
    Fallback на простой min по офферам если кэш ещё не заполнен.

    Сортировка: версия (Anniversary=0 … Classic=3), затем min_price ASC.
    """
    group_min_price: dict[str, float]    = {}
    group_realms:    dict[str, set[str]] = {}

    for offer in get_all_offers():
        ds = offer.display_server
        if not ds:
            continue
        # Реалмы
        group_realms.setdefault(ds, set())
        if offer.server_name:
            group_realms[ds].add(offer.server_name)
        # Fallback min_price — если кэш ещё не готов
        cur = group_min_price.get(ds)
        if cur is None or offer.price_per_1k < cur:
            group_min_price[ds] = offer.price_per_1k

    # Перекрываем fallback значениями из _index_cache (best_ask)
    for ds in group_min_price:
        cached = _index_cache.get(f"{ds}::All") or _index_cache.get(f"{ds}::Horde")
        if cached is not None:
            group_min_price[ds] = cached.best_ask

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
