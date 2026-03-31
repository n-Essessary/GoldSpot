from __future__ import annotations

"""
G2G парсер офферов WoW Classic Era gold.

Стратегия (двухуровневая):
  1. Прямой запрос без region_id (page=1..N).
     Если data непустой → используем его.
  2. Fallback: динамически получаем region_id со страниц категории,
     затем параллельно (semaphore=2) опрашиваем API для каждого ID.

Получение region_id:
  - Scrape pages 1–5: https://www.g2g.com/categories/wow-classic-era-vanilla-gold?page=N
  - Извлекаем ссылки вида /offer/group?fa=...&region_id=XXXX
  - Regex: region_id=([a-z0-9-]+)
  - Собираем уникальные значения

Антибот:
  - retry × 3 с exponential backoff при ошибке / 429 / 5xx
  - delay 1–2 сек между запросами к API
  - timeout 15 сек
"""

import asyncio
import logging
import random
import re
import uuid
from datetime import datetime, timezone

import httpx

from api.schemas import Offer

logger = logging.getLogger(__name__)

SOURCE = "g2g"

# ── Regex для парсинга заголовка G2G ──────────────────────────────────────────
# "Spineshatter [EU - Anniversary] - Alliance"
# "Nightslayer [US - Anniversary #2] - Horde"
# "Bloodsail Buccaneers [US - Classic Era] - Alliance"
_TITLE_RE = re.compile(
    r"^(?P<server>.+?)\s*"           # server_name (нежадный до первой '[')
    r"\[(?P<region>[A-Za-z]{2,})"   # [EU / US / KR / TW ...
    r"\s*-\s*"
    r"(?P<version>[^\]]+?)\s*\]"    # версия до ']', обрезаем пробелы
    r"\s*-\s*"
    r"(?P<faction>Alliance|Horde)",  # фракция (case-insensitive ниже)
    re.IGNORECASE,
)

# ── Regex для извлечения region_id из HTML ────────────────────────────────────
_REGION_ID_RE = re.compile(r"region_id=([a-z0-9-]+)")

# ── Константы ─────────────────────────────────────────────────────────────────
_SEARCH_API   = "https://sls.g2g.com/offer/search"
_CATEGORY_URL = "https://www.g2g.com/categories/wow-classic-era-vanilla-gold"
_SEO_TERM     = "wow-classic-era-vanilla-gold"
_PAGE_SIZE    = 100
_MAX_PAGES    = 10
_SCRAPE_PAGES = 5    # страниц категории для сбора region_id
_RETRY_COUNT  = 3
_TIMEOUT      = 15.0
_CONCURRENCY  = 2    # параллельных запросов к API

_API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

_HTML_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_faction(title: str) -> str:
    """Извлекает фракцию из RAW title.
    "Nightslayer [US - Anniversary] - Horde"  → "Horde"
    "Bloodsail Buccaneers - Alliance"         → "Alliance"
    """
    t = title.lower()
    if "alliance" in t:
        return "Alliance"
    if "horde" in t:
        return "Horde"
    return "Horde"  # default


def parse_g2g_title(title: str) -> tuple[str, str, str, str]:
    """Парсит RAW заголовок G2G в компоненты сервера.

    Примеры:
        "Spineshatter [EU - Anniversary] - Alliance"
        → ("Spineshatter", "EU", "Anniversary", "Alliance")

        "Nightslayer [US - Anniversary #2] - Horde"
        → ("Nightslayer", "US", "Anniversary #2", "Horde")

        "Bloodsail Buccaneers [US - Classic Era] - Alliance"
        → ("Bloodsail Buccaneers", "US", "Classic Era", "Alliance")

    Returns:
        (server_name, region, version, faction)
        При неудаче парсинга region и version будут пустыми,
        server_name = полный title, faction = fallback через _extract_faction.
    """
    m = _TITLE_RE.match(title.strip())
    if not m:
        logger.debug("G2G parse_g2g_title: не совпал шаблон для %r", title)
        return title.strip(), "", "", _extract_faction(title)

    server_name = m.group("server").strip()
    region      = m.group("region").strip().upper()   # нормализуем регистр
    version     = m.group("version").strip()
    faction     = m.group("faction").strip().capitalize()  # "alliance" → "Alliance"

    return server_name, region, version, faction


