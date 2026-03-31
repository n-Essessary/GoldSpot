import asyncio
import json
import re

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.g2g.com/",
}


def collect_candidate_ids(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if lk == "service_id":
                out["service_id"].add(str(v))
            elif lk == "brand_id":
                out["brand_id"].add(str(v))
            elif lk == "relation_id":
                out["relation_id"].add(str(v))
            collect_candidate_ids(v, out)
    elif isinstance(obj, list):
        for item in obj:
            collect_candidate_ids(item, out)


def print_json_preview(label, data, limit=600):
    s = json.dumps(data, ensure_ascii=False, default=str)
    print(f"{label}: {s[:limit]}")


async def discover_seo_terms_from_webpages():
    """
    Пробуем вытащить seo_term/slug из публичных страниц G2G.
    """
    urls = [
        "https://www.g2g.com/wow-classic-gold",
        "https://www.g2g.com/World-of-Warcraft-Classic-gold",
        "https://www.g2g.com/categories/world-of-warcraft",
        "https://www.g2g.com/categories/wow",
        "https://www.g2g.com/offer/search?fa=lgc_1_27816&currency=USD",
    ]
    terms = set()

    async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as c:
        for url in urls:
            try:
                r = await c.get(url)
                html = r.text
                final_url = str(r.url)
                print(f"webpage status: {r.status_code} | {final_url}")

                # 1) slug из final URL
                m = re.search(r"/categories/([^/?#]+)", final_url)
                if m:
                    terms.add(m.group(1))
                m2 = re.search(r"/([^/?#]+-gold)(?:[/?#]|$)", final_url)
                if m2:
                    terms.add(m2.group(1))

                # 2) seo_term в html/json кусках
                patterns = [
                    r'"seo_term"\s*:\s*"([^"]+)"',
                    r'"seoTerm"\s*:\s*"([^"]+)"',
                    r'seo_term[="\s:]+["\']([^"\']+)["\']',
                    r'"slug"\s*:\s*"([^"]+)"',
                ]
                for p in patterns:
                    for hit in re.findall(p, html, re.IGNORECASE):
                        if isinstance(hit, str) and 2 <= len(hit) <= 80:
                            terms.add(hit.strip())
            except Exception as e:
                print(f"webpage error: {url} -> {e}")

    # Сфокусируемся на terms, где есть wow/warcraft/classic/gold
    filtered = sorted(
        {
            t
            for t in terms
            if any(k in t.lower() for k in ["wow", "warcraft", "classic", "gold"])
        }
    )
    if not filtered:
        filtered = sorted(terms)
    return filtered[:30]


async def sls_search_with_q(q="wow classic gold", country="SG", currency="USD"):
    async with httpx.AsyncClient(
        headers={**HEADERS, "Accept": "application/json"},
        timeout=30,
        follow_redirects=True,
    ) as c:
        params = {
            "q": q,
            "country": country,
            "currency": currency,
            "sort": "lowest_price",
            "page_size": "48",
        }
        r = await c.get("https://sls.g2g.com/offer/search", params=params)
        print(f"sls /offer/search status: {r.status_code}")
        print(f"sls /offer/search body: {r.text[:800]}")

        if not r.is_success:
            return None, {"service_id": set(), "brand_id": set(), "relation_id": set()}

        try:
            data = r.json()
        except Exception:
            return None, {"service_id": set(), "brand_id": set(), "relation_id": set()}

        ids = {"service_id": set(), "brand_id": set(), "relation_id": set()}
        collect_candidate_ids(data, ids)
        return data, ids


async def discover_working_query(countries, queries, currency="USD"):
    """
    Перебираем страны и запросы, пока не получим успешный JSON с candidate IDs.
    Возвращаем первую рабочую комбинацию.
    """
    for country in countries:
        for q in queries:
            print(f"\nTrying country={country} q={q!r}")
            data, ids = await sls_search_with_q(q=q, country=country, currency=currency)
            has_ids = bool(ids["service_id"] or ids["brand_id"] or ids["relation_id"])
            if data is not None:
                print_json_preview("search json preview", data, limit=300)
            if data is not None and has_ids:
                print("✅ Found working combo with candidate IDs")
                return country, q, data, ids
            await asyncio.sleep(0.15)
    return None, None, None, {"service_id": set(), "brand_id": set(), "relation_id": set()}


async def sls_search_with_ids(service_id, brand_id, country="SG", currency="USD"):
    async with httpx.AsyncClient(
        headers={**HEADERS, "Accept": "application/json"},
        timeout=30,
        follow_redirects=True,
    ) as c:
        params = {
            "service_id": service_id,
            "brand_id": brand_id,
            "country": country,
            "currency": currency,
            "sort": "lowest_price",
            "page_size": "48",
        }
        r = await c.get("https://sls.g2g.com/offer/search", params=params)
        print(f"sls id-search status: {r.status_code} | service_id={service_id} brand_id={brand_id}")
        print(f"sls id-search body: {r.text[:600]}")
        if r.is_success:
            try:
                return r.json()
            except Exception:
                return None
        return None


async def sls_keyword_relation(relation_id=None, service_id=None, brand_id=None, country="SG"):
    async with httpx.AsyncClient(
        headers={**HEADERS, "Accept": "application/json"},
        timeout=30,
        follow_redirects=True,
    ) as c:
        params = {"country": country, "include_showcase": "1"}
        if relation_id is not None:
            params["relation_id"] = relation_id
        if service_id is not None:
            params["service_id"] = service_id
        if brand_id is not None:
            params["brand_id"] = brand_id

        r = await c.get("https://sls.g2g.com/offer/keyword_relation/collection", params=params)
        print(f"keyword_relation status: {r.status_code} | params={params}")
        print(f"keyword_relation body: {r.text[:600]}")
        if r.is_success:
            try:
                return r.json()
            except Exception:
                return None
        return None


async def sls_keyword_relation_with_seo(seo_term, country="SG"):
    async with httpx.AsyncClient(
        headers={**HEADERS, "Accept": "application/json"},
        timeout=30,
        follow_redirects=True,
    ) as c:
        params = {"country": country, "include_showcase": "1", "seo_term": seo_term}
        r = await c.get("https://sls.g2g.com/offer/keyword_relation/collection", params=params)
        print(f"keyword_relation seo status: {r.status_code} | seo_term={seo_term!r} country={country}")
        print(f"keyword_relation seo body: {r.text[:500]}")
        if r.is_success:
            try:
                return r.json()
            except Exception:
                return None
        return None


# ── MAIN ────────────────────────────────────────────────────────────────────
async def main():
    countries = ["SG", "US", "GB", "AU", "DE", "CA", "MY", "PH"]
    queries = [
        "wow classic gold",
        "world of warcraft classic gold",
        "wow classic era gold",
        "wow gold",
        "world of warcraft gold",
    ]

    print("=== STEP 1: q-search matrix on SLS ===")
    country, query, data, ids = await discover_working_query(countries, queries)
    print(f"\nChosen country: {country}")
    print(f"Chosen query: {query}")

    print("\nExtracted candidate IDs:")
    print(f"service_id: {sorted(ids['service_id'])[:10]}")
    print(f"brand_id:   {sorted(ids['brand_id'])[:10]}")
    print(f"relation_id:{sorted(ids['relation_id'])[:10]}")

    service_id = next(iter(ids["service_id"]), None)
    brand_id = next(iter(ids["brand_id"]), None)
    relation_id = next(iter(ids["relation_id"]), None)

    print("\n=== STEP 2: id-based search ===")
    if service_id and brand_id:
        id_data = await sls_search_with_ids(service_id=service_id, brand_id=brand_id, country=country or "SG")
        if id_data is not None:
            print_json_preview("id-search json preview", id_data)
    else:
        print("Skip id-search: missing service_id/brand_id from q-search response")

    print("\n=== STEP 3: keyword relation ===")
    if relation_id:
        rel_data = await sls_keyword_relation(relation_id=relation_id, country=country or "SG")
        if rel_data is not None:
            print_json_preview("relation_id json preview", rel_data)
    elif service_id and brand_id:
        rel_data = await sls_keyword_relation(service_id=service_id, brand_id=brand_id, country=country or "SG")
        if rel_data is not None:
            print_json_preview("service_id+brand_id json preview", rel_data)
    else:
        print("Skip keyword_relation: no relation_id and no service_id+brand_id pair")

    print("\n=== STEP 4: seed via webpage seo_term ===")
    discovered_seo_terms = await discover_seo_terms_from_webpages()
    manual_seo_terms = [
        "wow-classic-gold",
        "wow-gold",
        "world-of-warcraft-gold",
        "world-of-warcraft-classic-gold",
        "wow-classic-era-gold",
        "wow-wotlk-gold",
        "wow-cata-gold",
        "wow-retail-gold",
        "warcraft-gold",
    ]
    seo_terms = []
    for t in discovered_seo_terms + manual_seo_terms:
        if t not in seo_terms:
            seo_terms.append(t)
    print(f"Discovered seo_term candidates: {discovered_seo_terms}")
    print(f"Total seo_term candidates to test: {seo_terms}")

    seo_countries = ["SG", "US", "GB", "AU", "DE", "CA", "MY", "PH", "ID", "KR", "JP", "TW", "HK"]
    found_non_empty = False
    found_any_200 = False

    for seo in seo_terms:
        for country_try in seo_countries:
            seo_data = await sls_keyword_relation_with_seo(seo_term=seo, country=country_try)
            if seo_data is None:
                await asyncio.sleep(0.12)
                continue

            found_any_200 = True
            print_json_preview("seo_term keyword_relation json preview", seo_data, limit=350)

            payload = seo_data.get("payload") if isinstance(seo_data, dict) else None
            results = payload.get("results") if isinstance(payload, dict) else None
            if isinstance(results, list) and len(results) > 0:
                print(f"✅ Non-empty results found for seo_term={seo!r} country={country_try}")
                found_non_empty = True
                break
            await asyncio.sleep(0.12)
        if found_non_empty:
            break

    if not found_non_empty:
        if found_any_200:
            print("Only empty results for all successful seo_term responses")
        else:
            print("No successful seo_term keyword_relation response found")


if __name__ == "__main__":
    asyncio.run(main())
