from __future__ import annotations

"""
FunPay HTML-парсер офферов WoW Classic gold — мульти-серверная версия.

Стратегия:
  1. GET https://funpay.com/en/chips/114/
     → парсим <select name="server"> → получаем все server_id
  2. POST https://funpay.com/chips/get/  (game=2&server=<id>)
     → получаем HTML-фрагмент с .tc-item для каждого сервера
  3. Фильтруем data-online="1", парсим офферы, дедуплицируем по offer_id

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
from utils.server import normalize_server

logger = logging.getLogger(__name__)

SOURCE = "funpay"

# ── URLs ────────────────────────────────────────────────────────────────────
_INDEX_URL = "https://funpay.com/en/chips/114/"   # страница с dropdown
_AJAX_URL  = "https://funpay.com/chips/get/"      # XHR-endpoint

# game_id для WoW Classic gold (chips/114 → game=2)
_GAME_ID = "2"

# ── HTTP ────────────────────────────────────────────────────────────────────
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    # Обязателен для AJAX-endpoint — без него FunPay возвращает 403/пустой ответ
    "X-Requested-With": "XMLHttpRequest",
    "Referer": _INDEX_URL,
}

# ── Параметры параллелизма ───────────────────────────────────────────────────
# FunPay легко детектит слишком частые запросы → держим concurrency умеренным
_CONCURRENCY = 2
_DELAY_BETWEEN = 0.75
_REQUEST_TIMEOUT = 12.0 # секунды
_RETRY_COUNT = 3
_RETRY_BACKOFF = (1.0, 2.0, 4.0)

# ── Прочие константы ─────────────────────────────────────────────────────────
_ONLINE_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes"})
_MIN_HTML_BYTES = 200   # фрагмент намного короче полной страницы

_SELLER_SELECTORS: tuple[str, ...] = (
    ".media-user-name span",
    ".media-user-name",
    ".tc-seller span",
    ".tc-seller",
    "[data-seller]",
)


# ────────────────────────────────────────────────────────────────────────────
# Вспомогательные утилиты (перенесены без изменений из v1)
# ────────────────────────────────────────────────────────────────────────────

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


def _is_online(item: Tag) -> bool:
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
    try:
        srv = normalize_server(raw)
    except Exception as exc:
        logger.warning("FunPay: normalize_server упал для %r — %s", raw, exc)
        slug = re.sub(r"[^a-z0-9-]", "-", raw.lower()).strip("-") or "unknown"
        return slug, raw or "Unknown"
    return srv.slug, srv.display


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

    raw_price = _text(item, ".tc-price")
    price = _parse_float(raw_price)
    if price is None or price <= 0:
        raise ValueError(f".tc-price не распознана: {raw_price!r}")

    price_per_1k = round(price * 1000, 4)
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
        price_per_1k=price_per_1k,
        amount_gold=amount_gold,
        seller=seller,
        offer_url=offer_url,
        updated_at=fetched_at,
        fetched_at=fetched_at,
    )


# ────────────────────────────────────────────────────────────────────────────
# ШАГ 1 — получение server_id из dropdown главной страницы
# ────────────────────────────────────────────────────────────────────────────

def _extract_server_ids(html: str) -> list[str]:
    """
    Парсит <select name="server"> из HTML-страницы /en/chips/114/.
    Возвращает список строковых ID (value атрибут <option>).
    Пустые строки и «0» (плейсхолдер) пропускаются.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Ищем select с name="server" или id="server" или class, содержащим "server"
    select = (
        soup.find("select", {"name": "server"})
        or soup.find("select", {"id": "server"})
    )

    if select is None:
        # Fallback: ищем все select и берём тот, у которого больше всего <option> с числовым value
        all_selects = soup.find_all("select")
        best = max(
            all_selects,
            key=lambda s: sum(
                1 for o in s.find_all("option")
                if re.fullmatch(r"\d+", (o.get("value") or "").strip()) and o["value"] != "0"
            ),
            default=None,
        )
        select = best

    if select is None:
        logger.error("FunPay: <select name='server'> не найден в HTML")
        return []

    ids: list[str] = []
    for opt in select.find_all("option"):
        val = (opt.get("value") or "").strip()
        if re.fullmatch(r"\d+", val) and val != "0":
            ids.append(val)

    logger.info("FunPay: найдено server_id в dropdown: %d", len(ids))
    return ids


# ────────────────────────────────────────────────────────────────────────────
# ШАГ 2 — загрузка офферов одного сервера через AJAX endpoint
# ────────────────────────────────────────────────────────────────────────────

def _parse_fragment(html_fragment: str, fetched_at: datetime) -> list[Offer]:
    """
    Парсит HTML-фрагмент ответа /chips/get/ → список Offer.
    Логика идентична _parse_html из v1, но без проверки минимального размера.
    """
    if not html_fragment or not html_fragment.strip():
        return []

    try:
        soup = BeautifulSoup(html_fragment, "html.parser")
    except Exception as exc:
        logger.error("FunPay fragment: BS4 упал — %s", exc)
        return []

    all_items = soup.select(".tc-item")
    if not all_items:
        return []

    online_items = [it for it in all_items if _is_online(it)]
    if not online_items:
        # Если вдруг data-online не выставлен — берём всё (деградация)
        logger.debug("FunPay fragment: нет online-продавцов из %d, берём всех", len(all_items))
        online_items = all_items

    offers: list[Offer] = []
    for item in online_items:
        try:
            offers.append(_parse_item(item, fetched_at))
        except (ValueError, TypeError) as exc:
            logger.debug("FunPay fragment: пропуск оффера — %s", exc)
        except Exception as exc:
            logger.warning("FunPay fragment: неожиданная ошибка — %s", exc, exc_info=True)
    return offers