async def _http_get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    retries: int = _RETRY_COUNT,
) -> httpx.Response:
    """GET с retry × retries и exponential backoff (1s → 2s → 4s)."""
    last_exc: Exception = RuntimeError("no attempts")
    for attempt in range(retries):
        try:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            # retry only for 429 and 5xx
            if status == 429 or status >= 500:
                wait = 2 ** attempt * (3 if status == 429 else 1)
                logger.warning(
                    "G2G: HTTP %d для %s, попытка %d/%d, ждём %.0fs",
                    status, url, attempt + 1, retries, wait,
                )
                last_exc = exc
                await asyncio.sleep(wait)
                continue
            # fail-fast for other 4xx
            logger.warning("G2G: HTTP %d для %s — fail-fast", status, url)
            raise
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            wait = 2 ** attempt
            logger.warning(
                "G2G: %s для %s, попытка %d/%d, ждём %.0fs",
                type(exc).__name__, url, attempt + 1, retries, wait,
            )
            last_exc = exc
            await asyncio.sleep(wait)
    raise last_exc


# ── Парсинг JSON → Offer ──────────────────────────────────────────────────────

def parse_g2g(data: dict, fetched_at: datetime) -> list[Offer]:
    """Преобразует JSON-ответ /offer/search (все страницы) в list[Offer].

    Каждый item содержит поле title — RAW строка вида:
      "Spineshatter [EU - Anniversary] - Alliance"
    parse_g2g_title() нормализует её в:
      display_server = "(EU) Anniversary"  — совпадает с форматом FunPay
      server_name    = "Spineshatter"      — сервер внутри группы
      faction        = "Alliance"
    """
    items = data.get("data") or data.get("results") or []
    if not isinstance(items, list):
        logger.debug("G2G: parse_g2g — пустые данные")
        return []

    offers: list[Offer] = []
    skipped = 0

    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            # ── Заголовок оффера определяет сервер / фракцию ─────────────────
            raw_title = (
                item.get("title")
                or item.get("name")
                or item.get("offer_title")
                or ""
            )
            server_name, region, version, faction = parse_g2g_title(raw_title)

            if region and version:
                display_server = f"({region}) {version}"  # "(EU) Anniversary"
            else:
                display_server = server_name
                if raw_title:
                    logger.warning(
                        "G2G: не удалось нормализовать заголовок %r, "
                        "display_server=%r",
                        raw_title, display_server,
                    )

            # ── Цена и объём ──────────────────────────────────────────────────
            unit_price = item.get("unit_price") or item.get("unitPrice")
            amount     = (
                item.get("available_qty")
                or item.get("quantity")
                or item.get("amount")
            )

            if unit_price is None or amount is None:
                skipped += 1
                continue

            unit_price = float(unit_price)
            amount     = int(float(amount))

            if unit_price <= 0 or amount <= 0:
                skipped += 1
                continue

            price_per_1k = round(unit_price * 1000.0, 4)

            # ── ID / URL / продавец ───────────────────────────────────────────
            raw_id    = item.get("offer_id") or item.get("id") or ""
            offer_id  = f"g2g_{raw_id}" if raw_id else f"g2g_{uuid.uuid4().hex[:12]}"
            offer_url = f"https://www.g2g.com/offer/{raw_id}" if raw_id else None
            seller    = str(item.get("username") or item.get("seller") or "unknown").strip()

            # ── Дата обновления ───────────────────────────────────────────────
            raw_ts = (
                item.get("updated_at")
                or item.get("updatedAt")
                or item.get("updated")
            )
            try:
                updated_at = (
                    datetime.fromisoformat(str(raw_ts)).astimezone(timezone.utc)
                    if raw_ts
                    else fetched_at
                )
            except (ValueError, TypeError):
                updated_at = fetched_at

            offers.append(Offer(
                id=offer_id,
                source=SOURCE,
                server=display_server,         # model_validator → lowercase slug
                display_server=display_server,  # "(EU) Anniversary"
                server_name=server_name,        # "Spineshatter"
                faction=faction,
                price_per_1k=price_per_1k,
                amount_gold=amount,
                seller=seller,
                offer_url=offer_url,
                updated_at=updated_at,
                fetched_at=fetched_at,
            ))

        except (ValueError, TypeError, KeyError) as exc:
            skipped += 1
            logger.debug("G2G: пропуск оффера %r — %s", item.get("offer_id"), exc)

    if skipped:
        logger.debug("G2G: пропущено %d/%d офферов", skipped, len(items))

    return offers


