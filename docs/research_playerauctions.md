# PlayerAuctions — Research Report
> Generated from live browser investigation. All data verified against real PA pages.

---

## 1. Anti-bot

Only Cloudflare Analytics beacon (no WAF, no Turnstile, no JS challenge).
No session cookies required. Works with:

```python
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.playerauctions.com/",
}
```

Direct `aiohttp` fetch (no headless browser) confirmed working.

---

## 2. Data Location — SSR

All offer data is embedded in raw HTML as a JavaScript variable inside a `<script>` tag:

```html
<script>
    var offersModel = [
       {currencyPerUnit:100.000,volumDiscountListItem:true,discountListItem:[...],unitPriceListItem:0.00524,pricePerUnit:0.524,minValue:40,maxValue:3000,id:287490165},
       ...
    ];
    var metaServer = ...
</script>
```

**offersModel fields:**
| Field | Type | Notes |
|---|---|---|
| `id` | int | offer ID, used to find DOM element |
| `unitPriceListItem` | float | raw price — see pricing model below |
| `currencyPerUnit` | float | lot size (irrelevant for price — unitPriceListItem already normalized) |
| `pricePerUnit` | float | = unitPriceListItem × currencyPerUnit (not needed) |
| `minValue` / `maxValue` | int | min/max purchase quantity |
| `volumDiscountListItem` | bool | has volume discount |
| `discountListItem` | list | volume discount tiers (ignore) |

**No online-status field.** PA does not expose seller online status in offersModel or DOM.
All listed offers are considered "active" — PA manages listing expiry internally.

**No timestamps.** No `updatedAt` or similar in offersModel. Use `fetched_at = datetime.utcnow()`.

---

## 3. Pricing Model

Detection variable in same `<script>` block:
```html
var pricePerUnitTail = '/ Gold';   <!-- or '/K Gold' for Retail, '/ ' for SoD/HC/Anniversary -->
```

| Version | `pricePerUnitTail` | `unitPriceListItem` is | `raw_price_unit` | `price_per_1k` |
|---|---|---|---|---|
| Classic Era | `/ Gold` | price per 1 gold | `per_unit` | `× 1000` |
| Anniversary | `/ ` | price per 1 gold | `per_unit` | `× 1000` |
| Season of Discovery | `/ ` | price per 1 gold | `per_unit` | `× 1000` |
| Hardcore | `/ ` | price per 1 gold | `per_unit` | `× 1000` |
| MoP Classic | `/ Gold` | price per 1 gold | `per_unit` | `× 1000` |
| **Retail** | **`/K Gold`** | **price per 1K gold** | **`per_1k`** | **as-is** |

Detection rule:
```python
def get_raw_price_unit(price_per_unit_tail: str) -> str:
    return "per_1k" if "K" in price_per_unit_tail else "per_unit"
```

Verified examples:
- Classic Era: `unitPriceListItem=0.00524` → `$1 = 190.84 Gold` → `price_per_1k = $5.24` ✓
- MoP: `unitPriceListItem=0.000229` → `$1 = 4366.8 Gold` → `price_per_1k = $0.229` ✓
- Retail: `unitPriceListItem=0.0537` → `$0.054 / K Gold` → `price_per_1k = $0.054` ✓
- HC: `unitPriceListItem=0.065` → `price_per_1k = $65` ✓

---

## 4. Server + Faction Extraction

Each offer has a DOM element `id="odpUrl-{offer_id}"`. From there, walk up to `.offer-title-colum`:

```
.offer-title-colum
  ├── .offer-title-lv1 > <a>US Classic Era</a>   ← region+version string
  └── .offer-title-lv2 > <a href="...?Serverid=8920">Anathema - Alliance</a>   ← server - faction
```

**lv1 → region:**
```python
region = lv1_text.split()[0]
# "US Classic Era" → "US"
# "EU Season of Discovery" → "EU"
# "US 20th Anniversary Edition" → "US"
# "US" → "US"          (MoP, Retail)
# "EU" → "EU"          (MoP, Retail)
# "Oceania" → "OC"     (MoP OCE — special case!)
```

**lv2 → server + faction (handles "Arcanite Reaper- Horde" spacing bug):**
```python
def parse_lv2(lv2: str) -> tuple[str, str]:
    for sep in [" - ", "- "]:
        idx = lv2.rfind(sep)
        if idx >= 0:
            return lv2[:idx].strip(), lv2[idx + len(sep):].strip()
    return lv2, ""
```

**lv2 href contains per-server Serverid** (bonus — not required but useful):
```
href="/wow-classic-gold/?Serverid=8920"  → individual server PA ID
```

**Version is determined by config, not lv1.** lv1 only provides region.

---

## 5. offersModel Extraction (Python)

