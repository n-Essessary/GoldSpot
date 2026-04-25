---
name: system-architecture
description: "Global architecture rules and coordination between all skills. Highest-authority skill — all others must comply."
---

# System Architecture

## Scope

This system is a **marketplace intelligence platform** that aggregates and analyzes tradable virtual goods across games and platforms. Current implementation covers WoW Classic gold (FunPay + G2G). The architecture must support expansion to any game, any asset category, and any marketplace without rewriting core logic.

---

## Stack

```
Frontend (React + Vite → Vercel)
    ↕ REST/JSON
Backend (FastAPI → Railway)
    ↕ asyncpg pool
Database (PostgreSQL → Railway)
    ↑
Marketplace Adapters (one per source: FunPay, G2G, ...)
```

---

## Domain Model — Platform-Level Contracts

### Asset
Any tradable virtual good: currency (gold, coins), item, skin, account, bundle.
- `asset_type`: `currency` | `item` | `skin` | `account` | `bundle`
- `unit`: the denomination for a single unit of this asset (1 gold, 1 item, 1 account)
- `quantity`: number of units in the offer

### PricingModel
How source prices are expressed:
- `per_unit`: price for 1 unit of the asset (G2G gold: price per 1 gold)
- `per_lot`: price for the entire listed quantity (FunPay: price for N gold)
- `flat`: single price for indivisible asset (account, bundle)

`unit_price` (price per 1 unit, USD) is always derived at read-time — never stored.

For currency assets, `price_per_1k` = `unit_price * 1000` — a convenience display field derived from `unit_price`. It is game-specific and not a universal platform concept.

### Entity
A canonical registry entry for a game entity with a market (server, realm, platform).
- For WoW: `(server_name, region, version, realm_type)`
- For other games: defined by game-specific schema
- Always resolved via canonical registry — never trusted from parser directly

### display_group
The human-readable grouping key under which offers are aggregated.
- For WoW gold: `(REGION) Version` e.g. `(EU) Anniversary`
- For other assets: defined by asset schema
- Constructed via `make_display_group()` — never hand-assembled
- Must NEVER embed `realm_type` or other sub-attributes

### Source
A marketplace adapter: `funpay`, `g2g`, or any future addition.

---

## WoW-Specific Domain Values (current implementation)

These are implementation details, not platform universals:

- `GameVersion`: `Classic`, `Anniversary`, `Season of Discovery`, `Classic Era`
  - `Season of Mastery` → filtered at enum level, never displayed
- `realm_type`: `Normal` | `Hardcore` — never embedded in `display_group`
- `region`: from canonical registry, never from parser source
- Price normalization: G2G `unit_price = raw_price`, FunPay `unit_price = raw_price / lot_size`

---

## Normalization Pipeline (mandatory, all sources)

```
raw offer (adapter output)
  → phase 0: source-specific cleanup   — display_group string format
  → phase 1: normalize_offer_batch()   — validate / resolve / canonicalize /
                                          price-validate / dedup
  → _cache[source]
  → quarantine ring buffer (rejected offers)
```

**No silent fallbacks at any phase.**
- Unknown entity → `entity_id=None` + unresolved registry (not quarantine)
- Unknown version/category → log WARNING + quarantine
- Invalid price → quarantine with reason
- Ambiguous data → reject over guess

---

## API Contracts — Strict, Never Break

```
GET /offers        → { count, offers: [OfferRow], price_unit }
GET /servers       → { count, servers: [{ display_server, realms, min_price }] }
GET /meta          → { last_update }
GET /parser-status → { <source>: { offers, last_update, running, version, last_error } }
Admin /admin/*     → require X-Admin-Key header
```

Fields: never rename, never add required fields without defaults, never change datetime format.

---

## Responsibility Boundaries

| Skill | Owns | Must NOT |
|---|---|---|
| parser | Collect raw offers, emit normalized Offer objects | Touch DB, modify API shape |
| backend-api | Serve endpoints, cache reads, service coordination | Parse, store price_per_1k |
| data-logic | Price calculation, aggregation, indexing | Control API responses |
| database-engineer | Schema, migrations, query performance | Define API fields |
| normalize-pipeline | Validate, resolve, canonicalize, dedup | Default unknown data |
| marketplace-architecture | Understand source internals | Define canonical domain |
| frontend | Consume API, display data | Redefine backend fields |
| devops | Infra, deploy, monitoring | Change business logic |
| qa-testing | Define test cases, validate behavior | Modify production code |
| debug | Reproduce + fix bugs minimally | Fix without QA sign-off |

---

## Forbidden Actions (all skills)

- Hand-constructing `display_group` / `display_server` without `make_display_group()`
- Defaulting unknown version/entity to any value silently
- Storing `unit_price` or `price_per_1k` in DB
- Hardcoding marketplace-specific quirks outside adapter layer
- Applying fix without quarantine/log evidence
- Alembic migration without verifying chain (`alembic heads`)
- Blocking the async event loop

---

## Background Loop Behavior

- Each source runs an independent background loop
- Loop interval is source-specific (configured per adapter)
- `_snapshot_running` flag prevents concurrent DB snapshot writes
- Cache resilience: empty parse result NEVER replaces populated cache

---

## QA ↔ Debug Coordination (mandatory)

1. QA defines expected behavior and edge cases
2. Debug reproduces QA scenario before applying fix
3. Fix validated against QA expectations
4. No fix is complete without QA sign-off
5. Regression check required: other endpoints, DB writes, frontend

---

## Priority Order (conflict resolution)

1. system-architecture
2. backend-api (API contract)
3. database-engineer (data integrity)
4. normalize-pipeline (correctness)
5. qa-testing
6. debug
7. all others

---

## Goal

A stable, extensible, and data-consistent marketplace intelligence platform that correctly aggregates any tradable virtual good from any source, validated at every layer, with deterministic pricing.