# ── Низкоуровневые функции пагинации ──────────────────────────────────────────

def _build_params(page: int, region_id: str | None = None) -> dict:
    """Строит params для /offer/search."""
    params: dict = {
        "seo_term":  _SEO_TERM,
        "country":   "UA",
        "currency":  "USD",
        "page_size": _PAGE_SIZE,
        "page":      page,
        "sort":      "recommended_v2",
        "v":         "v2",
    }
    if region_id is not None:
        params["region_id"] = region_id
    return params


async def _fetch_all_pages(
    client: httpx.AsyncClient,
    region_id: str | None,
) -> list[dict]:
    """Собирает все страницы для одного region_id (или без него).

    Возвращает сырой список item-словарей.
    """
    all_items: list[dict] = []
    label = f"region_id={region_id!r}" if region_id else "без region_id"

    for page in range(1, _MAX_PAGES + 1):
        params = _build_params(page, region_id)
        try:
            resp = await _http_get_with_retry(
                client, _SEARCH_API, params=params, headers=_API_HEADERS,
            )
        except Exception as exc:
            logger.warning("G2G: страница %d (%s) недоступна: %s", page, label, exc)
            break

        try:
            payload = resp.json()
        except Exception as exc:
            logger.warning("G2G: невалидный JSON на странице %d (%s): %s", page, label, exc)
            break

        page_items = payload.get("data") or []
        if not isinstance(page_items, list) or not page_items:
            logger.debug("G2G: страница %d (%s) пуста — стоп", page, label)
            break

        all_items.extend(page_items)
        logger.debug(
            "G2G: страница %d (%s) → %d офферов (всего %d)",
            page, label, len(page_items), len(all_items),
        )

        if len(page_items) < _PAGE_SIZE:
            # Последняя страница — данных меньше чем page_size
            break

    return all_items


# ── Стратегия 1: прямой запрос без region_id ─────────────────────────────────

async def _fetch_direct(client: httpx.AsyncClient) -> list[dict]:
    """Запрос без region_id. Возвращает items или []."""
    items = await _fetch_all_pages(client, region_id=None)
    if items:
        logger.info("G2G: прямой запрос вернул %d офферов", len(items))
    else:
        logger.info("G2G: прямой запрос вернул 0 офферов → переходим к fallback")
    return items


# ── Получение реальных region_id со страниц категории ────────────────────────

async def fetch_g2g_region_ids(client: httpx.AsyncClient) -> list[str]:
    """Scrape страниц 1–_SCRAPE_PAGES категории G2G и собирает уникальные region_id.

    Ищет ссылки вида /offer/group?fa=...&region_id=XXXX
    и извлекает region_id через regex.

    Возвращает список уникальных region_id (порядок — порядок первого появления).
    """
    seen: dict[str, None] = {}  # OrderedDict-like через обычный dict (Python 3.7+)

    for page in range(1, _SCRAPE_PAGES + 1):
        url = f"{_CATEGORY_URL}?page={page}"
        try:
            resp = await _http_get_with_retry(
                client, url, headers=_HTML_HEADERS,
            )
            html = resp.text
        except Exception as exc:
            logger.warning("G2G: не удалось загрузить страницу категории %d: %s", page, exc)
            continue

        found = _REGION_ID_RE.findall(html)
        new_count = 0
        for rid in found:
            if rid not in seen:
                seen[rid] = None
                new_count += 1

        logger.debug(
            "G2G: страница категории %d → найдено %d новых region_id (всего %d)",
            page, new_count, len(seen),
        )

        # Небольшая пауза между scrape-запросами
        await asyncio.sleep(random.uniform(1.0, 2.0))

    region_ids = list(seen.keys())
    logger.info("G2G: собрано уникальных region_id: %d → %s", len(region_ids), region_ids)
    return region_ids