```python
import re, json

def extract_offers_model(html: str) -> list[dict]:
    start_marker = "var offersModel = ["
    end_marker = "var metaServer"
    
    start = html.find(start_marker)
    if start < 0:
        return []
    start += len("var offersModel = ")
    
    meta_pos = html.find(end_marker, start)
    if meta_pos < 0:
        return []
    
    raw = html[start:meta_pos].strip()
    raw = raw[:raw.rfind("]") + 1]   # strip trailing "; // Game MetaNameEN"
    
    # Convert JS object syntax to JSON (unquoted keys → quoted)
    quoted = re.sub(r'([{,\[])\s*([a-zA-Z_]\w*)\s*:', r'\1"\2":', raw)
    
    return json.loads(quoted)


def extract_price_unit_tail(html: str) -> str:
    m = re.search(r"var pricePerUnitTail\s*=\s*'([^']*)'", html)
    return m.group(1) if m else "/ Gold"  # default: per_unit
```

---

## 6. DOM Parsing (BeautifulSoup)

```python
from bs4 import BeautifulSoup

def parse_page(html: str) -> list[RawOffer]:
    soup = BeautifulSoup(html, "lxml")
    price_tail = extract_price_unit_tail(html)
    raw_price_unit = "per_1k" if "K" in price_tail else "per_unit"
    offers_data = extract_offers_model(html)
    
    results = []
    for offer in offers_data:
        offer_id = offer["id"]
        unit_price = offer["unitPriceListItem"]
        
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
        
        region_raw = lv1.split()[0] if lv1 else ""
        region = "OC" if region_raw == "Oceania" else region_raw
        server_name, faction = parse_lv2(lv2)
        
        if not server_name or not faction:
            continue
        
        # Offer URL
        href = link.get("href", "")
        offer_url = f"https://www.playerauctions.com/wow-gold/{href}" if href else None
        
        results.append(RawOffer(
            offer_id=str(offer_id),
            unit_price=unit_price,
            raw_price_unit=raw_price_unit,
            region=region,
            server_name=server_name,
            faction=faction,
            offer_url=offer_url,
        ))
    return results
```

---

## 7. URL Structure & Page Configs

### Classic-category pages (`/wow-classic-gold/`)
```
?Serverid={version_group_id}&PageIndex={n}
```

Version group Serverids (first dropdown = `filter-servers`):
```python
CLASSIC_VERSION_CONFIGS = [
    # (serverid, version_str, region)
    (14149, "Anniversary", "US"),
    (14156, "Anniversary", "EU"),
    # AU Anniversary: 0 offers — skip
    (13551, "Season of Discovery", "US"),
    (13553, "Season of Discovery", "EU"),
    (8582,  "Classic Era", "US"),
    (8583,  "Classic Era", "EU"),
    # OC Classic Era: 0 offers — skip
    (13457, "Hardcore", "US"),
    (13462, "Hardcore", "EU"),
    # OC Hardcore: 0 offers — skip
    # CN Titan: 0 offers — skip
]
```

### MoP Classic (`/wow-expansion-classic-gold/`)
```
?PageIndex={n}   # no Serverid — single page for all regions
```
Contains US + EU + Oceania offers. Region from lv1.

### Retail (`/wow-gold/`)
```
?Serverid={region_id}&PageIndex={n}
```
Region Serverids: `US=11353`, `EU=11354`

---

## 8. Offer Counts & Max Pages

| Version | URL Serverid | Total Offers | Pages |
|---|---|---|---|
| US Anniversary | 14149 | 77 | 3 |
| EU Anniversary | 14156 | 44 | 2 |
| US SoD | 13551 | 6 | 1 |
| EU SoD | 13553 | 4 | 1 |
| US Classic Era | 8582 | 148 | 5 |
| EU Classic Era | 8583 | 42 | 2 |
| US Hardcore | 13457 | 16 | 1 |
| EU Hardcore | 13462 | 1 | 1 |
| MoP (all regions) | — | 84 | 3 |
| Retail US | 11353 | 2974 | 100 |
| Retail EU | 11354 | 2147 | 72 |

**Pagination stop condition:** `len(offersModel) < 30` = last page.

---

## 9. Scraping Strategy

### Classic / SoD / HC / Anniversary / MoP
Scrape all pages per version config. Simple loop:
```python
async def scrape_all_pages(session, url_template: str, max_pages: int = 20) -> list[dict]:
    all_offers = []
    for page in range(1, max_pages + 1):
        html = await fetch(session, url_template.format(p=page))
        page_offers = parse_page(html)
        all_offers.extend(page_offers)
        if len(page_offers) < 30:
            break
    return all_offers
```

### Retail
**Region-pages strategy** (not per-server): scrape `?Serverid=11353&PageIndex=1..N` for US,
`?Serverid=11354&PageIndex=1..N` for EU. Then `group by (server_name, faction)` and take
`min(unitPriceListItem)` per group. This gives cheapest offer per server×faction.

Total: ~172 pages (100 US + 72 EU). With `Semaphore(10)` and ~0.5s/request → ~9 seconds.

**Do NOT use per-server Serverids for Retail** — the dropdown map is JS-only and not accessible
from server-side fetch. Region-pages are simpler and cover all servers automatically.

