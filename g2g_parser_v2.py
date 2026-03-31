import asyncio
import json
import re
from typing import Optional

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

API_HEADERS = {
    **HEADERS,
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.g2g.com/offer/search?fa=lgc_1_27816&currency=USD",
}


async def fetch_nuxt_state(url: str) -> Optional[dict]:
    """Извлекаем window.__NUXT__ из SSR HTML"""
    async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as c:
        r = await c.get(url)
        if not r.is_success:
            return None

        # G2G использует Nuxt.js — данные в window.__NUXT__
        match = re.search(r"window\.__NUXT__\s*=\s*({.+?})\s*;?\s*</script>", r.text, re.DOTALL)
        if not match:
            # Попробуем другой паттерн
            match = re.search(r"__NUXT__=(.+?);window\.__NUXT_LOADED", r.text, re.DOTALL)
        if not match:
            match = re.search(r"<script>window\.__NUXT__=(.+?)</script>", r.text, re.DOTALL)

        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return None


async def fetch_g2g_offers_ssr(fa: str = "lgc_1_27816") -> dict:
    """
    Получаем офферы через SSR страницу /offer/search
    Данные вшиты в HTML как window.__NUXT__
    """
    url = f"https://www.g2g.com/offer/search?fa={fa}&currency=USD&sort=lowest_price"

    async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as c:
        r = await c.get(url)
        html = r.text

        # 1. Пробуем __NUXT__
        for pattern in [
            r"window\.__NUXT__\s*=\s*(\{.+?\})\s*;?\s*</script>",
            r"__NUXT__=(\{[^<]+\})",
            r"<script[^>]*>window\.__NUXT__=(.+?)</script>",
        ]:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(1))
                    print(f"✅ Found __NUXT__ data, keys: {list(data.keys())[:10]}")
                    return data
                except Exception as e:
                    print(f"Parse error: {e}")

        # 2. Ищем JSON-данные офферов напрямую в HTML
        # G2G может инлайнить data как application/json script
        json_scripts = re.findall(r'<script type="application/json"[^>]*>(.+?)</script>', html, re.DOTALL)
        for js in json_scripts:
            try:
                d = json.loads(js)
                if "offer" in str(d).lower() or "price" in str(d).lower():
                    print("✅ Found JSON script data")
                    return d
            except Exception:
                pass

        # 3. Ищем lgc_ идентификаторы — они могут быть в data-атрибутах
        lgc_ids = re.findall(r"lgc_[\w_]+", html)
        prices = re.findall(r'"price":\s*"?([\d.]+)"?', html)
        sellers = re.findall(r'"username":\s*"([^"]+)"', html)

        print(f"lgc_ ids found: {set(lgc_ids)}")
        print(f"prices found: {prices[:5]}")
        print(f"sellers found: {sellers[:5]}")
        print(f"HTML length: {len(html)}")
        print(f"HTML preview: {html[:300]}")

        return {"lgc_ids": list(set(lgc_ids)), "prices": prices, "html_len": len(html)}


# ─── ПРАВИЛЬНЫЙ API FLOW через sls.g2g.com ────────────────────────────────
# Из network trace видно: sls.g2g.com ВООБЩЕ не вызывается на /offer/search
# Значит правильный endpoint для списка офферов — другой!


async def find_real_api_endpoint(fa: str = "lgc_1_27816") -> None:
    """Пробуем все возможные endpoints для получения данных офферов"""

    endpoints_to_try = [
        # Возможные эндпоинты Nuxt API
        f"https://www.g2g.com/api/offer/search?fa={fa}&currency=USD",
        f"https://www.g2g.com/_api/offer/search?fa={fa}",
        f"https://www.g2g.com/api/v1/offer/search?fa={fa}",
        f"https://www.g2g.com/api/v2/offer/search?fa={fa}",
        # sls с другими параметрами
        f"https://sls.g2g.com/offer/search?service={fa}&currency=USD",
        f"https://sls.g2g.com/offer/listing?fa={fa}",
        f"https://sls.g2g.com/v1/offer/search?fa={fa}",
        f"https://sls.g2g.com/offer/list?fa={fa}&currency=USD",
        # Возможно данные через CDN
        f"https://cdn.g2g.com/offer/search?fa={fa}",
    ]

    async with httpx.AsyncClient(headers=API_HEADERS, timeout=15, follow_redirects=True) as c:
        for url in endpoints_to_try:
            try:
                r = await c.get(url)
                content_type = r.headers.get("content-type", "(none)")
                body_preview = re.sub(r"\s+", " ", r.text[:220]).strip()
                print(f"{r.status_code} | {url[:80]}")
                print(f"  content-type: {content_type}")
                print(f"  body preview: {body_preview}")
                if r.is_success and content_type.startswith("application/json"):
                    print("  ✅ JSON endpoint candidate")
            except Exception as e:
                print(f"ERR  | {url[:80]} → {e}")
            await asyncio.sleep(0.2)


if __name__ == "__main__":
    async def main():
        print("\n=== SSR DATA EXTRACTION ===")
        result = await fetch_g2g_offers_ssr()
        print(json.dumps(result, indent=2, default=str)[:1000])

        print("\n=== ENDPOINT DISCOVERY ===")
        await find_real_api_endpoint()

    asyncio.run(main())
