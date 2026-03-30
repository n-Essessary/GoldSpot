from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from api.schemas import Offer
from config import settings
from utils.server import normalize_server

logger = logging.getLogger(__name__)

SOURCE = "g2g"


# ── Payload unwrapping ────────────────────────────────────────────────────────
# G2G возвращает {"results": [...]} или плоский список.
# Добавьте ключи если реальный API отличается.

def _unwrap_payload(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        for key in ("results", "offers", "data", "items", "list"):
            block = raw.get(key)
            if isinstance(block, list):
                return [x for x in block if isinstance(x, dict)]
    raise ValueError("G2G JSON: ожидался list или dict со списком офферов")


# ── Вспомогательные функции (идентичны fanpay_parser) ────────────────────────

def _pick_number(item: dict, *keys: str) -> float | None:
    for k in keys:
        if k in item and item[k] is not None:
            try:
                return float(item[k])
            except (TypeError, ValueError):
                continue
    return None


def _pick_int(item: dict, *keys: str) -> int | None:
    for k in keys:
        if k in item and item[k] is not None:
            try:
                return int(float(item[k]))
            except (TypeError, ValueError):
                continue
    return None


def _pick_str(item: dict, *keys: str, default: str = "") -> str:
    for k in keys:
        v = item.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return default


# ── Нормализация ──────────────────────────────────────────────────────────────
# G2G хранит цену в unit_price (за 1 gold) или price (за лот).
# Оба варианта обрабатываются через _pick_number с приоритетом ключей.

def _normalize(item: dict, fetched_at: datetime) -> Offer:
    # G2G отдаёт цену за единицу (1 gold) → умножаем на 1000 для price_per_1k
    # Если ключ unit_price есть — используем его напрямую
    unit_price = _pick_number(item, "unit_price", "unitPrice")
    if unit_price is not None and unit_price > 0:
        raw_amount = _pick_int(item, "amount", "quantity", "stock") or 1
        raw_price_per_1k = unit_price * 1000.0
    else:
        # Fallback: цена за весь лот
        raw_price = _pick_number(item, "price", "total_price", "totalPrice")
        raw_amount = _pick_int(item, "amount", "quantity", "stock")
        if raw_price is None or raw_amount is None or raw_amount <= 0:
            raise ValueError(
                f"G2G: нужны price и amount > 0, получили: price={raw_price}, amount={raw_amount}"
            )
        if raw_price <= 0:
            raise ValueError(f"G2G: price должен быть > 0, получили {raw_price}")
        raw_price_per_1k = (raw_price / raw_amount) * 1000.0

    seller   = _pick_str(item, "seller", "username", "user", "name", default="unknown")
    server_raw = _pick_str(item, "server", "realm", "region",         default="unknown")
    srv_info = normalize_server(server_raw)
    faction  = _pick_str(item, "faction", "side", "team",             default="Horde")
    offer_url = _pick_str(item, "url", "offer_url", "link", "permalink") or None

    fid = item.get("id") or item.get("offer_id")
    offer_id = f"g2g_{fid}" if fid not in (None, "") else f"g2g_{uuid.uuid4().hex[:12]}"

    raw_updated = item.get("updated_at") or item.get("updatedAt") or item.get("updated")
    try:
        updated_at = (
            datetime.fromisoformat(str(raw_updated)).astimezone(timezone.utc)
            if raw_updated
            else fetched_at
        )
    except (ValueError, TypeError):
        updated_at = fetched_at

    return Offer(
        id=offer_id,
        source=SOURCE,
        server=srv_info.slug,
        display_server=srv_info.display,
        faction=faction,
        price_per_1k=round(raw_price_per_1k, 4),
        amount_gold=raw_amount,
        seller=seller,
        offer_url=offer_url,
        updated_at=updated_at,
        fetched_at=fetched_at,
    )


# ── Mock ──────────────────────────────────────────────────────────────────────

def _mock_offers() -> list[Offer]:
    now = datetime.now(timezone.utc)
    return [
        Offer(
            id="g2g_demo_1",
            source=SOURCE,
            server="strizhant-eu",
            display_server="Стрижант-EU",
            faction="Horde",
            price_per_1k=0.44,
            amount_gold=80_000,
            seller="G2GTrader",
            offer_url="https://www.g2g.com/offers/demo_1",
            updated_at=now,
            fetched_at=now,
        ),
        Offer(
            id="g2g_demo_2",
            source=SOURCE,
            server="strizhant-eu",
            display_server="Стрижант-EU",
            faction="Alliance",
            price_per_1k=0.38,
            amount_gold=200_000,
            seller="EliteGold",
            offer_url="https://www.g2g.com/offers/demo_2",
            updated_at=now,
            fetched_at=now,
        ),
        Offer(
            id="g2g_demo_3",
            source=SOURCE,
            server="gordunni-eu",
            display_server="Гордунни-EU",
            faction="Alliance",
            price_per_1k=0.47,
            amount_gold=15_000,
            seller="QuickSeller",
            offer_url=None,
            updated_at=now,
            fetched_at=now,
        ),
    ]


# ── Entry point ───────────────────────────────────────────────────────────────

async def fetch_offers() -> list[Offer]:
    """
    Получает офферы с G2G.
    URL задаётся через переменную окружения WOW_GOLD_G2G_OFFERS_URL.
    При пустом URL или ошибке — поведение определяется use_mock_on_fetch_failure.
    """
    url = (getattr(settings, "g2g_offers_url", "") or "").strip()
    if not url:
        if settings.use_mock_on_fetch_failure:
            logger.info("G2G URL пуст — отдаём демо-офферы")
            return _mock_offers()
        return []

    fetched_at = datetime.now(timezone.utc)

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        raw = resp.json()

    items = _unwrap_payload(raw)
    offers: list[Offer] = []
    skipped = 0

    for row in items:
        try:
            offers.append(_normalize(row, fetched_at))
        except (ValueError, TypeError) as e:
            skipped += 1
            logger.debug("G2G: пропуск строки оффера: %s", e)

    if skipped:
        logger.warning("G2G: пропущено %d из %d записей", skipped, len(items))

    if not offers:
        raise ValueError(f"G2G: после нормализации список пуст (всего записей: {len(items)})")

    return offers
