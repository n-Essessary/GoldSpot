from __future__ import annotations

"""
FunPay HTML-парсер офферов WoW Classic gold.
URL: https://funpay.com/en/chips/114/

Фильтр: только data-online="1" (продавец онлайн).
Зависимости: httpx (уже в проекте), beautifulsoup4.
"""

import logging
import re
import uuid
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup, Tag

from api.schemas import Offer
from utils.server import normalize_server

logger = logging.getLogger(__name__)

SOURCE = "funpay"
_URL = "https://funpay.com/en/chips/114/"

# Минимальный браузерный User-Agent — без него FunPay отдаёт 403
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Онлайн-фильтр: принимаем "1", "true", "yes" — на случай смены формата FunPay
_ONLINE_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes"})

# Селекторы продавца в порядке приоритета.
_SELLER_SELECTORS: tuple[str, ...] = (
    ".media-user-name span",
    ".media-user-name",
    ".tc-seller span",
    ".tc-seller",
    "[data-seller]",
)

# Минимально допустимая длина HTML-страницы.
_MIN_HTML_BYTES = 5_000


def _text(node: Tag | None, selector: str) -> str:
    """
    Возвращает stripped text первого совпадения или ''.
    Никогда не бросает исключение: при любой ошибке BS4 возвращает ''.
    """
    if node is None:
        return ""
    try:
        el = node.select_one(selector)
        return el.get_text(strip=True) if el else ""
    except Exception:
        return ""


def _attr(node: Tag | None, attr: str, default: str = "") -> str:
    """
    Безопасно читает атрибут тега.
    BS4 может вернуть list[str] для multi-valued attributes — берём первый.
    """
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
    """
    Извлекает float из строк вида '0.48 $', '1 234.56', '0,48', '1.234,56'.
    Стратегия: ПОСЛЕДНИЙ разделитель (точка или запятая) — дробный.
    Возвращает None при любой ошибке.
    """
    if not raw:
        return None
    cleaned = re.sub(r"[^\d.,]", "", raw)
    if not cleaned:
        return None

    last_dot = cleaned.rfind(".")
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
    """
    Извлекает int из строк вида '10 000', '10000', '1,000'.
    Возвращает None при любой ошибке.
    """
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def _is_online(item: Tag) -> bool:
    """
    Возвращает True если продавец онлайн.
    """
    raw = _attr(item, "data-online")
    if raw.lower() in _ONLINE_TRUTHY:
        return True

    try:
        if item.select_one(".online-dot, .user-online-icon, [data-online='1']"):
            return True
    except Exception:
        pass

    try:
        name_block = item.select_one(".media-user-name")
        if name_block:
            classes = name_block.get("class") or []
            if isinstance(classes, str):
                classes = classes.split()
            if "online" in classes:
                return True
    except Exception:
        pass

    return False


def _extract_seller(item: Tag) -> str:
    """
    Извлекает имя продавца с fallback.
    """
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

    logger.debug("FunPay: имя продавца не найдено для item id=%s", _attr(item, "data-id"))
    return "unknown"


def _extract_server(item: Tag) -> tuple[str, str]:
    """
    Возвращает (slug, display_server).
    """
    raw = _text(item, ".tc-server")
    if not raw:
        raw = _attr(item, "data-server")

    raw = raw.strip()
    if not raw:
        logger.debug(
            "FunPay: сервер не найден для item id=%s",
            _attr(item, "data-id"),
        )
        return "unknown", "Unknown"

    try:
        srv = normalize_server(raw)
    except Exception as exc:
        logger.warning("FunPay: normalize_server упал для %r — %s", raw, exc)
        slug = re.sub(r"[^a-z0-9-]", "-", raw.lower()).strip("-") or "unknown"
        return slug, raw or "Unknown"

    return srv.slug, srv.display


def _parse_item(item: Tag, fetched_at: datetime) -> Offer:
    """
    Разбирает один .tc-item и возвращает Offer.
    """
    server_slug, display_server = _extract_server(item)
    faction_raw = _text(item, ".tc-side")
    faction = faction_raw if faction_raw in {"Horde", "Alliance"} else (
        "Alliance" if "alli" in faction_raw.lower() else "Horde"
    )
    seller = _extract_seller(item)

    raw_amount = _text(item, ".tc-amount")
    amount_gold = _parse_int(raw_amount)
    if amount_gold is None or amount_gold <= 0:
        logger.debug(
            "FunPay: .tc-amount некорректен (%r) для item id=%s — используем 1",
            raw_amount,
            _attr(item, "data-id"),
        )
        amount_gold = 1

    raw_price = _text(item, ".tc-price")
    price = _parse_float(raw_price)
    if price is None or price <= 0:
        raise ValueError(f".tc-price не распознана: {raw_price!r}")

    price_per_1k = round(price * 1000, 4)
    href = _attr(item, "href")
    offer_url: str | None = None
    if href:
        offer_url = f"https://funpay.com{href}" if href.startswith("/") else href

    raw_id = _attr(item, "data-id") or _attr(item, "data-offer")
    offer_id = f"fp_{raw_id}" if raw_id else f"fp_{uuid.uuid4().hex[:12]}"

    return Offer(
        id=offer_id,
        source=SOURCE,
        server=server_slug,
        display_server=display_server,
        faction=faction,
        price_per_1k=price_per_1k,
        amount_gold=amount_gold,
        seller=seller,
        offer_url=offer_url,
        updated_at=fetched_at,
        fetched_at=fetched_at,
    )


