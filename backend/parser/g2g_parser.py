from __future__ import annotations

"""
G2G парсер офферов WoW Classic Era gold.

Стратегия (dataset_id):
  1. GET /offer/keyword_relation/collection
       → получаем список серверов: [{dataset_id, value}, ...]
  2. Для каждого dataset_id — GET /offer/search
       params: offer_attributes=dataset_id (+ seo_term, currency, etc.)
       Пагинация до _MAX_PAGES или до пустой страницы.
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

# ── Константы ─────────────────────────────────────────────────────────────────
_SEARCH_API     = "https://sls.g2g.com/offer/search"
_COLLECTION_API = "https://sls.g2g.com/offer/keyword_relation/collection"

_SEO_TERM    = "wow-classic-era-vanilla-gold"
_BRAND_ID    = "lgc_game_27816"
_SERVICE_ID  = "lgc_service_1"

_PAGE_SIZE   = 100
_MAX_PAGES   = 10
_RETRY_COUNT = 3
_TIMEOUT     = 15.0
_CONCURRENCY = 2    # параллельных воркеров при обходе dataset_id

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
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
    region      = m.group("region").strip().upper()
    version     = m.group("version").strip()
    faction     = m.group("faction").strip().capitalize()

    return server_name, region, version, faction


async def _http_get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    retries: int = _RETRY_COUNT,
) -> httpx.Response:
    """GET с retry × retries и exponential backoff (1s → 2s → 4s)."""
    last_exc: Exception = RuntimeError("no attempts")
    for attempt in range(retries):
        try:
            resp = await client.get(url, params=params)
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


# ── Получение списка серверов (dataset_id) ────────────────────────────────────

async def fetch_g2g_datasets(
    client: httpx.AsyncClient,
) -> list[dict[str, str]]:
    """Запрашивает /offer/keyword_relation/collection и возвращает список серверов.

    Каждый элемент: {"dataset_id": "...", "value": "название сервера"}.

    API требует brand_id + service_id; region_id опционален — пробуем без него.
    Если ответ пуст — значит, категория не содержит серверов.
    """
    params: dict = {
        "brand_id":              _BRAND_ID,
        "service_id":            _SERVICE_ID,
        "include_searchable_only": 0,
    }

    try:
        resp = await _http_get_with_retry(client, _COLLECTION_API, params=params)
        payload = resp.json()
    except Exception as exc:
        logger.error("G2G: не удалось получить список серверов (datasets): %s", exc)
        return []

    # Ответ: {"result": {"data": [{"dataset_id": "...", "value": "..."}, ...]}}
    # или просто {"data": [...]}  — обрабатываем оба варианта.
    raw: list = (
        (payload.get("result") or {}).get("data")
        or payload.get("data")
        or []
    )

    datasets: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        dataset_id = str(entry.get("dataset_id") or entry.get("id") or "").strip()
        value      = str(entry.get("value") or entry.get("name") or "").strip()
        if dataset_id:
            datasets.append({"dataset_id": dataset_id, "value": value})

    logger.info("G2G: получено серверов (datasets): %d", len(datasets))
    return datasets


# ── Пагинация для одного dataset_id ──────────────────────────────────────────

def _build_search_params(page: int, dataset_id: str) -> dict:
    """Строит params для GET /offer/search с offer_attributes=dataset_id."""
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
    server_value: str,
) -> list[dict]:
    """Собирает все страницы /offer/search для одного dataset_id.

    Возвращает сырой список item-словарей.
    """
    all_items: list[dict] = []
    label = f"dataset_id={dataset_id!r} ({server_value!r})"

    for page in range(1, _MAX_PAGES + 1):
        params = _build_search_params(page, dataset_id)
        try:
            resp = await _http_get_with_retry(client, _SEARCH_API, params=params)
        except Exception as exc:
            logger.warning("G2G: страница %d (%s) недоступна: %s", page, label, exc)
            break

        try:
            payload = resp.json()
        except Exception as exc:
            logger.warning(
                "G2G: невалидный JSON на странице %d (%s): %s", page, label, exc,
            )
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
            break

    return all_items


async def _fetch_one_dataset(
    client: httpx.AsyncClient,
    dataset_id: str,
    server_value: str,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    """Обходит страницы для одного dataset_id под семафором с задержкой."""
    async with semaphore:
        items = await _fetch_pages_for_dataset(client, dataset_id, server_value)
        await asyncio.sleep(random.uniform(1.0, 2.0))
        return items


# ── Сбор всех офферов по всем dataset_id ─────────────────────────────────────

async def _fetch_all_datasets(client: httpx.AsyncClient) -> list[dict]:
    """Получает datasets → параллельно обходит каждый (semaphore=_CONCURRENCY).

    Возвращает объединённый сырой список item-словарей.
    """
    datasets = await fetch_g2g_datasets(client)
    if not datasets:
        logger.warning("G2G: список серверов пуст — офферы не будут получены")
        return []

    semaphore = asyncio.Semaphore(_CONCURRENCY)
    tasks = [
        _fetch_one_dataset(
            client,
            ds["dataset_id"],
            ds["value"],
            semaphore,
        )
        for ds in datasets
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items: list[dict] = []
    for ds, result in zip(datasets, results):
        did = ds["dataset_id"]
        if isinstance(result, Exception):
            logger.warning("G2G: dataset_id=%r ошибка: %s", did, result)
        elif result:
            logger.debug(
                "G2G: dataset_id=%r (%r) → %d офферов",
                did, ds["value"], len(result),
            )
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
    """Собирает все офферы G2G через dataset_id стратегию.

    Алгоритм:
      1. GET /offer/keyword_relation/collection → список серверов (dataset_id).
      2. Для каждого dataset_id — GET /offer/search?offer_attributes=<id>
         (параллельно, semaphore=2, delay 1–2s между запросами).
      3. Все результаты объединяются и дедуплицируются.
    """
    fetched_at = datetime.now(timezone.utc)

    async with httpx.AsyncClient(
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers=_HEADERS,
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
