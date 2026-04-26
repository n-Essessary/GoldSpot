---
name: _registry
description: "Single source of truth for all volatile values across GoldSpot — brand IDs, enum values, cycle configs, schema names, env vars. All other skills MUST reference this registry instead of duplicating values. Update here first, propagate via references."
---

# Registry — Volatile Values

> **Authority:** This file is the canonical source for every value listed below. Other skills must reference this file, not duplicate its content. If another skill contradicts this file, this file wins. If this file contradicts the actual code, invoke `conflict-resolution`.
>
> **Last verified:** 2026-04-24

---

## How to use this registry

- **Read order on any task:** check `_registry` first → then domain skill → then code
- **When you change code:** update this file **in the same commit** as the code change
- **When other skills need a constant:** they say `see _registry → <section>` — never inline the value
- **When this file says "see code path":** the code is authoritative; update the path here if it moves

---

## 1. Game Versions (canonical enum)

Used by: parser, normalize-pipeline, server-registry, data-logic, qa-testing.

```
Retail
MoP Classic
Season of Discovery
Anniversary
Classic Era
Classic
```

**Quarantined (must reject, never display):**
```
Season of Mastery
```

**Sort order** (sidebar, charts, listings):
```
Retail → MoP Classic → Season of Discovery → Anniversary → Classic Era → Classic → unknown(99)
```

**Source of truth in code:** `backend/service/version_utils.py`

---

## 2. Version Aliases (raw → canonical)

Used by: normalize-pipeline (`canonicalize_offer`), server-registry (`alias_key` builder).

```
retail, midnight, tww, the war within   → Retail
mop classic, mop                          → MoP Classic
seasonal, sod, season of discovery        → Season of Discovery
anniversary                               → Anniversary
classic era                               → Classic Era
classic                                   → Classic
```

Unknown after aliasing → log WARNING + quarantine `unknown_version:{value}`. Never default.

**Source of truth in code:** `backend/service/version_utils.py::_VERSION_ALIASES`

---

## 3. Realm Type (orthogonal to version)

```
Normal       (default)
Hardcore     (markers: "#hardcore", word "hardcore" in title)
```

Zero overlap with version detection. Never embedded in `display_server`.

**Source of truth in code:** `backend/service/version_utils.py::detect_realm_type`

---

## 4. G2G Configuration

| Game           | brand_id          | seo_term                      | service_id      |
|----------------|-------------------|-------------------------------|-----------------|
| Classic Era    | `lgc_game_27816`  | `wow-classic-era-vanilla-gold`| `lgc_service_1` |
| Anniversary    | `lgc_game_27816`  | `wow-classic-era-vanilla-gold`| `lgc_service_1` |
| SoD / Seasonal | `lgc_game_27816`  | `wow-classic-era-vanilla-gold`| `lgc_service_1` |
| MoP Classic    | _see code_        | _see code_                    | `lgc_service_1` |
| Retail         | `lgc_game_2299`   | `wow-gold`                    | `lgc_service_1` |

**Required headers on every G2G request:**
```
Accept: application/json
Referer: https://www.g2g.com/
Origin: https://www.g2g.com
```

**API host:** `sls.g2g.com` (never `www.g2g.com` for API — returns HTML)

**Standard query params:** `currency=USD&country=SG&v=v2`

**Source of truth in code:** `backend/parser/g2g_parser.py::_GAME_CONFIGS` (or equivalent constant block)

---

## 5. G2G Phase 2 `filter_attr` Construction

```python
og = offer_group.lstrip("/")
prefix = re.sub(r"_\d+$", "", og)
fa = f"{prefix}:{og}"
```

Example:
```
offer_group = "lgc_game_27816_lgc_service_1_573_alliance"
prefix      = "lgc_game_27816_lgc_service_1_573"
fa          = "lgc_game_27816_lgc_service_1_573:lgc_game_27816_lgc_service_1_573_alliance"
```

