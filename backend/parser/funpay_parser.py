from __future__ import annotations

"""
FunPay HTML-парсер офферов WoW Classic gold.

Стратегия:
  GET https://funpay.com/en/chips/114/
  → HTML уже содержит ВСЕ офферы (.tc-item) для всех серверов
  → парсим, группируем по серверу, дедуплицируем, возвращаем flat list

Зависимости: httpx, beautifulsoup4.
"""

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup, Tag

from api.schemas import Offer

logger = logging.getLogger(__name__)

SOURCE = "funpay"

# ── URL ─────────────────────────────────────────────────────────────────────
_URL = "https://funpay.com/en/chips/114/"

# ── Online-фильтр ────────────────────────────────────────────────────────────
# Значения data-online, которые считаются «онлайн»
_ONLINE_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes"})
_TIMEOUT = 15.0

# ── HTTP заголовки ───────────────────────────────────────────────────────────
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Селекторы продавца ───────────────────────────────────────────────────────
_SELLER_SELECTORS: tuple[str, ...] = (
    ".media-user-name span",
    ".media-user-name",
    ".tc-seller span",
    ".tc-seller",
    "[data-seller]",
)


# ── Вспомогательные функции ──────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# ONLINE-ФИЛЬТР — 3 варианта реализации
#
# ВАРИАНТ 1 (активный): data-online attribute — строгий, минимальный.
#   Читает только атрибут data-online="1".
#   Плюсы: детерминировано, не зависит от CSS.
#   Минусы: если FunPay уберёт атрибут — фильтр сломается.
#
# ВАРИАНТ 2: data-online + CSS-индикатор — мягкий фallback.
#   Если атрибута нет — ищет DOM-элемент онлайн-иконки.
#   Плюсы: устойчив к частичным изменениям вёрстки.
#   Минусы: CSS-классы могут меняться.
#
# ВАРИАНТ 3: мульти-сигнальный — максимально устойчивый.
#   Проверяет атрибут, CSS-классы блока продавца и DOM-иконки.
#   Плюсы: наименее хрупкий при рефакторинге вёрстки.
#   Минусы: чуть сложнее, выше риск false-positive.
#
# Для переключения замените тело функции _is_online на нужный вариант.
# ─────────────────────────────────────────────────────────────────────────────

def _is_online(item: Tag) -> bool:
    """
    ВАРИАНТ 1 (активный): читаем только data-online="1".
    Самый простой и надёжный способ — FunPay всегда проставляет атрибут.
    """
    return _attr(item, "data-online").lower() in _ONLINE_TRUTHY


# def _is_online(item: Tag) -> bool:
#     """
#     ВАРИАНТ 2: data-online + fallback на DOM-иконку.
#     Используй, если data-online иногда отсутствует.
#     """
#     if _attr(item, "data-online").lower() in _ONLINE_TRUTHY:
#         return True
#     return bool(item.select_one(".online-dot, .user-online-icon"))


# def _is_online(item: Tag) -> bool:
#     """
#     ВАРИАНТ 3: мульти-сигнальный — атрибут + CSS-классы + DOM-элементы.
#     Используй, если структура HTML нестабильна.
#     """
#     # Сигнал 1: data-online атрибут
#     if _attr(item, "data-online").lower() in _ONLINE_TRUTHY:
#         return True
#     # Сигнал 2: CSS-класс "online" в блоке имени продавца
#     name_block = item.select_one(".media-user-name")
#     if name_block:
#         classes = " ".join(name_block.get("class") or [])
#         if "online" in classes.lower():
#             return True
#     # Сигнал 3: DOM-элементы онлайн-индикатора
#     return bool(item.select_one(".online-dot, .user-online-icon, [class*='online']"))


def _text(node: Tag | None, selector: str) -> str:
    if node is None:
        return ""
    try:
        el = node.select_one(selector)
        return el.get_text(strip=True) if el else ""
    except Exception:
        return ""


def _attr(node: Tag | None, attr: str, default: str = "") -> str:
    if node is None:
        return default
    try:
        val = node.get(attr, default)
        if isinstance(val, list):
            val = val[0] if val else default
        return str(val).strip() if val is not None else default
    except Exception:
        return default


def _parse_float(raw: str | None) -> float | None:
    if not raw:
        return None
    cleaned = re.sub(r"[^\d.,]", "", raw)
    if not cleaned:
        return None
    last_dot   = cleaned.rfind(".")
    last_comma = cleaned.rfind(",")
    if last_dot == -1 and last_comma == -1:
        pass
    elif last_dot == -1:
        parts = cleaned.rsplit(",", 1)
        cleaned = parts[0].replace(",", "") + "." + parts[1]
    elif last_comma == -1:
        parts = cleaned.split(".")
        if len(parts) > 2:
            cleaned = "".join(parts[:-1]) + "." + parts[-1]
    elif last_comma > last_dot:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_int(raw: str | None) -> int | None:
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def _extract_seller(item: Tag) -> str:
    for selector in _SELLER_SELECTORS:
        try:
            val = _text(item, selector)
            val = re.sub(r"[\u200b\u00a0\s]+", " ", val).strip()
            if val and val.lower() not in {"", "-", "n/a"}:
                return val
        except Exception:
            continue
    for attr_name in ("data-seller", "data-user", "data-username"):
        val = _attr(item, attr_name)
        if val and val.lower() not in {"", "-", "n/a"}:
            return val
    return "unknown"


