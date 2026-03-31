from __future__ import annotations

"""
G2G парсер офферов WoW Classic Era gold.

Этапы:
  1. Сбор серверов: GET /categories/wow-classic-era-vanilla-gold?page=1..5
     HTML → BeautifulSoup → [(title, region_id), ...]
  2. Fetch офферов: GET sls.g2g.com/offer/search?region_id=...
     JSON → list[Offer]

Антибот:
  - asyncio.Semaphore(2) — не более 2 параллельных запросов к API
  - random delay 1–3 сек между запросами
  - retry × 3 с exponential backoff при ошибке
"""

import asyncio
import logging
import random
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from api.schemas import Offer

logger = logging.getLogger(__name__)

SOURCE = "g2g"
fallback_count = 0

# ── Regex для парсинга заголовка G2G ──────────────────────────────────────────
# "Spineshatter [EU - Anniversary] - Alliance"
# "Nightslayer [US - Anniversary #2] - Horde"
# "Bloodsail Buccaneers [US - Classic Era] - Alliance"
_TITLE_RE = re.compile(
    r"^(?P<server>.+?)\s*"          # server_name (жадный до первой '[')
    r"\[(?P<region>[A-Za-z]{2,})"  # [EU / US / KR / TW ...
    r"\s*-\s*"
    r"(?P<version>[^\]]+?)\s*\]"   # версия до ']', обрезаем пробелы
    r"\s*-\s*"
    r"(?P<faction>Alliance|Horde)",  # фракция (case-insensitive ниже)
    re.IGNORECASE,
)

# ── Константы ─────────────────────────────────────────────────────────────────
_CATEGORY_URL = "https://www.g2g.com/categories/wow-classic-era-vanilla-gold"
_SEARCH_API   = "https://sls.g2g.com/offer/search"
_SEO_TERM     = "wow-classic-era-vanilla-gold"
_PAGES        = range(1, 6)     # страницы 1..5
_CONCURRENCY  = 2
_PAGE_SIZE    = 50
_RETRY_COUNT  = 3
_TIMEOUT      = 15.0

_HTML_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.g2g.com/",
    "Origin":  "https://www.g2g.com",
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


def _extract_region_id(href: str) -> str | None:
    """Извлекает region_id из URL типа:
    /categories/wow-classic-era-vanilla-gold?region_id=us_wow_classic_era_nightslayer_horde
    """
    try:
        qs = parse_qs(urlparse(href).query)
        ids = qs.get("region_id", [])
        return ids[0] if ids else None
    except Exception:
        return None


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
        global fallback_count
        fallback_count += 1
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


# ── Этап 1: сбор серверов ─────────────────────────────────────────────────────

async def _fetch_servers_page(
    client: httpx.AsyncClient,
    page: int,
) -> list[dict]:
    """Парсит одну страницу категории, возвращает список {title, region_id}."""
    try:
        resp = await _http_get_with_retry(
            client,
            _CATEGORY_URL,
            params={"page": page},
            headers=_HTML_HEADERS,
        )
    except Exception as exc:
        logger.warning("G2G: не удалось загрузить страницу %d: %s", page, exc)
        return []

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        logger.warning("G2G: BS4 упал на странице %d: %s", page, exc)
        return []

    servers: list[dict] = []

    # Ищем все <a href="...?region_id=..."> — ссылки на подкатегории серверов
    for tag in soup.find_all("a", href=True):
        href: str = tag["href"]
        if "region_id=" not in href:
            continue

        region_id = _extract_region_id(href)
        if not region_id:
            continue

        # Название сервера берём из текста тега; fallback — из самого region_id
        title = tag.get_text(separator=" ", strip=True)
        if not title:
            title = region_id.replace("_", " ").title()

        servers.append({"title": title, "region_id": region_id})

    logger.debug("G2G: страница %d → %d серверов", page, len(servers))
    return servers


