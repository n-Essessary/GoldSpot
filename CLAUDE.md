# GoldSpot — Project Intelligence File

## What This Project Is
A marketplace intelligence platform for digital goods (in-game currency, items, skins).
Not a WoW-only tool. All architecture must generalize across games, assets, marketplaces.

---

## Claude's Responsibilities
- Reverse engineering external APIs (G2G, FunPay, future sources)
- Analyzing response structures and edge cases
- Proposing robust parsing strategies
- Validating data integrity assumptions
- Identifying failure modes in external integrations

## NOT Claude's Job
- UI/UX decisions
- Frontend implementation
- Superficial refactoring without architectural impact

---

## Stack (do not change without explicit instruction)
- Frontend: React + Vite → Vercel
- Backend: FastAPI → Railway
- DB: PostgreSQL (asyncpg) → Railway
- Adapters: FunPay HTML scraper, G2G REST API

---

## Key Files — Scope Boundaries

| File | Role | Touch only when... |
|---|---|---|
| `backend/parser/g2g_parser.py` | G2G two-phase fetch | fixing parser logic |
| `backend/parser/funpay_parser.py` | FunPay HTML scraper | fixing parser logic |
| `backend/service/offers_service.py` | aggregation + cache | service-layer changes only |
| `backend/api/router.py` | API endpoints | API contract changes only |
| `backend/api/schemas.py` | Pydantic shapes | schema changes only |
| `backend/db/writer.py` | DB writes | writer logic only — never remove `COALESCE` guard or `_flatten_param()` |
| `backend/utils/version_utils.py` | version normalization | version mapping changes only |

---

## Immutable Contracts — Never Break

### Price fields (source of truth)
- `raw_price` — exact source value, never modified
- `raw_price_unit` — `per_unit` | `per_lot` | `flat`
- `lot_size` — units covered by `raw_price`
- `unit_price` — derived: `raw_price` if per_unit; `raw_price / lot_size` if per_lot
- `price_per_1k` — NEVER stored in DB; always derived at read-time

### Entity identity
- `display_server` always `(REGION) Version` — via `make_display_group()`, never hand-assembled
- `display_server` must be `""` from parser — set downstream by `_apply_canonical()`
- `realm_type` (`Normal` | `Hardcore`) — never embedded in `display_server`
- `region` — from canonical registry only, never from parser source

### API shapes (never add required fields without versioning)
- `GET /offers` → `{ count, offers: [OfferRow], price_unit }`
- `GET /servers` → `{ count, servers: [{ display_server, realms, min_price }] }`
- `GET /meta` → `{ last_update }`
- `GET /parser-status` → `{ funpay: {...}, g2g: {...} }`

---

## G2G Parser — Critical Rules

API base: `sls.g2g.com`. Required headers on EVERY request:
Accept: application/json
Referer: https://www.g2g.com/
Origin: https://www.g2g.com

**Phase 1 (Discovery):** paginate ALL pages until `len(results) < page_size`. Never stop at page 1.
**Phase 2 (Real prices):** requires `group=0` param. Without it: wrong prices, wrong qty.
**filter_attr construction** (exact format — any deviation silently returns 0 results):
```python
og = offer_group.lstrip("/")
prefix = re.sub(r"_\d+$", "", og)
fa = f"{prefix}:{og}"
```
**Phase 1 prices are aggregates — never use them.** Only `results[0].unit_price_in_usd` from Phase 2 is valid.

---

## Known Silent Regression Points

| What | Rule |
|---|---|
| G2G `group=0` missing | Wrong prices returned, no error |
| Phase 1 stops at page 1 | Drops 90%+ of server×faction groups silently |
| `filter_attr` format wrong | Returns 0 results silently |
| `display_server` set in parser | Must be `""` — set by `_apply_canonical()` |
| FunPay price treated as per_unit | 1000× inflation — always `per_lot` |
| `COALESCE` guard removed in writer | DB write failures on null sources |
| `_snapshot_running` flag removed | Concurrent DB writes, corruption |

---

## Async Rules
- No blocking ops in async context (`requests`, `time.sleep`, sync I/O)
- `gather(return_exceptions=True)` always — check returned exceptions
- DB writes batched ≤ 50. Never fire >50 concurrent DB tasks.
- Semaphore(20) on G2G Phase 2 calls

---

## Workflow
- **Cursor**: single-file or scoped changes
- **Cowork**: multi-file backend tasks
- **Claude**: architecture review, API research, diagnosis
- Conflict between skill/prompt and actual code → stop, present conflict explicitly, wait for confirmation
