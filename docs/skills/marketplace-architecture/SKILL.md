---
name: marketplace-architecture
description: "How marketplaces work internally — scraping, APIs, pricing models, quirks. Covers FunPay and G2G with abstraction patterns for future sources."
---

# Marketplace Architecture

## Core Concept: Marketplace Adapter

Each source marketplace is an **adapter** that translates raw marketplace data into the platform's canonical `Offer` format. Adapters are isolated — they own source quirks, not the domain model.

An adapter must:
1. Fetch raw listings (HTML scrape, REST API, WebSocket, etc.)
2. Emit `Offer` objects with correct `raw_price`, `raw_price_unit`, `lot_size`
3. Never touch DB, never set `price_per_1k` directly
4. Return `[]` on unrecoverable failure — never raise to caller

---

## Adapter Interface

```python
async def fetch_offers() -> list[Offer]:
    """Entry point for the background loop. Must be idempotent."""
```

Each adapter also exposes:
- `SOURCE: str` — source identifier (e.g. `"funpay"`, `"g2g"`)
- `ASSET_TYPE: str` — category of goods (e.g. `"currency"`, `"item"`)

---

## FunPay

**All configuration values** (URL, currency conversion chain, online filter): see `_registry` → Section 7.
**Pricing model:** see `_registry` → Section 10.

### Behavior Rules
- Single HTML page returns ALL listings for all servers in one GET — no pagination
- If 0 online items: log WARNING about possible attribute change, return `[]`
- **Critical:** treating `per_lot` as `per_unit` causes 1000× price inflation
- Detect currency from `.tc-price .unit` element — never assume USD

### Title Format (WoW Classic)
```
(EU) Classic - Firemaw            → version=Classic, server_name=Firemaw
(EU) Anniversary - Spineshatter   → version=Anniversary
(EU) #Hardcore - Nek'Rosh         → realm_type=Hardcore, server_name=Nek'Rosh
(RU) Classic - Пламегор           → region=RU, Cyrillic realm name
```
`#` prefix = special realm_type indicator. Unicode apostrophe `\u2019` in names must be normalized.

### Offer URL
`https://funpay.com{href}` where `href` is on `.tc-item` element.

### Rate Limiting
- 429 → read `Retry-After` header, back off, max 2 retries
- 5xx → exponential backoff (2^attempt), max 2 retries

---

## G2G

### API Architecture

**All configuration values** (brand_id per game, seo_term, headers, host, query params): see `_registry` → Section 4.
**`filter_attr` formula:** see `_registry` → Section 5.
**Cycle config (intervals, semaphores, max_pages per game):** see `_registry` → Section 6.

### Two-Phase Architecture (Stable Rules)

For each game, sort modes run on their own schedule:
- **Classic** — dual-sort `lowest_price + recommended_v2` concurrent via `asyncio.gather()`
- **Retail** — two independent loops (different intervals + semaphores)

Each sort independently runs Phase 1 + Phase 2. Results combined and deduplicated by `offer_id`. Expected unique offers depends on game scale.

**Phase 1 — Discovery (grouped search, price NOT trusted):**
```
GET /offer/search
  ?seo_term=<game-specific>
  &brand_id=<game-specific>
  &service_id=lgc_service_1
  &sort={sort}
  &currency=USD&country=SG&v=v2
  &page_size=48&page={page}
  (no group=0, no filter_attr, no region_id)
```
Extract from each result: `offer_group` + `region_id`. Store as unique `(offer_group, region_id)` pairs in a dict. Paginate until `len(results) < page_size` or `page > max_pages`. **Stopping early silently drops 90%+ of server×faction groups.**

Phase 1 prices (`unit_price_in_usd`) are grouped aggregates — do not use them.

**Phase 2 — Real price per group:**
```
GET /offer/search
  ?seo_term=<game-specific>
  &filter_attr={fa}
  &region_id={region_id}
  &group=0                ← mandatory
  &include_offline=0
  &sort={sort}
  &currency=USD&country=SG&v=v2
  &page_size=1&page=1
```

`group=0` is mandatory — without it prices are wrong and `available_qty` is unreliable.

Read `results[0].unit_price_in_usd` — real cheapest price for that server×faction.

Concurrency: `asyncio.Semaphore` per Phase 2 call (limits in `_registry`). Wrap with `gather(return_exceptions=True)`.

### Pricing
See `_registry` → Section 10. G2G is `per_unit`.

### `_parse_title()` Contract
- Two-level: strict regex → flexible fallback
- Extracts: `server_name`, `source_region`, `faction`
- Does NOT set `version` or canonical `region` — resolved downstream by normalize_pipeline from server registry
- `display_server` left empty `""` in parser — set by `_apply_canonical()` in normalize_pipeline
- `seller` field = `"Lowest Price"` or `"Recommended"` (sort label) — not a real username

### Offer URL
Built from `offer_group` + `region_id` + `sort`:
```python
fa_encoded = quote(f"{prefix}:{og}", safe="")
url = f"https://www.g2g.com/categories/<seo_term>/offer/group?fa={fa_encoded}&region_id={region_id}&sort={sort}&include_offline=0"
```

---

## Cross-Platform Normalization Reference

| Attribute | FunPay | G2G |
|---|---|---|
| Pricing model | `per_lot` | `per_unit` |
| Server name location | after ` - ` in title | before `[REGION]` |
| Online signal | `data-online="1"` | `available_qty > 0` |
| Region location | `(EU)` prefix in title | inside `[EU - Version]` |
| Offer URL format | `funpay.com/{href}` | `g2g.com/.../{offer_id}?...` |

---

## Adding a New Marketplace Adapter

1. Create `parser/{source}_parser.py`
2. Define `SOURCE`, `ASSET_TYPE`, and `fetch_offers() -> list[Offer]`
3. Implement source-specific fetch logic, rate limiting, and retries
4. Map to `Offer` with correct `raw_price_unit` and `lot_size`
5. Register in `offers_service.py` (follow existing loop pattern)
6. Add source key to `_cache`, `_last_update`, `_running`, `_cache_version`, `_last_error`
7. Add `_normalize_{source}_offer()` for phase-0 cleanup

---

## Anti-Patterns

- Using `www.g2g.com` for API calls (returns HTML — see `_registry` § 4 for correct host)
- Omitting required G2G headers (requests fail or return wrong data)
- Using G2G grouped API for price extraction without `group=0`
- Stopping Phase 1 pagination before `len(results) < page_size` (drops 90%+ of groups)
- Assuming USD pricing on FunPay without currency detection
- Treating `per_lot` prices as `per_unit` (1000× inflation bug)
- Hardcoding brand_id/seo_term per game in adapter — use config table from `_registry` § 4
