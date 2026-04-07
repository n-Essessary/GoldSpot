"""
g2g_parser.py — Production-ready G2G parser.

Verified pipeline:
  1) /offer/keyword_relation/region  -> region_id + relation_id
  2) /offer/search                   -> offers (username, unit_price_in_usd, qty)
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import httpx

from api.schemas import Offer
from utils.version_utils import _canonicalize_version

logger = logging.getLogger(__name__)

SOURCE = "g2g"
BASE = "https://sls.g2g.com"

_MAX_HTTP_ATTEMPTS = 3


def _parse_retry_after_seconds(value: str | None, default: int = 60) -> int:
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


async def _http_get_retry(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """GET with retries: 429 → Retry-After backoff; 5xx → exponential backoff; 4xx no retry."""
    for attempt in range(_MAX_HTTP_ATTEMPTS):
        try:
            resp = await client.get(url, **kwargs)
            if resp.status_code == 429:
                retry_after = _parse_retry_after_seconds(
                    resp.headers.get("Retry-After"),
                    60,
                )
                if attempt < _MAX_HTTP_ATTEMPTS - 1:
                    logger.warning(
                        "G2G 429 rate limited — backing off %ds",
                        retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    continue
            elif resp.status_code >= 500:
                if attempt < _MAX_HTTP_ATTEMPTS - 1:
                    await asyncio.sleep(2**attempt)
                    continue
            resp.raise_for_status()
            return resp
        except httpx.TimeoutException:
            if attempt < _MAX_HTTP_ATTEMPTS - 1:
                await asyncio.sleep(2**attempt)
                continue
            raise
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500 and attempt < _MAX_HTTP_ATTEMPTS - 1:
                await asyncio.sleep(2**attempt)
                continue
            raise
    raise RuntimeError("_http_get_retry: exhausted without response")


# IMPORTANT: httpx при уровне INFO пишет "HTTP Request: ...", что забивает логи.
# Оставляем логи в основном модуле, а сетевой "access log" глушим.
for _httpx_logger_name in ("httpx", "httpcore"):
    logging.getLogger(_httpx_logger_name).setLevel(logging.WARNING)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.g2g.com/",
    "Origin": "https://www.g2g.com",
}

GAME_CONFIG: dict[str, dict[str, str]] = {
    # Верифицировано: https://www.g2g.com/categories/wow-classic-era-vanilla-gold
    # Покрывает Classic Era, Seasonal, TBC Anniversary — один brand_id
    "wow_classic_era_seasonal_anniversary": {
        "brand_id":   "lgc_game_27816",
        "service_id": "lgc_service_1",
        "label":      "WoW Classic Era / Seasonal / TBC Anniversary",
    },
}

# Категория G2G для построения ссылок
_CATEGORY_SLUG = "wow-classic-era-vanilla-gold"


def _build_offer_url(
    offer_id: str,
    offer_attributes: list[dict],
    region_id: str,
    game_slug: str = _CATEGORY_SLUG,
) -> str:
    """
    Строит рабочую ссылку на страницу группы офферов G2G для конкретного сервера+фракции.

    Приоритет: fa={col_id}:{dat_id} из offer_attributes[0] → прямая ссылка на
    конкретный сервер. Двоеточие URL-энкодируется как %3A (требование G2G).
    Fallback на /offer/{offer_id} только если offer_attributes пустой.
    """
    if offer_attributes:
        col_id = offer_attributes[0].get("collection_id", "")
        dat_id = offer_attributes[0].get("dataset_id", "")
        if col_id and dat_id:
            fa = quote(f"{col_id}:{dat_id}", safe="")
            return (
                f"https://www.g2g.com/categories/{game_slug}/offer/group"
                f"?fa={fa}&region_id={region_id}"
            )
    if offer_id:
        return f"https://www.g2g.com/offer/{offer_id}"
    return ""


# Строгий regex: "Server [Region - Version] - Faction"
_TITLE_RE = re.compile(
    r"^(?P<server>.+?)\s*"
    r"\[(?P<region>[A-Za-z]{2,})\s*-\s*(?P<version>[^\]]+?)\]\s*"
    r"(?:-\s*(?P<faction>Alliance|Horde))?$",
    re.IGNORECASE,
)

# Вспомогательные regex для гибкого fallback-парсинга
_REGION_RE         = re.compile(r"\b(EU|US|NA|OCE|KR|TW|SEA|RU)\b", re.IGNORECASE)
_BRACKET_REGION_RE = re.compile(r"\[([A-Za-z]{2,})\]")   # "[EU]" без версии
_FACTION_END_RE    = re.compile(r"-\s*(Alliance|Horde)\s*$", re.IGNORECASE)

# Версии в порядке приоритета (более длинные/специфичные — первыми).
# "Seasonal" на G2G == Season of Discovery (те же серверы: Crusader Strike, Lava Lash…).
_VERSION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Season of Discovery", re.compile(r"season\s+of\s+discovery|\bseasonal\b", re.I)),
    ("Anniversary",         re.compile(r"anniversary",                           re.I)),
    ("Classic Era",         re.compile(r"classic\s+era",                         re.I)),
    ("Classic",             re.compile(r"\bclassic\b",                           re.I)),
]

@dataclass
class G2GOffer:
    offer_id: str
    title: str
    server_name: str
    region_id: str
    relation_id: str
    price_usd: float
    min_qty: int
    available_qty: int
    seller: str
    brand_id: str
    service_id: str
    offer_url: str = ""          # прямая ссылка /offer/{offer_id} для индивидуальных офферов
    offer_group: str = ""        # "/{dataset_id}" — идентификатор группы сервер+фракция
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class G2GRegion:
    region_id: str
    relation_id: str


def _parse_title(title: str) -> tuple[str, str, str, str]:
    """Парсит RAW title G2G оффера → (server_name, region, version, faction).

    Двухуровневый парсинг:

    Уровень 1 — строгий regex (покрывает большинство тайтлов):
        "Spineshatter [EU - Anniversary] - Alliance"
          → ("Spineshatter", "EU", "Anniversary", "Alliance")
        "Lava Lash [EU - Seasonal] - Horde"
          → ("Lava Lash", "EU", "Season of Discovery", "Horde")

    Уровень 2 — гибкий fallback (нестандартные форматы):
        "Firemaw [EU] - Alliance"     → ("Firemaw",  "EU", "Classic",            "Alliance")
        "Classic Era Gold EU"         → ("",          "EU", "Classic Era",         "Horde")
        "Season of Discovery Gold"    → ("",          "",   "Season of Discovery",  "Horde")
    """
    t = (title or "").strip()
    if not t:
        return "", "", "", "Horde"

    # ── Уровень 1: строгий regex ──────────────────────────────────────────────
    m = _TITLE_RE.match(t)
    if m:
        server_name = (m.group("server") or "").strip()
        region      = (m.group("region") or "").upper().strip()
        version     = (m.group("version") or "").strip()
        faction     = (m.group("faction") or "").strip().capitalize() or (
            "Alliance" if "alliance" in t.lower() else "Horde"
        )
        return server_name, region, version, faction

    # ── Уровень 2: гибкий fallback ────────────────────────────────────────────

    # Faction: ищем " - Alliance" / " - Horde" в конце, иначе по вхождению
    fm = _FACTION_END_RE.search(t)
    if fm:
        faction = fm.group(1).capitalize()
    elif "alliance" in t.lower():
        faction = "Alliance"
    else:
        faction = "Horde"

    # server_name: часть до первой "[", либо до последнего " - faction"
    server_name = ""
    if "[" in t:
        server_name = t[:t.index("[")].strip()
    else:
        parts = re.split(r"\s+-\s+", t)
        if len(parts) >= 2 and parts[-1].strip().lower() in ("alliance", "horde"):
            server_name = parts[0].strip()

    # Region: сначала ищем в скобках "[EU]", затем в любом месте
    region = ""
    _KNOWN = {"EU", "US", "NA", "OCE", "KR", "TW", "SEA", "RU"}
    bm = _BRACKET_REGION_RE.search(t)
    if bm and bm.group(1).upper() in _KNOWN:
        region = bm.group(1).upper()
    else:
        rm = _REGION_RE.search(t)
        if rm:
            region = rm.group(1).upper()

    # Version: по ключевым словам в порядке приоритета
    version = ""
    for ver_name, pattern in _VERSION_PATTERNS:
        if pattern.search(t):
            version = ver_name
            break

    # Если регион есть, но версия не найдена — дефолт "Classic"
    if region and not version:
        version = "Classic"

    # Если server_name так и не определился — используем полный title как fallback
    if not server_name:
        server_name = t

    return server_name, region, version, faction


class G2GClient:
    def __init__(self, country: str = "SG", currency: str = "USD"):
        self.country = country
        self.currency = currency
        self._client: Optional[httpx.AsyncClient] = None
        self._region_cache: dict[str, list[G2GRegion]] = {}

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            headers=HEADERS,
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_):
        if self._client:
            await self._client.aclose()

    async def fetch_regions(self, brand_id: str, service_id: str) -> list[G2GRegion]:
        cache_key = f"{brand_id}:{service_id}"
        if cache_key in self._region_cache:
            return self._region_cache[cache_key]

        resp = await _http_get_retry(
            self._client,
            f"{BASE}/offer/keyword_relation/region",
            params={
                "brand_id": brand_id,
                "service_id": service_id,
                "country": self.country,
            },
        )

        payload = resp.json().get("payload", {})
        regions = [
            G2GRegion(region_id=r["region_id"], relation_id=r["relation_id"])
            for r in payload.get("results", [])
            if r.get("region_id") and r.get("relation_id")
        ]
        self._region_cache[cache_key] = regions
        logger.info(
            "G2G regions: brand_id=%s service_id=%s country=%s -> %d regions",
            brand_id,
            service_id,
            self.country,
            len(regions),
        )
        return regions

    async def fetch_all_sellers(
        self,
        brand_id: str,
        service_id: str,
        regions: list[G2GRegion],
    ) -> list[str]:
        """
        Собирает ВСЕХ продавцов — с пагинацией по всем страницам каждого региона.
        Без include_offline=0 — офлайн-продавцы тоже нужны для полного списка.
        """
        sellers: set[str] = set()

        for region in regions:
            page = 1
            while True:
                try:
                    resp = await _http_get_retry(
                        self._client,
                        f"{BASE}/offer/search",
                        params={
                            "brand_id":   brand_id,
                            "service_id": service_id,
                            "relation_id": region.relation_id,
                            "country":    self.country,
                            "currency":   self.currency,
                            "sort":       "lowest_price",
                            "page":       page,
                            "page_size":  48,
                        },
                    )
                    results = resp.json().get("payload", {}).get("results", [])
                except Exception as e:
                    logger.warning(
                        "G2G discovery region=%s page=%d: %s",
                        region.region_id,
                        page,
                        e,
                    )
                    break

                for o in results:
                    u = (o.get("username") or "").strip()
                    if u:
                        sellers.add(u)

                if len(results) < 48:
                    break
                page += 1
                await asyncio.sleep(0.15)

        logger.info("G2G discovered %d unique sellers", len(sellers))
        return sorted(sellers)

    async def fetch_seller_offers(
        self,
        brand_id: str,
        service_id: str,
        seller: str,
        page_size: int = 48,
    ) -> list[G2GOffer]:
        """
        Возвращает все индивидуальные офферы конкретного продавца.

        ?seller={username} → is_group_display=false, stable offer_id,
        реальный available_qty > 0, URL = /offer/{offer_id}.
        """
        all_offers: list[G2GOffer] = []
        page = 1
        max_pages = 5

        while page <= max_pages:
            try:
                resp = await _http_get_retry(
                    self._client,
                    f"{BASE}/offer/search",
                    params={
                        "brand_id":        brand_id,
                        "service_id":      service_id,
                        "country":         self.country,
                        "currency":        self.currency,
                        "sort":            "lowest_price",
                        "include_offline": "0",
                        "seller":          seller,
                        "page":            page,
                        "page_size":       page_size,
                    },
                )
                results = resp.json().get("payload", {}).get("results", [])
            except Exception as e:
                logger.warning("G2G seller=%s page=%d error: %s", seller, page, e)
                break

            if not results:
                break

            for o in results:
                try:
                    offer_id = o.get("offer_id", "")
                    qty = int(o.get("available_qty") or 0)
                    if qty <= 0:
                        qty = int(o.get("min_qty") or 1)
                    all_offers.append(G2GOffer(
                        offer_id=offer_id,
                        title=o.get("title", ""),
                        server_name=(_parse_title(o.get("title", ""))[0] or o.get("title", "")),
                        region_id=o.get("region_id", ""),
                        relation_id=o.get("relation_id", ""),
                        price_usd=float(o.get("unit_price_in_usd") or 0),
                        min_qty=int(o.get("min_qty") or 1),
                        available_qty=qty,
                        seller=(o.get("username") or "").strip(),
                        brand_id=o.get("brand_id", brand_id),
                        service_id=o.get("service_id", service_id),
                        offer_url=_build_offer_url(
                            offer_id=offer_id,
                            offer_attributes=o.get("offer_attributes") or [],
                            region_id=o.get("region_id", ""),
                        ),
                        offer_group=o.get("offer_group", ""),
                        raw=o,
                    ))
                except (ValueError, TypeError):
                    continue

            if len(results) < page_size:
                break

            page += 1
            await asyncio.sleep(0.2)

        return all_offers


async def _discover_sellers(
    client: "G2GClient",
    brand_id: str,
    service_id: str,
    regions: list[G2GRegion],
    delay: float = 0.3,
) -> list[str]:
    """
    Собирает уникальные имена продавцов из group-display офферов по всем регионам.

    По каждому region.relation_id делает один запрос page_size=48 (group entries).
    Каждый group entry содержит поле username продавца с cheapest ценой.
    Полученный список используется для последующего fetch_seller_offers().
    """
    sellers: set[str] = set()
    for region in regions:
        try:
            resp = await _http_get_retry(
                client._client,
                f"{BASE}/offer/search",
                params={
                    "brand_id":        brand_id,
                    "service_id":      service_id,
                    "relation_id":     region.relation_id,
                    "country":         client.country,
                    "currency":        client.currency,
                    "sort":            "lowest_price",
                    "include_offline": "0",
                    "page":            1,
                    "page_size":       48,
                },
            )
            results = resp.json().get("payload", {}).get("results", [])
            for o in results:
                u = (o.get("username") or "").strip()
                if u:
                    sellers.add(u)
        except Exception as e:
            logger.warning("G2G discover sellers region=%s: %s", region.region_id, e)
        await asyncio.sleep(delay)

    logger.info("G2G discovered %d unique sellers", len(sellers))
    return sorted(sellers)


async def fetch_g2g_game(
    game_key: str,
    sort: str = "lowest_price",
    country: str = "SG",
    max_regions: Optional[int] = None,
    delay: float = 0.35,
) -> list[G2GOffer]:
    """
    3-шаговый pipeline:
      1) fetch_regions   — получить все region_id / relation_id
      2) _discover_sellers — собрать уникальных продавцов из group entries
      3) fetch_seller_offers (параллельно, батчами по 5) — индивидуальные офферы
         is_group_display=false, stable offer_id, реальный available_qty
    """
    if game_key not in GAME_CONFIG:
        raise ValueError(f"Unknown game: {game_key}. Available: {list(GAME_CONFIG)}")

    cfg        = GAME_CONFIG[game_key]
    brand_id   = cfg["brand_id"]
    service_id = cfg["service_id"]

    async with G2GClient(country=country) as client:
        regions = await client.fetch_regions(brand_id, service_id)
        if max_regions:
            regions = regions[:max_regions]

        if not regions:
            logger.warning("G2G game=%s: no regions found, skipping", game_key)
            return []

        logger.info(
            "G2G game=%s: regions=%d (brand_id=%s service_id=%s country=%s)",
            game_key, len(regions), brand_id, service_id, country,
        )

        sellers = await client.fetch_all_sellers(brand_id, service_id, regions)
        if not sellers:
            logger.warning("G2G: no sellers discovered for %s", game_key)
            return []

        logger.info("G2G: fetching offers for %d sellers", len(sellers))

        all_offers: list[G2GOffer] = []
        batch_size = 5

        for i in range(0, len(sellers), batch_size):
            batch = sellers[i : i + batch_size]
            tasks = [client.fetch_seller_offers(brand_id, service_id, s) for s in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for seller, result in zip(batch, results):
                if isinstance(result, Exception):
                    logger.warning("G2G seller=%s failed: %s", seller, result)
                else:
                    all_offers.extend(result)
            if i + batch_size < len(sellers):
                await asyncio.sleep(delay)

        logger.info(
            "G2G game=%s done: sellers=%d raw_offers=%d",
            game_key, len(sellers), len(all_offers),
        )
        return all_offers


async def discover_brand_ids(
    category_id: str = "3c2a9034-2569-4484-92ad-c00e384e7085",
) -> list[dict]:
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as c:
        r = await _http_get_retry(
            c,
            f"{BASE}/offer/category/{category_id}/popular_brand",
            params={"country": "SG"},
        )
        return r.json().get("payload", {}).get("results", [])


_MAX_PRICE_PER_1K = 300.0  # Hard ceiling: above this is anomalous, skip


def _to_offer(
    raw: G2GOffer,
    fetched_at: datetime,
    skip_qty_check: bool = False,
) -> Optional[Offer]:
    """Convert G2GOffer → Offer using raw price (unit_price_in_usd = per 1 gold).

    Task 2: parsers always return raw price, NEVER compute price_per_1k.
      raw_price      = unit_price_in_usd  (price per 1 gold unit, USD)
      raw_price_unit = 'per_unit'
      lot_size       = 1
    price_per_1k is derived in Offer.model_validator: raw_price * 1000.
    """
    if raw.price_usd <= 0:
        return None

    # Validate against ceiling using derived price (raw_price * 1000)
    if raw.price_usd * 1000.0 > _MAX_PRICE_PER_1K:
        return None

    if raw.available_qty <= 0 and not skip_qty_check:
        return None

    server_name, region, version, faction = _parse_title(raw.title)
    version = _canonicalize_version(version) if version else version
    # Build display_server: "(EU) Version"
    # If neither region nor version can be determined — skip; offer is ungroupable.
    if region and version:
        display_server = f"({region}) {version}"
    elif version:
        display_server = version
    else:
        logger.debug("G2G: skipping unrecognised offer title=%r", raw.title)
        return None

    # Unique ID: offer_group (strip leading "/") + seller
    # ensures two sellers on the same server don't get deduplicated.
    raw_id = raw.offer_group.lstrip("/") if raw.offer_group else raw.offer_id
    offer_id_key = f"g2g_{raw_id}_{raw.seller}" if raw_id else f"g2g_{raw.offer_id}"

    try:
        return Offer(
            id=offer_id_key,
            source=SOURCE,
            server=display_server,
            display_server=display_server,
            server_name=server_name,
            faction=faction,
            # ── Raw price (Task 2) ────────────────────────────────────────────
            raw_price=raw.price_usd,      # unit_price_in_usd: price per 1 gold
            raw_price_unit="per_unit",
            lot_size=1,
            # ── amount & metadata ─────────────────────────────────────────────
            amount_gold=raw.available_qty if raw.available_qty > 0 else 1,
            seller=raw.seller or "unknown",
            offer_url=raw.offer_url or None,
            updated_at=fetched_at,
            fetched_at=fetched_at,
        )
    except Exception:
        return None


def _dedupe(offers: list[Offer]) -> list[Offer]:
    seen: set[str] = set()
    out: list[Offer] = []
    for offer in offers:
        if offer.id in seen:
            continue
        seen.add(offer.id)
        out.append(offer)
    return out


async def fetch_offers() -> list[Offer]:
    """
    Entrypoint для offers_service._run_g2g_loop().
    Парсит WoW Classic Era / Seasonal / TBC Anniversary (один brand_id).
    """
    fetched_at = datetime.now(timezone.utc)
    try:
        raw_offers = await fetch_g2g_game("wow_classic_era_seasonal_anniversary")

        total         = len(raw_offers)
        skipped_price = 0
        skipped_qty   = 0
        ok            = 0
        converted: list[Offer] = []

        for r in raw_offers:
            if r.price_usd <= 0:
                skipped_price += 1
                continue
            # Seller-based path may still occasionally return qty=0 for edge sellers.
            # Keep these offers with minimal amount to avoid dropping valid URLs.
            offer = _to_offer(r, fetched_at, skip_qty_check=True)
            if offer is None:
                skipped_qty += 1
            else:
                ok += 1
                converted.append(offer)

        logger.info(
            "G2G _to_offer: total=%d skipped_price=%d skipped_qty=%d ok=%d",
            total, skipped_price, skipped_qty, ok,
        )
        return _dedupe(converted)
    except Exception:
        logger.exception("G2G parser failed")
        return []
