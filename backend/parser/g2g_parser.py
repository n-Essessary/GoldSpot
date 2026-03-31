"""
g2g_parser.py — Production-ready G2G parser.

Verified pipeline:
  1) /offer/keyword_relation/region  -> region_id + relation_id
  2) /offer/search                   -> offers (username, unit_price_in_usd, qty)
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
    "wow_classic_era": {
        "brand_id": "lgc_game_27816",
        "service_id": "lgc_service_1",
        "label": "WoW Classic Era",
    },
    "wow_mop_classic": {
        "brand_id": "lgc_game_2299",
        "service_id": "lgc_service_1",
        "label": "WoW MoP Classic",
    },
}

_TITLE_RE = re.compile(
    r"^(?P<server>.+?)\s*"
    r"\[(?P<region>[A-Za-z]{2,})\s*-\s*(?P<version>[^\]]+?)\]\s*"
    r"(?:-\s*(?P<faction>Alliance|Horde))?",
    re.IGNORECASE,
)


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
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class G2GRegion:
    region_id: str
    relation_id: str


def _parse_title(title: str) -> tuple[str, str, str, str]:
    """Парсит RAW title G2G оффера → (server_name, region, version, faction).

    "Spineshatter [EU - Anniversary] - Alliance"
      → ("Spineshatter", "EU", "Anniversary", "Alliance")

    Fallback при неудаче (title без скобок или нераспознанный формат):
      → (title, "", "", faction)   # пустые region+version → display_server="Unknown"
    """
    m = _TITLE_RE.match((title or "").strip())
    if not m:
        fallback_faction = "Alliance" if "alliance" in (title or "").lower() else "Horde"
        # Возвращаем пустые region и version — _to_offer выставит display_server="Unknown"
        return (title or "").strip(), "", "", fallback_faction
    server_name = (m.group("server") or "").strip()
    region      = (m.group("region") or "").upper().strip()
    version     = (m.group("version") or "").strip()
    faction     = (m.group("faction") or "").strip().capitalize() or (
        "Alliance" if "alliance" in (title or "").lower() else "Horde"
    )
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

        resp = await self._client.get(
            f"{BASE}/offer/keyword_relation/region",
            params={
                "brand_id": brand_id,
                "service_id": service_id,
                "country": self.country,
            },
        )
        resp.raise_for_status()

        payload = resp.json().get("payload", {})
        regions = [
            G2GRegion(region_id=r["region_id"], relation_id=r["relation_id"])
            for r in payload.get("results", [])
            if r.get("region_id") and r.get("relation_id")
        ]
        self._region_cache[cache_key] = regions
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
        max_pages = 10

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
                },
            )
            resp.raise_for_status()

            results = resp.json().get("payload", {}).get("results", [])

            if not results:
                break

            for o in results:
                try:
                    all_offers.append(
                        G2GOffer(
                            offer_id=o.get("offer_id", ""),
                            title=o.get("title", ""),
                            server_name=o.get("title", ""),
                            region_id=o.get("region_id", ""),
                            relation_id=o.get("relation_id", ""),
                            price_usd=float(o.get("unit_price_in_usd") or 0),
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
                "G2G: relation_id=%s page=%d → %d офферов (всего %d)",
                relation_id, page, len(results), len(all_offers),
            )

            if len(results) < page_size:
                break  # последняя страница

            page += 1
            await asyncio.sleep(0.2)

        return all_offers


async def fetch_g2g_game(
    game_key: str,
    sort: str = "lowest_price",
    country: str = "SG",
    max_regions: Optional[int] = None,
    delay: float = 0.35,
) -> list[G2GOffer]:
    if game_key not in GAME_CONFIG:
        raise ValueError(f"Unknown game: {game_key}. Available: {list(GAME_CONFIG)}")

    cfg = GAME_CONFIG[game_key]
    brand_id = cfg["brand_id"]
    service_id = cfg["service_id"]
    all_offers: list[G2GOffer] = []

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
                all_offers.extend(offers)
            except httpx.HTTPStatusError as e:
                logger.warning("G2G HTTP error for region %s: %s", region.region_id, e)
            if i < len(regions) - 1:
                await asyncio.sleep(delay)

    return all_offers


async def discover_brand_ids(
    category_id: str = "3c2a9034-2569-4484-92ad-c00e384e7085",
) -> list[dict]:
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as c:
        r = await c.get(
            f"{BASE}/offer/category/{category_id}/popular_brand",
            params={"country": "SG"},
        )
        r.raise_for_status()
        return r.json().get("payload", {}).get("results", [])


def _to_offer(raw: G2GOffer, fetched_at: datetime) -> Optional[Offer]:
    if raw.price_usd <= 0 or raw.available_qty <= 0:
        return None

    server_name, region, version, faction = _parse_title(raw.title)
    # Строим display_server в формате FunPay: "(EU) Anniversary"
    # Если парсинг не удался (region или version пусты) → "Unknown"
    display_server = f"({region}) {version}" if region and version else "Unknown"
    offer_url = f"https://www.g2g.com/offer/{raw.offer_id}" if raw.offer_id else None
    seller = raw.seller or "unknown"

    try:
        return Offer(
            id=f"g2g_{raw.offer_id}" if raw.offer_id else f"g2g_{raw.relation_id}",
            source=SOURCE,
            server=display_server,
            display_server=display_server,
            server_name=server_name,
            faction=faction,
            price_per_1k=round(raw.price_usd * 1000.0, 4),
            amount_gold=raw.available_qty,
            seller=seller,
            offer_url=offer_url,
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
    Compatibility entrypoint for offers_service.
    """
    fetched_at = datetime.now(timezone.utc)
    try:
        raw_offers = await fetch_g2g_game("wow_classic_era")
        offers = [o for o in (_to_offer(r, fetched_at) for r in raw_offers) if o is not None]
        return _dedupe(offers)
    except Exception:
        logger.exception("G2G parser failed")
        return []
