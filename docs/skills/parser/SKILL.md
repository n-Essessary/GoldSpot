---
name: parser
description: "Build and maintain marketplace adapters — fetch, parse, and emit canonical Offer objects from any source."
---

# Parser / Marketplace Adapter

## Role

A parser is a **marketplace adapter**: it fetches raw data from one source and emits canonical `Offer` objects. It owns source-specific quirks (URL structure, auth, pricing model, HTML schema). It does NOT own domain logic.

---

## Mandatory Output Contract

Every emitted `Offer` must satisfy:

```python
Offer(
    id=str,               # unique: "{source}_{offer_id}"
    source=str,           # adapter identifier: "funpay" | "g2g" | ...
    server=str,           # lowercase slug (model_validator handles this)
    display_server=str,   # "(REGION) Version" — use make_display_group()
    server_name=str,      # realm name only ("Firemaw"), not group label
    faction=str,          # "Horde" | "Alliance" — exact casing
    raw_price=float,      # > 0, exact source price in USD
    raw_price_unit=str,   # "per_unit" | "per_lot" | "flat"
    lot_size=int,         # 1 for per_unit; quantity for per_lot
    amount_gold=int,      # > 0 (or quantity for non-currency assets)
    seller=str,
    offer_url=str|None,
    updated_at=datetime,  # UTC, timezone-aware
    fetched_at=datetime,  # UTC, timezone-aware
)
```

**Never set `price_per_1k` directly — derived by model_validator.**

---

## Price Unit Rules

See `_registry` → Section 10 (Pricing Model).

Mixing these up is the #1 parser bug. Verify with: `price_per_1k = unit_price * 1000`. Treating `per_lot` as `per_unit` causes 1000× inflation.

---

## FunPay Adapter Rules

Configuration values (URL, currency chain, online filter): see `_registry` → Section 7.

Behavior rules:
- Single GET — no pagination
- If 0 online items: log WARNING (possible markup change), return `[]`
- `_parse_float()` handles `$`, `,`, `.` — reuse, do not duplicate
- `display_server` from `.tc-server` is raw; normalization in `_normalize_funpay_offer()`

---

## G2G Adapter Rules (Two-Phase Dual-Sort Architecture)

**Configuration values** (brand_id, headers, cycle intervals, semaphores, max_pages per game): see `_registry` → Sections 4, 6.
**`filter_attr` construction formula:** see `_registry` → Section 5.

**Architectural rules (stable):**

Two sort modes per game cycle. For Classic: concurrent via `asyncio.gather()`. For Retail: two independent loops (different intervals). Each sort runs Phase 1 + Phase 2. Results combined and deduplicated by `offer_id`.

**Phase 1 — Discovery:** `GET /offer/search` with `seo_term`, no `group=0`, no `filter_attr`. Extract unique `(offer_group, region_id)` pairs into a dict. Paginate ALL pages until `len(results) < page_size` or `page > max_pages`. Stopping early silently drops 90%+ of groups. **Phase 1 prices are wrong — never use them** (grouped aggregates without `group=0`).

**Phase 2 — Real price per group:** for each pair, `GET /offer/search?filter_attr={fa}&region_id=...&group=0&include_offline=0&page_size=1`. `group=0` is mandatory. Read `results[0].unit_price_in_usd`.

`asyncio.gather(return_exceptions=True)` for Phase 2 — always check returned list.

**`_parse_title()` contract:** extracts `server_name`, `source_region`, `faction` only. Does NOT set `version` or canonical `region` — resolved downstream by normalize_pipeline. `display_server` left empty `""` — set by `_apply_canonical()`.

`seller` field = sort label (`"Lowest Price"` / `"Recommended"`), not a real username.

---

## Title Parsing Rules

Title parsing produces: `(entity_name, region, version, faction)`.

Two-level strategy — mandatory for all string-format sources:
1. **Strict regex** — covers well-formed titles
2. **Flexible fallback** — handles non-standard formats

Rules:
- Unknown version after both levels → log `WARNING`, emit with `version=""` → quarantine in pipeline
- Never default unknown version to `Classic` silently
- `realm_type` (`Hardcore`, `Normal`) is parsed separately from version — zero overlap
- Raw region from title is preserved alongside canonical region

---

## Anti-Block Strategy

- FunPay: random jitter between cycles (see `_registry` → Section 6)
- G2G: `asyncio.Semaphore` per Phase 2 (limits in `_registry` → Section 6)
- 429: read `Retry-After` header, backoff, max 2 retries
- 5xx: exponential backoff (2^attempt), max 2 retries

---

## Failure Handling Rules

- Return `[]` on unrecoverable error — never raise to `offers_service`
- Log `ERROR` for network failures, `DEBUG` for individual offer parse failures
- Cache is NOT wiped on `[]` return — `offers_service` guards this
- Quarantine individual bad offers, not the whole batch

---

## What Parsers Must NOT Do

- Touch DB directly
- Construct `display_server` without `make_display_group()` / `_normalize_*_offer()`
- Default unknown version or entity to any value silently
- Set `price_per_1k` directly on `Offer`
- Mix pricing models across sources

---

## Adding a New Adapter

1. Create `parser/{source}_parser.py`
2. Define `SOURCE`, `ASSET_TYPE`, `async def fetch_offers() -> list[Offer]`
3. Implement fetch, parse, dedupe, return flat `list[Offer]`
4. Add `_normalize_{source}_offer()` in `offers_service.py` for phase-0 cleanup
5. Register loop in `offers_service.py`:
   - Add to `_cache`, `_last_update`, `_running`, `_cache_version`, `_last_error`
   - Follow `_run_funpay_loop` / `_run_g2g_loop` pattern exactly
6. Add tests for: correct `raw_price_unit`, `price_per_1k` derivation, title parsing edge cases
