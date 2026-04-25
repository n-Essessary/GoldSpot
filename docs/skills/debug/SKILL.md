---
name: debug
description: "Root cause analysis and minimal safe fixes. Coordinate with QA. Never guess."
---

# Debug

## Protocol

1. Reproduce the issue (use QA scenario or logs)
2. State: expected behavior vs actual behavior
3. Isolate root cause (not symptom)
4. Apply minimal fix (surgical edit only)
5. Validate with QA (no regression, no new quarantine entries)

---

## Root Cause Map

| Symptom | Likely Cause |
|---|---|
| `offers: []` returned | Parser returned 0 → check `/parser-status`, check quarantine |
| Offers cached but not updating | `_snapshot_running` stuck, or meta poll not triggering re-fetch |
| Wrong price displayed | `raw_price_unit` mismatch (per_lot vs per_unit), or wrong `lot_size` |
| G2G price too high/wrong | Wrong API endpoint for price extraction; `group=0` missing in Phase 2 |
| G2G offer count too low | Phase 1 not collecting all groups; check unique `(offer_group, region_id)` pairs |
| Server in wrong region | Alias maps to wrong canonical entity; check `server_aliases` table |
| FunPay price 1000× too high | `raw_price_unit="per_lot"` treated as `per_unit` |
| `display_server` has `realm_type` | `make_display_group()` not used; manual string construction |
| Unknown version defaulted silently | Missing WARNING log; check `detect_version()` path |
| DB connection pool exhausted | Concurrent `_snapshot_all_servers`; check `_snapshot_running` guard |
| Railway log spam | `_load_alias_cache` missing exponential backoff/circuit breaker |
| Quarantine growing | Missing alias; run `/admin/unresolved-servers` |
| `price_per_1k <= 0` ValidationError | Both `raw_price=0` and `price_per_1k=0` unset in parser |
| CORS error | `ALLOWED_ORIGINS` env var missing or incomplete |
| Admin endpoint 403 | `X-Admin-Key` must equal full `ADMIN_API_KEY` env var |

---

## Live Debugging (Production)

```js
// Parser state
fetch('https://scintillating-flexibility-production-809a.up.railway.app/parser-status')
  .then(r => r.json()).then(d => { window._status = d })

// Quarantine
fetch('.../admin/quarantine', { headers: { 'X-Admin-Key': '...' } })
  .then(r => r.json()).then(d => { window._q = d })

// Unresolved aliases
fetch('.../admin/unresolved-servers', { headers: { 'X-Admin-Key': '...' } })
  .then(r => r.json()).then(d => { window._u = d })
```

---

## Async Debugging Rules

- Never `asyncio.run()` inside a running event loop
- `gather(return_exceptions=True)` — exceptions are silent; check returned list
- Background task failures invisible → check `_last_error` dict
- DB pool operations must be `await`ed
- Parser loop delays: FunPay 50–70s jitter, G2G 30s — "no update" may just be timing

---

## DB / Migration Debugging

- Before any Alembic migration: `alembic heads` → must show single head
- After migration: verify `alembic current` on Railway
- Alias cache TTL 60s — new aliases take up to 1 min
- `COALESCE(sources, ARRAY[]::text[])` guard in `db/writer.py` — must not be removed

---

## Fix Constraints

- Do NOT redesign pipeline to fix a single bug
- Do NOT remove `_snapshot_running` guard
- Do NOT change `raw_price` / `price_per_1k` storage contract
- Do NOT add silent fallbacks for unknown version/entity
- Preserve: `_flatten_param()`, `write_price_snapshot`, `_snapshot_all_servers`

---

## Output Format

```
Issue: [description]
Root cause: [specific file/line/component]
Fix: [minimal change — diff style]
Validation: [curl or JS snippet confirming fix]
Regression check: [what else was tested]
```

---

## Coordination

- QA defines the scenario → Debug reproduces it
- Fix not complete until QA confirms no regression
- Schema change needed → involve database-engineer
