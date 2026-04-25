---
name: backend-api
description: "FastAPI backend — endpoints, service layer, cache, and data aggregation."
---

# Backend API

## Stack

- FastAPI + asyncio, hosted on Railway
- Entry: `main.py` → `api/router.py` → `service/offers_service.py`
- DB: asyncpg pool via `db/writer.py`

---

## Strict API Contracts — Never Break

**Full endpoint list with response shapes:** see `_registry` → Section 11.

Rules that apply to every endpoint:
- Do NOT rename fields. Do NOT add required fields without defaults. Do NOT change datetime format.
- Admin endpoints must use `Depends(require_admin_key)` — see `_registry § 11` for auth contract
- Response shape changes require version bump or new endpoint — never break existing consumers

OfferRow field order (for `GET /offers`): `id, source, server_name, server_id, faction, price_per_1k, price_display, amount_gold, seller, offer_url, updated_at, fetched_at`

---

## Service Layer Rules

### Cache reads (< 5ms, no DB)
- `get_all_offers()` → `_cache["funpay"] + _cache["g2g"]`
- `get_servers()` → derived from cache + `_index_cache`
- `get_meta()` → max of `_last_update` values

### Cache writes (background only)
- Only `_run_{source}_loop()` writes to `_cache[source]`
- After write: `asyncio.create_task(_snapshot_all_servers())`
- `_snapshot_running` flag prevents concurrent snapshot writes — must not be removed

### Price derivation — mandatory
Pricing model (per_unit/per_lot/flat, derivation formulas): see `_registry` → Section 10.

DB-specific:
- `price_per_1k` is NEVER stored in DB — always derived at read-time
- `OfferRow.from_offer(offer, price_unit)` handles display conversion

---

## Endpoint Development Rules

1. New endpoints → `api/router.py` only
2. New business logic → `service/offers_service.py` or new service module
3. New DB queries → `db/writer.py`
4. Never call DB from `router.py` directly
5. Admin endpoints must use `Depends(require_admin_key)`

---

## Async Rules

- All DB operations must be `await`ed
- `asyncio.gather(*tasks, return_exceptions=True)` for parallel DB writes
- Never `asyncio.run()` inside a running event loop
- Background tasks via `asyncio.create_task()` — log exceptions explicitly
- `ThreadPoolExecutor(max_workers=4)` for CPU-bound parsing (BeautifulSoup)

---

## Error Handling

- Parser failure: `_last_error[source] = type(e).__name__`; cache preserved
- DB unavailable: endpoints return empty results (not 500)
- `gather(return_exceptions=True)` — check returned list for Exception instances
- Cold start: `_cache_initialized[source] = False` until first successful parse

---

## Performance Rules

Magic numbers (batch size, snapshot throttle, quarantine cap): see `_registry` → Section 13.

Rules:
- Cache reads: O(n) filter on list — acceptable at current scale
- Snapshot batching prevents pool exhaustion — never fire more than batch-size concurrent DB writes
- Snapshot throttle skips DB writes when price change is below threshold
- Quarantine is a ring buffer — oldest dropped first

---

## Preserved Components (never remove)

- `_flatten_param()` helper in `db/writer.py`
- `COALESCE(sources, ARRAY[]::text[])` guard in `db/writer.py`
- `write_price_snapshot` function
- `_snapshot_all_servers` + `_snapshot_running` guard
- Background loop structure in `_run_{source}_loop`
- `_cache_initialized[source]` cold-start guard

---

## What Backend Must NOT Do

- Implement parsing logic (HTML scraping, API pagination)
- Store `price_per_1k` in DB
- Default unknown version/entity silently
- Break `/offers` response shape
- Block the event loop
