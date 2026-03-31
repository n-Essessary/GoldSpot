import asyncio
import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.g2g.com/",
    "Origin": "https://www.g2g.com",
}


async def probe_real_endpoints():
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as c:
        # 1. Keyword search — рабочий endpoint
        print("=== /offer/keyword/search ===")
        for kw in ["wow classic gold", "wow classic", "wow gold"]:
            r = await c.get(
                "https://sls.g2g.com/offer/keyword/search",
                params={"keyword": kw, "country": "SG"},
            )
            print(f"  [{r.status_code}] kw='{kw}': {r.text[:300]}")
            await asyncio.sleep(0.3)

        # 2. Пробуем offer/search с параметрами из keyword response
        print("\n=== /offer/search с country + разными параметрами ===")
        combos = [
            {"q": "wow classic gold", "country": "SG", "currency": "USD"},
            {"q": "wow classic gold", "country": "SG", "currency": "USD", "sort": "lowest_price"},
            # service_id из keyword_relation если найдём
            {"service_id": "lgc_1_27816", "country": "SG", "currency": "USD"},
            {"service_id": "27816", "country": "SG", "currency": "USD"},
            {"brand_id": "lgc_27816", "country": "SG", "currency": "USD"},
        ]
        for params in combos:
            r = await c.get("https://sls.g2g.com/offer/search", params=params)
            print(f"  [{r.status_code}] params={params}: {r.text[:400]}")
            await asyncio.sleep(0.3)

        # 3. keyword_relation с service_id вместо seo_term
        print("\n=== /keyword_relation с service_id ===")
        for sid in ["lgc_1_27816", "27816", "lgc_27816", "1_27816"]:
            r = await c.get(
                "https://sls.g2g.com/offer/keyword_relation/collection",
                params={"service_id": sid, "country": "SG"},
            )
            print(f"  [{r.status_code}] service_id={sid}: {r.text[:300]}")
            await asyncio.sleep(0.2)


asyncio.run(probe_real_endpoints())
