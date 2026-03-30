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

SOURCE = "fanpay"


def _unwrap_payload(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        for key in ("offers", "data", "items", "results", "list"):
            block = raw.get(key)
            if isinstance(block, list):
                return [x for x in block if isinstance(x, dict)]
    raise ValueError("FanPay JSON: ожидался list или dict со списком офферов")


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


def _normalize(item: dict, fetched_at: datetime) -> Offer:
    raw_price = _pick_number(item, "price", "total_usd", "totalPrice", "sum_usd")
    raw_amount = _pick_int(item, "amount", "amount_gold", "gold", "quantity")

    if raw_price is None or raw_amount is None or raw_amount <= 0:
        raise ValueError(
            f"Нужны числовые price и amount > 0, получили: price={raw_price}, amount={raw_amount}"
        )
    if raw_price <= 0:
        raise ValueError(f"price должен быть > 0, получили {raw_price}")

    price_per_1k = (raw_price / raw_amount) * 1000.0

    seller = _pick_str(item, "seller", "user", "name", "login", default="unknown")
    server_raw = _pick_str(item, "server", "realm", "world", default="unknown")
    srv_info = normalize_server(server_raw)
    faction = _pick_str(item, "faction", "side", default="Horde")
    offer_url = _pick_str(item, "url", "offer_url", "link") or None

    fid = item.get("id")
    offer_id = str(fid) if fid not in (None, "") else f"fp_{uuid.uuid4().hex[:12]}"

    raw_updated = item.get("updated_at") or item.get("updatedAt")
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
        price_per_1k=round(price_per_1k, 4),
        amount_gold=raw_amount,
        seller=seller,
        offer_url=offer_url,
        updated_at=updated_at,
        fetched_at=fetched_at,
    )


def _mock_offers() -> list[Offer]:
    now = datetime.now(timezone.utc)
    return [
        Offer(
            id="fp_demo_1",
            source=SOURCE,
            server="strizhant-eu",
            display_server="Стрижант-EU",
            faction="Horde",
            price_per_1k=0.42,
            amount_gold=50_000,
            seller="GoldShop99",
            offer_url="https://fanpay.ru/offers/demo_1",
            updated_at=now,
            fetched_at=now,
        ),
        Offer(
            id="fp_demo_2",
            source=SOURCE,
            server="strizhant-eu",
            display_server="Стрижант-EU",
            faction="Alliance",
            price_per_1k=0.39,
            amount_gold=120_000,
            seller="SafeGold",
            offer_url="https://fanpay.ru/offers/demo_2",
            updated_at=now,
            fetched_at=now,
        ),
        Offer(
            id="fp_demo_3",
            source=SOURCE,
            server="gordunni-eu",
            display_server="Гордунни-EU",
            faction="Horde",
            price_per_1k=0.51,
            amount_gold=30_000,
            seller="FastTrade",
            offer_url=None,
            updated_at=now,
            fetched_at=now,
        ),
    ]


async def fetch_offers() -> list[Offer]:
    url = (settings.fanpay_offers_url or "").strip()
    if not url:
        if settings.use_mock_on_fetch_failure:
            logger.info("FANPAY URL пуст — отдаём демо-офферы")
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
            logger.debug("Пропуск строки оффера: %s", e)

    if skipped:
        logger.warning("FanPay: пропущено %d из %d записей", skipped, len(items))

    if not offers:
        raise ValueError(f"После нормализации список офферов пуст (всего записей: {len(items)})")

    return offers
