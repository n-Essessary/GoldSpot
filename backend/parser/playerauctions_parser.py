"""
playerauctions_parser.py — PlayerAuctions (PA) HTML adapter for WoW gold offers.

Strategy:
  All offer data for a PA listing page is embedded as a JS variable
  `var offersModel = [...]` inside a <script> tag. We:

    1. Fetch the listing page via curl_cffi with Chrome 120 TLS impersonation.
       PA is fronted by Cloudflare and blocks Railway / general datacenter IPs
       on plain httpx (HTTP 403 + JS challenge). curl_cffi spoofs the JA3/JA4
       fingerprint of a real Chrome browser and passes the IP-reputation gate.
    2. Extract the offersModel array (regex + JSON.loads after key-quoting).
    3. Extract `pricePerUnitTail` to detect per-unit vs per-1k pricing.
    4. Walk the DOM via BeautifulSoup to map each offer_id → (server, faction).
    5. Emit canonical Offer objects.

Two cycles:

  • Classic (`/wow-classic-gold/`)  — per-version Serverid filters; covers
    Anniversary, Season of Discovery, Classic Era, Hardcore. Plus MoP at
    `/wow-expansion-classic-gold/` (no Serverid; US+EU+OC mixed).

  • Retail (`/wow-gold/`)  — region pages only (US=11353, EU=11354). Group
    by (server_name, faction) and keep the cheapest per group; the per-server
    Serverid dropdown is JS-only.

References:
  docs/research_playerauctions.md  — full investigation report.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from api.schemas import Offer

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

SOURCE = "playerauctions"
BASE_URL = "https://www.playerauctions.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.playerauctions.com/",
}

_PROXY_URL = os.environ.get("PA_PROXY_URL")

# Pagination + concurrency config — see _registry § PA.
PA_CLASSIC_INTERVAL = 1800
PA_RETAIL_INTERVAL = 10800
PA_SEMAPHORE = 10
PA_MAX_PAGES_CLASSIC = 20
PA_MAX_PAGES_RETAIL = 110

# Stop iterating pages once we see fewer than this many offers — last page.
_PAGE_FULL_THRESHOLD = 30
_TIMEOUT = 20.0


@dataclass(frozen=True)
class ClassicVersionConfig:
    serverid: int
    version: str          # PA-side label; canonicalized in _normalize_pa_offer
    region: str           # "US" | "EU"


# (serverid, version, region). Versions with 0 offers per the research report
# are skipped (AU Anniversary, OC Classic Era, OC Hardcore, CN Titan).
CLASSIC_VERSION_CONFIGS: list[ClassicVersionConfig] = [
    ClassicVersionConfig(14149, "Anniversary",         "US"),
    ClassicVersionConfig(14156, "Anniversary",         "EU"),
    ClassicVersionConfig(13551, "Season of Discovery", "US"),
    ClassicVersionConfig(13553, "Season of Discovery", "EU"),
    ClassicVersionConfig(8582,  "Classic Era",         "US"),
    ClassicVersionConfig(8583,  "Classic Era",         "EU"),
    ClassicVersionConfig(13457, "Hardcore",            "US"),
    ClassicVersionConfig(13462, "Hardcore",            "EU"),
]

# MoP page has no Serverid and mixes regions; lv1 supplies the region.
_MOP_PATH = "/wow-expansion-classic-gold/"
_MOP_VERSION = "MoP Classic"

# Retail region page Serverids (region pages, NOT per-server).
_RETAIL_REGION_IDS: dict[str, int] = {"US": 11353, "EU": 11354}
_RETAIL_VERSION = "Retail"


# ── Raw offer container ──────────────────────────────────────────────────────

@dataclass
class _RawPAOffer:
    offer_id: str
    unit_price: float
    raw_price_unit: str   # "per_unit" | "per_1k"
    region: str
    server_name: str
    faction: str
    version: str
    offer_url: str | None


# ── HTML extraction ──────────────────────────────────────────────────────────


def extract_offers_model(html: str) -> list[dict]:
    """Parse the `var offersModel = [...]` JS array out of a PA listing page.

    PA emits raw JS object syntax with unquoted keys, e.g.:
        var offersModel = [
            {currencyPerUnit:100.000,unitPriceListItem:0.00524,id:287490165},
            ...
        ]; // Game MetaNameEN
        var metaServer = ...

    Strategy: locate the array between `var offersModel = ` and `var metaServer`,
    trim everything past the last `]`, then convert unquoted keys to JSON.

    Returns [] when the marker is missing (page didn't include offers — empty
    listing, error page, anti-bot block).
    """
    start_marker = "var offersModel = ["
    end_marker = "var metaServer"

    start = html.find(start_marker)
    if start < 0:
        return []
    start += len("var offersModel = ")

    end = html.find(end_marker, start)
    if end < 0:
        return []

    raw = html[start:end].strip()
    last_bracket = raw.rfind("]")
    if last_bracket < 0:
        return []
    raw = raw[: last_bracket + 1]

    # Unquoted JS keys → quoted JSON keys: {currencyPerUnit:1} → {"currencyPerUnit":1}
    quoted = re.sub(r'([{,\[])\s*([a-zA-Z_]\w*)\s*:', r'\1"\2":', raw)

    try:
        return json.loads(quoted)
    except json.JSONDecodeError as exc:
        logger.warning("PA: offersModel JSON decode failed — %s", exc)
        return []


def extract_price_unit_tail(html: str) -> str:
    """Return the literal value of `pricePerUnitTail` from page JS.

    Examples observed:
        '/ Gold'     — Classic Era / MoP / per_unit
        '/ '         — Anniversary / SoD / Hardcore / per_unit
        '/K Gold'    — Retail / per_1k
    """
    m = re.search(r"var pricePerUnitTail\s*=\s*'([^']*)'", html)
    return m.group(1) if m else "/ Gold"


def parse_lv2(lv2: str) -> tuple[str, str]:
    """Split the lv2 anchor text into (server_name, faction).

    PA usually formats as 'Anathema - Alliance', but at least one realm in the
    feed renders without a leading space ('Arcanite Reaper- Horde'). Try both
    separators, longest first, using `rfind` so a dash inside the realm name
    (e.g. 'Lei Shen') doesn't split on the wrong dash.
    """
    if not lv2:
        return "", ""
    for sep in (" - ", "- "):
        idx = lv2.rfind(sep)
        if idx >= 0:
            return lv2[:idx].strip(), lv2[idx + len(sep):].strip()
    return lv2.strip(), ""


def _region_from_lv1(lv1: str) -> str:
    """Map the first token of lv1 to a 2-letter region code.

    'US Classic Era'                 → 'US'
    'EU Season of Discovery'         → 'EU'
    'US 20th Anniversary Edition'    → 'US'
    'Oceania'                        → 'OC'   (MoP only)
    """
    if not lv1:
        return ""
    first = lv1.strip().split()[0] if lv1.strip() else ""
    return "OC" if first == "Oceania" else first


def parse_page(
    html: str,
    version: str,
    config_region: Optional[str],
) -> list[_RawPAOffer]:
    """Convert one PA listing page into a list of RawPAOffer.

    `version` is the PA-side label from the page config (e.g. 'Anniversary',
    'MoP Classic'); used verbatim so downstream canonicalization can map it.
    `config_region` is forced when known (Classic per-version pages, Retail
    region pages); for MoP the value is None and we read region from lv1.
    """
    offers_data = extract_offers_model(html)
    if not offers_data:
        logger.warning(
            "PA: offersModel not found on page (version=%r region=%r) — skipping",
            version, config_region,
        )
        return []

    price_tail = extract_price_unit_tail(html)
    raw_price_unit = "per_1k" if "K" in price_tail else "per_unit"

    soup = BeautifulSoup(html, "html.parser")
    results: list[_RawPAOffer] = []

    for entry in offers_data:
        try:
            offer_id = entry.get("id")
            unit_price = entry.get("unitPriceListItem")
            if offer_id is None or unit_price is None:
                continue
            try:
                unit_price = float(unit_price)
            except (TypeError, ValueError):
                continue
            if unit_price <= 0:
                continue

            link = soup.find(id=f"odpUrl-{offer_id}")
            if not link:
                continue
            title_div = link.find_parent(class_="offer-title-colum")
            if not title_div:
                continue

            lv1_el = title_div.find(class_="offer-title-lv1")
            lv2_el = title_div.find(class_="offer-title-lv2")
            lv1 = lv1_el.get_text(strip=True) if lv1_el else ""
            lv2 = lv2_el.get_text(strip=True) if lv2_el else ""

            region = config_region or _region_from_lv1(lv1)
            server_name, faction = parse_lv2(lv2)
            if not server_name or not faction:
                continue
            if faction not in ("Alliance", "Horde"):
                continue

            href = link.get("href", "") or ""
            if href.startswith("http"):
                offer_url = href
            elif href.startswith("/"):
                offer_url = f"{BASE_URL}{href}"
            else:
                offer_url = None

            results.append(_RawPAOffer(
                offer_id=str(offer_id),
                unit_price=unit_price,
                raw_price_unit=raw_price_unit,
                region=region,
                server_name=server_name,
                faction=faction,
                version=version,
                offer_url=offer_url,
            ))
        except Exception as exc:
            logger.debug("PA: per-offer parse failure — %s", exc)
            continue

    return results


# ── HTTP fetch ───────────────────────────────────────────────────────────────


async def _fetch_html(
    session: AsyncSession,
    url: str,
    semaphore: asyncio.Semaphore,
) -> str:
    """Fetch one URL with retry/backoff. Returns '' on unrecoverable failure.

    Uses curl_cffi with Chrome 120 TLS impersonation per call (Cloudflare
    fingerprints datacenter `httpx` connections as bots).
    """
    proxies = {"http": _PROXY_URL, "https": _PROXY_URL} if _PROXY_URL else None
    async with semaphore:
        for attempt in range(3):
            try:
                resp = await session.get(url, impersonate="chrome120", proxies=proxies)
                status = resp.status_code
                if status == 429:
                    if attempt < 2:
                        ra = resp.headers.get("Retry-After", "30")
                        try:
                            retry_after = int(ra)
                        except ValueError:
                            retry_after = 30
                        logger.warning(
                            "PA 429 rate limited %s — backing off %ds", url, retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        continue
                if status >= 500:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                if status >= 400:
                    logger.error("PA: HTTP %d fetching %s", status, url)
                    return ""
                return resp.text
            except asyncio.TimeoutError:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.error("PA: timeout fetching %s", url)
                return ""
            except Exception as exc:
                # curl_cffi raises curl_cffi.requests.errors.RequestsError
                # for transport / TLS / timeout failures — treat as transient
                # on the first two attempts, then give up.
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.error("PA: error fetching %s — %s", url, exc, exc_info=False)
                return ""
        return ""


async def _scrape_pages(
    session: AsyncSession,
    semaphore: asyncio.Semaphore,
    url_template: str,
    version: str,
    config_region: Optional[str],
    max_pages: int,
) -> list[_RawPAOffer]:
    """Walk paginated PA listing pages until len(offers) < threshold or cap.

    Pages are fetched sequentially (one page may be empty/last); per-page DOM
    parsing offloads to a thread to avoid blocking the loop.
    """
    all_offers: list[_RawPAOffer] = []
    for page in range(1, max_pages + 1):
        url = url_template.format(p=page)
        try:
            html = await _fetch_html(session, url, semaphore)
        except Exception as exc:
            logger.error("PA: fetch loop error %s — %s", url, exc)
            break
        if not html:
            break
        try:
            page_offers = await asyncio.to_thread(
                parse_page, html, version, config_region,
            )
        except Exception as exc:
            logger.error("PA: parse_page error %s — %s", url, exc, exc_info=True)
            break
        if not page_offers:
            break
        all_offers.extend(page_offers)
        if len(page_offers) < _PAGE_FULL_THRESHOLD:
            break
    return all_offers


# ── Conversion to Offer ──────────────────────────────────────────────────────


def _to_offer(raw: _RawPAOffer, fetched_at: datetime) -> Optional[Offer]:
    """Build an Offer from a RawPAOffer.

    Display server is intentionally left empty — _normalize_pa_offer (Phase 0)
    sets a temporary "(REGION) Version" form, then _apply_canonical (Phase 1)
    overwrites it from the canonical registry.
    """
    if raw.unit_price <= 0:
        return None
    try:
        return Offer(
            id=f"pa_{raw.offer_id}",
            source=SOURCE,
            # `server` must be non-empty (model_validator). We set a unique
            # temporary slug; _apply_canonical / Phase 0 will overwrite it.
            server=f"pa_{raw.offer_id}",
            display_server="",
            server_name=raw.server_name,
            faction=raw.faction,
            game_version=raw.version,
            raw_price=raw.unit_price,
            raw_price_unit=raw.raw_price_unit,
            lot_size=1,
            amount_gold=1000,
            seller=SOURCE,
            offer_url=raw.offer_url,
            updated_at=fetched_at,
            fetched_at=fetched_at,
            # Carry source region forward via raw_title so _build_alias_key
            # can construct "(REGION) Version - ServerName" without depending
            # on display_server (which is empty until Phase 0).
            raw_title=f"({raw.region}) {raw.version} - {raw.server_name} - {raw.faction}",
        )
    except Exception as exc:
        logger.debug("PA: Offer construction failed offer_id=%s — %s", raw.offer_id, exc)
        return None


# ── Public fetchers ──────────────────────────────────────────────────────────


async def fetch_classic_offers(
    client: AsyncSession | None,
    semaphore: asyncio.Semaphore,
) -> list[Offer]:
    """Fetch all Classic + MoP listing pages and emit Offer objects.

    Each version config is scraped concurrently; MoP runs alongside the per-
    version pages. No deduplication across versions — PA offer_ids are unique.

    Note: the `client` parameter is kept for back-compat with the existing
    offers_service loops which still construct an httpx client externally.
    PA needs curl_cffi (Cloudflare blocks Railway datacenter IPs on plain
    httpx), so this function ALWAYS creates its own AsyncSession internally
    and ignores the passed-in `client`.
    """
    fetched_at = datetime.now(timezone.utc)

    async with AsyncSession(
        headers=HEADERS,
        timeout=_TIMEOUT,
        impersonate="chrome120",
    ) as session:

        async def _classic_one(cfg: ClassicVersionConfig) -> list[_RawPAOffer]:
            url = (
                f"{BASE_URL}/wow-classic-gold/?Serverid={cfg.serverid}&PageIndex={{p}}"
            )
            return await _scrape_pages(
                session, semaphore, url,
                version=cfg.version,
                config_region=cfg.region,
                max_pages=PA_MAX_PAGES_CLASSIC,
            )

        async def _mop() -> list[_RawPAOffer]:
            url = f"{BASE_URL}{_MOP_PATH}?PageIndex={{p}}"
            return await _scrape_pages(
                session, semaphore, url,
                version=_MOP_VERSION,
                config_region=None,   # region from lv1
                max_pages=PA_MAX_PAGES_CLASSIC,
            )

        tasks: list = [_classic_one(c) for c in CLASSIC_VERSION_CONFIGS]
        tasks.append(_mop())

        results = await asyncio.gather(*tasks, return_exceptions=True)
    raw_offers: list[_RawPAOffer] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            label = (
                f"{CLASSIC_VERSION_CONFIGS[i].region} "
                f"{CLASSIC_VERSION_CONFIGS[i].version}"
                if i < len(CLASSIC_VERSION_CONFIGS)
                else "MoP"
            )
            logger.warning("PA classic task exception (%s) — %s", label, r)
            continue
        raw_offers.extend(r)

    offers = [o for o in (_to_offer(r, fetched_at) for r in raw_offers) if o is not None]
    logger.info(
        "PA classic: %d offers across %d configs",
        len(offers),
        len(CLASSIC_VERSION_CONFIGS) + 1,
    )
    return offers


async def fetch_retail_offers(
    client: AsyncSession | None,
    semaphore: asyncio.Semaphore,
) -> list[Offer]:
    """Fetch Retail US + EU region pages, group by (server, faction), keep min.

    Per-server Retail pages are JS-only on PA, so we scrape the region pages
    (which mix all servers) and reduce to the cheapest unit price per
    (server_name, faction) bucket. This produces one Offer per realm-faction.

    Note: the `client` parameter is kept for back-compat with the existing
    offers_service loops (see fetch_classic_offers docstring); we always
    create our own curl_cffi AsyncSession internally and ignore it.
    """
    fetched_at = datetime.now(timezone.utc)

    async with AsyncSession(
        headers=HEADERS,
        timeout=_TIMEOUT,
        impersonate="chrome120",
    ) as session:

        async def _retail_one(region: str, region_id: int) -> list[_RawPAOffer]:
            url = f"{BASE_URL}/wow-gold/?Serverid={region_id}&PageIndex={{p}}"
            return await _scrape_pages(
                session, semaphore, url,
                version=_RETAIL_VERSION,
                config_region=region,
                max_pages=PA_MAX_PAGES_RETAIL,
            )

        tasks = [_retail_one(region, rid) for region, rid in _RETAIL_REGION_IDS.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    raw_offers: list[_RawPAOffer] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            region = list(_RETAIL_REGION_IDS.keys())[i]
            logger.warning("PA retail task exception (%s) — %s", region, r)
            continue
        raw_offers.extend(r)

    # Group by (region, server_name, faction) and keep cheapest per group.
    cheapest: dict[tuple[str, str, str], _RawPAOffer] = {}
    for raw in raw_offers:
        key = (raw.region, raw.server_name, raw.faction)
        cur = cheapest.get(key)
        if cur is None or raw.unit_price < cur.unit_price:
            cheapest[key] = raw

    offers = [
        o for o in (_to_offer(r, fetched_at) for r in cheapest.values())
        if o is not None
    ]
    logger.info(
        "PA retail: %d unique offers (from %d raw rows across US+EU)",
        len(offers), len(raw_offers),
    )
    return offers


async def fetch_offers() -> list[Offer]:
    """Combined Classic + Retail fetch — public adapter entry point.

    Returns flat list of Offer. Never raises: returns [] on unrecoverable
    failure so the offers_service cache resilience guard preserves prior data.

    Each child fetcher manages its own curl_cffi AsyncSession (Cloudflare
    impersonation), so this entry point just orchestrates them concurrently.
    """
    semaphore = asyncio.Semaphore(PA_SEMAPHORE)
    try:
        classic_task = fetch_classic_offers(None, semaphore)
        retail_task = fetch_retail_offers(None, semaphore)
        classic_offers, retail_offers = await asyncio.gather(
            classic_task, retail_task, return_exceptions=False,
        )
        all_offers = list(classic_offers) + list(retail_offers)
        logger.info(
            "PA fetch_offers: %d total (classic=%d retail=%d)",
            len(all_offers), len(classic_offers), len(retail_offers),
        )
        return all_offers
    except Exception:
        logger.exception("PA fetch_offers failed")
        return []
