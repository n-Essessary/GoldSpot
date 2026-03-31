from __future__ import annotations

"""
G2G парсер офферов WoW Classic Era gold.

Стратегия (dataset_id из HTML):
  1. Scrape страниц 1–_SCRAPE_PAGES категории G2G.
     Regex: fa=lgc_27816_dropdown_18:([a-z0-9_]+)
     → набор уникальных dataset_id.
  2. Для каждого dataset_id — GET /offer/search
       params: offer_attributes=lgc_27816_dropdown_18:<slug>
  3. Все офферы объединяются и дедуплицируются.

Антибот:
  - retry × 3 с exponential backoff при ошибке / 429 / 5xx
  - semaphore=2 параллельных запроса к /offer/search
  - delay 1–2 сек между запросами одного воркера
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
_TITLE_RE = re.compile(
    r"^(?P<server>.+?)\s*"
    r"\[(?P<region>[A-Za-z]{2,})"
    r"\s*-\s*"
    r"(?P<version>[^\]]+?)\s*\]"
    r"\s*-\s*"
    r"(?P<faction>Alliance|Horde)",
    re.IGNORECASE,
)

# ── Regex для извлечения dataset slug из HTML ─────────────────────────────────
# Ищем: fa=lgc_27816_dropdown_18:<slug>
# offer_attributes в API = полная строка: lgc_27816_dropdown_18:<slug>
_DATASET_RE = re.compile(
    r"fa=lgc_27816_dropdown_18:(lgc_27816_dropdown_18_[a-z0-9_]+)",
    re.IGNORECASE,
)
_ATTR_PREFIX = "lgc_27816_dropdown_18"

# ── Константы ─────────────────────────────────────────────────────────────────
_SEARCH_API   = "https://sls.g2g.com/offer/search"
_CATEGORY_URL = "https://www.g2g.com/categories/wow-classic-era-vanilla-gold"

_SEO_TERM    = "wow-classic-era-vanilla-gold"
_PAGE_SIZE   = 100
_MAX_PAGES   = 10
_SCRAPE_PAGES = 5   # страниц категории для сбора dataset_id
_RETRY_COUNT = 3
_TIMEOUT     = 15.0
_CONCURRENCY = 2    # параллельных воркеров при обходе dataset_id

_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_HTML_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_faction(title: str) -> str:
    t = title.lower()
    if "alliance" in t:
        return "Alliance"
    if "horde" in t:
        return "Horde"
    return "Horde"


def parse_g2g_title(title: str) -> tuple[str, str, str, str]:
    """Парсит RAW заголовок G2G → (server_name, region, version, faction).

    "Spineshatter [EU - Anniversary] - Alliance"
      → ("Spineshatter", "EU", "Anniversary", "Alliance")
    """
    m = _TITLE_RE.match(title.strip())
    if not m:
        logger.debug("G2G parse_g2g_title: не совпал шаблон для %r", title)
        return title.strip(), "", "", _extract_faction(title)

    return (
        m.group("server").strip(),
        m.group("region").strip().upper(),
        m.group("version").strip(),
        m.group("faction").strip().capitalize(),
    )


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
            if status == 429 or status >= 500:
                wait = 2 ** attempt * (3 if status == 429 else 1)
                logger.warning(
                    "G2G: HTTP %d для %s, попытка %d/%d, ждём %.0fs",
                    status, url, attempt + 1, retries, wait,
                )
                last_exc = exc
                await asyncio.sleep(wait)
                continue
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
    """Преобразует JSON-ответ /offer/search в list[Offer]."""
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
            raw_title = (
                item.get("title")
                or item.get("name")
                or item.get("offer_title")
                or ""
            )
            server_name, region, version, faction = parse_g2g_title(raw_title)

            if region and version:
                display_server = f"({region}) {version}"
            else:
                display_server = server_name
                if raw_title:
                    logger.warning(
                        "G2G: не удалось нормализовать заголовок %r, "
                        "display_server=%r",
                        raw_title, display_server,
                    )

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

            raw_id    = item.get("offer_id") or item.get("id") or ""
            offer_id  = f"g2g_{raw_id}" if raw_id else f"g2g_{uuid.uuid4().hex[:12]}"
            offer_url = f"https://www.g2g.com/offer/{raw_id}" if raw_id else None
            seller    = str(
                item.get("username") or item.get("seller") or "unknown"
            ).strip()

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
                server=display_server,
                display_server=display_server,
                server_name=server_name,
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


# ── Получение dataset_id из HTML страниц категории ────────────────────────────

async def fetch_g2g_datasets(client: httpx.AsyncClient) -> list[str]:
    """Scrape страниц 1–_SCRAPE_PAGES категории и собирает уникальные dataset_id.

    Ищет: fa=lgc_27816_dropdown_18:<slug>
    Возвращает список полных offer_attributes-значений:
      ["lgc_27816_dropdown_18:spineshatter_alliance", ...]
    """
    seen: dict[str, None] = {}  # сохраняем порядок первого появления

    for page in range(1, _SCRAPE_PAGES + 1):
        url = f"{_CATEGORY_URL}?page={page}"
        try:
            resp = await _http_get_with_retry(
                client, url, headers=_HTML_HEADERS,
            )
            html = resp.text
        except Exception as exc:
            logger.warning("G2G: страница категории %d недоступна: %s", page, exc)
            continue

        slugs = _DATASET_RE.findall(html)
        new_on_page = 0
        for slug in slugs:
            attr = f"{_ATTR_PREFIX}:{slug}"
            if attr not in seen:
                seen[attr] = None
                new_on_page += 1

        logger.debug(
            "G2G: категория стр.%d → найдено %d новых dataset_id (всего %d)",
            page, new_on_page, len(seen),
        )

        await asyncio.sleep(random.uniform(1.0, 2.0))

    dataset_ids = list(seen.keys())
    logger.info("G2G: уникальных dataset_id: %d → %s", len(dataset_ids), dataset_ids)
    return dataset_ids


# ── Пагинация для одного dataset_id ──────────────────────────────────────────

def _build_search_params(page: int, dataset_id: str) -> dict:
    """Строит params для GET /offer/search."""
    return {
        "seo_term":         _SEO_TERM,
        "country":          "UA",
        "currency":         "USD",
        "page_size":        _PAGE_SIZE,
        "page":             page,
        "sort":             "recommended_v2",
        "v":                "v2",
        "offer_attributes": dataset_id,
    }


async def _fetch_pages_for_dataset(
    client: httpx.AsyncClient,
    dataset_id: str,
) -> list[dict]:
    """Собирает все страницы /offer/search для одного dataset_id."""
    all_items: list[dict] = []

    for page in range(1, _MAX_PAGES + 1):
        params = _build_search_params(page, dataset_id)
        try:
            resp = await _http_get_with_retry(
                client, _SEARCH_API, params=params, headers=_API_HEADERS,
            )
        except Exception as exc:
            logger.warning(
                "G2G: страница %d (dataset=%r) недоступна: %s",
                page, dataset_id, exc,
            )
            break

        try:
            payload = resp.json()
        except Exception as exc:
            logger.warning(
                "G2G: невалидный JSON на стр.%d (dataset=%r): %s",
                page, dataset_id, exc,
            )
            break

        page_items = payload.get("data") or []
        if not isinstance(page_items, list) or not page_items:
            logger.debug("G2G: стр.%d (dataset=%r) пуста — стоп", page, dataset_id)
            break

        all_items.extend(page_items)
        logger.debug(
            "G2G: стр.%d (dataset=%r) → %d офферов (всего %d)",
            page, dataset_id, len(page_items), len(all_items),
        )

        if len(page_items) < _PAGE_SIZE:
            break

    return all_items


async def _fetch_one_dataset(
    client: httpx.AsyncClient,
    dataset_id: str,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    """Обходит страницы для одного dataset_id под семафором с задержкой."""
    async with semaphore:
        items = await _fetch_pages_for_dataset(client, dataset_id)
        await asyncio.sleep(random.uniform(1.0, 2.0))
        return items


# ── Сбор всех офферов ─────────────────────────────────────────────────────────

async def _fetch_all_datasets(client: httpx.AsyncClient) -> list[dict]:
    """Scrape dataset_id из HTML → параллельный обход /offer/search.

    Возвращает объединённый сырой список item-словарей.
    """
    dataset_ids = await fetch_g2g_datasets(client)

    if not dataset_ids:
        logger.warning("G2G: dataset_id не найдены — офферы не получены")
        return []

    semaphore = asyncio.Semaphore(_CONCURRENCY)
    tasks = [
        _fetch_one_dataset(client, did, semaphore)
        for did in dataset_ids
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items: list[dict] = []
    for did, result in zip(dataset_ids, results):
        if isinstance(result, Exception):
            logger.warning("G2G: dataset_id=%r ошибка: %s", did, result)
        elif result:
            logger.debug("G2G: dataset_id=%r → %d офферов", did, len(result))
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
      1. Scrape HTML страниц категории → уникальные dataset_id
         (regex: fa=lgc_27816_dropdown_18:[a-z0-9_]+).
      2. Для каждого dataset_id — GET /offer/search?offer_attributes=<id>
         (параллельно, semaphore=2, delay 1–2s).
      3. Все результаты объединяются и дедуплицируются.
    """
    fetched_at = datetime.now(timezone.utc)

    async with httpx.AsyncClient(
        timeout=_TIMEOUT,
        follow_redirects=True,
    ) as client:
        all_items = await _fetch_all_datasets(client)

    if not all_items:
        logger.warning("G2G: API вернул 0 офферов")
        return []

    offers = parse_g2g({"data": all_items}, fetched_at)
    result = _dedupe(offers)

    dupes = len(offers) - len(result)
    logger.info(
        "G2G: всего офферов: %d (raw=%d, дубли=%d)",
        len(result), len(offers), dupes,
    )
    return result


# ── Entry point ───────────────────────────────────────────────────────────────

async def fetch_offers() -> list[Offer]:
    """Публичная точка входа — вызывается из offers_service."""
    try:
        return await fetch_g2g_all()
    except Exception:
        logger.exception("G2G: критическая ошибка в fetch_g2g_all — возвращаем []")
        return []
