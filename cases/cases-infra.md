# Case Log — Infrastructure

Railway, Vercel, external services, deployment, CORS.

---

## [BUG] FX provider `frankfurter.app` blocked from Railway

**Severity:** medium (currency conversion failed)

### Symptom
EUR → USD conversion failed with network error. Only on Railway, worked locally.

### Root cause
Railway's egress somehow can't reach `frankfurter.app`. Reason not investigated — likely IP block or DNS issue on Railway's side.

### Resolution
Fallback chain:
1. `open.er-api.com` (primary)
2. `jsdelivr CDN` (secondary, serves static FX rate JSON)

Both verified reachable from Railway.

### Prevention
- Rule in chat instructions: do NOT add `frankfurter.app` to FX chain
- Any new external dependency must be tested from Railway, not just local

---

## [BUG] Railway logs flooded by resolver retries

**Severity:** medium (log spam, increased Railway costs)

See `cases-pipeline.md` — circuit breaker on `server_resolver.py`.

---

## [INFRA] CORS for G2G API

### Rule
G2G API calls require browser-like headers. From server-side parser:
```
Accept: application/json
Referer: https://www.g2g.com/
Origin: https://www.g2g.com
```

### From browser (Chrome MCP debugging)
- Calls must originate from a tab on `https://www.g2g.com`, NOT `sls.g2g.com`
- Otherwise CORS rejects

---

## [INFRA] Deployment: Railway start command

```
alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port $PORT
```

### Why this exact form
- `alembic upgrade head &&` ensures schema is current before app starts
- If migration fails, app does NOT start — fails fast, no silent stale-schema operation
- `--host 0.0.0.0` required for Railway port binding
- `$PORT` env var injected by Railway, do not hardcode

### Failure mode if changed
- Removing `&&` → app starts on stale schema, queries fail
- Removing `--host 0.0.0.0` → Railway can't reach app, deploy times out

---

## [INFRA] Frontend env vars

### Rule
Never hardcode Railway or localhost URLs in frontend code. Use `VITE_API_URL`.

### Why
- Railway URL changes on environment changes
- Local dev uses different URL
- Hardcoded URL = guaranteed regression on any infra move

### Pattern
```js
const API_URL = import.meta.env.VITE_API_URL
```

Configure per environment in Vercel dashboard.

---

## [INFRA] DB capacity budget

| Allocation | Size |
|---|---|
| Total Railway DB limit | 5 GB |
| WoW (Classic + Retail) | 1 GB |
| Future games | ~3.5 GB |
| Headroom for indexes/WAL | ~500 MB |

Current usage: ~420 MB.

### Rule
New game expansion must check budget BEFORE schema design. Adding a game with 10× the snapshot frequency of WoW would blow the budget.

---

## [INFRA] Async / Python rules (backend-wide)

These apply across the codebase, not specific to one module:

- No blocking operations in async context (`requests`, `time.sleep`, sync file I/O)
- `gather(return_exceptions=True)` for all parallel tasks — always check returned exceptions
- DB writes batched in groups of 50 (see `cases-db.md`)
- `_snapshot_running` flag mandatory — prevents concurrent DB snapshot writes

### Why these exist
Each one was the root cause of a production incident. Not theoretical best practice.

---

## [INFRA] Server registry source

### Source of truth
`warcraft.wiki.gg/wiki/Classic_realms_list` — scraped via JS DOM traversal.

### Rule
- Re-scrape periodically when Blizzard adds/renames servers
- Never trust raw parser data for canonical server names — always look up via registry

### For new games
- Identify equivalent canonical source (official wiki, game API, etc.)
- Document scrape pattern in this file
- Add `game` discriminator to `servers` table (see `cases-db.md`)


## [BUG] XS performance hotspots — CPU/DB/memory under snapshot cycles

**Severity:** medium (degraded throughput, memory leak, DB connection bottleneck)

### Symptom
- Snapshot cycles consuming excess CPU (O(N_servers × N_offers) scans)
- 4–8 heavy `SELECT … WHERE alias = ANY($1)` per minute despite in-memory cache
- `_index_cache`, `_last_snap_price`, `_last_written` growing unbounded after hours of uptime
- asyncpg pool exhausted under burst writes (max_size=5 vs 50+ concurrent batch tasks)

### Root cause
1. `compute_server_index` called with full `all_offers` per `(server_id, faction)` pair
2. `normalize_pipeline` called `resolve_server_batch(pool, all_keys)` without checking `_alias_cache` first
3. Module-level dicts had no eviction — grew linearly with offer churn
4. asyncpg pool `max_size=5` too small for batched snapshot writes

### Resolution
- Pre-group offers: `by_sid = defaultdict(list)` once before loop; pass `by_sid[sid]` to compute
- Alias fast-path: check `_sr_module._alias_cache` first, only DB for actual misses
- Import via module (`import db.server_resolver as _sr_module`) not by-name — avoids stale reference
  after `global _alias_cache = new_dict` reassignment in `_load_alias_cache`
- `_BATCH_ENTRY_MAX`: 500 → 4096
- TTLCache(maxsize=10_000, ttl=3600) on `_index_cache`, `_last_snap_price`, `_last_written`
- asyncpg pool: `min_size=5, max_size=20`
- Added `cachetools>=5.3.0` to requirements.txt

### Prevention
- Rule: any module-level accumulator dict that grows per offer-cycle needs TTL or explicit reset
- Rule: alias resolution fast-path must check in-memory cache before DB — never skip
- Rule: when importing a module-level variable that gets reassigned via `global`, import the module,
  not the name
