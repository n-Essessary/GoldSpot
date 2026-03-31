from __future__ import annotations

"""
G2G парсер офферов WoW Classic Era gold.

Pipeline:
  1. fetch_datasets()
       GET /offer/keyword_relation/collection
       → список всех filter-датасетов для категории.

  2. find_server_dataset()
       → находим датасет с label "server"/"region"/"realm".
       → извлекаем опции: [{server_id, server_name}, ...].

  3. fetch_offers_for_server()
       GET /offer/search  с offer_attributes[dataset_id]=server_id
       → офферы для каждого сервера.

  4. Дедупликация по offer_id, префикс g2g_.

Антибот:
  - retry × 3, exponential backoff (1→2→4s, 429 → ×3)
  - semaphore = 2 параллельных воркера
  - delay 0.2–0.5 сек между страницами внутри воркера
  - timeout 15 сек

Совместимость:
  - fetch_offers() → list[Offer]  (точка входа из offers_service)
  - Offer.display_server = "(EU) Anniversary"
  - Offer.server_name   = "Spineshatter"
  - Offer.faction       = "Alliance" / "Horde"
"""

import asyncio
import logging
import random
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from api.schemas import Offer

logger = logging.getLogger(__name__)

SOURCE = "g2g"

# ── Regex: парсинг title офферa ───────────────────────────────────────────────
# "Spineshatter [EU - Anniversary] - Alliance"
# "Nightslayer [US - Anniversary #2] - Horde"
# "Bloodsail Buccaneers [US - Classic Era] - Alliance"
_TITLE_RE = re.compile(
    r"^(?P<server>.+?)\s*"
    r"\[(?P<region>[A-Za-z]{2,})"
    r"\s*-\s*"
    r"(?P<version>[^\]]+?)\s*\]"
    r"\s*-\s*"
    r"(?P<faction>Alliance|Horde)",
    re.IGNORECASE,
)

# ── Ключевые слова для поиска «серверного» датасета ──────────────────────────
_SERVER_LABELS: frozenset[str] = frozenset({"server", "region", "realm"})

# ── API эндпоинты ─────────────────────────────────────────────────────────────
_COLLECTION_API = "https://sls.g2g.com/offer/keyword_relation/collection"
_SEARCH_API     = "https://sls.g2g.com/offer/search"

# ── Параметры категории ───────────────────────────────────────────────────────
_FA          = "lgc_1_27816"          # brand filter: WoW Classic Era gold
_CURRENCY    = "USD"
_COUNTRY     = "UA"
_PAGE_SIZE   = 100
_MAX_PAGES   = 10
_RETRY_COUNT = 3
_TIMEOUT     = 15.0
_CONCURRENCY = 2
_SORTS       = ["lowest_price", "recommended_v2", "newest"]

