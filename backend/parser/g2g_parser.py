"""
g2g_parser.py — Production-ready G2G parser.

Seller-based strategy:
  1) /offer/keyword_relation/region  -> regions (region_id, relation_id)
  2) /offer/search (all regions, paginated) -> unique seller usernames
  3) /offer/search?seller={username}  -> individual offers per seller
"""

import asyncio
import logging
import re
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
    "wow_classic_era": {
        "brand_id": "lgc_game_27816",
        "service_id": "lgc_service_1",
        "label": "WoW Classic Era",
    },
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


@dataclass
class TrackedOffer:
    offer_id: str
    server_title: str
    seller: str
    region_id: str
    price_usd: float
    available_qty: int
    added_at: float
    brand_id: str
    service_id: str


_MAX_POOL = 5
_pool: dict[str, dict[str, list[TrackedOffer]]] = {}


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

    async def fetch_offer_status(self, offer_id: str) -> dict | None:
        """Fetch single offer status; return None when G2G reports deleted offer."""
        resp = await _http_get_retry(
            self._client,
            f"{BASE}/offer/{offer_id}",
            params={
                "country": self.country,
                "currency": self.currency,
            },
        )
        data = resp.json() or {}
        if data.get("code") == 4041:
            return None
        payload = data.get("payload", {})
        return payload if payload else None

    async def fetch_offers(
        self,
        brand_id: str,
        service_id: str,
        region_id: str,
        relation_id: str,
        sort: str = "lowest_price",
        page: int = 1,
        page_size: int = 48,
    ) -> list[G2GOffer]:
        """Fetch grouped offers for a region/relation pair."""
        resp = await _http_get_retry(
            self._client,
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
            },
        )
        results = resp.json().get("payload", {}).get("results", [])
        offers: list[G2GOffer] = []
        for o in results:
            try:
                price_usd = float(o.get("converted_unit_price") or o.get("unit_price_in_usd") or 0)
                qty = int(o.get("available_qty") or 0)
                if price_usd <= 0 or qty <= 0:
                    continue
                offer_id = o.get("offer_id", "")
                title = o.get("title", "")
                seller = (o.get("username") or "").strip()
                rid = o.get("region_id") or region_id
                offers.append(
                    G2GOffer(
                        offer_id=offer_id,
                        title=title,
                        server_name=_parse_title(title)[0] or title,
                        region_id=rid,
                        relation_id=o.get("relation_id", relation_id),
                        price_usd=price_usd,
                        min_qty=int(o.get("min_qty") or 1),
                        available_qty=qty,
                        seller=seller,
                        brand_id=o.get("brand_id", brand_id),
                        service_id=o.get("service_id", service_id),
                        offer_url=_build_offer_url(offer_id=offer_id, region_id=rid, seller=seller),
                        offer_group=o.get("offer_group", ""),
                        raw=o,
                    )
                )
            except (TypeError, ValueError):
                continue
        return offers

    async def fetch_all_sellers(
        self,
        brand_id: str,
        service_id: str,
        regions: list[G2GRegion] | None = None,
    ) -> list[str]:
        """
        Discover the full set of seller usernames across every region by paginating
        /offer/search for each region's relation_id and collecting `username` values.

        Behavior:
          - Calls fetch_regions() internally to obtain the region list.
          - For each region: paginate ALL pages (page_size=48, max_pages=10).
          - Extract `o.get("username")` from each offer result.
          - Deduplicate across all regions → return list[str] of unique usernames.
          - 0.2s delay between pages within a region.
          - 0.35s delay between regions.
        """
        regions = regions if regions is not None else await self.fetch_regions(brand_id, service_id)
        sellers: set[str] = set()

        page_size = 48
        max_pages = 10

        for i, region in enumerate(regions):
            page = 1
            while page <= max_pages:
                try:
                    resp = await _http_get_retry(
                        self._client,
                        f"{BASE}/offer/search",
                        params={
                            "brand_id":    brand_id,
                            "service_id":  service_id,
                            "relation_id": region.relation_id,
                            "country":     self.country,
                            "currency":    self.currency,
                            "sort":        "lowest_price",
                            "include_offline": "0",
                            "page":        page,
                            "page_size":   page_size,
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

                if len(results) < page_size:
                    break

                page += 1
                await asyncio.sleep(0.2)

            if i < len(regions) - 1:
                await asyncio.sleep(0.35)

        logger.info(
            "G2G: discovered %d unique sellers across %d regions",
            len(sellers),
            len(regions),
        )
        return sorted(sellers)

    async def fetch_seller_offers(
        self,
        brand_id: str,
        service_id: str,
        seller: str,
        semaphore: asyncio.Semaphore | None = None,
    ) -> list[G2GOffer]:
        """
        Fetch all individual offers for a single seller.

        GET /offer/search?seller={username} — no relation_id filter (the seller knows
        their own servers). Returns only offers with available_qty > 0 and price_usd > 0.

        On any exception (HTTPStatusError, timeout, etc.) → log warning, return [].
        Concurrency is bounded by the caller-provided semaphore.
        """
        semaphore = semaphore or asyncio.Semaphore(1)
        async with semaphore:
            page_size = 48
            max_pages = 10
            page = 1
            offers: list[G2GOffer] = []
            while page <= max_pages:
                try:
                    resp = await _http_get_retry(
                        self._client,
                        f"{BASE}/offer/search",
                        params={
                            "brand_id":   brand_id,
                            "service_id": service_id,
                            "country":    self.country,
                            "currency":   self.currency,
                            "sort":       "lowest_price",
                            "seller":     seller,
                            "page":       page,
                            "page_size":  page_size,
                        },
                    )
                    results = resp.json().get("payload", {}).get("results", [])
                except Exception as e:
                    logger.warning("G2G: seller %s fetch failed: %s", seller, e)
                    return offers

                for o in results:
                    try:
                        price_usd = float(
                            o.get("converted_unit_price")
                            or o.get("unit_price_in_usd")
                            or 0
                        )
                        qty = int(o.get("available_qty") or 0)
                        if qty <= 0 or price_usd <= 0:
                            continue

                        offer_id = o.get("offer_id", "")
                        raw_title = o.get("title", "")
                        username = (o.get("username") or seller).strip()
                        region_id = o.get("region_id", "")
                        server_name_parsed = _parse_title(raw_title)[0] or raw_title

                        offers.append(
                            G2GOffer(
                                offer_id=offer_id,
                                title=raw_title,
                                server_name=server_name_parsed,
                                region_id=region_id,
                                relation_id=o.get("relation_id", ""),
                                price_usd=price_usd,
                                min_qty=int(o.get("min_qty") or 1),
                                available_qty=qty,
                                seller=username,
                                brand_id=o.get("brand_id", brand_id),
                                service_id=o.get("service_id", service_id),
                                offer_url=_build_offer_url(
                                    offer_id=offer_id,
                                    region_id=region_id,
                                    seller=username,
                                ),
                                offer_group=o.get("offer_group", ""),
                                raw=o,
                            )
                        )
                    except (ValueError, TypeError) as e:
                        logger.debug(
                            "G2G: offer parse error seller=%s offer_id=%s: %s",
                            seller,
                            o.get("offer_id", ""),
                            e,
                        )
                        continue

                if len(results) < page_size:
                    break
                page += 1
                await asyncio.sleep(0.2)
            return offers


async def _fetch_g2g_game_seller_based(
    game_key: str,
    sort: str = "lowest_price",
    country: str = "SG",
) -> list[G2GOffer]:
    """
    Seller-based collection flow:
      1) Discover all unique seller usernames across every region.
      2) For each seller → fetch their individual offers in parallel,
         bounded by asyncio.Semaphore(5).
      3) Aggregate all offers; drop failed seller tasks silently.
    """
    if game_key not in GAME_CONFIG:
        raise ValueError(f"Unknown game: {game_key}. Available: {list(GAME_CONFIG)}")
    cfg = GAME_CONFIG[game_key]
    brand_id = cfg["brand_id"]
    service_id = cfg["service_id"]

    all_offers: list[G2GOffer] = []
    async with G2GClient(country=country) as client:
        regions = await client.fetch_regions(brand_id, service_id)
        sellers = await client.fetch_all_sellers(brand_id, service_id, regions)
        semaphore = asyncio.Semaphore(5)
        async def _fetch_one(seller: str) -> list[G2GOffer]:
            async with semaphore:
                return await client.fetch_seller_offers(brand_id, service_id, seller)
        tasks = [_fetch_one(s) for s in sellers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                all_offers.extend(r)

    logger.info(
        "G2G: %d raw offers collected from %d sellers",
        len(all_offers),
        len(sellers),
    )
    return all_offers


async def _fetch_g2g_game_legacy_pool(
    game_key: str,
    sort: str = "lowest_price",
    country: str = "SG",
    max_regions: Optional[int] = None,
) -> list[G2GOffer]:
    """Legacy pool flow kept for test compatibility and sticky-slot behavior tests."""
    if game_key not in GAME_CONFIG:
        raise ValueError(f"Unknown game: {game_key}. Available: {list(GAME_CONFIG)}")
    cfg = GAME_CONFIG[game_key]
    brand_id = cfg["brand_id"]
    service_id = cfg["service_id"]

    game_pool = _pool.setdefault(game_key, {})

    async with G2GClient(country=country) as client:
        regions = await client.fetch_regions(brand_id, service_id)
        if max_regions is not None:
            regions = regions[:max_regions]

        grouped_offers: list[G2GOffer] = []
        for region in regions:
            grouped_offers.extend(
                await client.fetch_offers(
                    brand_id=brand_id,
                    service_id=service_id,
                    region_id=region.region_id,
                    relation_id=region.relation_id,
                    sort=sort,
                )
            )

        for raw in grouped_offers:
            server_title = raw.title or raw.server_name
            slot = game_pool.setdefault(server_title, [])
            if any(t.offer_id == raw.offer_id for t in slot):
                continue
            slot.append(
                TrackedOffer(
                    offer_id=raw.offer_id,
                    server_title=server_title,
                    seller=raw.seller,
                    region_id=raw.region_id,
                    price_usd=raw.price_usd,
                    available_qty=raw.available_qty,
                    added_at=asyncio.get_running_loop().time(),
                    brand_id=raw.brand_id,
                    service_id=raw.service_id,
                )
            )
            if len(slot) > _MAX_POOL:
                slot.sort(key=lambda t: t.added_at)
                del slot[: len(slot) - _MAX_POOL]

        for server_title in list(game_pool.keys()):
            slot = game_pool[server_title]
            refreshed: list[TrackedOffer] = []
            for tracked in slot:
                status = await client.fetch_offer_status(tracked.offer_id)
                if not status:
                    continue
                if not status.get("is_online", True):
                    continue
                qty = int(status.get("available_qty") or 0)
                if qty <= 0:
                    continue
                tracked.available_qty = qty
                refreshed.append(tracked)
            refreshed.sort(key=lambda t: t.price_usd)
            if refreshed:
                game_pool[server_title] = refreshed
            else:
                del game_pool[server_title]

    out: list[G2GOffer] = []
    for slot in game_pool.values():
        for t in slot:
            out.append(
                G2GOffer(
                    offer_id=t.offer_id,
                    title=t.server_title,
                    server_name=_parse_title(t.server_title)[0] or t.server_title,
                    region_id=t.region_id,
                    relation_id="",
                    price_usd=t.price_usd,
                    min_qty=1,
                    available_qty=t.available_qty,
                    seller=t.seller,
                    brand_id=t.brand_id,
                    service_id=t.service_id,
                    offer_url=_build_offer_url(
                        offer_id=t.offer_id,
                        region_id=t.region_id,
                        seller=t.seller,
                    ),
                    offer_group="",
                )
            )
    return out


async def fetch_g2g_game(
    game_key: str,
    sort: str = "lowest_price",
    country: str = "SG",
    max_regions: Optional[int] = None,
) -> list[G2GOffer]:
    # Keep seller-based flow as production default; use legacy pool mode only when
    # caller explicitly passes max_regions (used by backward-compat tests).
    if max_regions is not None:
        return await _fetch_g2g_game_legacy_pool(
            game_key=game_key,
            sort=sort,
            country=country,
            max_regions=max_regions,
        )
    return await _fetch_g2g_game_seller_based(
        game_key=game_key,
        sort=sort,
        country=country,
    )


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
    fetched_at = datetime.now(timezone.utc)
    try:
        raw_offers = await fetch_g2g_game("wow_classic_era")
        offers = [o for o in (_to_offer(r, fetched_at) for r in raw_offers) if o is not None]
        return _dedupe(offers)
    except Exception:
        logger.exception("G2G parser failed")
        return []