**Mandatory Phase 2 params:** `group=0`, `include_offline=0`, `page_size=1`

Any deviation in separator, regex, or `group=0` omission → 0 results or wrong prices. **Single most fragile point in the codebase.**

**Source of truth in code:** `backend/parser/g2g_parser.py` Phase 2 section

---

## 6. Parser Cycle Configuration

| Source              | Interval     | Semaphore | Startup Delay | Notes                                            |
|---------------------|--------------|-----------|---------------|--------------------------------------------------|
| funpay              | 50–70s jitter| —         | 0             | Single GET, no concurrency                       |
| g2g_classic         | 30s          | 20        | 0             | Dual-sort via `gather()`                         |
| g2g_retail_lp       | 60s          | 30        | 0             | `lowest_price` only                              |
| g2g_retail_rec      | 180–300s     | 20        | 90s           | `recommended_v2` only                            |
| pa_classic          | 120s         | 10        | 0             | Classic-version configs + MoP                    |
| pa_retail           | 180s         | 10        | 60s           | Region pages (US+EU); group by (server,faction)  |

**Pagination:** Phase 1 paginates until `len(results) < page_size` or `page > max_pages`.
- `g2g_classic` → `max_pages = 10`, `page_size = 48`
- `g2g_retail`   → `max_pages = 25`, `page_size = 48`

**Sort modes used:**
- Classic: dual-sort `lowest_price + recommended_v2` concurrent via `asyncio.gather()`
- Retail: two independent loops with separate intervals

**Source of truth in code:** `backend/parser/g2g_parser.py` (cycle constants), `backend/service/offers_service.py` (loop registration)

---

## 7. FunPay Configuration

```
URL:       https://funpay.com/en/chips/114/
Method:    GET (single request, no pagination)
Filter:    .tc-item with data-online="1"
Pricing:   raw_price_unit = "per_lot", lot_size = amount_gold
```

**Currency conversion fallback chain (Railway EU IP returns EUR):**
1. `open.er-api.com` (primary)
2. `jsdelivr CDN currency API` (fallback)
3. ❌ `frankfurter.app` — **blocked on Railway, never use**

**Source of truth in code:** `backend/parser/funpay_parser.py`

---

## 7.1 PlayerAuctions (PA) Configuration

```
BASE_URL:  https://www.playerauctions.com
Anti-bot:  Cloudflare Analytics beacon only (no WAF) — plain httpx works.
Headers:   User-Agent + Accept (text/html) + Accept-Language + Referer (PA root)
Data:      offersModel JS variable embedded in listing HTML (SSR).
```

**Classic-category page (`/wow-classic-gold/?Serverid={sid}&PageIndex={n}`):**

| serverid | version             | region |
|----------|---------------------|--------|
| 14149    | Anniversary         | US     |
| 14156    | Anniversary         | EU     |
| 13551    | Season of Discovery | US     |
| 13553    | Season of Discovery | EU     |
| 8582     | Classic Era         | US     |
| 8583     | Classic Era         | EU     |
| 13457    | Hardcore            | US     |
| 13462    | Hardcore            | EU     |

Skipped (0 offers): AU Anniversary, OC Classic Era, OC Hardcore, CN Titan.

**MoP page:** `/wow-expansion-classic-gold/?PageIndex={n}` — no Serverid; mixes
US + EU + Oceania. Region comes from lv1 (`Oceania → OC`).

**Retail pages:** `/wow-gold/?Serverid={region_id}&PageIndex={n}`
- US: `11353`
- EU: `11354`

Per-server Retail Serverids are JS-only. Group all region-page rows by
`(server_name, faction)` and keep the cheapest per group.

**Pricing model:**
```
pricePerUnitTail contains "K" → raw_price_unit = "per_1k"  (Retail)
otherwise                       → raw_price_unit = "per_unit"  (Classic/SoD/HC/Anniversary/MoP)
```
Detection: `"K" in pricePerUnitTail`, not equality with `/K Gold`.