async def fetch_g2g_servers() -> list[dict]:
    """Собирает все серверы со страниц 1..5 категории G2G.

    Возвращает дедуплицированный список [{title, region_id}, ...].
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        tasks = [_fetch_servers_page(client, p) for p in _PAGES]
        pages = await asyncio.gather(*tasks, return_exceptions=True)

    seen: set[str] = set()
    result: list[dict] = []

    for page_result in pages:
        if isinstance(page_result, Exception):
            logger.warning("G2G: ошибка при сборе серверов: %s", page_result)
            continue
        for srv in page_result:
            rid = srv["region_id"]
            if rid not in seen:
                seen.add(rid)
                result.append(srv)

    logger.info("G2G: собрано %d уникальных серверов", len(result))
    return result


# ── Этап 2: fetch офферов ─────────────────────────────────────────────────────

async def fetch_g2g_offers(
    client: httpx.AsyncClient,
    region_id: str,
) -> dict:
    """GET sls.g2g.com/offer/search для одного region_id (с пагинацией)."""
    try:
        all_items: list[dict] = []
        page = 1
        while True:
            params = {
                "seo_term":  _SEO_TERM,
                "region_id": region_id,
                "currency":  "USD",
                "page_size": _PAGE_SIZE,
                "page":      page,
                "v":         "v2",
            }
            resp = await _http_get_with_retry(
                client,
                _SEARCH_API,
                params=params,
                headers=_API_HEADERS,
            )
            payload = resp.json()
            page_items = payload.get("data") or []
            if not isinstance(page_items, list) or not page_items:
                break
            all_items.extend(page_items)
            if len(page_items) < _PAGE_SIZE:
                break
            page += 1
        return {"data": all_items}
    except Exception as exc:
        logger.warning("G2G: не удалось получить офферы для %r: %s", region_id, exc)
        return {}


# ── Этап 3: парсинг JSON → Offer ──────────────────────────────────────────────

def parse_g2g(data: dict, title: str, fetched_at: datetime) -> list[Offer]:
    """Преобразует JSON-ответ /offer/search в list[Offer].

    title — RAW строка из G2G (например "Spineshatter [EU - Anniversary] - Alliance").
    Нормализуется через parse_g2g_title():
      display_server = "(EU) Anniversary"  — совпадает с форматом FunPay
      server_name    = "Spineshatter"      — сервер внутри группы
      faction        = "Alliance"
    """
    items = data.get("data") or data.get("results") or []
    if not isinstance(items, list):
        logger.debug("G2G: parse_g2g для %r — пустые данные", title)
        return []

    # ── Парсим заголовок один раз для всей пачки офферов этого сервера ─────
    server_name, region, version, faction = parse_g2g_title(title)

    if region and version:
        display_server = f"({region}) {version}"  # "(EU) Anniversary"
    else:
        # Fallback: нет структурированного формата — используем title как есть
        display_server = server_name
        logger.warning(
            "G2G: не удалось нормализовать заголовок %r, "
            "display_server=%r (фильтрация по серверу может не работать)",
            title, display_server,
        )

    offers: list[Offer] = []
    skipped = 0

    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            unit_price = item.get("unit_price") or item.get("unitPrice")
            amount     = item.get("available_qty") or item.get("quantity") or item.get("amount")

            if unit_price is None or amount is None:
                skipped += 1
                continue

            unit_price = float(unit_price)
            amount     = int(float(amount))

            if unit_price <= 0 or amount <= 0:
                skipped += 1
                continue

            price_per_1k = round(unit_price * 1000.0, 4)

            offer_id = item.get("offer_id") or item.get("id")
            offer_id = f"g2g_{offer_id}" if offer_id else f"g2g_{uuid.uuid4().hex[:12]}"

            seller = str(item.get("username") or item.get("seller") or "unknown").strip()

            # offer_url: строим из offer_id если доступен
            raw_id = item.get("offer_id") or item.get("id") or ""
            offer_url = f"https://www.g2g.com/offer/{raw_id}" if raw_id else None

            # updated_at: берём из поля или fallback на fetched_at
            raw_ts = item.get("updated_at") or item.get("updatedAt") or item.get("updated")
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
                server=display_server,      # model_validator → lowercase slug
                display_server=display_server,  # "(EU) Anniversary"
                server_name=server_name,    # "Spineshatter"
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
        logger.debug("G2G: %r — пропущено %d/%d офферов", title, skipped, len(items))

    return offers


# ── Этап 4+5: основная функция со Semaphore и jitter ─────────────────────────

async def fetch_g2g_all() -> list[Offer]:
    """Собирает все офферы G2G по всем серверам.

    - Concurrency = 2 (Semaphore)
    - Jitter delay 1–3 сек между запросами
    - Retry по 3 раза на каждый сервер
    - Ошибки не прерывают общий сбор
    """
    global fallback_count
    fallback_count = 0
    servers = await fetch_g2g_servers()
    if not servers:
        logger.warning("G2G: список серверов пуст — офферы не будут собраны")
        return []
    if len(servers) > 100:
        logger.warning("G2G: too many servers, limiting to 100")
        servers = servers[:100]

    sem      = asyncio.Semaphore(_CONCURRENCY)
    fetched_at = datetime.now(timezone.utc)
    all_offers: list[Offer] = []
    total_servers = len(servers)
    success = 0
    failed = 0

    async with httpx.AsyncClient(
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers=_API_HEADERS,
    ) as client:

        async def _fetch_one(srv: dict) -> list[Offer]:
            nonlocal success, failed
            async with sem:
                # Jitter: случайная пауза перед каждым запросом
                await asyncio.sleep(random.uniform(1.0, 3.0))
                try:
                    data = await fetch_g2g_offers(client, srv["region_id"])
                    if not data:
                        failed += 1
                        return []
                    offers = parse_g2g(data, srv["title"], fetched_at)
                    success += 1
                    return offers
                except Exception:
                    failed += 1
                    raise

        tasks = [_fetch_one(srv) for srv in servers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    seen: set[str] = set()
    for res in results:
        if isinstance(res, Exception):
            logger.warning("G2G: ошибка при сборе офферов: %s", res)
            continue
        for offer in res:
            if offer.id not in seen:
                seen.add(offer.id)
                all_offers.append(offer)

    logger.info(
        "G2G: собрано %d офферов с %d серверов",
        len(all_offers), len(servers),
    )
    if fallback_count:
        logger.warning("G2G fallback parse count: %d", fallback_count)
    logger.info("G2G: servers processed %d/%d, failed=%d", success, total_servers, failed)
    return all_offers


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
