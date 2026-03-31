import asyncio
import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.g2g.com/",
    "Origin": "https://www.g2g.com",
}

# Реальные ID из browser trace (не lgc_1_* — они устарели!)
GAME_CONFIG = {
    "wow_classic_era": {
        "brand_id": "lgc_game_27816",
        "service_id": "lgc_service_1",
        "seo_url": "wow-classic-era-vanilla-gold",
    },
    "wow_mop_classic": {
        "brand_id": "lgc_game_2299",   # из popular_brand response
        "service_id": "lgc_service_1",
        "seo_url": "wow-classic-gold",
    },
}


async def fetch_regions(brand_id: str, service_id: str, country: str = "SG") -> list[dict]:
    """
    GET /offer/keyword_relation/region
    Возвращает список регионов/серверов с relation_id
    """
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as c:
        r = await c.get(
            "https://sls.g2g.com/offer/keyword_relation/region",
            params={
                "brand_id": brand_id,
                "service_id": service_id,
                "country": country,
            },
        )
        print(f"regions status={r.status_code}: {r.text[:300]}")
        if r.is_success:
            return r.json().get("payload", {}).get("results", [])
        return []


async def fetch_offers(
    brand_id: str,
    service_id: str,
    relation_id: str,
    country: str = "SG",
    sort: str = "lowest_price",
    page: int = 1,
) -> dict:
    """
    GET /offer/search
    Возвращает офферы для конкретного сервера
    """
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as c:
        r = await c.get(
            "https://sls.g2g.com/offer/search",
            params={
                "brand_id": brand_id,
                "service_id": service_id,
                "relation_id": relation_id,
                "country": country,
                "currency": "USD",
                "sort": sort,
                "page": page,
                "page_size": 48,
            },
        )
        print(f"offers status={r.status_code}: {r.text[:400]}")
        if r.is_success:
            return r.json().get("payload", {})
        return {}


async def fetch_all_wow_classic():
    cfg = GAME_CONFIG["wow_classic_era"]

    # Step 1: получаем регионы
    regions = await fetch_regions(cfg["brand_id"], cfg["service_id"])
    print(f"\nRegions found: {len(regions)}")
    for reg in regions[:3]:
        print(f"  region_id={reg['region_id']} relation_id={reg['relation_id']}")

    if not regions:
        return

    # Step 2: для каждого региона — офферы
    for region in regions:
        result = await fetch_offers(
            brand_id=cfg["brand_id"],
            service_id=cfg["service_id"],
            relation_id=region["relation_id"],
        )
        offers = result.get("results", [])
        print(f"\nRegion {region['region_id']}: {len(offers)} offers")
        for o in offers[:3]:
            print(f"  price={o.get('unit_price')} seller={o.get('seller')} stock={o.get('available_qty')}")
        await asyncio.sleep(0.4)


asyncio.run(fetch_all_wow_classic())
