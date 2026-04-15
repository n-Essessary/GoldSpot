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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import httpx

from api.schemas import Offer

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
    region_id: str,
    seller: str,
    game_slug: str = _CATEGORY_SLUG,
) -> str:
    """
    Build direct buy URL for a seller-based G2G offer.

    Format verified live against the G2G site (Task 2):
      https://www.g2g.com/categories/wow-classic-era-vanilla-gold
      /offer/{offer_id}?region_id={region_id}&seller={seller}

    The old /offer/{offer_id} format (without query params) leads to dead pages.
    The old /offer/group?fa=... format (group-display) is not applicable to
    seller-fetched individual offers which have stable offer_ids.
    """
    if not offer_id:
        return ""
    return (
        f"https://www.g2g.com/categories/{game_slug}"
        f"/offer/{offer_id}"
        f"?region_id={region_id}&seller={seller}"
    )


# ── Title parsing regex ───────────────────────────────────────────────────────
# Strict format: "Server [Region - Version] - Faction"
_TITLE_RE = re.compile(
    r"^(?P<server>.+?)\s*"
    r"\[(?P<region>[A-Za-z]{2,})\s*-\s*(?P<version>[^\]]+?)\]\s*"
    r"(?:-\s*(?P<faction>Alliance|Horde))?$",
    re.IGNORECASE,
)

# Region and faction helpers (used in _parse_title)
_REGION_RE         = re.compile(r"\b(EU|US|NA|OCE|KR|TW|SEA|RU)\b", re.IGNORECASE)
_BRACKET_REGION_RE = re.compile(r"\[([A-Za-z]{2,})\]")   # "[EU]" without version
_FACTION_END_RE    = re.compile(r"-\s*(Alliance|Horde)\s*$", re.IGNORECASE)

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
    """Parse a raw G2G offer title → (server_name, source_region, version, faction).

    Responsibility boundary (strict):
      • Extracts: realm name, source region label, faction.
      • Does NOT attempt to determine game version.
      • Does NOT correct region based on realm identity.
      Version and normalised region are resolved exclusively from the canonical
      server registry (canonical_servers.py / servers DB table) during the
      normalization pipeline step. This guarantees deterministic, registry-driven
      identity assignment with no heuristic guessing.

    Level 1 — strict regex (covers the vast majority of G2G titles):
        "Spineshatter [EU - Anniversary] - Alliance"
          → ("Spineshatter", "EU", "Alliance")
        "Penance [EU - Seasonal] - Horde"
          → ("Penance", "EU", "Horde")
          (canonical registry maps Penance EU → AU Season of Discovery)

    Level 2 — flexible fallback for non-standard formats:
        "Firemaw [EU] - Alliance"  → ("Firemaw",  "EU", "Alliance")
        "Firemaw - Alliance"       → ("Firemaw",  "",   "Alliance")

    Returns ("", "", "", "Horde") for empty / unparseable titles.
    The empty server_name will be caught by normalize_pipeline's
    empty_server_title validation.
    """
    t = (title or "").strip()
    if not t:
        return "", "", "", "Horde"

    # ── Level 1: strict regex ─────────────────────────────────────────────────
    m = _TITLE_RE.match(t)
    if m:
        server_name = (m.group("server") or "").strip()
        region      = (m.group("region") or "").upper().strip()
        # Pass version through verbatim from the bracket.
        # Canonicalization ("Seasonal" → "Season of Discovery", etc.) happens
        # downstream in _normalize_g2g_offer via _canonicalize_version so that
        # the raw title alias key is never corrupted by pre-normalization.
        version = (m.group("version") or "").strip()
        faction     = (m.group("faction") or "").strip().capitalize() or (
            "Alliance" if "alliance" in t.lower() else "Horde"
        )
        return server_name, region, version, faction

    # ── Level 2: flexible fallback ────────────────────────────────────────────

    # Faction
    fm = _FACTION_END_RE.search(t)
    if fm:
        faction = fm.group(1).capitalize()
    elif "alliance" in t.lower():
        faction = "Alliance"
    else:
        faction = "Horde"

    # server_name: part before first "[", or before last " - faction"
    server_name = ""
    if "[" in t:
        server_name = t[:t.index("[")].strip()
    else:
        parts = re.split(r"\s+-\s+", t)
        if len(parts) >= 2 and parts[-1].strip().lower() in ("alliance", "horde"):
            server_name = parts[0].strip()

    # Source region: look in "[EU]" brackets first, then anywhere in title
    region = ""
    _KNOWN_REGIONS = {"EU", "US", "NA", "OCE", "KR", "TW", "SEA", "RU"}
    bm = _BRACKET_REGION_RE.search(t)
    if bm and bm.group(1).upper() in _KNOWN_REGIONS:
        region = bm.group(1).upper()
    else:
        rm = _REGION_RE.search(t)
        if rm:
            region = rm.group(1).upper()

    if not server_name:
        server_name = t

    lt = t.lower()
    if "classic era" in lt:
        version = "Classic Era"
    elif "seasonal" in lt or "season of discovery" in lt or " sod " in f" {lt} ":
        version = "Seasonal"
    elif "anniversary" in lt:
        version = "Anniversary"
    elif "hardcore" in lt:
        version = "Hardcore"
    else:
        version = "Classic" if region else ""
    return server_name, region, version, faction


