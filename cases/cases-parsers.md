# Case Log — Parsers

Adapter layer: G2G, FunPay, future marketplaces.

---

## [ARCH] G2G: two-phase fetch architecture

**Status:** production, stable
**Files:** `backend/parser/g2g_parser.py`

### Context
Need cheapest price per server×faction across ~150 WoW Classic servers + 514 Retail servers, refreshed every 30s. G2G API does not expose a flat per-offer endpoint with reliable grouping metadata.

### Options considered

**A) Single `/offer/search` with large `page_size`, group client-side**
- Without `group=0` → results are group aggregates. `unit_price_in_usd` is a group-level value, not a real offer price. `available_qty` unreliable.
- With `group=0` → returns per-seller offers, but no grouping metadata to map offer → server×faction.
- Rejected.

**B) One request per server×faction via `filter_attr`**
- Need list of `(offer_group, region_id)` pairs first — not exposed as a list endpoint.
- Cannot construct `filter_attr` without knowing valid `offer_group` values in advance.
- Rejected as standalone, became Phase 2.

**C) Two-phase: discover groups → fetch real prices** ← CHOSEN
- Phase 1: paginated global search → extract `(offer_group, region_id)` pairs
- Phase 2: per-pair request with `group=0` → real cheapest price
- Trade-off: ~300 HTTP calls per refresh. Mitigated with `Semaphore(20)`.

### Invariants (never violate)
- Phase 1 prices are NEVER used — discovery only
- `group=0` mandatory in Phase 2
- `filter_attr` format: `og.lstrip("/")` → strip `_\d+$` → `f"{prefix}:{og}"`
- Both sorts (`lowest_price` + `recommended_v2`) must run — single sort halves coverage

### If building new marketplace adapter — checklist
1. Does it have a flat list endpoint with real per-offer prices? → single-phase possible
2. Grouped-only endpoints? → two-phase required, study how groups map to entities
3. Sort-dependent visibility? → run multiple sorts and dedupe by offer ID
4. Required headers / Origin / CORS quirks? → document before writing code

---

## [BUG] G2G Phase 1 stopped at page 1 → 90% of groups dropped

**Severity:** critical (silent data loss)
**Files:** `backend/parser/g2g_parser.py`

### Symptom
Parser returned ~30 unique server×faction pairs instead of expected 150+. `/offers` covered only ~15% of servers.

### Root cause
Phase 1 did not paginate. Without iterating pages, only the first 48 results were collected.

### Resolution
```python
while True:
    results = await fetch_page(page)
    all_pairs.update((r["offer_group"], r["region_id"]) for r in results)
    if len(results) < page_size or page > 10:
        break
    page += 1
```

### Prevention
- Rule in chat instructions: "Phase 1 must paginate ALL pages (up to 10)"
- Sanity check on prod runs: `len(phase1_pairs) >= 100`

---

## [BUG] G2G Phase 1 prices used as real prices

**Severity:** critical (wrong displayed prices)

### Symptom
Displayed prices on frontend did not match actual cheapest offers on G2G.

### Root cause
Phase 1 search runs without `group=0` → returns grouped aggregates. `unit_price_in_usd` in those results is a group-level representative value, not a real per-seller offer price.

### Resolution
Phase 1 = discovery only. Extract `(offer_group, region_id)`. Discard everything else. Real prices come exclusively from Phase 2 `results[0].unit_price_in_usd`.

### Prevention
- Rule in chat instructions: "Phase 1 prices are unusable — only `(offer_group, region_id)` discovery"
- Code comment at Phase 1 result handling block

---

## [BUG] G2G `filter_attr` wrong format → 0 results or wrong server

**Severity:** critical (silent — returns empty results without error)

### Symptom
Phase 2 calls return empty `results` arrays for many groups, or prices for the wrong server.

### Root cause
`filter_attr` is the most fragile part of the entire parser. Wrong separator, wrong strip regex, or extra characters → API returns 0 results or unrelated data, no error code.