---

## 10. MoP Server List (complete, from 3 pages)

| Region | Server | PA Serverid |
|---|---|---|
| US | Pagle - Alliance | 13101 |
| US | Pagle - Horde | 13102 |
| US | Benediction - Alliance | 13082 |
| US | Faerlina - Alliance | 13088 |
| US | Faerlina - Horde | 13089 |
| US | Grobbulus - Alliance | 13091 |
| US | Grobbulus - Horde | 13092 |
| US | Mankrik - Horde | 13096 |
| US | Whitemane - Horde | 13112 |
| US | Galakras - Alliance | 14615 |
| US | Galakras - Horde | 14616 |
| US | Immerseus - Alliance | 14617 |
| US | Immerseus - Horde | 14618 |
| US | Lei Shen - Alliance | 14619 |
| US | Lei Shen - Horde | 14620 |
| US | Nazgrim - Alliance | 14621 |
| US | Nazgrim - Horde | 14622 |
| US | Ra-den - Alliance | 14623 |
| US | Ra-den - Horde | 14624 |
| EU | Gehennas - Horde | 13131 |
| EU | Venoxis - Horde | 13157 |
| EU | Firemaw - Alliance | 13128 |
| EU | Golemagg - Horde | 13133 |
| EU | Everlook - Alliance | 13126 |
| EU | Everlook - Horde | 13127 |
| EU | Auberdine - Alliance | 13122 |
| EU | Auberdine - Horde | 13123 |
| EU | Lakeshire - Alliance | 13136 |
| EU | Lakeshire - Horde | 13137 |
| EU | Mandokir - Horde | 13139 |
| EU | Mirage Raceway - Alliance | 13140 |
| EU | Mirage Raceway - Horde | 13141 |
| EU | Pyrewood Village - Alliance | 13148 |
| EU | Sulfuron - Horde | 13153 |
| EU | Garalon - Alliance | 14625 |
| EU | Garalon - Horde | 14626 |
| EU | Hoptallus - Alliance | 14627 |
| EU | Hoptallus - Horde | 14628 |
| EU | Norushen - Alliance | 14629 |
| EU | Norushen - Horde | 14630 |
| EU | Ook Ook - Alliance | 14631 |
| EU | Ook Ook - Horde | 14632 |
| EU | Shek'zeer - Alliance | 14633 |
| EU | Shek'zeer - Horde | 14634 |
| OC | Arugal - Horde | 13160 |

---

## 11. Version → GoldSpot canonical mapping

| PA lv1 / config | GoldSpot `version` | GoldSpot `game` |
|---|---|---|
| "Classic Era" | `"Classic Era"` | `"wow_classic"` |
| "Anniversary" | `"Anniversary"` | `"wow_classic"` |
| "Season of Discovery" | `"Season of Discovery"` | `"wow_classic"` |
| "Hardcore" | `"Hardcore"` | `"wow_classic"` (realm_type=Hardcore) |
| "MoP Classic" | `"MoP Classic"` | `"wow_classic"` |
| "Retail" | `"Retail"` | `"wow_retail"` |

**Hardcore**: `version="Classic Era"`, `realm_type="Hardcore"` — follow existing convention.
Check `version_utils.py` for exact canonicalization.

---

## 12. Cycle Config (recommended)

```python
# Classic-category + MoP: one cycle, all versions together
# Retail: separate cycle (heavier)

PA_CLASSIC_INTERVAL = 120   # seconds between cycles
PA_RETAIL_INTERVAL  = 180   # seconds between cycles (172 pages)
PA_SEMAPHORE        = 10    # concurrent requests
PA_MAX_PAGES_CLASSIC = 20   # safety cap per version
PA_MAX_PAGES_RETAIL  = 110  # safety cap per region
```

---

## 13. Known Edge Cases

1. **"Arcanite Reaper- Horde"** — no space before dash in PA data. Use `rfind` with both `" - "` and `"- "` separators.
2. **`lv1 = "Oceania"`** — MoP only. Map to `region = "OC"`.
3. **`lv1 = "US 20th Anniversary Edition"`** — Anniversary. Region = first word = `"US"`.
4. **`pricePerUnitTail = "/ "`** — SoD / HC / Anniversary. Treated as `per_unit` (no "K").
5. **OC Classic Era / OC Hardcore / AU Anniversary / CN Titan** — 0 offers. Skip these Serverids.
6. **Retail region pages have 100 pages** — do not cap too low. Use `len(offersModel) < 30` stop condition, not fixed page count.
7. **offersModel ends with `];\n    // Game MetaNameEN`** — use `raw.rfind("]")` to trim, not fixed sentinel string.

---

## 14. Seller Name

Seller usernames are in DOM (`.username` class in row), but PA doesn't expose them in `offersModel`.
For the `seller` field in `Offer`, use `"playerauctions"` as a static placeholder (same as how G2G uses sort labels).
Alternatively extract from DOM: `div.username.hide` → `textContent`.