@dataclass
class TrackedOffer:
    offer_id:      str
    server_title:  str    # raw G2G title e.g. "Spineshatter [EU - Anniversary] - Horde"
    seller:        str
    region_id:     str
    price_usd:     float  # updated each cycle by System 2
    available_qty: int
    added_at:      float  # time.monotonic() when added
    brand_id:      str
    service_id:    str


# key = game_key → server_title → list of TrackedOffer, max 5, sorted price ASC
_pool: dict[str, dict[str, list[TrackedOffer]]] = {}
_MAX_POOL = 5


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

    async def fetch_offers(
        self,
        brand_id: str,
        service_id: str,
        relation_id: str,
        sort: str = "lowest_price",
        page_size: int = 48,
    ) -> list[G2GOffer]:
        all_offers: list[G2GOffer] = []
        page = 1
        max_pages = 20  # increased from 10; grouped view has more pages per region

        while page <= max_pages:
            resp = await self._client.get(
                f"{BASE}/offer/search",
                params={
                    "brand_id": brand_id,
                    "service_id": service_id,
                    "relation_id": relation_id,
                    "country": self.country,
                    "currency": self.currency,
                    "sort": sort,
                    "page": page,
                    "page_size": page_size,
                    "include_offline": "0",    # online sellers only
                    "group_by": "keyword_id",  # one best-price offer per server×faction
                },
            )
            resp.raise_for_status()

            results = resp.json().get("payload", {}).get("results", [])

            if not results:
                break

            for o in results:
                try:
                    # grouped response: use converted_unit_price (per-unit),
                    # NOT unit_price_in_usd (which is per-lot in grouped mode)
                    price_usd = float(o.get("converted_unit_price") or 0)
                    if price_usd <= 0:
                        continue
                    all_offers.append(
                        G2GOffer(
                            offer_id=o.get("offer_id", ""),
                            title=o.get("title", ""),
                            server_name=o.get("title", ""),
                            region_id=o.get("region_id", ""),
                            relation_id=o.get("relation_id", ""),
                            price_usd=price_usd,
                            min_qty=int(o.get("min_qty") or 1),
                            available_qty=int(o.get("available_qty") or 0),
                            seller=(o.get("username") or "").strip(),
                            brand_id=o.get("brand_id", ""),
                            service_id=o.get("service_id", ""),
                            raw=o,
                        )
                    )
                except (ValueError, TypeError):
                    continue

            logger.debug(
                "G2G grouped: relation_id=%s page=%d → %d offers (total %d)",
                relation_id, page, len(results), len(all_offers),
            )

            if len(results) < page_size:
                break

            page += 1
            await asyncio.sleep(0.2)

        return all_offers

    async def fetch_offer_status(
        self,
        offer_id: str,
    ) -> dict | None:
        """
        Fetch current status of a single offer by offer_id.
        Returns payload dict if offer is live, None if deleted or error.
        """
        try:
            resp = await _http_get_retry(
                self._client,
                f"{BASE}/offer/{offer_id}",
                params={"country": self.country, "currency": self.currency},
            )
            data = resp.json()
            # Deleted offer returns code 4041
            if data.get("code") == 4041:
                return None
            payload = data.get("payload", data)
            if not payload or not payload.get("offer_id"):
                return None
            return payload
        except Exception as e:
            logger.debug("G2G offer_status offer_id=%s error: %s", offer_id, e)
            return None

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
                await asyncio.sleep(0.2)  # Task 1 delay policy: 0.2s between region page requests

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
        max_pages = 10

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
                    offer_id  = o.get("offer_id", "")
                    raw_title = o.get("title", "")
                    qty = int(o.get("available_qty") or 0)
                    if qty <= 0:
                        qty = int(o.get("min_qty") or 1)
                    # _parse_title returns (server_name, source_region, faction);
                    # only server_name is needed here for G2GOffer.server_name.
                    server_name_parsed = _parse_title(raw_title)[0] or raw_title
                    all_offers.append(G2GOffer(
                        offer_id=offer_id,
                        title=raw_title,
                        server_name=server_name_parsed,
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
                            region_id=o.get("region_id", ""),
                            seller=(o.get("username") or "").strip(),
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
) -> list[G2GOffer]:
    """
    Two-system architecture:
      System 1 — Scout: grouped search → cheapest online offer per server×faction
                        → hand off new offer_ids to pool (max 5 per slot).
      System 2 — Tracker: GET /offer/{offer_id} for each tracked offer in parallel;
                          refresh price/qty, evict deleted/offline offers, sort ASC.
    """
    if game_key not in GAME_CONFIG:
        raise ValueError(f"Unknown game: {game_key}. Available: {list(GAME_CONFIG)}")
    cfg = GAME_CONFIG[game_key]
    brand_id = cfg["brand_id"]
    service_id = cfg["service_id"]
    server_pool = _pool.setdefault(game_key, {})

    # ══════════════════════════════════════════════════════════════
    # SYSTEM 1 — Scout: grouped search → discover cheapest per slot
    # ══════════════════════════════════════════════════════════════
    grouped_offers: list[G2GOffer] = []
    async with G2GClient(country=country) as client:
        regions = await client.fetch_regions(brand_id, service_id)
        if max_regions:
            regions = regions[:max_regions]
        for i, region in enumerate(regions):
            try:
                offers = await client.fetch_offers(
                    brand_id=brand_id,
                    service_id=service_id,
                    relation_id=region.relation_id,
                    sort=sort,
                )
                grouped_offers.extend(offers)
            except httpx.HTTPStatusError as e:
                logger.warning("G2G scout HTTP error region %s: %s", region.region_id, e)
            if i < len(regions) - 1:
                await asyncio.sleep(0.3)
    logger.info("G2G system1 scout: %d grouped offers", len(grouped_offers))

    # Hand off new cheapest offers to pool
    for o in grouped_offers:
        if not o.offer_id or not o.title or not o.seller:
            continue
        slot = server_pool.setdefault(o.title, [])
        # Add only if offer_id not already tracked in this slot
        tracked_ids = {t.offer_id for t in slot}
        if o.offer_id not in tracked_ids:
            if len(slot) >= _MAX_POOL:
                # Evict oldest entry to make room
                slot.sort(key=lambda t: t.added_at)
                evicted = slot.pop(0)
                logger.debug(
                    "G2G pool evict oldest %s from slot %.50s",
                    evicted.seller, o.title,
                )
            slot.append(TrackedOffer(
                offer_id=o.offer_id,
                server_title=o.title,
                seller=o.seller,
                region_id=o.region_id,
                price_usd=o.price_usd,
                available_qty=o.available_qty,
                added_at=time.monotonic(),
                brand_id=brand_id,
                service_id=service_id,
            ))
            logger.debug("G2G pool add %s to slot %.50s", o.seller, o.title)

    # ══════════════════════════════════════════════════════════════
    # SYSTEM 2 — Tracker: monitor each tracked offer individually
    # ══════════════════════════════════════════════════════════════
    all_tracked: list[TrackedOffer] = [
        t for slot in server_pool.values() for t in slot
    ]
    # Fetch status for all tracked offers in parallel batches
    BATCH = 10
    BATCH_DELAY = 0.3
    status_map: dict[str, dict | None] = {}
    async with G2GClient(country=country) as client:
        for i in range(0, len(all_tracked), BATCH):
            batch = all_tracked[i:i + BATCH]
            results = await asyncio.gather(
                *[client.fetch_offer_status(t.offer_id) for t in batch],
                return_exceptions=True,
            )
            for tracked, result in zip(batch, results):
                if isinstance(result, Exception):
                    logger.warning(
                        "G2G tracker offer_id=%s error: %s",
                        tracked.offer_id, result,
                    )
                    status_map[tracked.offer_id] = tracked.__dict__  # keep as-is on error
                else:
                    status_map[tracked.offer_id] = result
            if i + BATCH < len(all_tracked):
                await asyncio.sleep(BATCH_DELAY)
    logger.info("G2G system2 tracker: %d offers monitored", len(all_tracked))

    # Update pool: refresh prices, remove dead offers
    for server_title, slot in list(server_pool.items()):
        for tracked in list(slot):
            payload = status_map.get(tracked.offer_id)
            if payload is None:
                # Offer deleted or error — remove
                slot.remove(tracked)
                logger.debug(
                    "G2G pool remove %s from slot %.50s — deleted/gone",
                    tracked.seller, server_title,
                )
                continue
            is_online = payload.get("is_online", True)
            status = payload.get("status", "live")
            if not is_online or status != "live":
                slot.remove(tracked)
                logger.debug(
                    "G2G pool remove %s from slot %.50s — offline/inactive",
                    tracked.seller, server_title,
                )
                continue
            # Update live price and qty
            tracked.price_usd = float(payload.get("unit_price_in_usd") or tracked.price_usd)
            tracked.available_qty = int(payload.get("available_qty") or tracked.available_qty)
        # Sort slot by price ASC
        slot.sort(key=lambda t: t.price_usd)
        # Clean up empty slots
        if not slot:
            del server_pool[server_title]

    # ══════════════════════════════════════════════════════════════
    # Build output: convert pool to G2GOffer list
    # Grouped offers fill gaps for any server not yet in pool
    # ══════════════════════════════════════════════════════════════
    seen: set[str] = set()
    combined: list[G2GOffer] = []
    # Pool offers first — these have live-updated prices
    for server_title, slot in server_pool.items():
        for tracked in slot:
            if tracked.offer_id in seen:
                continue
            seen.add(tracked.offer_id)
            combined.append(G2GOffer(
                offer_id=tracked.offer_id,
                title=tracked.server_title,
                server_name=tracked.server_title,
                region_id=tracked.region_id,
                relation_id="",
                price_usd=tracked.price_usd,
                min_qty=1,
                available_qty=tracked.available_qty,
                seller=tracked.seller,
                brand_id=tracked.brand_id,
                service_id=tracked.service_id,
                offer_url=_build_offer_url(
                    offer_id=tracked.offer_id,
                    region_id=tracked.region_id,
                    seller=tracked.seller,
                ),
            ))
    # Grouped fallback for servers not yet in pool
    for o in grouped_offers:
        key = o.offer_id if o.offer_id else f"{o.seller}:{o.title}"
        if key not in seen:
            seen.add(key)
            combined.append(o)
    pool_offer_count = sum(len(s) for s in server_pool.values())
    logger.info(
        "G2G total: %d offers (%d from pool, %d from grouped fallback)",
        len(combined), pool_offer_count, len(combined) - pool_offer_count,
    )
    return combined


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

    Separation of concerns (strict):
      • Parser role: extract raw data only — server_name, faction, price, qty.
      • Canonical role: resolve version, region, realm_type from registry.
        This happens in normalize_pipeline.normalize_offer_batch(), NOT here.

    Key fields set by this function:
      raw_title      — verbatim G2G API title; used as alias lookup key in
                       normalize_pipeline._build_alias_key(). This is the exact
                       string stored in server_aliases (e.g. "Firemaw [EU - Classic Era] - Horde").
      server_name    — parsed realm name (e.g. "Firemaw"); temporary until
                       canonicalization overwrites it.
      display_server — left empty (""); canonicalization sets it from registry.
      server         — set to server_name.lower() as temporary slug; overwritten.
      realm_type     — default "Normal"; canonicalization sets it from registry.

    Offers with price <= 0 or above ceiling are dropped here (not quarantined)
    because these are clearly invalid data points, not unresolved servers.

    Raw price contract (Task 2):
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

    # Extract only server_name and faction — NOT version (canonical resolves that)
    server_name, _source_region, _raw_version, faction = _parse_title(raw.title)

    # Unique ID: offer_group (strip leading "/") + seller
    raw_id = raw.offer_group.lstrip("/") if raw.offer_group else raw.offer_id
    offer_id_key = f"g2g_{raw_id}_{raw.seller}" if raw_id else f"g2g_{raw.offer_id}"

    try:
        return Offer(
            id=offer_id_key,
            source=SOURCE,
            # Temporary slug — overwritten by _apply_canonical() in normalize_pipeline.
            # Must be non-empty for Offer model validation to pass.
            server=server_name.lower() if server_name else offer_id_key,
            # display_server intentionally left empty; set by canonicalization.
            display_server="",
            server_name=server_name,
            faction=faction,
            # raw_title stored for alias lookup in normalize_pipeline._build_alias_key()
            raw_title=raw.title,
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