_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":   "application/json, text/plain, */*",
    "Referer":  "https://www.g2g.com/",
    "Origin":   "https://www.g2g.com",
    "Accept-Language": "en-US,en;q=0.9",
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
    """Парсит RAW title оффера → (server_name, region, version, faction).

    "Spineshatter [EU - Anniversary] - Alliance"
      → ("Spineshatter", "EU", "Anniversary", "Alliance")
    """
    m = _TITLE_RE.match(title.strip())
    if not m:
        logger.debug("G2G title не распознан: %r", title)
        return title.strip(), "", "", _extract_faction(title)
    return (
        m.group("server").strip(),
        m.group("region").strip().upper(),
        m.group("version").strip(),
        m.group("faction").strip().capitalize(),
    )


def _deep_get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Безопасный доступ к вложенным dict-ключам."""
    for k in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(k, default)
    return obj


# ── HTTP: retry + backoff ─────────────────────────────────────────────────────

async def _get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    retries: int = _RETRY_COUNT,
) -> httpx.Response:
    """GET с retry × retries и exponential backoff."""
    last_exc: Exception = RuntimeError("no attempts")
    for attempt in range(retries):
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429 or status >= 500:
                wait = (2 ** attempt) * (3 if status == 429 else 1)
                logger.warning(
                    "G2G HTTP %d → %s, попытка %d/%d, ждём %.0fs",
                    status, url, attempt + 1, retries, wait,
                )
                last_exc = exc
                await asyncio.sleep(wait)
                continue
            logger.warning("G2G HTTP %d → %s — fail-fast", status, url)
            raise
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            wait = 2 ** attempt
            logger.warning(
                "G2G %s → %s, попытка %d/%d, ждём %.0fs",
                type(exc).__name__, url, attempt + 1, retries, wait,
            )
            last_exc = exc
            await asyncio.sleep(wait)
    raise last_exc


# ── Step 1: получение датасетов ───────────────────────────────────────────────

async def fetch_datasets(client: httpx.AsyncClient) -> list[dict]:
    """GET /offer/keyword_relation/collection → сырой список датасетов.

    Обрабатывает несколько возможных структур JSON-ответа G2G.
    """
    params: dict = {
        "fa":                    _FA,
        "include_searchable_only": 0,
        "include_showcase":      1,
    }
    try:
        resp = await _get(client, _COLLECTION_API, params=params)
        payload = resp.json()
    except Exception as exc:
        logger.error("G2G: fetch_datasets ошибка: %s", exc)
        return []

    # G2G может вернуть данные в разных местах — пробуем все варианты
    raw: list = (
        _deep_get(payload, "payload", "datasets")
        or _deep_get(payload, "result", "data")
        or _deep_get(payload, "data")
        or _deep_get(payload, "datasets")
        or []
    )

    if not isinstance(raw, list):
        logger.warning("G2G: datasets: неожиданный тип %s", type(raw))
        return []

    logger.info("G2G: datasets=%d", len(raw))
    return raw


# ── Step 2: поиск серверного датасета + извлечение опций ─────────────────────

def find_server_dataset(datasets: list[dict]) -> dict | None:
    """Находит датасет, описывающий серверы (label содержит server/region/realm)."""
    # Прямое совпадение по label
    for ds in datasets:
        label = str(ds.get("label") or ds.get("name") or ds.get("display") or "").lower()
        if any(kw in label for kw in _SERVER_LABELS):
            return ds

    # Fallback: первый датасет с непустым списком опций
    for ds in datasets:
        opts = ds.get("options") or ds.get("values") or ds.get("children") or []
        if isinstance(opts, list) and opts:
            logger.debug("G2G: серверный датасет не найден по label, берём первый с options")
            return ds

    return None


def _parse_server_options(dataset: dict) -> list[dict[str, str]]:
    """Извлекает [{server_id, server_name}, ...] из датасета."""
    opts = (
        dataset.get("options")
        or dataset.get("values")
        or dataset.get("children")
        or []
    )
    dataset_id = str(
        dataset.get("id")
        or dataset.get("dataset_id")
        or dataset.get("key")
        or ""
    ).strip()

    servers: list[dict[str, str]] = []
    for opt in opts:
        if not isinstance(opt, dict):
            continue
        server_id = str(
            opt.get("id")
            or opt.get("value")
            or opt.get("dataset_id")
            or ""
        ).strip()
        server_name = str(
            opt.get("display")
            or opt.get("label")
            or opt.get("name")
            or opt.get("value")
            or ""
        ).strip()
        if server_id:
            servers.append({
                "dataset_id":  dataset_id,
                "server_id":   server_id,
                "server_name": server_name,
            })

    logger.info("G2G: servers=%d (dataset_id=%r)", len(servers), dataset_id)
    return servers


# ── Парсинг одного item → Offer ───────────────────────────────────────────────

def _parse_item(item: dict, fetched_at: datetime) -> Offer | None:
    """Преобразует сырой dict оффера → Offer. Возвращает None если данных не хватает."""
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
            display_server = server_name or "Unknown"
            if raw_title:
                logger.debug("G2G: title не нормализован: %r", raw_title)

        unit_price = item.get("unit_price") or item.get("unitPrice")
        amount = (
            item.get("available_qty")
            or item.get("quantity")
            or item.get("amount")
        )

        if unit_price is None or amount is None:
            return None

        unit_price = float(unit_price)
        amount = int(float(amount))

        if unit_price <= 0 or amount <= 0:
            return None

        price_per_1k = round(unit_price * 1000.0, 4)

        raw_id   = item.get("offer_id") or item.get("id") or ""
        offer_id = f"g2g_{raw_id}" if raw_id else f"g2g_{uuid.uuid4().hex[:12]}"
        offer_url = f"https://www.g2g.com/offer/{raw_id}" if raw_id else None
        seller = str(
            item.get("username") or item.get("seller") or "unknown"
        ).strip() or "unknown"

        raw_ts = (
            item.get("updated_at")
            or item.get("updatedAt")
            or item.get("updated")
        )
        try:
            updated_at = (
                datetime.fromisoformat(str(raw_ts)).astimezone(timezone.utc)
                if raw_ts else fetched_at
            )
        except (ValueError, TypeError):
            updated_at = fetched_at

        return Offer(
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
        )

    except (ValueError, TypeError, KeyError) as exc:
        logger.debug("G2G: пропуск оффера %r — %s", item.get("offer_id"), exc)
        return None


def _extract_items(payload: dict) -> list[dict]:
    """Извлекает список офферов из JSON-ответа /offer/search."""
    candidates = [
        _deep_get(payload, "payload", "offers"),
        _deep_get(payload, "payload", "data"),
        _deep_get(payload, "result", "offers"),
        _deep_get(payload, "result", "data"),
        payload.get("data"),
        payload.get("offers"),
        payload.get("results"),
    ]
    for c in candidates:
        if isinstance(c, list) and c:
            return c
    return []


# ── Step 3: получение офферов для одного сервера ─────────────────────────────

async def _fetch_pages(
    client: httpx.AsyncClient,
    dataset_id: str,
    server_id: str,
    sort: str,
) -> list[dict]:
    """Обходит страницы /offer/search для одного сервера с заданным sort."""
    all_items: list[dict] = []
    label = f"server_id={server_id!r} sort={sort!r}"

    for page in range(1, _MAX_PAGES + 1):
        # offer_attributes[dataset_id]=server_id — стандартный G2G query param
        params: dict = {
            "fa":                             _FA,
            f"offer_attributes[{dataset_id}]": server_id,
            "sort":                           sort,
            "currency":                       _CURRENCY,
            "country":                        _COUNTRY,
            "page_size":                      _PAGE_SIZE,
            "page":                           page,
        }
        try:
            resp = await _get(client, _SEARCH_API, params=params)
            payload = resp.json()
        except Exception as exc:
            logger.warning("G2G: стр.%d (%s) ошибка: %s", page, label, exc)
            break

        items = _extract_items(payload)
        if not items:
            logger.debug("G2G: стр.%d (%s) пуста — стоп", page, label)
            break

        all_items.extend(items)
        logger.debug(
            "G2G: стр.%d (%s) → %d офф. (всего %d)",
            page, label, len(items), len(all_items),
        )

        if len(items) < _PAGE_SIZE:
            break

        await asyncio.sleep(random.uniform(0.2, 0.5))

    return all_items


async def fetch_offers_for_server(
    client: httpx.AsyncClient,
    dataset_id: str,
    server_id: str,
    server_display_name: str,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    """Получает все офферы для одного сервера под семафором.

    Пробует sort-стратегии по порядку (_SORTS), останавливается на первом
    непустом результате.
    """
    async with semaphore:
        for sort in _SORTS:
            items = await _fetch_pages(client, dataset_id, server_id, sort)
            if items:
                logger.debug(
                    "G2G: %r → %d офферов (sort=%s)",
                    server_display_name, len(items), sort,
                )
                await asyncio.sleep(random.uniform(0.2, 0.5))
                return items
            await asyncio.sleep(random.uniform(0.2, 0.5))

        logger.debug("G2G: %r → 0 офферов (все sorts)", server_display_name)
        return []


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
    """Собирает все офферы G2G через dataset_id-стратегию.

    Алгоритм:
      1. GET /offer/keyword_relation/collection → datasets.
      2. find_server_dataset()  → dataset_id + list[server_id].
      3. Параллельный обход (semaphore=2): /offer/search?offer_attributes[...]=...
      4. parse → dedupe → return.
    """
    fetched_at = datetime.now(timezone.utc)

    async with httpx.AsyncClient(
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers=_HEADERS,
    ) as client:

        # ── Шаг 1: получаем датасеты ─────────────────────────────────────────
        datasets = await fetch_datasets(client)
        if not datasets:
            logger.warning("G2G: датасеты не получены — выходим")
            return []

        # ── Шаг 2: ищем серверный датасет ────────────────────────────────────
        server_ds = find_server_dataset(datasets)
        if server_ds is None:
            logger.warning("G2G: серверный датасет не найден среди %d датасетов", len(datasets))
            return []

        servers = _parse_server_options(server_ds)
        if not servers:
            logger.warning("G2G: список серверов пуст")
            return []

        # ── Шаг 3: параллельный сбор офферов ─────────────────────────────────
        semaphore = asyncio.Semaphore(_CONCURRENCY)
        tasks = [
            fetch_offers_for_server(
                client,
                srv["dataset_id"],
                srv["server_id"],
                srv["server_name"],
                semaphore,
            )
            for srv in servers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # ── Шаг 4: парсинг + dedupe ───────────────────────────────────────────────
    raw_items: list[dict] = []
    for srv, result in zip(servers, results):
        if isinstance(result, Exception):
            logger.warning(
                "G2G: server_id=%r ошибка: %s", srv["server_id"], result,
            )
        elif isinstance(result, list):
            raw_items.extend(result)

    if not raw_items:
        logger.warning("G2G: total offers=0")
        return []

    offers: list[Offer] = []
    for item in raw_items:
        o = _parse_item(item, fetched_at)
        if o is not None:
            offers.append(o)

    result_offers = _dedupe(offers)
    dupes = len(offers) - len(result_offers)

    logger.info(
        "G2G: total offers=%d (raw=%d, дубли=%d)",
        len(result_offers), len(offers), dupes,
    )
    return result_offers


# ── Entry point ───────────────────────────────────────────────────────────────

async def fetch_offers() -> list[Offer]:
    """Публичная точка входа — вызывается из offers_service.SOURCES.

    При любой ошибке возвращает [] — не ломает общий refresh.
    """
    try:
        return await fetch_g2g_all()
    except Exception:
        logger.exception("G2G: критическая ошибка — возвращаем []")
        return []
