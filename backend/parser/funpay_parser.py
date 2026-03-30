from __future__ import annotations

"""
FunPay HTML-парсер офферов WoW Classic gold.
URL: https://funpay.com/en/chips/114/

Фильтр: только data-online="1"  (продавец онлайн).
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


# ── Утилиты парсинга текста ───────────────────────────────────────────────────

def _text(node: Tag | None, selector: str) -> str:
    """Возвращает stripped text первого совпадения или ''."""
    if node is None:
        return ""
    el = node.select_one(selector)
    return el.get_text(strip=True) if el else ""


def _parse_float(raw: str | None) -> float | None:
    """
    Извлекает float из строк вида '0.48 $', '1 234.56', '0,48', '1.234,56'.
    Стратегия: ПОСЛЕДНИЙ разделитель (точка или запятая) — дробный.
    Возвращает None при любой ошибке.
    """
    if not raw:
        return None
    # Убираем всё кроме цифр, точки и запятой
    cleaned = re.sub(r"[^\d.,]", "", raw)
    if not cleaned:
        return None

    last_dot   = cleaned.rfind(".")
    last_comma = cleaned.rfind(",")

    if last_dot == -1 and last_comma == -1:
        # Только цифры
        pass
    elif last_dot == -1:
        # Только запятые: последняя — дробный разделитель
        # Убираем тысячные запятые, последнюю → точка
        parts = cleaned.rsplit(",", 1)
        cleaned = parts[0].replace(",", "") + "." + parts[1]
    elif last_comma == -1:
        # Только точки: если их больше одной — все кроме последней тысячные
        parts = cleaned.split(".")
        if len(parts) > 2:
            cleaned = "".join(parts[:-1]) + "." + parts[-1]
    elif last_comma > last_dot:
        # Запятая стоит позже → она дробный разделитель, точки — тысячные
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        # Точка стоит позже → она дробный разделитель, запятые — тысячные
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


# ── Нормализация полей ────────────────────────────────────────────────────────


def _seller(item: Tag) -> str:
    """
    Пробует несколько CSS-селекторов для имени продавца.
    FunPay периодически меняет вёрстку — проверяем каждый вариант по очереди.
    """
    for selector in (
        ".media-user-name span",   # основной вариант
        ".media-user-name",        # если span пропал
        ".tc-seller span",         # альтернативный класс
        ".tc-seller",
    ):
        val = _text(item, selector)
        if val:
            return val
    return "unknown"


# ── Парсинг HTML ──────────────────────────────────────────────────────────────

def _parse_item(item: Tag, fetched_at: datetime) -> Offer:
    """
    Разбирает один .tc-item и возвращает Offer.
    Бросает ValueError только если цена отсутствует или нулевая —
    всё остальное заполняется fallback-значениями.
    """
    srv_info = normalize_server(_text(item, ".tc-server"))
    server   = srv_info.slug     # "flamegor"
    display_server = srv_info.display  # "Flamegor (EU)"
    faction  = _text(item, ".tc-side") or "Horde"
    seller   = _seller(item)
    raw_amount = _text(item, ".tc-amount")
    raw_price  = _text(item, ".tc-price")

    # Цена — единственное hard-required поле: без неё оффер бессмысленен
    price = _parse_float(raw_price)
    if price is None or price <= 0:
        raise ValueError(f".tc-price не распознана: {raw_price!r}")

    # Количество — fallback 1, чтобы не ломать Offer.amount_gold > 0
    amount_gold = _parse_int(raw_amount)
    if amount_gold is None or amount_gold <= 0:
        logger.debug("FunPay: .tc-amount некорректен (%r), используем 1", raw_amount)
        amount_gold = 1

    # Цена дана за 1 gold → приводим к стандарту проекта: price per 1 000 gold
    price_per_1k = round(price * 1000, 4)

    # offer_url: .tc-item сам является <a href="...">
    href = item.get("href", "")
    if isinstance(href, list):          # BS4 может вернуть list для мульти-атрибутов
        href = href[0] if href else ""
    offer_url: str | None = None
    if href:
        offer_url = (
            f"https://funpay.com{href}" if href.startswith("/") else href
        )

    # ID: data-id или data-offer на элементе, иначе — UUID-фрагмент
    raw_id = item.get("data-id") or item.get("data-offer") or ""
    offer_id = f"fp_{raw_id}" if raw_id else f"fp_{uuid.uuid4().hex[:12]}"

    return Offer(
        id=offer_id,
        source=SOURCE,
        server=server,
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
    """Парсит страницу и возвращает все офферы (.tc-item).
    Фильтр data-online временно снят для диагностики.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Все офферы без фильтра по онлайн-статусу (временно для диагностики)
    all_items = soup.select(".tc-item")
    logger.info("FunPay: найдено .tc-item на странице: %d", len(all_items))

    # Диагностика: логируем поля первого элемента, чтобы понять реальную структуру
    if all_items:
        first = all_items[0]
        logger.warning(
            "FunPay [diag] первый .tc-item → "
            "server=%r  side=%r  seller=%r  amount=%r  price=%r  attrs=%s",
            _text(first, ".tc-server"),
            _text(first, ".tc-side"),
            _text(first, ".media-user-name span"),
            _text(first, ".tc-amount"),
            _text(first, ".tc-price"),
            dict(list(first.attrs.items())[:6]),  # первые 6 атрибутов элемента
        )

    offers: list[Offer] = []
    skipped = 0
    skip_reasons: dict[str, int] = {}

    for item in all_items:
        try:
            offers.append(_parse_item(item, fetched_at))
        except (ValueError, TypeError) as exc:
            skipped += 1
            reason = str(exc).split(":")[0]          # группируем по типу ошибки
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            logger.debug("FunPay: пропуск оффера (%s)", exc)

    logger.info(
        "FunPay: распарсено %d из %d элементов", len(offers), len(all_items)
    )
    if skipped:
        logger.warning(
            "FunPay: пропущено %d — причины: %s",
            skipped,
            ", ".join(f"{r}×{n}" for r, n in skip_reasons.items()),
        )

    return offers


# ── Публичный API ─────────────────────────────────────────────────────────────

async def fetch_funpay_offers() -> list[Offer]:
    """
    Загружает страницу FunPay и возвращает офферы онлайн-продавцов.
    При любой ошибке (сеть, HTTP, парсинг) логирует её и возвращает [].
    """
    fetched_at = datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient(
            headers=_HEADERS,
            timeout=10.0,
            follow_redirects=True,
        ) as client:
            resp = await client.get(_URL)
            resp.raise_for_status()

        offers = _parse_html(resp.text, fetched_at)
        logger.info("FunPay: загружено %d офферов", len(offers))
        return offers

    except Exception as exc:
        logger.error("FunPay: ошибка загрузки — %s", exc)
        return []


# Алиас для совместимости с offers_service.SOURCES
async def fetch_offers() -> list[Offer]:
    return await fetch_funpay_offers()