**Cycle config:**
```
PA_CLASSIC_INTERVAL = 120s
PA_RETAIL_INTERVAL  = 180s
PA_SEMAPHORE        = 10
PA_MAX_PAGES_CLASSIC = 20  (pagination stop: len(offersModel) < 30)
PA_MAX_PAGES_RETAIL  = 110
```

**Offer mapping:**
```
id = "pa_{offer_id}"
seller = "playerauctions"          (no per-seller identity in offersModel)
amount_gold = 1000                 (PA does not specify per-offer gold qty)
lot_size = 1
display_server = ""                (set by _apply_canonical via Phase 0+1)
raw_title = "(REGION) Version - ServerName - Faction"   (used for alias key)
```

**Source of truth in code:** `backend/parser/playerauctions_parser.py`,
`backend/service/offers_service.py::_normalize_pa_offer / _run_pa_*_loop`.

---

## 8. Database Schema (current)

| Table             | Retention | Purpose                                  |
|-------------------|-----------|------------------------------------------|
| `snapshots_1m`    | 24h       | Live prices, hot path                    |
| `snapshots_5m`    | 30d       | 7-day charts                             |
| `snapshots_1h`    | ~1y       | Long-term trends                         |
| `snapshots_1d`    | ∞         | Historical archive                       |
| `servers`         | —         | Canonical entity registry                |
| `server_aliases`  | —         | Raw title → server_id mapping            |

**`servers` columns:**
```sql
id      SERIAL PRIMARY KEY
game    TEXT NOT NULL              -- 'wow_classic' | 'wow_retail' | future games
name    TEXT NOT NULL              -- realm name
region  TEXT NOT NULL              -- 'EU' | 'US' | 'OCE' | 'KR' | 'TW' | 'RU'
version TEXT NOT NULL              -- see Section 1
faction TEXT DEFAULT 'All'
UNIQUE (game, name, region, version)
```

**`server_aliases` columns:**
```sql
id         SERIAL PRIMARY KEY
alias_key  TEXT NOT NULL UNIQUE
server_id  INTEGER REFERENCES servers(id)
source     TEXT                    -- 'g2g' | 'funpay' | NULL
created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
```

**Tiered storage downsampling:** Python asyncio background tasks. `1m → 5m → 1h → 1d`. Cleanup via `cleanup_old_snapshots()`.

**Required indexes:**
```sql
servers(game, region, version)
servers(game, name, region, version)
server_aliases(alias_key)         -- hot path
server_aliases(server_id)
snapshots_*(server_id, faction, ts DESC)
```

**Source of truth:** Alembic migrations in `backend/alembic/versions/`

---

## 9. Region Codes

```
EU, US, OCE, KR, TW, RU
```

Region is part of canonical server identity. Always sourced from `servers` table — never trusted from raw parser titles.

**Per-game region ID ranges (Retail, G2G internal):**
- EU: `293–540`
- US: `541–774`
- OCE: `775–786`
- RU: `787–806`

(Total 514 Retail servers. Classic ranges differ — see code.)

**Source of truth:** `servers` table + `backend/data/seed_*.py`

---

## 10. Pricing Model

| Model       | Used by                | `lot_size`     | `unit_price`              |
|-------------|------------------------|----------------|---------------------------|
| `per_unit`  | G2G (currency)         | 1              | `raw_price`               |
| `per_lot`   | FunPay (currency)      | `amount_gold`  | `raw_price / lot_size`    |
| `flat`      | accounts, bundles      | 1              | `raw_price`               |

**Derived display fields (NEVER stored in DB):**
- `unit_price` = derived from `raw_price` + `raw_price_unit` + `lot_size`
- `price_per_1k` = `unit_price * 1000` (currency assets only)

**Sort key for offer comparison:** always `price_per_1k` (or `unit_price` for non-currency).

**Source of truth in code:** `backend/api/schemas.py::Offer` model_validator