def _extract_server(item: Tag) -> tuple[str, str]:
    raw = _text(item, ".tc-server") or "Unknown"
    raw = raw.strip()
    if not raw:
        return "unknown", "Unknown"
    # Возвращаем RAW строку без каких-либо преобразований.
    # server = raw (model_validator в Offer приведёт к lowercase для slug).
    # display_server = raw — точно как на FunPay.
    return raw, raw


def _parse_item(item: Tag, fetched_at: datetime) -> Offer:
    server_slug, display_server = _extract_server(item)

    faction_raw = _text(item, ".tc-side")
    faction = faction_raw if faction_raw in {"Horde", "Alliance"} else (
        "Alliance" if "alli" in faction_raw.lower() else "Horde"
    )

    seller = _extract_seller(item)
    if not seller or seller == "unknown":
        seller = _attr(item, "data-seller") or "unknown"

    raw_amount = _text(item, ".tc-amount")
    amount_gold = _parse_int(raw_amount)
    if amount_gold is None or amount_gold <= 0:
        amount_gold = 1

    raw_price_text = _text(item, ".tc-price")
    lot_price = _parse_float(raw_price_text)
    if lot_price is None or lot_price <= 0:
        raise ValueError(f".tc-price not recognised: {raw_price_text!r}")

    href = _attr(item, "href")
    offer_url: str | None = None
    if href:
        offer_url = f"https://funpay.com{href}" if href.startswith("/") else href

    m = re.search(r"id=([\d\-]+)", href)
    offer_id = f"fp_{m.group(1)}" if m else f"fp_{uuid.uuid4().hex[:12]}"

    return Offer(
        id=offer_id,
        source=SOURCE,
        server=server_slug,
        display_server=display_server,
        faction=faction,
        # ── Raw price (Task 2) ────────────────────────────────────────────────
        # FunPay shows price per lot (e.g. "1000 gold for $X").
        # raw_price = lot_price (price for amount_gold gold, in USD)
        # raw_price_unit = 'per_lot'
        # lot_size = amount_gold
        # price_per_1k is derived: (lot_price / amount_gold) * 1000
        raw_price=lot_price,
        raw_price_unit="per_lot",
        lot_size=amount_gold,
        amount_gold=amount_gold,
        seller=seller,
        offer_url=offer_url,
        updated_at=fetched_at,
        fetched_at=fetched_at,
    )


def _group_by_server(offers: list[Offer]) -> dict[str, list[Offer]]:
    """Группирует офферы по display_server."""
    grouped: dict[str, list[Offer]] = {}
    for offer in offers:
        grouped.setdefault(offer.display_server, []).append(offer)
    return grouped


def _parse_html(html: str, fetched_at: datetime) -> list[Offer]:
    """
    Синхронный разбор HTML (BeautifulSoup + DOM). Вызывать через asyncio.to_thread.
    """
    if not html or not html.strip():
        logger.warning("FunPay: получен пустой HTML")
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        logger.error("FunPay: BeautifulSoup упал — %s", exc)
        return []

    items = soup.select(".tc-item")
    if not items:
        logger.warning("FunPay: .tc-item не найдены в HTML — возможно изменилась вёрстка")
        return []

    online_items = [it for it in items if _is_online(it)]
    if not online_items:
        logger.warning(
            "FunPay: онлайн-офферов не найдено из %d total — возможно изменился data-online",
            len(items),
        )
        return []
    logger.debug("FunPay: online=%d / total=%d", len(online_items), len(items))
    items = online_items

    raw_offers: list[Offer] = []
    for item in items:
        try:
            raw_offers.append(_parse_item(item, fetched_at))
        except (ValueError, TypeError) as exc:
            logger.debug("FunPay: пропуск оффера — %s", exc)
        except Exception as exc:
            logger.warning("FunPay: неожиданная ошибка — %s", exc, exc_info=True)

    grouped = _group_by_server(raw_offers)

    seen: set[str] = set()
    unique: list[Offer] = []
    for server_offers in grouped.values():
        for offer in server_offers:
            if offer.id not in seen:
                seen.add(offer.id)
                unique.append(offer)

    logger.info("FunPay: servers=%d offers=%d", len(grouped), len(unique))
    return unique


# ── Публичная точка входа ────────────────────────────────────────────────────

async def fetch_funpay_offers() -> list[Offer]:
    """
    GET https://funpay.com/en/chips/114/
    Парсит все .tc-item из полного HTML, группирует по серверу,
    дедуплицирует по offer.id, возвращает flat list[Offer].
    """
    fetched_at = datetime.now(timezone.utc)
    html = ""

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                timeout=_TIMEOUT,
                follow_redirects=True,
            ) as client:
                resp = await client.get(_URL)
                if resp.status_code == 429:
                    ra = resp.headers.get("Retry-After", "60")
                    try:
                        retry_after = int(ra)
                    except ValueError:
                        retry_after = 60
                    if attempt < 2:
                        logger.warning(
                            "FunPay 429 rate limited — backing off %ds",
                            retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        continue
                if resp.status_code >= 500:
                    if attempt < 2:
                        await asyncio.sleep(2**attempt)
                        continue
                resp.raise_for_status()
                html = resp.text
            break
        except httpx.TimeoutException:
            if attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            logger.error("FunPay: таймаут при запросе %s", _URL)
            return []
        except httpx.HTTPStatusError as exc:
            sc = exc.response.status_code
            if sc >= 500 and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            logger.error("FunPay: HTTP %d при запросе %s", sc, _URL)
            return []
        except Exception as exc:
            logger.error("FunPay: ошибка запроса — %s", exc, exc_info=True)
            return []

    if not html:
        return []

    return await asyncio.to_thread(_parse_html, html, fetched_at)


async def fetch_offers() -> list[Offer]:
    return await fetch_funpay_offers()
