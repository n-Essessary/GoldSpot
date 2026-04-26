# Case Log — Database

PostgreSQL schema, migrations, snapshot tables, alembic.

---

## [ARCH] Tiered snapshot storage

**Status:** production, stable
**Files:** `backend/db/snapshots.py`, migrations `00X_*.py`

### Tables
| Table | Resolution | Retention | Size (current) |
|---|---|---|---|
| `snapshots_1m` | 1 min | 24h rolling | 168 MB |
| `snapshots_5m` | 5 min | 30d rolling | 182 MB |
| `snapshots_1h` | 1 hour | growing | 16 MB |
| `snapshots_1d` | 1 day | growing | 824 KB |

Total ~420 MB of 5 GB Railway limit. Runway 10+ years at current rate.

### Why tiered (not single high-res table)
Single `snapshots_1m` retained forever would hit Railway 5 GB limit in months. Tiered downsampling preserves long history at low resolution where high resolution doesn't matter (1-day-old prices don't need minute-by-minute granularity).

### Downsampling
- Background asyncio tasks downsample 1m → 5m → 1h → 1d
- `_snapshot_running` flag prevents concurrent writes to same table
- Safety valve at row count threshold (prevents runaway growth on bug)

### Budget allocation
- 1 GB allocated for WoW (Classic + Retail combined)
- Remainder for future games

### Invariants
- `_snapshot_running` flag mandatory — never remove
- Downsampling failure must NOT block primary `snapshots_1m` writes
- Each tier's retention is independent — deletion bug in one tier doesn't cascade

---

## [BUG] `snapshots` table grew unbounded before tiered storage

**Severity:** critical (would have hit Railway 5 GB limit)

### Symptom
Single high-resolution table accumulating ~5 MB/day. Projected to fill DB in ~3 years.

### Resolution
Migrated to tiered storage (see ARCH entry above).

### Prevention
- Capacity planning at schema design, not after the fact
- Document storage budget per feature in chat instructions

---

## [BUG] PostgreSQL: SELECT-list aliases unusable in correlated subqueries

**Severity:** medium (queries silently returned wrong results)

### Symptom
Aggregation queries returned NULL or wrong values. No SQL error.

### Root cause
PostgreSQL doesn't allow SELECT-list column aliases to be referenced in correlated subqueries in the same SELECT. The alias resolves to NULL silently.

### Resolution
Restructure as CTE:
```sql
-- BAD (silent failure)
SELECT
  some_calc AS x,
  (SELECT ... WHERE outer.x = ...) AS y
FROM ...

-- GOOD
WITH base AS (
  SELECT some_calc AS x FROM ...
)
SELECT base.x, (SELECT ... WHERE base.x = ...) AS y FROM base
```

### Prevention
- Rule in chat instructions
- Code review check: any subquery referencing outer SELECT column → suspect, verify with EXPLAIN

---

## [BUG] Alembic broken migration chain

**Severity:** high (deploy fails, requires manual fix)

### Symptom
`alembic upgrade head` fails on deploy with "multiple heads" or "missing revision" error.

### Root cause
New migration created with wrong `down_revision` reference. Often happens when working on multiple branches in parallel.

### Resolution
- Run `alembic heads` BEFORE creating any new migration
- Verify chain integrity: `alembic history --verbose`
- For forks: explicit merge migration

### Prevention
- Rule in chat instructions: `alembic heads` before every new migration
- Start command includes `alembic upgrade head &&` — fails fast on broken chain, doesn't silently start app on stale schema

---

## [BUG] `alembic_version` table corruption

**Severity:** high (manual DB intervention required)

### Symptom
Schema is at version X. `alembic_version` table records version Y. Alembic refuses to upgrade or downgrade.

### Root cause
Manual schema edits without migration. Or partial migration failure that left schema in inconsistent state.

### Resolution
Manual correction:
```sql
UPDATE alembic_version SET version_num = 'correct_revision_id';
```
Then either:
- Run `alembic upgrade head` to apply pending migrations
- Or stamp current state: `alembic stamp head`

### Prevention
- NEVER edit schema manually without a migration
- If forced (emergency hotfix): document in migration notes immediately, create catch-up migration next deploy

