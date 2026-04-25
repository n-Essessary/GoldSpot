---
name: database-engineer
description: "PostgreSQL schema design, migrations, query optimization, and data storage contracts."
---

# Database Engineer

## Context

- PostgreSQL on Railway: `centerbeam.proxy.rlwy.net:23586`, db `railway`
- Migrations: Alembic
- Pool: asyncpg (managed in `db/writer.py` via `get_pool()`)

---

## Schema Overview

**Full schema definition (tables, columns, indexes, retention):** see `_registry` → Section 8.

Quick mental map:
- **Hot path**: `snapshots_1m` (24h), `server_aliases` (alias_key lookup)
- **Charts**: `snapshots_5m` (30d), `snapshots_1h` (~1y), `snapshots_1d` (∞)
- **Identity**: `servers` (canonical registry, keyed by `(game, name, region, version)`)

Tiered storage downsampling (`1m → 5m → 1h → 1d`) runs as Python asyncio background tasks. Cleanup via `cleanup_old_snapshots()`.

---

## Migration Rules (Critical)

1. **Before creating any migration**: `alembic heads` → must show exactly one head
2. After creating migration file: verify `down_revision` matches current head
3. After deploying: `alembic current` on Railway must match expected revision
4. Never edit a migration already applied to production
5. Irreversible migrations (DROP, ALTER TYPE) require explicit downgrade plan

---

## Storage Contract

Pricing model details: see `_registry` → Section 10.

DB-specific rules:
- `price_per_1k` is **never** stored — derived at read-time
- `raw_price` is source of truth alongside `raw_price_unit` and `lot_size`
- Timestamps: always `TIMESTAMP WITH TIME ZONE` (UTC)

---

## Indexing Requirements

See `_registry` → Section 8 for the canonical index list.

**Hot path:** `server_aliases(alias_key)` — hit on every parse cycle for every offer. Must be unique index.

**Snapshot tables** (`snapshots_1m`, `snapshots_5m`, `snapshots_1h`, `snapshots_1d`): each must have `(server_id, faction, ts DESC)` for chart queries.

**`servers`**: must have `(game, region, version)` and `(game, name, region, version)`.

---

## Query Performance Rules

Constants (alias cache TTL, batch sizes, snapshot throttle): see `_registry` → Section 13.

Rules:
- `server_aliases` lookup is on hot path → alias cache reduces DB hits to ~1/min
- `write_price_snapshot` batched via `gather(return_exceptions=True)`
- Skip write if `raw_price` delta below throttle threshold
- `query_index_history`: adaptive bucket size, never return raw rows per-second
- PostgreSQL: SELECT-list aliases cannot be referenced in correlated subqueries → use CTEs

---

## Alias Cache Resilience

- Cache failure must NOT wipe a populated cache with empty result
- `_load_alias_cache` must use exponential backoff + circuit breaker
  - Railway cold starts cause transient failures → without backoff: log spam
- New alias via `/admin/register-alias` → visible in cache within 60s

---

## `COALESCE` Guard — Must Preserve

```sql
COALESCE(sources, ARRAY[]::text[])
```
In `db/writer.py` aggregate queries. Prevents NULL array errors on single-source data. **Never remove.**

---

## Cleanup Policy

- `cleanup_old_snapshots()`: runs daily, removes `price_snapshots` older than 1 year
- Non-blocking: `asyncio.create_task()` in lifespan, sleeps between batches
- No-op when `DATABASE_URL` not set

---

## Anti-Patterns

- Full table scan on `server_aliases` (use indexed `alias_key`)
- Storing computed prices (store `raw_price`, derive at read-time)
- `asyncio.run()` inside async context
- DB queries inside route handlers (use service layer)
- Migration without chain integrity check
- Correlated subquery referencing SELECT-list alias (use CTE)