async def _fetch_server(
    client: httpx.AsyncClient,
    server_id: str,
    fetched_at: datetime,
    semaphore: asyncio.Semaphore,
) -> list[Offer]:
    """
    Делает один POST /chips/get/ для конкретного server_id.
    Возвращает офферы или [] при любой ошибке.
    """
    async with semaphore:
        await asyncio.sleep(_DELAY_BETWEEN)

        last_exc: Exception | None = None

        for attempt in range(_RETRY_COUNT):
            try:
                resp = await client.post(
                    _AJAX_URL,
                    data={"game": _GAME_ID, "server": server_id},
                )

                if resp.status_code == 404:
                    logger.debug("FunPay: server_id=%s не найден (404), пропускаем", server_id)
                    return []

                if resp.status_code == 429:
                    backoff = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                    logger.warning(
                        "FunPay: 429 rate-limit для server_id=%s, попытка %d/%d, ждём %.1fs",
                        server_id, attempt + 1, _RETRY_COUNT, backoff,
                    )
                    await asyncio.sleep(backoff)
                    last_exc = httpx.HTTPStatusError(
                        message="429 Too Many Requests",
                        request=resp.request,
                        response=resp,
                    )
                    continue

                resp.raise_for_status()

                offers = _parse_fragment(resp.text, fetched_at)
                logger.debug(
                    "FunPay server_id=%s → %d офферов (HTTP %d, %d байт)",
                    server_id, len(offers), resp.status_code, len(resp.text),
                )
                return offers

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    logger.debug("FunPay: server_id=%s не найден (404), пропускаем", server_id)
                    return []
                if exc.response.status_code == 429:
                    backoff = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                    logger.warning(
                        "FunPay: 429 rate-limit для server_id=%s, попытка %d/%d, ждём %.1fs",
                        server_id, attempt + 1, _RETRY_COUNT, backoff,
                    )
                    await asyncio.sleep(backoff)
                    last_exc = exc
                    continue
                logger.warning(
                    "FunPay: HTTP %d для server_id=%s",
                    exc.response.status_code, server_id,
                )
                return []

            except httpx.TimeoutException:
                logger.warning(
                    "FunPay: таймаут для server_id=%s, попытка %d/%d",
                    server_id, attempt + 1, _RETRY_COUNT,
                )
                last_exc = None
                if attempt < _RETRY_COUNT - 1:
                    backoff = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                    await asyncio.sleep(backoff)
                continue

            except Exception as exc:
                logger.warning("FunPay: ошибка для server_id=%s — %s", server_id, exc)
                return []

        logger.warning(
            "FunPay: server_id=%s — исчерпаны все %d попытки, последняя ошибка: %s",
            server_id, _RETRY_COUNT, last_exc,
        )
        return []


# ────────────────────────────────────────────────────────────────────────────
# ШАГ 3 — публичная точка входа
# ────────────────────────────────────────────────────────────────────────────

async def fetch_funpay_offers() -> list[Offer]:
    """
    1. Загружает главную страницу → извлекает все server_id из dropdown.
    2. Параллельно (с ограничением concurrency) запрашивает /chips/get/
       для каждого сервера.
    3. Объединяет офферы, дедуплицирует по offer.id, возвращает результат.
    """
    fetched_at = datetime.now(timezone.utc)

    async with httpx.AsyncClient(
        headers=_HEADERS,
        timeout=_REQUEST_TIMEOUT,
        follow_redirects=True,
    ) as client:

        # ── Шаг 1: получаем список серверов ──────────────────────────────
        try:
            index_resp = await client.get(_INDEX_URL)
            index_resp.raise_for_status()
        except httpx.TimeoutException:
            logger.error("FunPay: таймаут при загрузке index %s", _INDEX_URL)
            return []
        except httpx.HTTPStatusError as exc:
            logger.error("FunPay: HTTP %d при загрузке index", exc.response.status_code)
            return []
        except Exception as exc:
            logger.error("FunPay: ошибка загрузки index — %s", exc, exc_info=True)
            return []

        server_ids = _extract_server_ids(index_resp.text)
        if not server_ids:
            logger.error("FunPay: server_id не найдены — прерываем")
            return []

        logger.info("FunPay: начинаем опрос %d серверов (concurrency=%d)", len(server_ids), _CONCURRENCY)

        # ── Шаг 2: параллельные запросы к AJAX endpoint ───────────────────
        semaphore = asyncio.Semaphore(_CONCURRENCY)
        tasks = [
            _fetch_server(client, sid, fetched_at, semaphore)
            for sid in server_ids
        ]
        results: list[list[Offer]] = await asyncio.gather(*tasks)

    successful = sum(1 for batch in results if batch)
    logger.info(
        "FunPay: успешно получены офферы с %d/%d серверов",
        successful, len(server_ids),
    )

    # ── Шаг 3: объединяем + дедупликация ──────────────────────────────────
    seen: set[str] = set()
    all_offers: list[Offer] = []
    for batch in results:
        for offer in batch:
            if offer.id not in seen:
                seen.add(offer.id)
                all_offers.append(offer)

    logger.info(
        "FunPay: итого %d уникальных офферов с %d серверов",
        len(all_offers), len(server_ids),
    )
    return all_offers


async def fetch_offers() -> list[Offer]:
    return await fetch_funpay_offers()
