---
name: qa-testing-skill
description: "Define test cases, validate system behavior, catch regressions. Covers parser output, pipeline, API contracts, price calculations, and frontend."
---

# QA Testing

## Core Responsibility

Define test cases, validate behavior, catch regressions before and after fixes.

---

## Test Layers

### 1. Parser Output Validation

Reference: `_registry` §§ 1 (versions), 10 (pricing), 14 (factions). Known failures: `failure-modes` skill.

All emitted `Offer` objects must:
- Pass Pydantic validation
- Have `price_per_1k > 0` — derived correctly from `raw_price` + `raw_price_unit` + `lot_size`
- Have `display_server` matching `(REGION) Version` — no `realm_type` embedded
- Have `server_name` as realm only (not group label)
- `raw_price_unit` matches source convention (see `_registry § 10`)
- No offer with quarantined version (see `_registry § 1`) passes normalization
- No silent default when version detection fails (see `failure-modes § [normalize]`)

### 2. G2G Parser — Two-Phase Architecture

Reference: `_registry` §§ 4–6 (config), `marketplace-architecture` skill. Known failures: `failure-modes § [parser]` entries.

Tests:
- Phase 1 produces unique `(offer_group, region_id)` pairs per server×faction
- Phase 1 pagination reaches final page (`len(results) < page_size`) — never stops at page 1
- Phase 2 fetches with `group=0` — verify prices differ from grouped-mode response
- Phase 2 semaphore enforced (see `_registry § 6`)
- `filter_attr` formula produces exact expected string (see `_registry § 5`)
- Dual-sort (Classic) produces offers with both `"Lowest Price"` and `"Recommended"` seller labels
- Retail two-loop cycle produces offers from both `lowest_price` and `recommended_v2`
- Prices from Phase 2 match expected market range (not `available_qty=0`, not wrong currency)

### 3. Normalization Pipeline

- Unknown server title → `server_id=None` in cache, entry in `/admin/unresolved-servers`
- Unknown faction → quarantine with `unknown_faction:*`
- Duplicate `offer_id` → deduplicated, not counted twice
- `price_per_1k <= 0` → ValidationError, offer quarantined
- Unknown version after all patterns → quarantine with `unknown_version:*`

### 4. API Contract Tests

Reference: `_registry § 11` for endpoint list and response shapes.

```
GET /offers
  ✓ returns { count, offers, price_unit }
  ✓ ?server=(EU) Anniversary → only EU Anniversary offers
  ✓ ?faction=Horde → only Horde offers
  ✓ ?server_name=Firemaw → realm-level filter
  ✓ ?price_unit=per_1 → price_display = price_per_1k / 1000
  ✓ cold start → { count: 0, offers: [] }, not 500

GET /servers
  ✓ returns { count, servers }
  ✓ includes realm_sources: dict[str, list[str]] per ServerGroup
  ✓ sorted per canonical version order (see _registry § 1)

GET /parser-status
  ✓ no auth required
  ✓ returns counts, timestamps, running state per source

GET /meta
  ✓ last_update changes after each parse cycle

GET /price-history
  ✓ returns array of points ordered correctly (see failure-modes § [parser] and chart fixes)
  ✓ params: last, hours, faction
  ✓ returns [] when DB unavailable (not 500)

GET /admin/quarantine  (X-Admin-Key required)
  ✓ 403 without header
  ✓ returns up to _QUARANTINE_MAX entries newest-first (see _registry § 13)

GET /admin/unresolved-servers
  ✓ sorted by count DESC
```

### 5. Price Calculation Tests

| Scenario | Input | Expected `price_per_1k` |
|---|---|---|
| G2G per_unit | `raw_price=0.003`, `unit=per_unit` | `3.0` |
| FunPay per_lot | `raw_price=3.0`, `lot_size=1000`, `unit=per_lot` | `3.0` |
| FunPay per_lot | `raw_price=1.5`, `lot_size=500`, `unit=per_lot` | `3.0` |
| Both same server | G2G + FunPay at equivalent price | sorted equally by `price_per_1k` |

### 6. Background Loop Tests

- After parse cycle: `_cache[source]` count > 0
- After parse cycle: `_last_update[source]` timestamp changes
- `_snapshot_running=True` during snapshot: second call exits immediately
- Parser failure: `_last_error` set, cache preserved (not wiped)
- Cache populated: empty parse result does NOT replace it

### 7. Frontend Tests

- Loading state when `offers.length === 0 && loading === true`
- Error state on network failure
- `enabledSources` toggle filters client-side without new API call
- `initialServer` change triggers immediate re-fetch
- `formatTime()` returns `—` for null/invalid ISO string
- `isExpensive` flag for G2G offers exceeding outlier threshold (see `_registry § 13`)
- `positionValue` above cap renders `∞` (see `_registry § 13` position value cap) — no crash
- PriceChart zoom preserved across background refresh (see `failure-modes § [frontend]`)
- ServerSidebar shows green dot when realm has >1 source (`realm_sources.length > 1`)

### 8. Regression Checklist (after any fix)

Primary reference: `failure-modes` skill — run through entries relevant to touched area before closing.

Universal checks:
- [ ] `/offers` returns correct shape (see `_registry § 11`)
- [ ] G2G offer count is at expected level (not regressed — see `failure-modes § [parser] G2G low offer count`)
- [ ] FunPay prices not 1000× inflated (see `failure-modes § [parser] FunPay price 1000× inflated`)
- [ ] G2G prices match market range (see `failure-modes § [parser] G2G returns wrong prices`)
- [ ] No new quarantine entries for previously-working offers
- [ ] `display_server` never contains `Hardcore` or `Normal`
- [ ] `alembic heads` shows single head (see `failure-modes § [db] Alembic broken chain`)

---

## Output Format

```
Test: [name]
Input: [params or scenario]
Expected: [specific value or behavior]
Actual: [observed]
Status: PASS / FAIL
Reproduction: [curl or JS snippet]
```

---

## Constraints

- Test with real production data shape (not mocked)
- Validate both sources independently
- Test failure modes: empty parser result, DB down, network timeout
- Do NOT modify production code