---

## [BUG] `db/writer.py`: NULL `sources` array crashed batch insert

**Severity:** high (entire batch dropped)

### Root cause
Empty/NULL `sources` array caused `ARRAY` cast failure during batch insert.

### Resolution
```sql
COALESCE(sources, ARRAY[]::text[])
```
Guard wrapped around every `sources` array reference in writer queries.

### Prevention
- Rule in chat instructions: never remove `COALESCE(sources, ARRAY[]::text[])` guard
- Helper function `_flatten_param()` in `db/writer.py` — never remove

---

## [ARCH] DB write batching

**Status:** production rule

### Rule
- DB writes batched in groups of 50
- Never fire >50 concurrent DB tasks
- Each batch uses `asyncio.gather(return_exceptions=True)` — exceptions checked, not silently dropped

### Why
Railway PostgreSQL connection pool is limited. Burst of 300+ concurrent writes (one per offer) exhausts pool, causes cascading timeouts. Batching at 50 keeps pool healthy.

---

## [BUG] Server registry / aliases: `game` column added late

**Status:** resolved (migration 021)

### Context
Original schema assumed single game (WoW Classic). Adding Retail required distinguishing servers by game.

### Resolution
- Added `game` column to `servers` table
- New unique constraint: `servers_game_name_region_version_key`
- Backfill: existing rows tagged `game='wow_classic'`, new Retail rows `game='wow_retail'`

### Lesson learned
Schema should be multi-game from day one even if launching with one game. Migration to add a discriminator to a populated table is non-trivial — requires careful constraint replacement and backfill.

### For new games
- New game → new rows in `servers` with appropriate `game` value
- No new tables required (until per-game logic diverges significantly)

---

## [ARCH] Manual snapshot cleanup: Retail + MoP Classic pre-launch data

**Date:** 2026-04-25
**Status:** executed

### Context
Retail and MoP Classic были добавлены 2026-04-23. Первые ~2 дня снепшоты писались
с некорректными ценами (баг ×1000, per_1k vs per_unit путаница). Очистка удалила
все данные до 2026-04-25 00:00 UTC для этих версий.

### Rows deleted
| Table | Rows |
|---|---|
| snapshots_1m | 727 518 |
| snapshots_5m | 1 193 347 |
| snapshots_1h | 100 523 |
| snapshots_1d | 5 433 |
| **Total** | **2 026 821** |

### SQL pattern
```sql
DELETE FROM snapshots_Xm s
USING servers sv
WHERE s.server_id = sv.id
  AND s.recorded_at < '2026-04-25 00:00:00+00'
  AND (sv.game = 'wow_retail' OR (sv.game = 'wow_classic' AND sv.version = 'MoP Classic'));
```
Followed by `VACUUM` on all four tables.

### Key learnings
- `wow_retail` → `game='wow_retail'`; MoP Classic → `game='wow_classic', version='MoP Classic'`
- Always run SELECT COUNT first to confirm row count before DELETE
- VACUUM required after large deletes to reclaim disk space on Railway

---

## [BUG] /price-history возвращал 422 при last=2016 (7D период)

**Severity:** high (7D график показывал только ~2 дня)
**Files:** `backend/api/router.py`

### Symptom
7D таймфрейм (points=2016) отдавал HTTP 422. График рисовал ~500 точек (~41 час) вместо 7 дней.

### Root cause
Два независимых бага:
1. `last: int = Query(50, ge=1, le=2000)` — лимит 2000 меньше чем 2016 точек нужных для 7D при 5m разрешении
2. `use_tiered=true` никогда не передавался фронтом → Mode 3 (tiered storage) не активировался → запросы уходили в legacy Mode 2 (server_price_history), где данных за 7 дней не было

### Resolution
- `le=2000` → `le=3000`
- Убран gate `if use_tiered and ...` → tiered storage always-on для per-server запросов
- Mode 3 теперь default, Mode 2 — fallback если tiered вернул пусто

### Prevention
- При изменении `period.points` в PriceChart проверять что `le=` в router покрывает новое значение
- Tiered storage должен быть default, не opt-in флаг