# ── Стратегия 2: fallback через динамически полученные region_id ──────────────

async def _fetch_one_region(
    client: httpx.AsyncClient,
    region_id: str,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    """Запрос для одного region_id под семафором с задержкой (антибот)."""
    async with semaphore:
        items = await _fetch_all_pages(client, region_id=region_id)
        # Задержка после освобождения семафора, чтобы следующий запрос
        # не стартовал мгновенно
        await asyncio.sleep(random.uniform(1.0, 2.0))
        return items


async def _fetch_fallback(client: httpx.AsyncClient) -> list[dict]:
    """Динамически получает region_id и параллельно опрашивает API.

    1. Scrape страниц категории → уникальные region_id.
    2. Параллельный обход (semaphore=_CONCURRENCY=2).
    """
    region_ids = await fetch_g2g_region_ids(client)

    if not region_ids:
        logger.warning("G2G: не удалось получить ни одного region_id — fallback пуст")
        return []

    semaphore = asyncio.Semaphore(_CONCURRENCY)
    tasks = [
        _fetch_one_region(client, rid, semaphore)
        for rid in region_ids
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items: list[dict] = []
    for region_id, result in zip(region_ids, results):
        if isinstance(result, Exception):
            logger.warning("G2G: fallback region_id=%r ошибка: %s", region_id, result)
        elif result:
            logger.debug("G2G: fallback region_id=%r → %d офферов", region_id, len(result))
            all_items.extend(result)

    return all_items


# ── Дедупликация ──────────────────────────────────────────────────────────────

def _dedupe(offers: list[Offer]) -> list[Offer]:
    seen: set[str] = set()
    result: list[Offer] = []
    for o in offers:
        if o.id not in seen:
            seen.add(o.id)
            result.append(o)
    return result


# ── Основная функция ──────────────────────────────────────────────────────────

async def fetch_g2g_all() -> list[Offer]:
    """Собирает все офферы G2G.

    Алгоритм:
      1. Прямой запрос без region_id.
         Если вернул данные → используем их.
      2. Fallback: scrape region_id со страниц категории,
         параллельный обход (semaphore=2).
         Результаты объединяются и дедуплицируются.
    """
    fetched_at = datetime.now(timezone.utc)
    used_fallback = False

    async with httpx.AsyncClient(
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers=_API_HEADERS,
    ) as client:

        # ── Шаг 1: прямой запрос ─────────────────────────────────────────────
        all_items = await _fetch_direct(client)

        # ── Шаг 2: fallback если прямой запрос пуст ──────────────────────────
        if not all_items:
            used_fallback = True
            all_items = await _fetch_fallback(client)

    if not all_items:
        logger.warning("G2G: API вернул 0 офферов (direct + fallback)")
        return []

    offers = parse_g2g({"data": all_items}, fetched_at)
    result = _dedupe(offers)

    dupes = len(offers) - len(result)
    if used_fallback:
        logger.info(
            "G2G: fallback region_id used, offers=%d (raw=%d, дубли=%d)",
            len(result), len(offers), dupes,
        )
    else:
        logger.info(
            "G2G: всего офферов: %d (raw=%d, дубли=%d)",
            len(result), len(offers), dupes,
        )

    return result


# ── Entry point (вызывается из offers_service.SOURCES) ───────────────────────

async def fetch_offers() -> list[Offer]:
    """Публичная точка входа — вызывается из offers_service.

    При любой ошибке возвращает [] — не ломает общий refresh.
    """
    try:
        return await fetch_g2g_all()
    except Exception:
        logger.exception("G2G: критическая ошибка в fetch_g2g_all — возвращаем []")
        return []