---

## 11. API Endpoints

| Endpoint                          | Auth | Response shape                                              |
|-----------------------------------|------|-------------------------------------------------------------|
| `GET /offers`                     | —    | `{ count, offers: [OfferRow], price_unit }`                 |
| `GET /servers`                    | —    | `{ count, servers: [{ display_server, realms, min_price, realm_sources }] }` |
| `GET /meta`                       | —    | `{ last_update }`                                           |
| `GET /parser-status`              | —    | `{ <source>: { offers, last_update, running, version, last_error } }` |
| `GET /price-history`              | —    | per-realm history; params `last`, `hours`, `faction`        |
| `GET /admin/quarantine`           | key  | up to 500 entries newest-first                              |
| `GET /admin/unresolved-servers`   | key  | sorted by count DESC                                        |
| `GET /admin/price-profiles`       | key  | profile diagnostic stats                                    |
| `POST /admin/register-alias`      | key  | params `alias`, `server_id`, `source`                       |

**Admin auth:** `X-Admin-Key` header must equal `ADMIN_API_KEY` env var.

**Source of truth in code:** `backend/api/router.py`

---

## 12. Environment Variables

### Backend (Railway)
```
DATABASE_URL          # postgres://...@centerbeam.proxy.rlwy.net:23586/railway
ADMIN_API_KEY         # for /admin/* endpoints
ALLOWED_ORIGINS       # comma-separated, no trailing slash
```

### Frontend (Vercel)
```
VITE_API_URL          # https://scintillating-flexibility-production-809a.up.railway.app
```

**Start command (Railway):**
```
alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port $PORT
```

---

## 13. Magic Numbers / Constants

| Constant                | Value      | Used in                        |
|-------------------------|------------|--------------------------------|
| `_QUARANTINE_MAX`       | 500        | Quarantine ring buffer         |
| `_INDEX_TOP_N`          | 10         | Per-server index (top-N cheapest mean) |
| `_OUTLIER_MULTIPLIER`   | 3.0        | Outlier filter + UI ⚠ flag     |
| `_MIN_LIQUID_GOLD`      | 50_000     | `best_ask` cumulative threshold |
| `_VWAP_GOLD_CAP`        | 1_000_000  | VWAP top-offers cap            |
| `_MIN_OFFERS`           | 2          | Min offers for index calc      |
| Snapshot throttle       | 0.5%       | Skip DB write if delta < 0.5%  |
| Alias cache TTL         | 60s        | `server_resolver.py`           |
| DB write batch size     | 50         | `gather()` batch in writer     |
| Frontend meta poll      | 10s        | `useOffers.js`                 |
| Sidebar search debounce | 150ms      | `ServerSidebar.jsx`            |
| FunPay cycle jitter     | 50–70s     | Background loop                |
| Position value cap      | $9999      | UI shows `∞` above this        |

**Source of truth:** named constants in respective code files. If you change a value here, grep the codebase to confirm the constant name and update both.

---

## 14. Faction Values

```
Horde
Alliance
All       (group-level aggregate)
```

Exact casing required. `faction` validation rejects anything else (WoW). Non-WoW assets may have asset-specific faction/category schemas.

---

## 15. Asset Types (platform-level)

```
currency    (gold, coins)
item        (specific in-game item)
skin        (cosmetic)
account     (full account sale)
bundle      (multi-item package)
```

Currently only `currency` is implemented. Adding new asset type requires: schema extension, parser adapter, normalization rules, display logic.

---

## Update Protocol

When you change code that touches anything in this registry:

1. Open `_registry/SKILL.md`
2. Update the affected section
3. Bump `Last verified` date at the top
4. If you renamed a code path: update "Source of truth in code" pointers
5. Grep other skills for any duplicated value — they should reference this file, not inline the value
6. If duplication exists: replace with `see _registry → Section N`

**Never** edit a value in another skill without first updating it here.