### Resolution
Exact construction (no deviation):
```python
og = offer_group.lstrip("/")
prefix = re.sub(r"_\d+$", "", og)   # strip trailing _<digits>
fa = f"{prefix}:{og}"
# Example:
# offer_group = "lgc_game_27816_lgc_service_1_573_alliance"
# prefix      = "lgc_game_27816_lgc_service_1_573"
# fa          = "lgc_game_27816_lgc_service_1_573:lgc_game_27816_lgc_service_1_573_alliance"
```

### Prevention
- Rule in chat instructions with exact code snippet
- Any modification to this 3-line block must be reviewed against a live G2G request before deploy

---

## [ARCH] G2G: dual-sort fetching

**Status:** production (added 2026-04-23)
**Files:** `backend/parser/g2g_parser.py`

### Context
Single sort (`lowest_price`) was missing ~40% of offers — some servers/factions only surface under `recommended_v2`.

### Resolution
Run both sorts concurrently:
```python
await asyncio.gather(
    _fetch_sort("lowest_price", client),
    _fetch_sort("recommended_v2", client),
)
```
Each runs its own Phase 1 + Phase 2 independently. Combine + dedupe by `offer_id`.

### Result
Total unique offers: 300–440 per cycle (vs ~180 with single sort).

### Invariants
- Both sorts must run — removing either halves coverage
- Dedup key: `offer_id` (not offer_group, not server)
- `seller` field set to label `"Lowest Price"` / `"Recommended"` (G2G group offers don't expose seller identity at this level)

---

## [ARCH] G2G: WoW Retail support

**Status:** production (added 2026-04-23)
**Files:** `backend/parser/g2g_parser.py`, `backend/db/migrations/021_*.py`

### Context
Adding WoW Retail to existing Classic Era / Anniversary / Seasonal / MoP coverage.

### Key parameters (retail-specific)
- `brand_id=lgc_game_2299` (retail) — separate from Classic `lgc_game_27816`
- `seo_term=wow-gold`
- `_RETAIL_MAX_PAGES=25` (vs default 10 for Classic)
- Two cycles:
  - `lowest_price`: 60s interval, `Semaphore(30)`
  - `recommended_v2`: 180–300s interval, `Semaphore(20)`, startup delay 90s
- Cache key: `g2g_retail` (separate from `g2g`)

### DB
- New `game` column on `servers` table
- Unique constraint: `servers_game_name_region_version_key`
- Region ID ranges allocated: EU 293–540, US 541–774, OCE 775–786, RU 787–806 (514 total servers)
- Aliases migration: 1028 G2G + 505 FunPay = 1533 total

### Invariants
- `brand_id=lgc_game_2299` is RETAIL ONLY. Never use for Classic. Never use for MoP.
- Retail and Classic must have separate cache keys to avoid collision
- `version` column on offers must distinguish "Retail" from Classic versions

---

## [BUG] G2G calls failed without proper headers

**Severity:** high (parser returned empty or wrong data)

### Symptom
Direct calls to `sls.g2g.com/offer/search` from non-browser context returned empty results, blocked, or wrong data.

### Root cause
G2G API requires browser-like headers. Without `Referer` and `Origin` pointing to `g2g.com`, the API behaves differently.

### Resolution
Mandatory headers on every request:
```python
{
    "Accept": "application/json",
    "Referer": "https://www.g2g.com/",
    "Origin": "https://www.g2g.com",
}
```

### Prevention
- Headers set at HTTP client level (not per-request) — impossible to forget
- For Chrome MCP debugging: calls must originate from a tab on `www.g2g.com`, not `sls.g2g.com` (CORS)

---

## [BUG] FunPay returned EUR prices instead of USD

**Severity:** critical (3-5× price inflation in displayed values)

### Symptom
FunPay prices on frontend were systematically higher than actual marketplace listings.

### Root cause
Railway servers are located in EU. FunPay detects client region and serves EUR-denominated prices. Parser was assuming USD universally.

### Resolution
- Detect currency from `.tc-price .unit` DOM element on the FunPay page
- Convert EUR → USD via external FX API
- Fallback chain: `open.er-api.com` (primary) → `jsdelivr CDN` (secondary)
- `frankfurter.app` is unreachable from Railway — do NOT add to chain

### Prevention
- Currency detection is mandatory first step in `_normalize_funpay_offer()`
- Hardcoding currency = automatic regression on any infra move

---

## [BUG] FunPay price 1000× inflated

**Severity:** critical

### Symptom
After parser refactor, FunPay prices appeared 1000× too high.

### Root cause
`raw_price_unit` was set to `per_unit` instead of `per_lot`. FunPay listings are priced per lot (lot of N gold), not per unit. With `lot_size=1000`, treating as `per_unit` skips division by 1000.

### Resolution
```python
raw_price_unit = "per_lot"
lot_size = amount_gold  # parsed from listing
unit_price = raw_price / lot_size
```

### Prevention
- Rule in chat instructions: FunPay is always `per_lot`
- QA assertion: FunPay `unit_price` should be in same order of magnitude as G2G `unit_price`

---

## [BUG] G2G online filter `include_offline=0` had no effect on global search

**Severity:** medium (extra noise in Phase 1, no functional break)

### Root cause
`include_offline=0` only affects per-group queries (Phase 2). Global search (Phase 1) ignores it.

### Resolution
- Phase 1: don't bother with `include_offline=0` (no effect)
- Phase 2: `include_offline=0` mandatory to filter offline sellers

---

## [BUG] FunPay online filter accidentally removed during refactor

**Severity:** medium (offline sellers polluted results)

### Root cause
`data-online="1"` filter on `.tc-item` was removed during a parser cleanup as "unused attribute". It is intentional business logic — only online sellers should appear.

### Resolution
Filter restored. Documented as required.

### Prevention
- Comment in code: `# Intentional: filter offline sellers — required business logic`
- Rule in chat instructions

---

## [BUG] Russian Retail server titles not parsed

**Severity:** medium (RU servers missing from results)

### Symptom
G2G Retail RU servers were dropped during normalization.

### Root cause
G2G titles RU servers in Cyrillic with format `"Кириллица (Name)"`. Parser regex expected Latin server names.

### Resolution
Title conversion in adapter: `"Кириллица (Name)"` → `"Name [RU - Retail] - Faction"` before normalization.

### Prevention
- Per-region title format documented in adapter
- Test fixture with RU sample title in QA suite

---

## [BUG] Server alias not matched: Unicode apostrophe

**Severity:** medium (specific servers always quarantined)

### Symptom
Server `Nek'Rosh` (and similar) consistently failed to resolve to canonical entity.

### Root cause
Source data uses Unicode right single quote `\u2019` (`'`). Alias DB stores ASCII apostrophe `'`. String match fails.

### Resolution
Normalize apostrophes to ASCII before lookup:
```python
name = name.replace("\u2019", "'")
```

### Prevention
- Normalization step in alias resolver
- Add fixture with Unicode-apostrophe server to QA suite

---

## [INFRA] G2G live debugging via Chrome MCP — pattern

**Status:** working pattern, document for reuse

### Why
G2G API behavior cannot be fully replicated outside a browser context (CORS, Referer enforcement, anti-bot heuristics).

### Pattern
1. Open tab on `https://www.g2g.com` (NOT `sls.g2g.com` — CORS will block)
2. Use `javascript_exec` to run `fetch()` from page context
3. Async results: store in `window._varName`, retrieve in separate call after `wait`
4. Required headers passed explicitly:
   ```js
   fetch(url, { headers: { 'Accept': 'application/json', 'Referer': 'https://www.g2g.com/' } })
   ```
5. For real browser request inspection: `read_network_requests` with `urlPattern=sls.g2g.com/offer/search`

### Use cases
- Verifying new query parameters before adding to parser
- Debugging why a specific server×faction returns 0 results
- Comparing parser output vs what user sees on site

---

## [BUG] Retail and MoP Classic prices 1000× inflated

**Severity:** critical (all Retail/MoP prices wrong)
**Files:** `backend/parser/g2g_parser.py`, `backend/parser/funpay_parser.py`, `backend/api/schemas.py`

### Symptom
G2G Retail price_per_1k ≈ $43,000 instead of $43. FunPay Retail ≈ $56,000 instead of $56.
Classic Era prices unaffected.

### Root cause
G2G Retail/MoP: `unit_price_in_usd` is price **per 1K gold** (G2G UI shows "K Gold" as the unit denomination).
FunPay Retail/MoP: listing price is **per 1000 unit** (explicitly stated as "PAYMENT METHOD: FOR 1000 UNIT").
Both parsers applied the Classic-era formula (`* 1000`) on top — producing 1000× inflation.

Classic Era G2G: `unit_price_in_usd` is price per 1 gold → `* 1000` is correct.
Classic Era FunPay: `raw_price / lot_size * 1000` is correct.

### Resolution
Added `raw_price_unit = "per_1k"` as a new valid value in `Offer` schema.
Model validator new branch: `if per_1k: price_per_1k = raw_price` (no multiplication).
G2G parser: when `version in ("Retail", "MoP Classic")` → set `raw_price_unit = "per_1k"`.
FunPay parser: same version check → `raw_price_unit = "per_1k"`, adjust `lot_size` accordingly.

### Prevention
- Rule: before implementing price formula for a new version/game, inspect live offer page to confirm what the "unit" is
- Confirmed via: G2G offer page shows "K Gold" as quantity unit; FunPay shows "FOR 1000 UNIT" label
- QA assertion: Retail `price_per_1k` must be in $0.03–$0.15 range; Classic must be in $0.5–$10 range


## [BUG] FunPay chip 2: RU Retail серверы получали неверный region prefix

**Severity:** high (0 офферов FunPay для всех 20 RU Retail серверов)
**Files:** `backend/parser/funpay_parser.py`

### Symptom
`(RU) Retail` серверы в `/servers` показывали `sources: ["g2g"]` — FunPay отсутствовал.
Логи: `FunPay quarantined 628 offers (reasons: unresolved_server, empty_server_title)`.

### Root cause
Chip 2 (`funpay.com/en/chips/2/`) содержит EU и RU Retail серверы с голыми именами
(`"Ashenvale"`, `"Howling Fjord"`) без региона в HTML. `ChipConfig(2, "EU", "Retail", ...)`
→ код строил `display_server = "(EU) Retail - Ashenvale"` для всех серверов.
RU серверные алиасы в БД — `(EU) Retail - Aegwynn` для EU и голые `Ashenvale` для RU —
но `_normalize_funpay_offer` очищает `display_server` если нет `(REGION)` prefix до того
как resolver успевает смотреть алиасы → quarantine `empty_server_title`.

### What was tried
1. `region="EU+RU"` → `"+" in config.region` → prefix не строился → голые имена →
   `_normalize_funpay_offer` очищал их → те же 628 quarantined + EU Retail потерял FunPay.
   Откат.

### Resolution
Добавлен `_RU_RETAIL_SERVERS: frozenset[str]` со всеми 20 RU Retail именами.
В `_fetch_chip`, в блоке prefix-building:
```python
if (
    config.chip_id == 2
    and config.game_version == "Retail"
    and bare in _RU_RETAIL_SERVERS
):
    region = "RU"
else:
    region = config.region
offer.display_server = f"({region}) {config.game_version} - {bare}"
```
Алиасы `(RU) Retail - Ashenvale` уже были в БД от migration 021 —
дополнительно вставлены голые алиасы (`Ashenvale → server_id=787` и т.д.) которые
не используются но не мешают.

### Prevention
- При добавлении нового RU Retail сервера: добавить имя в `_RU_RETAIL_SERVERS` + алиас в БД
- Chip 2 покрывает EU+RU без региона в HTML — это постоянное ограничение FunPay API
- Не менять `region="EU"` в ChipConfig для chip 2 — EU алиасы в формате `(EU) Retail - Name`

## [ARCH] PlayerAuctions parser — добавление третьего источника

**Status:** production
**Files:** `backend/parser/playerauctions_parser.py`, `backend/service/offers_service.py`,
`backend/utils/version_utils.py`, `backend/api/schemas.py`, `docs/skills/_registry/SKILL.md`

### Context
Добавлен третий маркетплейс PlayerAuctions (PA) — HTML SSR сайт без публичного API.
Реализован после ~4 часов live browser investigation через Chrome MCP.

### Architecture
- **Данные:** SSR HTML, JS-переменная `var offersModel = [...]` в `<script>` теге
- **Parsing:** BeautifulSoup (html.parser) + regex для извлечения offersModel
- **Transport:** `curl_cffi` с `impersonate="chrome120"` — обязательно, Railway IP
  блокируется Cloudflare IP-reputation при использовании обычного httpx/aiohttp
- **Два цикла:** `_run_pa_classic_loop` (120s) + `_run_pa_retail_loop` (180s, 60s startup delay)

### Pricing model
- Classic Era / Anniversary / SoD / Hardcore / MoP: `unitPriceListItem` = per 1 gold
  → `raw_price_unit="per_unit"`, `price_per_1k = unitPriceListItem * 1000`
- Retail: `unitPriceListItem` = per 1K gold → `raw_price_unit="per_1k"`, `price_per_1k = as-is`
- Detection: `"K" in pricePerUnitTail` JS variable на странице

### URL structure
- Classic-category: `/wow-classic-gold/?Serverid={version_group_id}&PageIndex={n}`
- MoP: `/wow-expansion-classic-gold/?PageIndex={n}` (все регионы на одной странице)
- Retail: `/wow-gold/?Serverid={11353|11354}&PageIndex={n}` (region-pages стратегия)
- Pagination stop: `len(offersModel) < 30`

### Version group Serverids
```python
CLASSIC_VERSION_CONFIGS = [
    (14149, "Anniversary", "US"), (14156, "Anniversary", "EU"),
    (13551, "Season of Discovery", "US"), (13553, "Season of Discovery", "EU"),
    (8582, "Classic Era", "US"), (8583, "Classic Era", "EU"),
    (13457, "Hardcore", "US"), (13462, "Hardcore", "EU"),
]
RETAIL_REGION_IDS = {"US": 11353, "EU": 11354}
```

### Key invariants
- `curl_cffi` обязателен — не заменять на httpx/aiohttp для PA
- `impersonate="chrome120"` на каждом `.get()` вызове
- lv2 parsing: `rfind` с двумя сепараторами `" - "` и `"- "` — PA данные имеют баг
  пробела ("Arcanite Reaper- Horde")
- `lv1 = "Oceania"` → `region = "OC"` (MoP OCE — специальный случай)
- Retail: group by (server_name, faction), берём min(unitPriceListItem) — не emit дубли
- OC Classic Era / OC Hardcore / AU Anniversary / CN Titan — 0 офферов, пропускать

### Anti-bot
Cloudflare Analytics only (не WAF). Блокировка по IP-reputation датацентров.
`curl_cffi` с TLS fingerprint Chrome обходит блокировку с Railway IP.

### Live results (первый прогон)
- Total: 1372 офферов (classic=427, retail=945)
- Breakdown: Anniversary=131, SoD=10, Classic Era=190, Hardcore=17, MoP=79, Retail=945
- Price ranges: Classic Era $5.24–$39.99, MoP $0.19–$0.45, Retail $0.04–$0.05/K, HC $57.50–$182

### What was tried
1. `httpx` с Chrome headers → 200 с residential IP, 403 с Railway datacenter IP
2. `cloudscraper` — не рассматривался (устарел, не поддерживает новые CF версии)
3. `curl_cffi` — решение: TLS fingerprint идентичен Chrome, проходит IP-блокировку

### Deviation: schemas.py Literal extended
`Offer.source` расширен с `["funpay","g2g"]` → `["funpay","g2g","playerauctions"]`.
Файл в Do Not Touch списке, но изменение неизбежно — Pydantic отклоняет третье значение.
TODO на schemas.py:61 прямо предусматривал этот случай.