def _parse_html(html: str, fetched_at: datetime) -> list[Offer]:
    """
    Парсит страницу FunPay и возвращает офферы онлайн-продавцов.
    """
    if len(html) < _MIN_HTML_BYTES:
        logger.error(
            "FunPay: слишком короткий HTML (%d байт) — возможна капча или редирект",
            len(html),
        )
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        logger.error("FunPay: BeautifulSoup упал при парсинге — %s", exc)
        return []

    try:
        all_items = soup.select(".tc-item")
    except Exception as exc:
        logger.error("FunPay: soup.select('.tc-item') упал — %s", exc)
        return []

    total_found = len(all_items)
    logger.info("FunPay: найдено .tc-item на странице: %d", total_found)
    if not all_items:
        logger.warning("FunPay: .tc-item не найдены — возможно изменилась вёрстка")
        return []

    _log_first_item_debug(all_items[0])
    online_items = [it for it in all_items if _is_online(it)]
    offline_count = total_found - len(online_items)
    logger.info(
        "FunPay: онлайн %d / всего %d (офлайн пропущено: %d)",
        len(online_items),
        total_found,
        offline_count,
    )

    if not online_items:
        logger.warning(
            "FunPay: ни один продавец не онлайн из %d найденных — "
            "возможно изменился формат data-online",
            total_found,
        )
        online_items = all_items

    offers: list[Offer] = []
    skipped = 0
    skip_reasons: dict[str, int] = {}

    for item in online_items:
        try:
            offers.append(_parse_item(item, fetched_at))
        except (ValueError, TypeError) as exc:
            skipped += 1
            reason = str(exc).split(":")[0].strip()
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            logger.debug("FunPay: пропуск оффера — %s", exc)
        except Exception as exc:
            skipped += 1
            logger.warning("FunPay: неожиданная ошибка при парсинге оффера — %s", exc, exc_info=True)

    logger.info(
        "FunPay: распарсено %d из %d онлайн-офферов",
        len(offers),
        len(online_items),
    )
    if skipped:
        reasons_str = ", ".join(f"{r}×{n}" for r, n in skip_reasons.items())
        logger.warning("FunPay: пропущено %d — причины: %s", skipped, reasons_str)

    return offers


def _log_first_item_debug(first: Tag) -> None:
    """
    Логирует структуру первого .tc-item на уровне DEBUG.
    """
    logger.debug(
        "FunPay [diag] первый .tc-item → "
        "server=%r  side=%r  seller=%r  amount=%r  price=%r  online=%r  attrs=%s",
        _text(first, ".tc-server"),
        _text(first, ".tc-side"),
        _text(first, ".media-user-name span"),
        _text(first, ".tc-amount"),
        _text(first, ".tc-price"),
        _attr(first, "data-online"),
        dict(list(first.attrs.items())[:6]),
    )


async def fetch_funpay_offers() -> list[Offer]:
    """
    Загружает страницу FunPay и возвращает офферы онлайн-продавцов.
    При любой ошибке (сеть, HTTP, парсинг) логирует и возвращает [].
    """
    fetched_at = datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient(
            headers=_HEADERS,
            timeout=15.0,
            follow_redirects=True,
        ) as client:
            resp = await client.get(_URL)

        if resp.status_code != 200:
            logger.warning("FunPay: HTTP %d для %s", resp.status_code, _URL)
        resp.raise_for_status()

        offers = _parse_html(resp.text, fetched_at)
        logger.info("FunPay: итого загружено %d офферов", len(offers))
        return offers

    except httpx.TimeoutException:
        logger.error("FunPay: таймаут при запросе к %s", _URL)
        return []
    except httpx.HTTPStatusError as exc:
        logger.error("FunPay: HTTP-ошибка %d — %s", exc.response.status_code, _URL)
        return []
    except httpx.RequestError as exc:
        logger.error("FunPay: сетевая ошибка — %s", exc)
        return []
    except Exception as exc:
        logger.error("FunPay: неожиданная ошибка — %s", exc, exc_info=True)
        return []


async def fetch_offers() -> list[Offer]:
    return await fetch_funpay_offers()
