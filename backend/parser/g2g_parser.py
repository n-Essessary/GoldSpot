"""
g2g_parser.py — Production-ready G2G parser.

Sort-based strategy (simple, verified):
  1. /offer/search?sort=lowest_price    → cheapest offer per server×faction (~221 offers)
  2. /offer/search?sort=recommended_v2  → recommended offer per server×faction (~221 offers)
  Both sorts run concurrently via asyncio.gather(); results combined and deduplicated.
  Total expected: ~300–440 unique offers per game config.
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


def _build_offer_url(offer_group: str, region_id: str, sort: str, seo_term: str = "wow-classic-era-vanilla-gold") -> str:
    """Build G2G group buy page URL from offer_group, region and sort."""
    if not offer_group or not region_id:
        return ""
    og = offer_group.lstrip("/")
    prefix = re.sub(r"_\d+$", "", og)
    fa = f"{prefix}:{og}"
    from urllib.parse import quote
    fa_encoded = quote(fa, safe="")
    return (
        f"https://www.g2g.com/categories/{seo_term}/offer/group"
        f"?fa={fa_encoded}&region_id={region_id}&sort={sort}&include_offline=0"
    )


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


@dataclass(frozen=True)
class GameConfig:
    key: str
    seo_term: str
    brand_id: str
    game_version: str
    service_id: str = "lgc_service_1"
    label: str = ""


GAME_CONFIGS: list[GameConfig] = [
    GameConfig(
        key="wow_classic_era",
        seo_term="wow-classic-era-vanilla-gold",
        brand_id="lgc_game_27816",
        game_version="Classic Era",
        label="WoW Classic Era / Seasonal / TBC Anniversary",
    ),
    GameConfig(
        key="wow_mop_classic",
        seo_term="wow-classic-gold",
        brand_id="lgc_game_29076",
        game_version="MoP Classic",
        label="WoW Mists of Pandaria Classic",
    ),
]


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
    sort: str = ""
    game_version: str = ""
    offer_url: str | None = None
    offer_group: str = ""
    raw: dict = field(default_factory=dict, repr=False)


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
        version = "Classic"
    elif "seasonal" in lt or "season of discovery" in lt or " sod " in f" {lt} ":
        version = "Seasonal"
    elif "anniversary" in lt:
        version = "Anniversary"
    elif "hardcore" in lt:
        version = "Hardcore"
    else:
        version = "Classic" if region else ""
    return server_name, region, version, faction


# ── Sort-based fetcher ────────────────────────────────────────────────────────

_PAGE_SIZE = 48
_MAX_PAGES = 10


async def _fetch_sort(sort: str, client: httpx.AsyncClient, config: GameConfig) -> list[G2GOffer]:
    """Fetch offers via two-phase strategy for a single sort mode and game config.

    Phase 1 (discovery): paginate grouped /offer/search and collect unique
    (offer_group, region_id) pairs.
    Phase 2 (real price): request concrete offers per discovered pair using
    filter_attr+region_id with group=0, include_offline=0, page_size=1.
    """
    discovered_pairs: dict[tuple[str, str], None] = {}
    page = 1

    # Phase 1: discovery (grouped results, price is not trusted)
    while page <= _MAX_PAGES:
        params = {
            "seo_term":   config.seo_term,
            "sort":       sort,
            "service_id": config.service_id,
            "brand_id":   config.brand_id,
            "currency":   "USD",
            "country":    "SG",
            "v":          "v2",
            "page_size":  _PAGE_SIZE,
            "page":       page,
        }
        try:
            resp = await _http_get_retry(client, f"{BASE}/offer/search", params=params)
            results = resp.json().get("payload", {}).get("results", [])
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                retry_after = _parse_retry_after_seconds(
                    exc.response.headers.get("Retry-After"), 60
                )
                logger.warning("G2G 429 on config=%s sort=%s page=%d — sleeping %ds", config.key, sort, page, retry_after)
                await asyncio.sleep(retry_after)
                # retry once
                try:
                    resp = await _http_get_retry(client, f"{BASE}/offer/search", params=params)
                    results = resp.json().get("payload", {}).get("results", [])
                except Exception as retry_exc:
                    logger.warning("G2G retry failed config=%s sort=%s page=%d: %s", config.key, sort, page, retry_exc)
                    break
            elif status >= 500:
                logger.warning("G2G 5xx config=%s sort=%s page=%d — sleeping 2s", config.key, sort, page)
                await asyncio.sleep(2)
                try:
                    resp = await _http_get_retry(client, f"{BASE}/offer/search", params=params)
                    results = resp.json().get("payload", {}).get("results", [])
                except Exception as retry_exc:
                    logger.warning("G2G retry failed config=%s sort=%s page=%d: %s", config.key, sort, page, retry_exc)
                    break
            else:
                logger.warning("G2G HTTP error config=%s sort=%s page=%d: %s", config.key, sort, page, exc)
                break
        except Exception as exc:
            logger.warning("G2G fetch error config=%s sort=%s page=%d: %s", config.key, sort, page, exc)
            break

        if not results:
            break

        for o in results:
            offer_group = (o.get("offer_group") or "").strip()
            region_id = (o.get("region_id") or "").strip()
            if not offer_group or not region_id:
                continue
            discovered_pairs[(offer_group, region_id)] = None

        logger.debug("G2G config=%s sort=%s page=%d → %d results", config.key, sort, page, len(results))

        if len(results) < _PAGE_SIZE:
            break

        page += 1
        await asyncio.sleep(0.2)

    # Phase 2: fetch real offers concurrently (correct prices)
    sem = asyncio.Semaphore(20)
    pair_list = list(discovered_pairs.keys())

    async def _fetch_real_offer(offer_group: str, region_id: str) -> Optional[G2GOffer]:
        og = offer_group.lstrip("/")
        prefix = re.sub(r"_\d+$", "", og)
        fa = f"{prefix}:{og}"

        params = {
            "seo_term":        config.seo_term,
            "filter_attr":     fa,
            "region_id":       region_id,
            "sort":            sort,
            "group":           "0",
            "include_offline": "0",
            "page_size":       1,
            "page":            1,
            "service_id":      config.service_id,
            "brand_id":        config.brand_id,
            "currency":        "USD",
            "country":         "SG",
            "v":               "v2",
        }

        async with sem:
            try:
                resp = await _http_get_retry(client, f"{BASE}/offer/search", params=params)
                items = resp.json().get("payload", {}).get("results", [])
            except Exception as exc:
                logger.warning(
                    "G2G phase2 fetch failed config=%s sort=%s offer_group=%s region_id=%s: %s",
                    config.key,
                    sort,
                    offer_group,
                    region_id,
                    exc,
                )
                return None

        if not items:
            return None

        item = items[0]
        try:
            price_usd = float(item.get("unit_price_in_usd") or 0)
            offer_id = item.get("offer_id", "")
            seller = (item.get("username") or "").strip()
            raw_title = item.get("title", "")
            relation_id = item.get("relation_id", "")
            real_region_id = (item.get("region_id") or region_id or "").strip()
            real_offer_group = (item.get("offer_group") or offer_group or "").strip()

            offer_url = _build_offer_url(
                offer_group=real_offer_group,
                region_id=real_region_id,
                sort=sort,
                seo_term=config.seo_term,
            )

            return G2GOffer(
                offer_id=offer_id,
                title=raw_title,
                server_name=_parse_title(raw_title)[0] or raw_title,
                region_id=real_region_id,
                relation_id=relation_id,
                price_usd=price_usd if price_usd > 0 else 0,
                min_qty=int(item.get("min_qty") or 1),
                available_qty=int(item.get("available_qty") or 0),
                seller=seller,
                brand_id=item.get("brand_id", config.brand_id),
                service_id=item.get("service_id", config.service_id),
                sort=sort,
                game_version=config.game_version,
                offer_url=offer_url,
                offer_group=real_offer_group,
                raw=dict(item),
            )
        except (ValueError, TypeError):
            return None

    phase2_results = await asyncio.gather(
        *[_fetch_real_offer(offer_group, region_id) for offer_group, region_id in pair_list],
        return_exceptions=True,
    )

    offers: list[G2GOffer] = []
    for result in phase2_results:
        if isinstance(result, Exception):
            logger.warning("G2G phase2 task exception config=%s sort=%s: %s", config.key, sort, result)
            continue
        if result is None:
            continue
        offers.append(result)

    logger.info(
        "G2G phase2 config=%s sort=%s discovered=%d resolved=%d",
        config.key,
        sort,
        len(pair_list),
        len(offers),
    )
    return offers


# ── Offer conversion & deduplication ─────────────────────────────────────────

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

    offer_id_key = f"g2g_{raw.offer_id}"

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
            game_version=raw.game_version,
            # ── Raw price (Task 2) ────────────────────────────────────────────
            raw_price=raw.price_usd,      # unit_price_in_usd: price per 1 gold
            raw_price_unit="per_unit",
            lot_size=1,
            # ── amount & metadata ─────────────────────────────────────────────
            amount_gold=raw.available_qty if raw.available_qty > 0 else 1,
            seller="Lowest Price" if raw.sort == "lowest_price" else "Recommended",
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


# ── Public entry point ────────────────────────────────────────────────────────

async def fetch_offers() -> list[Offer]:
    """Fetch all G2G offers across all game configs and two sort modes concurrently.

    Runs _fetch_sort for every (config, sort) combination in parallel,
    combines results, converts to Offer objects, and deduplicates by offer id.
    Expected: ~300–440 unique offers per config in under 20s.
    """
    t0 = asyncio.get_event_loop().time()
    fetched_at = datetime.now(timezone.utc)

    try:
        async with httpx.AsyncClient(
            headers=HEADERS,
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        ) as client:
            tasks = [
                _fetch_sort(sort, client, config)
                for config in GAME_CONFIGS
                for sort in ("lowest_price", "recommended_v2")
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        all_raw: list[G2GOffer] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                config = GAME_CONFIGS[i // 2]
                sort = ("lowest_price", "recommended_v2")[i % 2]
                logger.warning("G2G fetch_sort task exception config=%s sort=%s: %s", config.key, sort, result)
                continue
            all_raw.extend(result)

        offers = [o for o in (_to_offer(r, fetched_at) for r in all_raw) if o is not None]
        result_offers = _dedupe(offers)

        elapsed = asyncio.get_event_loop().time() - t0
        logger.info(
            "G2G updated: %d offers in %.1fs (configs=%d raw=%d)",
            len(result_offers),
            elapsed,
            len(GAME_CONFIGS),
            len(all_raw),
        )
        return result_offers

    except Exception:
        logger.exception("G2G parser failed")
        return []