---

## [ARCH] Manual snapshot cleanup: Retail + MoP Classic pre-launch data

**Date:** 2026-04-25

### Context
Retail и MoP Classic добавлены 2026-04-23. Первые ~2 дня снепшоты писались с некорректными
ценами (баг ×1000). Очистка удалила все данные до 2026-04-25 00:00 UTC для этих версий.

### Rows deleted
| Table | Rows |
|---|---|
| snapshots_1m | 727 518 |
| snapshots_5m | 1 193 347 |
| snapshots_1h | 100 523 |
| snapshots_1d | 5 433 |
| **Total** | **2 026 821** |

### SQL pattern
```sql
DELETE FROM snapshots_Xm s
USING servers sv
WHERE s.server_id = sv.id
  AND s.recorded_at < '2026-04-25 00:00:00+00'
  AND (sv.game = 'wow_retail'
       OR (sv.game = 'wow_classic' AND sv.version = 'MoP Classic'));
VACUUM snapshots_Xm;
```

### Key learnings
- MoP Classic: `game='wow_classic'`, `version='MoP Classic'` (не `wow_mop`)
- Всегда запускать SELECT COUNT перед DELETE
- VACUUM обязателен после крупных удалений на Railway

---

## [ARCH] snapshots_5m retention снижен с 30d до 7d

**Date:** 2026-04-25
**Files:** `backend/db/tiered_snapshots.py`

### Rationale
7D график использует `snapshots_5m` (hours≤168 → 5m tier). 30D использует `snapshots_1h`.
Хранить 5m данные за 30 дней бессмысленно — при 500 точках на 30D разрешение равно ~86 мин,
что идентично `snapshots_1h`. Экономия: ~140MB после вымывания старых строк.

### Change
`INTERVAL '30 days'` → `INTERVAL '7 days'` в `cleanup_snapshots_5m`.

### Invariant
`query_tiered_history` роутинг не изменился: `hours <= 168 → snapshots_5m` корректен,
так как 7D = 168h покрывается 7-дневным retention с запасом.


## [BUG] Chromie Classic: дублирующий EU сервер вместо RU

**Severity:** medium (Chromie FunPay офферы уходили в неверный сервер)
**Files:** DB `servers`, `server_aliases`

### Symptom
`/servers` показывал Chromie в двух группах:
- `(RU) Classic` → sources: `[g2g]`
- `(EU) Classic` → sources: `[funpay]`

Chromie — RU сервер. FunPay офферы резолвились в `server_id=124` (EU) вместо `server_id=230` (RU).

### Root cause
При заполнении alias migration для chip 114 (`EU+US+RU`) FunPay alias
`(EU) Classic - Chromie` был создан с `region=EU` вместо `region=RU`.
Это создало отдельный `servers` row `id=124, region=EU, name=Chromie, version=Classic`
который никогда не должен был существовать.

### Resolution
```sql
-- Перенести FunPay alias на правильный RU сервер
UPDATE server_aliases SET server_id = 230 WHERE id = 406;
-- Удалить снепшоты EU Chromie (FK constraint)
DELETE FROM snapshots_1m WHERE server_id = 124;      -- 5886 rows
DELETE FROM snapshots_5m WHERE server_id = 124;      -- 11261 rows
DELETE FROM snapshots_1h WHERE server_id = 124;      -- 990 rows
DELETE FROM snapshots_1d WHERE server_id = 124;      -- 46 rows
DELETE FROM price_snapshots WHERE server_id = 124;   -- 102 rows
DELETE FROM servers WHERE id = 124;
```

### Prevention
- При создании alias migration: RU серверы на chip 114 (`EU+US+RU`) имеют prefix
  `(RU)` в `.tc-server` HTML — alias должен резолвиться в `region=RU`, не `region=EU`
- Проверка после любой alias migration:
```sql
  SELECT name, COUNT(DISTINCT region) FROM servers
  WHERE game='wow_classic' GROUP BY name HAVING COUNT(DISTINCT region) > 1;
```
  Если сервер появляется в >1 регионе — потенциальный дубликат, проверить вручную