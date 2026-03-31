import asyncio
import httpx

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.g2g.com/",
    "Origin": "https://www.g2g.com",
}


async def probe():
    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as c:
        # STEP 0: Get cookies from homepage
        print("\n[0] Получаем cookies с главной страницы...")
        r = await c.get("https://www.g2g.com/")
        print(f"    www.g2g.com → {r.status_code}")
        print(f"    Cookies: {dict(c.cookies)}")

        # STEP 1: Try finding real fa via suggest
        print("\n[1] Ищем game ID через suggest API...")
        for keyword in ["WoW Classic", "World of Warcraft Classic", "wow"]:
            for suggest_url in [
                "https://www.g2g.com/api/site/suggest",
                "https://sls.g2g.com/api/site/suggest",
                "https://www.g2g.com/suggest",
            ]:
                try:
                    r = await c.get(suggest_url, params={"keyword": keyword})
                    print(f"    {suggest_url} → {r.status_code}")
                    if r.is_success and r.text.strip():
                        print(f"    Response: {r.text[:300]}")
                except Exception as e:
                    print(f"    {suggest_url} → ERROR: {e}")

        # STEP 2: Try various fa formats
        print("\n[2] Пробуем разные форматы fa для keyword_relation...")
        fa_variants = [
            "lgc_1_27816",
            "lgc_27816",
            "27816",
            "lgc_1_106",  # WoW retail - maybe different ID
        ]
        endpoints = [
            "https://sls.g2g.com/offer/keyword_relation/collection",
            "https://www.g2g.com/offer/keyword_relation/collection",
        ]

        for endpoint in endpoints:
            for fa in fa_variants:
                for params in [
                    {"fa": fa},
                    {"fa": fa, "include_showcase": "1"},
                    {"fa": fa, "keyword": "gold"},
                ]:
                    try:
                        r = await c.get(endpoint, params=params)
                        status = r.status_code
                        preview = r.text[:200] if r.text else "(empty)"
                        print(f"    fa={fa} params={list(params.keys())} → {status}")
                        if status == 200:
                            print(f"    ✅ SUCCESS: {preview}")
                    except Exception as e:
                        print(f"    fa={fa} → ERROR: {e}")
                    await asyncio.sleep(0.1)

        # STEP 3: Try /offer/search directly
        print("\n[3] Пробуем /offer/search без offer_attributes...")
        search_variants = [
            {"fa": "lgc_1_27816"},
            {"fa": "lgc_27816"},
            {"fa": "27816"},
            {"fa": "lgc_1_27816", "currency": "USD"},
            # Without fa - see response
            {"q": "wow classic gold"},
            {"keyword": "wow classic gold"},
        ]

        for params in search_variants:
            for base in ["https://sls.g2g.com", "https://www.g2g.com"]:
                url = f"{base}/offer/search"
                try:
                    r = await c.get(url, params=params)
                    print(f"    {base} params={params} → {r.status_code}")
                    if r.is_success:
                        print(f"    ✅ {r.text[:400]}")
                    elif r.status_code != 400:
                        print(f"    Body: {r.text[:200]}")
                except Exception as e:
                    print(f"    ERROR: {e}")
                await asyncio.sleep(0.1)

        # STEP 4: Check endpoints via sitemap/robots
        print("\n[4] Проверяем meta-эндпоинты...")
        for url in [
            "https://www.g2g.com/robots.txt",
            "https://www.g2g.com/sitemap.xml",
            "https://sls.g2g.com/",
            "https://sls.g2g.com/offer/",
        ]:
            try:
                r = await c.get(url)
                print(f"    {url} → {r.status_code}")
                if r.is_success:
                    print(f"    {r.text[:300]}")
            except Exception as e:
                print(f"    {url} → ERROR: {e}")

        # STEP 5: Fetch WoW Classic pages and scrape fa/js
        print("\n[5] Ищем fa в HTML/JS страницы WoW Classic...")
        wow_urls = [
            "https://www.g2g.com/wow-classic-gold",
            "https://www.g2g.com/categories/wow-classic",
            "https://www.g2g.com/wow-classic/gold",
        ]
        import re

        for url in wow_urls:
            try:
                r = await c.get(url)
                print(f"    {url} → {r.status_code}")
                if r.is_success:
                    # Search for fa in HTML
                    fas = re.findall(r'fa["\\s:=]+["\\\']?(lgc_[\\w]+)["\\\']?', r.text)
                    print(f"    fa найдены: {set(fas)}")
                    # Search for any lgc identifiers
                    lgc = re.findall(r'lgc_[\\w_]+', r.text)
                    print(f"    lgc_ ids: {set(list(lgc)[:20])}")
            except Exception as e:
                print(f"    ERROR: {e}")


if __name__ == "__main__":
    asyncio.run(probe())
